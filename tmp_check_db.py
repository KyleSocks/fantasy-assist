import sqlite3
con = sqlite3.connect('data/hockey.db')
tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("tables:", tables)
print("players cols:", [r[1] for r in con.execute("PRAGMA table_info(players)")])
seasons = con.execute("SELECT DISTINCT season_id FROM seasons ORDER BY season_id").fetchall()
print("seasons:", [r[0] for r in seasons])
con.close()