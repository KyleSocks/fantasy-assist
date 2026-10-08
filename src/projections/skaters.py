"""Skater projection: weighted per-GP rates, per-stat regression toward the
positional mean, age-curve adjustment, rookie prior and durability."""
from __future__ import annotations

import logging
import sqlite3

from .common import (
    REGRESSION_N,
    Projection,
    _k_for,
    _level_value,
    _pos_group,
    _season_weights,
    age_at,
)

# Same logger name as before the split so existing log config still applies.
log = logging.getLogger("src.projections.projections")


def _regression_n(seasons: list[tuple], stat: str) -> float:
    """Per-stat denominator n accumulated across the observed seasons.

    Units match the stat's k from config: total shots for goals, total
    TOI-minutes for assists/shots/blocks/hits, total PP TOI-minutes for
    pp_points. Never games played.
    """
    denom = REGRESSION_N[stat]
    if denom == "shots":
        return float(sum((s[4] or 0) for s in seasons))
    if denom == "pp_toi_min":
        return float(sum((s[8] or 0) for s in seasons)) / 60.0
    if denom == "toi_min":
        return float(sum((s[9] or 0) for s in seasons)) / 60.0
    raise ValueError(f"unknown regression denominator {denom!r}")


def _project_skater(pid: int, season_id: int, conn: sqlite3.Connection,
                    cfg: dict, pos_means: dict, pos_median_toi: float) -> Projection | None:
    """Project one skater. Returns None if excluded."""
    # Get birth date and position
    row = conn.execute(
        "SELECT birth_date, position FROM players WHERE player_id = ?",
        (pid,)).fetchone()
    if row is None:
        return None
    birth_date, pos = row
    pg = _pos_group(pos)
    age = age_at(birth_date, season_id)

    # Career GP (all seasons)
    career_row = conn.execute(
        "SELECT COALESCE(SUM(gp),0) FROM skater_seasons WHERE player_id = ?",
        (pid,)).fetchone()
    career_gp = career_row[0]

    # Get 3 most recent seasons before projected season, gp > 0
    seasons = conn.execute(
        "SELECT season_id, gp, goals, assists, shots, blocks, hits,"
        " pp_points, pp_toi_sec, toi_sec, team_abbrev"
        " FROM skater_seasons WHERE player_id = ? AND season_id < ? AND gp > 0"
        " ORDER BY season_id DESC LIMIT 3", (pid, season_id)).fetchall()
    if not seasons:
        return None

    # Check toi_sec == 0 exclusion
    if all((s[9] or 0) == 0 for s in seasons):
        log.info("Excluding player %d: toi_sec == 0 in all seasons", pid)
        return None

    # Season weights
    gp_list = [s[1] for s in seasons]
    weights = _season_weights(gp_list)

    # Per-GP rates per season
    rates_by_season = []
    for i, s in enumerate(seasons):
        gp, goals, assists, shots, blocks, hits, pp_pts, pp_toi, toi = (
            s[1], s[2], s[3], s[4], s[5], s[6], s[7], s[8], s[9])
        r = {}
        if gp > 0:
            r["goals"] = (goals or 0) / gp
            r["assists"] = (assists or 0) / gp
            r["shots"] = (shots or 0) / gp
            r["blocks"] = (blocks or 0) / gp
            r["hits"] = (hits or 0) / gp
            r["pp_points"] = (pp_pts or 0) / gp
        else:
            r["goals"] = r["assists"] = r["shots"] = r["blocks"] = r["hits"] = 0.0
            r["pp_points"] = 0.0
        rates_by_season.append(r)

    # Weighted per-GP rates and per-stat regression denominators (n).
    # n is accumulated across the observed seasons in the same units as
    # that stat's k: shots for goals, TOI-minutes otherwise.
    raw_rates: dict[str, float] = {}
    n_totals: dict[str, float] = {}
    for cat in ("goals", "assists", "shots", "blocks", "hits", "pp_points"):
        raw_rates[cat] = sum(weights[i] * rates_by_season[i][cat]
                             for i in range(len(seasons)))
        n_totals[cat] = _regression_n(seasons, cat)

    # Regress toward positional mean
    regressed_rate: dict[str, float] = {}
    pos_mean_dict: dict[str, float] = {}
    for cat in ("goals", "assists", "shots", "blocks", "hits", "pp_points"):
        k = _k_for(cfg, cat)
        n_val = n_totals[cat]
        pm = pos_means.get(cat, 0.0)
        regressed_rate[cat] = (raw_rates[cat] * n_val + pm * k) / (n_val + k)
        pos_mean_dict[cat] = pm

    # Age adjustment: level-curve ratio L(projected_age) / L(obs_age).
    # obs_age is the season-weighted mean age across observed seasons, using
    # the same season weights as the rate computation. The observed rate
    # already embodies the player's current-age performance, so dividing by
    # L(obs_age) applies decline/improvement once relative to the player's
    # own observed age instead of double-counting it (older players) or
    # never crediting improvement (younger players).
    if age is not None:
        curve = (cfg["level_curves"]["defensemen"] if pg == "D"
                 else cfg["level_curves"]["forwards"])
        obs_age = sum(weights[i] * age_at(birth_date, s[0])
                     for i, s in enumerate(seasons))
        L_obs = _level_value(curve, obs_age)
        L_proj = _level_value(curve, age)
        age_adj = L_proj / L_obs
    else:
        obs_age, L_obs, L_proj, age_adj = None, 1.0, 1.0, 1.0

    # Rookie prior
    if career_gp < 100:
        for cat in ("goals", "assists", "shots", "blocks", "hits", "pp_points"):
            regressed_rate[cat] = 0.5 * regressed_rate[cat] + 0.5 * pos_mean_dict[cat]

    # Projected TOI/game: weighted average of toi_sec/gp
    toi_per_gp_vals = []
    pp_toi_per_gp_vals = []
    for i, s in enumerate(seasons):
        if s[1] > 0 and s[9] is not None:
            toi_per_gp_vals.append(weights[i] * (s[9] / s[1]))
        else:
            toi_per_gp_vals.append(0.0)
        if s[1] > 0 and s[8] is not None:
            pp_toi_per_gp_vals.append(weights[i] * (s[8] / s[1]))
        else:
            pp_toi_per_gp_vals.append(0.0)
    proj_toi_per_gp = sum(toi_per_gp_vals)
    proj_pp_toi_per_gp = sum(pp_toi_per_gp_vals)

    # Rookie TOI cap
    if career_gp < 100 and pg != "G":
        proj_toi_per_gp = min(proj_toi_per_gp, pos_median_toi)

    # Projected GP: 82 * durability
    # career team games available
    team_games = 0
    all_seasons = conn.execute(
        "SELECT season_id, team_abbrev FROM skater_seasons WHERE player_id = ?",
        (pid,)).fetchall()
    for sid, team in all_seasons:
        g = conn.execute(
            "SELECT COUNT(*) FROM schedule WHERE season_id = ? AND"
            " (home_abbrev = ? OR away_abbrev = ?)",
            (sid, team, team)).fetchone()[0]
        team_games += g
    if team_games > 0:
        durability = career_gp / team_games
    else:
        durability = 0.70
    durability = max(0.55, min(durability, 1.0))
    proj_gp = 82.0 * durability

    # Totals
    terms = {
        "raw_rates": raw_rates,
        "season_weights": weights,
        "pos_mean": pos_mean_dict,
        "k": {cat: _k_for(cfg, cat) for cat in
              ("goals", "assists", "shots", "blocks", "hits", "pp_points")},
        "n": n_totals,
        "regressed_rate": regressed_rate,
        "obs_age": obs_age,
        "L_obs": L_obs,
        "L_proj": L_proj,
        "age_adj": age_adj,
        "proj_toi_per_gp": proj_toi_per_gp,
        "proj_gp": proj_gp,
        "final": {},
    }
    finals = {}
    for cat in ("goals", "assists", "shots", "blocks", "hits", "pp_points"):
        total = regressed_rate[cat] * age_adj * proj_gp
        finals[cat] = total
    terms["final"] = finals

    return Projection(
        player_id=pid, season_id=season_id,
        gp=proj_gp, toi_per_gp=proj_toi_per_gp,
        goals=finals["goals"], assists=finals["assists"],
        shots=finals["shots"], blocks=finals["blocks"],
        hits=finals["hits"], pp_points=finals["pp_points"],
        wins=None, save_pct=None, position=pos, terms=terms,
    )
