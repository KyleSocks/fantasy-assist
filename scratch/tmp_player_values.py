"""Hand-compute expected values for the /api/player tests (McDavid, Tomkins)."""
import sqlite3

from src.projections.projections import (
    SKATER_CATEGORIES,
    WEIGHTS,
    _age_as_of,
    _age_factor,
    _compute_pos_means,
    _project_goalie,
    _project_skater,
    _season_weights,
    explain,
)

DB = "data/hockey.db"
SEASON = 20262027


def stdev_pop(values):
    n = len(values)
    if n < 2:
        return 0.0
    m = sum(values) / n
    return (sum((v - m) ** 2 for v in values) / n) ** 0.5


conn = sqlite3.connect(DB)

# ── McDavid (8478402, C, 3 seasons) ──────────────────────────────
pid = 8478402
row = conn.execute(
    "SELECT birth_date, position FROM players WHERE player_id = ?", (pid,)
).fetchone()
age = _age_as_of(row[0], SEASON)
af = _age_factor(age)
print("birth:", row[0], "pos:", row[1], "age:", age, "age_factor:", af)

prior = [20232024, 20242025, 20252026]
pm_c = _compute_pos_means(conn, prior, "C")
print("pos_mean C:", {k: round(v, 6) for k, v in pm_c.items()})

seasons = conn.execute(
    "SELECT season_id, gp, goals, assists, shots, blocks, hits, pp_points,"
    " pp_toi_sec, toi_sec, team_abbrev FROM skater_seasons"
    " WHERE player_id = ? AND season_id < ? AND gp > 0"
    " ORDER BY season_id DESC LIMIT 3", (pid, SEASON)).fetchall()
gp_list = [s[1] for s in seasons]
w = _season_weights(gp_list)
print("weights:", w)
career_gp = sum(s[1] for s in seasons)
print("career_gp:", career_gp)

team_games = 0
for s in seasons:
    g = conn.execute(
        "SELECT COUNT(*) FROM schedule WHERE season_id = ? AND"
        " (home_abbrev = ? OR away_abbrev = ?)",
        (s[0], s[10], s[10])).fetchone()[0]
    team_games += g
print("team_games:", team_games, "durability:", min(max(career_gp / team_games, 0.55), 1.0))

terms = explain(pid, SEASON, DB)
print("proj_gp:", terms["proj_gp"])
print("proj_toi_per_gp:", terms["proj_toi_per_gp"])
for cat, _, kkey in SKATER_CATEGORIES:
    vals = [
        r[0]
        for r in conn.execute(
            f"SELECT {cat} FROM skater_seasons"
            " WHERE player_id = ? AND season_id < ? AND gp > 0"
            " ORDER BY season_id", (pid, SEASON))
    ]
    sigma = stdev_pop(vals)
    shrink = abs(terms["raw_rates"][cat] - terms["regressed_rate"][cat]) * af * terms["proj_gp"]
    final = terms["final"][cat]
    half = sigma + shrink
    print(
        f"{cat}: actuals={vals} sigma={sigma:.6f} raw={terms['raw_rates'][cat]:.6f}"
        f" regressed={terms['regressed_rate'][cat]:.6f} final={final:.6f}"
        f" band=[{final - half:.6f}, {final + half:.6f}]"
    )

# ── Tomkins (8477035, G, 1 season) ─────────────────────────────────
pid = 8477035
row = conn.execute(
    "SELECT birth_date, position, full_name FROM players WHERE player_id = ?",
    (pid,)).fetchone()
print("\ntomkins pos:", row[1], "name:", row[2])
seasons = conn.execute(
    "SELECT season_id, gp, games_started, wins, saves, shots_against,"
    " goals_against, toi_sec, team_abbrev FROM goalie_seasons"
    " WHERE player_id = ? AND season_id < ? AND gp > 0"
    " ORDER BY season_id DESC LIMIT 3", (pid, SEASON)).fetchall()
print("seasons:", seasons)
s = seasons[0]
print("team:", s[8])
team_games = conn.execute(
    "SELECT COUNT(*) FROM schedule WHERE season_id = ? AND"
    " (home_abbrev = ? OR away_abbrev = ?)",
    (s[0], s[8], s[8])).fetchone()[0]
print("team_games:", team_games, "durability:", min(max(s[1] / team_games, 0.55), 1.0))

pm_g = _compute_pos_means(conn, prior, "G")
print("pos_mean G:", pm_g)
terms = explain(pid, SEASON, DB)
print("proj_gp:", terms["proj_gp"])
print("raw:", {k: round(v, 6) for k, v in terms["raw_rates"].items()})
print("regressed:", {k: round(v, 6) for k, v in terms["regressed_rate"].items()})
print("final:", {k: round(v, 6) for k, v in terms["final"].items()})
for cat, val in terms["final"].items():
    if cat == "save_pct":
        sigma = stdev_pop([s[4] / s[5]])
        shrink = abs(terms["raw_rates"]["save_pct"] - terms["regressed_rate"]["save_pct"])
    else:
        sigma = 0.0
        shrink = 0.0
    half = sigma + shrink
    print(f"{cat}: band=[{val - half:.6f}, {val + half:.6f}] (sigma={sigma:.6f} shrink={shrink:.6f})")

# ── schedule for projected season ─────────────────────────────────
for sid in (SEASON,):
    total = conn.execute(
        "SELECT COUNT(*) FROM schedule WHERE season_id = ?", (sid,)).fetchone()[0]
    heavy = conn.execute(
        "SELECT strftime('%Y-%W', game_date), COUNT(*) c FROM schedule"
        " WHERE season_id = ? GROUP BY 1 ORDER BY c DESC LIMIT 1", (sid,)).fetchone()
    light = conn.execute(
        "SELECT strftime('%Y-%W', game_date), COUNT(*) c FROM schedule"
        " WHERE season_id = ? GROUP BY 1 ORDER BY c ASC LIMIT 1", (sid,)).fetchone()
    print(f"\nschedule {sid}: total={total} heaviest={heavy} lightest={light}")

# ── board row + league default ────────────────────────────────────
from src.valuation.league import DEFAULT_LEAGUE, load_league
from pathlib import Path

print("DEFAULT_LEAGUE:", DEFAULT_LEAGUE)

# ── 'no data' candidate: skater rows with toi_sec == 0 in all seasons ──
rows = conn.execute(
    "SELECT player_id, COUNT(*) FROM skater_seasons"
    " WHERE gp > 0 GROUP BY player_id HAVING SUM(CASE WHEN toi_sec > 0 THEN 1 ELSE 0 END) = 0"
    " LIMIT 5").fetchall()
print("toi_sec==0 candidates:", rows)
