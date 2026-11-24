"""Tests for the live draft sync: src/draft/live.py + src/api/draft_live.py.

The page below is a hand-written stand-in with the structure the parser
relies on (rows holding a /players/<id> link, a /hockey/<league>/<team>
link and a "Tor - C" tag), not a captured Yahoo page.
"""
from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import draft_live
from src.draft.live import Roster, board_log, fold, match_picks, parse_draft_results

PAGE = """
<table><tr><th>Pick</th><th>Player</th><th>Team</th></tr>
<tr><td>1.</td><td><a href="https://sports.yahoo.com/nhl/players/6743/"><img/></a>
 <a href="https://sports.yahoo.com/nhl/players/6743/" class="name">Connor McDavid</a>
 <span class="Fz-xxs">Edm - C</span></td>
 <td><a href="/hockey/19725/3">Oilers Fan</a></td></tr>
<tr><td>2.</td><td><a href="https://sports.yahoo.com/nhl/players/7000/">Sebastian Aho</a>
 <span>(Car - C,LW)</span></td><td><a href="/hockey/19725/5">Team Name Loading&hellip;</a></td></tr>
<tr><td>3.</td><td><a class="Nowrap name F-link" href="#">Matt Boldy</a>
 <span class="Fz-xxs">Min - LW</span></td><td><a href="/hockey/19725/7/team?x=1">Wild Ones</a></td></tr>
<tr><td>4.</td><td><a href="/players/1/">Nobody Known</a></td><td><a href="/hockey/19725/9">T9</a></td></tr>
<tr><td>5.</td><td><a href="https://sports.yahoo.com/nhl/players/6743/">Connor McDavid</a></td></tr>
</table>"""

ROSTER = Roster((
    (97, "Connor McDavid", "C", "EDM"),
    (20, "Sebastian Aho", "C", "CAR"),
    (25, "Sebastian Aho", "D", "NYI"),
    (12, "Matthew Boldy", "L", "MIN"),
    (13, "Matthew Boldt", "L", "BOS"),
))


def test_parse_rows_in_page_order():
    picks = parse_draft_results(PAGE)
    # header row has no player; the repeated McDavid row is dropped
    assert [p.name for p in picks] == ["Connor McDavid", "Sebastian Aho",
                                       "Matt Boldy", "Nobody Known"]
    assert [p.pick for p in picks] == [1, 2, 3, 4]
    mc, aho, boldy, _ = picks
    assert (mc.yahoo_id, mc.team_id, mc.nhl_team, mc.positions) == (6743, 3, "EDM", ("C",))
    assert aho.team_name == "Team Name Loading…" and aho.positions == ("C", "LW")
    assert (boldy.team_id, boldy.yahoo_id, boldy.nhl_team) == (7, None, "MIN")


def test_match_exact_team_and_loose_initial():
    picks = match_picks(parse_draft_results(PAGE), ROSTER)
    ids = [p.player_id for p in picks]
    # two Sebastian Ahos: the CAR one; "Matt" -> Matthew Boldy (MIN)
    assert ids == [97, 20, 12, None]
    log = board_log(picks, my_team_id=5)
    assert log == [{"player_id": 97, "dest": "taken", "pick": 1},
                   {"player_id": 20, "dest": "me", "pick": 2},
                   {"player_id": 12, "dest": "taken", "pick": 3}]
    assert fold("Tim Stützle") == "tim stutzle"


T0 = datetime.datetime(2026, 9, 20, 20, 0, tzinfo=datetime.UTC)


def test_save_post_keeps_picks_through_an_empty_page(tmp_path):
    st = draft_live.save_post("19725", "u", PAGE, tmp_path, T0)
    assert len(st["picks"]) == 4
    st = draft_live.save_post("19725", "u", "<html>oops</html>", tmp_path, T0)
    assert len(st["picks"]) == 4                       # hiccup ignored
    st = draft_live.save_post("19725", "u", None, tmp_path,
                              T0 + datetime.timedelta(seconds=5))
    assert st["seen_at"].endswith("20:00:05+00:00") and len(st["picks"]) == 4
    assert (tmp_path / "19725.last.html").read_text() == "<html>oops</html>"


def test_room_choice_and_connected(tmp_path):
    draft_live.save_post("19725", "u", PAGE, tmp_path, T0)
    draft_live.save_post("room", "mock", PAGE, tmp_path,
                         T0 + datetime.timedelta(minutes=1))
    # the league's own room while it's fresh (once the other room is quiet),
    # else the freshest room
    quiet = T0 + datetime.timedelta(minutes=1, seconds=draft_live.LIVE_WINS_S + 1)
    assert draft_live.load_room(19725, tmp_path, quiet)["room"] == "19725"
    later = T0 + datetime.timedelta(hours=draft_live.FOLLOW_ANY_H, seconds=30)
    assert draft_live.load_room(19725, tmp_path, later)["room"] == "room"
    assert draft_live.load_room(None, tmp_path, later + datetime.timedelta(hours=1)) is None
    own = draft_live.load_room(19725, tmp_path, quiet)
    v = draft_live.room_view(own, ROSTER, 5,
                             T0 + datetime.timedelta(seconds=draft_live.CONNECTED_S))
    assert v["connected"] and v["count"] == 4 and v["unmatched"] == ["Nobody Known"]
    assert v["teams"] == {3: "Oilers Fan", 5: "Team Name Loading…", 7: "Wild Ones", 9: "T9"}
    v = draft_live.room_view(own, ROSTER, 5,
                             T0 + datetime.timedelta(seconds=draft_live.CONNECTED_S + 1))
    assert not v["connected"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(draft_live, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(draft_live, "TOKEN_PATH", tmp_path / "tok.txt")
    from src.api.main import app
    return TestClient(app, base_url="http://localhost")


def test_post_needs_token_and_passes_the_origin_guard(client, tmp_path):
    body = {"url": "https://hockey.fantasysports.yahoo.com/hockey/19725/draftresults",
            "html": PAGE}
    ext = {"Origin": "chrome-extension://abcdef"}
    assert client.post("/api/draft/live", json=body).status_code == 401
    # wrong token from the extension's origin: the cross-site guard stops it
    r = client.post("/api/draft/live", json=body, headers=dict(ext, **{"X-Draft-Token": "x"}))
    assert r.status_code == 403
    tok = client.get("/api/draft/token").json()["token"]
    assert tok == (tmp_path / "tok.txt").read_text().strip()
    r = client.post("/api/draft/live", json=body, headers=dict(ext, **{"X-Draft-Token": tok}))
    assert r.status_code == 200 and r.json()["room"] == "19725" and r.json()["picks"] == 4
    # other writes from that origin are still refused, token or not
    r = client.post("/api/inseason/refresh", headers=dict(ext, **{"X-Draft-Token": tok}))
    assert r.status_code == 403
    live = client.get("/api/draft/live", params={"league": "kyle_12_points"}).json()
    assert live["room"] == "19725" and live["connected"] and live["count"] == 4
    assert live["my_team_id"] == 5     # config/inseason.yaml yahoo.team_id


# ── the real Yahoo pages (tests/fixtures, trimmed from league 19725) ──

FIX = Path(__file__).parent / "fixtures"
REAL = Roster((
    (1, "Nathan MacKinnon", "C", "COL"), (2, "Jason Robertson", "L", "DAL"),
    (3, "Evan Bouchard", "D", "EDM"), (4, "Nikita Kucherov", "R", "TBL"),
    (5, "Macklin Celebrini", "C", "SJS"), (6, "Jack Hughes", "C", "NJD"),
    (7, "Quinn Hughes", "D", "MIN"), (8, "Lane Hutson", "D", "MTL"),
))


def test_real_results_page_your_picks_by_team_name(tmp_path, monkeypatch):
    monkeypatch.setattr(draft_live, "IMPORTED_LEAGUE_DIR", tmp_path / "imp")
    page = (FIX / "yahoo_draftresults_19725.html").read_text(encoding="utf-8")
    nav = (FIX / "yahoo_league_nav_19725.html").read_text(encoding="utf-8")
    settings = (FIX / "yahoo_settings_19725.html").read_text(encoding="utf-8")
    st = draft_live.save_post("19725", "u", page, tmp_path, T0, league_id=19725,
                              league_html=nav, settings_html=settings)
    assert len(st["picks"]) == 24 and st["my_team_id"] == 5
    # rounds restart their pick numbers; page order is the draft order
    assert [p["name"] for p in st["picks"][11:13]] == ["Auston Matthews", "Cole Caufield"]
    v = draft_live.room_view(st, REAL, None, T0)
    mine = [e["player_id"] for e in v["log"] if e["dest"] == "me"]
    # Robertson's Rules of Hockey (team 5): Jason Robertson 1.11, Evan Bouchard 2.2
    assert mine == [2, 3]
    assert {e["player_id"] for e in v["log"]} == {1, 2, 3, 4, 5, 6, 7, 8}
    # "SJ"/"NJ"/"TB" are Yahoo's abbreviations; Jack vs Quinn Hughes by team
    assert v["teams"][5] == "Robertson’s Rules of Hockey"
    assert v["settings"]["scoring_type"] == "points"


def test_settings_import_or_compare(tmp_path, monkeypatch):
    imp = tmp_path / "imp"
    monkeypatch.setattr(draft_live, "IMPORTED_LEAGUE_DIR", imp)
    settings = (FIX / "yahoo_settings_19725.html").read_text(encoding="utf-8")
    # your league (config/inseason.yaml follows 19725): compared, not written
    st = draft_live.save_post("19725", "u", None, tmp_path, T0, league_id=19725,
                              settings_html=settings)
    draft_live.import_settings(st, 19725, imp)
    assert st["settings"]["board_league"] == "kyle_12_points"
    assert st["settings"]["differences"] == [] and not imp.exists()
    # a mock room: written as the yahoo_mock board league
    mock = (FIX / "yahoo_settings_mock_cat.html").read_text(encoding="utf-8")
    st = draft_live.save_post("2655393", "u", None, tmp_path, T0, league_id=2655393,
                              settings_html=mock, mock=True, my_team_id=5)
    draft_live.import_settings(st, 2655393, imp)
    assert st["settings"]["board_league"] == "yahoo_mock" and st["my_team_id"] == 5
    from src.valuation.league import load_league
    cfg = load_league(str(imp / "yahoo_mock.yaml"))
    assert cfg.scoring_type == "h2h_categories" and cfg.yahoo is None
    assert "SV%" in cfg.categories


def test_mock_room_mid_draft(tmp_path):
    page = (FIX / "yahoo_draftresults_mock.html").read_text(encoding="utf-8")
    st = draft_live.save_post("2655393", "u", page, tmp_path, T0, league_id=2655393,
                              mock=True, my_team_id=5)
    # 18 made picks; the 6 "--empty--" slots aren't picks
    assert len(st["picks"]) == 18
    assert st["teams"]["5"] == "Kyle" and st["teams"]["1"] == "Team 1"
    roster = Roster(((1, "Evan Bouchard", "D", "EDM"), (2, "Connor McDavid", "C", "EDM"),
                     (3, "Matt Boldy", "L", "MIN")))
    v = draft_live.room_view(st, roster, None, T0)
    assert v["mock"] and v["my_team_id"] == 5
    assert v["log"] == [{"player_id": 2, "dest": "taken", "pick": 1},
                        {"player_id": 1, "dest": "me", "pick": 5},
                        {"player_id": 3, "dest": "taken", "pick": 16}]


def test_open_room_beats_older_league_data(tmp_path):
    draft_live.save_post("19725", "u", PAGE, tmp_path, T0)
    later = T0 + datetime.timedelta(hours=1)
    draft_live.save_post("2655393", "u", PAGE, tmp_path, later, mock=True)
    assert draft_live.load_room(19725, tmp_path, later)["room"] == "2655393"
    # once the mock goes quiet, back to the league's own room
    quiet = later + datetime.timedelta(seconds=draft_live.LIVE_WINS_S + 1)
    assert draft_live.load_room(19725, tmp_path, quiet)["room"] == "19725"
