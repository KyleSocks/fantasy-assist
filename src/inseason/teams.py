"""League-wide team analysis: lineup strength by position and 1-for-1 trades.

Every team's strength is its best nightly lineup (skaters + goalies, the
league's active slots) summed over a window, from the same blended,
opponent-adjusted game values the rest of src.inseason uses. Breaking the
starters down by the slot they fill gives strengths/weaknesses vs the league
average; points scored by players who had to sit are "bench overflow" --
surplus a team can trade from.

Trades: every 1-for-1 swap between your team and another, re-simulating both
lineups over the window. A trade is listed only if it helps you; "mutual"
trades help both sides (the ones worth proposing). ``pg_delta`` is the
blended per-game value you gain or give up -- a window gain with a negative
pg_delta is a schedule play that costs you later in the season.
"""
from __future__ import annotations

import datetime

from src.valuation.valuation import value_group

from .evaluate import Context, form, ros_over_replacement
from .lineup import best_lineup
from .roster import _match, dates_from, game_values, parse_list, parse_players

SLOT_ORDER = ("C", "LW", "RW", "D", "Util", "G")


def league_slots(ctx: Context) -> dict[str, int]:
    rs = ctx.league.roster_slots
    slots = {s: rs.get(s, 0) for s in ("C", "LW", "RW", "D", "G")}
    slots["Util"] = rs.get("UTIL", 0)
    return {s: n for s, n in slots.items() if n > 0}


def _roster_text(players: list[dict]) -> str:
    return "\n".join(f"{p['name']} | {p['team']} | {','.join(p['positions'])}"
                     + (" | IR" if p.get("inactive") else "")
                     + (f"  # {p['injury']}" if p.get("injury") else "")
                     for p in players)


def team_players(ctx: Context, players: list[dict]) -> tuple[dict[int, set[str]], list[str]]:
    """{pid: positions} for a stored roster (list of {name, team, positions})."""
    return parse_players(ctx, _roster_text(players))


def team_tags(ctx: Context, players: list[dict]) -> dict[int, str]:
    """{pid: Yahoo injury tag (DTD, O, IR-LT ...)} for a stored roster."""
    return parse_list(ctx, _roster_text(players)).tags


def close_call(gain: float, moved: float) -> bool:
    """A margin under 10% of the lineup value changing hands (or 5 pts) is
    inside rest-of-season projection error: too close to call."""
    return abs(gain) < max(5.0, 0.1 * moved)


class _Sim:
    """Nightly lineups with cached per-player game values. ``weighted``:
    each night counts ctx.game_weight(date) (fantasy-playoff nights extra)."""

    def __init__(self, ctx: Context, dates: list[str], slots: dict[str, int],
                 weighted: bool = False):
        self.ctx, self.dates, self.slots = ctx, dates, slots
        self.w = ctx.game_weight if weighted else (lambda d: 1.0)
        self.gv: dict[int, dict[str, float]] = {}

    def games(self, pid: int) -> dict[str, float]:
        if pid not in self.gv:
            self.gv[pid] = game_values(self.ctx, pid, self.dates)
        return self.gv[pid]

    def night(self, roster: dict[int, set[str]], d: str):
        return best_lineup([(p, e, self.games(p).get(d, 0.0))
                            for p, e in roster.items()
                            if self.games(p).get(d, 0.0) > 0], self.slots)

    def total(self, roster: dict[int, set[str]], only: set[str] | None = None) -> float:
        return sum(self.w(d) * self.night(roster, d)[0] for d in self.dates
                   if only is None or d in only)


def ros_dates(ctx: Context, start: str) -> list[str]:
    """Every date from ``start`` to the end of the fantasy playoffs."""
    end = ctx.fantasy_end()
    if start > end:
        return []
    n = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days
    return dates_from(start, n + 1)


class RosLineup:
    """Rest-of-season value of a roster as your best nightly lineup, through
    the fantasy playoffs (weighted): what a trade is really worth. A player
    who'd sit on your bench adds nothing; one below replacement never starts,
    so trading him away costs nothing (and dumping one on you gains them
    nothing). Each change re-simulates only the nights a moved player plays.
    """

    def __init__(self, ctx: Context, roster: dict[int, set[str]], start: str):
        self.sim = _Sim(ctx, ros_dates(ctx, start), league_slots(ctx), weighted=True)
        self.roster = roster
        self.base = {d: self.sim.night(roster, d)[0] for d in self.sim.dates}

    def gain(self, out: set[int] | list[int], add: dict[int, set[str]]) -> float:
        """Lineup value after removing ``out`` and adding ``add``, minus now."""
        nights: set[str] = set()
        for p in (*out, *add):
            nights |= set(self.sim.games(p))
        after = {p: e for p, e in self.roster.items() if p not in out}
        after.update(add)
        return sum(self.sim.w(d) * (self.sim.night(after, d)[0] - self.base[d])
                   for d in nights)

    def loss_without(self, p: int) -> float:
        """What ``p`` adds to the lineup (>= 0): the cost of losing him."""
        return -self.gain({p}, {})

    def gain_with(self, p: int, elig: set[str]) -> float:
        """What adding ``p`` (with no one leaving) adds to the lineup."""
        return self.gain(set(), {p: elig})


def team_report(sim: _Sim, roster: dict[int, set[str]]) -> dict:
    by_slot = {s: 0.0 for s in SLOT_ORDER}
    bench = 0.0
    starts: dict[int, int] = {}
    total = 0.0
    for d in sim.dates:
        t, assign = sim.night(roster, d)
        total += t
        for p, slot in assign.items():
            by_slot[slot] += sim.games(p)[d]
            starts[p] = starts.get(p, 0) + 1
        bench += sum(sim.games(p).get(d, 0.0) for p in roster
                     if p not in assign and sim.games(p).get(d, 0.0) > 0)
    return {"total": total, "by_slot": by_slot, "bench": bench, "starts": starts}


def analyze(ctx: Context, league: dict, my_team_id: int, start: str,
            days: int = 21, max_trades: int = 150) -> dict:
    """``league``: {"teams": {id: {"name", "players": [...]}}} as stored by
    the refresh. Returns team reports (with position strength vs league
    average) and 1-for-1 trades for ``my_team_id``."""
    dates = dates_from(start, days)
    slots = league_slots(ctx)
    sim = _Sim(ctx, dates, slots)
    rosters, unmatched = {}, []
    names = {int(k): v["name"] for k, v in league["teams"].items()}
    for tid, t in league["teams"].items():
        rosters[int(tid)], miss = team_players(ctx, t["players"])
        unmatched += miss
    reports = {tid: team_report(sim, r) for tid, r in rosters.items()}
    avg = {s: sum(r["by_slot"][s] for r in reports.values()) / len(reports)
           for s in SLOT_ORDER}
    teams = []
    for tid, rep in sorted(reports.items(), key=lambda kv: -kv[1]["total"]):
        roster = rosters[tid]
        tags = team_tags(ctx, league["teams"][str(tid)]["players"])
        players = sorted(({
            "id": p, "name": ctx.lines[p].name, "team": ctx.lines[p].team,
            "elig": sorted(e), "per_game": ctx.scorer.per_game[p],
            "games": len(sim.games(p)), "starts": rep["starts"].get(p, 0),
            "injury": tags.get(p), "playoff_games": ctx.playoff_games(ctx.lines[p].team),
            "avail": (ctx.lines[p].start_share if ctx.lines[p].is_goalie
                      else ctx.lines[p].play_share)}
            for p, e in roster.items()), key=lambda x: -x["per_game"])
        teams.append({
            "id": tid, "name": names[tid],
            "mine": tid == my_team_id, "total": rep["total"], "bench": rep["bench"],
            "by_slot": rep["by_slot"],
            "vs_avg": {s: rep["by_slot"][s] - avg[s] for s in SLOT_ORDER},
            "players": players})
    return {"dates": dates, "slots": slots, "league_avg": avg, "teams": teams,
            "trades": find_trades(sim, rosters, reports, my_team_id, max_trades),
            "unmatched": unmatched}


def find_trades(sim: _Sim, rosters: dict[int, dict[int, set[str]]],
                reports: dict[int, dict], me: int, limit: int = 40) -> list[dict]:
    """1-for-1 swaps that raise your window lineup total, best first:
    mutual (both gain) ranked by the smaller gain, then the rest."""
    ctx = sim.ctx
    mine = rosters[me]
    out = []
    for tid, theirs in rosters.items():
        if tid == me:
            continue
        for give, ge in mine.items():
            for get, te in theirs.items():
                nights = set(sim.games(give)) | set(sim.games(get))
                if not nights:
                    continue
                m_after = {**{p: e for p, e in mine.items() if p != give}, get: te}
                t_after = {**{p: e for p, e in theirs.items() if p != get}, give: ge}
                d_me = sim.total(m_after, nights) - sim.total(mine, nights)
                if d_me <= 0.05:
                    continue
                d_them = sim.total(t_after, nights) - sim.total(theirs, nights)
                out.append({
                    "partner_id": tid,
                    "give": {"id": give, "name": ctx.lines[give].name,
                             "team": ctx.lines[give].team, "elig": sorted(ge),
                             "per_game": ctx.scorer.per_game[give]},
                    "get": {"id": get, "name": ctx.lines[get].name,
                            "team": ctx.lines[get].team, "elig": sorted(te),
                            "per_game": ctx.scorer.per_game[get]},
                    "my_gain": d_me, "their_gain": d_them,
                    # long-term view: blended per-game value you gain (+) or
                    # give up (-); window gains can come from schedule alone
                    "pg_delta": (ctx.scorer.per_game[get]
                                 - ctx.scorer.per_game[give]),
                    "mutual": d_them > 0.05})
    out.sort(key=lambda t: (not t["mutual"],
                            -(min(t["my_gain"], t["their_gain"]) if t["mutual"]
                              else t["my_gain"])))
    return out[:limit]


# ── trade explorer: shop one of your players ─────────────────────────────

DEFAULT_MARKET_WEIGHTS = {"rostered": 0.5, "points": 0.3, "name": 0.2}


class Market:
    """How Yahoo managers value players, from data/market.json (Refresh).

    Three signals, each turned into a percentile within F / D / G:
      rostered -- % of all Yahoo leagues rostering him (the crowd's verdict)
      points   -- Yahoo Fan Pts this season: the TOTAL by default, as the
                  trade screen shows it (so a player who missed games looks
                  worse -- how the other manager sees him); per game with
                  ``market_points: per_game`` in inseason.yaml
      name     -- Yahoo pre-season rank (reputation / draft position)
    The weighted mean (``market_weights`` in inseason.yaml; missing signals
    are skipped) ranks players; a player's market value per game is the
    MODEL's value of the player at that rank in his group -- so it stays in
    fantasy points and differences mean "how far apart we and the crowd
    rank him". Players without Yahoo data fall back to market_per_game's
    season-to-date formula.
    """

    def __init__(self, ctx: Context, rows: list[dict],
                 weights: dict[str, float] | None = None):
        self.ctx = ctx
        w = weights or ctx.icfg.get("market_weights") or DEFAULT_MARKET_WEIGHTS
        self.weights = {k: float(w.get(k, 0)) for k in DEFAULT_MARKET_WEIGHTS}
        self.per_game_points = ctx.icfg.get("market_points", "total") == "per_game"
        self.info: dict[int, dict] = {}
        for r in rows:
            ln = _match(ctx, [r["name"], r.get("team") or "",
                              ",".join(r.get("positions") or [])])
            if ln is not None and ln.player_id not in self.info:
                self.info[ln.player_id] = r
        self.value: dict[int, float] = {}
        self.score: dict[int, float] = {}
        groups: dict[str, list[int]] = {}
        for pid in self.info:
            groups.setdefault(value_group(ctx.lines[pid].position), []).append(pid)
        for pids in groups.values():
            self._rank_group(pids)

    def _signals(self, pid: int) -> dict[str, float | None]:
        r = self.info[pid]
        gp, fpts = r.get("gp"), r.get("fpts")
        pre = r.get("pre_rank")
        if fpts is None:
            pts = None
        elif self.per_game_points:
            pts = fpts / gp if gp else None
        else:
            pts = fpts
        return {"rostered": r.get("pct_ros"),
                "points": pts,
                # lower rank = better: negate so higher is better everywhere
                "name": -pre if pre else None}

    def _rank_group(self, pids: list[int]) -> None:
        sig = {p: self._signals(p) for p in pids}
        pct: dict[str, dict[int, float]] = {}
        for k in DEFAULT_MARKET_WEIGHTS:
            have = sorted((p for p in pids if sig[p][k] is not None),
                          key=lambda p: sig[p][k])
            n = len(have)
            pct[k] = {p: (i / (n - 1) if n > 1 else 0.5) for i, p in enumerate(have)}
        for p in pids:
            parts = [(self.weights[k], pct[k][p]) for k in pct
                     if p in pct[k] and self.weights[k] > 0]
            tot = sum(w for w, _ in parts)
            self.score[p] = sum(w * v for w, v in parts) / tot if tot else 0.0
        by_score = sorted(pids, key=lambda p: -self.score[p])
        model = sorted((self.ctx.scorer.per_game[p] for p in pids), reverse=True)
        for p, v in zip(by_score, model):
            self.value[p] = v

    def per_game(self, pid: int) -> float:
        return self.value[pid] if pid in self.value else market_per_game(self.ctx, pid)

    def card(self, pid: int) -> dict:
        """Yahoo numbers to show next to a player."""
        r = self.info.get(pid) or {}
        return {"pct_ros": r.get("pct_ros"), "pre_rank": r.get("pre_rank"),
                "cur_rank": r.get("cur_rank"), "fpts": r.get("fpts"),
                "yahoo_gp": r.get("gp"), "market_source":
                    "yahoo" if pid in self.value else "season-to-date"}


def market_per_game(ctx: Context, pid: int) -> float:
    """Fallback market view when there's no Yahoo data: mostly what he's
    done THIS season, with only ``market_k_games`` games of preseason prior
    -- versus the model's 10-60 games per stat."""
    f = form(ctx, ctx.lines[pid])
    obs, prior, gp = f["observed"], f["prior"], ctx.lines[pid].gp
    if obs is None or not gp:
        return prior
    k = float(ctx.icfg.get("market_k_games", 5))
    return (obs * gp + prior * k) / (gp + k)


def trade_options(ctx: Context, league: dict, my_team_id: int, give: int,
                  start: str, days: int = 21, max_tilt: float = 0.6,
                  market: Market | None = None) -> dict:
    """Every 1-for-1 deal for ``give`` (one of your players), from deals the
    market would see as favouring you to ones that favour the other side.

    Two lenses, rest of season through the fantasy playoffs (weighted):
    - ``my_gain`` -- YOUR model: change in your best nightly lineup with
      the swap (RosLineup), each player's real schedule -- a player who'd
      sit on your bench adds nothing;
    - ``market_edge`` -- the MARKET view (``market``: Yahoo % rostered,
      Fan Pts per game and pre-season rank -- see Market; without it the
      season-to-date fallback), league-average schedule, from your side:
      > 0 = the other manager sees you winning. A player below replacement
      counts 0 (he'd just be dropped), not negative.
    ``tilt`` = market_edge / ``give``'s whole rest-of-season output as the
    market sees it (market per game x games) -- the gap as a share of what
    he's worth; deals with |tilt| > ``max_tilt`` are dropped (no one accepts
    those, or it's a gift).
    ``sweet_spot``: you gain by your model while the market sees it as even
    or in their favour -- the deals worth proposing. ``my_fit`` /
    ``their_fit``: change in each lineup's total over the next ``days`` --
    positional need, which also makes a deal easier to sell.
    Ordered by market_edge, favours-you first.
    """
    dates = dates_from(start, days)
    sim = _Sim(ctx, dates, league_slots(ctx))
    rosters = {int(t): team_players(ctx, v["players"])[0]
               for t, v in league["teams"].items()}
    names = {int(k): v["name"] for k, v in league["teams"].items()}
    mine = rosters[my_team_id]
    if give not in mine:
        raise ValueError(f"{ctx.lines[give].name} isn't on your active roster")
    mkt_pg = market.per_game if market else (lambda p: market_per_game(ctx, p))
    card = market.card if market else (lambda p: {"market_source": "season-to-date"})
    avg_ros = ctx.weighted_team_games(start, ctx.fantasy_end())
    repl = ctx.scorer.replacement
    cache: dict[int, tuple[float, float]] = {}

    def values(pid: int) -> tuple[float, float]:
        """(model ROS value over replacement, market ROS value over repl,
        floored at 0: a below-replacement player is just dropped)."""
        if pid not in cache:
            r = repl[value_group(ctx.lines[pid].position)]
            cache[pid] = (ros_over_replacement(ctx, pid, start),
                          max((mkt_pg(pid) - r) * avg_ros, 0.0))
        return cache[pid]

    g_ours, g_mkt = values(give)
    scale = max(abs(mkt_pg(give)) * avg_ros, 1e-9)
    ge = mine[give]
    ros = RosLineup(ctx, mine, start)
    give_loss = ros.loss_without(give)
    rows = []
    for tid, theirs in rosters.items():
        if tid == my_team_id:
            continue
        for get, te in theirs.items():
            _, mkt = values(get)
            edge = mkt - g_mkt
            tilt = edge / scale
            if abs(tilt) > max_tilt:
                continue
            nights = set(sim.games(give)) | set(sim.games(get))
            m_after = {**{p: e for p, e in mine.items() if p != give}, get: te}
            t_after = {**{p: e for p, e in theirs.items() if p != get}, give: ge}
            my_gain = ros.gain({give}, {get: te})
            close = close_call(my_gain, give_loss + ros.gain_with(get, te))
            rows.append({
                "partner_id": tid, "partner": names[tid],
                "get": {"id": get, "name": ctx.lines[get].name,
                        "team": ctx.lines[get].team, "elig": sorted(te),
                        "per_game": ctx.scorer.per_game[get],
                        "market_pg": mkt_pg(get), **card(get)},
                "my_gain": my_gain, "market_edge": edge, "tilt": tilt,
                "lean": ("favors you" if tilt > 0.15 else
                         "favors them" if tilt < -0.15 else "even"),
                "sweet_spot": my_gain > 0 and tilt <= 0.15, "close": close,
                "my_fit": sim.total(m_after, nights) - sim.total(mine, nights),
                "their_fit": sim.total(t_after, nights) - sim.total(theirs, nights),
            })
    rows.sort(key=lambda r: -r["market_edge"])
    gl = ctx.lines[give]
    return {
        "give": {"id": give, "name": gl.name, "team": gl.team,
                 "elig": sorted(ge), "per_game": ctx.scorer.per_game[give],
                 "market_pg": mkt_pg(give), **card(give),
                 "ros_value": g_ours, "market_value": g_mkt},
        "dates": dates, "ros_games": avg_ros, "options": rows,
        "ros_end": ctx.fantasy_end(),
        "playoff_game_weight": float(ctx.icfg.get("playoff_game_weight", 1.0)),
        "market_k_games": float(ctx.icfg.get("market_k_games", 5)),
        "market_weights": market.weights if market else None,
    }

