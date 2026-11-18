"""This week's head-to-head matchups: projected finals and win chances.

Points leagues only (category leagues are won stat by stat; not modelled).

For each team: the rest of the week as its best nightly lineup (the same
blended, opponent-adjusted game values and lineup rules as everywhere else
in src.inseason), added to the score Yahoo showed at the last refresh.

Spread: each starter's game is treated as independent, with variance

  skaters   sum over stats of (points per stat)^2 * expected count, times
            OVERDISPERSION (a goal also scores PPP, hot nights cluster)
  goalies   if he starts: wins and shutouts as yes/no, saves and goals
            against as counts; then mixed with the chance he starts at all
            (start share s): s * var + s * (1 - s) * mean^2

Win chance = P(my final > their final) with both finals normal:
Phi((my mean - their mean) / sqrt(my var + their var)).

Swing adds: free agents who'd raise your projected final most if added in
place of your weakest non-HOLD player for the rest of the week. HOLD
players are never dropped for a single week (they're worth more later).
"""
from __future__ import annotations

import datetime
import math

from .evaluate import Context, expected_line, hold_check
from .roster import dates_from
from .teams import _Sim, league_slots, team_players

OVERDISPERSION = 1.25
SKATER_STATS = ("goals", "assists", "pp_points", "sh_points", "shots", "hits", "blocks")


def phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def win_chance(mean_a: float, var_a: float, mean_b: float, var_b: float) -> float:
    sd = math.sqrt(max(var_a + var_b, 0.0))
    if sd == 0:
        return 1.0 if mean_a > mean_b else 0.0 if mean_a < mean_b else 0.5
    return phi((mean_a - mean_b) / sd)


def _weights(ctx: Context, pid: int, keys) -> dict[str, float]:
    ln = ctx.lines[pid]
    v = lambda st: ctx.scorer.value_stats(pid, ln.position, st)  # noqa: E731
    base = v({})
    return {k: v({k: 1.0}) - base for k in keys}


def game_variance(ctx: Context, pid: int) -> tuple[float, float]:
    """(mean, variance) of one team game's fantasy points for ``pid``
    against a league-average opponent (see the module docstring)."""
    ln = ctx.lines[pid]
    if not ln.is_goalie:
        line = expected_line(ln, None)
        w = _weights(ctx, pid, SKATER_STATS)
        var = OVERDISPERSION * sum(w[k] ** 2 * line.get(k, 0.0) for k in SKATER_STATS)
        return ctx.scorer.value_stats(pid, ln.position, line), var
    start = expected_line(ln, None, assume_start=True)
    w = _weights(ctx, pid, ("wins", "saves", "goals_against", "shutouts"))
    p_w = min(max(start.get("wins", 0.0), 0.0), 1.0)
    p_so = min(max(start.get("shutouts", 0.0), 0.0), 1.0)
    var_s = (w["wins"] ** 2 * p_w * (1 - p_w)
             + w["shutouts"] ** 2 * p_so * (1 - p_so)
             + w["saves"] ** 2 * start.get("saves", 0.0)
             + w["goals_against"] ** 2 * start.get("goals_against", 0.0))
    m_s = ctx.scorer.value_stats(pid, ln.position, start)
    s = min(max(ln.start_share, 0.0), 1.0)
    return s * m_s, s * var_s + s * (1 - s) * m_s ** 2


class WeekSim:
    """Best nightly lineups over the rest of the week, with spread."""

    def __init__(self, ctx: Context, dates: list[str]):
        self.ctx, self.dates = ctx, dates
        self.sim = _Sim(ctx, dates, league_slots(ctx)) if dates else None
        self._var: dict[int, tuple[float, float]] = {}

    def _gvar(self, pid: int, game_mean: float) -> float:
        if pid not in self._var:
            self._var[pid] = game_variance(self.ctx, pid)
        m, v = self._var[pid]
        return v * (game_mean / m) if m > 0 else v

    def project(self, roster: dict[int, set[str]]) -> dict:
        nights, mean, var = [], 0.0, 0.0
        for d in self.dates:
            total, assign = self.sim.night(roster, d)
            v = sum(self._gvar(p, self.sim.games(p)[d]) for p in assign)
            playing = sum(1 for p in roster if self.sim.games(p).get(d, 0.0) > 0)
            nights.append({"date": d, "playing": playing, "starters": len(assign),
                           "mean": total})
            mean += total
            var += v
        return {"mean": mean, "var": var, "nights": nights}


def remaining_dates(week_dates: list[str], start: str) -> list[str]:
    return [d for d in week_dates if d >= start]


def team_rosters(ctx: Context, league: dict) -> dict[int, dict[int, set[str]]]:
    return {int(t): team_players(ctx, v["players"])[0]
            for t, v in (league.get("teams") or {}).items()}


def weekly(ctx: Context, league: dict, matchups: dict, start: str) -> dict:
    """Every matchup this week: score now, projected rest of week, final,
    win chance. ``start``: first day still to play (today, or tomorrow when
    tonight's games are already in the score)."""
    dates = remaining_dates(matchups.get("dates") or [], start)
    ws = WeekSim(ctx, dates)
    rosters = team_rosters(ctx, league)
    names = {int(t): v.get("name") or f"Team {t}" for t, v in (league.get("teams") or {}).items()}
    proj = {t: (ws.project(r) if dates else {"mean": 0.0, "var": 0.0, "nights": []})
            for t, r in rosters.items()}
    out = []
    for m in matchups.get("matchups") or []:
        a, b = m["teams"]
        side = []
        for i, t in enumerate((a, b)):
            p = proj.get(t, {"mean": 0.0, "var": 0.0, "nights": []})
            side.append({"id": t, "name": names.get(t, f"Team {t}"),
                         "score": m["score"][i], "yahoo_proj": m["yahoo_proj"][i],
                         "rest": p["mean"], "sd": math.sqrt(p["var"]),
                         "final": m["score"][i] + p["mean"], "nights": p["nights"]})
        pa = win_chance(side[0]["final"], proj.get(a, {"var": 0})["var"],
                        side[1]["final"], proj.get(b, {"var": 0})["var"])
        side[0]["win"], side[1]["win"] = pa, 1 - pa
        out.append({"teams": side})
    return {"week": matchups.get("week"), "dates": matchups.get("dates") or [],
            "remaining": dates, "matchups": out, "ws": ws, "rosters": rosters}


def swing_adds(ctx: Context, ws: WeekSim, roster: dict[int, set[str]],
               free_agents: dict[int, set[str]], available_from: dict[int, str],
               my_final: float, my_var: float, opp_final: float, opp_var: float,
               start: str, top: int = 8, pool: int = 60) -> dict:
    """Best single add/drop for this week (see the module docstring)."""
    if not ws.dates:
        return {"adds": [], "drop_pool": []}
    days = len(ws.dates)
    droppable = [p for p in roster
                 if hold_check(ctx, p, start, days)["verdict"] != "HOLD"]
    base = ws.project(roster)["mean"]
    loss = {p: base - ws.project({q: e for q, e in roster.items() if q != p})["mean"]
            for p in droppable}
    drop = sorted(droppable, key=lambda p: loss[p])
    cands = sorted((p for p in free_agents if p in ctx.lines and p not in roster),
                   key=lambda p: -ctx.scorer.per_game.get(p, 0.0))[:pool]
    rows = []
    for p in cands:
        if not drop:
            break
        out_p = drop[0]
        after = {q: e for q, e in roster.items() if q != out_p}
        after[p] = free_agents[p]
        gain = ws.project(after)["mean"] - base
        if available_from.get(p, "") > start:
            # on waivers: his games before he clears can't count (at most
            # what he'd have added on those nights)
            gain -= sum(ws.sim.games(p).get(d, 0.0) for d in ws.dates
                        if d < available_from[p])
        if gain <= 0.05:
            continue
        rows.append({"add": p, "drop": out_p, "gain": gain,
                     "win": win_chance(my_final + gain, my_var, opp_final, opp_var),
                     "games": sum(1 for d in ws.dates if ws.sim.games(p).get(d, 0.0) > 0),
                     "waiver_until": available_from.get(p)})
    rows.sort(key=lambda r: -r["gain"])
    return {"adds": rows[:top], "drop_pool": drop[:3]}


def today() -> str:
    return datetime.date.today().isoformat()
