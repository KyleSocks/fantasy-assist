import sqlite3

conn = sqlite3.connect('data/hockey.db')

# Check schema
cur = conn.execute('PRAGMA table_info(skater_seasons)')
print('skater_seasons columns:')
for r in cur.fetchall():
    print(f'  {r[1]} ({r[2]})')

cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
print('\nTables:', [r[0] for r in cur.fetchall()])

# pp_points distribution in projection window
print('\n=== pp_points by season ===')
cur = conn.execute("""
    SELECT season_id, COUNT(*) as n, 
           SUM(CASE WHEN pp_points > 0 THEN 1 ELSE 0 END) as with_pp,
           AVG(pp_points) as avg_pp, MAX(pp_points) as max_pp
    FROM skater_seasons WHERE gp > 0
    GROUP BY season_id ORDER BY season_id
""")
for r in cur.fetchall():
    print(f'  {r[0]}: n={r[1]}, with_pp={r[2]}, avg_pp={r[3]:.2f}, max_pp={r[4]}')

# Positional pp_points rates (position is likely in skater_seasons or players)
# Check if skater_seasons has position-like column
cur = conn.execute("""
    SELECT p.full_name, ss.pp_points, ss.gp, ss.pp_toi_sec
    FROM skater_seasons ss JOIN players p ON ss.player_id = p.player_id
    WHERE ss.season_id = 20232024 AND ss.pp_points > 0 AND ss.gp > 0
    ORDER BY ss.pp_points DESC LIMIT 10
""")
print('\n=== Top pp_points players 20232024 (projection window) ===')
for r in cur.fetchall():
    print(f'  {r[0]}: pp_points={r[1]}, gp={r[2]}, pp_toi={r[3]}')

# Run model and compare
print('\n=== Model vs actual for pp_points (top 15 actual) ===')
from src.projections.projections import project_all
projs = project_all(20242025, 'data/hockey.db')
proj_by_pid = {p.player_id: p for p in projs}

cur = conn.execute("""
    SELECT ss.player_id, p.full_name, ss.pp_points, ss.gp
    FROM skater_seasons ss JOIN players p ON ss.player_id = p.player_id
    WHERE ss.season_id = 20242025 AND ss.gp > 0
    ORDER BY ss.pp_points DESC LIMIT 15
""")
for pid, name, pp, gp in cur.fetchall():
    if pid in proj_by_pid:
        model_pp = proj_by_pid[pid].pp_points
        print(f'  {name}: model={model_pp:.2f}, actual={pp}, gp={gp}, err={abs(model_pp-pp):.2f}')
    else:
        print(f'  {name}: NOT IN MODEL, actual={pp}, gp={gp}')

# Check baseline for same players
print('\n=== Baseline vs actual for pp_points (same top 15) ===')
cur = conn.execute("""
    SELECT ss.player_id, p.full_name, ss.pp_points, ss.gp
    FROM skater_seasons ss JOIN players p ON ss.player_id = p.player_id
    WHERE ss.season_id = 20242025 AND ss.gp > 0
    ORDER BY ss.pp_points DESC LIMIT 15
""")
for pid, name, pp, gp in cur.fetchall():
    cur2 = conn.execute("""
        SELECT pp_points FROM skater_seasons 
        WHERE player_id = ? AND season_id = 20232024
    """, (pid,))
    row = cur2.fetchone()
    base_pp = row[0] if row and row[0] is not None else 0
    print(f'  {name}: baseline={base_pp}, actual={pp}, err={abs(base_pp-pp):.2f}')

# Distribution of model errors
print('\n=== Error distribution for pp_points ===')
errors = []
cur = conn.execute("""
    SELECT ss.player_id, ss.pp_points
    FROM skater_seasons ss
    WHERE ss.season_id = 20242025 AND ss.gp > 0
""")
for pid, pp in cur.fetchall():
    if pid in proj_by_pid:
        errors.append(abs(proj_by_pid[pid].pp_points - pp))
    else:
        errors.append(abs(0 - pp))  # baseline treats missing as 0

errors.sort()
n = len(errors)
print(f'n={n}')
print(f'median error: {errors[n//2]:.3f}')
print(f'p90 error: {errors[int(n*0.9)]:.3f}')
print(f'p95 error: {errors[int(n*0.95)]:.3f}')
print(f'max error: {errors[-1]:.3f}')
print(f'players with err > 10: {sum(1 for e in errors if e > 10)}')
print(f'players with err > 5: {sum(1 for e in errors if e > 5)}')
print(f'players with err > 1: {sum(1 for e in errors if e > 1)}')
conn.close()