"""Backtest: project the most recent season from prior seasons and
validate the per-stat regression and end-year age fixes (OLD vs NEW).

Usage:
    python -m src.projections.backtest --db data/hockey.db

Sections (in order):
  1. Current model (NEW per-stat k/n + end-year age_adj) vs naive baseline:
     MAE / RMSE / Spearman.
  2. Per-category MAE for three groups (all skaters / <150 career GP /
     >400 career GP), comparing the OLD model (shared k=14, n=games,
     start-year age) to the NEW model (per-stat k/n, end-year age).
     NEW must beat OLD on the <150 group and must not be worse overall.
  3. Named-player diagnostics with explicit age terms.
  4. Double-counting demo: Duchene (8475168) at ages 32/33/34,
     old af vs new age_adj per year.
  5. Sum-back check on explain(): regressed_rate * age_adj * proj_gp
     must equal final within 1e-6.
  6. Acceptance audit for player 8484801 projected to 20262027:
     obs_age in (18, 19), L_proj == 0.86, k with six per-stat values,
     n per stat (goals n in the hundreds = shots, assists n in the
     thousands = TOI-minutes, goals n != assists n), and the regressed
     goals rate must move substantially toward the positional mean of
     0.220 compared to the OLD 0.455.
  7. Top-20 skaters by projected points for the production projection
     season (20262027): OLD vs NEW ranks with a rank-change column;
     small-sample players (career GP < 100) must move DOWN relative to
     established players, measured across all skaters.
  8. A player with exactly one observed season: per-stat n must not
     equal his games played for any stat.

Exits non-zero if any FAIL condition is hit.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

from src.paths import DEFAULT_DB_ARG

from .backtest_support import (  # noqa: F401  (re-exported for tests)
    CATS,
    NAMED,
    DUCHENE,
    AUDIT_PLAYER,
    AUDIT_SEASON,
    OLD_K,
    SMALL_SAMPLE_GP,
    age_factor,
    _spearman,
    _ranks,
    _metrics,
    _old_final,
    _observed_gp,
    _career_gp,
    _old_skater_total,
)
from .projections import (
    project_all, explain, age_at, _level_value, _load_config,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB_ARG)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    seasons = [r[0] for r in conn.execute(
        "SELECT DISTINCT season_id FROM skater_seasons ORDER BY season_id DESC")]
    if len(seasons) < 2:
        print("FAIL: need at least 2 seasons")
        conn.close()
        sys.exit(1)

    # Target = the most recent season that has prior seasons with actuals
    # (the original backtest target), so the career-GP groups (<150 / >400)
    # are computed from all seasons before it.
    target_season = seasons[1]
    print(f"Backtesting projection of season {target_season}")
    print()

    # Run the current model (NEW per-stat k/n + end-year age_adj).
    projections = project_all(target_season, args.db)
    proj_by_pid = {p.player_id: p for p in projections if p.player_id is not None}
    curves = _load_config()["level_curves"]

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

    # Ages at the target season (for diagnostics).
    ages: dict[int, float | None] = {}
    for pid in common:
        row = conn.execute(
            "SELECT birth_date FROM players WHERE player_id = ?",
            (pid,)).fetchone()
        ages[pid] = age_at(row[0], target_season) if row else None

    failures: list[str] = []

    def old_totals_for(pid: int) -> dict[str, float]:
        if pid not in old_totals:
            p = proj_by_pid[pid]
            old_totals[pid] = {cat: _old_skater_total(conn, curves, p, cat,
                                target_season) for cat in CATS}
        return old_totals[pid]

    old_totals: dict[int, dict[str, float]] = {}

    # ---- Section 1: model vs naive baseline (NEW) -----------------------
    print(f"Common players: {len(common)}")
    print()
    print("Model (NEW per-stat k/n + end-year age_adj) vs naive baseline:")
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
        preds = [baseline[pid][cat] if pid in baseline
                 and cat in baseline.get(pid, {}) else 0.0
                for pid in common]
        acts = [actuals[cat][pid] for pid in common]
        mae, rmse, rho = _metrics(preds, acts)
        baseline_results[cat] = (mae, rmse, rho)
        print(f"{cat:<12} {mae:>8.3f} {rmse:>8.3f} {rho:>9.4f}")

    # The baseline table above is informational; the enforced FAIL
    # conditions are the OLD-vs-NEW group checks below plus the sum-back,
    # acceptance-audit, top-20 and one-season n checks.

    # ---- Section 2: career-GP-group MAE, OLD vs NEW ----------------------
    career_gp = {pid: _career_gp(conn, pid, target_season) for pid in common}
    groups = {
        "all skaters": common,
        "<150 career GP": [pid for pid in common if career_gp[pid] < 150],
        ">400 career GP": [pid for pid in common if career_gp[pid] > 400],
    }
    print("Per-category MAE: OLD (shared k=14, n=games, start-year age)"
          " vs NEW (per-stat k/n, end-year age)")
    group_mean_mae: dict[str, tuple[float, float]] = {}
    for gname, members in groups.items():
        print(f"Group: {gname} (n={len(members)})")
        print(f"  {'Category':<12} {'OLD MAE':>9} {'NEW MAE':>9} {'delta':>9}")
        old_sum = 0.0
        new_sum = 0.0
        for cat in CATS:
            if members:
                old_mae = sum(abs(old_totals_for(pid)[cat]
                                - actuals[cat][pid]) for pid in members) \
                    / len(members)
                new_mae = sum(abs(getattr(proj_by_pid[pid], cat)
                                - actuals[cat][pid]) for pid in members) \
                    / len(members)
            else:
                old_mae = new_mae = 0.0
            delta = new_mae - old_mae
            old_sum += old_mae
            new_sum += new_mae
            print(f"  {cat:<12} {old_mae:>9.3f} {new_mae:>9.3f} {delta:>+9.3f}")
        group_mean_mae[gname] = (old_sum / len(CATS), new_sum / len(CATS))
    print()
    old_m, new_m = group_mean_mae["<150 career GP"]
    ok = new_m < old_m
    status = "PASS" if ok else "FAIL"
    print(f"  <150 career GP: mean MAE OLD {old_m:.3f} vs NEW {new_m:.3f}"
          f" (NEW must beat OLD) -> {status}")
    if not ok:
        failures.append("NEW did not beat OLD on the <150 career GP group")
    old_m, new_m = group_mean_mae["all skaters"]
    ok = new_m <= old_m + 1e-12
    status = "PASS" if ok else "FAIL"
    print(f"  all skaters: mean MAE OLD {old_m:.3f} vs NEW {new_m:.3f}"
          f" (NEW must not be worse) -> {status}")
    if not ok:
        failures.append("NEW was worse than OLD overall on all skaters")
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
              f"age_adj={t['age_adj']:.6f} proj_gp={t['proj_gp']:.3f}")
        for cat in CATS:
            old = old_totals_for(pid)[cat]
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
        curve = (curves["defensemen"] if pg == "D" else curves["forwards"])
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

    # ---- Section 6: acceptance audit (bug-report player) ----------------
    print(f"Acceptance audit: explain({AUDIT_PLAYER}, {AUDIT_SEASON}, db)")
    t = explain(AUDIT_PLAYER, AUDIT_SEASON, args.db)
    if not t:
        failures.append(f"explain({AUDIT_PLAYER}, {AUDIT_SEASON}) returned empty")
        print("  explain() returned empty -> FAIL")
    else:
        obs_age, L_obs, L_proj, age_adj = (t["obs_age"], t["L_obs"],
                                           t["L_proj"], t["age_adj"])
        k, n = t["k"], t["n"]
        print(f"  obs_age={obs_age} L_obs={L_obs:.4f} "
              f"L_proj={L_proj:.4f} age_adj={age_adj:.6f}")
        print(f"  k={k}")
        print(f"  n={n}")
        ok = obs_age is not None and 18.0 < obs_age < 19.0
        print(f"  obs_age in (18, 19) -> {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append(f"obs_age {obs_age} not in (18, 19)")
        ok = abs(L_proj - 0.86) <= 1e-9
        print(f"  L_proj == 0.86 -> {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append(f"L_proj {L_proj} != 0.86")
        ok = len(k) == 6 and len(set(k.values())) >= 2
        print(f"  k has six per-stat values -> {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append(f"k {k} is not six per-stat values")
        ok = (100 <= n["goals"] < 1000 and 1000 <= n["assists"] < 10000
              and n["goals"] != n["assists"])
        print(f"  n goals in the hundreds (shots), assists in the thousands"
              f" (TOI-minutes), goals != assists -> {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append(f"per-stat n wrong: {n}")
        old_n = float(_observed_gp(conn, AUDIT_PLAYER, AUDIT_SEASON))
        pm = t["pos_mean"]["goals"]
        raw = t["raw_rates"]["goals"]
        old_regressed = (raw * old_n + pm * OLD_K) / (old_n + OLD_K)
        if _career_gp(conn, AUDIT_PLAYER, AUDIT_SEASON) < SMALL_SAMPLE_GP:
            old_regressed = 0.5 * old_regressed + 0.5 * pm
        new_regressed = t["regressed_rate"]["goals"]
        old_gap = abs(old_regressed - pm)
        new_gap = abs(new_regressed - pm)
        print(f"  regressed goals rate: OLD {old_regressed:.4f} -> "
              f"NEW {new_regressed:.4f} (positional mean {pm:.4f})")
        print(f"  gap to positional mean: OLD {old_gap:.4f} -> NEW {new_gap:.4f}")
        ok = new_gap < old_gap * 0.85
        print(f"  NEW moves substantially toward the positional mean"
              f" -> {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append("regressed goals rate did not move toward the mean")
    print()

    # ---- Section 7: top-20 skaters OLD vs NEW ----------------------------
    # Top-20 by projected points for the production projection season
    # (AUDIT_SEASON): OLD vs NEW ranks with a rank-change column. The
    # small-sample (career GP < 100, the rookie-prior threshold) vs
    # established mean rank change is measured across ALL skaters.
    top_target = AUDIT_SEASON
    top_projections = project_all(top_target, args.db)
    top_by_pid = {p.player_id: p for p in top_projections
                  if p.player_id is not None}
    top_skaters = [pid for pid in top_by_pid
                   if top_by_pid[pid].position != "G"]
    top_old_totals: dict[int, dict[str, float]] = {}

    def top_old_totals_for(pid: int) -> dict[str, float]:
        if pid not in top_old_totals:
            p = top_by_pid[pid]
            top_old_totals[pid] = {cat: _old_skater_total(conn, curves, p,
                                    cat, top_target) for cat in CATS}
        return top_old_totals[pid]

    new_pts: dict[int, float] = {}
    old_pts: dict[int, float] = {}
    labels: dict[int, str] = {}
    for pid in top_skaters:
        p = top_by_pid[pid]
        row = conn.execute(
            "SELECT full_name FROM players WHERE player_id = ?",
            (pid,)).fetchone()
        labels[pid] = row[0] if row else str(pid)
        new_pts[pid] = p.goals + p.assists
        old_pts[pid] = (top_old_totals_for(pid)["goals"]
                        + top_old_totals_for(pid)["assists"])

    def rank_order(points: dict[int, float]) -> list[int]:
        return sorted(points, key=lambda p: (-points[p], p))

    new_order = rank_order(new_pts)
    old_order = rank_order(old_pts)
    new_rank = {p: i + 1 for i, p in enumerate(new_order)}
    old_rank = {p: i + 1 for i, p in enumerate(old_order)}
    union_top20 = set(new_order[:20]) | set(old_order[:20])

    print(f"Top-20 skaters by projected points (OLD vs NEW ranks,"
          f" season {top_target})")
    print(f"  {'NEW rank':>8} {'player':<24} {'OLD rank':>8} {'delta':>6}"
          f" {'career GP':>10} {'class':>6} {'NEW pts':>9} {'OLD pts':>9}")
    for pid in sorted(union_top20, key=lambda p: (new_rank[p], p)):
        delta = new_rank[pid] - old_rank[pid]
        cg = _career_gp(conn, pid, top_target)
        cls = "small" if cg < SMALL_SAMPLE_GP else "estab"
        print(f"  {new_rank[pid]:>8} {labels[pid]:<24} {old_rank[pid]:>8} "
              f"{delta:>+6} {cg:>10} {cls:>6} {new_pts[pid]:>9.1f} "
              f"{old_pts[pid]:>9.1f}")
    small_deltas: list[int] = []
    estab_deltas: list[int] = []
    for pid in top_skaters:
        delta = new_rank[pid] - old_rank[pid]
        cg = _career_gp(conn, pid, top_target)
        (small_deltas if cg < SMALL_SAMPLE_GP else estab_deltas).append(delta)
    if small_deltas and estab_deltas:
        mean_small = sum(small_deltas) / len(small_deltas)
        mean_estab = sum(estab_deltas) / len(estab_deltas)
        ok = mean_small > mean_estab
        print(f"  all skaters: mean delta small {mean_small:+.2f} vs estab"
              f" {mean_estab:+.2f} (small must move DOWN)"
              f" -> {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append("small-sample players did not move down vs established")
    else:
        print("  no small-sample players -> check not applicable")
    print()

    # ---- Section 8: one-observed-season player n check ------------------
    row = conn.execute(
        "SELECT s.player_id, s.season_id, s.gp FROM skater_seasons s"
        " JOIN players p ON s.player_id = p.player_id"
        " WHERE s.season_id < ? GROUP BY s.player_id"
        " HAVING COUNT(*) = 1 ORDER BY s.player_id LIMIT 1",
        (target_season,)).fetchone()
    print("One-observed-season player: per-stat n must not equal games played")
    if row is None:
        failures.append("no one-observed-season player found")
        print("  no one-observed-season player found -> FAIL")
    else:
        pid, sid, gp = row
        t = explain(pid, target_season, args.db)
        if not t:
            failures.append(f"explain({pid}) returned empty for n check")
            print(f"  player {pid}: explain() returned empty -> FAIL")
        else:
            n = t["n"]
            print(f"  player {pid} ({sid}, gp={gp}): n={n}")
            bad = [cat for cat in CATS if n[cat] == gp]
            ok = not bad
            print(f"  none of the per-stat n equal gp={gp}"
                  f" -> {'PASS' if ok else 'FAIL'}")
            if not bad:
                pass
            else:
                failures.append(f"one-season player n equals gp for {bad}")
    print()

    # ---- Final verdict --------------------------------------------------
    if failures:
        print("FAIL: " + "; ".join(failures))
        conn.close()
        sys.exit(1)
    print("PASS: NEW beats OLD on <150 career GP and is not worse overall;"
          " sum-back, acceptance audit, top-20 and one-season n checks OK")
    conn.close()
    sys.exit(0)


if __name__ == "__main__":
    main()