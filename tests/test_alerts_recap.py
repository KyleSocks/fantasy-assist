"""Alerts about your team (src/inseason/alerts.py) and the weekly recap
endpoint."""
import datetime
import json

from src.inseason import alerts
from src.inseason.model import Line
from tests.test_inseason import db  # noqa: F401  (fixture)
from tests.test_multi_league import OVI, SC, DEE, YPlayer, _fake_yahoo, world  # noqa: F401


class _Ctx:
    def __init__(self):
        self.lines = {1: Line(1, "Alex Ovechkin", "L", "AAA", 10, {}, {}, {}),
                      7: Line(7, "Free Guy", "C", "BBB", 10, {}, {}, {}),
                      9: Line(9, "Net Man", "G", "AAA", 10, {}, {}, {}, start_share=.7),
                      10: Line(10, "Backup", "G", "AAA", 10, {}, {}, {}, start_share=.3)}
        self.roles = {1: {"change": "up", "line": "F1", "pp": "PP1", "date": "2026-11-19"},
                      7: {"change": "up", "pp": "PP1", "usual_pp": "PP2", "date": "2026-11-19"}}
        self.icfg = {"goalie_b2b_starter_factor": 0.5}
        self._g = ["2026-11-21", "2026-11-22"]

    def team_games(self, team, a, b):
        return [(d, "X") for d in self._g if a <= d <= b] if team == "AAA" else []


def snap(at, players):
    return {"fetched": at, "my_team_id": 1, "teams": {"1": {"name": "Me", "players": players}}}


def P(name, team, injury=None, inactive=False):
    return {"name": name, "team": team, "positions": ["C"], "injury": injury, "inactive": inactive}


def test_alerts_hand_worked():
    """Ovechkin picks up a DTD tag; Net Man goes to IR; Ovechkin was
    promoted to F1, PP1; free agent Free Guy moved up to PP1; Net Man's
    team plays Nov 21 and 22, so tomorrow (22nd) is a back-to-back: start
    chance .7 x .5 = 35%."""
    old = snap("t1", [P("Alex Ovechkin", "AAA"), P("Net Man", "AAA")])
    new = snap("t2", [P("Alex Ovechkin", "AAA", "DTD"), P("Net Man", "AAA", inactive=True)])
    a = alerts.make(old, new, _Ctx(), {"Alex Ovechkin|AAA": 1, "Net Man|AAA": 9}, {7},
                    datetime.date(2026, 11, 21))
    assert [x["text"] for x in a] == [
        "Alex Ovechkin: now DTD",
        "Net Man moved to IR",
        "Alex Ovechkin promoted to F1, PP1 (last game 2026-11-19)",
        "Free agent Free Guy (BBB) moved up to PP1",
        "Net Man: AAA is on a back-to-back tomorrow, start chance about 35% (usually 70%)"]
    # first pull: nothing to compare tags with, the rest still applies
    first = alerts.make(None, new, _Ctx(), {"Alex Ovechkin|AAA": 1}, set(),
                        datetime.date(2026, 11, 21))
    assert [x["kind"] for x in first] == ["role"]
    log, fresh = alerts.merge(None, a)
    assert len(fresh) == 5
    log, fresh = alerts.merge(log, a)          # same alerts again: not new
    assert fresh == [] and len(log["items"]) == 5


def test_alerts_are_stored_and_sent(world, monkeypatch):  # noqa: F811
    c, data = world
    rosters = {111: {1: ("Main Squeeze", [OVI]), 2: ("Others", [SC, DEE])}}
    _fake_yahoo(monkeypatch, rosters)
    sent = []
    import src.inseason.alerts as al
    monkeypatch.setattr(al, "send", lambda url, items, **k: sent.append((url, [i["text"] for i in items])))
    import src.api.inseason as api
    orig = api._ctx

    def ctx_with_url(name=None):
        ctx = orig(name)
        ctx.icfg = {**ctx.icfg, "alerts": {"url": "http://ntfy.local/fa"}}
        return ctx
    monkeypatch.setattr(api, "_ctx", ctx_with_url)
    c.post("/api/inseason/refresh?league=main")
    rosters[111][1] = ("Main Squeeze", [YPlayer("Alex Ovechkin", "AAA", ("LW",), "LW", injury="DTD")])
    r = c.post("/api/inseason/refresh?league=main").json()
    assert r["alerts"] == {"new": 1, "sent": 1}
    assert sent == [("http://ntfy.local/fa", ["Alex Ovechkin: now DTD"])]
    got = c.get("/api/inseason/alerts?league=main&days=60").json()["alerts"]
    assert [a["text"] for a in got] == ["Alex Ovechkin: now DTD"]


def test_recap_endpoint(world, monkeypatch):  # noqa: F811
    c, data = world
    _fake_yahoo(monkeypatch, {111: {1: ("Main Squeeze", [OVI]), 2: ("Others", [SC])}})
    c.post("/api/inseason/refresh?league=main")
    r = c.get("/api/inseason/recap?league=main")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["my_team"] == "Main Squeeze"
    assert len(d["next_week"]["dates"]) == 7
    assert datetime.date.fromisoformat(d["next_week"]["dates"][0]).weekday() == 0
    for k in ("top_week", "heating_up", "cooling_off", "role_changes", "luck", "bandwagon"):
        assert isinstance(d[k], list)
    json.dumps(d)
