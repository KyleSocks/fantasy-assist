import sqlite3
c = sqlite3.connect(r'data/hockey.db')
for team in ('LAK','MIN','CGY','NYR','BOS','UTA','EDM'):
    rows = c.execute("""
    select p.player_id, p.full_name, sum(g.games_started), sum(g.gp), max(g.season_id)
    from players p join goalie_seasons g on p.player_id = g.player_id
    where p.current_team_abbrev = ? and g.season_id < 20262027 and g.gp > 0
    group by p.player_id order by 3 desc""", (team,)).fetchall()
    print(team, rows)
# projectable goalie set size
print('projectable goalies:', c.execute("select count(distinct player_id) from goalie_seasons where season_id < 20262027 and gp > 0").fetchone()[0])
# pool mean save pct for prior 3 seasons
import collections
rows = c.execute("select shots_against, saves from goalie_seasons where season_id in (20232024,20242025,20252026) and shots_against > 0").fetchall()
sa, sv = sum(r[0] for r in rows), sum(r[1] for r in rows)
print('pool mean save pct:', sv/sa)
# Portillo regressed
raw = 28/29
k, n, mean = 1000, 29, sv/sa
reg = (raw*n + mean*k)/(n+k)
print('Portillo regressed:', reg, 'diff:', abs(reg-mean))
