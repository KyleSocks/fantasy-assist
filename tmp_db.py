import sqlite3
conn = sqlite3.connect('data/hockey.db')
cur = conn.cursor()
# Check goalie_seasons schema
cur.execute("PRAGMA table_info(goalie_seasons)")
print("goalie_seasons columns:", [r[1] for r in cur.fetchall()])
# Check skater_seasons schema
cur.execute("PRAGMA table_info(skater_seasons)")
print("skater_seasons columns:", [r[1] for r in cur.fetchall()])
# Check players schema
cur.execute("PRAGMA table_info(players)")
print("players columns:", [r[1] for r in cur.fetchall()])
# Sample goalie data
cur.execute("SELECT * FROM goalie_seasons LIMIT 3")
for row in cur.fetchall():
    print("G:", row)
# Sample skater data
cur.execute("SELECT * FROM skater_seasons LIMIT 3")
for row in cur.fetchall():
    print("S:", row)