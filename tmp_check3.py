import sqlite3
conn = sqlite3.connect("data/hockey.db")
cur = conn.cursor()

# Check career GP and team games for 8480382
cur.execute("SELECT SUM(gp) FROM goalie_seasons WHERE player_id=8480382")
career_gp = cur.fetchone()[0]
print(f"Career GP for 8480382: {career_gp}")

cur.execute("SELECT season_id, team_abbrev, gp FROM goalie_seasons WHERE player_id=8480382")
for r in cur.fetchall():
    print(f"  season={r[0]}, team={r[1]}, gp={r[2]}")

# Check team games
cur.execute("SELECT season_id, team_abbrev FROM goalie_seasons WHERE player_id=8480382")
all_g = cur.fetchall()
team_games = 0
for sid, team in all_g:
    cur.execute("SELECT COUNT(*) FROM schedule WHERE season_id = ? AND (home_abbrev = ? OR away_abbrev = ?)", (sid, team, team))
    g = cur.fetchone()[0]
    print(f"  season={sid}, team={team}, schedule_games={g}")
    team_games += g
print(f"Total team_games: {team_games}")
print(f"Durability: {career_gp / team_games if team_games > 0 else 'N/A'}")

# Also check a known goalie like Hellebuyck (8476945)
cur.execute("SELECT SUM(gp) FROM goalie_seasons WHERE player_id=8476945")
career_gp2 = cur.fetchone()[0]
print(f"\nCareer GP for Hellebuyck (8476945): {career_gp2}")
cur.execute("SELECT season_id, team_abbrev, gp FROM goalie_seasons WHERE player_id=8476945")
for r in cur.fetchall():
    print(f"  season={r[0]}, team={r[1]}, gp={r[2]}")
cur.execute("SELECT season_id, team_abbrev FROM goalie_seasons WHERE player_id=8476945")
all_g2 = cur.fetchall()
team_games2 = 0
for sid, team in all_g2:
    cur.execute("SELECT COUNT(*) FROM schedule WHERE season_id = ? AND (home_abbrev = ? OR away_abbrev = ?)", (sid, team, team))
    g = cur.fetchone()[0]
    team_games2 += g
print(f"Total team_games: {team_games2}")
print(f"Durability: {career_gp2 / team_games2 if team_games2 > 0 else 'N/A'}")
conn.close()
