"""Model parameters the hand-computed tests were worked out with.

config/regression.yaml holds values fitted by the walk-forward backtest,
and they change whenever it is re-fitted; a test that checks the model's
arithmetic pins the parameters it was computed with instead of reading
the live file.
"""
from src.projections.common import _load_config

_D_CURVE = {20: 0.78, 21: 0.82, 22: 0.86, 23: 0.90, 24: 0.94, 25: 0.97, 26: 0.99,
            27: 1.00, 28: 1.00, 29: 0.99, 30: 0.97, 31: 0.95, 32: 0.92, 33: 0.89,
            34: 0.85, 35: 0.81, 36: 0.77, 37: 0.72, 38: 0.67, 39: 0.62, 40: 0.57}

PINNED = {
    # games played: history-based for everyone, original prior
    "gp_prior_share": 0.88, "gp_prior_weight": 3.0, "gp_chronic_seasons": 0,
    # skater rates as first hand-computed
    "goals": 200, "assists": 300, "shots": 150, "blocks": 250, "hits": 250,
    "pp_points": 120, "skater_season_weights": None, "era_adjust": False,
    "rookie_prior_weight": 0.5, "rookie_ramp_gp": 200,
    "team_change_pp_blend": 0.25, "team_change_toi_factor": 1.0,
    # goalies: weighted start count, original team context
    "goalie_start_model": None, "goalie_starts_reserve": 0.0,
    "goalie_win_rate_k": 40, "goalie_sa_ratio_k": 40, "shutout_scale": 1.0,
    "shutout_k_starts": 60, "team_sa_regress": 0.6, "team_hist_weight": 0.5,
    "max_goalie_starts": 68,
}


def pinned_config(**extra) -> dict:
    cfg = {**_load_config(), **PINNED, **extra}
    cfg["level_curves"] = {**cfg["level_curves"], "defensemen": dict(_D_CURVE)}
    return cfg
