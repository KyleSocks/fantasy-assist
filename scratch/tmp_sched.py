import sqlite3
from collections import Counter
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "hockey.db"
c = sqlite3.connect(DB)

# League-wide schedule for projection season 20262027
rows = c.execute("SELECT game_date FROM schedule WHERE season_id=20262027").fetchall()
print("total games 20262027:", len(rows))
ym = Counter(d[0][:7] for d in rows)  # YYYY-MM
print("by month (sorted):")
for k in sorted(ym):
    print("  ", k, ym[k])
counts = sorted(ym.values())
print("heaviest month:", ym.max(), max(ym.values()))
print("lightest month:", ym.min(), min(ym.values()))
print("min/max game_date:", min(d[0] for d in rows), max(d[0] for d in rows))

# Per-team (EDM) schedule for 20262027
edm = c.execute(
    "SELECT game_date FROM schedule WHERE season_id=20262027 AND (home_abbrev='EDM' OR away_abbrev='EDM')"
).fetchall()
print("\nEDM games 20262027:", len(edm))
edm_ym = Counter(d[0][:7] for d in edm)
print("EDM by month:", dict(sorted(edm_ym.items())))
if edm_ym:
    print("EDM heaviest:", max(edm_ym.values()), "lightest:", min(edm_ym.values()))

c.close()
