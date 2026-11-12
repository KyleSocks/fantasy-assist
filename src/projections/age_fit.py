"""Fit per-stat aging curves from the database (delta method).

For every player with back-to-back regular seasons (both with enough ice
time), compare his per-60 rate in season 2 with season 1, weighted by the
harmonic mean of the two seasons' TOI. The weighted ratio at age a (his
age in season 2) is how much the stat changes going INTO age a. Chaining
the ratios gives a level curve, normalized so its peak is 1.00 -- the same
shape config/regression.yaml's level_curves use, but per stat and fitted.

Goalies: the same on save %, additively (a level in save-% points).

Known bias: the delta method only sees players who kept playing, so the
decline at old ages is slightly understated.

Usage (prints YAML to paste into config/regression.yaml):
    uv run python -m src.projections.age_fit --db data/hockey.db
"""
from __future__ import annotations

import argparse
import sqlite3

import yaml

from src.paths import DEFAULT_DB_ARG

from .common import _toi_parts, age_at

# stat -> column index in skater_ratios' SELECT
STATS = {"goals": 3, "assists": 4, "shots": 5, "blocks": 6, "hits": 7,
         "pp_points": 8, "sh_points": 11}


def chain_levels(ratios: dict[int, float], lo: int, hi: int) -> dict[int, float]:
    """Level curve from year-over-year ratios, peak-normalized to 1.00.

    ``ratios[a]`` = level(a) / level(a - 1). Ages in [lo, hi] without a
    ratio carry the previous level (ratio 1)."""
    level = {lo: 1.0}
    for a in range(lo + 1, hi + 1):
        level[a] = level[a - 1] * ratios.get(a, 1.0)
    peak = max(level.values())
    return {a: round(v / peak, 3) for a, v in level.items()}


def smooth(values: dict[int, float]) -> dict[int, float]:
    """3-point moving average over consecutive ages present."""
    out = {}
    for a, v in values.items():
        nb = [values[b] for b in (a - 1, a, a + 1) if b in values]
        out[a] = sum(nb) / len(nb)
    return out


def skater_ratios(conn: sqlite3.Connection, positions: tuple[str, ...],
                  min_toi_min: float, min_pairs: int) -> dict[str, dict[int, float]]:
    rows = conn.execute(
        "SELECT s.player_id, s.season_id, s.gp, s.goals, s.assists, s.shots,"
        " s.blocks, s.hits, s.pp_points, s.pp_toi_sec, s.toi_sec, s.sh_points,"
        " s.sh_toi_sec, p.birth_date FROM skater_seasons s JOIN players p"
        " ON p.player_id = s.player_id WHERE s.gp > 0 AND p.position IN ({})"
        .format(",".join(f"'{x}'" for x in positions))).fetchall()
    by = {(r[0], r[1]): r for r in rows}
    sums: dict[str, dict[int, list[float]]] = {s: {} for s in STATS}
    counts: dict[int, int] = {}
    for (pid, sid), r2 in by.items():
        r1 = by.get((pid, sid - 10001))
        age = age_at(r2[13], sid)
        if r1 is None or age is None:
            continue
        t1 = sum(_toi_parts(r1[10], r1[9], r1[12])) / 60.0
        t2 = sum(_toi_parts(r2[10], r2[9], r2[12])) / 60.0
        if t1 < min_toi_min or t2 < min_toi_min:
            continue
        w = 2 * t1 * t2 / (t1 + t2)
        a = int(age)
        counts[a] = counts.get(a, 0) + 1
        for stat, ci in STATS.items():
            d1 = (r1[9] or 0) / 60.0 if stat == "pp_points" else t1
            d2 = (r2[9] or 0) / 60.0 if stat == "pp_points" else t2
            if d1 <= 0 or d2 <= 0:
                continue
            acc = sums[stat].setdefault(a, [0.0, 0.0])
            acc[0] += w * (r2[ci] or 0) / d2
            acc[1] += w * (r1[ci] or 0) / d1
    out: dict[str, dict[int, float]] = {}
    for stat, by_age in sums.items():
        out[stat] = {a: num / den for a, (num, den) in by_age.items()
                     if den > 0 and counts.get(a, 0) >= min_pairs}
    return out


def goalie_deltas(conn: sqlite3.Connection, min_sa: float,
                  min_pairs: int) -> dict[int, float]:
    rows = conn.execute(
        "SELECT g.player_id, g.season_id, g.saves, g.shots_against, p.birth_date"
        " FROM goalie_seasons g JOIN players p ON p.player_id = g.player_id"
        " WHERE g.shots_against > 0").fetchall()
    by = {(r[0], r[1]): r for r in rows}
    acc: dict[int, list[float]] = {}
    for (pid, sid), r2 in by.items():
        r1 = by.get((pid, sid - 10001))
        age = age_at(r2[4], sid)
        if r1 is None or age is None or r1[3] < min_sa or r2[3] < min_sa:
            continue
        w = 2 * r1[3] * r2[3] / (r1[3] + r2[3])
        a = acc.setdefault(int(age), [0.0, 0.0, 0])
        a[0] += w * (r2[2] / r2[3] - r1[2] / r1[3])
        a[1] += w
        a[2] += 1
    return {age: s / w for age, (s, w, n) in acc.items() if n >= min_pairs and w > 0}


def fit(conn: sqlite3.Connection, min_toi_min: float = 300.0, min_pairs: int = 30,
        min_sa: float = 500.0) -> dict:
    curves: dict = {"level_curves_by_stat": {}}
    for group, positions, lo, hi in (("forwards", ("C", "L", "R"), 18, 38),
                                     ("defensemen", ("D",), 19, 40)):
        ratios = skater_ratios(conn, positions, min_toi_min, min_pairs)
        curves["level_curves_by_stat"][group] = {
            stat: chain_levels(smooth(r), lo, hi) for stat, r in ratios.items() if r}
    deltas = smooth(goalie_deltas(conn, min_sa, min_pairs))
    if deltas:
        level, cur = {}, 0.0
        for a in range(20, 41):
            cur += deltas.get(a, 0.0)
            level[a] = cur
        peak = max(level.values())
        curves["goalie_sv_age"] = {a: round(v - peak, 4) for a, v in level.items()}
    return curves


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=DEFAULT_DB_ARG)
    ap.add_argument("--min-toi-min", type=float, default=300.0)
    ap.add_argument("--min-pairs", type=int, default=30)
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        print(yaml.safe_dump(fit(conn, args.min_toi_min, args.min_pairs),
                             sort_keys=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
