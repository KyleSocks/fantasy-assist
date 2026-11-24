"""Live draft sync endpoints (parsing: src/draft/live.py).

- POST /api/draft/live            the browser extension: the Yahoo draft
                                  room is open (heartbeat) and, when it has
                                  one, the draft-results page HTML. Needs the
                                  X-Draft-Token header (the extension is a
                                  different origin, so the usual same-origin
                                  write guard can't apply -- the token does).
- GET  /api/draft/live?league=    the board: picks so far, matched to board
                                  players, and whether the room is open.
- POST /api/draft/live/pull?league=  the server reads a PUBLIC league's
                                  draft-results page itself (no extension).
- GET  /api/draft/token           the token to paste into the extension.

Each Yahoo league's latest state is kept in data/draft_live/<league id>.json
(mock drafts and rooms without a league id: room.json); the last posted
page is saved next to it (.last.html) so a parser miss can be diagnosed.
"""
from __future__ import annotations

import datetime
import hmac
import json
import re
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from src.draft.live import (
    Pick,
    Roster,
    board_log,
    first_round_teams,
    match_picks,
    parse_draft_results,
    resolve_teams,
)
from src.draft.yahoo_settings import (
    differences,
    league_teams,
    my_team_from,
    parse_settings,
)
from src.league_files import league_path, league_paths
from src.paths import DATA_DIR, DB_PATH, IMPORTED_LEAGUE_DIR, LEAGUE_DIR

router = APIRouter(prefix="/api/draft")
LIVE_DIR = DATA_DIR / "draft_live"
TOKEN_PATH = DATA_DIR / "draft_token.txt"
CONNECTED_S = 20          # room counts as open this long after a heartbeat
FOLLOW_ANY_H = 12         # a board with no Yahoo link follows any room this fresh
LIVE_WINS_S = 120         # a room posting this recently beats the league's own
MAX_HTML = 4_000_000
LeagueName = Annotated[str | None, Query(pattern=r"^[A-Za-z0-9_-]{0,64}$")]


# ── token ─────────────────────────────────────────────────────────────

def draft_token(path: Path | None = None) -> str:
    path = path or TOKEN_PATH
    try:
        tok = path.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tok + "\n", encoding="utf-8")
    return tok


def token_valid(sent: str | None, path: Path | None = None) -> bool:
    return bool(sent) and hmac.compare_digest(sent.strip(), draft_token(path))


# ── storage ───────────────────────────────────────────────────────────

def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _room_key(league_id: int | None) -> str:
    return str(league_id) if league_id else "room"


def season_frame(db_path: Path | None = None,
                 today: datetime.date | None = None) -> tuple[str, int]:
    """(first regular-season game date, season end year) of the season in
    progress or about to start (September on), from the schedule table;
    Oct 1 when the schedule isn't loaded."""
    import sqlite3
    today = today or datetime.date.today()
    y = today.year if today.month >= 9 else today.year - 1
    start = f"{y}-10-01"
    try:
        conn = sqlite3.connect(f"file:{db_path or DB_PATH}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT MIN(game_date) FROM schedule WHERE game_type = 2 "
                "AND season_id = ?", (y * 10000 + y + 1,)).fetchone()
        finally:
            conn.close()
        start = row[0] or start
    except sqlite3.Error:
        pass
    return start, y + 1


def read_pages(state: dict, league_id: int | None, league_html: str | None,
               settings_html: str | None, mock: bool = False,
               my_team_id: int | None = None, page: str | None = None) -> None:
    """Teams, your team and the scoring settings from the league's own
    pages, into ``state`` (kept from earlier posts when a page is missing)."""
    if mock:
        state["mock"] = True
    if state.get("mock") and page and not state.get("teams"):
        teams = first_round_teams(page)
        if teams:
            state["teams"] = {str(k): v for k, v in teams.items()}
    if my_team_id:
        state["my_team_id"] = my_team_id
    if league_html and league_id:
        teams = league_teams(league_html, league_id)
        if teams:
            state["teams"] = {str(k): v for k, v in teams.items()}
        mine = my_team_from(league_html, league_id)
        if mine:
            state["my_team_id"] = mine
    if settings_html:
        start, end_year = season_frame()
        name = "yahoo_mock" if state.get("mock") else f"yahoo_{league_id or 'room'}"
        try:
            cfg, notes, yname = parse_settings(settings_html, name=name,
                                               season_start=start,
                                               season_end_year=end_year)
            state["settings"] = {"config": cfg, "notes": notes, "name": yname}
            state.pop("settings_error", None)
        except ValueError as e:
            state["settings_error"] = str(e)


def save_post(room: str, url: str | None, page: str | None,
              live_dir: Path | None = None, now: datetime.datetime | None = None,
              source: str = "extension", *, league_id: int | None = None,
              league_html: str | None = None, settings_html: str | None = None,
              mock: bool = False, my_team_id: int | None = None) -> dict:
    """Record a heartbeat, plus the parsed picks when ``page`` is given and
    teams / settings when the league's pages are."""
    d = live_dir or LIVE_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{room}.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {"room": room, "picks": [], "updated_at": None}
    stamp = (now or _now()).isoformat()
    state.update(seen_at=stamp, url=url, source=source)
    if page is not None:
        picks = parse_draft_results(page)
        # a page with no picks right after one with picks is a hiccup
        # (an error page, a reload), not an undone draft
        if picks or not state["picks"]:
            state["picks"] = [p.to_json() for p in picks]
            state["updated_at"] = stamp
        (d / f"{room}.last.html").write_text(page, encoding="utf-8")
    read_pages(state, league_id, league_html, settings_html, mock, my_team_id,
               page)
    path.write_text(json.dumps(state), encoding="utf-8")
    return state


def save_state(state: dict, live_dir: Path | None = None) -> None:
    d = live_dir or LIVE_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{state['room']}.json").write_text(json.dumps(state), encoding="utf-8")


def load_room(league_id: int | None, live_dir: Path | None = None,
              now: datetime.datetime | None = None) -> dict | None:
    """The room a board follows: any room open right now (posted in the
    last LIVE_WINS_S -- a mock draft, another league's draft), else its own
    Yahoo league's while that is fresh, else the most recently seen room
    if IT is fresh."""
    d = live_dir or LIVE_DIR
    now = now or _now()

    def fresh(st):
        seen = (st or {}).get("seen_at")
        return bool(seen) and (now - datetime.datetime.fromisoformat(seen)
                               ).total_seconds() <= FOLLOW_ANY_H * 3600
    own = None
    if league_id:
        try:
            own = json.loads((d / f"{league_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            own = None
    best = None
    for path in d.glob("*.json") if d.exists() else []:
        try:
            st = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if best is None or (st.get("seen_at") or "") > (best.get("seen_at") or ""):
            best = st
    # a room open right now (a mock draft, say) wins over your league's
    # earlier data; otherwise your league's room while it's fresh
    if best and own is not best and best.get("room") != (own or {}).get("room"):
        seen = datetime.datetime.fromisoformat(best["seen_at"]) if best.get("seen_at") else None
        if seen and (now - seen).total_seconds() <= LIVE_WINS_S:
            return best
    if fresh(own):
        return own
    return best if fresh(best) else None


def room_view(state: dict | None, roster: Roster, my_team_id: int | None,
              now: datetime.datetime | None = None) -> dict:
    """What the board needs: matched picks, its log entries, unmatched names."""
    if not state:
        return {"connected": False, "room": None, "picks": [], "log": [],
                "unmatched": [], "teams": {}, "my_team_id": my_team_id}
    picks = [Pick(**dict(p, positions=tuple(p.get("positions") or ())))
             for p in state.get("picks", [])]
    match_picks(picks, roster)
    known = {int(k): v for k, v in (state.get("teams") or {}).items()}
    resolve_teams(picks, known)
    if my_team_id is None:
        my_team_id = state.get("my_team_id")
    seen = state.get("seen_at")
    connected = bool(seen) and ((now or _now())
                                - datetime.datetime.fromisoformat(seen)
                                ).total_seconds() <= CONNECTED_S
    teams = dict(known)
    for p in picks:
        if p.team_id is not None:
            teams.setdefault(p.team_id, p.team_name or f"Team {p.team_id}")
    st = state.get("settings") or {}
    settings = None
    if st:
        cfg = st["config"]
        settings = {"name": st.get("name"), "board_league": st.get("board_league"),
                    "imported": st.get("imported"),
                    "differences": st.get("differences") or [],
                    "notes": st.get("notes") or [],
                    "scoring_type": cfg["scoring_type"],
                    "team_count": cfg["team_count"]}
    return {"connected": connected, "room": state.get("room"),
            "mock": bool(state.get("mock")), "settings": settings,
            "settings_error": state.get("settings_error"),
            "source": state.get("source"), "url": state.get("url"),
            "seen_at": seen, "updated_at": state.get("updated_at"),
            "count": len(picks), "my_team_id": my_team_id, "teams": teams,
            "picks": [p.to_json() for p in picks],
            "log": board_log(picks, my_team_id),
            "unmatched": [p.name for p in picks if p.player_id is None]}


def linked_league(league_id: int | None) -> str | None:
    """The board league that follows this Yahoo league: a config with its
    own ``yahoo`` block for it, else the default league when
    config/inseason.yaml's yahoo block names it. None: no config does."""
    from src.inseason.model import load_inseason_config
    from src.valuation.league import DEFAULT_LEAGUE, load_league
    if not league_id:
        return None
    for path in league_paths(LEAGUE_DIR):
        if path.parent == IMPORTED_LEAGUE_DIR:
            continue
        try:
            cfg = load_league(str(path))
        except (OSError, ValueError):
            continue
        if cfg.yahoo is not None and cfg.yahoo.league_id == league_id:
            return path.stem
    try:
        y = (load_inseason_config() or {}).get("yahoo") or {}
    except OSError:
        y = {}
    return Path(DEFAULT_LEAGUE).stem if y.get("league_id") == league_id else None


def import_settings(state: dict, league_id: int | None,
                    folder: Path | None = None) -> None:
    """Write the room's Yahoo settings as a board league
    (data/league_configs/<name>.yaml) unless one of your own configs already
    follows that Yahoo league -- then record how it differs instead."""
    import yaml

    from src.valuation.league import load_league
    st = state.get("settings")
    if not st:
        return
    cfg = dict(st["config"])
    own = None if state.get("mock") else linked_league(league_id)
    if own:
        try:
            mine = load_league(str(league_path(own, LEAGUE_DIR)))
            st.update(board_league=own, imported=False,
                      differences=differences(mine, cfg))
        except (OSError, ValueError) as e:
            st.update(board_league=own, imported=False,
                      differences=[f"your config doesn't load: {e}"])
        return
    if league_id and state.get("my_team_id") and not state.get("mock"):
        cfg["yahoo"] = {"league_id": league_id, "team_id": state["my_team_id"]}
    folder = folder or IMPORTED_LEAGUE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{cfg['name']}.yaml"
    text = (f"# Read from Yahoo{' (mock draft room)' if state.get('mock') else ''}: "
            f"{st.get('name') or cfg['name']}\n"
            "# Rewritten whenever the draft sync reads its settings page.\n"
            "# To keep your own edits, copy it to config/leagues/ under another name.\n"
            + yaml.safe_dump(cfg, sort_keys=False))
    try:
        old = path.read_text(encoding="utf-8")
    except OSError:
        old = None
    if old != text:
        path.write_text(text, encoding="utf-8")
    st.update(board_league=cfg["name"], imported=True, differences=[])


def _yahoo_ids(league: str | None) -> tuple[int | None, int | None]:
    """(Yahoo league id, your team id) the board league follows."""
    from src.inseason.leagues import yahoo_link
    from src.inseason.model import load_inseason_config
    from src.valuation.league import DEFAULT_LEAGUE, load_league
    name = league or Path(DEFAULT_LEAGUE).stem
    path = league_path(name, LEAGUE_DIR)
    if not path.exists():
        raise HTTPException(404, f"Unknown league '{name}'")
    try:
        icfg = load_inseason_config()
    except OSError:
        icfg = {}
    link = yahoo_link(load_league(str(path)), icfg)
    return (link["league_id"], link["team_id"]) if link else (None, None)


# ── endpoints ─────────────────────────────────────────────────────────

class LivePost(BaseModel):
    url: str | None = Field(None, max_length=2048)
    league_id: int | None = Field(None, ge=1, le=10**9)
    html: str | None = Field(None, max_length=MAX_HTML)
    league_html: str | None = Field(None, max_length=MAX_HTML)
    settings_html: str | None = Field(None, max_length=MAX_HTML)
    mock: bool = False
    my_team_id: int | None = Field(None, ge=1, le=10**6)
    check: bool = False       # the extension's Save button: token test only


_LID = re.compile(r"/hockey/(\d+)(?:[/?#]|$)")


@router.post("/live")
def post_live(body: LivePost,
              x_draft_token: Annotated[str | None, Header()] = None) -> dict:
    if not token_valid(x_draft_token):
        raise HTTPException(401, "Missing or wrong X-Draft-Token (copy it "
                                 "from the draft board into the extension)")
    if body.check:
        return {"ok": True}
    lid = body.league_id
    if lid is None and body.url:
        m = _LID.search(body.url)
        lid = int(m.group(1)) if m else None
    state = save_post(_room_key(lid), body.url, body.html, league_id=lid,
                      league_html=body.league_html,
                      settings_html=body.settings_html, mock=body.mock,
                      my_team_id=body.my_team_id)
    if body.settings_html:
        import_settings(state, lid)
        save_state(state)
    return {"room": state["room"], "picks": len(state["picks"]),
            "settings": (state.get("settings") or {}).get("board_league"),
            "settings_error": state.get("settings_error")}


@router.get("/live")
def get_live(league: LeagueName = None,
             me: Annotated[int | None, Query(ge=0, le=10**6)] = None) -> dict:
    """``me``: your fantasy team id when the league config doesn't say it
    (picked on the board; 0 = none)."""
    lid, tid = _yahoo_ids(league)
    room = load_room(lid)
    if room and room.get("room") != _room_key(lid):
        tid = None          # another league's room: its own page says yours
    if me is not None:
        tid = me or None
    roster = Roster.from_db(DB_PATH) if DB_PATH.exists() else Roster(())
    out = room_view(room, roster, tid)
    out["yahoo_league_id"] = lid
    return out


@router.post("/live/pull")
def pull_public(league: LeagueName = None) -> dict:
    """Read a public league's draft results from the server (no extension)."""
    import httpx

    from src.inseason.yahoo_public import BASE, HEADERS, YahooPageError, _get
    lid, _ = _yahoo_ids(league)
    if not lid:
        raise HTTPException(422, "This league config has no Yahoo league id")
    url = f"{BASE}/{lid}/draftresults"
    try:
        with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as c:
            page = _get(c, url)
            league_html = _get(c, f"{BASE}/{lid}")
            settings_html = _get(c, f"{BASE}/{lid}/settings")
    except (httpx.HTTPError, YahooPageError) as e:
        raise HTTPException(502, f"Couldn't read {url}: {e}")
    state = save_post(_room_key(lid), url, page, source="server", league_id=lid,
                      league_html=league_html, settings_html=settings_html)
    import_settings(state, lid)
    save_state(state)
    return {"room": state["room"], "picks": len(state["picks"])}


@router.get("/token")
def get_token() -> dict:
    return {"token": draft_token()}
