"""Tests for src/draft/timeframe.py -- hand-computed window values.

A hand-built in-season Context (no DB): one team, C1 D1 G1, points
G 2, A 1, SOG .5, BLK .5, W 3, SV .1, GA -1.

Blended per-game lines (skaters play every game):
  1 C AAA  G .5  A .5  SOG 4  BLK 0
  2 C BBB  G .2  A .3  SOG 2  BLK 0
  3 D BBB  G .1  A .4  SOG 2  BLK 1
  4 D AAA  G 0   A .2  SOG 1  BLK 2
Goalies (per start): 5 AAA share .5  W .5 SV 25 GA 2.5 SO .1
                     6 BBB share 1   W .6 SV 28 GA 2   SO .2
                     7 CCC share 1   W .3 SV 20 GA 3   SO 0
Opponent factors: AAA ga 1.2 (allows 20% more goals), BBB sa 1.1 and
gf .8 (its shooters score 20% less on goalies), CCC average.
Schedule: 11-02 AAA-BBB, 11-05 BBB-CCC, 11-10 AAA-CCC.
Fantasy playoffs 11-09..11-12, playoff games count 2x (ros).
"""
from __future__ import annotations

import pytest

from src.draft.timeframe import timeframe_values, window_for
from src.inseason.evaluate import Context
from src.inseason.model import Line
from src.valuation.league import LeagueConfig

CFG = LeagueConfig(
    name="t", team_count=1, scoring_type="points",
    roster_slots={"C": 1, "LW": 0, "RW": 0, "D": 1, "G": 1, "UTIL": 0,
                  "BENCH": 0},
    points_per_stat={"G": 2, "A": 1, "SOG": 0.5, "BLK": 0.5, "W": 3,
                     "SV": 0.1, "GA": -1},
    regular_window=("2026-10-01", "2026-11-08"),
    playoff_window=("2026-11-09", "2026-11-12"))


def _sk(pid, pos, team, g, a, s, b):
    rates = {"goals": g, "assists": a, "shots": s, "blocks": b, "hits": 0.0,
             "pp_points": 0.0, "sh_points": 0.0}
    return Line(pid, f"P{pid}", pos, team, 10, rates, rates, rates)


def _g(pid, team, share, w, sv, ga, so):
    r = {"wins": w, "saves": sv, "goals_against": ga, "shutouts": so,
         "save_pct": sv / (sv + ga)}
    return Line(pid, f"G{pid}", "G", team, 10, r, r, r, start_share=share)


def make_ctx(weight=2.0) -> Context:
    lines = {1: _sk(1, "C", "AAA", .5, .5, 4, 0), 2: _sk(2, "C", "BBB", .2, .3, 2, 0),
             3: _sk(3, "D", "BBB", .1, .4, 2, 1), 4: _sk(4, "D", "AAA", 0, .2, 1, 2),
             5: _g(5, "AAA", .5, .5, 25, 2.5, .1), 6: _g(6, "BBB", 1.0, .6, 28, 2, .2),
             7: _g(7, "CCC", 1.0, .3, 20, 3, 0)}
    one = {"ga": 1.0, "sa": 1.0, "gf": 1.0, "sf": 1.0}
    factors = {"AAA": dict(one, ga=1.2), "BBB": dict(one, sa=1.1, gf=0.8),
               "CCC": dict(one)}
    games = [("2026-11-02", "AAA", "BBB"), ("2026-11-05", "BBB", "CCC"),
             ("2026-11-10", "AAA", "CCC")]
    return Context(CFG, {"playoff_game_weight": weight}, 20262027,
                   "2026-11-01", lines, factors, None, games)


def by_id(vals):
    return {v.player_id: v for v in vals}


def test_next7_totals_and_vorp():
    projs, vals, win = timeframe_values(make_ctx(), "next7", "2026-11-02")
    assert (win.start, win.end) == ("2026-11-02", "2026-11-08")
    # P1: one game, at BBB (shots x1.1): G .5 A .5 SOG 4.4 -> 1+.5+2.2 = 3.7
    assert projs[1].shots == pytest.approx(4.4)
    # P2: at AAA (goals/assists x1.2) + CCC: G .44 A .66 SOG 4 -> 3.54
    assert projs[2].goals == pytest.approx(0.44)
    v = by_id(vals)
    # F pool 1 -> replacement = 2nd forward (P2, 3.54); P1 VORP .16
    assert v[1].total_value == pytest.approx(0.16)
    assert v[2].total_value == pytest.approx(0.0)
    # P3: G .22 A .88 SOG 4 BLK 2 = 4.32; P4: A .2 SOG 1.1 BLK 2 = 1.75
    assert v[3].total_value == pytest.approx(4.32 - 1.75)
    # Goalies: G5 at BBB (GA x.8) half a start: W .25 SV 12.5 GA 1 -> 1.0
    # G6 two starts: W 1.2 SV 56 GA 4 -> 5.2; G7 vs BBB: W .3 SV 20 GA 2.4
    # -> .5. Replacement = index 2 of startable goalies = .5
    assert projs[5].games_started == pytest.approx(0.5)
    assert v[6].total_value == pytest.approx(5.2 - 0.5)
    assert v[5].total_value == pytest.approx(1.0 - 0.5)
    assert v[1].gp_regular == 1 and v[1].gp_playoff == 0


def test_playoffs_window_only_counts_playoff_games():
    projs, vals, win = timeframe_values(make_ctx(), "playoffs", "2026-11-02")
    assert (win.start, win.end) == ("2026-11-09", "2026-11-12")
    # AAA-CCC on 11-10 is the only playoff game; BBB has none
    assert projs[1].goals == pytest.approx(0.5)
    assert projs[2].gp == 0
    v = by_id(vals)
    assert v[1].gp_playoff == 1 and v[1].vorp_regular == 0.0
    # P1 vs CCC: 1 + .5 + 2 = 3.5; P2 = 0 -> VORP 3.5
    assert v[1].vorp_playoff == pytest.approx(3.5)


def test_ros_weights_playoff_games():
    projs, _, win = timeframe_values(make_ctx(weight=2.0), "ros", "2026-11-02")
    # fantasy end = last NHL game (11-10) inside the playoff window
    assert win.end == "2026-11-10"
    # P1: 11-02 (x1) + 11-10 playoff (x2): goals .5 + 2 x .5
    assert projs[1].goals == pytest.approx(1.5)


def test_playoff_window_clamps_and_ends():
    ctx = make_ctx()
    assert window_for(ctx, "playoffs", "2026-11-11").start == "2026-11-11"
    with pytest.raises(ValueError):
        window_for(ctx, "playoffs", "2026-11-13")
    with pytest.raises(ValueError):
        window_for(ctx, "season", "2026-11-02")
