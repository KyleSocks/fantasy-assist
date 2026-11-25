"""Projection sandbox: "what if he got these minutes?"

Set a skater's power-play and even-strength ice time per game and the
projection follows the minutes, from his own scoring rates:

  base        his blended rates before the deployment adjustment
              (deploy.py), i.e. at his season minutes
  EV rate     his even-strength points (points - PP points) per second of
              season EV time
  PP rate     his PP points per second of season PP time; with under a
              minute of PP time a game there's nothing to go on, so the
              league's PP points per second for his position group
              (forwards / defence, from everyone with 2+ minutes)
  new points  EV rate x new EV time + PP rate x new PP time, split into
              goals and assists the way his points split (league-typical
              40% goals for forwards, 25% for defence without points)
  shots       x (new total time / season total time); hits and blocks stay

Value per game and rest of season (same schedule weights as everything
else) at the new minutes, against his current projection. Presets are his
team's own averages over the last 7 days for each line and PP unit
(lines.py roles), so "PP1" means what PP1 gets on that team.
"""
from __future__ import annotations

import copy

from .evaluate import Context, _value, expected_line

MIN_PP = 60.0          # seconds of PP a game before his own PP rate counts


def season_minutes(ctx: Context, pid: int) -> tuple[float, float] | None:
    ln = ctx.lines[pid]
    d = ln.extra.get("deploy") or ln.extra.get("deploy_base")
    if not d or not (d.get("season_pp", 0) + d.get("season_ev", 0)):
        return None
    return d["season_pp"], d["season_ev"]


def base_rates(ctx: Context, pid: int) -> dict[str, float]:
    """Blended rates before the deployment adjustment."""
    ln = ctx.lines[pid]
    delta = (ln.extra.get("deploy") or {}).get("delta") or {}
    return {k: v - delta.get(k, 0.0) for k, v in ln.blended.items()}


def league_pp_rate(ctx: Context) -> dict[str, float]:
    """PP points per second of PP time, forwards and defence."""
    num = {"F": 0.0, "D": 0.0}
    den = {"F": 0.0, "D": 0.0}
    for pid, ln in ctx.lines.items():
        if ln.is_goalie:
            continue
        m = season_minutes(ctx, pid)
        if not m or m[0] < 120:
            continue
        g = "D" if ln.position == "D" else "F"
        num[g] += base_rates(ctx, pid).get("pp_points", 0.0)
        den[g] += m[0]
    return {g: num[g] / den[g] if den[g] else 0.0 for g in num}


def rates_at(ctx: Context, pid: int, pp: float, ev: float,
             lg: dict[str, float] | None = None) -> dict[str, float]:
    """His per-game rates at ``pp`` / ``ev`` seconds a game (docstring)."""
    ln = ctx.lines[pid]
    m = season_minutes(ctx, pid)
    b = base_rates(ctx, pid)
    if not m:
        return b
    s_pp, s_ev = m
    group = "D" if ln.position == "D" else "F"
    pts = b["goals"] + b["assists"]
    ppp = min(b["pp_points"], pts)
    ev_rate = (pts - ppp) / s_ev if s_ev > 0 else 0.0
    if s_pp >= MIN_PP and ppp > 0:
        pp_rate = ppp / s_pp
    else:
        pp_rate = (lg or league_pp_rate(ctx))[group]
    new_pp = pp_rate * pp
    new_pts = ev_rate * ev + new_pp
    gfrac = b["goals"] / pts if pts > 0 else (0.25 if group == "D" else 0.40)
    out = dict(b)
    out.update(goals=gfrac * new_pts, assists=(1 - gfrac) * new_pts, pp_points=new_pp,
               shots=b["shots"] * ((pp + ev) / (s_pp + s_ev)))
    return out


def presets(ctx: Context, pid: int) -> list[dict]:
    """His team's average last-7-day PP and EV time per line / PP unit."""
    team = ctx.lines[pid].team
    is_d = ctx.lines[pid].position == "D"
    pp: dict[str, list[float]] = {}
    ev: dict[str, list[float]] = {}
    for q, r in (ctx.roles or {}).items():
        if q not in ctx.lines or ctx.lines[q].team != team or ctx.lines[q].is_goalie:
            continue
        if r.get("recent_toi") is None:
            continue
        rpp = r.get("recent_pp_toi") or 0.0
        if r.get("pp"):
            pp.setdefault(r["pp"], []).append(rpp)
        if r.get("line") and r["line"].startswith("D") == is_d:
            ev.setdefault(r["line"], []).append(r["recent_toi"] - rpp)
    avg = {k: sum(v) / len(v) for k, v in pp.items() if v}
    out = [{"kind": "pp", "label": k, "sec": avg[k]} for k in sorted(avg)]
    out.append({"kind": "pp", "label": "No PP", "sec": 0.0})
    eva = {k: sum(v) / len(v) for k, v in ev.items() if v}
    out += [{"kind": "ev", "label": k, "sec": eva[k]} for k in sorted(eva)]
    return out


def whatif(ctx: Context, pid: int, start: str, pp: float | None = None,
           ev: float | None = None) -> dict:
    """{"season", "current", "new", "diff", "presets"}; ``pp``/``ev`` default
    to the minutes his projection already expects."""
    ln = ctx.lines[pid]
    m = season_minutes(ctx, pid)
    if ln.is_goalie or not m:
        raise ValueError("no ice-time data for him yet")
    dep = ln.extra.get("deploy") or {}
    cur_pp, cur_ev = dep.get("exp_pp", m[0]), dep.get("exp_ev", m[1])
    pp = cur_pp if pp is None else max(0.0, float(pp))
    ev = cur_ev if ev is None else max(0.0, float(ev))
    # both sides through the same minutes -> points model, so the difference
    # is only the minutes (his official projection is reported as is)
    lg = league_pp_rate(ctx)
    now, alt = copy.copy(ln), copy.copy(ln)
    now.blended = rates_at(ctx, pid, cur_pp, cur_ev, lg)
    alt.blended = rates_at(ctx, pid, pp, ev, lg)
    end = ctx.fantasy_end()
    games = ctx.team_games(ln.team, start, end)

    def side(line, p, e):
        return {"pp": p, "ev": e,
                "per_game": _value(ctx, line, expected_line(line, None)),
                # not game_value: that's memoised per player id, and these
                # are what-if copies of him
                "ros": sum(_value(ctx, line, expected_line(line, ctx.factors.get(opp)))
                           * ctx.game_weight(d) for d, opp in games),
                "rates": {k: line.blended[k] for k in ("goals", "assists", "pp_points", "shots")}}

    cur, new = side(now, cur_pp, cur_ev), side(alt, pp, ev)
    return {"season": {"pp": m[0], "ev": m[1]}, "current": cur, "new": new,
            "projection": _value(ctx, ln, expected_line(ln, None)),
            "diff": {"per_game": new["per_game"] - cur["per_game"], "ros": new["ros"] - cur["ros"]},
            "games_left": len(games), "presets": presets(ctx, pid)}
