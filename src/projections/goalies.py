"""Goalie projection (raw starts and per-start rates) and the per-team
projection entry point that hands off to the starts allocation."""
from __future__ import annotations

import sqlite3

from .common import Projection, _season_weights
from .goalie_allocation import (
    _allocate_goalie_starts,
    _rostered_goalies,
)


def _project_goalie(pid: int, season_id: int, conn: sqlite3.Connection,
                    cfg: dict, pos_means: dict) -> Projection | None:
    """Project one goalie (raw starts, before per-team allocation).

    Starts are regressed toward zero (never a floor) with per-stat k/n:
    n = total games started across the observed seasons, k =
    goalie_starts_k from config -- a goalie with almost no history
    regresses toward almost no starts. save_pct regresses toward the
    pool mean with k = goalie_save_pct_k and n = total shots against.
    Count totals (wins, saves, goals-against, shutouts) are scaled from
    per-start rates by _apply_goalie_starts once the team's starts are
    allocated; save_pct is a rate and is not scaled.
    """
    seasons = conn.execute(
        "SELECT season_id, gp, games_started, wins, saves, shots_against,"
        " goals_against, shutouts, toi_sec, team_abbrev"
        " FROM goalie_seasons WHERE player_id = ? AND season_id < ? AND gp > 0"
        " ORDER BY season_id DESC LIMIT 3", (pid, season_id)).fetchall()
    if not seasons:
        return None

    gp_list = [s[1] for s in seasons]
    weights = _season_weights(gp_list)

    # Weighted recent games started, regressed toward 0 starts (per-stat
    # k/n: n = total starts across observed seasons, k = goalie_starts_k).
    w_starts = sum(weights[i] * (s[2] or 0) for i, s in enumerate(seasons))
    n_starts = sum((s[2] or 0) for s in seasons)
    k_starts = float(cfg.get("goalie_starts_k", 25))
    raw_starts = (w_starts * n_starts / (n_starts + k_starts)
                  if n_starts > 0 else 0.0)

    # Wins PER START (recency-weighted), regressed toward .500 with
    # k = goalie_win_rate_k starts. _apply_goalie_starts multiplies by the
    # allocated starts, so this must be a per-start rate: wins / TEAM games
    # (the old formula) counted the start share twice -- a 60-start, 33-win
    # goalie projected at 62 starts came out at 25 W instead of ~34.
    w_wins = sum(weights[i] * (s[3] or 0) for i, s in enumerate(seasons))
    k_win = float(cfg.get("goalie_win_rate_k", 40))
    raw_win = min(w_wins / w_starts, 1.0) if w_starts > 0 else 0.50
    wins_pct = (raw_win * n_starts + 0.50 * k_win) / (n_starts + k_win)

    # Save pct: weighted + regressed toward the pool mean with
    # k = goalie_save_pct_k, n = total shots against.
    save_pcts = []
    total_sa = 0
    for i, s in enumerate(seasons):
        sa = s[5]
        sv = s[4]
        if sa > 0:
            save_pcts.append(weights[i] * (sv / sa))
            total_sa += sa
        else:
            save_pcts.append(weights[i] * 0.920)
    weighted_sp = sum(save_pcts)
    k_sp = float(cfg.get("goalie_save_pct_k", 1000))
    pos_sp = pos_means.get("save_pct", 0.920)
    regressed_sp = (weighted_sp * total_sa + pos_sp * k_sp) / (total_sa + k_sp)

    # GA, SV and SO per start (recency-weighted across prior seasons).
    w_ga = 0.0
    w_sv = 0.0
    w_so = 0.0
    for i, s in enumerate(seasons):
        starts_i = s[2] or 0
        if starts_i > 0:
            w_ga += weights[i] * (s[6] or 0)   # goals_against
            w_sv += weights[i] * (s[4] or 0)   # saves
            w_so += weights[i] * (s[7] or 0)   # shutouts
    ga_per_start = w_ga / w_starts if w_starts > 0 else 0.0
    sv_per_start = w_sv / w_starts if w_starts > 0 else 0.0
    so_per_start = w_so / w_starts if w_starts > 0 else 0.0

    # No age curve for goalies: flat 1.00.
    age_adj = 1.0

    # TOI for goalie (not very meaningful but keep for completeness)
    toi_per_gp_vals = []
    for i, s in enumerate(seasons):
        if s[1] > 0 and s[8] is not None:
            toi_per_gp_vals.append(weights[i] * (s[8] / s[1]))
        else:
            toi_per_gp_vals.append(0.0)
    proj_toi = sum(toi_per_gp_vals)

    terms = {
        "raw_rates": {"save_pct": weighted_sp,
                       "ga_per_start": ga_per_start,
                       "sv_per_start": sv_per_start,
                       "so_per_start": so_per_start,
                       "wins_pct": wins_pct,
                       "starts_raw": raw_starts},
        "season_weights": weights,
        "pos_mean": {"save_pct": pos_sp},
        "k": {"save_pct": k_sp, "starts": k_starts},
        "n": {"save_pct": total_sa, "starts": n_starts},
        "regressed_rate": {"save_pct": regressed_sp, "starts": raw_starts},
        # Goalies keep flat 1.00: no age curve for save percentage.
        "obs_age": None, "L_obs": 1.0, "L_proj": 1.0, "age_adj": age_adj,
        "proj_toi_per_gp": proj_toi,
        "proj_gp": raw_starts,
        # Count totals are allocated per team by _allocate_goalie_starts;
        # save_pct is a rate and is final here.
        "final": {"wins": 0.0, "save_pct": regressed_sp * age_adj,
                   "shutouts": 0.0, "games_started": raw_starts,
                   "goals_against": 0.0, "saves": 0.0},
    }
    return Projection(
        player_id=pid, season_id=season_id,
        gp=raw_starts, toi_per_gp=proj_toi,
        goals=0.0, assists=0.0, shots=0.0, blocks=0.0, hits=0.0, pp_points=0.0,
        wins=0.0, save_pct=regressed_sp, position="G", terms=terms,
        shutouts=0.0, games_started=raw_starts,
        goals_against=0.0, saves=0.0,
    )


def _blank_goalie_projection(pid: int, season_id: int,
                             pos_means: dict) -> Projection:
    """Zero-start Projection for a rostered goalie with no season history."""
    pos_sp = pos_means.get("save_pct", 0.920)
    terms = {
        "raw_rates": {"save_pct": 0.920, "ga_per_start": 0.0,
                      "sv_per_start": 0.0, "so_per_start": 0.0,
                      "wins_pct": 0.50, "starts_raw": 0.0},
        "season_weights": [],
        "pos_mean": {"save_pct": pos_sp},
        "k": {"save_pct": 1000, "starts": 25},
        "n": {"save_pct": 0, "starts": 0},
        "regressed_rate": {"save_pct": pos_sp, "starts": 0.0},
        "obs_age": None, "L_obs": 1.0, "L_proj": 1.0, "age_adj": 1.0,
        "proj_toi_per_gp": 0.0,
        "proj_gp": 0.0,
        "final": {"wins": 0.0, "save_pct": pos_sp, "shutouts": 0.0,
                  "games_started": 0.0, "goals_against": 0.0, "saves": 0.0},
    }
    return Projection(
        player_id=pid, season_id=season_id,
        gp=0.0, toi_per_gp=0.0,
        goals=0.0, assists=0.0, shots=0.0, blocks=0.0, hits=0.0, pp_points=0.0,
        wins=0.0, save_pct=pos_sp, position="G", terms=terms,
        shutouts=0.0, games_started=0.0, goals_against=0.0, saves=0.0,
    )


def _project_goalies(season_id: int, conn: sqlite3.Connection,
                     cfg: dict, pos_means: dict) -> list[Projection]:
    """Project every rostered goalie and allocate starts per team (zero-sum).

    The goalie pool is defined by players.current_team_abbrev (position=G);
    goalie_seasons supplies statistics only and never determines which team
    a goalie is on. Rostered goalies without season history get a zero-start
    blank projection so they still appear in (and can be capped out of) the
    allocation.
    """
    rostered = _rostered_goalies(conn)
    name_of: dict[int, str] = {pid: name for pid, name, _ in rostered}
    results: list[Projection] = []
    for pid, full_name, team in rostered:
        proj = _project_goalie(pid, season_id, conn, cfg, pos_means)
        if proj is None:
            proj = _blank_goalie_projection(pid, season_id, pos_means)
        results.append(proj)
    _allocate_goalie_starts(results, name_of, conn, season_id, cfg)
    return results
