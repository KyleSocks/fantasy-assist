"""Multi-season backtest: run projection for each target season and compare to baseline."""
import sqlite3, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from src.projections.projections import project_all
from src.projections.backtest import _metrics

db = "data/hockey.db"
conn = sqlite3.connect(db)
seasons = [r[0] for r in conn.execute(
    "SELECT DISTINCT season_id FROM skater_seasons ORDER BY season_id")]
conn.close()

cats = ["goals", "assists", "shots", "blocks", "hits", "pp_points"]

for target in seasons[1:-1]:  # skip first (no baseline) and last (most recent)
    last_season = max(s for s in seasons if s < target)
    projections = project_all(target, db)
    proj_by_pid = {p.player_id: p for p in projections if p.player_id is not None}

    conn2 = sqlite3.connect(db)
    baseline = {}
    for cat in cats:
        cur = conn2.execute(
            f"SELECT player_id, {cat} FROM skater_seasons WHERE season_id = ? AND gp > 0",
            (last_season,))
        for pid, val in cur.fetchall():
            if pid not in baseline:
                baseline[pid] = {}
            baseline[pid][cat] = float(val) if val is not None else 0.0

    actuals = {}
    for cat in cats:
        cur = conn2.execute(
            f"SELECT player_id, {cat} FROM skater_seasons WHERE season_id = ? AND gp > 0",
            (target,))
        actuals[cat] = {r[0]: (float(r[1]) if r[1] is not None else 0.0)
                        for r in cur.fetchall()}
    conn2.close()

    common = sorted(pid for pid in proj_by_pid
                    if pid in actuals["goals"] and actuals["goals"].get(pid, -1) >= 0)
    if len(common) < 50:
        print(f"Skipping {target}: only {len(common)} common players")
        continue

    print(f"=== Target: {target} (baseline season {last_season}, {len(common)} common) ===")
    print(f"{'Category':<12} {'MAE':>8} {'RMSE':>8} {'Spearman':>9}  |  {'Base MAE':>9} {'Base Rho':>9}")
    print("-" * 65)
    for cat in cats:
        preds = [getattr(proj_by_pid[pid], cat) for pid in common]
        acts = [actuals[cat][pid] for pid in common]
        mae, rmse, rho = _metrics(preds, acts)
        bpreds = [baseline[pid][cat] if pid in baseline and cat in baseline.get(pid, {}) else 0.0
                  for pid in common]
        bmae, brmse, brho = _metrics(bpreds, acts)
        print(f"{cat:<12} {mae:>8.3f} {rmse:>8.3f} {rho:>9.4f}  |  {bmae:>9.3f} {brho:>9.4f}")

    # PASS/FAIL on key categories
    print()
    for cat in ("goals", "assists", "shots"):
        m1 = _metrics([getattr(proj_by_pid[pid], cat) for pid in common],
                      [actuals[cat][pid] for pid in common])[0]
        m2 = _metrics([baseline[pid][cat] if pid in baseline and cat in baseline.get(pid, {}) else 0.0
                       for pid in common],
                      [actuals[cat][pid] for pid in common])[0]
        status = "PASS" if m1 < m2 else "FAIL"
        print(f"  {cat}: model {m1:.3f} vs baseline {m2:.3f} -> {status}")
    print()