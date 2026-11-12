"""Goalie projection: the goalie's own skill (save %, start share, edge in
wins over his teams) combined with his projected team (team_context).

Per start, once the team's starts are allocated (goalie_allocation):

    wins   = team win rate per game + regressed win edge
    SA     = team shots against per game x his shots-per-start ratio
    saves  = SA x SV%
    GA     = SA x (1 - SV%)
    SO     = Poisson P(0 goals) = exp(-GA per start) x shutout_scale,
             blended with his own shutouts per start (k = shutout_k_starts)

The win edge is how much better his win rate was than his team's in each
season (wins are mostly a team stat; a starter's edge is small), regressed
toward 0 with k = goalie_win_rate_k starts. SV% regresses toward the pool
mean with k = goalie_save_pct_k shots; an optional additive age curve
(``goalie_sv_age``, fitted by src.projections.age_fit) applies on top.
"""
from __future__ import annotations

import math
import sqlite3

from .common import Projection, _level_value, _season_weights, age_at
from .goalie_allocation import (
    _allocate_goalie_starts,
    _apply_goalie_starts,
    _rostered_goalies,
)
from .team_context import _canon, project_teams, team_history, team_lookup


def _prior_goalie_seasons(conn: sqlite3.Connection, season_id: int) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT season_id FROM goalie_seasons WHERE season_id < ?"
        " ORDER BY season_id DESC LIMIT 3", (season_id,))]


def _project_goalie(pid: int, season_id: int, conn: sqlite3.Connection,
                    cfg: dict, pos_means: dict,
                    hist: dict | None = None) -> Projection | None:
    """Project one goalie's own skill terms (before team context).

    Starts regress toward 0 (no floor): n = total starts, k =
    goalie_starts_k. Per-start counts are provisional (his last team's
    regressed history) until _finalize_goalie applies the projected team.
    """
    seasons = conn.execute(
        "SELECT season_id, gp, games_started, wins, saves, shots_against,"
        " goals_against, shutouts, toi_sec, team_abbrev"
        " FROM goalie_seasons WHERE player_id = ? AND season_id < ? AND gp > 0"
        " ORDER BY season_id DESC LIMIT 3", (pid, season_id)).fetchall()
    if not seasons:
        return None
    if hist is None:
        hist = team_history(conn, _prior_goalie_seasons(conn, season_id))
    weights = _season_weights([s[1] for s in seasons])

    w_starts = sum(weights[i] * (s[2] or 0) for i, s in enumerate(seasons))
    n_starts = float(sum((s[2] or 0) for s in seasons))
    k_starts = float(cfg.get("goalie_starts_k", 25))
    raw_starts = (w_starts * n_starts / (n_starts + k_starts)
                  if n_starts > 0 else 0.0)

    # Win edge over his own team, season by season.
    edge_num = edge_den = 0.0
    for w, s in zip(weights, seasons):
        gs = s[2] or 0
        team = hist.get(s[0], {}).get(_canon(s[9]))
        if gs > 0 and team and team["games"] > 0:
            edge_num += w * ((s[3] or 0) / gs - team["wins"] / team["games"])
            edge_den += w
    raw_edge = edge_num / edge_den if edge_den > 0 else 0.0

    # Shots he faces per start vs his team's shots against per game. A
    # starter faces fewer (he is pulled, relievers take the rest); a backup
    # more (relief appearances). Regressed toward 1 with goalie_sa_ratio_k
    # starts of weight.
    ratio_num = ratio_den = 0.0
    for w, s in zip(weights, seasons):
        gs = s[2] or 0
        team = hist.get(s[0], {}).get(_canon(s[9]))
        if gs > 0 and team and team["games"] > 0 and team["sa"] > 0:
            ratio_num += w * ((s[5] or 0) / gs) / (team["sa"] / team["games"])
            ratio_den += w
    raw_ratio = ratio_num / ratio_den if ratio_den > 0 else 1.0
    k_ratio = float(cfg.get("goalie_sa_ratio_k", 40))
    sa_ratio = 1.0 + (raw_ratio - 1.0) * n_starts / (n_starts + k_ratio)
    k_win = float(cfg.get("goalie_win_rate_k", 40))
    edge = raw_edge * n_starts / (n_starts + k_win)

    # Save %: weighted, regressed toward the pool mean in shots.
    total_sa = float(sum((s[5] or 0) for s in seasons))
    weighted_sp = sum(weights[i] * ((s[4] or 0) / s[5] if s[5] else 0.920)
                      for i, s in enumerate(seasons))
    k_sp = float(cfg.get("goalie_save_pct_k", 1000))
    pos_sp = pos_means.get("save_pct", 0.920)
    regressed_sp = (weighted_sp * total_sa + pos_sp * k_sp) / (total_sa + k_sp)
    age_delta = 0.0
    curve = cfg.get("goalie_sv_age")
    if curve:
        row = conn.execute("SELECT birth_date FROM players WHERE player_id = ?",
                           (pid,)).fetchone()
        bd = row[0] if row else None
        age = age_at(bd, season_id)
        if age is not None:
            obs_age = sum(weights[i] * age_at(bd, s[0])
                          for i, s in enumerate(seasons))
            c = {int(a): float(v) for a, v in curve.items()}
            age_delta = _level_value(c, age) - _level_value(c, obs_age)
    final_sp = regressed_sp + age_delta

    own_so = (sum(weights[i] * (s[7] or 0) for i, s in enumerate(seasons))
              / w_starts if w_starts > 0 else 0.0)
    toi = sum(weights[i] * ((s[8] or 0) / s[1]) for i, s in enumerate(seasons))

    terms = {
        "raw_rates": {"save_pct": weighted_sp, "win_edge_raw": raw_edge,
                      "win_edge": edge, "own_so_per_start": own_so,
                      "sa_ratio_raw": raw_ratio, "sa_ratio": sa_ratio,
                      "starts_raw": raw_starts, "wins_pct": 0.0,
                      "ga_per_start": 0.0, "sv_per_start": 0.0,
                      "so_per_start": 0.0},
        "season_weights": weights,
        "pos_mean": {"save_pct": pos_sp},
        "k": {"save_pct": k_sp, "starts": k_starts, "win_edge": k_win},
        "n": {"save_pct": total_sa, "starts": n_starts},
        "regressed_rate": {"save_pct": regressed_sp, "starts": raw_starts},
        "obs_age": None, "L_obs": 1.0, "L_proj": 1.0, "age_adj": 1.0,
        "sv_age_delta": age_delta,
        "proj_toi_per_gp": toi,
        "proj_gp": raw_starts,
        "final": {"wins": 0.0, "save_pct": final_sp, "shutouts": 0.0,
                  "games_started": raw_starts, "goals_against": 0.0,
                  "saves": 0.0},
    }
    p = Projection(
        player_id=pid, season_id=season_id,
        gp=raw_starts, toi_per_gp=toi,
        goals=0.0, assists=0.0, shots=0.0, blocks=0.0, hits=0.0, pp_points=0.0,
        wins=0.0, save_pct=final_sp, position="G", terms=terms,
        shutouts=0.0, games_started=raw_starts, goals_against=0.0, saves=0.0)
    # Provisional per-start rates: his last team, regressed history only.
    ctx = project_teams(conn, sorted(hist), cfg, hist=hist)
    _finalize_goalie(p, team_lookup(ctx, seasons[0][9]), cfg)
    return p


def _finalize_goalie(p: Projection, team: dict, cfg: dict) -> None:
    """Per-start rates from the projected team; rescales count totals at
    the goalie's current allocated starts."""
    raw = p.terms["raw_rates"]
    sv = p.save_pct if p.save_pct is not None else 0.0
    sa = team["sa_pg"] * raw.get("sa_ratio", 1.0)
    lo, hi = cfg.get("goalie_win_rate_bounds", [0.15, 0.80])
    raw["wins_pct"] = max(lo, min(hi, team["win_pg"] + raw.get("win_edge", 0.0)))
    raw["sv_per_start"] = sa * sv
    raw["ga_per_start"] = sa * (1.0 - sv)
    so_model = math.exp(-raw["ga_per_start"]) * float(cfg.get("shutout_scale", 1.0))
    n = p.terms["n"].get("starts", 0.0)
    k = float(cfg.get("shutout_k_starts", 60))
    raw["so_model_per_start"] = so_model
    raw["so_per_start"] = ((raw.get("own_so_per_start", 0.0) * n + so_model * k)
                           / (n + k))
    p.terms["team_context"] = dict(team)
    _apply_goalie_starts(p, p.terms["final"]["games_started"])


def _blank_goalie_projection(pid: int, season_id: int,
                             pos_means: dict) -> Projection:
    """Zero-start Projection for a rostered goalie with no season history."""
    pos_sp = pos_means.get("save_pct", 0.920)
    terms = {
        "raw_rates": {"save_pct": pos_sp, "win_edge_raw": 0.0, "win_edge": 0.0,
                      "own_so_per_start": 0.0, "starts_raw": 0.0,
                      "wins_pct": 0.0, "ga_per_start": 0.0,
                      "sv_per_start": 0.0, "so_per_start": 0.0},
        "season_weights": [],
        "pos_mean": {"save_pct": pos_sp},
        "k": {"save_pct": 1000, "starts": 25},
        "n": {"save_pct": 0, "starts": 0},
        "regressed_rate": {"save_pct": pos_sp, "starts": 0.0},
        "obs_age": None, "L_obs": 1.0, "L_proj": 1.0, "age_adj": 1.0,
        "sv_age_delta": 0.0,
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
        shutouts=0.0, games_started=0.0, goals_against=0.0, saves=0.0)


def _team_goals(skaters: list[Projection] | None) -> dict[str, float] | None:
    """Projected skater goals by the team each skater suits up for."""
    if not skaters:
        return None
    out: dict[str, float] = {}
    for p in skaters:
        team = (p.terms.get("toi") or {}).get("team")
        if team and p.position != "G":
            out[team] = out.get(team, 0.0) + (p.goals or 0.0)
    return out or None


def _project_goalies(season_id: int, conn: sqlite3.Connection,
                     cfg: dict, pos_means: dict,
                     skaters: list[Projection] | None = None) -> list[Projection]:
    """Project every rostered goalie, allocate starts per team (zero-sum),
    then apply each team's projected context.

    The pool is the current roster (players.current_team_abbrev); in a
    backtest, where the projected season's goalie rows exist, it is the
    goalies who played that season with that season's team.
    """
    hist = team_history(conn, _prior_goalie_seasons(conn, season_id))
    rostered = _rostered_goalies(conn, season_id)
    name_of = {pid: name for pid, name, _ in rostered}
    team_of = {pid: team for pid, _, team in rostered}
    results: list[Projection] = []
    for pid, _name, _team in rostered:
        proj = _project_goalie(pid, season_id, conn, cfg, pos_means, hist)
        if proj is None:
            proj = _blank_goalie_projection(pid, season_id, pos_means)
        results.append(proj)
    _allocate_goalie_starts(results, name_of, conn, season_id, cfg)

    sv_num: dict[str, float] = {}
    sv_den: dict[str, float] = {}
    for p in results:
        t, gs = team_of.get(p.player_id), p.games_started or 0.0
        if t and gs > 0 and p.save_pct is not None:
            sv_num[t] = sv_num.get(t, 0.0) + gs * p.save_pct
            sv_den[t] = sv_den.get(t, 0.0) + gs
    team_sv = {t: sv_num[t] / sv_den[t] for t in sv_num if sv_den[t] > 0}
    ctx = project_teams(conn, sorted(hist), cfg, _team_goals(skaters), team_sv,
                        hist=hist)
    for p in results:
        if p.terms.get("season_weights"):
            _finalize_goalie(p, team_lookup(ctx, team_of.get(p.player_id)), cfg)
    return results
