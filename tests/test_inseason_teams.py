"""League analysis on the hand-computed world from tests/test_inseason.py
(points league, one team's worth of slots: C1 D1 G1, no LW/RW/Util)."""
import pytest

from src.inseason.evaluate import build_context
from src.inseason.teams import analyze
from src.inseason.yahoo_public import parse_roster, parse_team_links
from tests.test_inseason import ICFG, POINTS, PRIORS, db  # noqa: F401
from tests.test_yahoo_public import TEAM_PAGE

LEAGUE = {"teams": {
    "1": {"name": "Mine", "players": [
        {"name": "Alex Ovechkin", "team": "AAA", "positions": ["C"]},
        {"name": "Depth Wing", "team": "CCC", "positions": ["C"]},
        {"name": "Dee Man", "team": "BBB", "positions": ["D"]},
        {"name": "Dee Two", "team": "CCC", "positions": ["D"]}]},
    "2": {"name": "Theirs", "players": [
        {"name": "Strong Center", "team": "BBB", "positions": ["C"]},
        {"name": "Goalie B", "team": "BBB", "positions": ["G"]}]}}}


def test_team_totals_and_trades(db):  # noqa: F811
    ctx = build_context(db, POINTS, priors=PRIORS, icfg=ICFG)
    r = analyze(ctx, LEAGUE, 1, "2026-11-01", 4)
    mine = next(t for t in r["teams"] if t["mine"])
    assert mine["name"] == "Mine" and not r["unmatched"]
    # lineup total == what the starters scored, split by slot
    assert mine["total"] == pytest.approx(sum(mine["by_slot"].values()))
    # league average per slot is the mean of the two teams
    for s in ("C", "D", "G"):
        assert r["league_avg"][s] == pytest.approx(
            sum(t["by_slot"][s] for t in r["teams"]) / 2)
    # Both of my D play CCC/BBB on 11-02 and 11-04 with one D slot: one sits.
    assert mine["bench"] > 0
    for x in r["trades"]:
        assert x["my_gain"] > 0.05
        assert x["pg_delta"] == pytest.approx(x["get"]["per_game"] - x["give"]["per_game"])
        assert x["mutual"] == (x["their_gain"] > 0.05)
    # I have no goalie (empty G slot) and a D who sits; they have no D
    # (empty D slot). Dee Man for Goalie B fills both holes -> mutual.
    t = next(x for x in r["trades"]
             if x["give"]["name"] == "Dee Man" and x["get"]["name"] == "Goalie B")
    assert t["mutual"] and t["my_gain"] > 1 and t["their_gain"] > 1
    # Strong Center's games are vs CCC (stingy, GA factor 0.5): no gain for
    # Depth Wing, whose games are vs BBB -- schedule/opponent aware.
    assert not any(x["get"]["name"] == "Strong Center" for x in r["trades"])


def test_team_links_and_goalies():
    home = ('<a href="https://hockey.fantasysports.yahoo.com/hockey/19725/5">Robertson&#8217;s '
            'Rules of Hockey</a><a href="/hockey/19725/12">Staalin&#39; for Aho</a>'
            '<a href="/hockey/19725/5">Robertson’s Rules of Hockey</a><a href="/hockey/999/3">X</a>')
    assert parse_team_links(home, 19725) == {5: "Robertson’s Rules of Hockey",
                                             12: "Staalin' for Aho"}
    with_g = parse_roster(TEAM_PAGE, include_goalies=True)
    assert with_g[-1].name == "Jake Oettinger" and with_g[-1].positions == ("G",)
    assert "Jake Oettinger" not in [p.name for p in parse_roster(TEAM_PAGE)]
