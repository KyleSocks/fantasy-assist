"""Scratch: capture real raw API rows into a minimal JSON fixture for the ingest test."""
import json
from pathlib import Path

from src.ingest.models import Fetcher

SKATERS = [8478402, 8477492, 8477934]
GOALIES = [8478048, 8476945]
SEASON = 20212022

f = Fetcher()
try:
    def grab(report, ids, keep):
        url = {
            "skater_summary": "https://api.nhle.com/stats/rest/en/skater/summary?cayenneExp=seasonId=",
            "skater_realtime": "https://api.nhle.com/stats/rest/en/skater/realtime?cayenneExp=seasonId=",
            "skater_toi": "https://api.nhle.com/stats/rest/en/skater/timeonice?cayenneExp=seasonId=",
            "goalie_summary": "https://api.nhle.com/stats/rest/en/goalie/summary?cayenneExp=seasonId=",
        }[report]
        rows = f.get_paged(url, report, SEASON)
        out = []
        for r in rows:
            if r.get("playerId") in ids:
                out.append({k: r.get(k) for k in keep})
        return out

    keep = {
        "skater_summary": ["playerId", "seasonId", "gamesPlayed", "goals",
                           "assists", "points", "shots", "ppPoints", "teamAbbrevs"],
        "skater_realtime": ["playerId", "seasonId", "hits", "blockedShots"],
        "skater_toi": ["playerId", "seasonId", "evTimeOnIce", "ppTimeOnIce"],
        "goalie_summary": ["playerId", "seasonId", "gamesPlayed", "gamesStarted",
                           "wins", "losses", "otLosses", "saves", "shotsAgainst",
                           "goalsAgainst", "shutouts", "teamAbbrevs"],
    }
    parts = {
        "skater_summary": grab("skater_summary", SKATERS, keep["skater_summary"]),
        "skater_realtime": grab("skater_realtime", SKATERS, keep["skater_realtime"]),
        "skater_toi": grab("skater_toi", SKATERS, keep["skater_toi"]),
        "goalie_summary": grab("goalie_summary", GOALIES, keep["goalie_summary"]),
    }

    schedule = {}
    for day in ("2021-10-13",):
        raw = f.get_json("https://api-web.nhle.com/v1/schedule/" + day)
        games = []
        for week in raw.get("gameWeek", []):
            for g in week.get("games", []):
                gid = g.get("id")
                if gid is None:
                    continue
                games.append({
                    "id": gid,
                    "gameType": g.get("gameType"),
                    "homeTeam": {"abbrev": (g.get("homeTeam") or {}).get("abbrev", "")},
                    "awayTeam": {"abbrev": (g.get("awayTeam") or {}).get("abbrev", "")},
                })
        schedule[day] = {"gameWeek": [{"games": games}]}
finally:
    f.close()

fixture = {"season": SEASON, **parts, "schedule": schedule}

out = Path("tests/fixtures/ingest_20212022.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(fixture, indent=1))
print("wrote", out)
for k in ("skater_summary", "skater_realtime", "skater_toi", "goalie_summary"):
    print(k, len(parts[k]), [r.get("playerId") for r in parts[k]])
print("schedule games", sum(len(w["games"]) for d in schedule.values() for w in d["gameWeek"]))
print("unique schedule ids", len({g["id"] for d in schedule.values() for w in d["gameWeek"] for g in w["games"]}))
