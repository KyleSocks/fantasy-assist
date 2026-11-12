"""Shared projection building blocks: the Projection record, config access,
position groups, age curves, season weights and positional baselines."""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

import yaml

from src.paths import REGRESSION_CONFIG_PATH


CONFIG_PATH = REGRESSION_CONFIG_PATH


# Category definitions: (column, denominator_column, k_key)
SKATER_CATEGORIES = [
    ("goals", "toi_sec", "goals"),
    ("assists", "toi_sec", "assists"),
    ("shots", "toi_sec", "shots"),
    ("blocks", "toi_sec", "blocks"),
    ("hits", "toi_sec", "hits"),
    ("pp_points", "pp_toi_sec", "pp_points"),
]


# Skater stat names in canonical order (matches SKATER_CATEGORIES).
SKATER_STATS: tuple[str, ...] = tuple(c[0] for c in SKATER_CATEGORIES)

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
    # Shorthanded points (league stat SHP). Skaters only; projected outside
    # SKATER_CATEGORIES so explain()/band charts are unchanged.
    sh_points: float = 0.0
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
        " s.gp, p.position, s.sh_points"
        " FROM skater_seasons s JOIN players p ON s.player_id = p.player_id"
        " WHERE s.season_id IN ({}) AND s.gp > 0".format(
            ",".join(str(s) for s in season_ids)))
    rows = cur.fetchall()
    totals = {"goals": 0.0, "assists": 0.0, "shots": 0.0, "blocks": 0.0,
              "hits": 0.0, "pp_points": 0.0, "sh_points": 0.0}
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
            totals["sh_points"] += r[8] or 0
            total_gp += r[6]
    means = {}
    for cat in (*SKATER_STATS, "sh_points"):
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


# ── Per-60 positional baselines, team lookup, per-stat age curves ─────

# TOI denominator per skater stat for per-60 rates: "pp" = power-play TOI,
# everything else = total TOI (EV + PP + SH). Goals are not here: they are
# projected as shots x shooting %.
PER60_TOI = {"assists": "total", "shots": "total", "blocks": "total",
             "hits": "total", "pp_points": "pp", "sh_points": "total"}


def _toi_parts(ev, pp, sh) -> tuple[float, float, float]:
    """(ev, pp, sh) seconds with NULL as 0. toi_sec in skater_seasons is
    EVEN-STRENGTH time (the API's evTimeOnIce); sh_toi_sec is NULL until the
    first re-ingest after it was added, so total TOI is a slight undercount
    for penalty killers until then."""
    return float(ev or 0), float(pp or 0), float(sh or 0)


def _compute_pos_rates(conn: sqlite3.Connection, season_ids: list[int],
                       pos_group: str) -> dict:
    """Per-60 positional means, shooting % and mean TOI/GP by strength.

    Pooled (sum of stat / sum of TOI) over every player in the group across
    the given seasons, so it is the league rate per minute played.
    """
    positions = {"C": ("C",), "W": ("L", "R")}.get(pos_group, (pos_group,))
    rows = conn.execute(
        "SELECT s.gp, s.goals, s.assists, s.shots, s.blocks, s.hits,"
        " s.pp_points, s.sh_points, s.toi_sec, s.pp_toi_sec, s.sh_toi_sec"
        " FROM skater_seasons s JOIN players p ON s.player_id = p.player_id"
        " WHERE s.season_id IN ({}) AND s.gp > 0 AND p.position IN ({})".format(
            ",".join(str(s) for s in season_ids),
            ",".join(f"'{x}'" for x in positions))).fetchall()
    tot = {k: 0.0 for k in ("gp", "goals", "assists", "shots", "blocks",
                            "hits", "pp_points", "sh_points", "ev", "pp", "sh")}
    for r in rows:
        ev, pp, sh = _toi_parts(r[8], r[9], r[10])
        if ev + pp + sh <= 0:
            continue
        for i, k in enumerate(("gp", "goals", "assists", "shots", "blocks",
                               "hits", "pp_points", "sh_points")):
            tot[k] += float(r[i] or 0)
        tot["ev"] += ev
        tot["pp"] += pp
        tot["sh"] += sh
    total_toi = tot["ev"] + tot["pp"] + tot["sh"]
    per60 = {}
    for stat, denom in PER60_TOI.items():
        d = tot["pp"] if denom == "pp" else total_toi
        per60[stat] = 3600.0 * tot[stat] / d if d > 0 else 0.0
    gp = tot["gp"]
    return {
        "per60": per60,
        "sh_pct": tot["goals"] / tot["shots"] if tot["shots"] > 0 else 0.0,
        "pp_toi_pg": tot["pp"] / gp if gp > 0 else 0.0,
        "ev_toi_pg": tot["ev"] / gp if gp > 0 else 0.0,
        "sh_toi_pg": tot["sh"] / gp if gp > 0 else 0.0,
    }


def season_team(conn: sqlite3.Connection, table: str, pid: int,
                season_id: int) -> str | None:
    """The team a player suits up for in ``season_id``, without look-ahead
    on his stats.

    In a backtest the projected season's own row exists (rosters are known
    before a season starts, so using its team is fair); in production it
    does not, and the current roster (players.current_team_abbrev) is used.
    """
    row = conn.execute(
        f"SELECT team_abbrev FROM {table} WHERE player_id = ? AND season_id = ?",
        (pid, season_id)).fetchone()
    if row and row[0]:
        return row[0]
    row = conn.execute(
        "SELECT current_team_abbrev FROM players WHERE player_id = ?",
        (pid,)).fetchone()
    return row[0] if row and row[0] else None


def curve_for(cfg: dict, group: str, stat: str) -> dict:
    """Level curve for one stat. ``level_curves_by_stat`` (fitted from the
    database by ``python -m src.projections.age_fit``) wins when it has the
    stat; otherwise the shared forwards / defensemen curve."""
    by_stat = (cfg.get("level_curves_by_stat") or {}).get(group) or {}
    if stat in by_stat:
        return {int(a): float(v) for a, v in by_stat[stat].items()}
    return cfg["level_curves"][group]


# ── Games played (durability) ──────────────────────────────────────────

def _team_season_games(conn: sqlite3.Connection, season_id: int,
                       team: str | None) -> int:
    """Regular-season games (game_type 2) for a team, else the season's
    most games by any team, else 82."""
    if team:
        n = conn.execute(
            "SELECT COUNT(*) FROM schedule WHERE season_id = ? AND game_type = 2"
            " AND (home_abbrev = ? OR away_abbrev = ?)",
            (season_id, team, team)).fetchone()[0]
        if n:
            return int(n)
    rows = conn.execute(
        "SELECT home_abbrev, away_abbrev FROM schedule"
        " WHERE season_id = ? AND game_type = 2", (season_id,)).fetchall()
    counts: dict[str, int] = {}
    for h, a in rows:
        for t in (h, a):
            counts[t] = counts.get(t, 0) + 1
    return max(counts.values()) if counts else 82


def projected_play_share(seasons: list[tuple[int, int, str | None]],
                         conn: sqlite3.Connection, cfg: dict) -> dict:
    """Share of team games a player is projected to play.

    ``seasons``: (season_id, gp, team) for every prior season, any order.
    Seasons under ``gp_min_season_gp`` games are call-ups / cups of coffee,
    not evidence about health, and are skipped. The three most recent
    remaining seasons are weighted 5/3/2 and regressed toward
    ``gp_prior_share`` with ``gp_prior_weight`` (same units as the 5/3/2
    weights), then clamped to [gp_share_floor, 1].
    """
    min_gp = float(cfg.get("gp_min_season_gp", 10))
    prior = float(cfg.get("gp_prior_share", 0.88))
    k = float(cfg.get("gp_prior_weight", 3.0))
    floor = float(cfg.get("gp_share_floor", 0.40))
    used = sorted((s for s in seasons if (s[1] or 0) >= min_gp),
                  key=lambda s: -s[0])[:len(WEIGHTS)]
    num = den = 0.0
    shares = []
    for w, (sid, gp, team) in zip(WEIGHTS, used):
        games = _team_season_games(conn, sid, team)
        share = min(float(gp) / games, 1.0) if games else 1.0
        shares.append(share)
        num += w * share
        den += w
    raw = num / den if den else prior
    share = (num + k * prior) / (den + k)
    share = max(floor, min(share, 1.0))
    return {"raw_share": raw, "season_shares": shares, "share": share,
            "prior_share": prior, "prior_weight": k}
