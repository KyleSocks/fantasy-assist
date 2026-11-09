"""Projection model for next-season per-category totals (skaters + goalies).

Ambiguity notes (simplest defensible option chosen):
- "career team games available" = sum of team games from schedule per season played.
- pp_points total uses PP TOI in the final multiplication.
- Goalie save_pct treated as a rate for regression (pos_mean = league avg).
- Position groups: L/R/C -> F, D -> D, G -> G.
"""
from __future__ import annotations

import sqlite3

# The model is split across sibling modules (common / skaters / goalies /
# goalie_allocation). Everything that used to live here is re-exported so
# existing imports -- tests, tools/diag.py, src.api, src.valuation -- keep
# working unchanged.
from .common import (  # noqa: F401
    CONFIG_PATH,
    REGRESSION_N,
    SKATER_CATEGORIES,
    SKATER_STATS,
    WEIGHTS,
    Projection,
    _compute_pos_means,
    _compute_pos_median_toi,
    _k_for,
    _level_value,
    _load_config,
    _pos_group,
    _season_weights,
    age_at,
)
from .goalie_allocation import (  # noqa: F401
    _allocate_goalie_starts,
    _apply_goalie_starts,
    _cap_team_to_three,
    _goalie_season_history,
    _recent_games_started,
    _rostered_goalies,
    _team_games_for,
)
from .goalies import (  # noqa: F401
    _blank_goalie_projection,
    _project_goalie,
    _project_goalies,
)
from .skaters import _project_skater, _regression_n  # noqa: F401


def _prior_seasons(conn: sqlite3.Connection, season_id: int) -> list[int]:
    """Up to 3 skater seasons before ``season_id``, oldest first."""
    rows = conn.execute(
        "SELECT DISTINCT season_id FROM skater_seasons"
        " WHERE season_id < ? ORDER BY season_id DESC LIMIT 3",
        (season_id,)).fetchall()
    return [r[0] for r in reversed(rows)]


def _skater_baseline(conn: sqlite3.Connection, prior_seasons: list[int],
                     position: str) -> tuple[dict[str, float], float]:
    """(positional per-GP means, positional median TOI/GP) for a skater.

    C -> centres, L/R -> wingers, anything else -> defencemen.
    """
    pg = _pos_group(position)
    group = pg if pg in ("C", "W") else "D"
    return (_compute_pos_means(conn, prior_seasons, group),
            _compute_pos_median_toi(conn, prior_seasons, group))


def _open(db_path: str) -> sqlite3.Connection:
    """Connect, adding any columns newer than the DB (e.g. sh_points) so an
    old database still projects before its first re-ingest."""
    from src.ingest.models import _add_missing_columns
    conn = sqlite3.connect(db_path)
    _add_missing_columns(conn)
    return conn


def project_all(season_id: int, db_path: str) -> list[Projection]:
    """Project next-season totals for every skater and goalie."""
    conn = _open(db_path)
    try:
        cfg = _load_config()
        prior_seasons = _prior_seasons(conn, season_id)
        if not prior_seasons:
            return []

        baselines = {pos: _skater_baseline(conn, prior_seasons, pos)
                     for pos in ("C", "L", "D")}
        pos_means_g = _compute_pos_means(conn, prior_seasons, "G")

        skaters = [r[0] for r in conn.execute(
            "SELECT DISTINCT player_id FROM skater_seasons"
            " WHERE season_id < ? AND gp > 0", (season_id,))]
        results: list[Projection] = []
        for pid in skaters:
            row = conn.execute(
                "SELECT position FROM players WHERE player_id = ?",
                (pid,)).fetchone()
            if not row:
                continue
            pg = _pos_group(row[0])
            pm, pmt = baselines["C" if pg == "C" else "L" if pg == "W" else "D"]
            proj = _project_skater(pid, season_id, conn, cfg, pm, pmt)
            if proj is not None:
                results.append(proj)

        # All goalies (per-team zero-sum starts allocation)
        results.extend(_project_goalies(season_id, conn, cfg, pos_means_g))
        return results
    finally:
        conn.close()


def explain(player_id: int, season_id: int, db_path: str) -> dict:
    """Return all intermediate terms for a player's projection."""
    conn = _open(db_path)
    try:
        cfg = _load_config()
        prior_seasons = _prior_seasons(conn, season_id)
        if not prior_seasons:
            return {}
        row = conn.execute(
            "SELECT position FROM players WHERE player_id = ?",
            (player_id,)).fetchone()
        if row and _pos_group(row[0]) != "G":
            pm, pmt = _skater_baseline(conn, prior_seasons, row[0])
            proj = _project_skater(player_id, season_id, conn, cfg, pm, pmt)
        else:
            pm_g = _compute_pos_means(conn, prior_seasons, "G")
            all_g = _project_goalies(season_id, conn, cfg, pm_g)
            proj = next((q for q in all_g if q.player_id == player_id), None)
    finally:
        conn.close()
    if proj is None:
        return {}
    return proj.terms
