"""Marginal-value re-ranking for the live draft board.

Given the user's current roster, everyone drafted league-wide, and the
valuation table, re-rank the still-available pool by marginal value to
this roster:

    marginal = (total_value * urgency + pickup) * bench_scale

- urgency (per position group F/D/G): runway = remaining players with
  total_value above replacement level; need = teams still needing the
  position (league starter slots for the position minus drafted players,
  split into per-team chunks). When runway < need, urgency = need / runway
  — derived from that ratio, not a constant.
- bench_scale: 0.35 when every slot a player could start is already
  filled (bench asset), else 1.0.
- pickup (category leagues only): the user's projected team total per
  category is z-scored against the expected per-team mean (league pool
  total / team_count). Per-player category values are z-scored against
  the pool, so the pool total is 0 and the per-team mean is 0; a team
  of n drafted players has a z-space total that is the sum of their
  z-contributions, and its z-score is that sum over sqrt(n) (std of a
  sum of n unit-variance contributions). In every category where the
  user sits below -0.5, a player earns their z-contribution in that
  category (floored at 0).

Rationales are one-sentence string templates filled with numbers from
the data (VORP, team category z-scores, runway counts). No LLM, no
HTTP: display names come from the players table in data/hockey.db.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.valuation.league import LeagueConfig
from src.valuation.valuation import PlayerValue, _pos_group

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "hockey.db"

BENCH_SCALE = 0.35
DEFICIT_THRESHOLD = -0.5
STARTER_SLOTS = ("C", "LW", "RW", "D", "G")
POS_TO_SLOT = {"C": "C", "L": "LW", "R": "RW", "D": "D", "G": "G"}
SLOT_TO_GROUP = {"C": "F", "LW": "F", "RW": "F", "D": "D", "G": "G"}

_NAME_CACHE: dict[int, str] | None = None


@dataclass
class Recommendation:
    """One line of the draft recommendation: pick, value, reason."""

    name: str
    player_id: int
    marginal_value: float
    reason: str


def _load_names() -> dict[int, str]:
    global _NAME_CACHE
    if _NAME_CACHE is None:
        conn = sqlite3.connect(str(DB_PATH))
        try:
            rows = conn.execute(
                "SELECT player_id, full_name FROM players "
                "WHERE full_name IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()
        _NAME_CACHE = {pid: name for pid, name in rows}
    return _NAME_CACHE


def _eligible_slots(position: str) -> list[str]:
    """Slots a position can start, mirroring league.eligible_for."""
    if position == "G":
        return ["G"]
    slots = ["UTIL"]
    starter = POS_TO_SLOT.get(position)
    if starter is not None:
        slots.append(starter)
    return slots


def _roster_occupancy(
    drafted_by_me: list[int],
    pos_of: dict[int, str],
    cfg: LeagueConfig,
) -> tuple[dict[str, int], bool]:
    """Starter-slot occupancy per slot plus whether UTIL is filled.

    Drafted players are assumed to fill starter slots first (by their
    position), then UTIL, then BENCH.
    """
    occ = {s: 0 for s in STARTER_SLOTS}
    drafted = set(drafted_by_me)
    for pid in drafted:
        slot = POS_TO_SLOT.get(pos_of.get(pid, ""))
        if slot is not None:
            occ[slot] += 1
    starters_used = sum(
        min(occ[s], cfg.roster_slots.get(s, 0)) for s in STARTER_SLOTS
    )
    util_slots = cfg.roster_slots.get("UTIL", 0)
    util_full = (len(drafted) - starters_used) >= util_slots
    return occ, util_full


def _bench_asset(
    position: str,
    occ: dict[str, int],
    util_full: bool,
    cfg: LeagueConfig,
) -> bool:
    """True when every slot this position could start is already full."""
    for slot in _eligible_slots(position):
        if slot == "UTIL":
            if not util_full:
                return False
        elif occ.get(slot, 0) < cfg.roster_slots.get(slot, 0):
            return False
    return True


def _scarcity(
    drafted: set[int],
    available: list[PlayerValue],
    pos_of: dict[int, str],
    cfg: LeagueConfig,
) -> dict[str, tuple[float, int, int]]:
    """Per position group: (urgency, runway, teams_needing)."""
    drafted_count = {g: 0 for g in "FDG"}
    for pid in drafted:
        group = _pos_group(pos_of.get(pid, ""))
        if group is not None:
            drafted_count[group] += 1
    runway = {g: 0 for g in "FDG"}
    for v in available:
        if v.total_value > 0:
            runway[_pos_group(v.position)] += 1
    out: dict[str, tuple[float, int, int]] = {}
    for g in "FDG":
        per_team = sum(
            cfg.roster_slots.get(s, 0)
            for s, group in SLOT_TO_GROUP.items()
            if group == g
        )
        total_slots = cfg.team_count * per_team
        remaining = total_slots - drafted_count[g]
        if remaining > 0 and per_team > 0:
            teams_needing = math.ceil(remaining / per_team)
        else:
            teams_needing = 0
        run = runway[g]
        if remaining > 0 and 0 < run < teams_needing:
            urgency = teams_needing / run
        else:
            urgency = 1.0
        out[g] = (urgency, run, teams_needing)
    return out


def _team_category_state(
    drafted_by_me: list[int],
    cat_by_pid: dict[int, dict],
    cfg: LeagueConfig,
) -> dict[str, float]:
    """Z-score of the user's projected team total per category.

    In z-space the league pool total is 0, so the expected per-team
    mean (pool total / team_count) is 0; a team of n drafted players
    has z-space total = sum of their z-contributions and std sqrt(n).
    """
    if cfg.categories is None:
        return {}
    drafted = set(drafted_by_me)
    n = len(drafted)
    if n == 0:
        return {c: 0.0 for c in cfg.categories}
    sums = {c: 0.0 for c in cfg.categories}
    for pid in drafted:
        z = cat_by_pid.get(pid)
        if not z:
            continue
        for c in cfg.categories:
            sums[c] += z.get(c, 0.0)
    return {c: sums[c] / math.sqrt(n) for c in cfg.categories}


def _pickup(v: PlayerValue, team_state: dict[str, float]) -> float:
    """z-contribution in every category where the user sits below -0.5."""
    z = v.cat_zscores
    return sum(
        max(z.get(c, 0.0), 0.0)
        for c, tz in team_state.items()
        if tz < DEFICIT_THRESHOLD
    )


def _ordinal(rank: int) -> str:
    if rank == 1:
        return "Top"
    if rank == 2:
        return "2nd-best"
    if rank == 3:
        return "3rd-best"
    return f"{rank}th-best"


def _reason(
    v: PlayerValue,
    rank: int,
    team_state: dict[str, float],
    mult: float,
    runway: int,
    bench: bool,
) -> str:
    parts = [f"{_ordinal(rank)} remaining {v.position} by VORP ({v.total_value:.1f})"]
    if team_state:
        deficits = [
            (max(v.cat_zscores.get(c, 0.0), 0.0), c)
            for c, tz in team_state.items()
            if tz < DEFICIT_THRESHOLD
        ]
        best = max(deficits) if deficits else None
        if best is not None and best[0] > 0:
            cat = best[1]
            parts.append(f" and you are {team_state[cat]:.1f} in {cat}")
    if mult > 1.0:
        parts.append(f" with only {runway} {v.position} above replacement left")
    if bench:
        parts.append(" (bench asset)")
    return "".join(parts)



def recommend(
    drafted_by_me: list[int],
    drafted_all: list[int],
    vals: list[PlayerValue],
    cfg: LeagueConfig,
    round_no: int,
) -> list[Recommendation]:
    """Re-rank the available pool by marginal value; return the top 3.

    Args:
        drafted_by_me: player_ids on the user's roster.
        drafted_all: player_ids drafted by any team (not available).
        vals: windowed valuation table, one PlayerValue per pool player;
            the engine keys off total_value (the windowed headline value).
        cfg: LeagueConfig for the league being drafted in.
        round_no: current draft round, tracked by the client; the engine
            is a pure function of roster state and availability.

    Returns:
        Up to 3 Recommendation rows, best marginal value first.
    """
    drafted = set(drafted_by_me) | set(drafted_all)
    available = [v for v in vals if v.player_id not in drafted]
    if not available:
        return []

    pos_of = {v.player_id: v.position for v in vals}
    cat_by_pid = {v.player_id: v.cat_zscores for v in vals}
    names = _load_names()

    occ, util_full = _roster_occupancy(drafted_by_me, pos_of, cfg)
    urgency = _scarcity(drafted, available, pos_of, cfg)
    team_state = _team_category_state(drafted_by_me, cat_by_pid, cfg)

    scored = []
    for v in available:
        mult, runway, _need = urgency.get(_pos_group(v.position), (1.0, 0, 0))
        pickup = _pickup(v, team_state)
        bench = _bench_asset(v.position, occ, util_full, cfg)
        marginal = (v.total_value * mult + pickup) * (BENCH_SCALE if bench else 1.0)
        scored.append((marginal, v, bench, mult, runway))
    scored.sort(key=lambda t: (-t[0], t[1].player_id))
    top = scored[:3]

    by_pos: dict[str, list[PlayerValue]] = {}
    for v in available:
        by_pos.setdefault(v.position, []).append(v)
    for group in by_pos.values():
        group.sort(key=lambda v: (-v.total_value, v.player_id))
    rank = {
        v.player_id: i + 1 for lst in by_pos.values() for i, v in enumerate(lst)
    }

    return [
        Recommendation(
            name=names.get(v.player_id, f"Player {v.player_id}"),
            player_id=v.player_id,
            marginal_value=marginal,
            reason=_reason(v, rank[v.player_id], team_state, mult, runway, bench),
        )
        for marginal, v, bench, mult, runway in top
    ]

