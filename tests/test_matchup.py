"""This week's matchup (src/inseason/matchup.py, /api/inseason/matchup)."""
import datetime
import json
import math

import pytest

from src.api.inseason_matchup import default_start
from src.inseason import matchup as mu
from src.inseason import yahoo_public as yp
from tests.test_inseason import db  # noqa: F401  (fixture)
from tests.test_multi_league import OVI, SC, DEE, YPlayer, _fake_yahoo, world  # noqa: F401

D2 = YPlayer("Dee Two", "CCC", ("D",), "D")

# trimmed from the real league page (two matchups) and a matchup page header
HOME = """<section id="matchupweek"><span class="flyout-title">Week 8</span><ul>
<li class="Linkable Listitem No-p " data-linkable="true" data-target="/hockey/19725/matchup?week=8&amp;mid1=1&amp;mid2=9">
<div class="Fz-sm"><a class="F-link" href="https://hockey.fantasysports.yahoo.com/hockey/19725/1">Jake n Bake</a></div>
<div class="Ta-end Pend-lg Phone-ta-c Phone-no-p">
    <div class="Fz-lg ">21.30</div>  <div class="F-shade Italic F-positive ">242.69</div>
</div>
<span>vs</span>
<div class="Pstart-lg Ta-start Phone-ta-c Phone-no-p">
    <div class="Fz-lg ">1.30</div>  <div class="F-shade Italic F-negative ">205.34</div>
</div></li>
<li class="Linkable Listitem No-p " data-linkable="true" data-target="/hockey/19725/matchup?week=8&amp;mid1=5&amp;mid2=10">
<div class="Fz-lg ">3.75</div>  <div class="F-shade Italic F-positive ">168.87</div>
<div class="Fz-lg ">0.00</div>  <div class="F-shade Italic F-negative ">227.54</div>
</li></ul></section>"""
HEADER = """<li>Live</li><li>Totals</li><li>Mon 11/16</li><li>Tue 11/17</li><li>Wed 11/18</li>
<li>Thu 11/19</li><li>Fri 11/20</li><li>Sat 11/21</li><li>Sun 11/22</li>"""


def test_parse_matchups_and_dates():
    m = yp.parse_matchups(HOME)
    assert m["week"] == 8
    assert m["matchups"] == [
        {"week": 8, "teams": [1, 9], "score": [21.3, 1.3], "yahoo_proj": [242.69, 205.34]},
        {"week": 8, "teams": [5, 10], "score": [3.75, 0.0], "yahoo_proj": [168.87, 227.54]}]
    assert yp.parse_week_dates(HEADER, datetime.date(2026, 11, 17)) == [
        f"2026-11-{d}" for d in range(16, 23)]
    # a late-December week read in January belongs to the earlier year
    assert yp.parse_week_dates("Mon 12/29 Tue 12/30", datetime.date(2027, 1, 2)) == [
        "2026-12-29", "2026-12-30"]
    assert yp.parse_matchups("<html>no module</html>") == {"week": None, "matchups": []}


def test_win_chance_hand_worked():
    """Ahead by 10 with sd 10 on each side: z = 10 / sqrt(200) = 0.7071,
    Phi(0.7071) = 0.7602."""
    assert mu.win_chance(110, 100, 100, 100) == pytest.approx(0.7602, abs=1e-4)
    assert mu.win_chance(100, 0, 90, 0) == 1.0 and mu.win_chance(90, 0, 90, 0) == 0.5
    assert mu.win_chance(100, 50, 100, 50) == pytest.approx(0.5)


def test_default_start_counts_tonight_after_7pm():
    d = datetime.date(2026, 11, 17)
    assert default_start("2026-11-17T09:10", d) == "2026-11-17"
    assert default_start("2026-11-17T21:10", d) == "2026-11-18"
    assert default_start("2026-11-16T21:10", d) == "2026-11-17"   # stale read: today
    assert default_start(None, d) == "2026-11-17"


def test_skater_game_variance_matches_the_formula(world):  # noqa: F811
    """Variance = 1.25 * sum(points per stat ^ 2 * expected count), the
    points per stat being the league's (G 5, A 3, PPP 1, SHP 2, SOG .25,
    BLK .75, HIT .5) for a league-average opponent."""
    from src.inseason.evaluate import expected_line
    import src.api.inseason as api
    ctx = api._ctx("main")
    w = {"goals": 5, "assists": 3, "pp_points": 1, "sh_points": 2, "shots": .25,
         "blocks": .75, "hits": .5}
    line = expected_line(ctx.lines[1], None)
    mean, var = mu.game_variance(ctx, 1)
    assert var == pytest.approx(1.25 * sum(w[k] ** 2 * line[k] for k in w))
    assert mean == pytest.approx(sum(w[k] * line[k] for k in w))


def test_matchup_endpoint(world, monkeypatch):  # noqa: F811
    """Main Squeeze (1) vs Others (2). The projection runs over the days
    still to play; final = Yahoo's score + the projected rest; the two win
    chances add to 1. A free agent who plays this week shows up as a
    swing add, dropping the only non-HOLD player. Games left this week:
    AAA (Ovechkin) 11/3; CCC (Dee Two, Depth Wing) 11/2 and 11/4."""
    c, data = world
    _fake_yahoo(monkeypatch, {111: {1: ("Main Squeeze", [OVI, D2]), 2: ("Others", [SC])}})
    c.post("/api/inseason/refresh?league=main")
    (data / "fa.txt").write_text("Depth Wing | CCC | RW\nDee Man | BBB | D\n")
    assert c.get("/api/inseason/matchup?league=main").status_code == 400   # no matchups yet
    (data / "matchups.json").write_text(json.dumps({
        "week": 5, "dates": [f"2026-11-0{d}" for d in range(1, 8)],
        "fetched": "2026-11-01T08:00",
        "matchups": [{"week": 5, "teams": [1, 2], "score": [10.0, 4.0], "yahoo_proj": [50, 40]}]}))
    r = c.get("/api/inseason/matchup?league=main&start=2026-11-02")
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["remaining"] == [f"2026-11-0{d}" for d in range(2, 8)]
    assert (m["you"]["name"], m["them"]["name"]) == ("Main Squeeze", "Others")
    for side in (m["you"], m["them"]):
        assert side["final"] == pytest.approx(side["score"] + side["rest"])
        assert sum(n["mean"] for n in side["nights"]) == pytest.approx(side["rest"])
    assert m["you"]["win"] + m["them"]["win"] == pytest.approx(1)
    assert m["league"][0]["teams"][0]["final"] == pytest.approx(m["you"]["final"])
    # Dee Man (3.07 a game) is the best free D, so Dee Two (2.58) is no
    # HOLD: he's the drop. Dee Man (BBB: 11/2, 11/3, 11/4) for Dee Two
    # (CCC: 11/2, 11/4) gains a game and a better rate.
    assert m["drop_pool"][0]["name"] == "Dee Two"
    top = m["adds"][0]
    assert (top["add"]["name"], top["drop"]["name"], top["games"]) == ("Dee Man", "Dee Two", 3)
    assert all(a["gain"] > 0 and a["win"] >= m["you"]["win"] for a in m["adds"])
    # someone else's view: no swing adds
    other = c.get("/api/inseason/matchup?league=main&start=2026-11-02&team=2").json()
    assert other["you"]["name"] == "Others" and other["adds"] == []
    assert c.get("/api/inseason/matchup?league=main&team=7").status_code == 404
