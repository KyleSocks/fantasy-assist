import sqlite3
conn = sqlite3.connect(r'data\hockey.db')
print('max skater season:', conn.execute('SELECT MAX(season_id) FROM skater_seasons').fetchone())
print('max goalie season:', conn.execute('SELECT MAX(season_id) FROM goalie_seasons').fetchone())
print('schedule seasons:', conn.execute('SELECT DISTINCT season_id FROM schedule ORDER BY season_id DESC LIMIT 3').fetchall())
q = 'SELECT team, COUNT(*) FROM (SELECT home_abbrev AS team FROM schedule WHERE season_id=20262027 AND game_type=2 UNION ALL SELECT away_abbrev FROM schedule WHERE season_id=20262027 AND game_type=2) WHERE team IN (\'LAK\',\'MIN\',\'CGY\') GROUP BY team'
print('team games 20262027:', conn.execute(q).fetchall())
for name in ('Portillo','Garand','Sergeev','Gustavsson'):
    row = conn.execute('SELECT player_id, full_name, position, current_team_abbrev FROM players WHERE full_name LIKE ?', ('%'+name+'%',)).fetchall()
    print(name, row)
