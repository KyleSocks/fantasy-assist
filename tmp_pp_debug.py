import sqlite3
from src.projections.projections import _season_weights, _season_weighted, _compute_pos_means
import yaml, statistics

conn = sqlite3.connect('data/hockey.db')
cfg = yaml.safe_load(open('config/regression.yaml'))

pid = conn.execute("SELECT player_id FROM players WHERE full_name='Connor McDavid'").fetchone()[0]
seasons = conn.execute(
    "SELECT season_id, gp, pp_points, pp_toi_sec FROM skater_seasons"
    " WHERE player_id=? AND gp>0 ORDER BY season_id DESC", (pid,)
).fetchall()
gp_list = [s[1] for s in seasons]
weights = _season_weights(gp_list)
pp_pts = _season_weighted(seasons, 'pp_points')
pp_toi_rate = _season_weighted(seasons, 'pp_toi_sec') / max(gp_list[0], 1)
pp_pts_per_toi = pp_pts / max(pp_toi_rate * gp_list[0], 1)

# Replicate inline regression
k = cfg['regression_k']
n = 0.9 * len(seasons)
pos_means = _compute_pos_means(conn, 'C', 89)
pos_median = pos_means.get('pp_toi_sec', 30.0)
print(f"McDavid seasons: {seasons}")
print(f"  gp_list: {gp_list}")
print(f"  weights: {[f'{w:.3f}' for w in weights]}")
print(f"  weighted pp_pts={pp_pts:.4f}")
print(f"  pp_toi_rate={pp_toi_rate:.1f}")
print(f"  pp_pts_per_toi={pp_pts_per_toi:.6f}")
pp_pts_adj = min(pp_pts * 0.75 + pp_pts_per_toi * 30 * 0.25, pp_pts * 1.25)
print(f"  after pp formula: {pp_pts_adj:.4f}")
regressed = pp_pts_adj * k / (k + n) + pos_median * n / (k + n)
print(f"  after regression: {regressed:.4f}")
gp = gp_list[0] if gp_list[0] > 0 else 82
target = 89
gp_adjusted = regressed * target / gp if gp > 0 else regressed
print(f"  after GP adjust: {gp_adjusted:.4f}")
print(f"  ACTUAL 20242025: 38")
print(f"  ERROR: {abs(gp_adjusted - 38):.2f}")
print(f"  k={k}, n={n}")
