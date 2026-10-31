"""GUI endpoints: lists round-trip, roster audit, plan -- on the tiny
hand-computed world from tests/test_inseason.py."""
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
