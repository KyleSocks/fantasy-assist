"""Goalie grid: every team's games over the next days, who's likely in net
and how good a start it is.

  start chance   goalie_share (evaluate.py): his share of starts, with the
                 back-to-back rule on the second night. Goalies listed
                 injured are left out and their share goes to the healthy
                 goalies on that team, in proportion to their shares.
  if he starts   his projected value for a start against that opponent
                 (opponent's goals and shots for)
  grade          A-E by the share of this stretch's likely starts (40%+
                 odds) that are better than this one: A = under 20% better,
                 B = 20-40%, ... E = 80%+
  expected       start chance x value if he starts -- what he's worth to
                 roster that night
"""
from __future__ import annotations

import datetime

from .evaluate import Context, _value, expected_line, goalie_share

GRADES = "ABCDE"


def grid(ctx: Context, start: str, days: int = 7,
         injured: set[int] | None = None, min_share: float = 0.05) -> dict:
    injured = injured or set()
    d0 = datetime.date.fromisoformat(start)
    dates = [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(days)]
    goalies: dict[str, list] = {}
    for ln in ctx.lines.values():
        if ln.is_goalie and ln.team and ln.start_share > 0:
            goalies.setdefault(ln.team, []).append(ln)
    teams = []
    starts = []
    for team, gs in goalies.items():
        healthy = [g for g in gs if g.player_id not in injured]
        cells = {}
        for d in dates:
            games = ctx.team_games(team, d, d)
            if not games:
                continue
            opp = games[0][1]
            share = {g.player_id: goalie_share(ctx, g, d) for g in healthy}
            tot = sum(share.values())
            out = []
            for g in healthy:
                p = share[g.player_id] / tot if tot else 0.0
                if p < min_share:
                    continue
                if_start = _value(ctx, g, expected_line(g, ctx.factors.get(opp),
                                                        assume_start=True))
                own = share[g.player_id]
                out.append({"id": g.player_id, "name": g.name, "chance": p,
                            "if_start": if_start, "expected": p * if_start,
                            "b2b": own < g.start_share - 1e-9 or own > g.start_share + 1e-9})
                if p >= 0.4:
                    starts.append(if_start)
            out.sort(key=lambda x: -x["chance"])
            cells[d] = {"opp": opp, "goalies": out}
        teams.append({"team": team, "games": len(cells), "cells": cells})
    starts.sort()

    def grade(v: float) -> str:
        if not starts:
            return "C"
        better = sum(1 for x in starts if x > v) / len(starts)
        return GRADES[min(4, int(better * 5))]

    for t in teams:
        for c in t["cells"].values():
            for g in c["goalies"]:
                g["grade"] = grade(g["if_start"])
    teams.sort(key=lambda t: (-t["games"], t["team"]))
    return {"dates": dates, "teams": teams}
