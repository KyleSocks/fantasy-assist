"""Windowed (regular-week vs playoff-week) fantasy valuation.

Values players over a schedule-driven game window instead of the full
82-game season. The window is a contiguous run of weekly windows derived
purely from the league config (``fantasy_start_date`` advanced 7 days per
week, no hardcoded dates):

    week N spans fantasy_start_date + 7*(N-1) .. + 6 days

Per-window totals are derived from the season projection:

    per_game[cat] = season_total[cat] / gp
    durability     = gp / 82
    window_gp      = window_games[team] * durability
    window_total   = per_game[cat] * window_gp

i.e. every count total scales by ``window_gp / gp`` (equivalently
``window_games_count / 82``); rates (``save_pct``, ``toi_per_gp``) are
left untouched. ``valuate()`` then runs twice — once per window, each
pass deriving its OWN replacement levels from that window's totals — and
the results combine as:

    total_value = (1 - w) * vorp_regular + w * vorp_playoff

where ``w = cfg.playoff_weight``.

Players whose team is missing from the players table are valued at the
league-mean window games and logged, never dropped.

CLI: python -m src.valuation.schedule_value [--league NAME]
prints the acceptance report (week grid, window game totals, top-20
reorder vs the old season-long VORP, same-projection/different-games
pair, playoff_weight=0.0 reproduction, gp_regular < 82 invariant, and
the short-schedule hard fail).
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sqlite3
from pathlib import Path

from src.projections.projections import Projection, project_all
from src.valuation.league import LeagueConfig, load_league
from src.valuation.valuation import PlayerValue, valuate, valuate_windowed

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "hockey.db"
LEAGUE_DIR = ROOT / "config" / "leagues"
INGEST_COMMAND = "uv run python -m src.ingest.ingest --seasons 1"


def _resolve_db(db_path: str | Path | None = None) -> str:
    return str(db_path) if db_path is not None else str(DB_PATH)


def _require_window(cfg: LeagueConfig) -> None:
    if (
        cfg.fantasy_start_date is None
        or cfg.regular_weeks is None
        or cfg.playoff_weeks is None
    ):
        raise ValueError(
            f"league '{cfg.name}' has no window config (fantasy_start_date, "
            "regular_weeks, playoff_weeks); windowed valuation requires it"
        )


def week_bounds(cfg: LeagueConfig) -> dict[int, tuple[str, str]]:
    """Map every window week number to its (start_iso, end_iso) dates.

    Pure date arithmetic: week N spans fantasy_start_date + 7*(N-1)
    through + 6 days.
    """
    _require_window(cfg)
    start = datetime.date.fromisoformat(cfg.fantasy_start_date)
    bounds: dict[int, tuple[str, str]] = {}
    for w in _all_weeks(cfg):
        s = start + datetime.timedelta(days=7 * (w - 1))
        e = s + datetime.timedelta(days=6)
        bounds[w] = (s.isoformat(), e.isoformat())
    return bounds


def regular_week_numbers(cfg: LeagueConfig) -> list[int]:
    """Inclusive list of regular-window week numbers."""
    _require_window(cfg)
    lo, hi = cfg.regular_weeks
    return list(range(lo, hi + 1))


def playoff_week_numbers(cfg: LeagueConfig) -> list[int]:
    """Inclusive list of playoff-window week numbers."""
    _require_window(cfg)
    lo, hi = cfg.playoff_weeks
    return list(range(lo, hi + 1))


def _all_weeks(cfg: LeagueConfig) -> list[int]:
    return sorted(
        set(regular_week_numbers(cfg)) | set(playoff_week_numbers(cfg))
    )


def _week_of(date: str, cfg: LeagueConfig) -> int:
    """Window week number for a game date inside the window."""
    window_start = datetime.date.fromisoformat(cfg.fantasy_start_date)
    return 1 + (datetime.date.fromisoformat(date) - window_start).days // 7


def team_games(
    cfg: LeagueConfig, db_path: str | Path | None = None
) -> dict[str, dict[int, int]]:
    """Games each team plays in each window week.

    Returns ``{team_abbrev: {week_number: game_count}}`` over
    regular-season (``game_type=2``) games inside the fantasy window.
    """
    bounds = week_bounds(cfg)
    start = bounds[min(bounds)][0]
    end = bounds[max(bounds)][1]
    db = _resolve_db(db_path)
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT game_date, home_abbrev, away_abbrev FROM schedule "
            "WHERE game_type=2 AND game_date>=? AND game_date<=?",
            (start, end),
        ).fetchall()
    except sqlite3.OperationalError as e:
        raise ValueError(
            f"could not read the schedule table: {e}. Run the ingest to "
            f"populate it: {INGEST_COMMAND}"
        ) from e
    finally:
        conn.close()
    out: dict[str, dict[int, int]] = {}
    for date, home, away in rows:
        w = _week_of(date, cfg)
        for team in (home, away):
            if team:
                out.setdefault(team, {})
                out[team][w] = out[team].get(w, 0) + 1
    return out


def window_games(
    cfg: LeagueConfig, window: str, db_path: str | Path | None = None
) -> dict[str, int]:
    """Total games each team plays inside one window.

    ``window`` is ``"regular"`` or ``"playoff"``. Returns
    ``{team_abbrev: count}``.
    """
    if window not in ("regular", "playoff"):
        raise ValueError(
            f"unknown window '{window}' (expected 'regular' or 'playoff')"
        )
    tg = team_games(cfg, db_path)
    weeks = (
        regular_week_numbers(cfg)
        if window == "regular"
        else playoff_week_numbers(cfg)
    )
    return {
        team: sum(counts.get(w, 0) for w in weeks)
        for team, counts in tg.items()
    }


def assert_schedule_covers(
    cfg: LeagueConfig, db_path: str | Path | None = None
) -> None:
    """Hard-fail when the schedule does not cover the fantasy window.

    Two checks:
    - per-week coverage: every window week has at least one regular-season
      (``game_type=2``) game in its date range;
    - tail coverage: the schedule's latest regular-season game is on or
      after the window's final week's end date.

    A calendar day with no games (preseason, Thanksgiving, Christmas,
    All-Star break) is NOT an error — only a missing week or a truncated
    tail is. Raises ``ValueError`` naming the gap and the ingest command.
    """
    bounds = week_bounds(cfg)
    db = _resolve_db(db_path)
    conn = sqlite3.connect(db)
    try:
        max_date = conn.execute(
            "SELECT MAX(game_date) FROM schedule WHERE game_type=2"
        ).fetchone()[0]
        for w in sorted(bounds):
            s, e = bounds[w]
            n = conn.execute(
                "SELECT COUNT(*) FROM schedule "
                "WHERE game_type=2 AND game_date>=? AND game_date<=?",
                (s, e),
            ).fetchone()[0]
            if n == 0:
                raise ValueError(
                    f"week {w} ({s}..{e}) has no regular-season "
                    f"(game_type=2) games in the schedule. Run the ingest "
                    f"to extend the schedule: {INGEST_COMMAND}"
                )
    except sqlite3.OperationalError as e:
        raise ValueError(
            f"could not read the schedule table: {e}. Run the ingest to "
            f"populate it: {INGEST_COMMAND}"
        ) from e
    finally:
        conn.close()
    last_end = bounds[max(bounds)][1]
    if max_date is None or max_date < last_end:
        raise ValueError(
            f"schedule's last regular-season (game_type=2) game is "
            f"{max_date} but the window needs games through {last_end}. "
            f"Run the ingest to extend the schedule: {INGEST_COMMAND}"
        )


def windowed_projection(p: Projection, window_games_count: int) -> Projection:
    """Derive a windowed Projection from a season Projection.

    Every count total scales by ``window_gp / gp`` (equivalently
    ``window_games_count / 82``); rates (``save_pct``, ``toi_per_gp``) are
    left untouched. A player projected to 0 season games scales to 0 in
    the window (gp and all counts 0, rates preserved).
    """
    if p.gp > 0:
        window_gp = window_games_count * p.gp / 82.0
        scale = window_gp / p.gp
    else:
        window_gp = 0.0
        scale = 0.0
    return Projection(
        player_id=p.player_id,
        season_id=p.season_id,
        gp=window_gp,
        toi_per_gp=p.toi_per_gp,
        goals=p.goals * scale,
        assists=p.assists * scale,
        shots=p.shots * scale,
        blocks=p.blocks * scale,
        hits=p.hits * scale,
        pp_points=p.pp_points * scale,
        wins=(p.wins * scale) if p.wins is not None else None,
        save_pct=p.save_pct,
        shutouts=p.shutouts * scale,
        games_started=p.games_started * scale,
        goals_against=p.goals_against * scale,
        saves=p.saves * scale,
        position=p.position,
        terms=p.terms,
    )


def _mean_games(counts: dict[str, int]) -> float:
    if not counts:
        return 0.0
    return sum(counts.values()) / len(counts)


def _team_of(db: str) -> dict[int, str]:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT player_id, current_team_abbrev FROM players"
        ).fetchall()
    finally:
        conn.close()
    return {pid: team for pid, team in rows if team}


def windowed_values(
    projs: list[Projection],
    cfg: LeagueConfig,
    db_path: str | Path | None = None,
    team_of: dict[int, str] | None = None,
) -> list[PlayerValue]:
    """Two-pass windowed valuation combined into ``total_value``.

    Builds the regular- and playoff-window projection lists from the
    schedule-derived per-team window games, runs ``valuate_windowed()``
    (two ``valuate()`` passes, each with its own replacement levels), and
    returns the combined ``PlayerValue`` list. Players whose team is
    missing from the players table are valued at the league-mean window
    games and logged, never dropped.
    """
    _require_window(cfg)
    db = _resolve_db(db_path)
    assert_schedule_covers(cfg, db)
    reg_games = window_games(cfg, "regular", db)
    po_games = window_games(cfg, "playoff", db)
    reg_mean = _mean_games(reg_games)
    po_mean = _mean_games(po_games)
    if team_of is None:
        team_of = _team_of(db)
    reg_projs: list[Projection] = []
    po_projs: list[Projection] = []
    missing: list[int] = []
    for p in projs:
        team = team_of.get(p.player_id)
        if team is None or team not in reg_games:
            rg, pg = reg_mean, po_mean
            missing.append(p.player_id)
        else:
            rg, pg = reg_games[team], po_games[team]
        reg_projs.append(windowed_projection(p, rg))
        po_projs.append(windowed_projection(p, pg))
    if missing:
        log.warning(
            "windowed valuation: %d player(s) have no team in the window and "
            "are valued at league-mean games: %s",
            len(missing), missing,
        )
    return valuate_windowed(reg_projs, po_projs, cfg)


# ─── CLI acceptance report ─────────────────────────────────────────────


def next_season_id(db_path: str | Path | None = None) -> int:
    """The upcoming season id: max(skater_seasons.season_id) + 10001."""
    db = _resolve_db(db_path)
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT MAX(season_id) FROM skater_seasons").fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        raise ValueError(f"no skater_seasons rows in {db}")
    return int(row[0]) + 10001


def _names(db: str) -> dict[int, str]:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT player_id, full_name FROM players WHERE full_name IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    return {pid: name for pid, name in rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Windowed valuation acceptance report"
    )
    parser.add_argument(
        "--league",
        default="kyle_12_points",
        help="league name (YAML stem in config/leagues/)",
    )
    args = parser.parse_args(argv)
    cfg = load_league(str(LEAGUE_DIR / f"{args.league}.yaml"))
    season = next_season_id()
    projs = project_all(season, str(DB_PATH))
    db = str(DB_PATH)
    names = _names(db)

    print(f"== Window grid ({cfg.name}, season {season}) ==")
    bounds = week_bounds(cfg)
    reg_hi = cfg.regular_weeks[1]
    for w in sorted(bounds):
        s, e = bounds[w]
        kind = "regular" if w <= reg_hi else "playoff"
        print(f"  week {w:>2}  {s} .. {e}   ({kind})")

    reg_games = window_games(cfg, "regular")
    po_games = window_games(cfg, "playoff")
    print("== Window game totals (game_type=2 only) ==")
    print(
        f"  regular weeks {cfg.regular_weeks[0]}-{reg_hi}: "
        f"{sum(reg_games.values()) // 2} games across {len(reg_games)} teams "
        f"(per-team min {min(reg_games.values())}, "
        f"max {max(reg_games.values())})"
    )
    po_lo, po_hi = cfg.playoff_weeks
    print(
        f"  playoff weeks {po_lo}-{po_hi}: "
        f"{sum(po_games.values()) // 2} games across {len(po_games)} teams "
        f"(per-team min {min(po_games.values())}, "
        f"max {max(po_games.values())})"
    )

    vals = windowed_values(projs, cfg)
    old = valuate(list(projs), cfg)
    old_rank = {
        v.player_id: i + 1
        for i, v in enumerate(
            sorted(old, key=lambda v: (-v.vorp, v.player_id))
        )
    }
    top20 = sorted(vals, key=lambda v: (-v.total_value, v.player_id))[:20]
    print("== Top 20 by windowed total_value vs old season-long VORP rank ==")
    print("  new rank  old rank  name (pos)")
    for i, v in enumerate(top20, start=1):
        name = names.get(v.player_id, f"Player {v.player_id}")
        print(f"  {i:>8}  {old_rank[v.player_id]:>8}  {name} ({v.position})")

    best = top20[0]
    p = next(q for q in projs if q.player_id == best.player_id)
    team_of = _team_of(db)
    best_name = names.get(best.player_id, f"Player {best.player_id}")
    team = team_of.get(best.player_id, "?")
    team_max = max(reg_games, key=reg_games.get)
    team_min = min(reg_games, key=reg_games.get)
    hi = windowed_projection(p, reg_games[team_max])
    lo = windowed_projection(p, reg_games[team_min])
    print("== Same projection, different window games ==")
    print(
        f"  {best_name} (season projection: gp={p.gp:.1f}, goals={p.goals:.1f}, "
        f"assists={p.assists:.1f}, per-game goals={p.goals / p.gp:.4f})"
    )
    print(
        f"    on {team_max}'s regular window ({reg_games[team_max]} games): "
        f"window goals={hi.goals:.2f}, window gp={hi.gp:.1f}"
    )
    print(
        f"    on {team_min}'s regular window ({reg_games[team_min]} games): "
        f"window goals={lo.goals:.2f}, window gp={lo.gp:.1f}"
    )
    print(f"  (player's own team: {team})")

    zero_cfg = cfg.model_copy(update={"playoff_weight": 0.0})
    zero_vals = windowed_values(projs, zero_cfg)
    same_value = all(
        abs(z.total_value - z.vorp_regular) < 1e-9 for z in zero_vals
    )
    zero_top = [
        v.player_id
        for v in sorted(zero_vals, key=lambda v: (-v.total_value, v.player_id))
    ][:20]
    reg_top = [
        v.player_id
        for v in sorted(zero_vals, key=lambda v: (-v.vorp_regular, v.player_id))
    ][:20]
    print("== playoff_weight=0.0 reproduction ==")
    print(f"  total_value == vorp_regular for all players: {same_value}")
    print(f"  top-20 by total_value == top-20 by vorp_regular: {zero_top == reg_top}")

    max_gp_reg = max(v.gp_regular for v in vals)
    print("== gp_regular < 82 invariant ==")
    print(f"  max gp_regular = {max_gp_reg:.2f} (< 82: {max_gp_reg < 82})")

    short_cfg = cfg.model_copy(update={"playoff_weeks": [24, 30]})
    print("== short-schedule hard fail ==")
    try:
        assert_schedule_covers(short_cfg)
        print("  ERROR: expected a coverage failure but it passed")
        return 1
    except ValueError as e:
        print(f"  raised ValueError: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
