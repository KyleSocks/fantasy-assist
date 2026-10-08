"""NHL Stats ingestor for the fantasy hockey draft assistant.

CLI: python -m src.ingest.ingest --seasons 4 [--dry-run] [--db PATH]
"""
from __future__ import annotations

import argparse
import datetime
import sys
from dataclasses import dataclass, field

import httpx

from src.paths import DEFAULT_DB_ARG

from .models import (
    Fetcher,
    GoalieSummary,
    SkaterRealtime,
    SkaterSummary,
    SkaterTOI,
    init_db,
    log_ingest,
)
from .rosters import ingest_rosters as _rosters
from .schedule import ingest_schedule as _schedule


# ── Season helpers ───────────────────────────────────────────────────

def current_season_id() -> int:
    """Derive the current NHL season id (YYYYYYYY) from today's date."""
    now = datetime.date.today()
    if now.month >= 9:
        return now.year * 10000 + (now.year + 1)
    return (now.year - 1) * 10000 + now.year


def past_season_ids(n: int) -> list[int]:
    """Return n most-recent season ids, most recent first."""
    now = datetime.date.today()
    start_year = now.year if now.month >= 9 else now.year - 1
    return [sy * 10000 + sy + 1 for sy in range(start_year, start_year - n, -1)]


# ── Report ───────────────────────────────────────────────────────────

@dataclass
class IngestReport:
    rows_written: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ── Row aggregation ──────────────────────────────────────────────────

def _combine_rows(raw_rows: list[dict], model_cls, stat_fields: list[str]):
    """Group a season's raw API rows by playerId and combine a player's
    multiple rows (regular season + playoffs) into a single record.

    The NHL stats API returns two rows for a player traded mid-season: a
    regular-season row whose ``teamAbbrevs`` lists every team they played
    for (comma-separated), and a playoff row whose ``teamAbbrevs`` is the
    single team they finished with. A non-traded player gets one row with
    combined regular + playoff stats. We sum the stat fields across all
    rows and store the single-team abbreviation (the team they were traded
    to) as ``team_abbrev`` so the result is deterministic regardless of the
    API's row ordering.

    Returns ``(combined, team_map)`` where ``combined`` maps playerId to a
    ``model_cls`` instance with summed stats and ``team_map`` maps playerId
    to the deterministic team abbreviation.
    """
    by_pid: dict[int, list[dict]] = {}
    for r in raw_rows:
        pid = r.get("playerId")
        if pid is None:
            continue
        by_pid.setdefault(pid, []).append(r)
    combined: dict = {}
    team_map: dict[int, str] = {}
    for pid, rows in by_pid.items():
        single = [r for r in rows if "," not in (r.get("teamAbbrevs") or "")]
        if single:
            team_map[pid] = single[-1].get("teamAbbrevs") or "UNK"
        else:
            team_str = rows[-1].get("teamAbbrevs") or ""
            team_map[pid] = team_str.split(",")[-1].strip() or "UNK"
        base = model_cls(**rows[0])
        for r in rows[1:]:
            for field_name in stat_fields:
                val = r.get(field_name)
                if val is not None:
                    current = getattr(base, field_name) or 0
                    setattr(base, field_name, current + val)
        combined[pid] = base
    return combined, team_map


# ── Skater season ingest ─────────────────────────────────────────────

def _ingest_skaters(conn, fetcher: Fetcher, season_id: int,
                    dry_run: bool = False) -> tuple[dict[str, int], list[str]]:
    notes: list[str] = []
    raw_summary = fetcher.get_paged(
        "https://api.nhle.com/stats/rest/en/skater/summary?cayenneExp=seasonId=",
        "skater_summary", season_id,
    )
    summary, team_map = _combine_rows(
        raw_summary, SkaterSummary,
        ["gamesPlayed", "goals", "assists", "points", "shots", "ppPoints"],
    )

    raw_realtime = fetcher.get_paged(
        "https://api.nhle.com/stats/rest/en/skater/realtime?cayenneExp=seasonId=",
        "skater_realtime", season_id,
    )
    realtime, _ = _combine_rows(
        raw_realtime, SkaterRealtime, ["hits", "blockedShots"],
    )

    raw_toi = fetcher.get_paged(
        "https://api.nhle.com/stats/rest/en/skater/timeonice?cayenneExp=seasonId=",
        "skater_toi", season_id,
    )
    toi_map, _ = _combine_rows(
        raw_toi, SkaterTOI, ["evTimeOnIce", "ppTimeOnIce"],
    )

    counts: dict[str, int] = {"skater_seasons": 0}
    cur = conn.cursor()
    for pid, s in summary.items():
        team_abbrev = team_map.get(pid, "UNK")
        rt = realtime.get(pid)
        ti = toi_map.get(pid)
        hits = rt.hits if rt else None
        blocks = rt.blockedShots if rt else None
        if hits is None and rt is not None:
            notes.append(f"skater {pid}: hits NULL")
        if blocks is None and rt is not None:
            notes.append(f"skater {pid}: blocks NULL")
        pp_toi_sec = int(ti.ppTimeOnIce) if ti and ti.ppTimeOnIce is not None else None
        toi_sec = int(ti.evTimeOnIce) if ti and ti.evTimeOnIce is not None else None
        if dry_run:
            counts["skater_seasons"] += 1
            continue
        cur.execute(
            "INSERT INTO skater_seasons (player_id, season_id, team_abbrev, gp, goals,"
            " assists, points, shots, blocks, hits, pp_points, pp_toi_sec, toi_sec)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(player_id, season_id) DO UPDATE SET"
            " gp=excluded.gp, goals=excluded.goals, assists=excluded.assists,"
            " points=excluded.points, shots=excluded.shots, blocks=excluded.blocks,"
            " hits=excluded.hits, pp_points=excluded.pp_points,"
            " pp_toi_sec=excluded.pp_toi_sec, toi_sec=excluded.toi_sec",
              (pid, season_id, team_abbrev, s.gamesPlayed, s.goals, s.assists,
               s.points, s.shots, blocks, hits, s.ppPoints, pp_toi_sec, toi_sec),
        )
        counts["skater_seasons"] += 1
    return counts, notes


# ── Goalie season ingest ─────────────────────────────────────────────

def _ingest_goalies(conn, fetcher: Fetcher, season_id: int,
                    dry_run: bool = False) -> tuple[dict[str, int], list[str]]:
    notes: list[str] = []
    raw_rows = fetcher.get_paged(
        "https://api.nhle.com/stats/rest/en/goalie/summary?cayenneExp=seasonId=",
        "goalie_summary", season_id,
    )
    goalies, team_map = _combine_rows(
        raw_rows, GoalieSummary,
        ["gamesPlayed", "gamesStarted", "wins", "losses", "otLosses",
         "saves", "shotsAgainst", "goalsAgainst", "shutouts"],
    )
    counts: dict[str, int] = {"goalie_seasons": 0}
    cur = conn.cursor()
    for pid, g in goalies.items():
        team_abbrev = team_map.get(pid, "UNK")
        if dry_run:
            counts["goalie_seasons"] += 1
            continue
        cur.execute(
            "INSERT INTO goalie_seasons (player_id, season_id, team_abbrev, gp,"
            " games_started, wins, losses, ot_losses, saves, shots_against,"
            " goals_against, shutouts, toi_sec) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(player_id, season_id) DO UPDATE SET"
            " gp=excluded.gp, games_started=excluded.games_started,"
            " wins=excluded.wins, losses=excluded.losses,"
            " ot_losses=excluded.ot_losses, saves=excluded.saves,"
            " shots_against=excluded.shots_against, goals_against=excluded.goals_against,"
            " shutouts=excluded.shutouts, toi_sec=excluded.toi_sec",
             (pid, season_id, team_abbrev, g.gamesPlayed, g.gamesStarted,
              g.wins, g.losses, g.otLosses, g.saves, g.shotsAgainst,
              g.goalsAgainst, g.shutouts, None),
        )
        counts["goalie_seasons"] += 1
    return counts, notes


# ── Public API ───────────────────────────────────────────────────────

def ingest_seasons(n: int, db_path: str = DEFAULT_DB_ARG,
                  season_ids: list[int] | None = None) -> IngestReport:
    """Ingest NHL seasons (stats + schedule).

    Ingests the ``n`` most-recent seasons, or exactly ``season_ids``
    when that list is provided (``n`` is then ignored).
    """
    report = IngestReport()
    conn = init_db(db_path)
    fetcher = Fetcher()
    try:
        ids = season_ids if season_ids is not None else past_season_ids(n)
        for season_id in ids:
            conn.execute("BEGIN")
            try:
                sc, sn = _ingest_skaters(conn, fetcher, season_id)
                gc, gn = _ingest_goalies(conn, fetcher, season_id)
                slc, sln = _schedule(conn, fetcher, season_id)
                for k, v in {**sc, **gc, **slc}.items():
                    report.rows_written[k] = report.rows_written.get(k, 0) + v
                all_notes = sn + gn + sln
                report.notes.extend(all_notes)
                log_ingest(conn, "seasons", season_id,
                            sum(sc.values()) + sum(gc.values()) + sum(slc.values()),
                            notes=all_notes)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    finally:
        fetcher.close()
        conn.close()
    return report


def ingest_rosters(db_path: str = DEFAULT_DB_ARG) -> IngestReport:
    """Ingest current rosters, player details, and team metadata."""
    report = IngestReport()
    conn = init_db(db_path)
    fetcher = Fetcher()
    try:
        conn.execute("BEGIN")
        try:
            counts, notes = _rosters(conn, fetcher)
            for k, v in counts.items():
                report.rows_written[k] = report.rows_written.get(k, 0) + v
            report.notes.extend(notes)
            log_ingest(conn, "rosters", None, sum(counts.values()), notes=notes)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        fetcher.close()
        conn.close()
    return report


def _dry_run_report(n: int, db_path: str) -> None:
    """Print planned writes per table without writing anything."""
    conn = init_db(db_path)
    fetcher = Fetcher()
    try:
        for season_id in past_season_ids(n):
            sc, _ = _ingest_skaters(conn, fetcher, season_id, dry_run=True)
            gc, _ = _ingest_goalies(conn, fetcher, season_id, dry_run=True)
            slc, _ = _schedule(conn, fetcher, season_id, dry_run=True)
            rc, _ = _rosters(conn, fetcher, dry_run=True)
            total = {**sc, **gc, **slc, **rc}
            print(f"Season {season_id}:")
            for table, count in total.items():
                print(f"  {table}: {count} rows")
            break
    finally:
        fetcher.close()
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest NHL stats into SQLite.")
    parser.add_argument("--seasons", type=int, default=1,
                        help="Number of seasons to ingest")
    parser.add_argument("--db", default=DEFAULT_DB_ARG,
                        help="SQLite database path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned writes only")
    args = parser.parse_args()
    try:
        if args.dry_run:
            _dry_run_report(args.seasons, args.db)
            return
        report = ingest_seasons(args.seasons, args.db)
        report2 = ingest_rosters(args.db)
        for r in (report, report2):
            for table, count in r.rows_written.items():
                print(f"{table}: {count} rows written")
            if r.notes:
                print(f"notes: {'; '.join(r.notes[:10])}", file=sys.stderr)
    except (httpx.HTTPError, OSError) as e:
        print(f"ERROR: network failure: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()