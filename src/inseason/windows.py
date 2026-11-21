"""Stats over the last 7 / 14 / 28 / 56 days, for the "Stats from" switch
on the roster and free-agent tables and the player comparison.

Same NHL reports as the trend window (src/inseason/ingest.py _recent),
summed over each date range, stored in skater_window / goalie_window
(replaced on every refresh). Values: skaters per game played, goalies per
start, scored like everything else (trend.value_of).
"""
from __future__ import annotations

import datetime
import sqlite3

WINDOWS = (7, 14, 28, 56)
SKATER_COLS = ("goals", "assists", "shots", "blocks", "hits", "pp_points", "sh_points")
GOALIE_COLS = ("wins", "saves", "shots_against", "goals_against", "shutouts")

SCHEMA = """
CREATE TABLE IF NOT EXISTS skater_window (
    player_id INTEGER NOT NULL, days INTEGER NOT NULL, gp INTEGER,
    goals INTEGER, assists INTEGER, shots INTEGER, blocks INTEGER, hits INTEGER,
    pp_points INTEGER, sh_points INTEGER, window_start TEXT, window_end TEXT,
    PRIMARY KEY (player_id, days));
CREATE TABLE IF NOT EXISTS goalie_window (
    player_id INTEGER NOT NULL, days INTEGER NOT NULL, gs INTEGER,
    wins INTEGER, saves INTEGER, shots_against INTEGER, goals_against INTEGER,
    shutouts INTEGER, window_start TEXT, window_end TEXT,
    PRIMARY KEY (player_id, days));
"""


def ingest_windows(conn: sqlite3.Connection, fetcher, season: int, as_of: str,
                   windows=WINDOWS) -> dict[str, int]:
    from src.ingest.models import GoalieSummary, SkaterRealtime, SkaterSummary
    from .ingest import _BASE, _combine_rows, _range_filter
    conn.executescript(SCHEMA)
    end = datetime.date.fromisoformat(as_of)
    rows_sk, rows_g = [], []
    for days in windows:
        start = (end - datetime.timedelta(days=days - 1)).isoformat()
        f = _range_filter(start, end.isoformat())
        summary, _ = _combine_rows(
            fetcher.get_paged(f"{_BASE}/skater/summary{f}", f"window{days}_skater_summary", season),
            SkaterSummary, ["gamesPlayed", "goals", "assists", "shots", "ppPoints", "shPoints"])
        realtime, _ = _combine_rows(
            fetcher.get_paged(f"{_BASE}/skater/realtime{f}", f"window{days}_skater_realtime", season),
            SkaterRealtime, ["hits", "blockedShots"])
        goalies, _ = _combine_rows(
            fetcher.get_paged(f"{_BASE}/goalie/summary{f}", f"window{days}_goalie_summary", season),
            GoalieSummary, ["gamesPlayed", "gamesStarted", "wins", "saves",
                            "shotsAgainst", "goalsAgainst", "shutouts"])
        for pid, s in summary.items():
            rt = realtime.get(pid)
            rows_sk.append((pid, days, s.gamesPlayed, s.goals, s.assists, s.shots,
                            rt.blockedShots if rt else None, rt.hits if rt else None,
                            s.ppPoints, s.shPoints, start, end.isoformat()))
        for pid, g in goalies.items():
            rows_g.append((pid, days, g.gamesStarted, g.wins, g.saves, g.shotsAgainst,
                           g.goalsAgainst, g.shutouts, start, end.isoformat()))
    conn.execute("DELETE FROM skater_window")
    conn.execute("DELETE FROM goalie_window")
    conn.executemany("INSERT INTO skater_window VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows_sk)
    conn.executemany("INSERT INTO goalie_window VALUES (?,?,?,?,?,?,?,?,?,?)", rows_g)
    return {"window_rows": len(rows_sk) + len(rows_g)}


def load_windows(conn: sqlite3.Connection) -> dict[int, dict[int, dict]]:
    """{pid: {days: {"games", "stats", "start", "end"}}}; {} before the
    first refresh that fills the tables."""
    out: dict[int, dict[int, dict]] = {}
    try:
        for r in conn.execute(f"SELECT player_id, days, gp, {', '.join(SKATER_COLS)},"
                              " window_start, window_end FROM skater_window"):
            out.setdefault(r[0], {})[r[1]] = {
                "games": r[2] or 0, "start": r[-2], "end": r[-1],
                "stats": {k: float(v or 0) for k, v in zip(SKATER_COLS, r[3:3 + len(SKATER_COLS)])}}
        for r in conn.execute(f"SELECT player_id, days, gs, {', '.join(GOALIE_COLS)},"
                              " window_start, window_end FROM goalie_window"):
            out.setdefault(r[0], {})[r[1]] = {
                "games": r[2] or 0, "start": r[-2], "end": r[-1],
                "stats": {k: float(v or 0) for k, v in zip(GOALIE_COLS, r[3:3 + len(GOALIE_COLS)])}}
    except sqlite3.OperationalError:
        return {}
    return out
