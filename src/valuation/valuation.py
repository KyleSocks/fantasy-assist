"""Z-scores, replacement level, VORP, and auction values."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.projections.projections import Projection, _load_config
from src.valuation.league import LeagueConfig, category_vector, points_value


# Categories that belong to each position pool.
_GOALIE_CAT_NAMES = {"W", "SO", "SV%", "GS", "GA", "SV"}
_SKATER_CAT_NAMES = {"G", "A", "PPP", "SHP", "SOG", "HIT", "BLK", "HIT+BLK"}


def _cats_for_position(cfg: LeagueConfig, pos: str) -> list[str]:
    """Return the list of category keys that apply to a position.

    Goalies are z-scored only on goalie categories; skaters on skater
    categories.  The two sets are disjoint and each player's cat_zscores
    contains only keys from its own pool.
    """
    if cfg.categories is None:
        return []
    if pos == "G":
        return [c for c in cfg.categories if c in _GOALIE_CAT_NAMES]
    return [c for c in cfg.categories if c in _SKATER_CAT_NAMES]


@dataclass
class PlayerValue:
    player_id: int
    position: str
    raw_value: float
    vorp: float
    cat_zscores: dict
    replacement_used: float
    # ── Windowed (regular vs playoff week) valuation ──
    # Zero for the season-long path; populated by valuate_windowed().
    gp_regular: float = 0.0
    gp_playoff: float = 0.0
    vorp_regular: float = 0.0
    vorp_playoff: float = 0.0
    total_value: float = 0.0
    # Goalies projected below min_startable_starts are excluded from the
    # draftable pool and ranked below every startable goalie.
    startable: bool = True


def value_group(pos: str) -> str:
    """Valuation pool for a position: G, D, or F (C/L/R and anything else).

    Not the same as src.projections' _pos_group, which splits C and W.
    """
    if pos == "G":
        return "G"
    if pos == "D":
        return "D"
    return "F"


# Old private name, still imported by src.draft.recommend and tools/diag.py.
_pos_group = value_group


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


def _min_startable_starts() -> float:
    """Configurable minimum projected starts to be startable (default 15)."""
    return float(_load_config().get("min_startable_starts", 15))


def _season_starts(p: Projection) -> float:
    """Season projected starts: the allocated team starts, if present.

    Windowed projections share the season terms dict, so windowed copies
    keep the season starts; standalone Projections fall back to their
    own starts/gp fields.
    """
    starts = (p.terms or {}).get("final", {}).get("games_started")
    if starts is None:
        starts = p.games_started
    if starts <= 0:
        starts = p.gp
    return float(starts)


def _is_startable(p: Projection, min_starts: float) -> bool:
    """Goalies below min_starts are excluded; skaters are always startable."""
    if _pos_group(p.position) != "G":
        return True
    return _season_starts(p) >= min_starts


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


def _pool_mean_saves(pool: list[Projection]) -> float | None:
    """Mean projected saves of the goalies in ``pool`` (None if not usable)."""
    g_saves = np.array(
        [q.saves for q in pool if _pos_group(q.position) == "G"],
        dtype=float,
    )
    if g_saves.size == 0:
        return None
    mean_saves = float(g_saves.mean())
    if mean_saves < 1e-12:
        return None
    return mean_saves


def _volume_weight(p: Projection, pool: list[Projection],
                   mean_saves: float | None = None) -> float:
    """Volume factor for rate categories: saves / goalie pool mean saves.

    Save percentage is only worth what the shot volume behind it is, so
    rate z-scores scale with projected saves relative to the mean saves
    of the goalies in the draftable pool. Skaters carry no saves and
    contribute 0 on rate categories. Pass ``mean_saves`` (from
    _pool_mean_saves) to avoid recomputing it per player.
    """
    if mean_saves is None:
        mean_saves = _pool_mean_saves(pool)
    if mean_saves is None:
        return 1.0
    return float(p.saves) / mean_saves


def _compute_pool_stats(
    pool: list[Projection],
    cfg: LeagueConfig,
    group: list[Projection] | None = None,
) -> tuple[dict[int, float], dict[int, dict[str, float]]]:
    """Compute pass z-scores for a single position pool.

    Mean/std come from ``pool`` (the rosterable players) and are applied to
    every player in ``group`` (the whole position group; default: the pool),
    so players outside the pool get their genuine -- usually negative --
    z-scores rather than 0. That is what makes the (pool_size+1)th player a
    real replacement level.

    Returns (total_z, cat_z) where total_z maps player_id -> sum of z-scores
    across the pool's categories, and cat_z maps player_id -> {cat: z}.
    Only the categories relevant to this pool are scored.
    """
    group = pool if group is None else group
    pos = _pos_group(pool[0].position) if pool else "F"
    cats = _cats_for_position(cfg, pos)
    rate_cats = _rate_cats(cfg)
    pid_map = {p.player_id: p for p in group}

    mean_saves = _pool_mean_saves(pool)
    pass1_z: dict[int, float] = {}
    pass1_cat_z: dict[int, dict[str, float]] = {}
    for cat in cats:
        zmap = _zscore_category(pool, group, cat, cfg)
        for pid, z in zmap.items():
            if cat in rate_cats:
                z *= _volume_weight(pid_map[pid], pool, mean_saves)
            pass1_cat_z.setdefault(pid, {})[cat] = z
            pass1_z[pid] = pass1_z.get(pid, 0.0) + z
    return pass1_z, pass1_cat_z


def _select_pools(by_pos: dict[str, list[Projection]], pools: dict[str, int],
                  min_starts: float, key) -> dict[str, list[Projection]]:
    """Top ``pools[pos]`` players per group by ``key``; goalies must be startable."""
    out: dict[str, list[Projection]] = {}
    for pos, players in by_pos.items():
        if pos == "G":
            players = [p for p in players if _is_startable(p, min_starts)]
        out[pos] = sorted(players, key=key, reverse=True)[:pools[pos]]
    return out


def _zscore_pools(
    pool: dict[str, list[Projection]], cfg: LeagueConfig,
    by_pos: dict[str, list[Projection]],
) -> tuple[dict[int, float], dict[int, dict[str, float]]]:
    """Run _compute_pool_stats on every non-empty pool (applied to its whole
    position group in ``by_pos``) and merge the results."""
    total_z: dict[int, float] = {}
    cat_z: dict[int, dict[str, float]] = {}
    for pos, pl in pool.items():
        if not pl:
            continue
        pz, pcz = _compute_pool_stats(pl, cfg, by_pos[pos])
        for pid, z in pz.items():
            total_z[pid] = z
        for pid, cz in pcz.items():
            cat_z.setdefault(pid, {}).update(cz)
    return total_z, cat_z


def _compute_values(projs: list[Projection], cfg: LeagueConfig,
                    pools: dict[str, int]) -> list[PlayerValue]:
    """Compute values for all players. Two-pass for category leagues."""
    min_starts = _min_startable_starts()

    # Build position groups
    by_pos: dict[str, list[Projection]] = {"F": [], "D": [], "G": []}
    for p in projs:
        by_pos[_pos_group(p.position)].append(p)

    # Initial pool: top N by raw value per position
    pool = _select_pools(by_pos, pools, min_starts,
                         key=lambda p: _raw_value(p, cfg))

    if cfg.scoring_type == "points":
        values = {p.player_id: points_value(p, cfg) for p in projs}
        cat_z: dict[int, dict[str, float]] = {p.player_id: {} for p in projs}
    else:
        # Pass 1: z-score using pool mean/std, applied to all players
        pass1_z, _ = _zscore_pools(pool, cfg, by_pos)
        # Pass 2: recompute the pool from pass-1 values, then z-score again
        pool2 = _select_pools(by_pos, pools, min_starts,
                              key=lambda p: pass1_z.get(p.player_id, 0.0))
        values, cat_z = _zscore_pools(pool2, cfg, by_pos)

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
            startable=_is_startable(p, min_starts),
        ))
    return result


def _goalie_replacement_level(
    g_vals: list[PlayerValue],
    cfg: LeagueConfig,
) -> tuple[float, PlayerValue | None]:
    """Goalie replacement level = value of the (tc*slots+1)th ranked goalie."""
    g_slots = cfg.roster_slots.get("G", 0)
    idx = cfg.team_count * g_slots + 1
    startable = [v for v in g_vals if v.startable]
    startable.sort(key=lambda v: v.raw_value, reverse=True)
    if idx < len(startable):
        return startable[idx].raw_value, startable[idx]
    return 0.0, None


def _replacement_by_group(vals: list[PlayerValue], pools: dict[str, int],
                          cfg: LeagueConfig) -> dict[str, float]:
    """Replacement level per group = value of the (pool_size+1)th ranked player.

    Goalies use the (team_count * goalie_slots + 1)th startable goalie.
    """
    by_pos: dict[str, list[PlayerValue]] = {"F": [], "D": [], "G": []}
    for v in vals:
        by_pos[_pos_group(v.position)].append(v)

    levels: dict[str, float] = {}
    for pos, players in by_pos.items():
        if pos == "G":
            # Goalie replacement = (team_count * goalie_slots + 1)th ranked goalie
            lvl, _ = _goalie_replacement_level(players, cfg)
            levels[pos] = lvl
        else:
            players = [v for v in players if v.startable]
            ranked = sorted(players, key=lambda v: v.raw_value, reverse=True)
            idx = pools[pos]
            if idx < len(ranked):
                levels[pos] = ranked[idx].raw_value
            else:
                levels[pos] = 0.0
    return levels


def replacement_levels(projs: list[Projection], cfg: LeagueConfig) -> dict[str, float]:
    """Replacement level per position group = value of (pool_size+1)th ranked player."""
    pools = _determine_pools(projs, cfg)
    vals = _compute_values(projs, cfg, pools)
    return _replacement_by_group(vals, pools, cfg)


def valuate(projs: list[Projection], cfg: LeagueConfig) -> list[PlayerValue]:
    """Compute VORP for all players."""
    pools = _determine_pools(projs, cfg)
    vals = _compute_values(projs, cfg, pools)
    levels = _replacement_by_group(vals, pools, cfg)

    # Assign VORP
    for v in vals:
        pos = _pos_group(v.position)
        v.vorp = v.raw_value - levels.get(pos, 0.0)
        v.replacement_used = levels.get(pos, 0.0)

    # Non-startable goalies sort below every startable goalie (they stay
    # on the board; they are only excluded from the draftable pool).
    g_vals = [v for v in vals if _pos_group(v.position) == "G"]
    startable_g = [v for v in g_vals if v.startable]
    if startable_g:
        floor = min(v.vorp for v in startable_g) - 1e-6
        for v in g_vals:
            if not v.startable:
                v.vorp = floor
                v.replacement_used = v.raw_value - v.vorp

    return vals


def valuate_windowed(
    regular_projs: list[Projection],
    playoff_projs: list[Projection],
    cfg: LeagueConfig,
) -> list[PlayerValue]:
    """Two-pass windowed valuation combined into ``total_value``.

    Runs ``valuate()`` once per window, each pass deriving its OWN
    replacement levels from that window's totals (the two windows never
    share pool stats). Then combines per player:

        total_value = (1 - w) * vorp_regular + w * vorp_playoff

    where ``w = cfg.playoff_weight``. Returns the regular-pass
    ``PlayerValue`` list extended with ``gp_regular``, ``gp_playoff``,
    ``vorp_regular``, ``vorp_playoff`` and ``total_value``.
    """
    w = cfg.playoff_weight
    reg_vals = valuate(list(regular_projs), cfg)
    po_vals = valuate(list(playoff_projs), cfg)
    po_by_id = {v.player_id: v for v in po_vals}
    reg_gp = {p.player_id: p.gp for p in regular_projs}
    po_gp = {p.player_id: p.gp for p in playoff_projs}
    for v in reg_vals:
        po = po_by_id[v.player_id]
        v.gp_regular = reg_gp[v.player_id]
        v.gp_playoff = po_gp[v.player_id]
        v.vorp_regular = v.vorp
        v.vorp_playoff = po.vorp
        v.total_value = (1.0 - w) * v.vorp + w * po.vorp
    return reg_vals


def auction_values(
    vals: list[PlayerValue], cfg: LeagueConfig, budget: int = 200,
    key: str = "vorp",
) -> dict[int, float]:
    """Value-proportional auction dollars. Total spend == team_count * budget.

    ``key`` selects the PlayerValue field that drives the split:
    ``"vorp"`` for the season-long pass, ``"total_value"`` for the windowed
    board. Only players with a positive value participate.
    """
    total_budget = cfg.team_count * budget
    positive = [v for v in vals if getattr(v, key) > 0]
    total = sum(getattr(v, key) for v in positive)
    if total < 1e-12:
        return {v.player_id: 0.0 for v in vals}
    result: dict[int, float] = {}
    for v in vals:
        value = getattr(v, key)
        if value > 0:
            result[v.player_id] = (value / total) * total_budget
        else:
            result[v.player_id] = 0.0
    return result
