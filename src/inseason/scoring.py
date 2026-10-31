"""Score an expected game line under a league config.

Points leagues: fantasy points (points_value), so the zero is "no production".

Category / roto leagues: per-game z-units against the league's rosterable
pool (same pool sizes as the draft valuation). Counting categories are
scored UNCENTERED (x / sd) so value is additive across games and a game with
no production is worth 0; rate categories (SV%) are centered ((x - mu) / sd)
and volume-weighted by saves / pool-mean saves, as in src.valuation.

Replacement level per group (F / D / G) = per-game value of the
(pool_size + 1)th player in that group.
"""
from __future__ import annotations

import numpy as np

from src.projections.projections import Projection
from src.valuation.league import LeagueConfig, category_vector, points_value
from src.valuation.valuation import (
    _cats_for_position,
    _determine_pools,
    _raw_value,
    _rate_cats,
    value_group,
)


def to_projection(pid: int, position: str, stats: dict[str, float]) -> Projection:
    g = stats.get
    return Projection(
        player_id=pid, season_id=0, gp=1.0, toi_per_gp=0.0,
        goals=g("goals", 0.0), assists=g("assists", 0.0),
        shots=g("shots", 0.0), blocks=g("blocks", 0.0), hits=g("hits", 0.0),
        pp_points=g("pp_points", 0.0),
        wins=g("wins", 0.0), save_pct=g("save_pct", 0.0),
        shutouts=g("shutouts", 0.0), games_started=g("games_started", 0.0),
        goals_against=g("goals_against", 0.0), saves=g("saves", 0.0),
        position=position)


class GameScorer:
    def __init__(self, cfg: LeagueConfig,
                 neutral: dict[int, tuple[str, dict[str, float]]]):
        """``neutral``: pid -> (position, opponent-neutral per-team-game line)."""
        self.cfg = cfg
        self.points = cfg.scoring_type == "points"
        self.rate_cats = set() if self.points else _rate_cats(cfg)
        projs = {pid: to_projection(pid, pos, st)
                 for pid, (pos, st) in neutral.items()}
        self.pool_sizes = _determine_pools(list(projs.values()), cfg)
        by_group: dict[str, list[Projection]] = {"F": [], "D": [], "G": []}
        for p in projs.values():
            by_group[value_group(p.position)].append(p)

        self.mu: dict[str, dict[str, float]] = {}
        self.sd: dict[str, dict[str, float]] = {}
        self.mean_saves: float | None = None
        if not self.points:
            for grp, ps in by_group.items():
                pool = sorted(ps, key=lambda p: _raw_value(p, cfg),
                              reverse=True)[:self.pool_sizes[grp]]
                cats = _cats_for_position(cfg, "G" if grp == "G" else "C")
                self.mu[grp], self.sd[grp] = {}, {}
                for c in cats:
                    vals = np.array([category_vector(p, cfg)[c] for p in pool],
                                    dtype=float)
                    self.mu[grp][c] = float(vals.mean()) if vals.size else 0.0
                    self.sd[grp][c] = float(vals.std()) if vals.size else 0.0
                if grp == "G" and pool:
                    ms = float(np.mean([p.saves for p in pool]))
                    self.mean_saves = ms if ms > 1e-12 else None

        self.per_game = {pid: self.value(p) for pid, p in projs.items()}
        self.replacement: dict[str, float] = {}
        self.group_rank: dict[int, int] = {}
        for grp, ps in by_group.items():
            ranked = sorted(ps, key=lambda p: (-self.per_game[p.player_id],
                                               p.player_id))
            for i, p in enumerate(ranked, start=1):
                self.group_rank[p.player_id] = i
            n = self.pool_sizes[grp]
            self.replacement[grp] = (self.per_game[ranked[n].player_id]
                                     if n < len(ranked) else 0.0)

    def value(self, p: Projection) -> float:
        if self.points:
            return float(points_value(p, self.cfg))
        grp = value_group(p.position)
        vec = category_vector(p, self.cfg)
        total = 0.0
        for c, sd in self.sd.get(grp, {}).items():
            if sd < 1e-12:
                continue
            if c in self.rate_cats:
                w = (p.saves / self.mean_saves) if self.mean_saves else 1.0
                total += (vec[c] - self.mu[grp][c]) / sd * w
            else:
                total += vec[c] / sd
        return total

    def value_stats(self, pid: int, position: str, stats: dict[str, float]) -> float:
        return self.value(to_projection(pid, position, stats))
