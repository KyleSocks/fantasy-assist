"""Model parameters the hand-computed tests were worked out with.

config/regression.yaml holds values fitted by the walk-forward backtest,
and they change whenever it is re-fitted; a test that checks the model's
arithmetic pins the parameters it was computed with instead of reading
the live file.
"""
from src.projections.common import _load_config

PINNED = {"gp_prior_share": 0.88, "gp_prior_weight": 3.0,
          "goalie_starts_reserve": 0.0,
          "gp_chronic_seasons": 0}        # history-based GP for everyone


def pinned_config(**extra) -> dict:
    return {**_load_config(), **PINNED, **extra}
