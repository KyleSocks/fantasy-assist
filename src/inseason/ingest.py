"""Season-to-date ingest for the mid-season assistant.

Pulls regular-season (gameTypeId=2) skater and goalie totals for the current
season into skater_current / goalie_current, refreshes current rosters (so
trades move players to their new team) and re-ingests the current season's
schedule from today forward.

    uv run python -m src.inseason.ingest            # stats + rosters + schedule
    uv run python -m src.inseason.ingest --no-schedule
"""
from __future__ import annotations

import argparse
import datetime
import sys

from src.ingest.ingest import _combine_rows, current_season_id
from src.ingest.models import (
    Fetcher,
    GoalieSummary,
    SkaterRealtime,
    SkaterSummary,
    SkaterTOI,
    init_db,
    log_ingest,
)
from src.ingest.rosters import ingest_rosters
from src.ingest.schedule import ingest_schedule
from src.paths import DEFAULT_DB_ARG

_BASE = "https://api.nhle.com/stats/rest/en"
_FILTER = "?cayenneExp=gameTypeId=2 and seasonId="


def _skaters(conn, fetcher, season_id: int, as_of: str) -> int:
    summary, team_map = _combine_rows(
        fetcher.get_paged(f"{_BASE}/skater/summary{_FILTER}",
                          "cur_skater_summary", season_id),
        SkaterSummary,
        ["gamesPlayed", "goals", "assists", "points", "shots", "ppPoints"])
    realtime, _ = _combine_rows(
        fetcher.get_paged(f"{_BASE}/skater/realtime{_FILTER}",
                          "cur_skater_realtime", season_id),
        SkaterRealtime, ["hits", "blockedShots"])
    toi, _ = _combine_rows(
        fetcher.get_paged(f"{_BASE}/skater/timeonice{_FILTER}",
                          "cur_skater_toi", season_id),
        SkaterTOI, ["evTimeOnIce", "ppTimeOnIce"])
    n = 0
    for pid, s in summary.items():
        rt, ti = realtime.get(pid), toi.get(pid)
        conn.execute(
            "INSERT INTO skater_current (player_id, season_id, team_abbrev, gp,"
            " goals, assists, points, shots, blocks, hits, pp_points,"
            " pp_toi_sec, toi_sec, as_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(player_id, season_id) DO UPDATE SET"
            " team_abbrev=excluded.team_abbrev, gp=excluded.gp,"
            " goals=excluded.goals, assists=excluded.assists,"
            " points=excluded.points, shots=excluded.shots,"
            " blocks=excluded.blocks, hits=excluded.hits,"
            " pp_points=excluded.pp_points, pp_toi_sec=excluded.pp_toi_sec,"
            " toi_sec=excluded.toi_sec, as_of=excluded.as_of",
            (pid, season_id, team_map.get(pid, "UNK"), s.gamesPlayed, s.goals,
             s.assists, s.points, s.shots,
             rt.blockedShots if rt else None, rt.hits if rt else None,
             s.ppPoints,
             int(ti.ppTimeOnIce) if ti and ti.ppTimeOnIce is not None else None,
             int(ti.evTimeOnIce) if ti and ti.evTimeOnIce is not None else None,
             as_of))
        n += 1
    return n


def _goalies(conn, fetcher, season_id: int, as_of: str) -> int:
    goalies, team_map = _combine_rows(
        fetcher.get_paged(f"{_BASE}/goalie/summary{_FILTER}",
                          "cur_goalie_summary", season_id),
        GoalieSummary,
        ["gamesPlayed", "gamesStarted", "wins", "losses", "otLosses",
         "saves", "shotsAgainst", "goalsAgainst", "shutouts"])
    n = 0
    for pid, g in goalies.items():
        conn.execute(
            "INSERT INTO goalie_current (player_id, season_id, team_abbrev, gp,"
            " games_started, wins, losses, ot_losses, saves, shots_against,"
            " goals_against, shutouts, as_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(player_id, season_id) DO UPDATE SET"
            " team_abbrev=excluded.team_abbrev, gp=excluded.gp,"
            " games_started=excluded.games_started, wins=excluded.wins,"
            " losses=excluded.losses, ot_losses=excluded.ot_losses,"
            " saves=excluded.saves, shots_against=excluded.shots_against,"
            " goals_against=excluded.goals_against,"
            " shutouts=excluded.shutouts, as_of=excluded.as_of",
            (pid, season_id, team_map.get(pid, "UNK"), g.gamesPlayed,
             g.gamesStarted, g.wins, g.losses, g.otLosses, g.saves,
             g.shotsAgainst, g.goalsAgainst, g.shutouts, as_of))
        n += 1
    return n


def ingest_current(db_path: str = DEFAULT_DB_ARG, season_id: int | None = None,
                   schedule: bool = True, rosters: bool = True,
                   fetcher=None, as_of: str | None = None,
                   schedule_start: datetime.date | None = None) -> dict[str, int]:
    """Ingest season-to-date stats (+ rosters, + schedule). Returns row counts."""
    season_id = season_id or current_season_id()
    as_of = as_of or datetime.date.today().isoformat()
    own_fetcher = fetcher is None
    fetcher = fetcher or Fetcher()
    conn = init_db(db_path)
    counts: dict[str, int] = {}
    try:
        conn.execute("BEGIN")
        try:
            counts["skater_current"] = _skaters(conn, fetcher, season_id, as_of)
            counts["goalie_current"] = _goalies(conn, fetcher, season_id, as_of)
            notes: list[str] = []
            if rosters:
                rc, rn = ingest_rosters(conn, fetcher)
                counts.update(rc)
                notes += rn
            if schedule:
                sc, sn = ingest_schedule(conn, fetcher, season_id,
                                         start=schedule_start
                                         or datetime.date.today(),
                                         step_days=7)
                counts.update(sc)
                notes += sn
            log_ingest(conn, "inseason", season_id, sum(counts.values()),
                       notes=notes)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
        if own_fetcher:
            fetcher.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB_ARG)
    ap.add_argument("--season", type=int, default=None,
                    help="season id, e.g. 20262027 (default: current)")
    ap.add_argument("--no-schedule", action="store_true")
    ap.add_argument("--no-rosters", action="store_true")
    args = ap.parse_args(argv)
    counts = ingest_current(args.db, args.season,
                            schedule=not args.no_schedule,
                            rosters=not args.no_rosters)
    for k, v in counts.items():
        print(f"  {k:<16} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
