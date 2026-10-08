"""Constants, metrics and the OLD-model reference used by backtest.py."""
from __future__ import annotations

import math
import sqlite3

from .projections import _level_value, age_at

CATS = ("goals", "assists", "shots", "blocks", "hits", "pp_points")

# Named-player diagnostics (id, label).
NAMED = (
    (8482157, "Cuylle"),
    (8479314, "Tkachuk"),
    (8473994, "Benn"),
)
# Double-counting demo target.
DUCHENE = 8475168
# Acceptance-audit player and season (projected to 20262027 per acceptance).
AUDIT_PLAYER = 8484801
AUDIT_SEASON = 20262027
# OLD shared shrinkage constant (before per-stat config).
OLD_K = 14
# Rookie-prior / small-sample threshold (career games played).
SMALL_SAMPLE_GP = 100


def age_factor(age: int | None) -> float:
    """OLD single-point age factor, kept here for the OLD-vs-NEW comparison.

    This is the pre level-curve replacement implementation (it applied a
    fixed decline on top of an already-declined observed rate, which is
    the double-counting the NEW age_adj removes).
    """
    if age is None:
        return 1.0
    if age <= 22:
        return 1.0  # handled by rookie prior
    if age <= 27:
        return 1.00
    if age <= 31:
        return 0.97 ** (age - 27)
    return 0.93 ** (age - 31) * 0.97 ** 4


def _spearman(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation."""
    n = len(x)
    if n < 3:
        return 0.0
    rx = _ranks(x)
    ry = _ranks(y)
    dx = [rx[i] - i - 1 for i in range(n)]
    dy = [ry[i] - i - 1 for i in range(n)]
    num = sum(dx[i] * dy[i] for i in range(n))
    den = math.sqrt(sum(d * d for d in dx) * sum(d * d for d in dy))
    if den == 0:
        return 0.0
    return num / den


def _ranks(vals: list[float]) -> list[float]:
    """Assign ranks (1-based, average ties)."""
    n = len(vals)
    sorted_idx = sorted(range(n), key=lambda i: vals[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n and vals[sorted_idx[j]] == vals[sorted_idx[i]]:
            j += 1
        avg_rank = (i + j - 1) / 2 + 1
        for k in range(i, j):
            ranks[sorted_idx[k]] = avg_rank
        i = j
    return ranks


def _metrics(preds: list[float], actuals: list[float]) -> tuple[float, float, float]:
    """MAE, RMSE, Spearman rho."""
    n = len(preds)
    if n == 0:
        return (0.0, 0.0, 0.0)
    mae = sum(abs(p - a) for p, a in zip(preds, actuals)) / n
    rmse = math.sqrt(sum((p - a) ** 2 for p, a in zip(preds, actuals)) / n)
    rho = _spearman(preds, actuals)
    return (mae, rmse, rho)


def _old_final(proj, cat: str, age: int | None) -> float:
    """OLD per-category total: regressed_rate * age_factor * proj_gp.

    Uses the same regressed_rate / proj_gp the model already computed
    (stored in terms), differing from the NEW total only in the age
    multiplier (age_factor vs age_adj).
    """
    t = proj.terms
    return t["regressed_rate"][cat] * age_factor(age) * t["proj_gp"]


def _observed_gp(conn: sqlite3.Connection, pid: int, season_id: int) -> int:
    """Total GP across the observed seasons (up to the 3 most recent
    with gp > 0 before season_id) -- the OLD model's shared n, which was
    games played for every stat."""
    rows = conn.execute(
        "SELECT gp FROM skater_seasons WHERE player_id = ? AND"
        " season_id < ? AND gp > 0 ORDER BY season_id DESC LIMIT 3",
        (pid, season_id)).fetchall()
    return sum(r[0] or 0 for r in rows)


def _career_gp(conn: sqlite3.Connection, pid: int, season_id: int) -> int:
    """Career GP across all seasons before season_id (no look-ahead).

    Used for the career-GP group classifications, the top-20
    small-sample / established labels, and the OLD pipeline's rookie
    prior: the career as of the projection point, i.e. the OLD model
    run with the data available at the time (no peeking at future
    seasons). At the production projection season this equals the
    full career."""
    row = conn.execute(
        "SELECT COALESCE(SUM(gp), 0) FROM skater_seasons"
        " WHERE player_id = ? AND season_id < ?", (pid, season_id)).fetchone()
    return row[0]


def _old_skater_total(conn: sqlite3.Connection, curves: dict, proj,
                      cat: str, season_id: int) -> float:
    """OLD per-category total: shared k=14, n=games, start-year age.

    Re-runs the pre-fix pipeline on the same observed seasons: OLD n is
    total games played (identical for every stat, never per-stat), OLD k
    is 14 for every stat (no per-stat config), same rookie prior
    (no-look-ahead career GP, i.e. the OLD model run with the data
    available at the time), positional means and proj_gp; the OLD age is
    age_at(...) - 1, because measuring at Feb 1 of the season's START
    year instead of its END year shifts every age by exactly one year.
    """
    t = proj.terms
    old_n = float(_observed_gp(conn, proj.player_id, season_id))
    pm = t["pos_mean"].get(cat, 0.0)
    old_regressed = (t["raw_rates"][cat] * old_n + pm * OLD_K) / (old_n + OLD_K)
    if _career_gp(conn, proj.player_id, season_id) < SMALL_SAMPLE_GP:
        old_regressed = 0.5 * old_regressed + 0.5 * pm
    if t["obs_age"] is None:
        old_age_adj = 1.0
    else:
        row = conn.execute(
            "SELECT birth_date FROM players WHERE player_id = ?",
            (proj.player_id,)).fetchone()
        new_age = age_at(row[0] if row else None, season_id)
        if new_age is None:
            old_age_adj = 1.0
        else:
            curve = (curves["defensemen"] if proj.position == "D"
                     else curves["forwards"])
            old_age_adj = (_level_value(curve, new_age - 1.0)
                           / _level_value(curve, t["obs_age"] - 1.0))
    return old_regressed * old_age_adj * t["proj_gp"]
