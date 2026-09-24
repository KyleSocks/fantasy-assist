"""Roster and player ingest — one module per data source."""
from __future__ import annotations

import httpx

from .models import Fetcher, TEAM_ABBREVS

# Roster API returns players grouped by position under these keys.
_ROSTER_KEYS = ("forwards", "defensemen", "goalies")


def _extract_player(raw_entry: dict, team_abbrev: str) -> tuple:
    """Extract a player tuple from a roster entry dict."""
    pid = raw_entry.get("id")
    if pid is None:
        return None
    first = (raw_entry.get("firstName") or {}).get("default", "")
    last = (raw_entry.get("lastName") or {}).get("default", "")
    full_name = f"{first} {last}".strip()
    pos = raw_entry.get("positionCode") or ""
    shoots = raw_entry.get("shootsCatches") or ""
    birth = raw_entry.get("birthDate") or None
    height_in = raw_entry.get("heightInInches")
    weight = raw_entry.get("weightInPounds")
    return (pid, full_name, pos, shoots, birth, height_in, weight, team_abbrev, 1)


def ingest_rosters(conn, fetcher: Fetcher,
                   dry_run: bool = False) -> tuple[dict[str, int], list[str]]:
    """Fetch current rosters for all 32 teams and upsert players/teams."""
    notes: list[str] = []
    counts: dict[str, int] = {"players": 0, "teams": 0}
    cur = conn.cursor()
    for abbrev in TEAM_ABBREVS:
        try:
            raw = fetcher.get_json(
                f"https://api-web.nhle.com/v1/roster/{abbrev}/current")
        except (httpx.HTTPError, OSError) as e:
            notes.append(f"roster {abbrev}: {e}")
            continue
        if not dry_run:
            cur.execute(
                "INSERT INTO teams (abbrev) VALUES (?)"
                " ON CONFLICT(abbrev) DO UPDATE SET abbrev=excluded.abbrev",
                (abbrev,),
            )
            counts["teams"] += 1
        for key in _ROSTER_KEYS:
            for entry in (raw.get(key) or []):
                row = _extract_player(entry, abbrev)
                if row is None:
                    continue
                if dry_run:
                    counts["players"] += 1
                    continue
                cur.execute(
                    "INSERT INTO players (player_id, full_name, position, shoots,"
                    " birth_date, height_in, weight_lb, current_team_abbrev, is_active)"
                    " VALUES (?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(player_id) DO UPDATE SET"
                    " full_name=excluded.full_name, position=excluded.position,"
                    " shoots=excluded.shoots, birth_date=excluded.birth_date,"
                    " height_in=excluded.height_in, weight_lb=excluded.weight_lb,"
                    " current_team_abbrev=excluded.current_team_abbrev,"
                    " is_active=excluded.is_active",
                    row,
                )
                counts["players"] += 1
    return counts, notes
