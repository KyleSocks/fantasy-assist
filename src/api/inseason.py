"""In-season endpoints (mounted by src/api/routes.py).

GET /api/inseason/streams?date=YYYY-MM-DD&league=&group=F|D|G&top=25&all=false
GET /api/inseason/player/{player_id}?league=&start=&days=10
GET /api/inseason/schedule?start=&days=10&league=

The context (projection + blend + scorer) is cached per league and is
rebuilt when data/hockey.db changes on disk.
"""
from __future__ import annotations

import datetime
import os

from fastapi import APIRouter, HTTPException

from src.inseason.evaluate import (
    Context,
    build_context,
    hold_check,
    schedule_grid,
    streams,
)
from src.paths import DB_PATH, DEFAULT_LEAGUE_PATH, LEAGUE_DIR
from src.valuation.league import load_league

router = APIRouter(prefix="/api/inseason")
_CACHE: dict[str, tuple[float, Context]] = {}


def _ctx(league: str | None) -> Context:
    name = league or DEFAULT_LEAGUE_PATH.stem
    path = LEAGUE_DIR / f"{name}.yaml"
    if not path.exists():
        raise HTTPException(404, f"Unknown league '{name}'")
    if not DB_PATH.exists():
        raise HTTPException(500, "Database not ready: run "
                                 "uv run python -m src.inseason.ingest")
    mtime = os.path.getmtime(DB_PATH)
    hit = _CACHE.get(name)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        ctx = build_context(str(DB_PATH), load_league(str(path)))
    except ValueError as e:
        raise HTTPException(500, str(e))
    _CACHE[name] = (mtime, ctx)
    return ctx


def _today() -> str:
    return datetime.date.today().isoformat()


@router.get("/streams")
def get_streams(date: str | None = None, league: str | None = None,
                group: str | None = None, top: int = 25,
                all: bool = False) -> dict:
    ctx = _ctx(league)
    d = date or _today()
    return {"league": ctx.league.name, "date": d, "as_of": ctx.as_of,
            "nhl_games": ctx.games_on(d),
            "unit": "fpts" if ctx.scorer.points else "z",
            "players": streams(ctx, d, top, group, likely_available=not all)}


@router.get("/player/{player_id}")
def get_player(player_id: int, league: str | None = None,
               start: str | None = None, days: int = 10) -> dict:
    ctx = _ctx(league)
    if player_id not in ctx.lines:
        raise HTTPException(404, f"Unknown player id {player_id}")
    h = hold_check(ctx, player_id, start or _today(), days)
    ln = h.pop("line")
    h.update(prior=ln.prior, observed=ln.observed, blended=ln.blended,
             start_share=ln.start_share if ln.is_goalie else None,
             toi_pg_prior=ln.toi_pg_prior, toi_pg_obs=ln.toi_pg_obs,
             as_of=ctx.as_of, league=ctx.league.name,
             unit="fpts" if ctx.scorer.points else "z")
    return h


@router.get("/schedule")
def get_schedule(start: str | None = None, days: int = 10,
                 league: str | None = None) -> dict:
    ctx = _ctx(league)
    s = start or _today()
    return {"start": s, "days": days, "teams": schedule_grid(ctx, s, days)}
