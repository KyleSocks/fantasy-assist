"""Injury fallout and the goalie grid (src/inseason/fallout.py,
src/inseason/goalie_grid.py), with who owns each player in your league."""
from __future__ import annotations

from typing import Annotated

from fastapi import Query

from src.api import inseason as ins
from src.api.inseason import DateStr, LeagueName
from src.inseason import leagues as store
from src.inseason.roster import parse_list


def league_status(ctx, start: str) -> dict:
    """{"owner": {pid: (team id, name)}, "my_team": id, "injured": {pid:
    {tag, back}}} from the stored league rosters and free-agent list."""
    from src.inseason.teams import team_tags, trade_roster
    snap = store.read_json(ins._dir(ctx) / ins.LEAGUE_FILE) or {}
    owner, injured = {}, {}
    for tid, t in (snap.get("teams") or {}).items():
        players = t.get("players") or []
        roster, out = trade_roster(ctx, players, start)
        tags = team_tags(ctx, players)
        for pid in roster:
            owner[pid] = (int(tid), t.get("name") or f"Team {tid}")
        for pid, back in out.items():
            injured[pid] = {"tag": tags.get(pid), "back": back}
    fa = parse_list(ctx, ins._read(ins.FA_FILE, ctx))
    for pid in fa.inactive:
        if pid not in owner:
            injured.setdefault(pid, {"tag": fa.tags.get(pid), "back": None})
    me = snap.get("my_team_id")
    return {"owner": owner, "my_team": int(me) if me is not None else None,
            "injured": injured, "has_league": bool(snap)}


def _who(st: dict, pid: int) -> dict:
    o = st["owner"].get(pid)
    if not o:
        return {"owner": None, "owner_id": None, "mine": False,
                "free_agent": st["has_league"]}
    return {"owner": o[1], "owner_id": o[0], "mine": o[0] == st["my_team"], "free_agent": False}


@ins.router.get("/fallout")
def get_fallout(league: LeagueName = None, start: DateStr = None) -> dict:
    """Regulars who are out and the teammates who took their minutes."""
    from src.inseason.fallout import fallout
    ctx = ins._ctx(league)
    st = league_status(ctx, ins._day(start))
    rows = fallout(ctx, st["injured"])
    for r in rows:
        r.update(_who(st, r["id"]))
        for t in r["takers"]:
            t.update(_who(st, t["id"]))
    return {"as_of": ctx.as_of, "unit": "fpts" if ctx.scorer.points else "value",
            "players": rows}


@ins.router.get("/goalie-grid")
def get_goalie_grid(league: LeagueName = None, start: DateStr = None,
                    days: Annotated[int, Query(ge=1, le=14)] = 7) -> dict:
    """Every team's games, likely starters and a grade for each start."""
    from src.inseason.goalie_grid import grid
    ctx = ins._ctx(league)
    day = ins._day(start)
    st = league_status(ctx, day)
    g = grid(ctx, day, days, injured=set(st["injured"]))
    for t in g["teams"]:
        for c in t["cells"].values():
            for x in c["goalies"]:
                x.update(_who(st, x["id"]))
    return {"as_of": ctx.as_of, "unit": "fpts" if ctx.scorer.points else "value", **g}
