"""Grid-search k values to optimize backtest MAE across all categories.

Pre-computes all player data once, then only varies k in the inner loop.

Usage:
    python tmp_tune.py
"""
from __future__ import annotations

import itertools
import math
import sqlite3

from src.projections.projections import (
    _load_config, _season_weights, _pos_group, _age_as_of, _age_factor,
    _compute_pos_means, _compute_pos_median_toi,
)

DB = "data/hockey.db"
TARGET_SEASON = 20242025
CATEGORIES = ("goals", "assists", "shots", "blocks", "hits", "pp_points")

# Grid to search (include 0 to test no-shrinkage)
K_RANGES = {
    "goals": [0, 100, 200, 300, 400, 500],
    "assists": [0, 100, 150, 200, 250, 300, 400, 500],
    "shots": [0, 50, 100, 150, 200, 300, 400],
    "blocks": [0, 100, 150, 200, 250, 300, 400],
    "hits": [0, 100, 150, 200, 250, 300, 400],
    "pp_points": [0, 50, 80, 100, 150, 200, 300],
}


def _mae(preds: list[float], actuals: list[float]) -> float:
    n = len(preds)
    if n == 0:
        return 0.0
    return sum(abs(p - a) for p, a in zip(preds, actuals)) / n


def precompute_player_data(conn: sqlite3.Connection) -> tuple:
    """Pre-compute everything except the k-dependent regression step."""
    prior_seasons = [r[0] for r in conn.execute(
        "SELECT DISTINCT season_id FROM skater_seasons"
        " WHERE season_id < ? ORDER BY season_id DESC LIMIT 3",
        (TARGET_SEASON,)).fetchall()]
    prior_seasons.reverse()

    pos_means_c = _compute_pos_means(conn, prior_seasons, "C")
    pos_means_w = _compute_pos_means(conn, prior_seasons, "W")
    pos_means_d = _compute_pos_means(conn, prior_seasons, "D")
    pos_median_toi_c = _compute_pos_median_toi(conn, prior_seasons, "C")
    pos_median_toi_w = _compute_pos_median_toi(conn, prior_seasons, "W")
    pos_median_toi_d = _compute_pos_median_toi(conn, prior_seasons, "D")

    # Get actuals
    actuals: dict[str, dict[int, float]] = {}
    for cat in CATEGORIES:
        cur = conn.execute(
            "SELECT player_id, {} FROM skater_seasons"
            " WHERE season_id = ? AND gp > 0".format(cat), (TARGET_SEASON,))
        actuals[cat] = {r[0]: (float(r[1]) if r[1] is not None else 0.0)
                       for r in cur.fetchall()}

    last_season = max(s for s in prior_seasons if s < TARGET_SEASON)
    baseline: dict[str, dict[int, float]] = {}
    for cat in CATEGORIES:
        cur = conn.execute(
            "SELECT player_id, {} FROM skater_seasons"
            " WHERE season_id = ? AND gp > 0".format(cat), (last_season,))
        baseline[cat] = {r[0]: (float(r[1]) if r[1] is not None else 0.0)
                       for r in cur.fetchall()}

    # Pre-compute per-player data
    skaters = [r[0] for r in conn.execute(
        "SELECT DISTINCT player_id FROM skater_seasons"
        " WHERE season_id < ? AND gp > 0", (TARGET_SEASON,))]

    player_data = []  # list of dicts with everything needed
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

        birth_row = conn.execute(
            "SELECT birth_date, position FROM players WHERE player_id = ?",
            (pid,)).fetchone()
        if not birth_row:
            continue
        birth_date, pos = birth_row
        age = _age_as_of(birth_date, TARGET_SEASON)
        af = _age_factor(age)

        career_row = conn.execute(
            "SELECT COALESCE(SUM(gp),0) FROM skater_seasons WHERE player_id = ?",
            (pid,)).fetchone()
        career_gp = career_row[0]

        seasons = conn.execute(
            "SELECT season_id, gp, goals, assists, shots, blocks, hits,"
            " pp_points, pp_toi_sec, toi_sec, team_abbrev"
            " FROM skater_seasons WHERE player_id = ? AND season_id < ? AND gp > 0"
            " ORDER BY season_id DESC LIMIT 3", (pid, TARGET_SEASON)).fetchall()
        if not seasons:
            continue
        if all((s[9] or 0) == 0 for s in seasons):
            continue

        weights = _season_weights([s[1] for s in seasons])

        # Per-season rates
        rates_by_season = []
        for s in seasons:
            gp, goals, assists, shots, blocks, hits, pp_pts, pp_toi, toi = (
                s[1], s[2], s[3], s[4], s[5], s[6], s[7], s[8], s[9])
            r = {}
            if toi and toi > 0:
                toi_hrs = toi / 3600
                r["goals"] = (goals or 0) / toi_hrs
                r["assists"] = (assists or 0) / toi_hrs
                r["shots"] = (shots or 0) / toi_hrs
                r["blocks"] = (blocks or 0) / toi_hrs
                r["hits"] = (hits or 0) / toi_hrs
            else:
                r["goals"] = r["assists"] = r["shots"] = r["blocks"] = r["hits"] = 0.0
            if pp_toi is not None and pp_toi > 0:
                r["pp_points"] = (pp_pts or 0) / (pp_toi / 3600)
            else:
                r["pp_points"] = 0.0
            rates_by_season.append(r)

        # n values (in minutes, not seconds)
        n_totals = {}
        for cat in CATEGORIES:
            if cat == "goals":
                n_totals[cat] = sum(seasons[i][4] or 0 for i in range(len(seasons)))
            elif cat == "pp_points":
                n_totals[cat] = sum((seasons[i][8] or 0) for i in range(len(seasons))) / 60.0
            else:
                n_totals[cat] = sum((seasons[i][9] or 0) for i in range(len(seasons))) / 60.0

        # Weighted rates (the "raw" per-60 rates before regression)
        weighted_rates = {}
        for cat in CATEGORIES:
            weighted_rates[cat] = sum(weights[i] * rates_by_season[i][cat]
                                     for i in range(len(seasons)))

        # TOI projections
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

        if career_gp < 100 and pg != "G":
            proj_toi_per_gp = min(proj_toi_per_gp, pmt)

        # GP projection
        all_seasons = conn.execute(
            "SELECT season_id, team_abbrev FROM skater_seasons WHERE player_id = ?",
            (pid,)).fetchall()
        team_games = 0
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

        # Store everything needed for fast k-varying evaluation
        player_data.append({
            "pid": pid,
            "weighted_rates": weighted_rates,
            "n_totals": n_totals,
            "pos_means": pm,
            "af": af,
            "career_gp": career_gp,
            "proj_toi_per_gp": proj_toi_per_gp,
            "proj_pp_toi_per_gp": proj_pp_toi_per_gp,
            "proj_gp": proj_gp,
            "pg": pg,
        })

    return player_data, actuals, baseline


def evaluate_k(k_values: dict, player_data: list, actuals: dict) -> dict[str, float]:
    """Evaluate MAE for given k values using pre-computed data."""
    preds: dict[str, list[float]] = {cat: [] for cat in CATEGORIES}
    acts: dict[str, list[float]] = {cat: [] for cat in CATEGORIES}

    for pd in player_data:
        pid = pd["pid"]
        if pid not in actuals["goals"]:
            continue

        for cat in CATEGORIES:
            k = k_values[cat]
            n_val = pd["n_totals"][cat]
            pm_val = pd["pos_means"].get(cat, 0.0)
            raw_rate = pd["weighted_rates"][cat]
            denom = n_val + k
            if denom == 0:
                continue
            regressed = (raw_rate * n_val + pm_val * k) / denom

            # Rookie prior
            if pd["career_gp"] < 100:
                regressed = 0.5 * regressed + 0.5 * pm_val

            # Final total
            if cat == "pp_points":
                total = regressed * pd["af"] * (pd["proj_pp_toi_per_gp"] / 3600) * pd["proj_gp"]
            else:
                total = regressed * pd["af"] * (pd["proj_toi_per_gp"] / 3600) * pd["proj_gp"]

            if cat in actuals and pid in actuals[cat]:
                preds[cat].append(total)
                acts[cat].append(actuals[cat][pid])

    maes = {}
    for cat in CATEGORIES:
        maes[cat] = _mae(preds[cat], acts[cat])
    return maes


def main():
    conn = sqlite3.connect(DB)
    print("Pre-computing player data...")
    player_data, actuals, baseline = precompute_player_data(conn)
    conn.close()
    print(f"  {len(player_data)} players pre-computed\n")

    # Baseline MAEs
    baseline_maes = {}
    for cat in CATEGORIES:
        common = set(actuals[cat].keys()) & set(baseline[cat].keys())
        if common:
            preds = [baseline[cat][pid] for pid in common]
            acts = [actuals[cat][pid] for pid in common]
            baseline_maes[cat] = _mae(preds, acts)
        else:
            baseline_maes[cat] = 0.0

    print("Baseline MAE (repeat last season):")
    for cat in CATEGORIES:
        print(f"  {cat:<12} {baseline_maes[cat]:.3f}")
    print()

    # Grid search
    print("Grid searching k values...")
    best = None
    best_total = float('inf')
    count = 0

    for combo in itertools.product(*K_RANGES.values()):
        k_values = dict(zip(CATEGORIES, combo))
        maes = evaluate_k(k_values, player_data, actuals)
        total = sum(maes[cat] for cat in CATEGORIES)
        count += 1
        if total < best_total:
            best = k_values
            best_total = total
            best_maes = maes

    print(f"\nSearched {count} combinations")
    print(f"\nBest k values (min total MAE = {best_total:.3f}):")
    for cat in CATEGORIES:
        delta = best_maes[cat] - baseline_maes[cat]
        status = "PASS" if best_maes[cat] < baseline_maes[cat] else "FAIL"
        print(f"  {cat:<12} k={best[cat]:<4} MAE={best_maes[cat]:.3f} "
              f"(baseline {baseline_maes[cat]:.3f}, delta {delta:+.3f}) {status}")

    all_pass = all(best_maes[cat] < baseline_maes[cat] for cat in CATEGORIES)
    print(f"\nAll categories beat baseline: {all_pass}")

    # Also show per-category best (in case some trade-offs exist)
    print("\nPer-category optimal k (min MAE for each category alone):")
    for cat in CATEGORIES:
        cat_best = None
        cat_best_val = float('inf')
        for k_val in K_RANGES[cat]:
            k_values = dict(best)  # start from best global
            k_values[cat] = k_val
            mae = evaluate_k(k_values, player_data, actuals)
            if mae[cat] < cat_best_val:
                cat_best_val = mae[cat]
                cat_best = k_val
        print(f"  {cat:<12} k={cat_best:<4} MAE={cat_best_val:.3f} "
              f"(baseline {baseline_maes[cat]:.3f})")


if __name__ == "__main__":
    main()