"""Find 1-season / 2-season skaters that have a players row."""
from __future__ import annotations

import sqlite3

conn = sqlite3.connect("data/hockey.db")

for target in (1, 2):
    rows = conn.execute(
        "SELECT s.player_id, p.full_name, p.position, p.current_team_abbrev "
        "FROM skater_seasons s JOIN players p ON p.player_id = s.player_id "
        "WHERE s.season_id < 20262027 AND s.gp > 0 AND p.full_name IS NOT NULL "
        "GROUP BY s.player_id "
        "HAVING COUNT(*) = ? "
        "ORDER BY s.player_id LIMIT 6", (target,)
    ).fetchall()
    print(f"{target}-season skaters (with players row):", rows)

# Confirm McDavid / Tomkins team abbrev for schedule lookup
for pid in (8478402, 8477035):
    row = conn.execute(
        "SELECT player_id, full_name, position, current_team_abbrev "
        "FROM players WHERE player_id=?", (pid,)
    ).fetchone()
    print("players row:", row)
conn.close()
