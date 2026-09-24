"""Compare per-GP rates vs per-60 TOI rates for 20242025 prediction.

For each player, compute:
  A) Per-60 TOI rate (current approach) → project via TOI
  B) Per-GP rate (simpler) → project via games played
Then compare MAE for both approaches.
"""
from __future__ import annotations
import sqlite3
from src.projections.projections import (
    _load_config, _season_weights, _pos_group, _age_as_of, _age_factor,
    _compute_pos_means, _compute_pos_median_toi,
)

DB = "data/hockey.db"
TARGET_SEASON = 20242025
CATEGORIES = ("goals", "assists", "shots", "blocks", "hits", "pp_points")


def main():
    conn = sqlite3.connect(DB)
    prior_seasons = [r[0] for r in conn.execute(
        "SELECT DISTINCT season_id FROM skater_seasons"
        " WHERE season_id < ? ORDER BY season_id DESC LIMIT 3",
        (TARGET_SEASON,)).fetchall()]
    prior_seasons.reverse()

    pos_means_c = _compute_pos_means(conn, prior_seasons, "C")
    pos_means_w = _compute_pos_means(conn, prior_seasons, "W")
    pos_means_d = _compute_pos_means(conn, prior_seasons, "D")

    # Get actuals and per-GP actuals
    actuals: dict[str, dict[int, float]] = {}
    for cat in CATEGORIES:
        cur = conn.execute(
            f"SELECT player_id, {cat} FROM skater_seasons"
            f" WHERE season_id = ? AND gp > 0", (TARGET_SEASON,))
        actuals[cat] = {r[0]: (float(r[1]) if r[1] is not None else 0.0)
                       for r in cur.fetchall()}

    # Get GP actuals for per-GP comparison
    gp_actuals = {r[0]: r[1] for r in conn.execute(
        "SELECT player_id, gp FROM skater_seasons"
        " WHERE season_id = ? AND gp > 0", (TARGET_SEASON,))}

    # Precompute per-player data
    skaters = [r[0] for r in conn.execute(
        "SELECT DISTINCT player_id FROM skater_seasons"
        " WHERE season_id < ? AND gp > 0", (TARGET_SEASON,))]

    # For each player, compute:
    # A) TOI-based projection (current model with k=300)
    # B) GP-based projection (per-GP rate * proj_gp)
    # Compare against actuals

    results = {cat: {"toi_preds": [], "toi_acts": [], "gp_preds": [], "gp_acts": []}
               for cat in CATEGORIES}

    for pid in skaters:
        row = conn.execute(
            "SELECT position FROM players WHERE player_id = ?", (pid,)).fetchone()
        if not row:
            continue
        pg = _pos_group(row[0])
        pm = pos_means_c if pg == "C" else pos_means_w if pg == "W" else pos_means_d

        birth_row = conn.execute(
            "SELECT birth_date, position FROM players WHERE player_id = ?",
            (pid,)).fetchone()
        if not birth_row:
            continue
        birth_date, pos = birth_row
        age = _age_as_of(birth_date, TARGET_SEASON)
        af = _age_factor(age)

        seasons = conn.execute(
            "SELECT season_id, gp, goals, assists, shots, blocks, hits,"
            " pp_points, pp_toi_sec, toi_sec"
            " FROM skater_seasons WHERE player_id = ? AND season_id < ? AND gp > 0"
            " ORDER BY season_id DESC LIMIT 3", (pid, TARGET_SEASON)).fetchall()
        if not seasons:
            continue

        weights = _season_weights([s[1] for s in seasons])
        k = 300

        # --- TOI-based (current) ---
        for cat in CATEGORIES:
            rates = []
            n_total = 0
            for i, s in enumerate(seasons):
                gp, goals, assists, shots, blocks, hits, pp_pts, pp_toi, toi = (
                    s[1], s[2], s[3], s[4], s[5], s[6], s[7], s[8], s[9])
                if cat == "pp_points":
                    if pp_toi is not None and pp_toi > 0:
                        rate = (pp_pts or 0) / (pp_toi / 3600)
                        n_total += pp_toi / 60.0
                    else:
                        rate = 0.0
                else:
                    if toi and toi > 0:
                        if cat == "goals": val = goals
                        elif cat == "assists": val = assists
                        elif cat == "shots": val = shots
                        elif cat == "blocks": val = blocks
                        else: val = hits
                        rate = (val or 0) / (toi / 3600)
                        n_total += toi / 60.0
                    else:
                        rate = 0.0
                rates.append(rate)
            raw_rate = sum(weights[i] * rates[i] for i in range(len(seasons)))
            regressed = (raw_rate * n_total + pm.get(cat, 0) * k) / (n_total + k)

            # TOI projection
            toi_per_gp = sum(
                weights[i] * (seasons[i][9] / seasons[i][1])
                for i in range(len(seasons))
                if seasons[i][1] > 0 and seasons[i][9] is not None)
            pp_toi_per_gp = sum(
                weights[i] * (seasons[i][8] / seasons[i][1])
                for i in range(len(seasons))
                if seasons[i][1] > 0 and seasons[i][8] is not None)

            career_gp = conn.execute(
                "SELECT COALESCE(SUM(gp),0) FROM skater_seasons WHERE player_id = ?",
                (pid,)).fetchone()[0]
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
            durability = max(0.55, min(career_gp / team_games, 1.0)) if team_games > 0 else 0.70
            proj_gp = 82.0 * durability

            if cat == "pp_points":
                toi_pg = pp_toi_per_gp
            else:
                toi_pg = toi_per_gp

            total_toi = regressed * af * (toi_pg / 3600) * proj_gp

            if pid in actuals[cat]:
                results[cat]["toi_preds"].append(total_toi)
                results[cat]["toi_acts"].append(actuals[cat][pid])

        # --- GP-based (simpler) ---
        # Per-GP rates: rate = stat / gp
        for cat in CATEGORIES:
            gp_rates = []
            for i, s in enumerate(seasons):
                gp = s[1]
                if cat == "goals":
                    val = s[2]
                elif cat == "assists":
                    val = s[3]
                elif cat == "shots":
                    val = s[4]
                elif cat == "blocks":
                    val = s[5]
                elif cat == "hits":
                    val = s[6]
                else:
                    val = s[7]
                if gp > 0:
                    gp_rates.append(weights[i] * (val or 0) / gp)
                else:
                    gp_rates.append(0.0)

            raw_gp_rate = sum(gp_rates)
            # Position mean per GP: total stat / total GP for position
            # Use k=0 (no regression) first to see raw performance
            regressed_gp = raw_gp_rate  # no shrinkage for now

            # Project
            career_gp = conn.execute(
                "SELECT COALESCE(SUM(gp),0) FROM skater_seasons WHERE player_id = ?",
                (pid,)).fetchone()[0]
            all_seasons2 = conn.execute(
                "SELECT season_id, team_abbrev FROM skater_seasons WHERE player_id = ?",
                (pid,)).fetchall()
            team_games = 0
            for sid, team in all_seasons2:
                g = conn.execute(
                    "SELECT COUNT(*) FROM schedule WHERE season_id = ? AND"
                    " (home_abbrev = ? OR away_abbrev = ?)",
                    (sid, team, team)).fetchone()[0]
                team_games += g
            durability = max(0.55, min(career_gp / team_games, 1.0)) if team_games > 0 else 0.70
            proj_gp = 82.0 * durability

            total_gp = regressed_gp * af * proj_gp

            if pid in actuals[cat]:
                results[cat]["gp_preds"].append(total_gp)
                results[cat]["gp_acts"].append(actuals[cat][pid])

    conn.close()

    # Compare MAEs
    print(f"{'Category':<12} {'TOI MAE':>8} {'GP MAE':>8} {'Delta':>8} {'Winner'}")
    print("-" * 46)
    for cat in CATEGORIES:
        r = results[cat]
        mae_toi = sum(abs(p - a) for p, a in zip(r["toi_preds"], r["toi_acts"])) / len(r["toi_acts"]) if r["toi_acts"] else 0
        mae_gp = sum(abs(p - a) for p, a in zip(r["gp_preds"], r["gp_acts"])) / len(r["gp_acts"]) if r["gp_acts"] else 0
        delta = mae_gp - mae_toi
        winner = "GP" if mae_gp < mae_toi else "TOI"
        print(f"{cat:<12} {mae_toi:>8.3f} {mae_gp:>8.3f} {delta:>+8.3f} {winner}")

    # Also show average over/under estimate
    print(f"\n{'Category':<12} {'TOI bias':>10} {'GP bias':>10}")
    print("-" * 34)
    for cat in CATEGORIES:
        r = results[cat]
        if r["toi_acts"]:
            toi_bias = sum(p - a for p, a in zip(r["toi_preds"], r["toi_acts"])) / len(r["toi_acts"])
        else:
            toi_bias = 0
        if r["gp_acts"]:
            gp_bias = sum(p - a for p, a in zip(r["gp_preds"], r["gp_acts"])) / len(r["gp_acts"])
        else:
            gp_bias = 0
        print(f"{cat:<12} {toi_bias:>+10.3f} {gp_bias:>+10.3f}")


if __name__ == "__main__":
    main()