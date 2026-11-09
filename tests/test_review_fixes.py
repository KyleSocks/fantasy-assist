"""Regression tests for three review findings on fix/review-criticals:

1. a traded player's combined stats row inflating his new team's games
   played (and with it goalie start shares and opponent factors);
2. /streams?all=true double-counting your own players in ``adds``;
3. response caches keyed on id(ctx), which CPython can reuse.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import src.api.inseason as api
from src.api.main import app
from src.inseason.model import opponent_factors, team_games_played, team_share
from tests.test_inseason import db  # noqa: F401  (pytest fixture)

# BOS has played 15. Player 2 was traded NYR -> BOS: 12 GP with NYR + 6
# with BOS, which the stats API returns as ONE row (gp 18) under BOS.
SKATERS = {1: {"team_abbrev": "BOS", "gp": 15, "goals": 5, "shots": 40},
           2: {"team_abbrev": "BOS", "gp": 18, "goals": 9, "shots": 54},
           3: {"team_abbrev": "NYR", "gp": 16, "goals": 6, "shots": 45},
           4: {"team_abbrev": "BOS", "gp": 15, "goals": 0, "shots": 0},
           5: {"team_abbrev": "BOS", "gp": 9, "goals": 0, "shots": 0}}
GOALIES = {10: {"team_abbrev": "BOS", "gp": 15, "goals_against": 40,
                "shots_against": 450},
           11: {"team_abbrev": "NYR", "gp": 16, "goals_against": 45,
                "shots_against": 480}}


def test_traded_row_does_not_inflate_team_gp():
    # 15 is shared by two BOS regulars; the traded player's 18 is his alone
    assert team_games_played(SKATERS) == {"BOS": 15, "NYR": 16}
    # no shared value (one skater, or all different): plain max
    assert team_games_played({1: {"team_abbrev": "X", "gp": 7},
                              2: {"team_abbrev": "X", "gp": 4}}) == {"X": 7}
    assert team_games_played({1: {"team_abbrev": "X", "gp": 0}}) == {}


def test_team_share_prorates_only_traded_rows():
    assert team_share(15, 15) == 1.0
    assert team_share(10, 15) == 1.0         # missed games: not traded
    assert team_share(18, 15) == 15 / 18
    assert team_share(None, 15) == 1.0 and team_share(5, 0) == 1.0


def test_opponent_factors_prorate_traded_scorer():
    gp = {"BOS": 15, "NYR": 16}
    f = opponent_factors(SKATERS, GOALIES, gp, k=0)
    # BOS goals for = 5 + 9 * 15/18 = 12.5 over 15 games; league mean is
    # (12.5 + 6) / 31 games
    league_gf = (12.5 + 6) / 31
    assert abs(f["BOS"]["gf"] - (12.5 / 15) / league_gf) < 1e-9


def _client(db, tmp_path, monkeypatch):  # noqa: F811
    leagues = tmp_path / "leagues"
    leagues.mkdir()
    (leagues / "t.yaml").write_text(
        Path("config/leagues/kyle_12_points.yaml").read_text())
    monkeypatch.setattr(api, "DB_PATH", Path(db))
    monkeypatch.setattr(api, "LEAGUE_DIR", leagues)
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    api._CACHE.clear()
    api._FA_CACHE.clear()
    return TestClient(app)


def test_streams_all_does_not_double_count_my_players(db, tmp_path,  # noqa: F811
                                                      monkeypatch):
    c = _client(db, tmp_path, monkeypatch)
    c.put("/api/inseason/lists?league=t", json={
        "roster": "Alex Ovechkin | AAA | LW\n", "free_agents": ""})
    rows = c.get("/api/inseason/streams?league=t&date=2026-11-01&all=true"
                 ).json()["players"]
    ovi = next(r for r in rows if r["name"] == "Alex Ovechkin")
    assert ovi["adds"] == 0.0
    others = [r for r in rows if r["name"] != "Alex Ovechkin"]
    assert others and all(r["adds"] is not None for r in others)


def test_cache_generation_is_unique_per_context(db, tmp_path,  # noqa: F811
                                                monkeypatch):
    _client(db, tmp_path, monkeypatch)
    a = api._ctx("t")
    assert api._ctx("t") is a and api._gen(a) == api._gen(a)
    api._CACHE.clear()
    b = api._ctx("t")
    assert b is not a and api._gen(b) != api._gen(a)
