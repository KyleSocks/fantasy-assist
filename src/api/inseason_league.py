"""League-tab endpoints, on the same /api/inseason router (imported at the
bottom of src/api/inseason.py):

GET /api/inseason/league?start=&days=21   team strengths + 1-for-1 trades
GET /api/inseason/trade-options?give=<id>  shop one player, 1-for-1
GET /api/inseason/partner-trades?partner=<team id>&give=ids&get=ids&size=2
    every 1-1 .. 2-2 package with one team, plus one offer scored

Paths and the context cache are read through the inseason module at call
time, so tests that monkeypatch it (DATA_DIR, DB_PATH) still apply.
"""
from __future__ import annotations

import json
import os
from typing import Annotated

from fastapi import HTTPException, Query

from src.api import inseason as ins
from src.api.inseason import DateStr, Days, LeagueName
from src.inseason.roster import parse_list

_LEAGUE_CACHE: dict[tuple, dict] = {}


@ins.router.get("/league")
def get_league(start: DateStr = None, days: Days = 21,
               league: LeagueName = None) -> dict:
    """Every team's lineup strength by position + your 1-for-1 trades.
    Needs data/league_rosters.json (written by Refresh data)."""
    from src.inseason.teams import analyze

    path = ins.DATA_DIR / ins.LEAGUE_FILE
    if not path.exists():
        raise HTTPException(400, "no league rosters yet: press Refresh data")
    ctx = ins._ctx(league)
    data = json.loads(path.read_text(encoding="utf-8"))
    s = ins._day(start)
    key = (ctx.league.name, s, days, os.path.getmtime(path), ins._gen(ctx))
    if key not in _LEAGUE_CACHE:
        _LEAGUE_CACHE.clear()
        out = analyze(ctx, data, int(data["my_team_id"]), s, days)
        out.update(fetched=data.get("fetched"), as_of=ctx.as_of,
                   unit="fpts" if ctx.scorer.points else "z")
        _LEAGUE_CACHE[key] = out
    return _LEAGUE_CACHE[key]


@ins.router.get("/trade-options")
def get_trade_options(give: int, start: DateStr = None, days: Days = 21,
                      league: LeagueName = None) -> dict:
    """1-for-1 deals for one of your players, from ones the market sees as
    favouring you to ones favouring the other side (teams.trade_options)."""
    from src.inseason.teams import Market, trade_options

    path = ins.DATA_DIR / ins.LEAGUE_FILE
    if not path.exists():
        raise HTTPException(400, "no league rosters yet: press Refresh data")
    ctx = ins._ctx(league)
    if give not in ctx.lines:
        raise HTTPException(404, f"Unknown player id {give}")
    data = json.loads(path.read_text(encoding="utf-8"))
    mpath = ins.DATA_DIR / ins.MARKET_FILE
    mdata = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else None
    market = Market(ctx, mdata["players"]) if mdata else None
    try:
        out = trade_options(ctx, data, int(data["my_team_id"]), give,
                            ins._day(start), days, market=market)
    except ValueError as e:
        raise HTTPException(400, str(e))
    out.update(as_of=ctx.as_of, unit="fpts" if ctx.scorer.points else "z",
               market_fetched=mdata.get("fetched") if mdata else None)
    return out


IdList = Annotated[str, Query(max_length=40, pattern=r"^[0-9,]*$")]


def _ids(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x]


@ins.router.get("/partner-trades")
def get_partner_trades(partner: int, give: IdList = "", get: IdList = "",
                       start: DateStr = None, days: Days = 21,
                       size: Annotated[int, Query(ge=1, le=2)] = 2,
                       league: LeagueName = None) -> dict:
    """Every 1-1 / 2-1 / 1-2 / 2-2 deal with one team (inseason.partner);
    ``give`` / ``get`` (comma-separated ids): players every deal must
    include; with both, that exact deal is scored on its own as well."""
    from src.inseason.partner import partner_trades
    from src.inseason.teams import Market

    path = ins.DATA_DIR / ins.LEAGUE_FILE
    if not path.exists():
        raise HTTPException(400, "no league rosters yet: press Refresh data")
    ctx = ins._ctx(league)
    data = json.loads(path.read_text(encoding="utf-8"))
    mpath = ins.DATA_DIR / ins.MARKET_FILE
    mdata = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else None
    market = Market(ctx, mdata["players"]) if mdata else None
    # ticked players: every deal listed includes them; with both sides
    # ticked, that exact deal is also scored on its own
    req = (_ids(give), _ids(get))
    offer = req if req[0] and req[1] else None
    try:
        out = partner_trades(ctx, data, int(data["my_team_id"]), partner,
                             ins._day(start), days, market=market, max_size=size,
                             offer=offer, require=req,
                             free_agents=parse_list(ctx, ins._read(ins.FA_FILE)).active)
    except ValueError as e:
        raise HTTPException(400, str(e))
    out.update(as_of=ctx.as_of, unit="fpts" if ctx.scorer.points else "z",
               fetched=data.get("fetched"),
               market_fetched=mdata.get("fetched") if mdata else None)
    return out
