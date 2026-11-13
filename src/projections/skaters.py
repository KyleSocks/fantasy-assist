"""Skater projection.

Per game, a skater's line is (rate per 60) x (projected TOI), with:

- assists, shots, blocks, hits, SHP: per 60 of TOTAL ice time (EV + PP +
  SH), season-weighted 5/3/2, regressed toward the positional per-60 mean
  with n = TOI-minutes and k from config/regression.yaml;
- PPP: per 60 of POWER-PLAY time x projected PP TOI, so a player who loses
  or gains a PP1 spot moves with it;
- goals: projected shots x shooting %. Shot volume is stable year to year;
  shooting % is mostly luck, so it is regressed toward the positional
  sh% over the player's whole career with n = career shots, k = ``goals``;
- a smooth rookie prior (weight falls linearly to 0 at rookie_ramp_gp);
- per-stat age curves (see common.curve_for);
- games played from projected_play_share (regular season only).

terms keep the explain()/band contract: raw_rates, regressed_rate and
pos_mean are PER GAME (regressed_rate at the projected TOI), and
regressed_rate[cat] * age_adj_by_stat[cat] * proj_gp == final[cat].
"""
from __future__ import annotations

import logging
import sqlite3

from .common import (
    PER60_TOI,
    SKATER_STATS,
    Projection,
    _compute_pos_rates,
    _k_for,
    _level_value,
    _pos_group,
    _season_weights,
    _toi_parts,
    age_at,
    curve_for,
    era_factors,
    projected_play_share,
    season_games,
    season_team,
)

# Same logger name as before the split so existing log config still applies.
log = logging.getLogger("src.projections.projections")

_COLS = ("season_id, gp, goals, assists, shots, blocks, hits, pp_points,"
         " pp_toi_sec, toi_sec, team_abbrev, sh_points, sh_toi_sec")


def _regression_n(seasons: list[tuple], stat: str) -> float:
    """Per-stat sample size n over the observed seasons, in k's units:
    TOI-minutes (PP minutes for pp_points). Goals use career shots and are
    computed in _project_skater. Never games played."""
    if stat == "goals":
        return float(sum((s[4] or 0) for s in seasons))
    if stat == "pp_points":
        return float(sum((s[8] or 0) for s in seasons)) / 60.0
    return sum(sum(_toi_parts(s[9], s[8], s[12])) for s in seasons) / 60.0


def _weighted(values: list[float | None], weights: list[float]) -> float:
    """Weighted mean over the seasons that have a value, renormalized."""
    pairs = [(w, v) for w, v in zip(weights, values) if v is not None]
    den = sum(w for w, _ in pairs)
    return sum(w * v for w, v in pairs) / den if den > 0 else 0.0


def _prior_season_ids(conn: sqlite3.Connection, season_id: int) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT season_id FROM skater_seasons WHERE season_id < ?"
        " ORDER BY season_id DESC LIMIT 3", (season_id,))]


def _rookie_weight(cfg: dict, career_gp: float) -> float:
    """Weight on the positional mean for a small-sample player: starts at
    rookie_prior_weight with 0 career GP and falls linearly to 0 at
    rookie_ramp_gp (replaces the old 50/50-then-nothing cliff at 100)."""
    w0 = float(cfg.get("rookie_prior_weight", 0.5))
    ramp = float(cfg.get("rookie_ramp_gp", 200))
    if ramp <= 0:
        return 0.0
    return w0 * max(0.0, 1.0 - career_gp / ramp)


def _project_skater(pid: int, season_id: int, conn: sqlite3.Connection,
                    cfg: dict, pos_means: dict, pos_median_toi: float,
                    pos_rates: dict | None = None,
                    season_len: int | None = None,
                    era: dict | None = None) -> Projection | None:
    """Project one skater. Returns None if excluded.

    ``pos_means`` (per-GP positional means) is kept for callers that pass
    it; the model uses ``pos_rates`` (per-60, sh%, TOI/GP from
    common._compute_pos_rates), computed here when not supplied.
    ``pos_median_toi`` is the positional median EVEN-STRENGTH TOI/GP in
    seconds (the rookie TOI cap).
    """
    row = conn.execute(
        "SELECT birth_date, position FROM players WHERE player_id = ?",
        (pid,)).fetchone()
    if row is None:
        return None
    birth_date, pos = row
    pg = _pos_group(pos)
    if era is None:
        era = era_factors(conn, season_id, cfg)
    if pos_rates is None:
        pos_rates = _compute_pos_rates(
            conn, _prior_season_ids(conn, season_id),
            pg if pg in ("C", "W") else "D", era)
    age = age_at(birth_date, season_id)

    # Every prior season (no look-ahead): career GP / shots / goals and GP.
    history = conn.execute(
        f"SELECT {_COLS} FROM skater_seasons WHERE player_id = ?"
        " AND season_id < ? AND gp > 0 ORDER BY season_id DESC",
        (pid, season_id)).fetchall()
    if not history:
        return None
    seasons = history[:3]
    if all(sum(_toi_parts(s[9], s[8], s[12])) == 0 for s in seasons):
        log.info("Excluding player %d: no ice time in observed seasons", pid)
        return None
    career_gp = float(sum(s[1] for s in history))
    weights = _season_weights([s[1] for s in seasons])
    sw = cfg.get("skater_season_weights")
    if sw:                       # same form as 5/3/2: x min(gp / 50, 1)
        raw_w = [float(w) * min(s[1] / 50.0, 1.0) for w, s in zip(sw, seasons)]
        weights = [w / sum(raw_w) for w in raw_w]

    # ── ice time per game by strength ────────────────────────────────
    ev_pg = _weighted([s[9] / s[1] if s[9] is not None else None
                       for s in seasons], weights)
    pp_pg = _weighted([s[8] / s[1] if s[8] is not None else None
                       for s in seasons], weights)
    sh_pg = _weighted([s[12] / s[1] if s[12] is not None else None
                       for s in seasons], weights)
    if career_gp < 100:                      # rookie EV TOI cap
        ev_pg = min(ev_pg, pos_median_toi)
    new_team = season_team(conn, "skater_seasons", pid, season_id)
    changed = bool(new_team and seasons[0][10] and new_team != seasons[0][10])
    if changed:
        # A new team's power-play depth chart is unknown: pull PP TOI part
        # of the way toward the positional mean.
        b = float(cfg.get("team_change_pp_blend", 0.25))
        pp_pg = (1 - b) * pp_pg + b * pos_rates["pp_toi_pg"]
        # ... and players who move tend to land smaller roles than the one
        # they left (they are let go, or join a deeper lineup).
        f = float(cfg.get("team_change_toi_factor", 1.0))
        ev_pg, pp_pg, sh_pg = ev_pg * f, pp_pg * f, sh_pg * f
    toi = {"total": ev_pg + pp_pg + sh_pg, "pp": pp_pg}

    # ── per-60 rates, regressed ──────────────────────────────────────
    rw = _rookie_weight(cfg, career_gp)
    per60_raw: dict[str, float] = {}
    per60_reg: dict[str, float] = {}
    n_totals: dict[str, float] = {}
    k_vals: dict[str, float] = {}
    col = {"assists": 3, "shots": 4, "blocks": 5, "hits": 6, "pp_points": 7,
           "sh_points": 11}
    for stat, denom in PER60_TOI.items():
        vals = []
        for s in seasons:
            ev, pp, sh = _toi_parts(s[9], s[8], s[12])
            d = pp if denom == "pp" else ev + pp + sh
            vals.append(3600.0 * (s[col[stat]] or 0) * era.get(s[0], {}).get(stat, 1.0)
                        / d if d > 0 else None)
        raw = _weighted(vals, weights)
        n = _regression_n(seasons, stat)
        k = (float(cfg.get("sh_points", 600)) if stat == "sh_points"
             else _k_for(cfg, stat))
        pm = pos_rates["per60"][stat]
        reg = (raw * n + pm * k) / (n + k)
        per60_raw[stat] = raw
        per60_reg[stat] = (1 - rw) * reg + rw * pm
        n_totals[stat], k_vals[stat] = n, k

    # ── goals = shots x shooting % (career, regressed) ───────────────
    # (era: each season's goals restated at the latest league shooting %)
    career_shots = float(sum((s[4] or 0) for s in history))
    career_goals = float(sum((s[2] or 0) * era.get(s[0], {}).get("sh_pct", 1.0)
                             for s in history))
    k_sh = _k_for(cfg, "goals")
    pm_sh = pos_rates["sh_pct"]
    sh_pct = (career_goals + pm_sh * k_sh) / (career_shots + k_sh)
    sh_pct = (1 - rw) * sh_pct + rw * pm_sh
    n_totals["goals"], k_vals["goals"] = career_shots, k_sh

    # ── per-game rates at projected TOI ──────────────────────────────
    def per_game(stat: str, r60: float) -> float:
        return r60 * toi[PER60_TOI[stat]] / 3600.0

    regressed = {s: per_game(s, per60_reg[s]) for s in PER60_TOI}
    regressed["goals"] = regressed["shots"] * sh_pct
    pos_mean = {s: per_game(s, pos_rates["per60"][s]) for s in PER60_TOI}
    pos_mean["goals"] = pos_mean["shots"] * pm_sh
    raw_pg = {s: _weighted([(x[c] or 0) / x[1] for x in seasons], weights)
              for s, c in (("goals", 2), ("assists", 3), ("shots", 4),
                           ("blocks", 5), ("hits", 6), ("pp_points", 7),
                           ("sh_points", 11))}

    # ── age: L(proj_age) / L(obs_age), per stat ──────────────────────
    group = "defensemen" if pg == "D" else "forwards"
    obs_age = None
    if age is not None:
        obs_age = sum(weights[i] * age_at(birth_date, s[0])
                      for i, s in enumerate(seasons))
    age_by_stat: dict[str, float] = {}
    for stat in (*SKATER_STATS, "sh_points"):
        curve = curve_for(cfg, group, stat)
        age_by_stat[stat] = (1.0 if age is None else
                             _level_value(curve, age) / _level_value(curve, obs_age))
    base_curve = cfg["level_curves"][group]
    L_obs = 1.0 if age is None else _level_value(base_curve, obs_age)
    L_proj = 1.0 if age is None else _level_value(base_curve, age)

    # ── games played ─────────────────────────────────────────────────
    play = projected_play_share([(s[0], s[1], s[10]) for s in history],
                                conn, cfg)
    if season_len is None:
        season_len = season_games(conn, season_id)
    proj_gp = float(season_len) * play["share"]

    finals = {s: regressed[s] * age_by_stat[s] * proj_gp
              for s in (*SKATER_STATS, "sh_points")}
    terms = {
        "raw_rates": {s: raw_pg[s] for s in SKATER_STATS},
        "season_weights": weights,
        "pos_mean": {s: pos_mean[s] for s in SKATER_STATS},
        "k": {s: k_vals[s] for s in SKATER_STATS},
        "n": {s: n_totals[s] for s in SKATER_STATS},
        "regressed_rate": {s: regressed[s] for s in SKATER_STATS},
        "obs_age": obs_age, "L_obs": L_obs, "L_proj": L_proj,
        "age_adj": L_proj / L_obs,
        "age_adj_by_stat": {s: age_by_stat[s] for s in SKATER_STATS},
        "proj_toi_per_gp": ev_pg,
        "toi": {"ev_pg": ev_pg, "pp_pg": pp_pg, "sh_pg": sh_pg,
                "total_pg": toi["total"], "team_changed": changed,
                "team": new_team},
        "per60": {"raw": per60_raw, "regressed": per60_reg,
                  "pos_mean": dict(pos_rates["per60"])},
        "sh_pct": {"career_goals": career_goals, "career_shots": career_shots,
                   "pos_mean": pm_sh, "k": k_sh, "regressed": sh_pct},
        "rookie_weight": rw,
        "play_share": play,
        "season_games": season_len,
        "proj_gp": proj_gp,
        "final": {s: finals[s] for s in SKATER_STATS},
        "sh_points": {"raw_rate": raw_pg["sh_points"],
                      "pos_mean": pos_mean["sh_points"],
                      "k": k_vals["sh_points"], "n": n_totals["sh_points"],
                      "regressed_rate": regressed["sh_points"],
                      "final": finals["sh_points"]},
    }
    return Projection(
        player_id=pid, season_id=season_id,
        gp=proj_gp, toi_per_gp=ev_pg,
        goals=finals["goals"], assists=finals["assists"],
        shots=finals["shots"], blocks=finals["blocks"],
        hits=finals["hits"], pp_points=finals["pp_points"],
        wins=None, save_pct=None, position=pos, terms=terms,
        sh_points=finals["sh_points"],
    )
