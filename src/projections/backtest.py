"""Backtest: project season N-1 from prior seasons and validate age handling.

Usage:
    python -m src.projections.backtest --db data/hockey.db

Sections (in order):
  1. Current model (NEW age_adj) vs naive baseline: MAE / RMSE / Spearman.
  2. Three-group MAE table (all skaters / <=22 / >=31) x 6 categories
     comparing the OLD single-point `_age_factor` to the NEW `age_adj`.
  3. Named-player diagnostics with explicit `age_adj`.
  4. Double-counting demo: Duchene (8475168) at ages 32/33/34,
     old `af` vs new `age_adj` per year.
  5. Sum-back check on explain(): regressed_rate * age_adj * proj_gp
     must equal final within 1e-6.

Exits non-zero if any FAIL condition is hit (model does not beat baseline on
goals/assists/shots, or the sum-back check misses 1e-6).
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys

from .projections import (
    project_all, explain, _age_as_of, _level_value, _load_config,
)

CATS = ("goals", "assists", "shots", "blocks", "hits", "pp_points")

# Named-player diagnostics (id, label).
NAMED = (
    (8482157, "Cuylle"),
    (8479314, "Tkachuk"),
    (8473994, "Benn"),
)
# Double-counting demo target.
DUCHENE = 8475168


def age_factor(age: int | None) -> float:
    """OLD single-point age factor, kept here for the OLD-vs-NEW comparison.

    This is the pre level-curve replacement implementation (it applied a
    fixed decline on top of an already-declined observed rate, which is the
    double-counting the NEW age_adj removes).
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

    Uses the same regressed_rate / proj_gp the model already computed (stored
    in terms), differing from the NEW total only in the age multiplier
    (age_factor vs age_adj).
    """
    t = proj.terms
    return t["regressed_rate"][cat] * age_factor(age) * t["proj_gp"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/hockey.db")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    seasons = [r[0] for r in conn.execute(
        "SELECT DISTINCT season_id FROM skater_seasons ORDER BY season_id DESC")]
    if len(seasons) < 2:
        print("FAIL: need at least 2 seasons")
        conn.close()
        sys.exit(1)

    # Target = second-to-last season (N-1), which has actuals in the DB.
    target_season = seasons[1]  # e.g. 20242025
    print(f"Backtesting projection of season {target_season}")
    print()

    # Run the current model (NEW age_adj).
    projections = project_all(target_season, args.db)
    proj_by_pid = {p.player_id: p for p in projections if p.player_id is not None}

    # Naive baseline: repeat last season's totals.
    last_season = max(s for s in seasons if s < target_season)
    baseline: dict[int, dict[str, float]] = {}
    for cat in CATS:
        cur = conn.execute(
            "SELECT player_id, {} FROM skater_seasons"
            " WHERE season_id = ? AND gp > 0".format(cat), (last_season,))
        for pid, val in cur.fetchall():
            if pid not in baseline:
                baseline[pid] = {}
            baseline[pid][cat] = float(val) if val is not None else 0.0

    # Actuals for the target season.
    actuals: dict[str, dict[int, float]] = {}
    for cat in CATS:
        cur = conn.execute(
            "SELECT player_id, {} FROM skater_seasons"
            " WHERE season_id = ? AND gp > 0".format(cat), (target_season,))
        actuals[cat] = {r[0]: (float(r[1]) if r[1] is not None else 0.0)
                        for r in cur.fetchall()}

    # Common players: in model prediction AND in actuals.
    common = sorted(
        pid for pid in proj_by_pid
        if pid in actuals["goals"] and actuals["goals"].get(pid, -1) >= 0
    )
    if not common:
        print("FAIL: no common players for backtest")
        conn.close()
        sys.exit(1)

    # Ages at the target season (for grouping + OLD factor).
    ages: dict[int, int | None] = {}
    for pid in common:
        row = conn.execute(
            "SELECT birth_date FROM players WHERE player_id = ?",
            (pid,)).fetchone()
        ages[pid] = _age_as_of(row[0], target_season) if row else None

    failures: list[str] = []

    # ---- Section 1: model vs naive baseline (NEW age_adj) ----------------
    print(f"Common players: {len(common)}")
    print()
    print("Model (NEW age_adj) vs naive baseline:")
    print(f"{'Category':<12} {'MAE':>8} {'RMSE':>8} {'Spearman':>9}")
    print("-" * 40)
    results: dict[str, tuple[float, float, float]] = {}
    for cat in CATS:
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
    for cat in CATS:
        preds = [baseline[pid][cat] if pid in baseline and cat in baseline.get(pid, {})
                else 0.0 for pid in common]
        acts = [actuals[cat][pid] for pid in common]
        mae, rmse, rho = _metrics(preds, acts)
        baseline_results[cat] = (mae, rmse, rho)
        print(f"{cat:<12} {mae:>8.3f} {rmse:>8.3f} {rho:>9.4f}")

    print()
    for cat in ("goals", "assists", "shots"):
        model_mae = results[cat][0]
        base_mae = baseline_results[cat][0]
        status = "PASS" if model_mae < base_mae else "FAIL"
        print(f"  {cat}: model MAE {model_mae:.3f} vs baseline MAE {base_mae:.3f}"
              f" -> {status}")
        if status == "FAIL":
            failures.append(f"model did not beat baseline on {cat}")
    print()

    # ---- Section 2: three-group MAE, OLD vs NEW -------------------------
    groups = {
        "all skaters": common,
        "<=22": [pid for pid in common
                 if ages[pid] is not None and ages[pid] <= 22],
        ">=31": [pid for pid in common
                 if ages[pid] is not None and ages[pid] >= 31],
    }
    print("Three-group MAE: OLD (_age_factor) vs NEW (age_adj)")
    for gname, members in groups.items():
        print(f"Group: {gname} (n={len(members)})")
        print(f"  {'Category':<12} {'OLD MAE':>9} {'NEW MAE':>9} {'delta':>9}")
        for cat in CATS:
            old_mae = sum(abs(_old_final(proj_by_pid[pid], cat, ages[pid])
                            - actuals[cat][pid]) for pid in members) / len(members)
            new_mae = sum(abs(getattr(proj_by_pid[pid], cat)
                            - actuals[cat][pid]) for pid in members) / len(members)
            delta = new_mae - old_mae
            print(f"  {cat:<12} {old_mae:>9.3f} {new_mae:>9.3f} {delta:>+9.3f}")
    print()

    # ---- Section 3: named-player diagnostics ----------------------------
    print("Named-player diagnostics")
    for pid, label in NAMED:
        if pid not in proj_by_pid:
            print(f"  {label} ({pid}): not in model")
            continue
        t = proj_by_pid[pid].terms
        age = ages.get(pid)
        print(f"  {label} ({pid}): age={age} obs_age={t['obs_age']} "
              f"L_obs={t['L_obs']:.4f} L_proj={t['L_proj']:.4f} "
              f"age_adj={t['age_adj']:.6f} old_af={age_factor(age):.6f} "
              f"proj_gp={t['proj_gp']:.3f}")
        for cat in CATS:
            old = _old_final(proj_by_pid[pid], cat, age)
            new = t["final"][cat]
            print(f"    {cat:<10} OLD={old:8.3f} NEW={new:8.3f}")
    print()

    # ---- Section 4: double-counting demo -------------------------------
    if DUCHENE in proj_by_pid:
        obs_age = proj_by_pid[DUCHENE].terms["obs_age"]
        pos_row = conn.execute(
            "SELECT position FROM players WHERE player_id = ?",
            (DUCHENE,)).fetchone()
        pg = pos_row[0] if pos_row else "C"
        curve = (_load_config()["level_curves"]["defensemen"]
                 if pg == "D" else _load_config()["level_curves"]["forwards"])
        print(f"Double-counting demo: Duchene ({DUCHENE}), "
              f"obs_age={obs_age}")
        print(f"  {'age':>4} {'old af':>9} {'new age_adj':>13} {'new/old':>9}")
        for a in (32, 33, 34):
            af = age_factor(a)
            aa = _level_value(curve, a) / _level_value(curve, obs_age)
            print(f"  {a:>4} {af:>9.6f} {aa:>13.6f} {aa / af:>9.4f}")
    else:
        print(f"Double-counting demo: Duchene ({DUCHENE}) not in model")
    print()

    # ---- Section 5: sum-back check -------------------------------------
    # explain() returns proj.terms; verify its internals are consistent:
    # regressed_rate * age_adj * proj_gp must equal final within 1e-6.
    print("Sum-back check (explain(): regressed_rate * age_adj * proj_gp == final)")
    sumback_ok = True
    for pid in [p for p, _ in NAMED] + [DUCHENE]:
        terms = explain(pid, target_season, args.db)
        if not terms:
            sumback_ok = False
            print(f"  {pid}: explain() returned empty -> FAIL")
            continue
        worst = max(abs(terms["regressed_rate"][cat] * terms["age_adj"]
                      * terms["proj_gp"] - terms["final"][cat]) for cat in CATS)
        ok = worst < 1e-6
        sumback_ok = sumback_ok and ok
        print(f"  {pid}: max |regressed_rate*age_adj*proj_gp - final| = "
              f"{worst:.2e} -> {'PASS' if ok else 'FAIL'}")
    if not sumback_ok:
        failures.append("sum-back check exceeded 1e-6")
    print()

    # ---- Final verdict --------------------------------------------------
    if failures:
        print("FAIL: " + "; ".join(failures))
        conn.close()
        sys.exit(1)
    print("PASS: model beats baseline on goals, assists, shots; sum-back OK")
    conn.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
