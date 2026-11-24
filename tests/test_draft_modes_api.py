"""/api/board and /api/recommend with ?tf= and ?keeper= end to end, on the
tiny in-season world from tests/test_inseason.py (its schedule runs
2026-10-20..11-04). The season board's own valuation is replaced by a
fixed one so only the mode layer is under test; the window and keeper
numbers themselves are hand-checked in test_draft_timeframe.py and
test_draft_keeper.py."""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.api.draft_modes as modes
import src.api.inseason as ins
from src.api import routes
from src.projections.common import Projection
from src.valuation.valuation import PlayerValue
from tests.test_inseason import db  # noqa: F401  (pytest fixture)

# the package re-exports recommend(), so "import ... as" would get the function
rec = importlib.import_module("src.draft.recommend")

POS = {1: "L", 2: "C", 3: "R", 4: "D", 5: "D", 6: "G", 7: "G"}


def _base():
    projs = {pid: Projection(pid, 20262027, 82, 0, 0, 0, 0, 0, 0, 0, None, None,
                             position=pos) for pid, pos in POS.items()}
    vals = [PlayerValue(pid, pos, raw_value=100.0 - pid, vorp=50.0 - pid,
                        cat_zscores={}, replacement_used=50.0,
                        total_value=50.0 - pid) for pid, pos in POS.items()]
    return projs, vals


@pytest.fixture
def client(db, tmp_path, monkeypatch):  # noqa: F811
    leagues = tmp_path / "leagues"
    leagues.mkdir()
    (leagues / "t.yaml").write_text(
        Path("config/leagues/kyle_12_points.yaml").read_text()
        .replace("name: kyle_12_points", "name: t"))
    conn = sqlite3.connect(db)
    conn.execute("UPDATE players SET birth_date='2005-06-01' WHERE player_id=2")
    conn.execute("UPDATE players SET birth_date='1992-06-01' WHERE player_id=1")
    conn.commit()
    conn.close()
    for mod in (ins, routes, modes, rec):
        monkeypatch.setattr(mod, "DB_PATH", Path(db), raising=False)
    monkeypatch.setattr(ins, "LEAGUE_DIR", leagues)
    monkeypatch.setattr(ins, "DATA_DIR", tmp_path)
    monkeypatch.setattr(routes, "LEAGUE_DIR", leagues)
    monkeypatch.setattr(routes, "_require_db", lambda: None)
    monkeypatch.setattr(routes, "_next_season_id", lambda: 20262027)
    monkeypatch.setattr(routes, "_projections_and_values", lambda cfg, s: _base())
    monkeypatch.setattr(routes, "_load_adp", lambda: None)
    monkeypatch.setattr(modes, "_today", lambda: "2026-11-01")
    ins._CACHE.clear()
    routes._BOARDS.clear()
    modes._WINDOW.clear()
    return TestClient(routes.app)


def test_season_board_unchanged(client):
    b = client.get("/api/board?league=t").json()
    assert b["mode"]["timeframe"] == "season" and b["mode"]["window"] is None
    assert [p["id"] for p in b["players"]] == [1, 2, 3, 4, 5, 6, 7]
    assert b["players"][0]["vorp"] == 49.0


def test_next7_board_and_recommend(client):
    b = client.get("/api/board?league=t&tf=next7").json()
    assert b["mode"]["window"] == {"start": "2026-11-01", "end": "2026-11-07"}
    assert b["mode"]["value_columns"] == [["gp_regular", "GP"]]
    gp = {p["id"]: p["gp_regular"] for p in b["players"]}
    # AAA plays 11-01 and 11-03, BBB 11-02/03/04, CCC 11-01/02/04
    assert gp[1] == 2 and gp[2] == 3 and gp[3] == 3
    ranks = sorted(b["players"], key=lambda p: p["rank"])
    assert [p["vorp"] for p in ranks] == sorted((p["vorp"] for p in ranks), reverse=True)
    r = client.get("/api/recommend?league=t&tf=next7&drafted_all=2").json()
    assert len(r["recommendations"]) == 3
    assert 2 not in [x["player_id"] for x in r["recommendations"]]


def test_keeper_board_favors_the_young_player(client):
    b = client.get("/api/board?league=t&keeper=2&kdisc=0.5").json()
    assert b["mode"]["keeper"] == {"years": 2, "discount": 0.5}
    assert b["mode"]["value_columns"][:3] == [["age", "Age"], ["now", "Now"],
                                              ["future", "Future"]]
    row = {p["id"]: p for p in b["players"]}
    # Strong Center (21, rising) vs Ovechkin's slot (34, falling): same
    # base within one point, but the young player now ranks first
    assert row[2]["age"] == 21.0 and row[1]["age"] == 34.0
    assert row[2]["future"] > row[1]["future"]
    assert row[2]["rank"] < row[1]["rank"]
    assert row[2]["vorp"] == pytest.approx(row[2]["now"] + row[2]["future"], abs=0.02)


def test_bad_modes_rejected(client):
    assert client.get("/api/board?league=t&tf=nope").status_code == 422
    assert client.get("/api/board?league=t&keeper=9").status_code == 422
