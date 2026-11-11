"""Trade with one team: every 1-for-1, 2-for-1, 1-for-2 and 2-for-2 package,
valued like teams.trade_options (your model vs the market's view, rest of
season through the fantasy playoffs) plus each lineup's fit over a window.

Your side is valued as your best nightly lineup (RosLineup): a player who'd
sit on your bench adds nothing, a below-replacement player never starts.
Simulating ~30k packages that way is too slow, so it's two stages:
  1. screen: each player's lineup value alone -- what he adds to your
     lineup (theirs) or what losing him costs (yours) -- summed per deal;
  2. the best ``screen`` deals by that estimate get the exact simulation,
     which catches the interactions (two D for one D slot, etc.).
"""
from __future__ import annotations

import itertools

from src.valuation.valuation import value_group

from .evaluate import Context, ros_over_replacement
from .roster import dates_from
from .teams import (
    Market,
    RosLineup,
    _Sim,
    close_call,
    league_slots,
    market_per_game,
    team_players,
    team_tags,
)


def partner_trades(ctx: Context, league: dict, my_team_id: int, partner_id: int,
                   start: str, days: int = 21, market: Market | None = None,
                   max_size: int = 2, offer: tuple[list[int], list[int]] | None = None,
                   limit: int = 60,
                   require: tuple[list[int], list[int]] | None = None,
                   screen: int = 300,
                   free_agents: dict[int, set[str]] | None = None) -> dict:
    """Every package deal (up to ``max_size`` players a side) with one team.

    ``my_gain``: change in your rest-of-season lineup (RosLineup, through
    the fantasy playoffs, playoff nights weighted). Taking back more players
    than you send means cutting the ones who add least to your lineup.
    ``their_view``: what the other manager gains as the MARKET sees it --
    per-player value above replacement, floored at 0 (nobody counts dumping
    a bad player as a loss); they cut their lowest market values.
    ``tilt`` = their_view / market value of what they send; deals beyond
    -0.6 are dropped (they'd never accept). ``sweet_spot``: you gain and it
    looks even or better to them. ``my_fit`` / ``their_fit`` = change in
    each lineup over the next ``days``. ``offer``: one specific deal (your
    ids, their ids) scored the same way. ``require``: (your ids, their ids)
    every listed deal must include -- the other spots up to ``max_size`` a
    side are filled from both rosters; losing and lopsided deals are kept
    (you asked about these players); sweet spots first, then by your gain.
    ``free_agents`` (healthy ones, {id: Yahoo positions}): a deal that
    leaves you an open roster spot fills it with the best of them for your
    lineup (``my_adds``) -- a lower bound, since you'd really stream it.
    """
    rosters = {int(t): team_players(ctx, v["players"])[0]
               for t, v in league["teams"].items()}
    names = {int(k): v["name"] for k, v in league["teams"].items()}
    if partner_id not in rosters or partner_id == my_team_id:
        raise ValueError(f"unknown trade partner {partner_id}")
    mine, theirs = rosters[my_team_id], rosters[partner_id]
    need_g, need_t = (set(require[0]), set(require[1])) if require else (set(), set())
    if not need_g <= set(mine) or not need_t <= set(theirs):
        raise ValueError("ticked players must be on the two active rosters")
    if len(need_g) > max_size or len(need_t) > max_size:
        raise ValueError(f"tick at most {max_size} players a side")
    constrained = bool(need_g or need_t)
    mkt_pg = market.per_game if market else (lambda p: market_per_game(ctx, p))
    card = market.card if market else (lambda p: {"market_source": "season-to-date"})
    avg_ros = ctx.weighted_team_games(start, ctx.fantasy_end())
    repl = ctx.scorer.replacement

    def market_value(p: int) -> float:
        r = repl[value_group(ctx.lines[p].position)]
        return max((mkt_pg(p) - r) * avg_ros, 0.0)

    M = {p: market_value(p) for p in (*mine, *theirs)}

    ros = RosLineup(ctx, mine, start)
    keeps = {p: ros.loss_without(p) for p in mine}                # >= 0
    adds = {p: ros.gain_with(p, e) for p, e in theirs.items()}   # >= 0
    # free-agent shortlist for open spots: the best 2 at each position
    fa_pool = {p: e for p, e in (free_agents or {}).items()
               if p in ctx.lines and p not in mine and p not in theirs}
    short: dict[int, set[str]] = {}
    for pos in ("C", "LW", "RW", "D", "G"):
        elig = sorted((p for p, e in fa_pool.items() if pos in e),
                      key=lambda p: -ctx.scorer.per_game[p])[:2]
        short.update({p: fa_pool[p] for p in elig})
    fa_best = max((ros.gain_with(p, e) for p, e in short.items()), default=0.0)

    def cuts(keep: list[int], vals: dict[int, float], n: int) -> list[int]:
        return sorted(keep, key=lambda p: (vals[p], p))[:n] if n > 0 else []

    def deal(give: tuple[int, ...], get: tuple[int, ...]) -> dict:
        m_cut = cuts([p for p in mine if p not in give], keeps, len(get) - len(give))
        t_cut = cuts([p for p in theirs if p not in get], M, len(give) - len(get))
        view = (sum(M[p] for p in give) - sum(M[p] for p in get)
                - sum(M[p] for p in t_cut))
        scale = max(sum(M[p] for p in get), 0.25 * avg_ros, 1e-9)
        tilt = view / scale
        return {"give": list(give), "get": list(get), "my_cut": m_cut,
                "their_cut": t_cut, "their_view": view, "tilt": tilt,
                "lean": ("they win" if tilt > 0.15 else
                         "you win" if tilt < -0.15 else "even"),
                # stage-1 estimate; replaced by the exact simulation below
                "my_gain": (sum(adds[p] for p in get) - sum(keeps[p] for p in give)
                            - sum(keeps[p] for p in m_cut)
                            + fa_best * max(len(give) - len(get), 0))}

    def exact(d: dict) -> dict:
        out = {*d["give"], *d["my_cut"]}
        add = {p: theirs[p] for p in d["get"]}
        gain = ros.gain(out, add)
        d["my_adds"] = []
        # open roster spots: add the free agent who helps the lineup most
        for _ in range(max(len(d["give"]) - len(d["get"]), 0)):
            best = max(((ros.gain(out, {**add, f: e}), f)
                        for f, e in short.items() if f not in add), default=None)
            if best is None or best[0] <= gain + 1e-9:
                break
            gain, f = best
            add[f] = short[f]
            d["my_adds"].append(f)
        d["my_gain"] = gain
        d["close"] = close_call(gain, sum(keeps[p] for p in d["give"])
                                + sum(adds[p] for p in d["get"]))
        d["sweet_spot"] = d["my_gain"] > 0 and d["tilt"] >= -0.15
        return d

    def combos(roster: dict, need: set[int], n: int):
        # the required players plus every way to fill the rest
        if len(need) > n:
            return
        free = [p for p in roster if p not in need]
        for extra in itertools.combinations(free, n - len(need)):
            yield (*sorted(need), *extra)

    cands = []
    sizes = range(1, max_size + 1)
    for ng in sizes:
        for nt in sizes:
            for give in combos(mine, need_g, ng):
                for get in combos(theirs, need_t, nt):
                    d = deal(give, get)
                    if constrained or d["tilt"] >= -0.6:
                        cands.append(d)
    n_considered = len(cands)
    cands.sort(key=lambda d: -d["my_gain"])
    rows = [exact(d) for d in cands[:screen]]
    if not constrained:
        rows = [d for d in rows if d["my_gain"] > 0]
    rows.sort(key=lambda d: (not d["sweet_spot"],
                             -(d["my_gain"] if d["sweet_spot"] or constrained
                               else d["tilt"])))
    n_found = len(rows)
    rows = rows[:limit]

    sim = _Sim(ctx, dates_from(start, days), league_slots(ctx))
    base_m, base_t = sim.total(mine), sim.total(theirs)

    def fit(d: dict) -> dict:
        m1 = {p: e for p, e in mine.items() if p not in d["give"] and p not in d["my_cut"]}
        m1.update({p: theirs[p] for p in d["get"]})
        m1.update({p: short[p] for p in d.get("my_adds", [])})
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
        offer_row = fit(exact(deal(g, t)))

    tags = {**team_tags(ctx, league["teams"][str(my_team_id)]["players"]),
            **team_tags(ctx, league["teams"][str(partner_id)]["players"])}

    def player(p: int, elig: set[str], lineup: float) -> dict:
        ln = ctx.lines[p]
        return {"id": p, "name": ln.name, "team": ln.team, "elig": sorted(elig),
                "per_game": ctx.scorer.per_game[p], "market_pg": mkt_pg(p),
                "ros_value": ros_over_replacement(ctx, p, start),
                "lineup_value": lineup, "market_value": market_value(p),
                "injury": tags.get(p), "playoff_games": ctx.playoff_games(ln.team),
                "avail": ln.start_share if ln.is_goalie else ln.play_share, **card(p)}

    return {
        "partner": {"id": partner_id, "name": names[partner_id]},
        "dates": sim.dates, "ros_games": avg_ros, "ros_end": ctx.fantasy_end(),
        "playoff_game_weight": float(ctx.icfg.get("playoff_game_weight", 1.0)),
        "considered": n_considered, "scored": min(screen, n_considered),
        "found": n_found,
        "mine": sorted((player(p, e, keeps[p]) for p, e in mine.items()),
                       key=lambda x: -x["lineup_value"]),
        "theirs": sorted((player(p, e, adds[p]) for p, e in theirs.items()),
                         key=lambda x: -x["lineup_value"]),
        "free_agents": [player(p, e, ros.gain_with(p, e)) for p, e in short.items()],
        "deals": rows, "offer": offer_row, "constrained": constrained,
    }
