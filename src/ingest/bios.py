"""Backfill players who are no longer on an NHL roster.

The roster ingest only knows today's rosters, so a player who retired or
left the league had season stats but no players row: no position, no
birth date. The projection, the walk-forward backtest and the age-curve
fit all need those, and skipping such players biased the backtest and the
age curves toward players who are still in the league (survivors).

Rows added here are is_active = 0 with no current team, so they never
reach the draft board; a later roster ingest that finds the player on a
roster sets is_active = 1 and his team. Existing rows only gain a missing
birth date or position, never lose roster data.
"""
from __future__ import annotations

from .models import Fetcher, PlayerBio

_BASE = "https://api.nhle.com/stats/rest/en"
_FILTER = "?cayenneExp=gameTypeId=2 and seasonId="


def _upsert(cur, bio: PlayerBio, name: str | None, position: str | None) -> None:
    cur.execute(
        "INSERT INTO players (player_id, full_name, position, birth_date,"
        " current_team_abbrev, is_active) VALUES (?, ?, ?, ?, NULL, 0)"
        " ON CONFLICT(player_id) DO UPDATE SET"
        " birth_date = COALESCE(players.birth_date, excluded.birth_date),"
        " position = COALESCE(NULLIF(players.position, ''), excluded.position)",
        (bio.playerId, name or f"Player {bio.playerId}", position or None,
         bio.birthDate))


def ingest_bios(conn, fetcher: Fetcher, season_id: int,
                dry_run: bool = False) -> tuple[dict[str, int], list[str]]:
    """Upsert a players row for everyone with regular-season stats in
    ``season_id``. Returns ({"player_bios": n}, notes)."""
    notes: list[str] = []
    n = missing_birth = 0
    cur = conn.cursor()
    for kind in ("skater", "goalie"):
        rows = fetcher.get_paged(f"{_BASE}/{kind}/bios{_FILTER}",
                                 f"{kind}_bios", season_id)
        for raw in rows:
            bio = PlayerBio(**raw)
            if bio.playerId is None:
                continue
            if not bio.birthDate:
                missing_birth += 1
            pos = "G" if kind == "goalie" else bio.positionCode
            name = bio.goalieFullName if kind == "goalie" else bio.skaterFullName
            if not dry_run:
                _upsert(cur, bio, name, pos)
            n += 1
    if missing_birth:
        notes.append(f"bios {season_id}: {missing_birth} rows without birthDate")
    return {"player_bios": n}, notes
