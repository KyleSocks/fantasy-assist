"""Throwaway inspection of player data for the drill-down endpoint."""
import sqlite3

conn = sqlite3.connect("data/hockey.db")

print("== McDavid / top players ==")
rows = conn.execute(
    "SELECT player_id, full_name, position, current_team_abbrev FROM players "
    "WHERE full_name LIKE '%McDavid%' OR full_name LIKE '%MacKinnon%'"
).fetchall()
for r in rows:
    print(r)

print("== top skaters by points last season ==")
skaters = conn.execute(
    "SELECT s.player_id, p.full_name, s.season_id, s.gp, s.goals, s.assists, "
    "s.shots, s.blocks, s.hits, s.pp_points, s.toi_sec, s.pp_toi_sec, s.team_abbrev "
    "FROM skater_seasons s JOIN players p ON p.player_id = s.player_id "
    "WHERE s.season_id = 20252026 ORDER BY (s.goals + s.assists) DESC LIMIT 3"
).fetchall()
for r in skaters:
    print(r)

print("== top goalies last season ==")
goalies = conn.execute(
    "SELECT s.player_id, p.full_name, s.season_id, s.gp, s.games_started, s.wins, "
    "s.saves, s.shots_against, s.goals_against, s.shutouts, s.team_abbrev "
    "FROM goalie_seasons s JOIN players p ON p.player_id = s.player_id "
    "WHERE s.season_id = 20252026 AND s.games_started > 40 "
    "ORDER BY s.games_started DESC LIMIT 3"
).fetchall()
for r in goalies:
    print(r)

if skaters:
    pid = skaters[0][0]
    print("== all skater seasons for top skater ==", pid)
    rows = conn.execute(
        "SELECT season_id, gp, goals, assists, shots, blocks, hits, pp_points, "
        "toi_sec, pp_toi_sec, team_abbrev FROM skater_seasons "
        "WHERE player_id = ? ORDER BY season_id", (pid,)
    ).fetchall()
    for r in rows:
        print(r)

print("== players with only one season of skater data ==")
rows = conn.execute(
    "SELECT player_id, COUNT(*) c FROM skater_seasons "
    "GROUP BY player_id HAVING c = 1 ORDER BY MAX(gp) DESC LIMIT 3"
).fetchall()
print(rows)
if rows:
    pid = rows[0][0]
    name = conn.execute(
        "SELECT full_name, position FROM players WHERE player_id = ?", (pid,)
    ).fetchone()
    print("one-season player:", pid, name)
    rows2 = conn.execute(
        "SELECT season_id, gp, goals, assists, shots FROM skater_seasons "
        "WHERE player_id = ? ORDER BY season_id", (pid,)
    ).fetchall()
    for r in rows2:
        print(r)

print("== schedule per season ==")
rows = conn.execute(
    "SELECT season_id, COUNT(*), MIN(game_date), MAX(game_date) FROM schedule "
    "GROUP BY season_id ORDER BY season_id"
).fetchall()
for r in rows:
    print(r)

conn.close()
