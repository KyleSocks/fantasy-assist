"""Trend: how a player has produced over the last few weeks (trend_days,
default 21) against his blended rate -- the same blend every decision uses.

Skaters: value per game played. Goalies: value per start. Below
trend_min_games games in the window there is no trend (too few to read).
"""
from __future__ import annotations

from .evaluate import Context, _value
from .model import Line


def _goalie_start(rates: dict[str, float]) -> dict[str, float]:
    """One start's line from per-start rates."""
    return {"wins": rates.get("wins", 0.0), "saves": rates.get("saves", 0.0),
            "goals_against": rates.get("goals_against", 0.0),
            "shutouts": rates.get("shutouts", 0.0), "games_started": 1.0,
            "save_pct": rates.get("save_pct", 0.0)}


def trend(ctx: Context, line: Line) -> dict:
    """{"games", "per_game", "base", "diff", "pct", "start", "end"};
    per_game is None when there are too few games in the window."""
    rec = line.extra.get("recent")
    min_games = int(ctx.icfg.get("trend_min_games", 3))
    games = rec["games"] if rec else 0
    out = {"games": games, "per_game": None, "base": None, "diff": None,
           "pct": None, "start": rec and rec["start"], "end": rec and rec["end"]}
    if games < min_games:
        return out
    per = {k: v / games for k, v in rec["stats"].items()}
    if line.is_goalie:
        sa = rec["stats"].get("shots_against", 0.0)
        per["save_pct"] = rec["stats"].get("saves", 0.0) / sa if sa else 0.0
        now = _value(ctx, line, _goalie_start(per))
        base = _value(ctx, line, _goalie_start(line.blended))
    else:
        now = _value(ctx, line, per)
        base = _value(ctx, line, {k: line.blended.get(k, 0.0)
                                  for k in rec["stats"]})
    diff = now - base
    pct = diff / base if ctx.scorer.points and base > 0 else None
    return dict(out, per_game=now, base=base, diff=diff, pct=pct)
