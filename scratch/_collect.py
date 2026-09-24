"""Scratch: collect real data for the offline hardening tests. Run, then delete."""
import sqlite3

DB = r"c:\Users\kyles\Desktop\Fantasy Hockey Assistant\data\hockey.db"

# 1) Backup goalies whose career start ratio is below the 0.30 floor.
conn = sqlite3.connect(DB)
print("=== backup goalies, career gp > 5, ascending start ratio ===")
for r in conn.execute(
    "SELECT player_id, SUM(gp) AS cg, SUM(games_started) AS cs "
    "FROM goalie_seasons WHERE gp > 0 "
    "GROUP BY player_id HAVING cg > 5 ORDER BY (cs * 1.0) / cg ASC LIMIT 8"
):
    print(r[0], "cg", r[1], "cs", r[2], "ratio", round(r[2] / r[1], 4))
conn.close()
