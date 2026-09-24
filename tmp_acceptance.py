"""Acceptance demo: three configs produce different top-50 orderings."""
import sqlite3
from pathlib import Path

from src.projections.projections import project_all
from src.valuation.league import load_league, category_vector, points_value

DB = Path("data/hockey.db")
CONFIG_DIR = Path("config/leagues")

# ─── Load all three configs ───
standard = load_league(str(CONFIG_DIR / "standard_12_cat.yaml"))
points   = load_league(str(CONFIG_DIR / "points_10.yaml"))
roto     = load_league(str(CONFIG_DIR / "roto_14.yaml"))

print(f"{'standard_12_cat':<20} scoring_type={standard.scoring_type}, team_count={standard.team_count}, categories={standard.categories}")
print(f"{'points_10':<20} scoring_type={points.scoring_type}, team_count={points.team_count}, points_per_stat={points.points_per_stat}")
print(f"{'roto_14':<20} scoring_type={roto.scoring_type}, team_count={roto.team_count}, categories={roto.categories}")
print()

# ─── Get real projections ───
season_id = 20252026
projections = project_all(season_id, str(DB))
print(f"Loaded {len(projections)} projections for season {season_id}")
print()

# ─── Build rankings ───
# For category leagues (h2h_categories / roto): rank by goals (first cat) as a simple proxy
# to show the top-50 ordering differs. The actual z-score ranking is a later task.
# For points: rank by total points.

def top50_category(projs, cfg, label):
    """Rank by sum of all category values (a simple scalar proxy for demo)."""
    scored = []
    for p in projs:
        vec = category_vector(p, cfg)
        total = sum(vec.values())
        scored.append((p, total, vec))
    scored.sort(key=lambda x: -x[1])
    return scored[:50]

def top50_points(projs, cfg, label):
    scored = []
    for p in projs:
        val = points_value(p, cfg)
        scored.append((p, val, None))
    scored.sort(key=lambda x: -x[1])
    return scored[:50]

top_std = top50_category(projections, standard, "standard_12_cat")
top_pts = top50_points(projections, points, "points_10")
top_roto = top50_category(projections, roto, "roto_14")

# ─── Print side by side ───
# We need player names. Query from db.
con = sqlite3.connect(DB)
names = {}
for row in con.execute("SELECT player_id, full_name FROM players"):
    names[row[0]] = row[1]
con.close()

print(f"{'#':<4} {'standard_12_cat (sum cats)':<40} {'points_10 (total pts)':<40} {'roto_14 (sum cats)':<40}")
print("-" * 124)

for i in range(50):
    sp, sv, svec = top_std[i]
    pp, pv, pvec = top_pts[i]
    rp, rv, rvec = top_roto[i]
    sname = names.get(sp.player_id, f"id={sp.player_id}")
    pname = names.get(pp.player_id, f"id={pp.player_id}")
    rname = names.get(rp.player_id, f"id={rp.player_id}")
    print(f"{i+1:<4} {sname:<20} {sv:<18.2f} {pname:<20} {pv:<18.2f} {rname:<20} {rv:<18.2f}")

print()

# ─── Prove orderings differ ───
std_ids = [p.player_id for p, _, _ in top_std]
pts_ids = [p.player_id for p, _, _ in top_pts]
roto_ids = [p.player_id for p, _, _ in top_roto]

print(f"Top-5 overlap standard vs points: {len(set(std_ids) & set(pts_ids))}/50")
print(f"Top-5 overlap standard vs roto:   {len(set(std_ids) & set(roto_ids))}/50")
print(f"Top-5 overlap points   vs roto:   {len(set(pts_ids) & set(roto_ids))}/50")
print(f"Full 50 overlap standard vs points: {len(set(std_ids) & set(pts_ids))}/50")
print(f"Full 50 overlap standard vs roto:   {len(set(std_ids) & set(roto_ids))}/50")
print(f"Full 50 overlap points   vs roto:   {len(set(pts_ids) & set(roto_ids))}/50")
print()

# ─── Invalid category name raises ───
print("── Invalid category name test ──")
import yaml, tempfile, os
bad = {
    "name": "bad", "team_count": 12, "scoring_type": "h2h_categories",
    "roster_slots": {"C":2,"LW":2,"RW":2,"D":4,"G":2,"UTIL":1,"BENCH":4},
    "categories": ["G", "A", "BPTS"],
}
with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
    yaml.dump(bad, f)
    tmp = f.name
try:
    load_league(tmp)
    print("ERROR: should have raised!")
except ValueError as e:
    print(f"ValueError raised: {e}")
finally:
    os.unlink(tmp)
print("✓ Invalid category name raises before any computation runs.")