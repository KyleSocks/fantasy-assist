"""Tune k for per-GP rate approach."""
from __future__ import annotations
import sqlite3
from src.projections.projections import (
    _load_config, _season_weights, _pos_group, _age_as_of, _age_factor,
)

DB = "data/hockey.db"
TARGET = 20242025
CATS = ("goals", "assists", "shots", "blocks", "hits", "pp_points")


def main():
    conn = sqlite3.connect(DB)
    prior_seasons = [r[0] for r in conn.execute(
        "SELECT DISTINCT season_id FROM skater_seasons"
        " WHERE season_id < ? ORDER BY season_id DESC LIMIT 3", (TARGET,)).fetchall()]
    prior_seasons.reverse()
    season_list = ",".join(str(s) for s in prior_seasons)

    # Position mean per GP (DB positions: C, D, G, L, R; groups: C, W, D)
    pos_gp_means = {}
    pos_to_db = {"C": ("C",), "W": ("L", "R"), "D": ("D",)}
    for pos, db_positions in pos_to_db.items():
        placeholders = ",".join("?" * len(db_positions))
        sql = ("SELECT COALESCE(SUM(goals),0), COALESCE(SUM(assists),0),"
               " COALESCE(SUM(shots),0), COALESCE(SUM(blocks),0),"
               " COALESCE(SUM(hits),0), COALESCE(SUM(pp_points),0),"
               " COALESCE(SUM(gp),0)"
               " FROM skater_seasons s JOIN players p ON s.player_id = p.player_id"
               " WHERE s.season_id IN ({}) AND p.position IN ({}) AND s.gp > 0"
               .format(season_list, placeholders))
        cur = conn.execute(sql, db_positions)
        g, a, sh, b, h, pp, total_gp = cur.fetchone()
        total_gp = max(total_gp, 1)
        pos_gp_means[pos] = {
            "goals": g / total_gp, "assists": a / total_gp,
            "shots": sh / total_gp, "blocks": b / total_gp,
            "hits": h / total_gp, "pp_points": pp / total_gp,
        }
        print(f"Pos {pos}: gp/goal={g/total_gp:.3f} gp/ast={a/total_gp:.3f}"
              f" gp/sh={sh/total_gp:.3f} gp/pp={pp/total_gp:.3f}")

    # Get actuals
    actuals = {}
    for cat in CATS:
        actuals[cat] = {r[0]: (float(r[1]) if r[1] is not None else 0.0)
                       for r in conn.execute(
                           f"SELECT player_id, {cat} FROM skater_seasons"
                           f" WHERE season_id = ? AND gp > 0", (TARGET,))}

    skaters = [r[0] for r in conn.execute(
        "SELECT DISTINCT player_id FROM skater_seasons"
        " WHERE season_id < ? AND gp > 0", (TARGET,))]

    # Precompute per-player data
    player_data = {}
    for pid in skaters:
        row = conn.execute(
            "SELECT position, birth_date FROM players WHERE player_id = ?",
            (pid,)).fetchone()
        if not row:
            continue
        pos, birth_date = row
        pg = _pos_group(pos)
        age = _age_as_of(birth_date, TARGET)
        af = _age_factor(age)

        seasons = conn.execute(
            "SELECT season_id, gp, goals, assists, shots, blocks, hits,"
            " pp_points, pp_toi_sec, toi_sec"
            " FROM skater_seasons WHERE player_id = ? AND season_id < ? AND gp > 0"
            " ORDER BY season_id DESC LIMIT 3", (pid, TARGET)).fetchall()
        if not seasons:
            continue

        weights = _season_weights([s[1] for s in seasons])

        gp_rates = {}
        for cat in CATS:
            rates = []
            for i, s in enumerate(seasons):
                gp = s[1]
                if cat == "goals": val = s[2]
                elif cat == "assists": val = s[3]
                elif cat == "shots": val = s[4]
                elif cat == "blocks": val = s[5]
                elif cat == "hits": val = s[6]
                else: val = s[7]
                if gp > 0:
                    rates.append(weights[i] * (val or 0) / gp)
                else:
                    rates.append(0.0)
            gp_rates[cat] = sum(rates)

        total_gp = sum(s[1] for s in seasons)

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

        player_data[pid] = {
            "pg": pg, "af": af, "gp_rates": gp_rates,
            "total_gp": total_gp, "proj_gp": proj_gp,
        }

    # Test k values
    k_values = [14, 28, 42, 56, 70, 84, 112, 140, 168]
    print(f"\n{'K':>4} | " + " | ".join(f"{c:>10}" for c in CATS))
    print("-" * (4 + 12 * len(CATS)))
    for k in k_values:
        maes = []
        for cat in CATS:
            total_err = 0
            count = 0
            for pid, pd in player_data.items():
                if pid not in actuals[cat]:
                    continue
                raw = pd["gp_rates"][cat]
                pm = pos_gp_means[pd["pg"]][cat]
                n = pd["total_gp"]
                regressed = (raw * n + pm * k) / (n + k)
                pred = regressed * pd["af"] * pd["proj_gp"]
                total_err += abs(pred - actuals[cat][pid])
                count += 1
            maes.append(total_err / count if count else 0)
        print(f"{k:>4} | " + " | ".join(f"{m:>10.3f}" for m in maes))

    conn.close()


if __name__ == "__main__":
    main()