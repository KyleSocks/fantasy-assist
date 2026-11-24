"""Draft-board values over a chosen stretch of the schedule.

The draft board normally values the whole season from the preseason
projection. A draft held mid-season (a league started late, a short
one-week / one-month contest, or one that only cares about the fantasy
playoffs) should value what each player is expected to do over THAT
stretch instead, using what he has done this season.

For every player the in-season model (src/inseason) already has a blended
per-game line (preseason projection blended with season-to-date stats,
deployment-adjusted) and opponent factors. Over a window the player's
expected totals are

    total[stat] = sum over his team's games in the window of
                  weight(date) * expected_line(line, opponent)[stat]

(goalies: each game also scaled by his start chance that night, so a
back-to-back moves a start to the backup). Those totals become a
Projection, and the ordinary draft valuation (``valuate``: pool sizes,
replacement level, VORP, category z-scores) runs on them -- so a window
board is ranked exactly like the season board, just on different totals.

Timeframes:
  season    the preseason full-season board (unchanged; not handled here)
  ros       today to the end of the fantasy playoffs, playoff games
            weighted like the rest of the app (Context.game_weight)
  next7     the next 7 days, every game counts once
  next30    the next 30 days
  playoffs  the league's fantasy playoff window only (playoff_window in
            the league yaml); clamped to start today if it has begun
"""
from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass

from src.inseason.evaluate import Context, expected_line, goalie_share
from src.projections.common import Projection
from src.valuation.valuation import PlayerValue, valuate

TIMEFRAMES: dict[str, str] = {
    "season": "Full season (preseason projection)",
    "ros": "Rest of season",
    "next7": "Next 7 days",
    "next30": "Next 30 days",
    "playoffs": "Fantasy playoffs",
}
SEASON_GAMES = 82        # a goalie's start share -> season starts (startable)
COUNT_FIELDS = ("goals", "assists", "shots", "blocks", "hits", "pp_points",
                "sh_points")
GOALIE_FIELDS = ("wins", "saves", "goals_against", "shutouts",
                 "games_started")


@dataclass(frozen=True)
class Window:
    timeframe: str
    start: str
    end: str
    weight: Callable[[str], float]
    label: str


def _plus(d: str, days: int) -> str:
    return (datetime.date.fromisoformat(d)
            + datetime.timedelta(days=days)).isoformat()


def window_for(ctx: Context, timeframe: str, today: str) -> Window:
    """The date range and per-date game weight for a timeframe."""
    if timeframe not in TIMEFRAMES or timeframe == "season":
        raise ValueError(f"unknown timeframe '{timeframe}' "
                         f"(one of {sorted(set(TIMEFRAMES) - {'season'})})")
    label = TIMEFRAMES[timeframe]
    if timeframe == "ros":
        return Window(timeframe, today, ctx.fantasy_end(), ctx.game_weight,
                      label)
    if timeframe in ("next7", "next30"):
        days = 7 if timeframe == "next7" else 30
        return Window(timeframe, today, _plus(today, days - 1),
                      lambda d: 1.0, label)
    po = ctx.league.playoff_window
    if not po:
        raise ValueError(f"league '{ctx.league.name}' has no playoff_window")
    if today > po[1]:
        raise ValueError(f"the fantasy playoffs ended on {po[1]}")
    return Window(timeframe, max(today, po[0]), po[1], lambda d: 1.0, label)


def window_projection(ctx: Context, pid: int, win: Window,
                      season_id: int) -> Projection:
    """Expected totals for one player over ``win``, as a Projection."""
    line = ctx.lines[pid]
    tot = dict.fromkeys(COUNT_FIELDS + GOALIE_FIELDS, 0.0)
    gp = 0.0
    for d, opp in ctx.team_games(line.team, win.start, win.end):
        w = win.weight(d)
        if w <= 0:
            continue
        e = expected_line(line, ctx.factors.get(opp))
        if line.is_goalie:
            # tonight's start chance instead of the flat season share
            s = (goalie_share(ctx, line, d) / line.start_share
                 if line.start_share > 0 else 0.0)
            for k in GOALIE_FIELDS:
                tot[k] += w * s * e[k]
            gp += w * s * e["games_started"]
        else:
            for k in COUNT_FIELDS:
                tot[k] += w * e[k]
            gp += w * line.play_share
    if line.is_goalie:
        return Projection(
            player_id=pid, season_id=season_id, gp=gp, toi_per_gp=0.0,
            goals=0.0, assists=0.0, shots=0.0, blocks=0.0, hits=0.0,
            pp_points=0.0, wins=tot["wins"],
            save_pct=line.blended.get("save_pct"),
            shutouts=tot["shutouts"], games_started=tot["games_started"],
            goals_against=tot["goals_against"], saves=tot["saves"],
            position=line.position,
            # startable-goalie test (valuation._season_starts) looks at
            # season starts: judge the role, not a 7-day sample
            terms={"final": {"games_started":
                             line.start_share * SEASON_GAMES}})
    toi = line.toi_pg_obs or line.toi_pg_prior or 0.0
    return Projection(
        player_id=pid, season_id=season_id, gp=gp, toi_per_gp=toi,
        goals=tot["goals"], assists=tot["assists"], shots=tot["shots"],
        blocks=tot["blocks"], hits=tot["hits"], pp_points=tot["pp_points"],
        sh_points=tot["sh_points"], wins=None, save_pct=None,
        position=line.position)


def _in_playoffs(ctx: Context, d: str) -> bool:
    po = ctx.league.playoff_window
    return bool(po) and po[0] <= d <= po[1]


def timeframe_values(ctx: Context, timeframe: str, today: str,
                     pids: set[int] | None = None,
                     ) -> tuple[dict[int, Projection], list[PlayerValue], Window]:
    """(projections by id, values, window) for a timeframe.

    ``pids``: limit to these players (default every player the in-season
    model has a line for). ``total_value`` is the window VORP; gp_regular /
    gp_playoff split the window's games at the fantasy playoffs, and
    vorp_regular / vorp_playoff carry the window VORP on whichever side
    the window falls (both when it spans the two)."""
    win = window_for(ctx, timeframe, today)
    ids = sorted(ctx.lines if pids is None else pids & set(ctx.lines))
    projs = {pid: window_projection(ctx, pid, win, ctx.season) for pid in ids}
    vals = valuate(list(projs.values()), ctx.league)
    for v in vals:
        line = ctx.lines[v.player_id]
        reg = po = 0
        for d, _ in ctx.team_games(line.team, win.start, win.end):
            if win.weight(d) > 0:
                if _in_playoffs(ctx, d):
                    po += 1
                else:
                    reg += 1
        v.gp_regular, v.gp_playoff = float(reg), float(po)
        v.total_value = v.vorp
        v.vorp_regular = v.vorp if reg else 0.0
        v.vorp_playoff = v.vorp if po else 0.0
    return projs, vals, win
