"""Game-by-game stats for one player this season, and weekly totals.

Fetched on demand from the NHL stats API's per-game reports (the same
reports as the season ingest, with ``isGame=true`` and a playerId filter):
skater summary (G, A, SOG, PPP, SHP) + realtime (hits, blocks), or goalie
summary. Regular season only.
"""
from __future__ import annotations

import datetime

_BASE = "https://api.nhle.com/stats/rest/en"

SKATER_FIELDS = {"goals": "goals", "assists": "assists", "shots": "shots",
                 "pp_points": "ppPoints", "sh_points": "shPoints"}
REALTIME_FIELDS = {"hits": "hits", "blocks": "blockedShots"}
GOALIE_FIELDS = {"games_started": "gamesStarted", "wins": "wins",
                 "saves": "saves", "shots_against": "shotsAgainst",
                 "goals_against": "goalsAgainst", "shutouts": "shutouts"}


def _url(report: str, pid: int) -> str:
    return (f"{_BASE}/{report}?isAggregate=false&isGame=true"
            f"&cayenneExp=gameTypeId=2 and playerId={int(pid)} and seasonId=")


def _game(row: dict) -> dict:
    return {"game_id": row.get("gameId"),
            "date": str(row.get("gameDate") or "")[:10],
            "opp": row.get("opponentTeamAbbrev"),
            "home": row.get("homeRoad") == "H"}


def fetch_games(fetcher, pid: int, season: int, goalie: bool) -> list[dict]:
    """[{game_id, date, opp, home, stats: {...}}] oldest first."""
    if goalie:
        rows = fetcher.get_paged(_url("goalie/summary", pid),
                                 f"log_goalie_{pid}", season)
        games = [dict(_game(r), stats={k: float(r.get(f) or 0)
                                       for k, f in GOALIE_FIELDS.items()})
                 for r in rows]
    else:
        rows = fetcher.get_paged(_url("skater/summary", pid),
                                 f"log_skater_{pid}", season)
        rt = {r.get("gameId"): r for r in fetcher.get_paged(
            _url("skater/realtime", pid), f"log_realtime_{pid}", season)}
        games = []
        for r in rows:
            stats = {k: float(r.get(f) or 0) for k, f in SKATER_FIELDS.items()}
            extra = rt.get(r.get("gameId"), {})
            stats.update({k: float(extra.get(f) or 0)
                          for k, f in REALTIME_FIELDS.items()})
            games.append(dict(_game(r), stats=stats))
    return sorted((g for g in games if g["date"]), key=lambda g: g["date"])


def scoring_stats(stats: dict[str, float], goalie: bool) -> dict[str, float]:
    """A game's stats in the shape the scorer takes (goalies: save %)."""
    if not goalie:
        return stats
    sa = stats.get("shots_against", 0.0)
    return dict(stats, save_pct=stats.get("saves", 0.0) / sa if sa else 0.0)


def week_start(date: str) -> str:
    """Monday of the fantasy week (Mon-Sun) a date falls in."""
    d = datetime.date.fromisoformat(date)
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def weekly(games: list[dict]) -> list[dict]:
    """Weekly totals, oldest first: [{week, games, value, stats}]. Each game
    needs a ``value``. Weeks between the first and last game in which he
    did not play are included with 0 games, so a gap shows as a gap."""
    if not games:
        return []
    by: dict[str, dict] = {}
    for g in games:
        w = by.setdefault(week_start(g["date"]),
                          {"games": 0, "value": 0.0, "stats": {}})
        w["games"] += 1
        w["value"] += g["value"]
        for k, v in g["stats"].items():
            w["stats"][k] = w["stats"].get(k, 0.0) + v
    out = []
    d = datetime.date.fromisoformat(min(by))
    last = datetime.date.fromisoformat(max(by))
    while d <= last:
        k = d.isoformat()
        out.append(dict(by.get(k, {"games": 0, "value": 0.0, "stats": {}}),
                        week=k))
        d += datetime.timedelta(days=7)
    return out
