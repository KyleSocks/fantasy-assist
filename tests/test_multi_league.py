"""More than one Yahoo league: a config with its own ``yahoo:`` block keeps
its files in data/leagues/<stem>/, the rest share data/. Runs on the tiny
world from tests/test_inseason.py (players: Alex Ovechkin L AAA, Strong
Center C BBB, Depth Wing R CCC, Dee Man D BBB, Dee Two D CCC, goalies)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.api.inseason as api
from src.api.main import app
from src.inseason import leagues as store
from src.inseason.yahoo_public import YPlayer
from src.valuation.league import load_league
from tests.test_inseason import db  # noqa: F401  (pytest fixture)

BASE = Path("config/leagues/kyle_12_points.yaml").read_text()


@pytest.fixture
def world(db, tmp_path, monkeypatch):  # noqa: F811
    """Leagues "main" (no yahoo block) and "side" (its own: league 777,
    team 2); the default Yahoo league is 111, team 1."""
    leagues = tmp_path / "cfg"
    leagues.mkdir()
    (leagues / "main.yaml").write_text(BASE)
    (leagues / "side.yaml").write_text(
        BASE + "\nteam_name: Side Hustle\nyahoo:\n  league_id: 777\n  team_id: 2\n")
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(api, "DB_PATH", Path(db))
    monkeypatch.setattr(api, "LEAGUE_DIR", leagues)
    monkeypatch.setattr(api, "DATA_DIR", data)
    monkeypatch.setattr(api, "DEFAULT_LEAGUE_PATH", leagues / "main.yaml")
    import src.inseason.model as model
    monkeypatch.setattr(model, "load_inseason_config",
                        lambda path=None: {"yahoo": {"league_id": 111, "team_id": 1}})
    api._CACHE.clear()
    return TestClient(app), data


def test_yahoo_block_parses_and_is_validated(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text(BASE + "\nyahoo:\n  league_id: 777\n  team_id: 2\n")
    cfg = load_league(str(p))
    assert (cfg.yahoo.league_id, cfg.yahoo.team_id, cfg.yahoo.free_agent_pages) == (777, 2, 0)
    assert load_league("config/leagues/kyle_12_points.yaml").yahoo is None
    p.write_text(BASE + "\nyahoo:\n  league_id: 0\n  team_id: 2\n")
    with pytest.raises(ValueError):
        load_league(str(p))


def test_link_and_folder_rules(tmp_path):
    own = load_league(str(_write(tmp_path, "yahoo:\n  league_id: 777\n  team_id: 2\n")))
    plain = load_league("config/leagues/kyle_12_points.yaml")
    icfg = {"yahoo": {"league_id": 111, "team_id": 1, "free_agent_pages": 3}}
    assert store.yahoo_link(own, icfg) == {
        "league_id": 777, "team_id": 2, "free_agent_pages": 0, "own": True}
    assert store.yahoo_link(plain, icfg) == {
        "league_id": 111, "team_id": 1, "free_agent_pages": 3, "own": False}
    assert store.yahoo_link(plain, {}) is None
    assert store.data_dir(tmp_path, "side", own) == tmp_path / "leagues" / "side"
    assert store.data_dir(tmp_path, "main", plain) == tmp_path


def _write(tmp_path, extra):
    p = tmp_path / "own.yaml"
    p.write_text(BASE + "\n" + extra)
    return p


def test_lists_are_kept_per_league(world):
    c, data = world
    c.put("/api/inseason/lists?league=main", json={
        "roster": "Alex Ovechkin | AAA | LW\n", "free_agents": "Strong Center | BBB | C\n"})
    c.put("/api/inseason/lists?league=side", json={
        "roster": "Dee Man | BBB | D\n", "free_agents": "Depth Wing | CCC | RW\n"})
    assert (data / "my_roster.txt").read_text() == "Alex Ovechkin | AAA | LW\n"
    assert (data / "leagues" / "side" / "my_roster.txt").read_text() == "Dee Man | BBB | D\n"
    assert c.get("/api/inseason/lists?league=main").json() == {
        "roster": "Alex Ovechkin | AAA | LW\n",
        "free_agents": "Strong Center | BBB | C\n", "folder": "data"}
    assert c.get("/api/inseason/lists?league=side").json()["folder"] == "data/leagues/side"
    q = "/api/inseason/audit?start=2026-11-01&days=4&league="
    assert [p["name"] for p in c.get(q + "main").json()["players"]] == ["Alex Ovechkin"]
    assert [p["name"] for p in c.get(q + "side").json()["players"]] == ["Dee Man"]
    fa = "/api/inseason/free-agents?start=2026-11-01&days=4&league="
    assert [p["name"] for p in c.get(fa + "main").json()["players"]] == ["Strong Center"]
    assert [p["name"] for p in c.get(fa + "side").json()["players"]] == ["Depth Wing"]


def test_leagues_endpoint_describes_each_config(world):
    c, data = world
    (data / "league_rosters.json").write_text(json.dumps({
        "league_id": 111, "my_team_id": 1, "fetched": "2026-11-16T09:00",
        "teams": {"1": {"name": "Main Squeeze", "players": []}}}))
    r = c.get("/api/inseason/leagues").json()
    assert r["leagues"] == ["main", "side"] and r["default"] == "main"
    assert r["info"]["main"] == {
        "scoring_type": "points", "team_count": 12,
        "yahoo": {"league_id": 111, "team_id": 1, "free_agent_pages": 0, "own": False},
        "team_name": "Main Squeeze",           # from Yahoo: no team_name set
        "rosters_fetched": "2026-11-16T09:00"}
    assert r["info"]["side"]["yahoo"]["league_id"] == 777
    assert r["info"]["side"]["team_name"] == "Side Hustle"   # config wins
    assert r["info"]["side"]["rosters_fetched"] is None


def _fake_yahoo(monkeypatch, rosters):
    """rosters: {yahoo league id: {team id: (name, [YPlayer])}}; records
    which (league, team) the refresh asked for."""
    import src.inseason.ingest as ing
    import src.inseason.yahoo_public as yp
    asked = []
    monkeypatch.setattr(ing, "ingest_current", lambda db, **kw: {})

    def lists(league_id, team_id, pages=0, **kw):
        asked.append((league_id, team_id))
        return list(rosters[league_id][team_id][1]), [YPlayer("Depth Wing", "CCC", ("RW",))]
    monkeypatch.setattr(yp, "fetch_lists", lists)
    monkeypatch.setattr(yp, "fetch_league", lambda league_id: {
        t: {"name": n, "players": list(ps)} for t, (n, ps) in rosters[league_id].items()})
    monkeypatch.setattr(yp, "fetch_market", lambda league_id: [])
    return asked


OVI = YPlayer("Alex Ovechkin", "AAA", ("LW",), "LW")
DEE = YPlayer("Dee Man", "BBB", ("D",), "D")
SC = YPlayer("Strong Center", "BBB", ("C",), "C")
TWO = YPlayer("Dee Two", "CCC", ("D",), "D")


def test_refresh_pulls_each_linked_league_into_its_own_folder(world, monkeypatch):
    c, data = world
    asked = _fake_yahoo(monkeypatch, {
        111: {1: ("Main Squeeze", [OVI]), 2: ("Others", [SC])},
        777: {2: ("Side Hustle", [DEE]), 3: ("Rivals", [TWO])}})
    r = c.post("/api/inseason/refresh").json()          # the timer's call
    assert asked == [(111, 1), (777, 2)]
    assert r["league"] == "main" and r["yahoo_error"] is None
    assert set(r["leagues"]) == {"main", "side"}
    assert r["leagues"]["side"]["yahoo"]["roster"] == 1
    assert (data / "my_roster.txt").read_text().endswith("Alex Ovechkin | AAA | LW\n")
    side = data / "leagues" / "side"
    assert (side / "my_roster.txt").read_text().endswith("Dee Man | BBB | D\n")
    assert json.loads((side / "league_rosters.json").read_text())["my_team_id"] == 2
    # the button's call: only the league it names
    asked.clear()
    r = c.post("/api/inseason/refresh?league=side").json()
    assert asked == [(777, 2)] and "leagues" not in r and r["league"] == "side"


def test_refresh_logs_moves_and_the_endpoint_reports_them(world, monkeypatch):
    """Pull 1 is the baseline. Before pull 2, Others let Strong Center go and
    Main Squeeze pick him up, sending Alex Ovechkin to IR -- so the log
    reads: Main Squeeze claimed Strong Center (last on Others; nothing went
    back, so not a trade) and moved Ovechkin to IR."""
    c, data = world
    rosters = {111: {1: ("Main Squeeze", [OVI]), 2: ("Others", [SC, DEE])}}
    _fake_yahoo(monkeypatch, rosters)
    assert c.get("/api/inseason/moves?league=main").status_code == 400   # no pull yet
    assert c.post("/api/inseason/refresh?league=main").json()["moves"] == 0
    first = c.get("/api/inseason/moves?league=main").json()
    assert first["events"] == [] and first["tracking_since"] == first["updated"]

    rosters[111] = {1: ("Main Squeeze", [YPlayer("Alex Ovechkin", "AAA", ("LW",), "IR+"), SC]),
                    2: ("Others", [DEE])}
    assert c.post("/api/inseason/refresh?league=main").json()["moves"] == 2
    m = c.get("/api/inseason/moves?league=main&days=7").json()
    got = [(e["type"], e["team"], e["from_team"], e["player"]["name"], e["player"]["id"])
           for e in m["events"]]
    assert sorted(got) == [("claim", "Main Squeeze", "Others", "Strong Center", 2),
                           ("ir", "Main Squeeze", None, "Alex Ovechkin", 1)]
    assert m["tracking_since"] == first["tracking_since"] and m["my_team_id"] == 1
    by = {t["name"]: t for t in m["teams"]}
    assert (by["Main Squeeze"]["add"], by["Main Squeeze"]["ir"], by["Main Squeeze"]["mine"]) == (1, 1, True)
    assert (by["Others"]["trade"], by["Others"]["add"], by["Others"]["drop"]) == (0, 0, 1)
    assert all(e["player"]["per_game"] is not None for e in m["events"])
    # the other league's log is its own
    assert c.get("/api/inseason/moves?league=side").status_code == 400


def test_refresh_refuses_a_pull_where_a_team_came_back_empty(world, monkeypatch):
    c, data = world
    rosters = {111: {1: ("Main Squeeze", [OVI]), 2: ("Others", [SC])}}
    _fake_yahoo(monkeypatch, rosters)
    c.post("/api/inseason/refresh?league=main")
    before = (data / "league_rosters.json").read_text()
    rosters[111] = {1: ("Main Squeeze", [OVI]), 2: ("Others", [])}
    r = c.post("/api/inseason/refresh?league=main").json()
    assert "no players came back for Others" in r["yahoo_error"]
    assert (data / "league_rosters.json").read_text() == before
    assert c.post("/api/inseason/refresh?league=main&strict=1").status_code == 502


def test_league_without_any_yahoo_link_says_how_to_add_one(world, monkeypatch):
    c, _ = world
    import src.inseason.model as model
    monkeypatch.setattr(model, "load_inseason_config", lambda path=None: {})
    _fake_yahoo(monkeypatch, {})
    r = c.post("/api/inseason/refresh?league=main").json()
    assert "config/leagues/main.yaml" in r["yahoo_error"] and r["yahoo"] is None


def test_roster_tab_can_show_any_team(world, monkeypatch):
    """After a pull, ?team= shows another team's roster from the league
    snapshot (IR slots kept as IR); no team / your id = your own list."""
    c, data = world
    _fake_yahoo(monkeypatch, {111: {
        1: ("Main Squeeze", [OVI]),
        2: ("Others", [SC, YPlayer("Dee Man", "BBB", ("D",), "IR+")])}})
    c.post("/api/inseason/refresh?league=main")
    q = "/api/inseason/audit?start=2026-11-01&days=4&league=main"
    mine = c.get(q).json()
    assert [p["name"] for p in mine["players"]] == ["Alex Ovechkin"]
    assert mine["team"] == {"id": 1, "name": "Main Squeeze", "mine": True}
    assert [t["name"] for t in mine["teams"]] == ["Main Squeeze", "Others"]   # yours first
    assert c.get(q + "&team=1").json()["players"] == mine["players"]
    other = c.get(q + "&team=2").json()
    assert other["team"] == {"id": 2, "name": "Others", "mine": False}
    by = {p["name"]: p for p in other["players"]}
    assert set(by) == {"Strong Center", "Dee Man"}
    assert by["Dee Man"]["verdict"].startswith("IR") or by["Dee Man"].get("inactive")
    assert c.get(q + "&team=9").status_code == 404
