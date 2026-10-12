"""Small read-only queries shared by the API, valuation and draft modules."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def next_season_id(db_path: str | Path) -> int:
    """The upcoming season id: ``MAX(skater_seasons.season_id) + 10001``.

    season_id encodes start_year*10000 + end_year (e.g. 20252026 = 2025-26).
    The following season 2026-27 is 20262027 = 20252026 + 10001, so we add
    10001 (not 10000) to roll from one season to the next.

    Raises ValueError when the table has no rows.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT MAX(season_id) FROM skater_seasons").fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        raise ValueError(f"no skater_seasons rows in {db_path}")
    return int(row[0]) + 10001


def player_names(db_path: str | Path) -> dict[int, str]:
    """``{player_id: full_name}`` for every player with a name."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT player_id, full_name FROM players WHERE full_name IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    return {pid: name for pid, name in rows}


def player_teams(db_path: str | Path) -> dict[int, str]:
    """``{player_id: current_team_abbrev}`` for every player with a team."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT player_id, current_team_abbrev FROM players"
        ).fetchall()
    finally:
        conn.close()
    return {pid: team for pid, team in rows if team}
