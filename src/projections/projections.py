"""Projection model for next-season per-category totals (skaters + goalies).

Ambiguity notes (simplest defensible option chosen):
- "career team games available" = sum of team games from schedule per season played.
- pp_points total uses PP TOI in the final multiplication.
- Goalie save_pct treated as a rate for regression (pos_mean = league avg).
- Position groups: L/R/C -> F, D -> D, G -> G.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "regression.yaml"

# Category definitions: (column, denominator_column, k_key)
SKATER_CATEGORIES = [
    ("goals", "toi_sec", "goals"),
    ("assists", "toi_sec", "assists"),
    ("shots", "toi_sec", "shots"),
    ("blocks", "toi_sec", "blocks"),
    ("hits", "toi_sec", "hits"),
    ("pp_points", "pp_toi_sec", "pp_points"),
]
# Per-stat regression denominator for "n": accumulated across the observed
# seasons in the same units as that stat's k from config/regression.yaml —
# total shots for goals, total TOI-minutes for assists/shots/blocks/hits,
# total PP TOI-minutes for pp_points. Games played is never a denominator.
REGRESSION_N = {"goals": "shots", "assists": "toi_min", "shots": "toi_min",
               "blocks": "toi_min", "hits": "toi_min",
               "pp_points": "pp_toi_min"}

WEIGHTS = [5, 3, 2]  # most-recent-first


@dataclass
class Projection:
    player_id: int
    season_id: int
    gp: float
    toi_per_gp: float
    goals: float
    assists: float
    shots: float
    blocks: float
    hits: float
    pp_points: float
    wins: float | None
    save_pct: float | None
    shutouts: float = 0.0
    games_started: float = 0.0
    goals_against: float = 0.0
    saves: float = 0.0
    position: str = "F"
    terms: dict = field(default_factory=dict)


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _k_for(cfg: dict, stat: str) -> float:
    """Per-stat shrinkage k from config; raises if the stat has no entry.

    Every skater stat needs its own k in config/regression.yaml; there is
    deliberately no shared default to fall back to.
    """
    try:
        k = cfg[stat]
    except (KeyError, TypeError) as exc:
        raise KeyError(
            f"per-stat k for {stat!r} missing from {CONFIG_PATH}") from exc
    return float(k)


def _pos_group(pos: str) -> str:
    """Split into C, W (wingers L+R), D, G per spec 'split C/W/D'."""
    if pos == "C":
        return "C"
    if pos in ("L", "R"):
        return "W"
    return pos  # D or G


def age_at(birth_date: str | None, season_id: int) -> float | None:
    """Age in whole years as of Feb 1 of the season's END year.

    season_id 20242025 ends in 2025, so age is measured at 2025-02-01;
    season_id 20252026 ends in 2026, measured at 2026-02-01. The age
    increments only if the birthday has occurred on or before that date.
    """
    if not birth_date:
        return None
    end_year = season_id % 10000
    b_year = int(birth_date[:4])
    b_month = int(birth_date[5:7])
    b_day = int(birth_date[8:10])
    if b_month < 2 or (b_month == 2 and b_day <= 1):
        return float(end_year - b_year)
    return float(end_year - b_year - 1)


def _level_value(curve: dict[int, float], age: float | None) -> float:
    """L(age) from a level-curve table.

    Ages below/above the table clamp to the nearest entry; fractional ages
    interpolate linearly between bracketing integer ages. Missing age -> 1.0.
    """
    if age is None:
        return 1.0
    ages = sorted(curve)
    lo, hi = ages[0], ages[-1]
    if age <= lo:
        return float(curve[lo])
    if age >= hi:
        return float(curve[hi])
    a = math.floor(age)
    frac = age - a
    return (1 - frac) * float(curve[a]) + frac * float(curve[a + 1])


def _compute_pos_means(conn: sqlite3.Connection, season_ids: list[int],
                       pos_group: str) -> dict[str, float]:
    """Compute league per-60 rate means for a position group across given seasons."""
    if pos_group == "G":
        # For goalies, compute mean save_pct
        cur = conn.execute(
            "SELECT shots_against, saves FROM goalie_seasons "
            "WHERE season_id IN ({}) AND shots_against > 0".format(
                ",".join(str(s) for s in season_ids)))
        rows = cur.fetchall()
        if not rows:
            return {}
        total_sa = sum(r[0] for r in rows)
        total_sv = sum(r[1] for r in rows)
        return {"save_pct": total_sv / total_sa if total_sa > 0 else 0.920}
    # Skater positional means: per-GP rates
    cur = conn.execute(
        "SELECT s.goals, s.assists, s.shots, s.blocks, s.hits, s.pp_points,"
        " s.gp, p.position"
        " FROM skater_seasons s JOIN players p ON s.player_id = p.player_id"
        " WHERE s.season_id IN ({}) AND s.gp > 0".format(
            ",".join(str(s) for s in season_ids)))
    rows = cur.fetchall()
    totals = {"goals": 0.0, "assists": 0.0, "shots": 0.0, "blocks": 0.0,
              "hits": 0.0, "pp_points": 0.0}
    total_gp = 0
    for r in rows:
        if _pos_group(r[7]) != pos_group:
            continue
        if r[6] > 0:
            totals["goals"] += r[0] or 0
            totals["assists"] += r[1] or 0
            totals["shots"] += r[2] or 0
            totals["blocks"] += r[3] or 0
            totals["hits"] += r[4] or 0
            totals["pp_points"] += r[5] or 0
            total_gp += r[6]
    means = {}
    for cat in ("goals", "assists", "shots", "blocks", "hits", "pp_points"):
        means[cat] = totals[cat] / total_gp if total_gp > 0 else 0.0
    return means


def _compute_pos_median_toi(conn: sqlite3.Connection, season_ids: list[int],
                             pos_group: str) -> float:
    """Positional median of toi_sec/gp."""
    if pos_group == "C":
        positions = ("C",)
    elif pos_group == "W":
        positions = ("L", "R")
    else:
        positions = (pos_group,)
    cur = conn.execute(
        "SELECT s.toi_sec / s.gp FROM skater_seasons s"
        " JOIN players p ON s.player_id = p.player_id"
        " WHERE s.season_id IN ({}) AND s.gp > 0 AND s.toi_sec > 0"
        " AND p.position IN ({})".format(
            ",".join(str(s) for s in season_ids),
            ",".join(f"'{x}'" for x in positions)))
    vals = sorted(r[0] for r in cur.fetchall())
    if not vals:
        return 0
    mid = len(vals) // 2
    return vals[mid]


def _season_weights(gp_list: list[int]) -> list[float]:
    """Weight seasons 5/3/2, scale by min(gp/50,1), renormalize."""
    raw = []
    for i, gp in enumerate(gp_list):
        w = WEIGHTS[i] * min(gp / 50.0, 1.0)
        raw.append(w)
    total = sum(raw)
    if total == 0:
        n = len(raw)
        return [1.0 / n] * n
    return [w / total for w in raw]


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

    # Win rate from the most recent observed season's team record.
    prior = seasons[0]
    wins_pct = 0.50
    team = prior[9]
    team_games = _team_games_for(prior[0], conn).get(team, 0) if team else 0
    if team_games > 0:
        wins_pct = (prior[3] or 0) / team_games

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


def _team_games_for(season_id: int, conn: sqlite3.Connection) -> dict[str, int]:
    """Regular-season games per team from the schedule (game_type=2)."""
    team_games: dict[str, int] = {}
    rows = conn.execute(
        "SELECT home_abbrev, away_abbrev FROM schedule"
        " WHERE season_id = ? AND game_type = 2", (season_id,)).fetchall()
    for home, away in rows:
        if home:
            team_games[home] = team_games.get(home, 0) + 1
        if away:
            team_games[away] = team_games.get(away, 0) + 1
    return team_games


def _apply_goalie_starts(p: Projection, starts: float) -> None:
    """Scale a goalie's count totals from the allocated projected starts.

    Wins, saves, goals-against and shutouts scale with projected starts;
    save_pct and the per-start rates are untouched.
    """
    starts = max(starts, 0.0)
    raw = p.terms["raw_rates"]
    final = p.terms["final"]
    final["games_started"] = starts
    final["wins"] = raw["wins_pct"] * starts
    final["goals_against"] = raw["ga_per_start"] * starts
    final["saves"] = raw["sv_per_start"] * starts
    final["shutouts"] = raw["so_per_start"] * starts
    p.gp = starts
    p.games_started = starts
    p.wins = final["wins"]
    p.goals_against = final["goals_against"]
    p.saves = final["saves"]
    p.shutouts = final["shutouts"]
    p.terms["proj_gp"] = starts


def _rostered_goalies(conn: sqlite3.Connection) -> list[tuple[int, str, str | None]]:
    """Rostered goalies from the players table: (player_id, full_name, team).

    The current roster IS the source of team membership. goalie_seasons is
    never consulted to decide which team a goalie plays for.
    """
    cols = [r[1] for r in conn.execute("PRAGMA table_info(players)")]
    if "full_name" in cols:
        rows = conn.execute(
            "SELECT player_id, full_name, current_team_abbrev"
            " FROM players WHERE position = ? ORDER BY player_id", ("G",)).fetchall()
        return [(int(r[0]), r[1], r[2]) for r in rows]
    rows = conn.execute(
        "SELECT player_id, current_team_abbrev"
        " FROM players WHERE position = ? ORDER BY player_id", ("G",)).fetchall()
    return [(int(r[0]), None, r[1]) for r in rows]


def _recent_games_started(conn: sqlite3.Connection, pid: int) -> int:
    """Most recent season games_started from goalie_seasons (0 if none)."""
    row = conn.execute(
        "SELECT games_started FROM goalie_seasons"
        " WHERE player_id = ? AND gp > 0 ORDER BY season_id DESC LIMIT 1",
        (pid,)).fetchone()
    if row and row[0]:
        return int(row[0])
    return 0


def _goalie_season_history(conn: sqlite3.Connection, pid: int) -> list[tuple]:
    """All goalie_seasons rows for a goalie, ascending by season_id."""
    return conn.execute(
        "SELECT season_id, team_abbrev, games_started FROM goalie_seasons"
        " WHERE player_id = ? AND gp > 0 ORDER BY season_id ASC",
        (pid,)).fetchall()


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


def _cap_team_to_three(team: str, members: list[Projection],
                       name_of: dict[int, str],
                       conn: sqlite3.Connection) -> list[Projection]:
    """HARD INVARIANT: a team carries at most 3 goalies in the allocation.

    If the roster has more than 3, keep the 3 with the most recent-season
    games_started (tie-break: lower player_id first). Dropped goalies are
    printed by name with their season history and get 0 projected starts;
    this is a visible data-quality report, never a silent trim.
    """
    if len(members) <= 3:
        return members
    scored = []
    for p in members:
        recent = _recent_games_started(conn, p.player_id)
        scored.append((recent, p.player_id, p))
    scored.sort(key=lambda x: (-x[0], x[1]))
    kept = [item[2] for item in scored[:3]]
    dropped = scored[3:]
    kept_names = ", ".join(name_of.get(p.player_id, str(p.player_id))
                           for p in kept)
    print("[cap>3] %s: keeping %s, dropping %d rostered goalie(s)" % (
        team, kept_names, len(dropped)))
    for recent, pid, p in dropped:
        pname = name_of.get(pid, str(pid))
        print("  DROPPED: %s | team=%s | recent_starts=%d"
              % (pname, team, recent))
        history = _goalie_season_history(conn, pid)
        if history:
            for row in history:
                print("    %d %s starts=%s" % (row[0], row[1], row[2]))
        else:
            print("    (no goalie_seasons history)")
        _apply_goalie_starts(p, 0.0)
    return kept


def _allocate_goalie_starts(projs: list[Projection],
                            name_of: dict[int, str],
                            conn: sqlite3.Connection,
                            season_id: int, cfg: dict) -> None:
    """Zero-sum per-team allocation of projected goalie starts.

    The goalie pool and every team membership come from
    players.current_team_abbrev (see _rostered_goalies); goalie_seasons
    only supplies statistics. A goalie with no current_team_abbrev gets 0
    projected starts and is printed by name. Each team keeps at most 3
    goalies (see _cap_team_to_three). A team plays a fixed number of
    regular-season games, shared among its goalies, so the team's projected
    starts must sum to exactly that game count and no goalie may start more
    games than the team plays. Each goalie's raw starts become a share of
    the team total; a zero raw sum splits the team's games evenly and is
    logged. Capping at max_goalie_starts (regression.yaml, default 68)
    redistributes any remainder proportionally among the team's uncapped
    goalies, re-checking both invariants, at most 3 times. A cap or sum
    violation raises with the team abbreviation and the offending numbers;
    it is never clamped silently.
    """
    goalies = [p for p in projs if p.position == "G"]
    if not goalies:
        return
    cap = float(cfg.get("max_goalie_starts", 68))
    team_games = _team_games_for(season_id, conn)
    rostered = _rostered_goalies(conn)
    roster_pid = {pid for pid, _, _ in rostered}
    team_of: dict[int, str] = {}
    for pid, _, team in rostered:
        if team:
            team_of[pid] = team
    unassigned = [p for p in goalies
                  if p.player_id not in roster_pid
                  or p.player_id not in team_of]
    for p in unassigned:
        pname = name_of.get(p.player_id, str(p.player_id))
        print("[unassigned] %s | team=NONE | starts=0.00" % pname)
        _apply_goalie_starts(p, 0.0)
    by_team: dict[str, list[Projection]] = {}
    for p in goalies:
        if p.player_id in unassigned:
            continue
        team = team_of.get(p.player_id)
        if team is None:
            pname = name_of.get(p.player_id, str(p.player_id))
            print("[unassigned] %s | team=NONE | starts=0.00" % pname)
            _apply_goalie_starts(p, 0.0)
            continue
        by_team.setdefault(team, []).append(p)
    print("[per-team goalie count from players, pre-cap]:")
    for team in sorted(by_team):
        count = len(by_team[team])
        flag = "  <== FLAG >3" if count > 3 else ""
        print("  %s %d%s" % (team, count, flag))
    for team in list(by_team.keys()):
        by_team[team] = _cap_team_to_three(team, by_team[team], name_of, conn)
    print("[per-team goalie allocation, post-cap]:")
    for team in sorted(by_team):
        members = by_team[team]
        total = team_games.get(team, 0)
        if total <= 0:
            for p in members:
                _apply_goalie_starts(p, 0.0)
            print("  %s games=%d: %s" % (
                team, total,
                "; ".join("%s=%.2f" % (name_of.get(p.player_id, str(p.player_id)),
                                        0.0) for p in members)))
            continue
        raw = [p.terms["final"]["games_started"] for p in members]
        raw_sum = sum(raw)
        if raw_sum > 0:
            starts = [r / raw_sum * total for r in raw]
        else:
            starts = [total / len(members)] * len(members)
            log.warning(
                "team %s goalies have no start history; splitting %d games "
                "evenly across %d goalies", team, total, len(members))
        for _iteration in range(3):
            over = [i for i, s in enumerate(starts) if s > cap]
            if not over:
                break
            for i in over:
                starts[i] = cap
            remainder = total - sum(starts)
            uncapped = [i for i in range(len(starts)) if starts[i] < cap]
            if not uncapped:
                raise ValueError(
                    "goalie starts for %s: every goalie is at max starts "
                    "%g but the team plays %d games, so the remainder "
                    "cannot be allocated" % (team, cap, total))
            base = sum(starts[i] for i in uncapped)
            if base > 0:
                for i in uncapped:
                    starts[i] += remainder * starts[i] / base
            else:
                for i in uncapped:
                    starts[i] += remainder / len(uncapped)
        for p, s in zip(members, starts):
            if s > cap:
                raise ValueError(
                    "goalie starts for %s: player %s projected at %.4f "
                    "starts exceeds max_goalie_starts %g"
                    % (team, name_of.get(p.player_id, str(p.player_id)),
                       s, cap))
        starts_sum = sum(starts)
        if abs(starts_sum - total) > 0.01:
            raise ValueError(
                "goalie starts for %s: goalies sum to %.4f starts but the "
                "team plays %d games" % (team, starts_sum, total))
        for p, s in zip(members, starts):
            _apply_goalie_starts(p, s)
        detail = "; ".join("%s=%.2f"
                           % (name_of.get(p.player_id, str(p.player_id)), s)
                           for p, s in zip(members, starts))
        print("  %s games=%d sum=%.2f: %s" % (team, total, starts_sum, detail))


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
