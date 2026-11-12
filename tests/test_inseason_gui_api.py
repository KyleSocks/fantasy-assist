"""GUI endpoints: lists round-trip, roster audit, plan -- on the tiny
hand-computed world from tests/test_inseason.py."""
import pytest
from pathlib import Path

from fastapi.testclient import TestClient

import src.api.inseason as api
from src.api.main import app
from tests.test_inseason import db  # noqa: F401  (pytest fixture)


def _client(db, tmp_path, monkeypatch):  # noqa: F811
    leagues = tmp_path / "leagues"
    leagues.mkdir()
    (leagues / "t.yaml").write_text(
        Path("config/leagues/kyle_12_points.yaml").read_text())
    monkeypatch.setattr(api, "DB_PATH", Path(db))
    monkeypatch.setattr(api, "LEAGUE_DIR", leagues)
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    api._CACHE.clear()
    return TestClient(app)


def test_lists_audit_and_plan(db, tmp_path, monkeypatch):  # noqa: F811
    c = _client(db, tmp_path, monkeypatch)
    assert c.get("/api/inseason/leagues").json()["leagues"] == ["t"]
    assert c.get("/inseason").status_code == 200
    r = c.put("/api/inseason/lists?league=t", json={
        "roster": "# mine\nAlex Ovechkin | AAA | LW\nDee Man | BBB | D\n",
        "free_agents": "Strong Center | BBB | C\nNobody\n"})
    assert r.json() == {"saved": True, "unmatched": ["Nobody"]}
    assert (tmp_path / "my_roster.txt").read_text().startswith("# mine")
    assert c.get("/api/inseason/lists").json()["free_agents"].startswith("Strong")

    a = c.get("/api/inseason/audit?league=t&start=2026-11-01&days=4").json()
    assert {p["name"] for p in a["players"]} == {"Alex Ovechkin", "Dee Man"}
    assert len(a["nights"]) == 4 and a["unmatched"] == ["Nobody"]
    ovi = next(p for p in a["players"] if p["name"] == "Alex Ovechkin")
    # AAA plays twice in the window; alone at LW, so he starts both
    assert ovi["window_games"] == 2 and ovi["starts"] == 2
    assert ovi["lineup_adds"] > 0

    p = c.get("/api/inseason/plan?league=t&hold=1&adds=2&start=2026-11-01&days=4")
    body = p.json()
    assert p.status_code == 200 and body["hold"]["name"] == "Alex Ovechkin"
    assert len(body["nightly"]) == 4 and body["plan_value"] >= body["keep_value"]
    assert c.get("/api/inseason/plan?league=t&hold=999").status_code == 404
    assert c.get("/api/inseason/plan?league=t&hold=1&adds=x").status_code == 422


def test_audit_without_roster_is_400(db, tmp_path, monkeypatch):  # noqa: F811
    c = _client(db, tmp_path, monkeypatch)
    assert c.get("/api/inseason/audit?league=t").status_code == 400


def test_goalies_show_up_in_audit_plan_and_free_agents(db, tmp_path, monkeypatch):  # noqa: F811
    """Goalies in the list files (as Refresh now writes them) reach the
    roster audit with real lineup value, can be planned, and are listed as
    free agents."""
    c = _client(db, tmp_path, monkeypatch)
    c.put("/api/inseason/lists?league=t", json={
        "roster": "Alex Ovechkin | AAA | LW\nDee Man | BBB | D\n"
                  "Goalie B | BBB | G\n",
        "free_agents": "Strong Center | BBB | C\nGoalie A | AAA | G\n"})
    a = c.get("/api/inseason/audit?league=t&start=2026-11-01&days=4").json()
    g = next(p for p in a["players"] if p["name"] == "Goalie B")
    assert g["starts"] > 0 and g["lineup_adds"] > 0
    fa = c.get("/api/inseason/free-agents?league=t&start=2026-11-01&days=4").json()
    assert "Goalie A" in {p["name"] for p in fa["players"]}
    p = c.get("/api/inseason/plan?league=t&hold=7&adds=2&start=2026-11-01"
              "&days=4&force=true").json()
    assert p["keep_value"] > 0


def test_trade_options_endpoint(db, tmp_path, monkeypatch):  # noqa: F811
    import json

    from tests.test_inseason_teams import LEAGUE
    c = _client(db, tmp_path, monkeypatch)
    assert c.get("/api/inseason/trade-options?league=t&give=1").status_code == 400
    (tmp_path / "league_rosters.json").write_text(json.dumps(dict(LEAGUE, my_team_id=1)))
    r = c.get("/api/inseason/trade-options?league=t&give=1&start=2026-11-01&days=4")
    assert r.status_code == 200 and r.json()["give"]["name"] == "Alex Ovechkin"
    assert c.get("/api/inseason/trade-options?league=t&give=2").status_code == 400
    assert c.get("/api/inseason/trade-options?league=t&give=999").status_code == 404
    r = c.get("/api/inseason/partner-trades?league=t&partner=2&start=2026-11-01&days=4"
              "&give=3,4&get=7")
    assert r.status_code == 200
    j = r.json()
    assert j["partner"]["name"] == "Theirs" and j["offer"]["get"] == [7]
    assert c.get("/api/inseason/partner-trades?league=t&partner=1").status_code == 400
    assert c.get("/api/inseason/partner-trades?league=t&partner=2&give=x").status_code == 422


def test_streams_report_what_they_add_to_my_lineup(db, tmp_path, monkeypatch):  # noqa: F811
    c = _client(db, tmp_path, monkeypatch)
    r = c.get("/api/inseason/streams?league=t&date=2026-11-02&all=true").json()
    assert all(p["adds"] is None for p in r["players"])        # no roster yet
    c.put("/api/inseason/lists?league=t", json={
        "roster": "Alex Ovechkin | AAA | LW\n", "free_agents": ""})
    r = c.get("/api/inseason/streams?league=t&date=2026-11-02&all=true").json()
    assert r["players"] and all(p["adds"] is not None and "elig" in p
                                for p in r["players"])
    # an empty lineup takes anyone who plays: adds == his whole game
    for p in r["players"]:
        assert p["adds"] == pytest.approx(p["value"])
    a = c.get("/api/inseason/audit?league=t&start=2026-11-02&days=1").json()
    assert a["nights"][0]["can_fit"]["F"] >= 1


def test_replacement_follows_the_free_agent_file(db, tmp_path, monkeypatch):  # noqa: F811
    """Saving the free-agent list re-sets replacement to the best healthy
    one (Depth Wing; Strong Center is OUT). No file = the formula, which
    in this 7-player world with 12 teams' pools is 0 (nobody is outside)."""
    c = _client(db, tmp_path, monkeypatch)
    q = "/api/inseason/player/1?league=t&start=2026-11-01&days=4"
    assert c.get(q).json()["replacement_pg"] == 0.0
    c.put("/api/inseason/lists?league=t", json={
        "roster": "Alex Ovechkin | AAA | LW\n",
        "free_agents": "Depth Wing | CCC | RW\nStrong Center | BBB | C | OUT\n"})
    depth = api._ctx("t").scorer.per_game[3]
    assert c.get(q).json()["replacement_pg"] == pytest.approx(depth)
