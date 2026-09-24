"""Hand-compute expected values for GET /api/player/{id}.

Run:  uv run python scratch/tmp_detail.py
Covers McDavid (8478402, skater), Tomkins (8477035, goalie), Belzile
(8475968, no-data), plus schedule summaries and the board row.
"""
from __future__ import annotations

import sqlite3

from src.projections.projections import explain
from src.projections import projections as P
from src.valuation.league import DEFAULT_LEAGUE, load_league
from src.valuation.valuation import auction_values, valuate
from src.api.routes import build_board

DB = "data/hockey.db"
SEASON = 20262027
conn = sqlite3.connect(DB)


def pop_std(vals):
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    return (sum((v - m) ** 2 for v in vals) / n) ** 0.5


def skater_actuals(pid, cat):
    rows = conn.execute(
        f"SELECT {cat} FROM skater_seasons WHERE player_id=? AND season_id<? "
        "AND gp>0 ORDER BY season_id DESC LIMIT 3", (pid, SEASON)).fetchall()
    return [r[0] for r in reversed(rows)]  # oldest -> newest


def goalie_actuals(pid, cat):
    rows = conn.execute(
        "SELECT season_id, gp, games_started, wins, saves, shots_against, "
        "goals_against, shutouts FROM goalie_seasons "
        "WHERE player_id=? AND season_id<? AND gp>0 "
        "ORDER BY season_id DESC LIMIT 3", (pid, SEASON)).fetchall()
    out = []
    for r in reversed(rows):
        sa, sv = r[5], r[4]
        out.append(sv / sa if cat == "save_pct" and sa > 0 else (r[{"wins": 3,
                     "games_started": 2, "goals_against": 6, "saves": 4,
                     "shutouts": 7}[cat]] or 0))
    return out


GOALIE_CATS = ["wins", "save_pct", "shutouts", "games_started",
               "goals_against", "saves"]
SKATER_CATS = ["goals", "assists", "shots", "blocks", "hits", "pp_points"]

# ---- McDavid ----
pid = 8478402
t = explain(pid, SEASON, DB)
print("MCDAVID  terms keys:", sorted(t.keys()))
print("  age_factor", t["age_factor"], "proj_gp", t["proj_gp"],
      "proj_toi_per_gp", t["proj_toi_per_gp"])
for cat in SKATER_CATS:
    act = skater_actuals(pid, cat)
    sigma = pop_std(act)
    shrink = abs(t["raw_rates"][cat] - t["regressed_rate"][cat]) * t["age_factor"] * t["proj_gp"]
    final = t["final"][cat]
    hw = sigma + shrink
    print(f"  {cat}: act={act} sig={sigma:.6f} raw={t['raw_rates'][cat]:.6f} "
          f"reg={t['regressed_rate'][cat]:.6f} fin={final:.6f} "
          f"shrink={shrink:.6f} band=[{final-hw:.6f},{final+hw:.6f}]")

# ---- Tomkins ----
pid = 8477035
tg = explain(pid, SEASON, DB)
print("\nTOMKINS  terms keys:", sorted(tg.keys()))
print("  age_factor", tg["age_factor"], "proj_gp", tg["proj_gp"],
      "proj_toi_per_gp", tg["proj_toi_per_gp"])
print("  raw_rates", tg["raw_rates"])
print("  regressed_rate", tg["regressed_rate"])
print("  final", {k: round(v, 6) for k, v in tg["final"].items()})
for cat in GOALIE_CATS:
    act = goalie_actuals(pid, cat)
    raw = tg["raw_rates"].get(cat)
    reg = tg["regressed_rate"].get(cat)
    sigma = pop_std(act)
    shrink = abs(raw - reg) if (raw is not None and reg is not None) else 0.0
    final = tg["final"][cat]
    hw = sigma + shrink
    print(f"  {cat}: act={act} raw={raw} reg={reg} sig={sigma:.6f} "
          f"shrink={shrink:.6f} fin={final:.6f} band=[{final-hw:.6f},{final+hw:.6f}]")

# ---- Belzile (no data) ----
print("\nBELZILE  explain ->", explain(8475968, SEASON, DB))
print("  players row:", conn.execute(
    "SELECT player_id, full_name, position, current_team_abbrev FROM players "
    "WHERE player_id=8475968").fetchone())

# ---- schedule summaries ----
def schedule_summary(team):
    total = conn.execute(
        "SELECT COUNT(*) FROM schedule WHERE season_id=? AND "
        "(home_abbrev=? OR away_abbrev=?)", (SEASON, team, team)).fetchone()[0]
    monthly = conn.execute(
        "SELECT substr(game_date,1,7) m, COUNT(*) c FROM schedule WHERE "
        "season_id=? AND (home_abbrev=? OR away_abbrev=?) GROUP BY 1 ORDER BY 1",
        (SEASON, team, team)).fetchall()
    heavy = max(monthly, key=lambda r: r[1]) if monthly else (None, 0)
    light = min(monthly, key=lambda r: r[1]) if monthly else (None, 0)
    return total, monthly, heavy, light


print("\nSCHEDULE sample game_date:", conn.execute(
    "SELECT game_date FROM schedule WHERE season_id=20262027 LIMIT 1").fetchone())
for team in ("EDM", "TBL"):
    total, monthly, heavy, light = schedule_summary(team)
    print(f"  {team}: total={total} heavy={heavy} light={light}")
    print(f"     monthly={monthly}")

# ---- board row (default league) ----
board = build_board("kyle_12_points")
for pid in (8478402, 8477035):
    row = next(p for p in board["players"] if p["id"] == pid)
    print(f"\nBOARD {pid}:", row)

# ---- breakdown counts ----
print("\nBREAKDOWN counts (expected skater 40 / goalie 17)")
skater_rows = 4 + 6 * 6  # 4 global + 6 cats x 6 rows
goalie_rows = 4 + 3 + 5 * 2  # 4 global + save_pct 3 + 5 others x 2
print("  skater:", skater_rows, "goalie:", goalie_rows)
print("\nDEFAULT_LEAGUE:", DEFAULT_LEAGUE)

conn.close()
