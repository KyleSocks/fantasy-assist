"""Projection sandbox (src/inseason/sandbox.py), worked by hand."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from src.inseason import sandbox as SB
from tests.test_inseason import ICFG, POINTS, PRIORS, db  # noqa: F401


def L(pid, pos, blended, season, delta=None):
    extra = {"deploy_base": {"season_pp": season[0], "season_ev": season[1]}}
    if delta:
        extra["deploy"] = {"season_pp": season[0], "season_ev": season[1], "delta": delta}
    return NS(player_id=pid, position=pos, is_goalie=False, team="AAA", blended=dict(blended),
              extra=extra)


def test_rates_at_more_minutes():
    """Per game at season minutes (deploy adjustment already taken out):
    0.4 G + 0.6 A = 1.0 points, 0.3 of them on the PP, 3 shots; season
    120 s PP + 900 s EV. EV rate 0.7/900, PP rate 0.3/120. At 240 s PP +
    960 s EV: 0.7 x 960/900 + 0.3 x 240/120 = 0.74667 + 0.6 = 1.34667
    points, 40% goals = 0.53867; shots 3 x 1200/1020 = 3.52941."""
    ln = L(1, "C", {"goals": 0.45, "assists": 0.6, "pp_points": 0.35, "shots": 3.0},
           (120, 900), delta={"goals": 0.05, "pp_points": 0.05})
    ctx = NS(lines={1: ln})
    r = SB.rates_at(ctx, 1, 240, 960, {"F": 0.001, "D": 0.001})
    assert r["pp_points"] == pytest.approx(0.6)
    assert r["goals"] + r["assists"] == pytest.approx(0.7 * 960 / 900 + 0.6)
    assert r["goals"] == pytest.approx(0.4 * (0.7 * 960 / 900 + 0.6))
    assert r["shots"] == pytest.approx(3 * 1200 / 1020)
    # at his season minutes nothing changes
    same = SB.rates_at(ctx, 1, 120, 900, {"F": 0.001, "D": 0.001})
    assert same["goals"] == pytest.approx(0.4) and same["pp_points"] == pytest.approx(0.3)


def test_rates_at_no_pp_history_uses_league_rate():
    """A defenceman with 20 s of PP a game (< 60) has no PP rate of his
    own: 120 s of PP at the league's .0015 PP points a second for D = 0.18
    PP points; even strength unchanged (0.2 points on 1200 s)."""
    ln = L(1, "D", {"goals": 0.05, "assists": 0.15, "pp_points": 0.0, "shots": 1.5}, (20, 1200))
    r = SB.rates_at(NS(lines={1: ln}), 1, 120, 1200, {"F": 0.002, "D": 0.0015})
    assert r["pp_points"] == pytest.approx(0.18)
    assert r["goals"] + r["assists"] == pytest.approx(0.2 + 0.18)
    assert r["goals"] == pytest.approx(0.25 * 0.38)


def test_whatif_on_a_real_context(db):  # noqa: F811
    """Ovi (1): more PP time raises value per game and rest of season;
    at the minutes the projection already uses, the difference is 0."""
    from src.inseason.evaluate import build_context
    ctx = build_context(db, POINTS, priors=PRIORS, icfg=ICFG)
    ctx.lines[1].extra["deploy_base"] = {"season_pp": 120.0, "season_ev": 900.0}
    same = SB.whatif(ctx, 1, "2026-11-01")
    assert same["diff"]["per_game"] == pytest.approx(0) and same["new"]["pp"] == 120.0
    more = SB.whatif(ctx, 1, "2026-11-01", pp=240, ev=900)
    assert more["diff"]["per_game"] > 0
    assert more["diff"]["ros"] > 0 and more["games_left"] == 2
    # the app's memoised per-game values aren't touched by the what-if copies
    from src.inseason.evaluate import game_value
    memo = dict(ctx.__dict__.get("_game_value", {}))
    SB.whatif(ctx, 1, "2026-11-01", pp=600, ev=900)
    assert ctx.__dict__.get("_game_value", {}) == memo
    assert game_value(ctx, ctx.lines[1], None) == pytest.approx(more["projection"])
    assert more["season"] == {"pp": 120.0, "ev": 900.0}
    with pytest.raises(ValueError):
        ctx.lines[1].extra.pop("deploy_base")
        SB.whatif(ctx, 1, "2026-11-01")
