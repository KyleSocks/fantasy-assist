"""Scratch: derive exact values to hard-code into the offline hardening tests."""
from src.projections.projections import Projection
from src.valuation.league import LeagueConfig
from src.valuation.valuation import valuate, _volume_weight


def _small_pool():
    projs = [
        Projection(player_id=1, season_id=2025, gp=82, toi_per_gp=21,
                   goals=10, assists=5, shots=50, blocks=5, hits=5,
                   pp_points=3, wins=None, save_pct=None, position="C"),
        Projection(player_id=2, season_id=2025, gp=82, toi_per_gp=21,
                   goals=8, assists=8, shots=40, blocks=4, hits=4,
                   pp_points=2, wins=None, save_pct=None, position="C"),
        Projection(player_id=3, season_id=2025, gp=82, toi_per_gp=21,
                   goals=6, assists=6, shots=30, blocks=3, hits=3,
                   pp_points=1, wins=None, save_pct=None, position="L"),
        Projection(player_id=4, season_id=2025, gp=82, toi_per_gp=21,
                   goals=4, assists=4, shots=20, blocks=2, hits=2,
                   pp_points=1, wins=None, save_pct=None, position="R"),
        Projection(player_id=5, season_id=2025, gp=82, toi_per_gp=21,
                   goals=2, assists=2, shots=10, blocks=1, hits=1,
                   pp_points=0, wins=None, save_pct=None, position="C"),
        Projection(player_id=6, season_id=2025, gp=82, toi_per_gp=21,
                   goals=1, assists=1, shots=5, blocks=0, hits=0,
                   pp_points=0, wins=None, save_pct=None, position="L"),
        Projection(player_id=7, season_id=2025, gp=82, toi_per_gp=18,
                   goals=3, assists=3, shots=15, blocks=3, hits=3,
                   pp_points=1, wins=None, save_pct=None, position="D"),
        Projection(player_id=8, season_id=2025, gp=65, toi_per_gp=60,
                   goals=0, assists=2, shots=0, blocks=0, hits=0,
                   pp_points=0, wins=20, save_pct=0.92, position="G"),
        Projection(player_id=9, season_id=2025, gp=50, toi_per_gp=60,
                   goals=0, assists=1, shots=0, blocks=0, hits=0,
                   pp_points=0, wins=10, save_pct=0.90, position="G"),
    ]
    return projs


def _cfg(tc):
    return LeagueConfig(
        name=f"small_{tc}", team_count=tc, scoring_type="h2h_categories",
        roster_slots={"C": 1, "LW": 1, "RW": 1, "D": 1, "G": 1,
                      "UTIL": 0, "BENCH": 0},
        categories=["G", "A"],
    )


for tc in (1, 2):
    projs = _small_pool()
    vals = valuate(projs, _cfg(tc))
    ranked = sorted(vals, key=lambda v: v.vorp, reverse=True)
    print(f"=== team_count={tc} ===")
    for v in ranked:
        print(f"  pid={v.player_id} pos={v.position} vorp={v.vorp:.6f} raw={v.raw_value:.6f}")
    print()

# Backup goalie _volume_weight hand check
starter = Projection(player_id=1, season_id=2025, gp=55.0, toi_per_gp=60.0,
                     goals=0, assists=2, shots=0, blocks=0, hits=0,
                     pp_points=0, wins=20, save_pct=0.905, position="G")
backup = Projection(player_id=2, season_id=2025, gp=15.0, toi_per_gp=60.0,
                    goals=0, assists=1, shots=0, blocks=0, hits=0,
                    pp_points=0, wins=2, save_pct=0.930, position="G")
pool = [starter, backup]
mean_gp = (55.0 + 15.0) / 2.0
print("backup weight:", _volume_weight(backup, pool), "expected", 15.0 / 35.0)
print("starter weight:", _volume_weight(starter, pool), "expected", 55.0 / 35.0)
