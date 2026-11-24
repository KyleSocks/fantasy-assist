"""Draft-board modes: timeframe (src/draft/timeframe.py) and keeper value
(src/draft/keeper.py) laid over the season board's valuation.

routes.py asks ``mode_values`` for the (projections, values) a board or
recommendation should use; the default mode returns the season board's own
cached pair untouched.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from src.cache import Memo
from src.draft.keeper import (
    DEFAULT_DISCOUNT,
    MAX_YEARS,
    apply_keeper,
    future_values,
    load_curves,
    player_ages,
)
from src.draft.timeframe import TIMEFRAMES, timeframe_values
from src.paths import DB_PATH
from src.valuation.league import LeagueConfig


@dataclass(frozen=True)
class Mode:
    timeframe: str = "season"
    keeper_years: int = 0
    keeper_discount: float = DEFAULT_DISCOUNT

    @property
    def is_default(self) -> bool:
        return self.timeframe == "season" and self.keeper_years == 0


def parse_mode(timeframe: str | None, keeper: int | None,
               discount: float | None) -> Mode:
    tf = timeframe or "season"
    if tf not in TIMEFRAMES:
        raise HTTPException(422, f"Unknown timeframe '{tf}' "
                                 f"(one of {list(TIMEFRAMES)})")
    years = int(keeper or 0)
    if not 0 <= years <= MAX_YEARS:
        raise HTTPException(422, f"keeper must be 0..{MAX_YEARS}")
    d = DEFAULT_DISCOUNT if discount is None else float(discount)
    if not 0 < d <= 1:
        raise HTTPException(422, "discount must be in (0, 1]")
    return Mode(tf, years, round(d, 3))


def _today() -> str:
    from src.api.inseason import _today as today
    return today()


def window_stamp() -> tuple:
    """What a window board depends on beyond the DB and config/: the day
    and the in-season settings (data/settings.yaml)."""
    from src.cache import file_stamp
    from src.inseason.settings import SETTINGS_PATH
    return (_today(),) + file_stamp(SETTINGS_PATH)


_WINDOW = Memo(maxsize=8)


def _window(cfg: LeagueConfig, timeframe: str, today: str):
    """(projs, vals, window) for a timeframe, cached per in-season context
    (rebuilt when the DB / config changes) and day."""
    from src.api.inseason import _ctx, _gen
    try:
        ctx = _ctx(cfg.name)
    except HTTPException as e:
        raise HTTPException(e.status_code, "This timeframe needs this "
                            f"season's data: {e.detail}")
    key = (cfg.name, timeframe, today, _gen(ctx))
    try:
        return _WINDOW.get(key, lambda: timeframe_values(ctx, timeframe, today))
    except ValueError as e:
        raise HTTPException(422, str(e))


def mode_values(cfg: LeagueConfig, season: int, mode: Mode, base):
    """(projs by id, values, meta) for a board in ``mode``.

    ``base``: the season board's cached (projs by id, values). Neither is
    modified. ``meta`` carries what the client shows: the timeframe, its
    dates, the keeper settings, extra per-player columns."""
    projs, vals = base
    meta: dict = {"timeframe": mode.timeframe,
                  "timeframe_label": TIMEFRAMES[mode.timeframe],
                  "timeframes": TIMEFRAMES, "window": None,
                  "keeper": {"years": mode.keeper_years,
                             "discount": mode.keeper_discount},
                  "value_columns": [["vorp_regular", "VORP RS"],
                                    ["vorp_playoff", "VORP PO"]],
                  "extra": {}}
    if cfg.playoff_window is None:          # no fantasy playoffs (a mock room)
        meta["value_columns"] = [["gp_regular", "GP"]]
    if mode.is_default:
        return projs, vals, meta
    if mode.timeframe != "season":
        projs, vals, win = _window(cfg, mode.timeframe, _today())
        meta["window"] = {"start": win.start, "end": win.end}
        meta["value_columns"] = [["gp_regular", "GP"]]
        if mode.timeframe == "ros" and cfg.playoff_window:
            meta["value_columns"] = [["gp_regular", "GP RS"],
                                     ["gp_playoff", "GP PO"]]
    if mode.keeper_years:
        ages = player_ages(DB_PATH, season)
        future = future_values(base[1], ages, mode.keeper_years,
                               mode.keeper_discount, load_curves())
        now = {v.player_id: v.total_value for v in vals}
        vals = apply_keeper(vals, future)
        meta["value_columns"] = [["age", "Age"], ["now", "Now"],
                                 ["future", "Future"]] + meta["value_columns"]
        meta["extra"] = {pid: {"age": ages.get(pid),
                               "now": round(now[pid], 2),
                               "future": round(future.get(pid, 0.0), 2)}
                         for pid in now}
    return projs, vals, meta
