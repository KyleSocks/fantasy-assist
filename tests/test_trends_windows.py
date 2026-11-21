"""% rostered history (src/inseason/rostered.py) and stat windows
(src/inseason/windows.py), worked out by hand."""
import sqlite3

import pytest

from src.inseason import rostered, windows
from src.inseason.model import Line


def R(name, team, pct):
    return {"name": name, "team": team, "pct_ros": pct}


def test_history_and_changes():
    """Snapshots Nov 10, 14, 17 09:00 and 17 21:00. 7-day change from the
    newest snapshot at least 7 days before Nov 17 21:00 = Nov 10:
    Ann 10 -> 40 = +30. 3-day: newest at or before Nov 14 21:00 = Nov 14:
    Ann 25 -> 40 = +15. Bob only appears now: no change known."""
    h = None
    for at, rows in (("2026-11-10T09:00", [R("Ann F", "BOS", 10)]),
                     ("2026-11-14T09:00", [R("Ann F", "BOS", 25)]),
                     ("2026-11-17T09:00", [R("Ann F", "BOS", 33)]),
                     ("2026-11-17T21:00", [R("Ann F", "BOS", 40), R("Bob D", "NYR", 5)])):
        h = rostered.update(h, rows, at)
    assert len(h["snapshots"]) == 4
    c7 = rostered.changes(h, 7)
    assert c7["ann f|BOS"] == {"pct": 40, "delta": 30, "since": "2026-11-10T09:00"}
    assert c7["bob d|NYR"]["delta"] is None
    assert rostered.changes(h, 3)["ann f|BOS"]["delta"] == 15
    # old snapshots age out after KEEP_DAYS
    h = rostered.update(h, [R("Ann F", "BOS", 41)], "2026-12-12T09:00")
    assert [s["at"] for s in h["snapshots"]] == ["2026-11-14T09:00", "2026-11-17T09:00",
                                                 "2026-11-17T21:00", "2026-12-12T09:00"]
    assert rostered.changes(None, 7) == {}
    # same pull twice replaces, rows without a % are skipped
    h2 = rostered.update(h, [R("Ann F", "BOS", 50), R("Cy", "TOR", None)], "2026-12-12T09:00")
    assert len(h2["snapshots"]) == 4 and h2["snapshots"][-1]["pct"] == {"ann f|BOS": 50}


def _ln(pid, name, team):
    return Line(pid, name, "C", team, 1, {}, {}, {})


def test_by_player_matches_name_and_team():
    lines = {1: _ln(1, "Sebastian Aho", "CAR"), 2: _ln(2, "Sebastian Aho", "NYI"),
             3: _ln(3, "Ann F", "BOS")}
    ch = {"sebastian aho|CAR": {"pct": 99}, "ann f|TOR": {"pct": 12}, "nobody|BOS": {"pct": 1}}
    # Ann traded since Yahoo's pull: the name alone is unique, so she matches
    assert rostered.by_player(lines, ch) == {1: {"pct": 99}, 3: {"pct": 12}}


class _Fetch:
    def __init__(self):
        self.urls = []

    def get_paged(self, url, report, season):
        self.urls.append(url)
        if "skater/summary" in url:
            return [{"playerId": 1, "gamesPlayed": 3, "goals": 2, "assists": 1, "shots": 9,
                     "ppPoints": 1, "shPoints": 0}]
        if "skater/realtime" in url:
            return [{"playerId": 1, "hits": 4, "blockedShots": 2}]
        return [{"playerId": 9, "gamesPlayed": 2, "gamesStarted": 2, "wins": 1, "saves": 50,
                 "shotsAgainst": 54, "goalsAgainst": 4, "shutouts": 0}]


def test_windows_ingest_and_load():
    conn = sqlite3.connect(":memory:")
    f = _Fetch()
    assert windows.ingest_windows(conn, f, 20262027, "2026-11-20", (7, 14)) == {"window_rows": 4}
    assert 'gameDate>="2026-11-14"' in f.urls[0] and 'gameDate>="2026-11-07"' in f.urls[3]
    w = windows.load_windows(conn)
    assert w[1][7]["games"] == 3 and w[1][7]["stats"]["shots"] == 9 and w[1][14]["stats"]["hits"] == 4
    assert w[9][14]["stats"]["saves"] == 50 and w[9][7]["start"] == "2026-11-14"
    assert windows.load_windows(sqlite3.connect(":memory:")) == {}
