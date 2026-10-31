"""NHL schedule ingest — one module per data source."""
from __future__ import annotations

import datetime
import time

import httpx

from .models import Fetcher

# 100 ms sleep for schedule endpoint (250 ms is only mandated for stats API).
_SCHED_SLEEP_S = 0.1


def ingest_schedule(conn, fetcher: Fetcher, season_id: int,
                    dry_run: bool = False,
                    start: datetime.date | None = None,
                    ) -> tuple[dict[str, int], list[str]]:
    """Fetch day-by-day schedule for one season and upsert games.

    ``start`` (optional) skips days before it -- the mid-season refresh only
    needs today onward. Default: the whole season from Oct 1.
    """
    notes: list[str] = []
    counts: dict[str, int] = {"schedule": 0}
    start_year = season_id // 10000
    end_year = season_id % 10000
    cur = conn.cursor()
    d = datetime.date(start_year, 10, 1)
    if start is not None and start > d:
        d = start
    end = datetime.date(end_year, 4, 30)
    while d <= end:
        date_str = d.isoformat()
        try:
            raw = fetcher.get_json(
                f"https://api-web.nhle.com/v1/schedule/{date_str}")
        except (httpx.HTTPError, OSError) as e:
            notes.append(f"schedule {date_str}: {e}")
            d += datetime.timedelta(days=1)
            continue
        for week in raw.get("gameWeek", []):
            for game in week.get("games", []):
                home = (game.get("homeTeam") or {}).get("abbrev", "")
                away = (game.get("awayTeam") or {}).get("abbrev", "")
                gid = game.get("id")
                gtype = game.get("gameType")
                if gid is None:
                    continue
                if dry_run:
                    counts["schedule"] += 1
                    continue
                cur.execute(
                    "INSERT INTO schedule (game_id, season_id, game_date,"
                    " home_abbrev, away_abbrev, game_type) VALUES (?,?,?,?,?,?)"
                    " ON CONFLICT(game_id) DO UPDATE SET"
                    " season_id=excluded.season_id, game_date=excluded.game_date,"
                    " home_abbrev=excluded.home_abbrev, away_abbrev=excluded.away_abbrev,"
                    " game_type=excluded.game_type",
                    (gid, season_id, date_str, home, away, gtype),
                )
                counts["schedule"] += 1
        d += datetime.timedelta(days=1)
        time.sleep(_SCHED_SLEEP_S)
    return counts, notes
