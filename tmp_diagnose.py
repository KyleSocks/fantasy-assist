"""Diagnose pp_points catastrophe and assists/shots underperformance."""
import sqlite3

conn = sqlite3.connect("data/hockey.db")

# Check pp_points in training seasons vs target
for season in [20212022, 20222023, 20232024, 20242025]:
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(CASE WHEN pp_points > 0 THEN 1 ELSE 0 END), 0),"
        " AVG(pp_points), MAX(pp_points),"
        " COALESCE(SUM(CASE WHEN pp_points IS NULL THEN 1 ELSE 0 END), 0)"
        " FROM skater_seasons WHERE season_id = ? AND gp > 0", (season,)
    ).fetchone()
    print(f"Season {season}: total={row[0]}, nonzero_pp={row[1]}, avg_pp={row[2]:.3f}, max_pp={row[3]}, null_pp={row[4]}")

print()

# Check pp_points positional means for training seasons
for pos in ["C", "W", "D"]:
    cur = conn.execute(
        "SELECT AVG(pp_points / (toi_sec/3600)) FROM skater_seasons"
        " WHERE season_id IN (20212022, 20222023, 20232024)"
        " AND gp > 0 AND toi_sec > 0 AND position LIKE ?"
        .replace("LIKE ?", "LIKE ?"),
        (f"%{pos}%",) if pos != "C" else ("C",))
    # Actually let's do it properly
    if pos == "C":
        vals = conn.execute(
            "SELECT pp_points / (toi_sec/3600) FROM skater_seasons"
            " WHERE season_id IN (20212022, 20222023, 20232024)"
            " AND gp > 0 AND toi_sec > 0 AND position = 'C'"
        ).fetchall()
    else:
        vals = conn.execute(
            "SELECT pp_points / (toi_sec/3600) FROM skater_seasons"
            " WHERE season_id IN (20212022, 20222023, 20232024)"
            " AND gp > 0 AND toi_sec > 0 AND (position LIKE '%W%' OR position LIKE '%D%')"
            if pos == "W" else
            "SELECT pp_points / (toi_sec/3600) FROM skater_seasons"
            " WHERE season_id IN (20212022, 20222023, 20232024)"
            " AND gp > 0 AND toi_sec > 0 AND position LIKE '%D%'"
        ).fetchall()
    if vals:
        avg = sum(v[0] for v in vals) / len(vals)
        print(f"Position {pos} pp_points per-60 mean (3 seasons): {avg:.4f}")
    else:
        print(f"Position {pos} pp_points per-60 mean: NO DATA")

print()

# Check: for the target season, what are actual pp_points?
row = conn.execute(
    "SELECT COUNT(*), COALESCE(SUM(CASE WHEN pp_points > 0 THEN 1 ELSE 0 END), 0),"
    " AVG(pp_points), MAX(pp_points), SUM(pp_points)"
    " FROM skater_seasons WHERE season_id = 20242025 AND gp > 0"
).fetchone()
print(f"Target 20242025: total={row[0]}, nonzero_pp={row[1]}, avg_pp={row[2]:.3f}, max_pp={row[3]}, total_pp={row[4]}")

# Check: what does the model predict for pp_points?
from src.projections.projections import project_all
projs = project_all(20242025, "data/hockey.db")
pp_preds = [p.pp_points for p in projs if p.player_id is not None and p.goals > 0 or p.assists > 0 or p.shots > 0]
print(f"Model pp_points predictions: count={len(pp_preds)}, sum={sum(pp_preds):.1f}, avg={sum(pp_preds)/max(len(pp_preds),1):.3f}, max={max(pp_preds):.1f}")

# Check: what's the raw pp rate for a few top players?
print("\nTop pp_points players in target season:")
for r in conn.execute(
    "SELECT p.name, ss.pp_points, ss.toi_sec FROM skater_seasons ss"
    " JOIN players p ON ss.player_id = p.player_id"
    " WHERE ss.season_id = 20242025 AND ss.pp_points > 5 AND ss.gp > 0"
    " ORDER BY ss.pp_points DESC LIMIT 10"
):
    rate = r[1] / (r[2] / 3600) if r[2] > 0 else 0
    print(f"  {r[0]}: pp={r[1]}, toi={r[2]}, rate/60={rate:.2f}")

print("\nTheir training data (seasons before 20242025):")
for r in conn.execute(
    "SELECT p.name, ss.season_id, ss.pp_points, ss.toi_sec FROM skater_seasons ss"
    " JOIN players p ON ss.player_id = p.player_id"
    " WHERE ss.season_id < 20242025 AND ss.pp_points > 5 AND ss.gp > 0"
    " ORDER BY ss.pp_points DESC LIMIT 10"
):
    rate = r[1] / (r[3] / 3600) if r[3] > 0 else 0
    print(f"  {r[0]} {r[1]}: pp={r[1]}, toi={r[3]}, rate/60={rate:.2f}")

conn.close()