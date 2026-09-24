import sys
sys.path.insert(0, '.')
from src.projections.projections import project_all
from pathlib import Path
import sqlite3

projs = project_all(20252026, 'data/hockey.db')
p_by_id = {p.player_id: p for p in projs}

for pid in [8480382, 8475852, 8473503, 8476234]:
    p = p_by_id.get(pid)
    if p:
        print(f"pid={pid}: gp={p.gp:.1f}, wins={p.wins}, save_pct={p.save_pct:.4f}, position={p.position}")

# Check historical data for 8480382
conn = sqlite3.connect("data/hockey.db")
cur = conn.cursor()
cur.execute("SELECT * FROM goalie_seasons WHERE player_id=8480382 ORDER BY season_id DESC")
for r in cur.fetchall():
    print(f"8480382: season={r[1]}, team={r[2]}, gp={r[3]}, gs={r[4]}, w={r[5]}, l={r[6]}, so={r[7]}, saves={r[8]}, sa={r[9]}")
conn.close()
