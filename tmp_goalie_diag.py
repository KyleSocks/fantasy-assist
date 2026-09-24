"""Diagnose why no goalies appear in the top 24 for kyle_12_points.

Computes raw points, the G replacement level, and VORP for goalies,
then compares the best goalie VORP against the skater top-24 cutoff.
Run: uv run python tmp_goalie_diag.py
"""
from __future__ import annotations

import sqlite3

from src.projections.projections import project_all
from src.valuation.league import load_league, points_value, STAT_FIELD_MAP
from src.valuation.valuation import (
    _pos_group,
    _determine_pools,
    valuate,
)

DB = "data/hockey.db"
SEASON = 20262027  # next season after latest ingested 20252026


def main() -> None:
    conn = sqlite3.connect(DB)
    names = {
        row[0]: (row[1], row[2])
        for row in conn.execute(
            "SELECT player_id, full_name, position FROM players"
        )
    }
    conn.close()

    cfg = load_league("config/leagues/kyle_12_points.yaml")
    projs = project_all(SEASON, DB)
    vals = valuate(projs, cfg)
    pools = _determine_pools(projs, cfg)

    # Raw points per player
    raw = {p.player_id: points_value(p, cfg) for p in projs}

    # Replacement levels per position group (index = pool size = (pool+1)th)
    by_pos: dict[str, list[int]] = {"F": [], "D": [], "G": []}
    for p in projs:
        by_pos[_pos_group(p.position)].append(p.player_id)
    levels = {}
    for pos, pids in by_pos.items():
        ranked = sorted(pids, key=lambda i: raw[i], reverse=True)
        idx = pools[pos]
        levels[pos] = raw[ranked[idx]] if idx < len(ranked) else 0.0

    print("=== pools (replacements) ===")
    print(pools)
    print("=== replacement levels (raw points) ===")
    for k, v in levels.items():
        print(f"  {k}: {v:.1f}")

    # Goalie raw points + VORP, ranked
    goalies = [p for p in projs if _pos_group(p.position) == "G"]
    g_rows = []
    for p in goalies:
        vorp = raw[p.player_id] - levels["G"]
        g_rows.append((p.player_id, names.get(p.player_id, ("?", "?"))[0], vorp))
    g_rows.sort(key=lambda t: t[2], reverse=True)

    print("\n=== top 15 goalies by VORP ===")
    print("rank  name                     raw_pts   vorp")
    for i, (pid, name, vorp) in enumerate(g_rows[:15]):
        p = next(x for x in goalies if x.player_id == pid)
        print(f"{i+1:2d}  {name[:24]:24s} {raw[pid]:8.1f}  {vorp:7.1f}")

    # Per-stat breakdown for the #1 goalie
    best_pid = g_rows[0][0]
    best_p = next(x for x in goalies if x.player_id == best_pid)
    print("\n=== #1 goalie per-stat (kyle_12_points weights) ===")
    comp = {}
    for stat, w in cfg.points_per_stat.items():
        if stat == "HIT+BLK":
            continue
        attr = STAT_FIELD_MAP[stat]
        v = getattr(best_p, attr, None)
        comp[stat] = (v if v is not None else 0.0) * w
    for stat, contrib in comp.items():
        print(f"  {stat:4s} x{cfg.points_per_stat[stat]:>5} = {contrib:8.1f}")
    print(f"  TOTAL raw points = {raw[best_pid]:.1f}  VORP = {g_rows[0][2]:.1f}")

    # Skater top-24 cutoff (overall board order)
    ordered = sorted(
        vals, key=lambda v: (-v.vorp, v.player_id)
    )
    cutoff = ordered[23]
    print("\n=== overall board top-24 cutoff ===")
    print(f"  rank 24: {names.get(cutoff.player_id, ('?', '?'))[0]} "
          f"VORP={cutoff.vorp:.1f}")
    print(f"  best goalie VORP: {g_rows[0][2]:.1f}")
    gap = g_rows[0][2] - cutoff.vorp
    print(f"  gap (best goalie - cutoff): {gap:.1f}")

    # How many goalies have positive VORP / above cutoff
    above = sum(1 for _, _, vorp in g_rows if vorp > cutoff.vorp)
    print(f"  goalies above cutoff: {above}")


if __name__ == "__main__":
    main()
