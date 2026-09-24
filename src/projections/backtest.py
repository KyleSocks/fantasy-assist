"""Backtest: project season N-1 from seasons N-4..N-2, compare to naive baseline.

Usage:
    python -m src.projections.backtest --db data/hockey.db

Prints per-category MAE, RMSE, Spearman rho for model vs baseline.
Exits non-zero if model does not beat baseline MAE on goals, assists, shots.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys

from .projections import project_all, Projection


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


def _get_actuals(conn: sqlite3.Connection, season_id: int, category: str,
                 player_ids: list[int]) -> dict[int, float]:
    """Actual totals for a season from skater_seasons."""
    cur = conn.execute(
        "SELECT player_id, {} FROM skater_seasons WHERE season_id = ?",
        (season_id,))
    cur = conn.execute(
        "SELECT player_id, {} FROM skater_seasons WHERE season_id = ? AND gp > 0".format(category),
        (season_id,))
    return {r[0]: float(r[1]) for r in cur.fetchall()}


def _get_actuals_gp(conn: sqlite3.Connection, season_id: int) -> dict[int, float]:
    cur = conn.execute(
        "SELECT player_id, gp FROM skater_seasons WHERE season_id = ? AND gp > 0",
        (season_id,))
    return {r[0]: float(r[1]) for r in cur.fetchall()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/hockey.db")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    seasons = [r[0] for r in conn.execute(
        "SELECT DISTINCT season_id FROM skater_seasons ORDER BY season_id DESC")]
    if len(seasons) < 2:
        print("FAIL: need at least 2 seasons")
        sys.exit(1)

    # Target = second-to-last season (N-1)
    target_season = seasons[1]  # e.g. 20242025
    print(f"Backtesting projection of season {target_season}")
    print()

    # Run model
    projections = project_all(target_season, args.db)
    proj_by_pid = {p.player_id: p for p in projections if p.player_id is not None}

    # Naive baseline: repeat last season's totals
    # "Last season" relative to target = the most recent season before target
    last_season = max(s for s in seasons if s < target_season)
    baseline: dict[int, dict[str, float]] = {}
    for cat in ("goals", "assists", "shots", "blocks", "hits", "pp_points"):
        cur = conn.execute(
            "SELECT player_id, {} FROM skater_seasons"
            " WHERE season_id = ? AND gp > 0".format(cat), (last_season,))
        for pid, val in cur.fetchall():
            if pid not in baseline:
                baseline[pid] = {}
            baseline[pid][cat] = float(val) if val is not None else 0.0

    # Determine common player set (players with data in both model and actual)
    actuals: dict[str, dict[int, float]] = {}
    for cat in ("goals", "assists", "shots", "blocks", "hits", "pp_points"):
        cur = conn.execute(
            "SELECT player_id, {} FROM skater_seasons"
            " WHERE season_id = ? AND gp > 0".format(cat), (target_season,))
        actuals[cat] = {r[0]: (float(r[1]) if r[1] is not None else 0.0) for r in cur.fetchall()}

    # Common players: in model prediction AND in actuals AND had gp > 0 in target
    common = sorted(
        pid for pid in proj_by_pid
        if pid in actuals["goals"] and actuals["goals"].get(pid, -1) >= 0
    )
    if not common:
        print("FAIL: no common players for backtest")
        sys.exit(1)

    categories = ["goals", "assists", "shots", "blocks", "hits", "pp_points"]
    print(f"Common players: {len(common)}")
    print()

    # Print headers
    print(f"{'Category':<12} {'MAE':>8} {'RMSE':>8} {'Spearman':>9}")
    print("-" * 40)

    results: dict[str, tuple[float, float, float]] = {}
    for cat in categories:
        preds = [proj_by_pid[pid].__getattribute__(cat) if hasattr(proj_by_pid[pid], cat)
                else 0.0 for pid in common]
        # Use the dataclass field directly
        preds = [getattr(proj_by_pid[pid], cat) for pid in common]
        acts = [actuals[cat][pid] for pid in common]
        mae, rmse, rho = _metrics(preds, acts)
        results[cat] = (mae, rmse, rho)
        print(f"{cat:<12} {mae:>8.3f} {rmse:>8.3f} {rho:>9.4f}")

    print()
    print("Naive baseline (repeat last season totals):")
    print(f"{'Category':<12} {'MAE':>8} {'RMSE':>8} {'Spearman':>9}")
    print("-" * 40)

    baseline_results: dict[str, tuple[float, float, float]] = {}
    for cat in categories:
        preds = [baseline[pid][cat] if pid in baseline and cat in baseline.get(pid, {})
                else 0.0 for pid in common]
        acts = [actuals[cat][pid] for pid in common]
        mae, rmse, rho = _metrics(preds, acts)
        baseline_results[cat] = (mae, rmse, rho)
        print(f"{cat:<12} {mae:>8.3f} {rmse:>8.3f} {rho:>9.4f}")

    print()

    # Compare: model must beat baseline on goals, assists, shots
    fail_cats = []
    for cat in ("goals", "assists", "shots"):
        model_mae = results[cat][0]
        base_mae = baseline_results[cat][0]
        status = "PASS" if model_mae < base_mae else "FAIL"
        print(f"  {cat}: model MAE {model_mae:.3f} vs baseline MAE {base_mae:.3f} -> {status}")
        if status == "FAIL":
            fail_cats.append(cat)

    if fail_cats:
        print(f"FAIL: model did not beat baseline on: {', '.join(fail_cats)}")
        conn.close()
        sys.exit(1)

    print("PASS: model beats baseline on goals, assists, shots")
    conn.close()
    sys.exit(0)


if __name__ == "__main__":
    main()