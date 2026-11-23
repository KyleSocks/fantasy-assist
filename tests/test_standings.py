"""Standings, playoff settings and playoff odds (src/inseason/standings.py).
Fixtures are the real markup of the Yahoo league home page's standings
table and the settings page's Playoffs row (league 19725, Nov 2026)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.inseason import standings as S
from tests.test_inseason import db  # noqa: F401
from tests.test_multi_league import world  # noqa: F401

FIX = Path(__file__).parent / "fixtures"


def test_parse_standings_real_markup():
    rows = S.parse_standings((FIX / "yahoo_standings_table.html").read_text())
    assert len(rows) == 12
    assert rows[0] == {"team_id": 5, "name": "Robertson’s Rules of Hockey", "rank": 1,
                       "w": 8, "l": 0, "t": 0, "pct": 1.0, "pts": 1998.2}
    assert rows[3]["name"] == "Jake n Bake" and (rows[3]["w"], rows[3]["l"]) == (5, 3)
    assert rows[-1]["name"] == "Delaney's Peerless Team" and rows[-1]["pts"] == 1422.9
    assert S.parse_standings("<html>no table</html>") == []


def test_parse_playoffs_real_markup():
    assert S.parse_playoffs((FIX / "yahoo_settings_playoffs.html").read_text()) == {
        "teams": 6, "weeks": [24, 25, 26]}
    assert S.parse_playoffs("<td>Playoffs:</td><td><b>No playoffs</b></td>") is None


def _st(rows, weeks=(24, 25, 26), teams=2):
    return {"teams": [{"team_id": i, "name": f"T{i}", "rank": i, "w": w, "l": l, "t": 0,
                       "pct": None, "pts": p} for i, (w, l, p) in enumerate(rows, 1)],
            "playoffs": {"teams": teams, "weeks": list(weeks)}}


def test_odds_with_no_weeks_left_is_the_table():
    """Week 24 is the first playoff week and 23 weeks are played: nothing
    left, so the top 2 by wins (then points for) are in. T2 and T3 tie on
    wins; T3 has more points for."""
    o = S.odds(_st([(20, 3, 2000), (12, 11, 1500), (12, 11, 1600), (5, 18, 1000)]))
    assert o["weeks_left"] == 0 and o["spots"] == 2
    assert {t: round(v["odds"], 3) for t, v in o["teams"].items()} == {1: 1.0, 2: 0.0, 3: 1.0, 4: 0.0}
    assert o["teams"][3]["mean_seed"] == 2


def test_odds_far_ahead_and_far_behind():
    """8 weeks played, 15 left, 6 of 12 make it. 8-0 with top points for
    is all but in; 0-8 with the fewest is all but out; a .500 team is in
    between."""
    rows = [(8, 0, 2000)] + [(4, 4, 1800)] * 10 + [(0, 8, 1400)]
    o = S.odds(_st(rows, teams=6), {"sims": 2000})
    assert o["weeks_played"] == 8 and o["weeks_left"] == 15
    assert o["teams"][1]["odds"] > 0.95
    assert o["teams"][12]["odds"] < 0.05
    assert 0.2 < o["teams"][5]["odds"] < 0.8
    assert sum(v["odds"] for v in o["teams"].values()) == pytest.approx(6)
    # same input, same numbers (fixed seed)
    assert S.odds(_st(rows, teams=6), {"sims": 2000}) == o


def test_focus_from_odds():
    assert S.focus_for(0.97) == "playoffs" and S.focus_for(0.9) == "playoffs"
    assert S.focus_for(0.6) == "balanced" and S.focus_for(0.2) == "make_playoffs"
    assert S.focus_for(None) == "balanced"
    assert S.focus_for(0.8, {"safe_odds": 0.75}) == "playoffs"


def test_refresh_stores_standings_and_auto_focus(monkeypatch, request):
    """Refresh saves standings.json; with trade_focus auto, my team (1, 8-0
    and the most points with 15 weeks left) is a lock -> "playoffs"; the
    endpoint lists everyone's odds."""
    world = request.getfixturevalue("world")
    from tests.test_multi_league import OVI, SC, _fake_yahoo
    c, data = world
    import src.inseason.model as model
    monkeypatch.setattr(model, "load_inseason_config", lambda path=None: {
        "yahoo": {"league_id": 111, "team_id": 1}, "trade_focus": "auto", "standings": {"sims": 500}})
    rows = [(8, 0, 2000), (4, 4, 1800), (4, 4, 1700), (0, 8, 1300)]
    st = _st(rows, teams=2)
    monkeypatch.setattr(S, "fetch_standings", lambda league_id, client=None: {**st, "fetched": "2026-11-23T09:00"})
    _fake_yahoo(monkeypatch, {111: {1: ("Main Squeeze", [OVI]), 2: ("Others", [SC])}})
    r = c.post("/api/inseason/refresh?league=main").json()
    assert r.get("standings") == 4, r
    assert (data / "main" / "standings.json").exists() or any(data.rglob("standings.json"))
    d = c.get("/api/inseason/standings?league=main").json()
    s = d["standings"]
    assert s["my_team_id"] == 1 and s["weeks_left"] == 15 and s["spots"] == 2
    assert s["my_odds"] > 0.9 and s["focus"] == "playoffs" and d["focus"] == "playoffs"
    assert [t["team_id"] for t in s["teams"]] == [1, 2, 3, 4] and s["teams"][0]["mine"]
    assert s["teams"][3]["odds"] < 0.1
