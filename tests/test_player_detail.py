"""Player detail: game log from the per-game stats reports, weekly totals,
and the /player/<id>/games endpoint (NHL fetch monkeypatched)."""
from __future__ import annotations

import pytest

from src.inseason.gamelog import fetch_games, scoring_stats, week_start, weekly
from tests.test_inseason import db  # noqa: F401  (pytest fixture)
from tests.test_inseason_gui_api import _client


class _Log:
    def __init__(self):
        self.urls = []

    def get_paged(self, base, report, season):
        self.urls.append(base)
        if "skater/summary" in base:
            return [{"gameId": 2, "gameDate": "2026-11-04", "opponentTeamAbbrev": "BBB",
                     "homeRoad": "R", "goals": 1, "assists": 2, "shots": 5,
                     "ppPoints": 1, "shPoints": 0},
                    {"gameId": 1, "gameDate": "2026-10-20T00:00:00", "homeRoad": "H",
                     "opponentTeamAbbrev": "CCC", "goals": 0, "assists": 1, "shots": 2}]
        if "skater/realtime" in base:
            return [{"gameId": 2, "hits": 3, "blockedShots": 1}]
        return [{"gameId": 9, "gameDate": "2026-11-01", "opponentTeamAbbrev": "CCC",
                 "homeRoad": "H", "gamesStarted": 1, "wins": 1, "saves": 27,
                 "shotsAgainst": 30, "goalsAgainst": 3, "shutouts": 0}]


def test_fetch_games_merges_reports_oldest_first():
    f = _Log()
    games = fetch_games(f, 1, 20262027, goalie=False)
    assert all("isGame=true" in u and "gameTypeId=2 and playerId=1 and seasonId="
               in u for u in f.urls)
    assert [(g["date"], g["opp"], g["home"]) for g in games] == [
        ("2026-10-20", "CCC", True), ("2026-11-04", "BBB", False)]
    assert games[1]["stats"] == {"goals": 1, "assists": 2, "shots": 5,
                                 "pp_points": 1, "sh_points": 0, "hits": 3,
                                 "blocks": 1}
    # no realtime row for game 1 -> hits / blocks 0, missing fields 0
    assert games[0]["stats"]["hits"] == 0 and games[0]["stats"]["pp_points"] == 0


def test_goalie_log_and_save_pct():
    g = fetch_games(_Log(), 6, 20262027, goalie=True)[0]
    assert g["stats"]["saves"] == 27 and g["stats"]["games_started"] == 1
    assert scoring_stats(g["stats"], True)["save_pct"] == pytest.approx(0.9)


def test_weekly_totals_monday_weeks_with_gaps():
    """2026-10-20 is a Tuesday (week of 10-19); 11-02 Mon and 11-04 Wed are
    the week of 11-02; the weeks of 10-26 has no games and shows as 0."""
    assert week_start("2026-10-20") == "2026-10-19"
    assert week_start("2026-11-08") == "2026-11-02"       # Sunday
    games = [{"date": "2026-10-20", "value": 3.0, "stats": {"goals": 1}},
             {"date": "2026-11-02", "value": 1.5, "stats": {"goals": 0, "hits": 2}},
             {"date": "2026-11-04", "value": 4.0, "stats": {"goals": 2, "hits": 1}}]
    wk = weekly(games)
    assert [(w["week"], w["games"], w["value"]) for w in wk] == [
        ("2026-10-19", 1, 3.0), ("2026-10-26", 0, 0.0), ("2026-11-02", 2, 5.5)]
    assert wk[2]["stats"] == {"goals": 2, "hits": 3}
    assert weekly([]) == []


def test_games_endpoint_scores_each_game(db, tmp_path, monkeypatch):  # noqa: F811
    """Kyle's league: G 5, A 3, PPP 1, SOG .25, BLK .75, HIT .5.
    Game 1: 1 A + 2 SOG = 3.5; game 2: 5 + 6 + 1 + 1.25 + .75 + 1.5 = 15.5."""
    from src.api import inseason_player as mod
    c = _client(db, tmp_path, monkeypatch)
    mod._LOG_CACHE.clear()
    monkeypatch.setattr(mod, "Fetcher", _Log)
    monkeypatch.setattr(_Log, "close", lambda self: None, raising=False)
    r = c.get("/api/inseason/player/1/games?league=t").json()
    assert r["name"] == "Alex Ovechkin" and r["error"] is None
    assert [g["value"] for g in r["games"]] == pytest.approx([3.5, 15.5])
    assert [(w["week"], w["games"]) for w in r["weeks"]] == [
        ("2026-10-19", 1), ("2026-10-26", 0), ("2026-11-02", 1)]
    assert r["pre_per_game"] > 0 and r["unit"] == "fpts"
    assert c.get("/api/inseason/player/999/games?league=t").status_code == 404
    p = c.get("/api/inseason/player/1?league=t").json()
    assert p["name"] == "Alex Ovechkin" and p["team"] == "AAA"


def test_games_endpoint_survives_api_failure(db, tmp_path, monkeypatch):  # noqa: F811
    from src.api import inseason_player as mod
    c = _client(db, tmp_path, monkeypatch)
    mod._LOG_CACHE.clear()

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(mod, "fetch_games", boom)
    r = c.get("/api/inseason/player/1/games?league=t").json()
    assert r["games"] == [] and r["weeks"] == [] and "NHL API" in r["error"]
