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

from .evaluate import Context
from .lineup import best_lineup
from .roster import dates_from, game_values, parse_players

SLOT_ORDER = ("C", "LW", "RW", "D", "Util", "G")


def league_slots(ctx: Context) -> dict[str, int]:
    rs = ctx.league.roster_slots
    slots = {s: rs.get(s, 0) for s in ("C", "LW", "RW", "D", "G")}
    slots["Util"] = rs.get("UTIL", 0)
    return {s: n for s, n in slots.items() if n > 0}


def team_players(ctx: Context, players: list[dict]) -> tuple[dict[int, set[str]], list[str]]:
    """{pid: positions} for a stored roster (list of {name, team, positions})."""
    text = "\n".join(f"{p['name']} | {p['team']} | {','.join(p['positions'])}"
                     for p in players)
    return parse_players(ctx, text)


class _Sim:
    """Nightly lineups with cached per-player game values."""

    def __init__(self, ctx: Context, dates: list[str], slots: dict[str, int]):
        self.ctx, self.dates, self.slots = ctx, dates, slots
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
        return sum(self.night(roster, d)[0] for d in self.dates
                   if only is None or d in only)


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
        players = sorted(({
            "id": p, "name": ctx.lines[p].name, "team": ctx.lines[p].team,
            "elig": sorted(e), "per_game": ctx.scorer.per_game[p],
            "games": len(sim.games(p)), "starts": rep["starts"].get(p, 0)}
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
