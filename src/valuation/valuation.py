"""Z-scores, replacement level, VORP, and auction values."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.projections.projections import Projection
from src.valuation.league import LeagueConfig, category_vector, points_value


@dataclass
class PlayerValue:
    player_id: int
    position: str
    raw_value: float
    vorp: float
    cat_zscores: dict
    replacement_used: float


def _pos_group(pos: str) -> str:
    if pos == "G":
        return "G"
    if pos == "D":
        return "D"
    return "F"


def _starting_slots(cfg: LeagueConfig) -> dict[str, int]:
    rs = cfg.roster_slots
    f = rs.get("C", 0) + rs.get("LW", 0) + rs.get("RW", 0)
    d = rs.get("D", 0)
    g = rs.get("G", 0)
    ub = rs.get("UTIL", 0) + rs.get("BENCH", 0)
    return {"F": f, "D": d, "G": g, "UB": ub}


def _rate_cats(cfg: LeagueConfig) -> set[str]:
    if cfg.categories is None:
        return set()
    return {c for c in cfg.categories if c == "SV%" or "pct" in c.lower()}


def _raw_value(p: Projection, cfg: LeagueConfig) -> float:
    if cfg.scoring_type == "points":
        return points_value(p, cfg)
    cv = category_vector(p, cfg)
    return sum(cv.values())


def _determine_pools(projs: list[Projection], cfg: LeagueConfig) -> dict[str, int]:
    """Two-pass pool determination with proportional UTIL/BENCH allocation."""
    slots = _starting_slots(cfg)
    tc = cfg.team_count
    f_base = tc * slots["F"]
    d_base = tc * slots["D"]
    g_base = tc * slots["G"]
    ub_total = tc * slots["UB"]

    # Pass 1: rank non-G players by raw value, take top ub_total
    non_g = [p for p in projs if _pos_group(p.position) != "G"]
    non_g_ranked = sorted(non_g, key=lambda p: _raw_value(p, cfg), reverse=True)
    top_ub = non_g_ranked[:ub_total]
    f_cnt = sum(1 for p in top_ub if _pos_group(p.position) == "F")
    d_cnt = sum(1 for p in top_ub if _pos_group(p.position) == "D")
    total = f_cnt + d_cnt
    if total == 0:
        f_alloc = ub_total // 2
        d_alloc = ub_total - f_alloc
    else:
        f_alloc = round(ub_total * f_cnt / total)
        d_alloc = ub_total - f_alloc

    return {"F": f_base + f_alloc, "D": d_base + d_alloc, "G": g_base}


def _zscore_category(pool: list[Projection], all_projs: list[Projection],
                    cat: str, cfg: LeagueConfig) -> dict[int, float]:
    """Z-score a category: mean/std from pool, applied to all players."""
    pool_vals = np.array([category_vector(p, cfg)[cat] for p in pool], dtype=float)
    mean = pool_vals.mean()
    std = pool_vals.std()
    if std < 1e-12:
        return {p.player_id: 0.0 for p in all_projs}
    result: dict[int, float] = {}
    for p in all_projs:
        v = category_vector(p, cfg)[cat]
        result[p.player_id] = (v - mean) / std
    return result


def _volume_weight(p: Projection, pool: list[Projection]) -> float:
    """Volume factor for rate categories: gp / pool mean gp."""
    pool_gp = np.array([q.gp for q in pool], dtype=float)
    mean_gp = pool_gp.mean()
    if mean_gp < 1e-12:
        return 1.0
    return p.gp / mean_gp


def _compute_values(projs: list[Projection], cfg: LeagueConfig,
                    pools: dict[str, int]) -> list[PlayerValue]:
    """Compute values for all players. Two-pass for category leagues."""
    rate_cats = _rate_cats(cfg)
    pid_map = {p.player_id: p for p in projs}

    # Build position groups
    by_pos: dict[str, list[Projection]] = {"F": [], "D": [], "G": []}
    for p in projs:
        by_pos[_pos_group(p.position)].append(p)

    # Initial pool: top N by raw value per position
    pool: dict[str, list[Projection]] = {}
    for pos, players in by_pos.items():
        n = pools[pos]
        pool[pos] = sorted(players, key=lambda p: _raw_value(p, cfg), reverse=True)[:n]
    all_pool = pool["F"] + pool["D"] + pool["G"]

    if cfg.scoring_type == "points":
        values = {p.player_id: points_value(p, cfg) for p in projs}
        cat_z: dict[int, dict[str, float]] = {p.player_id: {} for p in projs}
    else:
        # Pass 1: z-score using pool mean/std, applied to all players
        pass1_z: dict[int, float] = {}
        pass1_cat_z: dict[int, dict[str, float]] = {}
        for cat in cfg.categories or []:
            zmap = _zscore_category(all_pool, projs, cat, cfg)
            for pid, z in zmap.items():
                if cat in rate_cats:
                    z *= _volume_weight(pid_map[pid], all_pool)
                pass1_cat_z.setdefault(pid, {})[cat] = z
                pass1_z[pid] = pass1_z.get(pid, 0.0) + z

        # Recompute pool using pass 1 values
        pool2: dict[str, list[Projection]] = {}
        for pos, players in by_pos.items():
            n = pools[pos]
            pool2[pos] = sorted(players, key=lambda p: pass1_z.get(p.player_id, 0.0), reverse=True)[:n]
        all_pool2 = pool2["F"] + pool2["D"] + pool2["G"]

        # Pass 2: z-score using recomputed pool mean/std
        pass2_z: dict[int, float] = {}
        pass2_cat_z: dict[int, dict[str, float]] = {}
        for cat in cfg.categories or []:
            zmap = _zscore_category(all_pool2, projs, cat, cfg)
            for pid, z in zmap.items():
                if cat in rate_cats:
                    z *= _volume_weight(pid_map[pid], all_pool2)
                pass2_cat_z.setdefault(pid, {})[cat] = z
                pass2_z[pid] = pass2_z.get(pid, 0.0) + z

        values = pass2_z
        cat_z = pass2_cat_z

    result: list[PlayerValue] = []
    for p in projs:
        rv = values.get(p.player_id, 0.0)
        result.append(PlayerValue(
            player_id=p.player_id,
            position=p.position,
            raw_value=rv,
            vorp=0.0,
            cat_zscores=cat_z.get(p.player_id, {}),
            replacement_used=0.0,
        ))
    return result


def replacement_levels(projs: list[Projection], cfg: LeagueConfig) -> dict[str, float]:
    """Replacement level per position group = value of (pool_size+1)th ranked player."""
    pools = _determine_pools(projs, cfg)
    # Need values to rank
    vals = _compute_values(projs, cfg, pools)
    by_pos: dict[str, list[PlayerValue]] = {"F": [], "D": [], "G": []}
    for v in vals:
        by_pos[_pos_group(v.position)].append(v)

    levels: dict[str, float] = {}
    for pos, players in by_pos.items():
        ranked = sorted(players, key=lambda v: v.raw_value, reverse=True)
        idx = pools[pos]  # (pool_size + 1)th player is at index pool_size
        if idx < len(ranked):
            levels[pos] = ranked[idx].raw_value
        else:
            levels[pos] = 0.0
    return levels


def valuate(projs: list[Projection], cfg: LeagueConfig) -> list[PlayerValue]:
    """Compute VORP for all players."""
    pools = _determine_pools(projs, cfg)
    vals = _compute_values(projs, cfg, pools)

    # Compute replacement levels
    by_pos: dict[str, list[PlayerValue]] = {"F": [], "D": [], "G": []}
    for v in vals:
        by_pos[_pos_group(v.position)].append(v)

    levels: dict[str, float] = {}
    for pos, players in by_pos.items():
        ranked = sorted(players, key=lambda v: v.raw_value, reverse=True)
        idx = pools[pos]
        if idx < len(ranked):
            levels[pos] = ranked[idx].raw_value
        else:
            levels[pos] = 0.0

    # Assign VORP
    for v in vals:
        pos = _pos_group(v.position)
        v.vorp = v.raw_value - levels.get(pos, 0.0)
        v.replacement_used = levels.get(pos, 0.0)

    return vals


def auction_values(vals: list[PlayerValue], cfg: LeagueConfig, budget: int = 200) -> dict[int, float]:
    """VORP-proportional auction values. Total spend == team_count * budget."""
    total_budget = cfg.team_count * budget
    # Only players with positive VORP participate
    positive = [v for v in vals if v.vorp > 0]
    total_vorp = sum(v.vorp for v in positive)
    if total_vorp < 1e-12:
        return {v.player_id: 0.0 for v in vals}
    result: dict[int, float] = {}
    for v in vals:
        if v.vorp > 0:
            result[v.player_id] = (v.vorp / total_vorp) * total_budget
        else:
            result[v.player_id] = 0.0
    return result