"""This week's matchup, on the /api/inseason router (imported at the bottom
of src/api/inseason.py):

GET /api/inseason/matchup?league=&team=&start=

Needs matchups.json and league_rosters.json (both written by Refresh).
``start`` = first day still to play; default today, or tomorrow when the
scores were read after 7 pm (tonight's games are then mostly in them).
See src/inseason/matchup.py for the model.
"""
from __future__ import annotations

import datetime
from typing import Annotated

from fastapi import HTTPException, Query

from src.api import inseason as ins
from src.api.inseason import DateStr, LeagueName
from src.inseason import leagues as store
from src.inseason import matchup as mu
from src.inseason.roster import parse_list

EVENING_HOUR = 19


def default_start(fetched: str | None, today: datetime.date | None = None) -> str:
    today = today or datetime.date.today()
    if fetched:
        try:
            t = datetime.datetime.fromisoformat(fetched)
        except ValueError:
            t = None
        if t and t.date() == today and t.hour >= EVENING_HOUR:
            return (today + datetime.timedelta(days=1)).isoformat()
    return today.isoformat()


@ins.router.get("/matchup")
def get_matchup(league: LeagueName = None, start: DateStr = None,
                team: Annotated[int | None, Query(ge=1, le=99)] = None) -> dict:
    ctx = ins._ctx(league)
    if not ctx.scorer.points:
        raise HTTPException(400, "matchup projections are for points leagues only")
    folder = ins._dir(ctx)
    m = store.read_json(folder / ins.MATCHUPS_FILE)
    snap = store.read_json(folder / ins.LEAGUE_FILE)
    if not m or not snap:
        raise HTTPException(400, "no matchups yet: press Refresh data")
    me = team or snap.get("my_team_id")
    s = ins._day(start) if start else default_start(m.get("fetched"))
    w = mu.weekly(ctx, snap, m, s)
    mine = next((x for x in w["matchups"] if me in (x["teams"][0]["id"], x["teams"][1]["id"])), None)
    if mine is None:
        raise HTTPException(404, f"team {me} has no matchup this week")
    you, them = ((mine["teams"][0], mine["teams"][1]) if mine["teams"][0]["id"] == me
                 else (mine["teams"][1], mine["teams"][0]))
    swing = {"adds": [], "drop_pool": []}
    if me == snap.get("my_team_id"):
        pl = parse_list(ctx, ins._read(ins.FA_FILE, ctx))
        taken = ins._league_taken(ctx) or set()
        fas = {p: e for p, e in pl.active.items() if p not in taken}
        swing = mu.swing_adds(ctx, w["ws"], w["rosters"].get(me, {}), fas,
                              pl.available_from, you["final"], you["sd"] ** 2,
                              them["final"], them["sd"] ** 2, s)

    def named(p):
        ln = ctx.lines[p]
        return {"id": p, "name": ln.name, "team": ln.team, "pos": ln.position,
                "per_game": ctx.scorer.per_game.get(p)}
    adds = [{**r, "add": named(r["add"]), "drop": named(r["drop"])} for r in swing["adds"]]
    return {
        "week": w["week"], "dates": w["dates"], "remaining": w["remaining"],
        "start": s, "scores_read": m.get("fetched"), "you": you, "them": them,
        "adds": adds, "drop_pool": [named(p) for p in swing["drop_pool"]],
        "league": [{"teams": [{k: t[k] for k in ("id", "name", "score", "final", "win", "sd")}
                              for t in x["teams"]]} for x in w["matchups"]],
        "my_team_id": snap.get("my_team_id"), "as_of": ctx.as_of, "unit": "fpts",
    }
