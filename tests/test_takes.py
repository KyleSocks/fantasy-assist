"""Buy low / sell high / trade targets / waiver adds / stashes
(src/inseason/takes.py) on a small hand-built league, every number worked
out by hand below."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

import src.inseason.takes as takes

AS_OF = "2026-11-15"


class Ctx:
    """Just what takes.py reads. Replacement bar: F 2.00 (so worth starting
    = 2.20, waiver add = 1.90, stash = 2.20). Every team has played 20
    games and has 10 left."""

    def __init__(self, lines, per_game, roles=None):
        self.lines = lines
        self.scorer = NS(per_game=per_game, replacement={"F": 2.0, "D": 2.0, "G": 2.0}, points=True)
        self.games = [("2026-10-07", "AAA", "BBB")]
        self.as_of = AS_OF
        self.icfg = {}
        self.luck = {}
        self.roles = roles or {}

    def team_games(self, team, a, b):
        return [("d", "x")] * (20 if b == AS_OF else 10)

    def fantasy_end(self):
        return "2027-03-22"


def L(pid, name, gp=20, pos="C", team="AAA"):
    return NS(player_id=pid, name=name, team=team, position=pos, is_goalie=pos == "G",
              gp=gp, extra={})


def league(monkeypatch):
    lines = {1: L(1, "Abe", gp=14), 2: L(2, "Bob"), 3: L(3, "Cal"), 4: L(4, "Dan"),
             5: L(5, "Eli"), 6: L(6, "Fay")}
    per_game = {1: 3.0, 2: 2.5, 3: 2.5, 4: 1.95, 5: 2.3, 6: 2.0}
    roles = {4: {"line": "F1", "pp": "PP1", "usual_line": "F3", "usual_pp": "PP2", "change": "up"}}
    ctx = Ctx(lines, per_game, roles)
    market = NS(per_game=lambda p: {1: 2.4, 2: 3.0, 3: 2.6, 4: 1.5, 5: 1.0, 6: 1.0}[p])
    # Cal: 4.00 per game over 14 days (5 games) vs 2.50 usual -- 60% hot
    monkeypatch.setattr(takes, "recent", lambda c, p, days=14:
                        {"days": 14, "games": 5, "per_game": 4.0, "base": 2.5} if p == 3 else None)
    rostered = {2: {"pct": 60.0, "d7": 5.0}}
    return takes.build(ctx, owners={1: 2, 3: 2, 6: 2, 2: 1}, team_names={1: "Me", 2: "Others"},
                       my_team=1, fas={4}, injured_fas={5}, market=market,
                       rostered=rostered.get, start="2026-11-16")


def test_buy_low(monkeypatch):
    out = league(monkeypatch)
    # Abe: model 3.00 >= 2.20 and 3.00 - 2.40 = 0.60 >= 10% of 3.00;
    # edge 0.60 x 10 games = 6. Missed 20 - 14 = 6 games (>= 3).
    # Fay (2.00) isn't worth starting; Cal's 2.50 vs 2.60 is no gap.
    assert [x["name"] for x in out["buy_low"]] == ["Abe"]
    abe = out["buy_low"][0]
    assert abe["edge"] == pytest.approx(6.0)
    assert abe["take"] == (
        "Buy low from Others. Your model has him at 3.00 per team game, Yahoo managers at 2.40: "
        "about 6 fpts more than his price over 10 games left. Why the price is low: "
        "missed 6 of AAA's 20 games, so his season totals look small.")


def test_sell_high(monkeypatch):
    out = league(monkeypatch)
    # Bob: market 3.00 - model 2.50 = 0.50 >= 10% of 3.00; edge 0.50 x 10 = 5
    assert [x["name"] for x in out["sell_high"]] == ["Bob"]
    assert out["sell_high"][0]["take"] == (
        "Sell high. Yahoo managers value him at 3.00 per team game, your model at 2.50: "
        "a trade at his price gets you about 5 fpts more than he's worth over 10 games left. "
        "Why the price is high: % rostered on Yahoo up 5 this week (now 60%).")


def test_trade_target_waiver_add_stash(monkeypatch):
    out = league(monkeypatch)
    # Cal: hot (4.00 - 2.50 = 1.50 >= 20% of 2.50) and 2.50 >= 2.20; the
    # crowd's 2.60 is already above the model
    assert [x["name"] for x in out["trade_targets"]] == ["Cal"]
    assert out["trade_targets"][0]["take"] == (
        "Trade target on Others: 4.00 per game over the last 14 days (5 games) vs 2.50 usual. "
        "Yahoo managers already rate him at least as high as your model does, so expect to pay full price.")
    # Dan: promoted, 1.95 >= 1.90 but under the 2.00 bar
    assert [x["name"] for x in out["waiver_adds"]] == ["Dan"]
    assert out["waiver_adds"][0]["take"] == (
        "Free agent in your league: promoted to F1, PP1 (was F3, PP2). 1.95 per game is just "
        "under the free-agent bar (2.00), so it's a bet on the new role or hot streak.")
    # Eli: injured free agent, 2.30 >= 2.20
    assert [x["name"] for x in out["stashes"]] == ["Eli"]
    assert out["stashes"][0]["take"] == (
        "Injured free agent worth an IR spot: 2.30 per game when he plays vs 2.00 for the "
        "best healthy free agents.")


def test_buy_low_is_not_repeated_as_trade_target(monkeypatch):
    lines = {1: L(1, "Abe")}
    ctx = Ctx(lines, {1: 3.0})
    monkeypatch.setattr(takes, "recent", lambda c, p, days=14:
                        {"days": 14, "games": 5, "per_game": 5.0, "base": 3.0})
    out = takes.build(ctx, owners={1: 2}, team_names={2: "Others"}, my_team=1, fas=set(),
                      injured_fas=set(), market=NS(per_game=lambda p: 2.0),
                      rostered=lambda p: None, start="2026-11-16")
    assert [x["name"] for x in out["buy_low"]] == ["Abe"]
    assert out["trade_targets"] == []
