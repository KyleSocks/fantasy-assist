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


def project_all(season_id: int, db_path: str) -> list[Projection]:
    """Project next-season totals for every skater and goalie."""
    conn = sqlite3.connect(db_path)
    cfg = _load_config()
    # Determine which seasons to use for positional means
    prior_seasons = [r[0] for r in conn.execute(
        "SELECT DISTINCT season_id FROM skater_seasons"
        " WHERE season_id < ? ORDER BY season_id DESC LIMIT 3",
        (season_id,)).fetchall()]
    if not prior_seasons:
        return []
    prior_seasons.reverse()  # oldest first

    pos_means_c = _compute_pos_means(conn, prior_seasons, "C")
    pos_means_w = _compute_pos_means(conn, prior_seasons, "W")
    pos_means_d = _compute_pos_means(conn, prior_seasons, "D")
    pos_means_g = _compute_pos_means(conn, prior_seasons, "G")
    pos_median_toi_c = _compute_pos_median_toi(conn, prior_seasons, "C")
    pos_median_toi_w = _compute_pos_median_toi(conn, prior_seasons, "W")
    pos_median_toi_d = _compute_pos_median_toi(conn, prior_seasons, "D")

    # All skaters
    skaters = [r[0] for r in conn.execute(
        "SELECT DISTINCT player_id FROM skater_seasons"
        " WHERE season_id < ? AND gp > 0", (season_id,))]
    results: list[Projection] = []
    for pid in skaters:
        row = conn.execute(
            "SELECT position FROM players WHERE player_id = ?", (pid,)).fetchone()
        if not row:
            continue
        pg = _pos_group(row[0])
        if pg == "C":
            pm, pmt = pos_means_c, pos_median_toi_c
        elif pg == "W":
            pm, pmt = pos_means_w, pos_median_toi_w
        else:
            pm, pmt = pos_means_d, pos_median_toi_d
        proj = _project_skater(pid, season_id, conn, cfg, pm, pmt)
        if proj is not None:
            results.append(proj)

    # All goalies (per-team zero-sum starts allocation)
    results.extend(_project_goalies(season_id, conn, cfg, pos_means_g))

    conn.close()
    return results


def explain(player_id: int, season_id: int, db_path: str) -> dict:
    """Return all intermediate terms for a player's projection."""
    conn = sqlite3.connect(db_path)
    cfg = _load_config()
    prior_seasons = [r[0] for r in conn.execute(
        "SELECT DISTINCT season_id FROM skater_seasons"
        " WHERE season_id < ? ORDER BY season_id DESC LIMIT 3",
        (season_id,)).fetchall()]
    if not prior_seasons:
        conn.close()
        return {}
    prior_seasons.reverse()

    row = conn.execute(
        "SELECT position FROM players WHERE player_id = ?",
        (player_id,)).fetchone()
    if row and _pos_group(row[0]) != "G":
        pg = _pos_group(row[0])
        if pg == "C":
            pm = _compute_pos_means(conn, prior_seasons, "C")
            pmt = _compute_pos_median_toi(conn, prior_seasons, "C")
        elif pg == "W":
            pm = _compute_pos_means(conn, prior_seasons, "W")
            pmt = _compute_pos_median_toi(conn, prior_seasons, "W")
        else:
            pm = _compute_pos_means(conn, prior_seasons, "D")
            pmt = _compute_pos_median_toi(conn, prior_seasons, "D")
        proj = _project_skater(player_id, season_id, conn, cfg, pm, pmt)
    else:
        pm_g = _compute_pos_means(conn, prior_seasons, "G")
        all_g = _project_goalies(season_id, conn, cfg, pm_g)
        proj = next((q for q in all_g if q.player_id == player_id), None)

    conn.close()
    if proj is None:
        return {}
    return proj.terms
