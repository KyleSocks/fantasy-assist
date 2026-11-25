"""Projection sandbox: GET /api/inseason/player/{id}/whatif?pp=&ev=
(src/inseason/sandbox.py)."""
from __future__ import annotations

from typing import Annotated

from fastapi import HTTPException, Query

from src.api import inseason as ins
from src.api.inseason import DateStr, LeagueName

Sec = Annotated[float | None, Query(ge=0, le=3000)]


@ins.router.get("/player/{player_id}/whatif")
def get_whatif(player_id: int, league: LeagueName = None, start: DateStr = None,
               pp: Sec = None, ev: Sec = None) -> dict:
    """His projection at ``pp`` / ``ev`` seconds of ice time a game."""
    from src.inseason.sandbox import whatif
    ctx = ins._ctx(league)
    if player_id not in ctx.lines:
        raise HTTPException(404, f"Unknown player id {player_id}")
    try:
        out = whatif(ctx, player_id, ins._day(start), pp, ev)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"player_id": player_id, "name": ctx.lines[player_id].name,
            "unit": "fpts" if ctx.scorer.points else "value", **out}
