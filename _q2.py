import sqlite3
conn = sqlite3.connect(r'data\hockey.db')
for pid, name in ((8481707,'Portillo'),(8482193,'Garand'),(8482949,'Sergeev'),(8479406,'Gustavsson')):
    rows = conn.execute('SELECT season_id, gp, games_started, wins, saves, shots_against, goals_against, shutouts, team_abbrev FROM goalie_seasons WHERE player_id=? ORDER BY season_id', (pid,)).fetchall()
    print(name, rows)
print('--- goalies on LAK/MIN/CGY 20252026:')
for team in ('LAK','MIN','CGY'):
    rows = conn.execute('SELECT p.player_id, p.full_name, COUNT(*) FROM players p JOIN goalie_seasons g ON p.player_id=g.player_id WHERE p.current_team_abbrev=? AND g.season_id<20262027 GROUP BY p.player_id', (team,)).fetchall()
    print(team, rows)
print('--- goalies w/o team:')
rows = conn.execute("SELECT p.player_id, p.full_name FROM players p WHERE p.position='G' AND (p.current_team_abbrev IS NULL OR p.current_team_abbrev='') AND EXISTS (SELECT 1 FROM goalie_seasons g WHERE g.player_id=p.player_id)").fetchall()
print(rows)
print('--- total goalies with seasons:', conn.execute("SELECT COUNT(DISTINCT p.player_id) FROM players p JOIN goalie_seasons g ON p.player_id=g.player_id").fetchone())
