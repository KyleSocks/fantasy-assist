"""Player detail endpoint, on the /api/inseason router (imported at the
bottom of src/api/inseason.py):

GET /api/inseason/player/{id}/games   this season's game log + weekly totals

The log is fetched from the NHL API on first click and kept until the
stats are next refreshed (the context's as_of changes).
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

from src.api import inseason as ins
from src.api.inseason import LeagueName
from src.ingest.models import Fetcher
from src.inseason.evaluate import form
from src.inseason.gamelog import fetch_games, scoring_stats, weekly

log = logging.getLogger(__name__)
_LOG_CACHE: dict[tuple, list[dict]] = {}
_MAX_CACHED = 400


def _games(pid: int, season: int, goalie: bool, as_of: str) -> list[dict]:
    key = (pid, season, as_of)
    if key not in _LOG_CACHE:
        fetcher = Fetcher()
        try:
            games = fetch_games(fetcher, pid, season, goalie)
        finally:
            fetcher.close()
        if len(_LOG_CACHE) >= _MAX_CACHED:
            _LOG_CACHE.clear()
        _LOG_CACHE[key] = games
    return _LOG_CACHE[key]


@ins.router.get("/player/{player_id}/games")
def get_player_games(player_id: int, league: LeagueName = None) -> dict:
    ctx = ins._ctx(league)
    ln = ctx.lines.get(player_id)
    if ln is None:
        raise HTTPException(404, f"Unknown player id {player_id}")
    error = None
    try:
        raw = _games(player_id, ctx.season, ln.is_goalie, ctx.as_of)
    except Exception as e:                      # network / API shape
        log.warning("game log for %s failed: %s", player_id, e)
        raw, error = [], "Couldn't load the game log from the NHL API."
    games = [dict(g, value=ctx.scorer.value_stats(
        player_id, ln.position, scoring_stats(g["stats"], ln.is_goalie)))
        for g in raw]
    f = form(ctx, ln)
    return {"id": player_id, "name": ln.name, "team": ln.team,
            "position": ln.position, "goalie": ln.is_goalie,
            "games": games, "weeks": weekly(games),
            # preseason pace: per game played (skaters) / per team game (goalies)
            "pre_per_game": f["prior"], "error": error,
            "unit": "fpts" if ctx.scorer.points else "z"}
