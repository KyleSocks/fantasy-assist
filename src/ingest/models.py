"""NHL ingest — constants, Pydantic models, Fetcher, and DB helpers."""
from __future__ import annotations

import datetime
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from pydantic import BaseModel, ConfigDict

from src import net
from src.paths import SCHEMA_PATH

PAGE_LIMIT = 100
SLEEP_S = 0.25
TIMEOUT = 30

# 32 NHL team abbreviations — no "list teams" endpoint exists in the permitted APIs.
TEAM_ABBREVS: list[str] = [
    "ANA", "BOS", "BUF", "CAR", "CGY", "CBJ", "CHI", "COL",
    "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NJD",
    "NSH", "NYI", "NYR", "OTT", "PHI", "PIT", "SEA", "SJS",
    "STL", "TBL", "TOR", "VAN", "VGK", "WPG", "WSH", "UTA",
]


# ── Pydantic models (ignore unknown fields) ─────────────────────────

class _Row(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SkaterSummary(_Row):
    playerId: Optional[int] = None
    seasonId: Optional[int] = None
    gamesPlayed: Optional[int] = None
    goals: Optional[int] = None
    assists: Optional[int] = None
    points: Optional[int] = None
    shots: Optional[int] = None
    ppPoints: Optional[int] = None
    shPoints: Optional[int] = None
    teamAbbrevs: Optional[str] = None


class SkaterRealtime(_Row):
    playerId: Optional[int] = None
    seasonId: Optional[int] = None
    hits: Optional[int] = None
    blockedShots: Optional[int] = None


class SkaterTOI(_Row):
    playerId: Optional[int] = None
    seasonId: Optional[int] = None
    evTimeOnIce: Optional[float] = None
    ppTimeOnIce: Optional[float] = None
    shTimeOnIce: Optional[float] = None


class PlayerBio(_Row):
    """A row of the skater/bios or goalie/bios stats report."""
    playerId: Optional[int] = None
    skaterFullName: Optional[str] = None
    goalieFullName: Optional[str] = None
    positionCode: Optional[str] = None
    birthDate: Optional[str] = None


class GoalieSummary(_Row):
    playerId: Optional[int] = None
    seasonId: Optional[int] = None
    gamesPlayed: Optional[int] = None
    gamesStarted: Optional[int] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    otLosses: Optional[int] = None
    saves: Optional[int] = None
    shotsAgainst: Optional[int] = None
    goalsAgainst: Optional[int] = None
    shutouts: Optional[int] = None
    teamAbbrevs: Optional[str] = None


# ── HTTP layer with per-(report, season) cache ───────────────────────

class Fetcher:
    def __init__(self) -> None:
        self._client = httpx.Client(timeout=TIMEOUT,
                                   headers={"User-Agent": "hockey-draft/0.1"},
                                   follow_redirects=True)
        self._cache: dict[tuple[str, int], list[dict]] = {}

    def get_paged(self, base_url: str, report: str, season: int) -> list[dict]:
        """Fetch all pages from a stats REST endpoint.

        The API wraps each page as ``{"data": [...], "total": N}``.

        Pages are requested sorted by ``playerId`` so pagination is
        deterministic. Without a stable sort the server's page boundaries
        shift between requests, which duplicates *and* omits players across
        runs -- making a full fetch non-reproducible and the ingest
        non-idempotent.
        """
        key = (report, season)
        if key in self._cache:
            return self._cache[key]
        sep = "&" if "?" in base_url else "?"
        rows: list[dict] = []
        start = 0
        while True:
            url = (f"{base_url}{season}{sep}start={start}"
                   f"&limit={PAGE_LIMIT}&sort=playerId")
            resp = net.get(self._client, url)
            body = resp.json()
            page = body.get("data", []) if isinstance(body, dict) else body
            if not isinstance(page, list) or len(page) == 0:
                break
            rows.extend(page)
            if len(page) < PAGE_LIMIT:
                break
            start += PAGE_LIMIT
            time.sleep(SLEEP_S)
        self._cache[key] = rows
        return rows

    def get_json(self, url: str) -> Any:
        return net.get(self._client, url).json()

    def close(self) -> None:
        self._client.close()


# ── DB layer ─────────────────────────────────────────────────────────

def init_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate_schema(conn)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _add_missing_columns(conn)
    return conn


# Columns added after a table was first created: (table, column, type).
_ADDED_COLUMNS = [
    ("skater_seasons", "sh_points", "INTEGER"),
    ("skater_current", "sh_points", "INTEGER"),
    ("skater_seasons", "sh_toi_sec", "INTEGER"),
]


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """ALTER TABLE for columns newer than an existing DB (CREATE TABLE IF
    NOT EXISTS never adds columns). New values stay NULL until re-ingest."""
    for table, col, typ in _ADDED_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if cols and col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Drop season tables created under the old 3-column primary key.

    ``skater_seasons`` and ``goalie_seasons`` originally used
    ``PRIMARY KEY (player_id, season_id, team_abbrev)``. A player traded
    mid-season then produced one row per team segment, and the row kept
    depended on fetch order. The PK is now ``(player_id, season_id)`` with a
    single deterministic ``team_abbrev`` (the team they were traded to) and
    stats summed across the regular-season and playoff rows, so the old
    per-team rows are not compatible and must be re-ingested.
    """
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("skater_seasons", "goalie_seasons"):
        if table not in tables:
            continue
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (table,)
        ).fetchone()[0]
        if "PRIMARY KEY (player_id, season_id, team_abbrev)" in sql:
            conn.execute(f"DROP TABLE {table}")


def _utc_now_naive() -> str:
    """UTC now as a naive ISO string -- the format ingest_log.run_at has
    always used (MAX(run_at) sorts it as text; routes.data_freshness reads
    it as naive UTC). Replaces the deprecated datetime.utcnow()."""
    return (datetime.datetime.now(datetime.timezone.utc)
            .replace(tzinfo=None).isoformat())


def log_ingest(conn: sqlite3.Connection, source: str, season_id: int | None,
               rows: int, notes: list[str] | None = None,
               status: str = "OK") -> None:
    conn.execute(
        "INSERT INTO ingest_log (source, season_id, rows_written, run_at, status, notes)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (source, season_id, rows, _utc_now_naive(), status,
         "; ".join(notes) if notes else None),
    )