"""Trade with one team: every 1-for-1, 2-for-1, 1-for-2 and 2-for-2 package,
valued like teams.trade_options (your model vs the market's view, rest of
season above replacement) plus each lineup's fit over a window.
"""
from __future__ import annotations

import itertools

from src.valuation.valuation import value_group

from .evaluate import Context, player_window
from .roster import dates_from
from .teams import Market, _Sim, league_slots, market_per_game, team_players


def partner_trades(ctx: Context, league: dict, my_team_id: int, partner_id: int,
                   start: str, days: int = 21, market: Market | None = None,
                   max_size: int = 2, offer: tuple[list[int], list[int]] | None = None,
                   limit: int = 60) -> dict:
    """Every package deal (up to ``max_size`` players a side) with one team.

    Same two lenses as trade_options, rest of season above replacement:
    ``my_gain`` by YOUR model on real schedules, ``their_view`` = what the
    other manager gains as the MARKET sees it. Taking back more players than
    you send means cutting your worst ones (their value above replacement is
    lost; the open spot is worth a replacement-level free agent = 0), same
    for them. ``tilt`` = their_view / market value of what they send; deals
    beyond -0.6 are dropped (they'd never accept). ``sweet_spot``: you gain
    and it looks even or better to them. ``my_fit`` / ``their_fit`` = change
    in each lineup over the next ``days``, after the cuts. ``offer``: one
    specific deal (your ids, their ids) to score the same way.
    """
    rosters = {int(t): team_players(ctx, v["players"])[0]
               for t, v in league["teams"].items()}
    names = {int(k): v["name"] for k, v in league["teams"].items()}
    if partner_id not in rosters or partner_id == my_team_id:
        raise ValueError(f"unknown trade partner {partner_id}")
    mine, theirs = rosters[my_team_id], rosters[partner_id]
    mkt_pg = market.per_game if market else (lambda p: market_per_game(ctx, p))
    card = market.card if market else (lambda p: {"market_source": "season-to-date"})
    end = ctx.season_end()
    avg_ros = ctx.avg_team_games(start, end)
    repl = ctx.scorer.replacement
    V: dict[int, float] = {}
    M: dict[int, float] = {}
    for p in (*mine, *theirs):
        r = repl[value_group(ctx.lines[p].position)]
        V[p] = player_window(ctx, p, start, end)["value"] - r * avg_ros
        M[p] = (mkt_pg(p) - r) * avg_ros

    def cuts(keep: list[int], vals: dict[int, float], n: int) -> list[int]:
        return sorted(keep, key=lambda p: vals[p])[:n] if n > 0 else []

    def deal(give: tuple[int, ...], get: tuple[int, ...]) -> dict | None:
        m_keep = [p for p in mine if p not in give]
        t_keep = [p for p in theirs if p not in get]
        m_cut = cuts(m_keep, V, len(get) - len(give))
        t_cut = cuts(t_keep, M, len(give) - len(get))
        my_gain = (sum(V[p] for p in get) - sum(V[p] for p in give)
                   - sum(max(V[p], 0) for p in m_cut))
        view = (sum(M[p] for p in give) - sum(M[p] for p in get)
                - sum(max(M[p], 0) for p in t_cut))
        scale = max(sum(max(M[p], 0) for p in get), 0.25 * avg_ros, 1e-9)
        tilt = view / scale
        return {"give": list(give), "get": list(get), "my_cut": m_cut,
                "their_cut": t_cut, "my_gain": my_gain, "their_view": view,
                "tilt": tilt,
                "lean": ("they win" if tilt > 0.15 else
                         "you win" if tilt < -0.15 else "even"),
                "sweet_spot": my_gain > 0 and tilt >= -0.15}

    rows = []
    sizes = range(1, max_size + 1)
    for ng in sizes:
        for nt in sizes:
            for give in itertools.combinations(mine, ng):
                for get in itertools.combinations(theirs, nt):
                    d = deal(give, get)
                    if d["my_gain"] > 0 and d["tilt"] >= -0.6:
                        rows.append(d)
    n_found = len(rows)
    rows.sort(key=lambda d: (not d["sweet_spot"],
                             -(d["my_gain"] if d["sweet_spot"] else d["tilt"])))
    rows = rows[:limit]
    sim = _Sim(ctx, dates_from(start, days), league_slots(ctx))
    base_m, base_t = sim.total(mine), sim.total(theirs)

    def fit(d: dict) -> dict:
        m1 = {p: e for p, e in mine.items() if p not in d["give"] and p not in d["my_cut"]}
        m1.update({p: theirs[p] for p in d["get"]})
        t1 = {p: e for p, e in theirs.items() if p not in d["get"] and p not in d["their_cut"]}
        t1.update({p: mine[p] for p in d["give"]})
        d["my_fit"] = sim.total(m1) - base_m
        d["their_fit"] = sim.total(t1) - base_t
        return d

    for d in rows:
        fit(d)
    offer_row = None
    if offer and offer[0] and offer[1]:
        g = tuple(p for p in offer[0] if p in mine)
        t = tuple(p for p in offer[1] if p in theirs)
        if len(g) != len(offer[0]) or len(t) != len(offer[1]):
            raise ValueError("offer players must be on the two active rosters")
        offer_row = fit(deal(g, t))

    def player(p: int, elig: set[str]) -> dict:
        ln = ctx.lines[p]
        return {"id": p, "name": ln.name, "team": ln.team, "elig": sorted(elig),
                "per_game": ctx.scorer.per_game[p], "market_pg": mkt_pg(p),
                "ros_value": V[p], "market_value": M[p], **card(p)}

    return {
        "partner": {"id": partner_id, "name": names[partner_id]},
        "dates": sim.dates, "ros_games": avg_ros, "found": n_found,
        "mine": sorted((player(p, e) for p, e in mine.items()), key=lambda x: -x["ros_value"]),
        "theirs": sorted((player(p, e) for p, e in theirs.items()), key=lambda x: -x["ros_value"]),
        "deals": rows, "offer": offer_row,
    }
