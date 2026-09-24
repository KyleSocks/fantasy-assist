"""Scratch: exercise src/draft/recommend on real data to pin test values."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.draft.recommend import recommend
from src.projections.projections import project_all
from src.valuation.league import load_league
from src.valuation.valuation import valuate

DB = str(ROOT / "data" / "hockey.db")
CFG_DIR = ROOT / "config" / "leagues"


def build(cfg_name: str):
    cfg = load_league(str(CFG_DIR / f"{cfg_name}.yaml"))
    projs = project_all(20262027, DB)
    vals = valuate(projs, cfg)
    return vals, cfg


vals, cfg = build("roto_14")
by_id = {v.player_id: v for v in vals}

conn = sqlite3.connect(DB)
print("positions:", conn.execute(
    "SELECT position, COUNT(*) FROM players GROUP BY position").fetchall())
conn.close()

raw_top = sorted(vals, key=lambda v: (-v.vorp, v.player_id))[:5]
print("raw top5:", [(v.player_id, v.position, round(v.vorp, 4)) for v in raw_top])

# state 1: empty roster
r0 = recommend([], [], vals, cfg, 1)
print("=== empty roster (roto_14) ===")
for r in r0:
    print(f"  {r.name} ({r.player_id}) mv={r.marginal_value:.4f} | {r.reason}")

# state 2: drafted the top available G
gs = sorted([v for v in vals if v.position == "G"],
            key=lambda v: (-v.vorp, v.player_id))
g = gs[0]
print("top G:", g.player_id, round(g.vorp, 4))
r1 = recommend([g.player_id], [g.player_id], vals, cfg, 2)
print("=== after drafting top G ===")
for r in r1:
    print(f"  {r.name} ({r.player_id}) mv={r.marginal_value:.4f} | {r.reason}")
print("differs from empty:",
      [(r.player_id, round(r.marginal_value, 6)) for r in r1] !=
      [(r.player_id, round(r.marginal_value, 6)) for r in r0])

# state 3: top 2 C drafted, 0 D
cs = sorted([v for v in vals if v.position == "C"],
            key=lambda v: (-v.vorp, v.player_id))
top2 = cs[:2]
ids3 = [v.player_id for v in top2]
print("top 2 C:", [(v.player_id, round(v.vorp, 4)) for v in top2])
r3 = recommend(ids3, ids3, vals, cfg, 3)
print("=== 2C drafted, 0D ===")
for r in r3:
    v = by_id[r.player_id]
    print(f"  {r.name} ({r.player_id}, {v.position}) mv={r.marginal_value:.4f} | {r.reason}")

# state 4: all starters full + one extra util C
taken: set[int] = set()
roster: list[int] = []
for pos, n in (("C", 2), ("L", 2), ("R", 2), ("D", 4), ("G", 2)):
    cand = sorted((v for v in vals if v.position == pos and v.player_id not in taken),
                 key=lambda v: (-v.vorp, v.player_id))[:n]
    for v in cand:
        roster.append(v.player_id)
        taken.add(v.player_id)
util = sorted((v for v in vals if v.position == "C" and v.player_id not in taken),
             key=lambda v: (-v.vorp, v.player_id))[0]
roster.append(util.player_id)
print("state4 roster:", roster)
print("state4 roster vorps:", [round(by_id[i].vorp, 4) for i in roster])
r4 = recommend(roster, roster, vals, cfg, 14)
print("=== all starters full + util ===")
for r in r4:
    v = by_id[r.player_id]
    print(f"  {r.name} ({r.player_id}, {v.position}) vorp={v.vorp:.4f} mv={r.marginal_value:.4f} | {r.reason}")

# state 4 debug: team state, urgency, pickup for each rec
from src.draft.recommend import (_scarcity, _team_category_state,
                                 _pickup, _roster_occupancy, _bench_asset)
pos_of = {v.player_id: v.position for v in vals}
cat_by_pid = {v.player_id: v.cat_zscores for v in vals}
occ, util_full = _roster_occupancy(roster, pos_of, cfg)
urg = _scarcity(set(roster), [v for v in vals if v.player_id not in set(roster)], pos_of, cfg)
ts = _team_category_state(roster, cat_by_pid, cfg)
print("occ:", occ, "util_full:", util_full)
print("urgency:", {k: (round(a, 4), b, c) for k, (a, b, c) in urg.items()})
print("team_state:", {k: round(v, 4) for k, v in ts.items()})
for r in r4:
    v = by_id[r.player_id]
    print(f"  pickup({v.player_id})={_pickup(v, ts):.4f} cat_z={ {k: round(z,4) for k,z in v.cat_zscores.items()} }")

# distinctness across the 4 states
states = [r0, r1, r3, r4]
keys = [tuple((r.player_id, round(r.marginal_value, 6)) for r in s) for s in states]
print("all four distinct:", len(set(keys)) == 4)

kvals, kcfg = build("kyle_12_points")
rk = recommend([], [], kvals, kcfg, 1)
print("=== empty roster (kyle_12_points) ===")
for r in rk:
    print(f"  {r.name} ({r.player_id}) mv={r.marginal_value:.4f} | {r.reason}")
