import sqlite3
conn = sqlite3.connect("data/hockey.db")
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("Tables:", tables)

# Check for a players table with names
for t in tables:
    cur.execute(f"PRAGMA table_info({t})")
    cols = [r[1] for r in cur.fetchall()]
    if any("name" in c.lower() for c in cols):
        print(f"\nTable {t} has name column(s): {cols}")
        cur.execute(f"SELECT * FROM {t} LIMIT 5")
        for row in cur.fetchall():
            print(row)
conn.close()
