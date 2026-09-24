import sqlite3
from src.ingest.ingest import ingest_seasons

# Wipe skater_seasons so we get a clean re-ingest with pp_points
conn = sqlite3.connect("data/hockey.db")
conn.execute("DELETE FROM skater_seasons")
conn.commit()
conn.close()
print("Cleared skater_seasons")

report = ingest_seasons(5, db_path="data/hockey.db")
print(f"skater_seasons: {report.rows_written.get('skater_seasons', 0)} rows written")

conn = sqlite3.connect("data/hockey.db")
check = conn.execute(
    "SELECT SUM(CASE WHEN pp_points IS NULL THEN 1 ELSE 0 END),"
    " SUM(CASE WHEN pp_points > 0 THEN 1 ELSE 0 END) FROM skater_seasons"
).fetchone()
print(f"pp_points NULL: {check[0]}, pp_points>0: {check[1]}")
conn.close()