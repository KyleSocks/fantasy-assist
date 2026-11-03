"""In-season endpoints (mounted by src/api/routes.py).

GET /api/inseason/streams?date=YYYY-MM-DD&league=&group=F|D|G&top=25&all=false
GET /api/inseason/player/{player_id}?league=&start=&days=10
GET /api/inseason/schedule?start=&days=10&league=
GET/PUT /api/inseason/lists          your roster + free-agent list text
GET /api/inseason/audit?start=&days=9&league=
GET /api/inseason/free-agents?start=&days=9&league=
GET /api/inseason/plan?hold=<id>&adds=1,4&start=&days=9&locked=DET,STL
POST /api/inseason/refresh            re-ingest stats + pull roster/FA/league from Yahoo
GET /api/inseason/league?start=&days=21   team strengths + 1-for-1 trades

The context (projection + blend + scorer) is cached per league and is
rebuilt when data/hockey.db changes on disk.
"""
from __future__ import annotations

import datetime
import json
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
from src.inseason.roster import (
    audit,
    free_agent_table,
    parse_list,
    parse_players,
    roster_plan,
)
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
            "players": streams(ctx, d, top, group, likely_available=not all,
                               taken=None if all else _league_taken(ctx),
                               unavailable=frozenset() if all
                               else _unavailable_on(ctx, d)),
            "availability": ("all players" if all else
                             "not rostered in your league" if (DATA_DIR / LEAGUE_FILE).exists()
                             else "outside the rosterable pool (estimate)")}


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
LEAGUE_FILE = "league_rosters.json"


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


def _lists(ctx, inactive: set[int] | None = None,
           available_from: dict[int, str] | None = None,
           fa_out: set[int] | None = None):
    roster, miss_r = parse_players(ctx, _read(ROSTER_FILE), inactive)
    fas, miss_f = parse_players(ctx, _read(FA_FILE), fa_out, available_from)
    return roster, fas, miss_r + miss_f


def _unavailable_on(ctx, date: str) -> set[int]:
    """Free-agent-file players who can't be streamed on ``date``: injured
    (OUT) or still on waivers."""
    pl = parse_list(ctx, _read(FA_FILE))
    return set(pl.inactive) | {p for p, d in pl.available_from.items() if d > date}


def _league_taken(ctx, include_inactive: bool = True) -> set[int] | None:
    """Every player rostered in your league (IR included), if pulled."""
    path = DATA_DIR / LEAGUE_FILE
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    text = "\n".join(f"{p['name']} | {p['team']} | {','.join(p['positions'])}"
                     for t in data["teams"].values() for p in t["players"])
    pl = parse_list(ctx, text)
    return set(pl.active) | (set(pl.inactive) if include_inactive else set())


@router.get("/audit")
def get_audit(start: str | None = None, days: int = 9,
              league: str | None = None) -> dict:
    ctx = _ctx(league)
    inactive: set[int] = set()
    roster, _, missing = _lists(ctx, inactive)
    if not roster:
        raise HTTPException(400, f"no roster: add players to data/{ROSTER_FILE}")
    out = audit(ctx, roster, start or _today(), days, inactive)
    out.update(as_of=ctx.as_of, league=ctx.league.name, unmatched=missing,
               warning=ctx.schedule_warning(),
               unit="fpts" if ctx.scorer.points else "z")
    return out


_FA_CACHE: dict = {}


@router.get("/free-agents")
def get_free_agents(start: str | None = None, days: int = 9,
                    league: str | None = None) -> dict:
    ctx = _ctx(league)
    fa_path = DATA_DIR / FA_FILE
    key = (id(ctx), start or _today(), days,
           os.path.getmtime(fa_path) if fa_path.exists() else 0)
    if _FA_CACHE.get("key") == key:
        return _FA_CACHE["value"]
    pl = parse_list(ctx, _read(FA_FILE))
    # safety net: never list someone rostered in your league (a name clash
    # in the free-agent file must not surface a rostered player)
    taken = _league_taken(ctx, include_inactive=True) or set()
    for bucket in (pl.active, pl.inactive):
        for pid in [p for p in bucket if p in taken]:
            del bucket[pid]
    rows = free_agent_table(ctx, pl, start or _today(), days)
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("FA", "W", "OUT")}
    # names Yahoo lists that aren't in the NHL data (mostly minor-leaguers
    # with no NHL games) are counted, not dumped into the status line
    out = {"players": rows, "counts": counts, "unmatched": pl.missing,
           "unmatched_count": len(pl.missing), "as_of": ctx.as_of}
    _FA_CACHE.update(key=key, value=out)
    return out


@router.get("/plan")
def get_plan(hold: int, adds: str = "1,4", start: str | None = None,
             days: int = 9, locked: str = "", league: str | None = None,
             force: bool = False) -> dict:
    ctx = _ctx(league)
    avail: dict[int, str] = {}
    roster, fas, _ = _lists(ctx, available_from=avail)
    if hold not in ctx.lines:
        raise HTTPException(404, f"Unknown player id {hold}")
    try:
        budget = [int(x) for x in adds.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, f"adds must look like 1,4 (got '{adds}')")
    lock = {t.strip() for t in locked.split(",") if t.strip()}
    return roster_plan(ctx, roster, fas, hold, start or _today(), days,
                       budget or [0], lock, avail, force=force)


@router.get("/leagues")
def get_leagues() -> dict:
    return {"leagues": sorted(p.stem for p in LEAGUE_DIR.glob("*.yaml")),
            "default": DEFAULT_LEAGUE_PATH.stem}


# Refuse a Yahoo refresh whose free-agent list shrank below this share of the
# previous one (a partial page, a wall mid-pagination): keep the old lists.
FA_SHRINK_FLOOR = 0.5
FA_SHRINK_MIN_OLD = 40     # only compare against a list at least this long


def _list_count(path) -> int:
    try:
        return sum(1 for ln in path.read_text("utf-8").splitlines()
                   if ln.strip() and not ln.lstrip().startswith("#"))
    except OSError:
        return 0


def _write_atomic(files: dict) -> None:
    """Write every {path: text} or none: temp files first, then rename."""
    tmps = []
    try:
        for path, text in files.items():
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmps.append((tmp, path))
    except Exception:
        for tmp, _ in tmps:
            tmp.unlink(missing_ok=True)
        raise
    for tmp, path in tmps:
        os.replace(tmp, path)


@router.post("/refresh")
def refresh(league: str | None = None, force: bool = False) -> dict:
    """Re-ingest season-to-date stats (+ rosters, schedule), then rewrite
    data/my_roster.txt, data/fa.txt and data/league_rosters.json from the
    public Yahoo league pages.

    Every Yahoo page is fetched before anything is written, and the three
    files are replaced together. A Yahoo failure -- including a free-agent
    list that shrank below FA_SHRINK_FLOOR of the previous one (``force``
    overrides) -- keeps the old lists and is reported, not raised."""
    import time

    from src.inseason.ingest import ingest_current
    from src.inseason.model import load_inseason_config
    from src.inseason.yahoo_public import fetch_league, fetch_lists, render

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
            stats: dict = {}
            roster, fas = fetch_lists(int(y["league_id"]), int(y["team_id"]),
                                      int(y.get("free_agent_pages", 0)),
                                      stats=stats)
            teams = fetch_league(int(y["league_id"]))
            old = _list_count(DATA_DIR / FA_FILE)
            if (not force and old >= FA_SHRINK_MIN_OLD
                    and len(fas) < FA_SHRINK_FLOOR * old):
                raise ValueError(
                    f"free-agent list shrank from {old} to {len(fas)} players"
                    " -- likely a partial Yahoo page; kept the old lists "
                    "(refresh with force=true to accept it)")
            roster_text = render(roster, "My roster")
            fa_text = render(fas, "Free agents")
            league_text = json.dumps({
                "league_id": int(y["league_id"]), "my_team_id": int(y["team_id"]),
                "fetched": datetime.datetime.now().isoformat(timespec="minutes"),
                "teams": {str(tid): {"name": t["name"], "players": [
                    {"name": p.name, "team": p.team, "positions": list(p.positions),
                     "inactive": p.inactive}
                    for p in t["players"]]} for tid, t in teams.items()},
            }, ensure_ascii=False, indent=1)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _write_atomic({DATA_DIR / ROSTER_FILE: roster_text,
                           DATA_DIR / FA_FILE: fa_text,
                           DATA_DIR / LEAGUE_FILE: league_text})
            ctx = _ctx(league)
            _, miss_r = parse_players(ctx, roster_text)
            _, miss_f = parse_players(ctx, fa_text)
            out["yahoo"] = {"roster": len(roster), "free_agents": len(fas),
                            "teams": len(teams), "unmatched": miss_r + miss_f,
                            "unparsed_rows": stats.get("unparsed", 0)}
        except Exception as e:
            out["yahoo_error"] = f"Yahoo lists not updated: {e}"
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out


_LEAGUE_CACHE: dict[tuple, dict] = {}


@router.get("/league")
def get_league(start: str | None = None, days: int = 21,
               league: str | None = None) -> dict:
    """Every team's lineup strength by position + your 1-for-1 trades.
    Needs data/league_rosters.json (written by Refresh data)."""
    from src.inseason.teams import analyze

    path = DATA_DIR / LEAGUE_FILE
    if not path.exists():
        raise HTTPException(400, "no league rosters yet: press Refresh data")
    ctx = _ctx(league)
    data = json.loads(path.read_text(encoding="utf-8"))
    s = start or _today()
    key = (ctx.league.name, s, days, os.path.getmtime(path), id(ctx))
    if key not in _LEAGUE_CACHE:
        _LEAGUE_CACHE.clear()
        out = analyze(ctx, data, int(data["my_team_id"]), s, days)
        out.update(fetched=data.get("fetched"), as_of=ctx.as_of,
                   unit="fpts" if ctx.scorer.points else "z")
        _LEAGUE_CACHE[key] = out
    return _LEAGUE_CACHE[key]
