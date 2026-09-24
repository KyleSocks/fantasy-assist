"""Quick probe for drill-down test fixtures."""
from __future__ import annotations

import sqlite3

conn = sqlite3.connect("data/hockey.db")

one = conn.execute(
    "SELECT player_id, COUNT(*) c FROM skater_seasons "
    "WHERE season_id<20262027 AND gp>0 GROUP BY player_id "
    "HAVING c=1 ORDER BY player_id LIMIT 6"
).fetchall()
print("1-season skaters:", one)

two = conn.execute(
    "SELECT player_id, COUNT(*) c FROM skater_seasons "
    "WHERE season_id<20262027 AND gp>0 GROUP BY player_id "
    "HAVING c=2 ORDER BY player_id LIMIT 6"
).fetchall()
print("2-season skaters:", two)

for pid, c in one[:3]:
    name = conn.execute(
        "SELECT full_name, position, current_team_abbrev FROM players "
        "WHERE player_id=?", (pid,)
    ).fetchone()
    print("  ", pid, name)

m = conn.execute(
    "SELECT season_id, gp, goals, assists FROM skater_seasons "
    "WHERE player_id=8478402 AND season_id<20262027 AND gp>0 "
    "ORDER BY season_id DESC LIMIT 3"
).fetchall()
print("MCDAVID seasons (reversed oldest->newest):",
      [tuple(r) for r in reversed(m)])

t = conn.execute(
    "SELECT season_id, gp, wins, saves, shots_against FROM goalie_seasons "
    "WHERE player_id=8477035 AND season_id<20262027 AND gp>0 "
    "ORDER BY season_id DESC LIMIT 3"
).fetchall()
print("TOMKINS seasons (reversed):", [tuple(r) for r in reversed(t)])
conn.close()
