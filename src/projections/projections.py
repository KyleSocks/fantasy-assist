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
    _compute_pos_rates,
    era_factors,
    season_games,
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
    """(positional per-GP means, positional median EV TOI/GP) for a skater.

    C -> centres, L/R -> wingers, anything else -> defencemen.
    """
    pg = _pos_group(position)
    group = pg if pg in ("C", "W") else "D"
    return (_compute_pos_means(conn, prior_seasons, group),
            _compute_pos_median_toi(conn, prior_seasons, group))


def _group_key(position: str) -> str:
    pg = _pos_group(position)
    return pg if pg in ("C", "W") else "D"


def _config(overrides: dict | None) -> dict:
    """regression.yaml with optional overrides (the backtest's --set/--grid)."""
    cfg = _load_config()
    if overrides:
        cfg = {**cfg, **overrides}
    return cfg


def _open(db_path: str) -> sqlite3.Connection:
    """Connect, adding any columns newer than the DB (e.g. sh_points) so an
    old database still projects before its first re-ingest."""
    from src.ingest.models import _add_missing_columns
    conn = sqlite3.connect(db_path)
    _add_missing_columns(conn)
    return conn


def _project_skaters(conn: sqlite3.Connection, season_id: int, cfg: dict,
                     prior_seasons: list[int],
                     include_inactive: bool = False) -> list[Projection]:
    groups = ("C", "W", "D")
    means = {g: _compute_pos_means(conn, prior_seasons, g) for g in groups}
    medians = {g: _compute_pos_median_toi(conn, prior_seasons, g) for g in groups}
    era = era_factors(conn, season_id, cfg)
    rates = {g: _compute_pos_rates(conn, prior_seasons, g, era) for g in groups}
    season_len = season_games(conn, season_id)
    # The draft board projects players on a roster; ex-NHL players
    # backfilled by src/ingest/bios.py (is_active = 0) are projected in a
    # backtest (the projected season's rows already exist) and for the
    # in-season tool (include_inactive: a call-up or waiver player needs
    # his own prior, not the league-average fallback).
    backtest = conn.execute("SELECT 1 FROM skater_seasons WHERE season_id = ?"
                            " LIMIT 1", (season_id,)).fetchone() is not None
    out: list[Projection] = []
    for (pid,) in conn.execute(
            "SELECT DISTINCT player_id FROM skater_seasons"
            " WHERE season_id < ? AND gp > 0", (season_id,)).fetchall():
        row = conn.execute(
            "SELECT position, COALESCE(is_active, 1) FROM players"
            " WHERE player_id = ?", (pid,)).fetchone()
        if not row or not row[0] or _pos_group(row[0]) == "G":
            continue
        if not (backtest or include_inactive) and row[1] == 0:
            continue
        g = _group_key(row[0])
        proj = _project_skater(pid, season_id, conn, cfg, means[g], medians[g],
                               rates[g], season_len, era)
        if proj is not None:
            out.append(proj)
    return out


def project_all(season_id: int, db_path: str,
                cfg_overrides: dict | None = None,
                include_inactive: bool = False) -> list[Projection]:
    """Project next-season totals for every skater and goalie.

    Skaters first: their projected goals by team feed the goalies' team
    context (src.projections.team_context). ``include_inactive`` also
    projects players not on a current roster (is_active = 0): the in-season
    tool needs them, the draft board must not see them.
    """
    conn = _open(db_path)
    try:
        cfg = _config(cfg_overrides)
        prior_seasons = _prior_seasons(conn, season_id)
        if not prior_seasons:
            return []
        results = _project_skaters(conn, season_id, cfg, prior_seasons,
                                   include_inactive)
        pos_means_g = _compute_pos_means(conn, prior_seasons, "G")
        results.extend(_project_goalies(season_id, conn, cfg, pos_means_g,
                                        skaters=results,
                                        include_inactive=include_inactive))
        return results
    finally:
        conn.close()


def explain(player_id: int, season_id: int, db_path: str) -> dict:
    """Return all intermediate terms for a player's projection.

    A goalie's terms depend on every team's projected roster, so goalies
    run the full projection (src.cache.project_all_cached memoizes it).
    """
    conn = _open(db_path)
    try:
        cfg = _config(None)
        prior_seasons = _prior_seasons(conn, season_id)
        if not prior_seasons:
            return {}
        row = conn.execute(
            "SELECT position FROM players WHERE player_id = ?",
            (player_id,)).fetchone()
        proj = None
        if row and _pos_group(row[0]) != "G":
            g = _group_key(row[0])
            era = era_factors(conn, season_id, cfg)
            proj = _project_skater(
                player_id, season_id, conn, cfg,
                _compute_pos_means(conn, prior_seasons, g),
                _compute_pos_median_toi(conn, prior_seasons, g),
                _compute_pos_rates(conn, prior_seasons, g, era), era=era)
    finally:
        conn.close()
    if proj is None and not (row and _pos_group(row[0]) != "G"):
        from src.cache import project_all_cached
        proj = next((q for q in project_all_cached(season_id, db_path)
                     if q.player_id == player_id), None)
    if proj is None:
        return {}
    return proj.terms
