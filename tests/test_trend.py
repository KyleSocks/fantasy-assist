"""Trend column: last-N-days totals from the stats API's date-range query,
scored per game (per start) against the blend. Hand-computed values."""
from __future__ import annotations

import sqlite3

import pytest

from src.inseason.evaluate import build_context
from src.inseason.ingest import _recent
from src.inseason.trend import trend
from src.ingest.models import init_db
from tests.test_inseason import ICFG, POINTS, PRIORS, db  # noqa: F401


class _Range:
    def __init__(self):
        self.urls = []

    def get_paged(self, base, report, season):
        self.urls.append(base)
        if "skater/summary" in base:
            return [{"playerId": 1, "gamesPlayed": 4, "goals": 4, "assists": 2,
                     "shots": 16, "ppPoints": 1, "shPoints": 0, "teamAbbrevs": "AAA"}]
        if "skater/realtime" in base:
            return [{"playerId": 1, "hits": 4, "blockedShots": 2}]
        return [{"playerId": 6, "gamesPlayed": 3, "gamesStarted": 3, "wins": 3,
                 "saves": 84, "shotsAgainst": 90, "goalsAgainst": 6,
                 "shutouts": 1, "teamAbbrevs": "AAA"}]


def _with_recent(db):  # noqa: F811
    conn = init_db(db)
    f = _Range()
    counts = _recent(conn, f, 20262027, "2026-11-10", 21)
    conn.commit()
    conn.close()
    return f, counts


def test_recent_ingest_uses_date_range_and_replaces_rows(db):  # noqa: F811
    f, counts = _with_recent(db)
    assert counts == {"skater_recent": 1, "goalie_recent": 1}
    assert len(f.urls) == 3
    # 21 days ending 11-10 starts 10-21
    assert all('gameDate>="2026-10-21" and gameDate<="2026-11-10 23:59:59"' in u
               and "isAggregate=true" in u and "gameTypeId=2" in u for u in f.urls)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT gp, goals, hits, blocks, window_start FROM"
                        " skater_recent").fetchone() == (4, 4, 4, 2, "2026-10-21")
    conn.close()
    _with_recent(db)                                   # refresh replaces
    assert sqlite3.connect(db).execute(
        "SELECT COUNT(*) FROM skater_recent").fetchone()[0] == 1


def test_skater_trend_hand_computed(db):  # noqa: F811
    """Test league: G 2, A 1, SOG .5, BLK .5 (HIT, PPP unscored).
    Last 21 days, Ovechkin 4 GP: 4 G, 2 A, 16 SOG, 2 BLK -> per game
    1 G, .5 A, 4 SOG, .5 BLK = 2 + .5 + 2 + .25 = 4.75.
    Blend per game (prior k=10, 10 GP: G .35, A .4, SOG 3, BLK .45)
    = .7 + .4 + 1.5 + .225 = 2.825 -> +1.925, +68.1%."""
    _with_recent(db)
    ctx = build_context(db, POINTS, priors=PRIORS, icfg=ICFG)
    t = trend(ctx, ctx.lines[1])
    assert t["games"] == 4 and t["start"] == "2026-10-21"
    assert t["per_game"] == pytest.approx(4.75)
    assert t["base"] == pytest.approx(2.825)
    assert t["pct"] == pytest.approx(1.925 / 2.825)


def test_goalie_trend_per_start(db):  # noqa: F811
    """Goalie A, 3 starts: 3 W, 84 SV, 6 GA -> per start 3 x 1 W + .1 x 28 SV
    - 2 GA = 3.8. Compared with his blended per-start line."""
    _with_recent(db)
    ctx = build_context(db, POINTS, priors=PRIORS, icfg=ICFG)
    ln = ctx.lines[6]
    t = trend(ctx, ln)
    assert t["per_game"] == pytest.approx(3.8)
    b = ln.blended
    assert t["base"] == pytest.approx(3 * b["wins"] + 0.1 * b["saves"]
                                      - b["goals_against"])


def test_too_few_games_or_no_data_shows_no_trend(db):  # noqa: F811
    ctx = build_context(db, POINTS, priors=PRIORS, icfg=dict(ICFG, trend_min_games=5))
    assert trend(ctx, ctx.lines[1])["per_game"] is None          # no table rows
    _with_recent(db)
    ctx = build_context(db, POINTS, priors=PRIORS, icfg=dict(ICFG, trend_min_games=5))
    t = trend(ctx, ctx.lines[1])
    assert t["per_game"] is None and t["games"] == 4               # 4 < 5
