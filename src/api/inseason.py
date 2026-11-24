"""In-season endpoints (mounted by src/api/routes.py).

GET /api/inseason/streams?date=YYYY-MM-DD&league=&group=F|D|G&top=25&all=false
GET /api/inseason/player/{player_id}?league=&start=&days=10
GET /api/inseason/schedule?start=&days=10&league=
GET/PUT /api/inseason/lists?league=  your roster + free-agent list text
GET /api/inseason/audit?start=&days=9&league=
GET /api/inseason/free-agents?start=&days=9&league=
GET /api/inseason/plan?hold=<id>&adds=1,4&start=&days=9&locked=DET,STL
POST /api/inseason/refresh            re-ingest stats + pull roster/FA/league from Yahoo
GET /api/inseason/league?start=&days=21   team strengths + 1-for-1 trades
GET /api/inseason/partner-trades?partner=<team id>&give=ids&get=ids   package deals with one team
GET /api/inseason/moves?days=30&league=   roster changes for every team (inseason_moves.py)
GET /api/inseason/leagues             league configs + the Yahoo league each follows
GET /api/inseason/matchup?league=&team=&start=   this week's head-to-head (inseason_matchup.py)
GET /api/inseason/recap?league=      last week in review + next week (inseason_recap.py)
GET/PUT /api/inseason/settings       settings gear: changes to config/inseason.yaml (inseason_settings.py)
GET /api/inseason/fallout             injured regulars and who took their minutes (inseason_injuries.py)
GET /api/inseason/goalie-grid         every team's games, likely starters, graded (inseason_injuries.py)

Every endpoint takes ?league=<config stem>. A league with its own ``yahoo:``
block reads and writes its files under data/leagues/<stem>/; the others share
data/ (src/inseason/leagues.py).

The context (projection + blend + scorer) is cached per league and is
rebuilt when data/hockey.db changes on disk.
"""
from __future__ import annotations

import datetime
import itertools
import json
import os
import threading

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.cache import config_stamp, db_stamp
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
from src.inseason import leagues as store
from src.inseason.leagues import ALERTS_FILE, FA_FILE, LEAGUE_FILE, MARKET_FILE, MARKET_HISTORY_FILE, MATCHUPS_FILE, MOVES_FILE, ROSTER_FILE, STANDINGS_FILE  # noqa: F401
from src.league_files import league_path, league_paths
from src.paths import DATA_DIR, DB_PATH, DEFAULT_LEAGUE_PATH, LEAGUE_DIR
from src.valuation.league import LeagueConfig, load_league

router = APIRouter(prefix="/api/inseason")
_CACHE: dict[str, tuple[tuple, Context]] = {}
# Every Context built gets the next number. Response caches key on it, not
# on id(ctx): CPython reuses a freed object's id, so a rebuilt context
# could otherwise hit a cache entry computed from the old data.
_GEN = itertools.count(1)


def _gen(ctx: Context) -> int:
    return ctx.__dict__.setdefault("_cache_gen", next(_GEN))


def _ctx(league: str | None) -> Context:
    name = league or DEFAULT_LEAGUE_PATH.stem
    path = league_path(name, LEAGUE_DIR)
    if not path.exists():
        raise HTTPException(404, f"Unknown league '{name}'")
    if not DB_PATH.exists():
        raise HTTPException(500, "Database not ready: run "
                                 "uv run python -m src.inseason.ingest")
    # DB incl. its WAL (a commit may not touch hockey.db until checkpoint)
    # + config/ (inseason.yaml, league files are editable live)
    from src.cache import file_stamp
    from src.inseason.settings import SETTINGS_PATH
    stamp = (str(DB_PATH), db_stamp(DB_PATH), config_stamp(), file_stamp(SETTINGS_PATH))
    hit = _CACHE.get(name)
    if hit and hit[0] == stamp:
        return _with_waivers(hit[1])
    try:
        ctx = build_context(str(DB_PATH), load_league(str(path)))
    except ValueError as e:
        raise HTTPException(500, str(e))
    _gen(ctx)
    ctx.__dict__["_key"] = name       # config stem: picks the data folder
    _CACHE[name] = (stamp, ctx)
    return _with_waivers(ctx)


def _league_cfg(name: str | None) -> tuple[str, LeagueConfig | None]:
    """(config stem, parsed config or None if the file is missing/invalid)
    without building a context -- for endpoints that only need file paths."""
    key = name or DEFAULT_LEAGUE_PATH.stem
    path = league_path(key, LEAGUE_DIR)
    try:
        return key, load_league(str(path))
    except (OSError, ValueError):
        return key, None


def _dir(ctx=None):
    """Folder with this league's roster / free-agent / league files."""
    key = getattr(ctx, "__dict__", {}).get("_key")
    return store.data_dir(DATA_DIR, key, getattr(ctx, "league", None))


def _with_waivers(ctx: Context) -> Context:
    """Replacement level from your league's real free agents (fa.txt,
    written by Refresh), re-applied whenever that file changes."""
    path = _dir(ctx) / FA_FILE
    mtime = os.path.getmtime(path) if path.exists() else None
    if ctx.__dict__.get("_fa_mtime", "unset") != mtime:
        from src.inseason.evaluate import apply_waiver_replacement
        active = parse_list(ctx, _read(FA_FILE, ctx)).active if mtime else ()
        apply_waiver_replacement(ctx, active)
        ctx.__dict__["_fa_mtime"] = mtime
    # % rostered trend (market_history.json), re-read when it changes
    hpath = _dir(ctx) / MARKET_HISTORY_FILE
    hm = os.path.getmtime(hpath) if hpath.exists() else None
    if ctx.__dict__.get("_ros_mtime", "unset") != hm:
        from src.inseason import rostered
        hist = store.read_json(hpath) if hm else None
        ctx.__dict__["_rostered"] = {
            days: rostered.by_player(ctx.lines, rostered.changes(hist, days))
            for days in (3, 7)}
        ctx.__dict__["_ros_mtime"] = hm
    # standings -> playoff odds -> "Playing for" when trade_focus is auto
    spath = _dir(ctx) / STANDINGS_FILE
    sm = os.path.getmtime(spath) if spath.exists() else None
    if ctx.__dict__.get("_st_mtime", "unset") != sm:
        ctx.__dict__["_standings"] = _standings(ctx, store.read_json(spath) if sm else None)
        ctx.__dict__["_auto_focus"] = (ctx.__dict__["_standings"] or {}).get("focus")
        ctx.__dict__["_st_mtime"] = sm
    return ctx


def _standings(ctx: Context, st: dict | None) -> dict | None:
    """Standings with playoff odds and the focus they imply for your team
    (your team = my_team_id of the stored league rosters)."""
    if not st:
        return None
    from src.inseason import standings as S
    cfg = ctx.icfg.get("standings") or {}
    o = S.odds(st, cfg)
    snap = store.read_json(_dir(ctx) / LEAGUE_FILE) or {}
    me = snap.get("my_team_id")
    me = int(me) if me is not None else None
    mine = o["teams"].get(me, {}).get("odds") if me is not None else None
    return {"fetched": st.get("fetched"), "playoffs": st.get("playoffs"),
            "weeks_played": o["weeks_played"], "weeks_left": o["weeks_left"],
            "spots": o["spots"], "my_team_id": me, "my_odds": mine,
            "focus": S.focus_for(mine, cfg) if mine is not None else None,
            "teams": [{**t, **o["teams"].get(t["team_id"], {}), "mine": t["team_id"] == me}
                      for t in st.get("teams") or []]}


def _today() -> str:
    return datetime.date.today().isoformat()


# ── Input limits ─────────────────────────────────────────────────────────
# The app has no login, so every query parameter is bounded: a typo'd or
# hostile ?days=100000 would otherwise pin the CPU for minutes.
MAX_DAYS = 60
MAX_TOP = 200
MAX_LIST_CHARS = 200_000         # ~4000 lines; Yahoo's whole player pool fits
LEAGUE_NAME = r"^[A-Za-z0-9_-]{0,64}$"     # "" = default league

Days = Annotated[int, Query(ge=1, le=MAX_DAYS)]
LeagueName = Annotated[str | None, Query(pattern=LEAGUE_NAME)]
DateStr = Annotated[str | None, Query(max_length=10)]


def _day(value: str | None) -> str:
    """A YYYY-MM-DD query value (default today), validated."""
    if not value:                 # missing or "" (an empty date input)
        return _today()
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(422, f"expected a date like 2026-11-09, got '{value}'")


@router.get("/streams")
def get_streams(date: DateStr = None, league: LeagueName = None,
                group: Literal["F", "D", "G", "S"] | None = None,
                top: Annotated[int, Query(ge=1, le=MAX_TOP)] = 25,
                all: bool = False) -> dict:
    ctx = _ctx(league)
    d = _day(date)
    rows = streams(ctx, d, top, group, likely_available=not all,
                   taken=None if all else _league_taken(ctx),
                   unavailable=frozenset() if all else _unavailable_on(ctx, d))
    _add_lineup_fit(ctx, rows, d)
    return {"league": ctx.league.name, "date": d, "as_of": ctx.as_of,
            "nhl_games": ctx.games_on(d),
            "unit": "fpts" if ctx.scorer.points else "z",
            "players": rows,
            "availability": ("all players" if all else
                             "not rostered in your league" if (_dir(ctx) / LEAGUE_FILE).exists()
                             else "outside the rosterable pool (estimate)")}


def _add_lineup_fit(ctx, rows: list[dict], date: str) -> None:
    """``adds``: what each stream adds to YOUR lineup on ``date`` -- the
    best lineup with him minus without, re-arranging multi-position
    starters, so a C/LW counts when a LW/RW can slide over to the open RW.
    None when there's no roster file; 0 for players already on it (only
    possible with all=true) -- they're in the base lineup, so re-adding them
    would count the same player twice."""
    from src.inseason.lineup import best_lineup
    from src.inseason.plan import nhl_eligibility
    from src.inseason.roster import active_slots, game_values
    try:
        roster, fas, _ = _lists(ctx, set())
    except Exception:
        roster = {}
    if not roster:
        for r in rows:
            r["adds"] = None
        return
    slots = active_slots(ctx)
    base = []
    for p, e in roster.items():
        v = game_values(ctx, p, [date]).get(date, 0.0)
        if v > 0:
            base.append((p, e, v))
    total, _ = best_lineup(base, slots)
    for r in rows:
        pid = r["player_id"]
        elig = fas.get(pid) or nhl_eligibility(ctx.lines[pid].position)
        r["elig"] = sorted(elig)
        if pid in roster:
            r["elig"] = sorted(roster[pid])
            r["adds"] = 0.0
            continue
        with_him, _ = best_lineup(base + [(pid, elig, r["value"])], slots)
        r["adds"] = max(with_him - total, 0.0)


def _injury_status(ctx, pid: int, start: str) -> dict | None:
    """Listed injured on a roster in this league (or on the free-agent
    list): his tag, the return date the trade tools use, and whether you
    set it. None when he's healthy."""
    from src.inseason.teams import injury_returns, team_tags, trade_roster
    snap = store.read_json(_dir(ctx) / LEAGUE_FILE) or {}
    for t in (snap.get("teams") or {}).values():
        roster, out = trade_roster(ctx, t.get("players") or [], start)
        tag = team_tags(ctx, t.get("players") or []).get(pid)
        if pid in roster and pid not in out and tag:
            # tagged (DTD, O...) but not on IR: counted as healthy unless
            # you set a date
            return {"owner": t.get("name"), "tag": tag, "back": None, "yours": False}
        if pid in out:
            return {"owner": t.get("name"), "tag": tag,
                    "back": out[pid],
                    "yours": pid in injury_returns()}
    return None


def _projection_inputs(ctx, pid: int, start: str) -> dict:
    """What the projection uses beyond the season blend: goals from
    shooting %, deployment factors, back-to-back nights (goalies)."""
    from src.inseason.evaluate import goalie_share
    ln = ctx.lines[pid]
    out: dict = {"sh_pct": ln.extra.get("sh_pct"), "deploy": None, "b2b": [], "onice": None}
    on = ln.extra.get("onice")
    if on:
        out["onice"] = {**on, "value_delta": ctx.scorer.value_stats(
            pid, ln.position, {"assists": on["delta_pg"]}) - ctx.scorer.value_stats(pid, ln.position, {})}
    dep = ln.extra.get("deploy")
    if dep:
        val = lambda st: ctx.scorer.value_stats(pid, ln.position, st)  # noqa: E731
        out["deploy"] = {**dep, "value_delta": val(dep["delta"]) - val({})}
    if ln.is_goalie and ln.start_share > 0:
        end = (datetime.date.fromisoformat(start) + datetime.timedelta(days=13)).isoformat()
        out["b2b"] = [{"date": d, "share": goalie_share(ctx, ln, d)}
                      for d, _ in ctx.team_games(ln.team, start, end)
                      if abs(goalie_share(ctx, ln, d) - ln.start_share) > 1e-9]
    return out


@router.get("/player/{player_id}")
def get_player(player_id: int, league: LeagueName = None,
               start: DateStr = None, days: Days = 10) -> dict:
    ctx = _ctx(league)
    if player_id not in ctx.lines:
        raise HTTPException(404, f"Unknown player id {player_id}")
    h = hold_check(ctx, player_id, _day(start), days)
    ln = h.pop("line")
    from src.inseason.trend import trend
    h.update(trend=trend(ctx, ln), trend_days=ctx.icfg.get("trend_days", 21))
    h.update(name=ln.name, team=ln.team, position=ln.position, gp=ln.gp,
             prior=ln.prior, observed=ln.observed, blended=ln.blended,
             start_share=ln.start_share if ln.is_goalie else None,
             toi_pg_prior=ln.toi_pg_prior, toi_pg_obs=ln.toi_pg_obs,
             as_of=ctx.as_of, league=ctx.league.name,
             unit="fpts" if ctx.scorer.points else "z")
    from src.inseason.roster import luck_json, role_json
    from src.inseason.roster import _rostered
    from src.inseason.trend import windows_json
    h.update(injured=_injury_status(ctx, player_id, _day(start)))
    h.update(windows=windows_json(ctx, ln), rostered=_rostered(ctx, player_id),
             adv=ln.extra.get("adv"))
    h.update(luck=luck_json(ctx, player_id), role=role_json(ctx, player_id),
             inputs=_projection_inputs(ctx, player_id, _day(start)))
    return h


@router.get("/schedule")
def get_schedule(start: DateStr = None, days: Days = 10,
                 league: LeagueName = None) -> dict:
    ctx = _ctx(league)
    s = _day(start)
    return {"start": s, "days": days, "teams": schedule_grid(ctx, s, days)}


# ── Roster / free-agent lists (my_roster.txt, fa.txt in the league's folder) ──


def _read(name: str, ctx=None) -> str:
    path = _dir(ctx) / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


class Lists(BaseModel):
    roster: str = Field(max_length=MAX_LIST_CHARS)
    free_agents: str = Field(max_length=MAX_LIST_CHARS)


@router.get("/lists")
def get_lists(league: LeagueName = None) -> dict:
    key, cfg = _league_cfg(league)
    folder = store.data_dir(DATA_DIR, key, cfg)

    def text(name: str) -> str:
        return (folder / name).read_text(encoding="utf-8") if (folder / name).exists() else ""
    return {"roster": text(ROSTER_FILE), "free_agents": text(FA_FILE),
            "folder": _shown(folder)}


def _shown(folder) -> str:
    """The folder as the page names it: data or data/leagues/<key>."""
    try:
        return "data" + "".join(f"/{p}" for p in folder.relative_to(DATA_DIR).parts)
    except ValueError:
        return str(folder)


@router.put("/lists")
def put_lists(body: Lists, league: LeagueName = None) -> dict:
    ctx = _ctx(league)              # unknown league: 404 before writing
    folder = _dir(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    _write_atomic({folder / ROSTER_FILE: body.roster,
                   folder / FA_FILE: body.free_agents})
    _, miss_r = parse_players(ctx, body.roster)
    _, miss_f = parse_players(ctx, body.free_agents)
    return {"saved": True, "unmatched": miss_r + miss_f}


def _lists(ctx, inactive: set[int] | None = None,
           available_from: dict[int, str] | None = None,
           fa_out: set[int] | None = None):
    roster, miss_r = parse_players(ctx, _read(ROSTER_FILE, ctx), inactive)
    fas, miss_f = parse_players(ctx, _read(FA_FILE, ctx), fa_out, available_from)
    return roster, fas, miss_r + miss_f


def _unavailable_on(ctx, date: str) -> set[int]:
    """Free-agent-file players who can't be streamed on ``date``: injured
    (OUT) or still on waivers."""
    pl = parse_list(ctx, _read(FA_FILE, ctx))
    return set(pl.inactive) | {p for p, d in pl.available_from.items() if d > date}


def _league_taken(ctx, include_inactive: bool = True) -> set[int] | None:
    """Every player rostered in your league (IR included), if pulled."""
    path = _dir(ctx) / LEAGUE_FILE
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    text = "\n".join(f"{p['name']} | {p['team']} | {','.join(p['positions'])}"
                     for t in data["teams"].values() for p in t["players"])
    pl = parse_list(ctx, text)
    return set(pl.active) | (set(pl.inactive) if include_inactive else set())


@router.get("/audit")
def get_audit(start: DateStr = None, days: Days = 9,
              league: LeagueName = None,
              team: Annotated[int | None, Query(ge=1, le=99)] = None) -> dict:
    """Roster audit. ``team`` = a Yahoo team id from league_rosters.json to
    see any team's roster the same way (verdicts against the same free
    agents, its own nightly lineup); omitted or your id = your roster
    (my_roster.txt). ``teams`` lists every team for the page's picker."""
    ctx = _ctx(league)
    snap = store.read_json(_dir(ctx) / LEAGUE_FILE) or {}
    mine = snap.get("my_team_id")
    teams = [{"id": int(t), "name": v.get("name") or f"Team {t}", "mine": int(t) == mine}
             for t, v in (snap.get("teams") or {}).items()]
    inactive: set[int] = set()
    if team is not None and team != mine:
        entry = (snap.get("teams") or {}).get(str(team))
        if entry is None:
            raise HTTPException(404, f"no team {team} in this league's rosters: "
                                     "press Refresh data")
        text = "\n".join(f"{p['name']} | {p.get('team', '')} | {','.join(p.get('positions') or [])}"
                         + (" | IR" if p.get("inactive") else "")
                         for p in entry.get("players") or [])
        roster, missing = parse_players(ctx, text, inactive)
        shown = {"id": team, "name": entry.get("name") or f"Team {team}", "mine": False}
        if not roster:
            raise HTTPException(400, f"no players read for {shown['name']}: press Refresh data")
    else:
        roster, _, missing = _lists(ctx, inactive)
        if not roster:
            raise HTTPException(400, f"no roster: press Refresh data, or add players "
                                     f"to {_shown(_dir(ctx))}/{ROSTER_FILE} on the Lists tab")
        shown = next((t for t in teams if t["mine"]), {"id": mine, "name": None, "mine": True})
    out = audit(ctx, roster, _day(start), days, inactive)
    out.update(trend_days=ctx.icfg.get("trend_days", 21), team=shown,
               teams=sorted(teams, key=lambda t: (not t["mine"], t["name"].lower())))
    out.update(as_of=ctx.as_of, league=ctx.league.name, unmatched=missing,
               warning=ctx.schedule_warning(),
               unit="fpts" if ctx.scorer.points else "z")
    return out


_FA_CACHE: dict = {}


@router.get("/free-agents")
def get_free_agents(start: DateStr = None, days: Days = 9,
                    league: LeagueName = None) -> dict:
    ctx = _ctx(league)
    fa_path = _dir(ctx) / FA_FILE
    hist = _dir(ctx) / MARKET_HISTORY_FILE
    key = (_gen(ctx), _day(start), days,
           os.path.getmtime(fa_path) if fa_path.exists() else 0,
           os.path.getmtime(hist) if hist.exists() else 0, str(_dir(ctx)))
    if _FA_CACHE.get("key") == key:
        return _FA_CACHE["value"]
    pl = parse_list(ctx, _read(FA_FILE, ctx))
    # safety net: never list someone rostered in your league (a name clash
    # in the free-agent file must not surface a rostered player)
    taken = _league_taken(ctx, include_inactive=True) or set()
    for bucket in (pl.active, pl.inactive):
        for pid in [p for p in bucket if p in taken]:
            del bucket[pid]
    rows = free_agent_table(ctx, pl, _day(start), days)
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("FA", "W", "OUT")}
    # names Yahoo lists that aren't in the NHL data (mostly minor-leaguers
    # with no NHL games) are counted, not dumped into the status line
    out = {"players": rows, "counts": counts, "unmatched": pl.missing,
           "unmatched_count": len(pl.missing), "as_of": ctx.as_of,
           "trend_days": ctx.icfg.get("trend_days", 21)}
    _FA_CACHE.update(key=key, value=out)
    return out


@router.get("/plan")
def get_plan(hold: int, adds: Annotated[str, Query(max_length=40)] = "1,4", start: DateStr = None,
             days: Days = 9, locked: Annotated[str, Query(max_length=200)] = "", league: LeagueName = None,
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
    if any(not 0 <= n <= 20 for n in budget):
        raise HTTPException(422, "adds per week must be between 0 and 20")
    lock = {t.strip() for t in locked.split(",") if t.strip()}
    return roster_plan(ctx, roster, fas, hold, _day(start), days,
                       budget or [0], lock, avail, force=force)


@router.get("/alerts")
def get_alerts(league: LeagueName = None,
               days: Annotated[int, Query(ge=1, le=60)] = 7) -> dict:
    """Alerts about your team from the last ``days`` days, newest first
    (src/inseason/alerts.py)."""
    key, cfg = _league_cfg(league)
    log = store.read_json(store.data_dir(DATA_DIR, key, cfg) / ALERTS_FILE) or {}
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    items = [a for a in log.get("items") or [] if (a.get("at") or "") >= cutoff]
    return {"alerts": list(reversed(items))}


@router.get("/leagues")
def get_leagues() -> dict:
    """Every league config, plus what the picker needs to know about each:
    scoring, the Yahoo league it follows (its own or the default) and your
    team's name there. A config that doesn't parse is listed with ``error``."""
    from src.inseason.model import load_inseason_config

    icfg = load_inseason_config() or {}
    names = [p.stem for p in league_paths(LEAGUE_DIR)]
    info: dict[str, dict] = {}
    for name in names:
        try:
            cfg = load_league(str(league_path(name, LEAGUE_DIR)))
            info[name] = store.describe(name, cfg, icfg, DATA_DIR)
        except (OSError, ValueError) as e:
            info[name] = {"error": str(e).splitlines()[0][:200]}
    return {"leagues": names, "default": DEFAULT_LEAGUE_PATH.stem, "info": info}


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


_REFRESH_LOCK = threading.Lock()


@router.post("/refresh")
def refresh(league: LeagueName = None, force: bool = False,
            strict: bool = False):
    """Re-ingest season-to-date stats (+ rosters, schedule), then rewrite
    my_roster.txt, fa.txt and league_rosters.json from the public Yahoo
    league pages, and log what changed on every team (roster_moves.json).

    ``league`` given (the page's button): that league's Yahoo league only.
    Omitted (the timer): the default league and every league config with
    its own ``yahoo:`` block. See src/api/inseason_refresh.py.

    Every Yahoo page is fetched before anything is written, and the three
    files are replaced together. A Yahoo failure -- including a free-agent
    list that shrank below FA_SHRINK_FLOOR of the previous one (``force``
    overrides) -- keeps the old lists and is reported, not raised.
    A refresh already running answers 409.

    ``strict`` (the systemd timer): a Yahoo failure answers 502 with the
    same JSON body, so `curl --fail-with-body` fails the unit while still
    logging what happened. The page's button leaves it off and shows the
    error inline."""
    # one refresh at a time: the timer and a button press must not run two
    # NHL ingests into the same DB (or interleave the Yahoo file writes)
    if not _REFRESH_LOCK.acquire(blocking=False):
        raise HTTPException(409, "a refresh is already running; try again "
                                 "in a minute")
    try:
        out = _refresh(league, force)
    finally:
        _REFRESH_LOCK.release()
    if strict and out.get("yahoo_error"):
        return JSONResponse(out, status_code=502)
    return out


def _refresh(league: str | None, force: bool) -> dict:
    from src.api import inseason_refresh
    return inseason_refresh.run(league, force)


# league tab endpoints register on this router
from src.api import inseason_league  # noqa: E402,F401
from src.api import inseason_player  # noqa: E402,F401
from src.api import inseason_moves  # noqa: E402,F401
from src.api import inseason_matchup  # noqa: E402,F401
from src.api import inseason_recap  # noqa: E402,F401
from src.api import inseason_settings  # noqa: E402,F401
from src.api import inseason_injuries  # noqa: E402,F401
