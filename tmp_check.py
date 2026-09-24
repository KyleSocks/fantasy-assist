import sqlite3
conn = sqlite3.connect("data/hockey.db")
cur = conn.cursor()
cur.execute("SELECT * FROM goalie_seasons WHERE player_id IN (8480382, 8475852, 8473503, 8476234) AND season_id=20252026")
for r in cur.fetchall():
    print(r)
conn.close()
