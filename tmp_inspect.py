"""Temp: inspect hockey.db schema for available stats."""
import sqlite3

c = sqlite3.connect("data/hockey.db")
cur = c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("tables:", tables)
for t in ("skater_seasons", "goalie_seasons"):
    if t in tables:
        cur = c.execute(f"PRAGMA table_info({t})")
        print(f"{t}: {[r[1] for r in cur.fetchall()]}")
        cur = c.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"{t} rows: {cur.fetchone()[0]}")
c.close()
