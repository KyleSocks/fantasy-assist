"""In-season endpoints (mounted by src/api/routes.py).

GET /api/inseason/streams?date=YYYY-MM-DD&league=&group=F|D|G&top=25&all=false
GET /api/inseason/player/{player_id}?league=&start=&days=10
GET /api/inseason/schedule?start=&days=10&league=
GET/PUT /api/inseason/lists          your roster + free-agent list text
GET /api/inseason/audit?start=&days=9&league=
GET /api/inseason/free-agents?start=&days=9&league=
GET /api/inseason/plan?hold=<id>&adds=1,4&start=&days=9&locked=DET,STL
POST /api/inseason/refresh            re-ingest stats + pull roster/FA from Yahoo

The context (projection + blend + scorer) is cached per league and is
rebuilt when data/hockey.db changes on disk.
"""
from __future__ import annotations

import datetime
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.inseason.evaluate import (
    Context,
    build_context,
    hold_check,
    schedule_grid,
    streams,
)
from src.inseason.roster import audit, free_agent_table, parse_players, roster_plan
from src.paths import DATA_DIR, DB_PATH, DEFAULT_LEAGUE_PATH, LEAGUE_DIR
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


# ── Roster / free-agent lists (data/my_roster.txt, data/fa.txt) ─────────

ROSTER_FILE = "my_roster.txt"
FA_FILE = "fa.txt"


def _read(name: str) -> str:
    path = DATA_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


class Lists(BaseModel):
    roster: str
    free_agents: str


@router.get("/lists")
def get_lists() -> dict:
    return {"roster": _read(ROSTER_FILE), "free_agents": _read(FA_FILE)}


@router.put("/lists")
def put_lists(body: Lists, league: str | None = None) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / ROSTER_FILE).write_text(body.roster, encoding="utf-8")
    (DATA_DIR / FA_FILE).write_text(body.free_agents, encoding="utf-8")
    ctx = _ctx(league)
    _, miss_r = parse_players(ctx, body.roster)
    _, miss_f = parse_players(ctx, body.free_agents)
    return {"saved": True, "unmatched": miss_r + miss_f}


def _lists(ctx):
    roster, miss_r = parse_players(ctx, _read(ROSTER_FILE))
    fas, miss_f = parse_players(ctx, _read(FA_FILE))
    return roster, fas, miss_r + miss_f


@router.get("/audit")
def get_audit(start: str | None = None, days: int = 9,
              league: str | None = None) -> dict:
    ctx = _ctx(league)
    roster, _, missing = _lists(ctx)
    if not roster:
        raise HTTPException(400, f"no roster: add players to data/{ROSTER_FILE}")
    out = audit(ctx, roster, start or _today(), days)
    out.update(as_of=ctx.as_of, league=ctx.league.name, unmatched=missing,
               unit="fpts" if ctx.scorer.points else "z")
    return out


@router.get("/free-agents")
def get_free_agents(start: str | None = None, days: int = 9,
                    league: str | None = None) -> dict:
    ctx = _ctx(league)
    _, fas, missing = _lists(ctx)
    return {"players": free_agent_table(ctx, fas, start or _today(), days),
            "unmatched": missing, "as_of": ctx.as_of}


@router.get("/plan")
def get_plan(hold: int, adds: str = "1,4", start: str | None = None,
             days: int = 9, locked: str = "", league: str | None = None) -> dict:
    ctx = _ctx(league)
    roster, fas, _ = _lists(ctx)
    if hold not in ctx.lines:
        raise HTTPException(404, f"Unknown player id {hold}")
    try:
        budget = [int(x) for x in adds.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, f"adds must look like 1,4 (got '{adds}')")
    lock = {t.strip() for t in locked.split(",") if t.strip()}
    return roster_plan(ctx, roster, fas, hold, start or _today(), days,
                       budget or [0], lock)


@router.get("/leagues")
def get_leagues() -> dict:
    return {"leagues": sorted(p.stem for p in LEAGUE_DIR.glob("*.yaml")),
            "default": DEFAULT_LEAGUE_PATH.stem}


@router.post("/refresh")
def refresh(league: str | None = None) -> dict:
    """Re-ingest season-to-date stats (+ rosters, schedule), then rewrite
    data/my_roster.txt and data/fa.txt from the public Yahoo league pages.
    A Yahoo failure keeps the old lists and is reported, not raised."""
    import time

    from src.inseason.ingest import ingest_current
    from src.inseason.model import load_inseason_config
    from src.inseason.yahoo_public import fetch_lists, render

    t0 = time.monotonic()
    try:
        counts = ingest_current(str(DB_PATH))
    except Exception as e:  # network / API errors surface to the page
        raise HTTPException(502, f"stats ingest failed: {e}")
    _CACHE.clear()
    out = {"ingest": counts, "yahoo": None, "yahoo_error": None}
    y = (load_inseason_config() or {}).get("yahoo") or {}
    if not y.get("league_id") or not y.get("team_id"):
        out["yahoo_error"] = "set yahoo.league_id / team_id in config/inseason.yaml"
    else:
        try:
            roster, fas = fetch_lists(int(y["league_id"]), int(y["team_id"]),
                                      int(y.get("free_agent_pages", 4)))
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            (DATA_DIR / ROSTER_FILE).write_text(
                render(roster, "My roster"), encoding="utf-8")
            (DATA_DIR / FA_FILE).write_text(
                render(fas, "Free agents"), encoding="utf-8")
            ctx = _ctx(league)
            _, miss_r = parse_players(ctx, (DATA_DIR / ROSTER_FILE).read_text("utf-8"))
            _, miss_f = parse_players(ctx, (DATA_DIR / FA_FILE).read_text("utf-8"))
            out["yahoo"] = {"roster": len(roster), "free_agents": len(fas),
                            "unmatched": miss_r + miss_f}
        except Exception as e:
            out["yahoo_error"] = f"Yahoo lists not updated: {e}"
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out
