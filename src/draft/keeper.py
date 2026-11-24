"""Keeper-league values: this season plus the seasons you could keep him.

In a keeper league a pick is also an option on the next few seasons, so a
21-year-old on the rise is worth more than a 33-year-old with the same
projection. Keeper value adds the discounted value of each future season
the player could be kept:

    future_y  = max(0, raw * L(age + y) / L(age) - replacement)
    keeper    = this_season + sum_{y=1..years} discount^y * future_y

- raw / replacement: the player's full-season raw value and his group's
  replacement level from the season board's valuation (regular-season
  pass), so future seasons are judged on a whole season even when the
  board's timeframe is shorter.
- L: the skater aging curves in config/regression.yaml (``level_curves``:
  forwards / defensemen, peak-normalized), the same ones the projection
  uses. Goalies use a flat curve (the projection has no goalie level
  curve), so their future seasons equal this one.
- max(0, ...): a season below replacement level is worth nothing -- you
  simply wouldn't keep him that year.
- age: whole years at Feb 1 of the season (projections.common.age_at);
  unknown birth date -> no aging (ratio 1).

Rules differ by league (how many keepers, whether a keeper costs a pick),
so the settings are the board's: how many future seasons count and the
per-season discount.
"""
from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import yaml

from src.paths import CONFIG_DIR
from src.projections.common import _level_value, age_at
from src.valuation.valuation import PlayerValue, value_group

REGRESSION_PATH = CONFIG_DIR / "regression.yaml"
MAX_YEARS = 5
DEFAULT_DISCOUNT = 0.75


def load_curves(path: str | Path | None = None) -> dict[str, dict[int, float]]:
    """{"F": forward curve, "D": defensemen curve} from regression.yaml."""
    with open(path or REGRESSION_PATH) as f:
        lc = (yaml.safe_load(f) or {}).get("level_curves") or {}
    def conv(c):
        return {int(k): float(v) for k, v in (c or {}).items()}
    return {"F": conv(lc.get("forwards")), "D": conv(lc.get("defensemen"))}


def age_ratio(group: str, age: float | None, years: int,
              curves: dict[str, dict[int, float]]) -> float:
    """L(age + years) / L(age); 1.0 for goalies, unknown ages or no curve."""
    curve = curves.get(group)
    if group == "G" or age is None or not curve:
        return 1.0
    now = _level_value(curve, age)
    return _level_value(curve, age + years) / now if now > 0 else 1.0


def future_values(season_vals: list[PlayerValue], ages: dict[int, float | None],
                  years: int, discount: float,
                  curves: dict[str, dict[int, float]]) -> dict[int, float]:
    """Discounted value of seasons 1..years after this one, per player."""
    years = max(0, min(int(years), MAX_YEARS))
    out: dict[int, float] = {}
    for v in season_vals:
        g = value_group(v.position)
        age = ages.get(v.player_id)
        total = 0.0
        for y in range(1, years + 1):
            season = v.raw_value * age_ratio(g, age, y, curves) - v.replacement_used
            total += discount ** y * max(0.0, season)
        out[v.player_id] = total
    return out


def apply_keeper(vals: list[PlayerValue], future: dict[int, float],
                 ) -> list[PlayerValue]:
    """Copies of ``vals`` with total_value raised by each player's future
    value (the shared, cached value lists are never modified)."""
    return [dataclasses.replace(v, total_value=v.total_value
                                + future.get(v.player_id, 0.0))
            for v in vals]


def player_ages(db_path: str | Path, season_id: int) -> dict[int, float | None]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT player_id, birth_date FROM players").fetchall()
    finally:
        conn.close()
    return {pid: age_at(b, season_id) for pid, b in rows}
