import sys
sys.path.insert(0, '.')
from src.projections.projections import project_all
from src.valuation.league import load_league
from src.valuation.valuation import valuate
from pathlib import Path
import sqlite3

projs = project_all(20252026, 'data/hockey.db')
cfg = load_league(str(Path('config/leagues/kyle_12_points.yaml').resolve()))
vals = valuate(projs, cfg)

conn = sqlite3.connect("data/hockey.db")
cur = conn.cursor()
cur.execute("SELECT player_id, full_name, current_team_abbrev FROM players")
names = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
conn.close()

p_by_id = {p.player_id: p for p in projs}
gs = [v for v in vals if v.position == 'G']
gs.sort(key=lambda v: v.vorp, reverse=True)

print(f'Total goalies: {len(gs)}')
print()
print(f'{"Rank":>4} {"Name":<30} {"Team":>5} {"VORP":>8} {"SV%":>6} {"W":>4} {"GP":>6} {"Z_SV%":>7} {"Z_W":>7} {"Z_G":>6}')
print('-' * 85)
for i, g in enumerate(gs[:40], 1):
    nm = names.get(g.player_id, ('???', '???'))
    p = p_by_id.get(g.player_id)
    svp = f'{p.save_pct:.3f}' if p and p.save_pct is not None else 'N/A'
    w = f'{p.wins:.0f}' if p and p.wins is not None else 'N/A'
    gp = f'{p.gp:.1f}' if p else 'N/A'
    z_sv = f'{g.cat_zscores.get("SV%", 0):.3f}'
    z_w = f'{g.cat_zscores.get("W", 0):.3f}'
    z_g = f'{g.cat_zscores.get("G", 0):.3f}'
    print(f'{i:>4} {nm[0]:<30} {nm[1]:>5} {g.vorp:>8.3f} {svp:>6} {w:>4} {gp:>6} {z_sv:>7} {z_w:>7} {z_g:>6}')
