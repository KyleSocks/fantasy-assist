"""Roster changes for every team, on the /api/inseason router (imported at
the bottom of src/api/inseason.py):

GET /api/inseason/moves?days=30&league=

Reads roster_moves.json (written by Refresh; see src/inseason/moves.py) and
adds what the page needs: team names, each player's id and blended value per
game, and a per-team count over the window.
"""
from __future__ import annotations

import datetime
from typing import Annotated

from fastapi import HTTPException, Query

from src.api import inseason as ins
from src.api.inseason import LeagueName
from src.inseason import leagues as store
from src.inseason import moves
from src.inseason.roster import _match

MAX_MOVE_DAYS = 400


def _player(ctx, p: dict) -> dict:
    out = {"name": p.get("name"), "team": p.get("team"),
           "elig": list(p.get("positions") or []), "id": None, "per_game": None}
    ln = _match(ctx, [p.get("name") or "", p.get("team") or "",
                      ",".join(p.get("positions") or [])])
    if ln is not None:
        out.update(id=ln.player_id, per_game=ctx.scorer.per_game.get(ln.player_id))
    return out


@ins.router.get("/moves")
def get_moves(days: Annotated[int, Query(ge=1, le=MAX_MOVE_DAYS)] = 30,
              league: LeagueName = None) -> dict:
    """Adds, drops, trades and IR moves on every team over the last ``days``
    days, newest first. ``tracking_since``: the first snapshot the log was
    built from -- nothing before it is known."""
    ctx = ins._ctx(league)
    folder = ins._dir(ctx)
    snap = store.read_json(folder / ins.LEAGUE_FILE)
    if snap is None:
        raise HTTPException(400, "no league rosters yet: press Refresh data")
    log = store.read_json(folder / ins.MOVES_FILE) or {}
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    names = {int(t): v.get("name") or f"Team {t}"
             for t, v in (snap.get("teams") or {}).items()}
    mine = snap.get("my_team_id")
    recent = [e for e in log.get("events") or [] if (e.get("at") or "") >= cutoff]
    events = [{
        "type": e["type"], "at": e.get("at"), "since": e.get("since"),
        "team_id": e["team_id"],
        "team": names.get(e["team_id"], f"Team {e['team_id']}"),
        "from_team_id": e.get("from_team_id"),
        "from_team": (names.get(e["from_team_id"], f"Team {e['from_team_id']}")
                      if e.get("from_team_id") is not None else None),
        "player": _player(ctx, e.get("player") or {}),
    } for e in reversed(recent)]
    counts = moves.summarize(recent, list(names))
    teams = sorted(({"id": t, "name": n, "mine": t == mine, **counts[t]}
                    for t, n in names.items()),
                   key=lambda t: (-(t["add"] + t["drop"] + t["trade"]), t["name"]))
    return {"league": ctx.league.name, "days": days, "events": events,
            "teams": teams, "my_team_id": mine,
            "tracking_since": log.get("started") or snap.get("fetched"),
            "updated": snap.get("fetched"), "as_of": ctx.as_of,
            "unit": "fpts" if ctx.scorer.points else "z"}
