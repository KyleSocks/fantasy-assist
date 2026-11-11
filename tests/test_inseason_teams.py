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


# ── trade explorer ─────────────────────────────────────────────────────

def _trade_ctx(db):  # noqa: F811
    return build_context(db, POINTS, priors=PRIORS, icfg=dict(ICFG))


def test_market_per_game_leans_on_this_season(db):  # noqa: F811
    from src.inseason.teams import market_per_game
    ctx = _trade_ctx(db)
    # Ovi: prior 3.75/gm, 1.9/gm over 10 GP, market k = 5 (default)
    assert market_per_game(ctx, 1) == pytest.approx((1.9 * 10 + 3.75 * 5) / 15, abs=1e-6)
    # the market reacts to a cold start much more than the model does
    assert market_per_game(ctx, 1) < ctx.scorer.per_game[1]
    ctx.icfg["market_k_games"] = 1e9            # all prior
    assert market_per_game(ctx, 1) == pytest.approx(3.75, abs=1e-6)


def test_trade_options_range_and_labels(db):  # noqa: F811
    """Ordered from 'looks like you win' to 'looks like they win', with
    your model's gain, labels and the sweet-spot flag consistent."""
    from src.inseason.evaluate import player_window
    from src.inseason.teams import trade_options
    from src.valuation.valuation import value_group
    ctx = _trade_ctx(db)
    r = trade_options(ctx, LEAGUE, 1, 1, "2026-11-01", 4, max_tilt=99)
    opts = r["options"]
    assert {o["get"]["name"] for o in opts} == {"Strong Center", "Goalie B"}
    assert [o["market_edge"] for o in opts] == sorted(
        (o["market_edge"] for o in opts), reverse=True)
    avg = ctx.avg_team_games("2026-11-01", ctx.season_end())
    for o in opts:
        pid = o["get"]["id"]
        repl = ctx.scorer.replacement[value_group(ctx.lines[pid].position)]
        ros = player_window(ctx, pid, "2026-11-01", ctx.season_end())["value"] - repl * avg
        assert o["my_gain"] == pytest.approx(ros - r["give"]["ros_value"])
        assert o["lean"] == ("favors you" if o["tilt"] > .15 else
                             "favors them" if o["tilt"] < -.15 else "even")
        assert o["sweet_spot"] == (o["my_gain"] > 0 and o["tilt"] <= .15)
    # Ovi is cold (market below model), Strong Center hot (market above
    # model): the market thinks I'm winning that swap
    sc = next(o for o in opts if o["get"]["name"] == "Strong Center")
    assert sc["market_edge"] > 0
    # a tight tilt cap drops lopsided deals
    tight = trade_options(ctx, LEAGUE, 1, 1, "2026-11-01", 4, max_tilt=1e-6)
    assert all(abs(o["tilt"]) <= 1e-6 for o in tight["options"])


def test_trade_options_requires_my_player(db):  # noqa: F811
    from src.inseason.teams import trade_options
    with pytest.raises(ValueError):
        trade_options(_trade_ctx(db), LEAGUE, 1, 2, "2026-11-01", 4)   # theirs


def test_market_ranks_by_yahoo_signals_and_maps_to_model_values(db):  # noqa: F811
    """The crowd loves Strong Center (100% rostered, hot Fan Pts) and has
    soured on Ovi (20%): Strong Center gets the best forward model value,
    Ovi the next -- the market's rank, priced in model points."""
    from src.inseason.teams import Market
    ctx = _trade_ctx(db)
    rows = [
        {"name": "Strong Center", "team": "BBB", "positions": ["C"], "gp": 10,
         "fpts": 40.0, "pre_rank": 150, "pct_ros": 100.0},
        {"name": "Alex Ovechkin", "team": "AAA", "positions": ["LW"], "gp": 10,
         "fpts": 19.0, "pre_rank": 20, "pct_ros": 20.0},
        {"name": "Depth Wing", "team": "CCC", "positions": ["RW"], "gp": 10,
         "fpts": 8.0, "pre_rank": 400, "pct_ros": 5.0},
    ]
    m = Market(ctx, rows)
    model_f = sorted((ctx.scorer.per_game[p] for p in (1, 2, 3)), reverse=True)
    assert m.per_game(2) == pytest.approx(model_f[0])     # Strong Center
    assert m.per_game(1) == pytest.approx(model_f[1])     # Ovi
    assert m.per_game(3) == pytest.approx(model_f[2])
    assert m.card(2)["pct_ros"] == 100.0 and m.card(2)["market_source"] == "yahoo"
    # name recognition only: Ovi's pre-season rank wins
    m2 = Market(ctx, rows, {"rostered": 0, "points": 0, "name": 1})
    assert m2.per_game(1) == pytest.approx(model_f[0])
    # no Yahoo row -> season-to-date fallback
    from src.inseason.teams import market_per_game
    assert m.per_game(4) == pytest.approx(market_per_game(ctx, 4))
    assert m.card(4)["market_source"] == "season-to-date"
    # in trade_options: Ovi for the crowd's favourite looks like I'm winning
    r = trade_options_with(ctx, m)
    sc = next(o for o in r["options"] if o["get"]["name"] == "Strong Center")
    assert sc["market_edge"] > 0 and sc["get"]["pct_ros"] == 100.0


def trade_options_with(ctx, market):
    from src.inseason.teams import trade_options
    return trade_options(ctx, LEAGUE, 1, 1, "2026-11-01", 4, max_tilt=99, market=market)


# ── package deals with one team ────────────────────────────────────────

def test_partner_trades_packages_cuts_and_offer(db):  # noqa: F811
    from src.inseason.partner import partner_trades
    ctx = _trade_ctx(db)
    r = partner_trades(ctx, LEAGUE, 1, 2, "2026-11-01", 4, offer=([3, 4], [7]))
    assert r["partner"]["name"] == "Theirs"
    V = {p["id"]: p["ros_value"] for p in r["mine"] + r["theirs"]}
    M = {p["id"]: p["market_value"] for p in r["mine"] + r["theirs"]}
    assert r["deals"] and r["found"] >= len(r["deals"])
    for d in r["deals"]:
        assert 1 <= len(d["give"]) <= 2 and 1 <= len(d["get"]) <= 2
        assert d["my_gain"] > 0 and d["tilt"] >= -0.6
        # taking back more players than you send costs you your worst
        assert len(d["my_cut"]) == max(len(d["get"]) - len(d["give"]), 0)
        assert len(d["their_cut"]) == max(len(d["give"]) - len(d["get"]), 0)
        assert d["my_gain"] == pytest.approx(
            sum(V[p] for p in d["get"]) - sum(V[p] for p in d["give"])
            - sum(max(V[p], 0) for p in d["my_cut"]))
        assert d["their_view"] == pytest.approx(
            sum(M[p] for p in d["give"]) - sum(M[p] for p in d["get"])
            - sum(max(M[p], 0) for p in d["their_cut"]))
        assert d["sweet_spot"] == (d["tilt"] >= -0.15)
        assert "my_fit" in d and "their_fit" in d
    # sweet spots first
    flags = [d["sweet_spot"] for d in r["deals"]]
    assert flags == sorted(flags, reverse=True)
    # 2-for-1 offer: they must cut one of their own, I lose nothing extra
    o = r["offer"]
    assert o["give"] == [3, 4] and o["get"] == [7]
    assert len(o["their_cut"]) == 1 and o["my_cut"] == []
    # 1-for-1 only
    one = partner_trades(ctx, LEAGUE, 1, 2, "2026-11-01", 4, max_size=1)
    assert all(len(d["give"]) == len(d["get"]) == 1 for d in one["deals"])


def test_partner_trades_rejects_bad_input(db):  # noqa: F811
    from src.inseason.partner import partner_trades
    ctx = _trade_ctx(db)
    with pytest.raises(ValueError):
        partner_trades(ctx, LEAGUE, 1, 1, "2026-11-01", 4)      # myself
    with pytest.raises(ValueError):
        partner_trades(ctx, LEAGUE, 1, 2, "2026-11-01", 4, offer=([7], [3]))  # swapped


def test_partner_trades_ticked_players_filter_deals(db):  # noqa: F811
    """Ticking 2 of mine + 1 of theirs lists only deals with all three --
    2-for-1 and 2-for-2 with one more of theirs -- losses included."""
    from src.inseason.partner import partner_trades
    ctx = _trade_ctx(db)
    r = partner_trades(ctx, LEAGUE, 1, 2, "2026-11-01", 4, require=([3, 4], [7]))
    assert r["constrained"]
    combos = {(tuple(sorted(d["give"])), tuple(sorted(d["get"]))) for d in r["deals"]}
    # their roster is Strong Center (2) + Goalie B (7): exactly two packages
    assert combos == {((3, 4), (7,)), ((3, 4), (2, 7))}
    keys = [(not d["sweet_spot"], -d["my_gain"]) for d in r["deals"]]
    assert keys == sorted(keys)        # likely-accept first, then my gain
    # one side ticked: every deal has him, sizes 1-2 on both sides
    one = partner_trades(ctx, LEAGUE, 1, 2, "2026-11-01", 4, require=([1], []))
    assert one["deals"] and all(1 in d["give"] for d in one["deals"])
    assert {len(d["give"]) for d in one["deals"]} == {1, 2}
    with pytest.raises(ValueError):    # 3 ticks on one side
        partner_trades(ctx, LEAGUE, 1, 2, "2026-11-01", 4, require=([1, 3, 4], []))
    with pytest.raises(ValueError):    # 2 ticks in 1-for-1 mode
        partner_trades(ctx, LEAGUE, 1, 2, "2026-11-01", 4, max_size=1, require=([1, 3], []))
