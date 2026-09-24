"""Compute the exact chart arrays + bands to encode in tests."""
from __future__ import annotations

from src.api.routes import build_player_detail
from src.projections.projections import explain

# ---- McDavid (skater, 3 seasons) ----
d = build_player_detail(8478402, "roto_14")
print("MCDAVID labels:", d["seasons"], "->", [str(s) for s in d["seasons"]])
print("MCDAVID band_note:", d.get("band_note"))
for c in d["categories"]:
    if c["key"] in ("goals", "assists"):
        print(f"  {c['key']}: actuals={c['actuals']} proj={round(c['projection'],6)} band={[round(x,6) for x in c['band']]}")

# ---- Tomkins (goalie, 1 season) ----
g = build_player_detail(8477035, "roto_14")
print("\nTOMKINS seasons:", [str(s) for s in g["seasons"]])
print("TOMKINS band_note:", g.get("band_note"))
for c in g["categories"]:
    print(f"  {c['key']}: actuals={c['actuals']} proj={round(c['projection'],6)} band={[round(x,6) for x in c['band']]}")

# ---- Mason Geertsen (8477419, skater, 1 season) ----
s = build_player_detail(8477419, "roto_14")
print("\nGEERTSEN seasons:", [str(x) for x in s["seasons"]])
for c in s["categories"]:
    if c["key"] == "goals":
        print("  goals: actuals=", c["actuals"], "proj=", round(c["projection"], 6),
              "band=", [round(x, 6) for x in c["band"]],
              "width=", round(c["band"][1] - c["band"][0], 6))

# ---- Belzile (zero-season) players row check ----
import sqlite3
conn = sqlite3.connect("data/hockey.db")
row = conn.execute("SELECT player_id, full_name, position, current_team_abbrev FROM players WHERE player_id=8475968").fetchone()
print("\nBELZILE players row:", row)
print("BELZILE detail:", build_player_detail(8475968, "roto_14"))
conn.close()

# ---- breakdown row counts ----
print("\nBREAKDOWN skater rows:", len(build_player_detail(8478402, "roto_14")["breakdown"]))
print("BREAKDOWN goalie rows:", len(build_player_detail(8477035, "roto_14")["breakdown"]))
print("explain skater keys:", len(explain(8478402, 20262027, "data/hockey.db")))
print("explain goalie keys:", len(explain(8477035, 20262027, "data/hockey.db")))
