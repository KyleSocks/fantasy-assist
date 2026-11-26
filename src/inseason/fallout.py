"""Injury fallout: when a regular is out, who has taken his minutes.

Everything comes from the shift charts we already store (lines.py:
game_deployment, the last few games per team) and the projection's own
ice-time adjustment (deploy.py), so it reflects what coaches have actually
done since he went out -- not a guess at who should move up.

  out        a skater whose team has played a game since his last one
             (he's missing from the team's latest game), whose usual role
             (before his last game) was a top-6 forward line (F1/F2), a
             top-4 pair (D1/D2) or a power-play unit, plus anyone the
             caller lists as injured (Yahoo tags) who has such a role. Out
             longer than the stored games reach, his role is read from his
             season power-play time: top 5 on his team = PP1, next 5 = PP2
  takers     teammates in the team's latest game who now hold his line or
             PP unit and didn't usually, or were promoted, or whose PP time
             went up by ``pp_gain_sec`` or more; each with how much the
             ice-time change moves his projection per game (deploy.py's
             adjustment, priced in the league's scoring)

Sorted by how big a slot opened (PP1 first) and then by the takers'
projection gain.
"""
from __future__ import annotations

from .evaluate import Context
from .lines import label

TOP = {"F1", "F2", "D1", "D2"}
SLOT_RANK = {"PP1": 0, "F1": 1, "D1": 1, "PP2": 2, "F2": 3, "D2": 3}


def _slot(r: dict) -> list[str]:
    """The parts of his usual role worth taking over."""
    out = []
    if r.get("usual_pp") in ("PP1", "PP2"):
        out.append(r["usual_pp"])
    if r.get("usual_line") in TOP:
        out.append(r["usual_line"])
    return out


def deploy_gain(ctx: Context, pid: int) -> float:
    """How much the ice-time adjustment (deploy.py) moves his projection
    per game, in league units (0 without one)."""
    dep = ctx.lines[pid].extra.get("deploy") if pid in ctx.lines else None
    if not dep or not dep.get("delta"):
        return 0.0
    ln = ctx.lines[pid]
    return (ctx.scorer.value_stats(pid, ln.position, dep["delta"])
            - ctx.scorer.value_stats(pid, ln.position, {}))


def latest_games(roles: dict) -> dict[str, str]:
    """{team: date of its latest stored game}."""
    latest: dict[str, str] = {}
    for r in roles.values():
        if r.get("team") and r.get("date"):
            latest[r["team"]] = max(latest.get(r["team"], ""), r["date"])
    return latest


def season_pp_slot(ctx: Context, pid: int) -> list[str]:
    """PP1 / PP2 from his season PP time rank on his team (top 5 / next 5)."""
    team = ctx.lines[pid].team
    ranked = sorted(((ctx.lines[q].extra.get("deploy") or {}).get("season_pp") or 0.0, q)
                    for q in ctx.lines if ctx.lines[q].team == team
                    and not ctx.lines[q].is_goalie)
    order = [q for v, q in reversed(ranked) if v > 0]
    if pid not in order:
        return []
    i = order.index(pid)
    return ["PP1"] if i < 5 else ["PP2"] if i < 10 else []


def usual_slot(ctx: Context, pid: int) -> list[str]:
    """The top-6 / top-4 / PP role he normally holds (see module docstring)."""
    r = (ctx.roles or {}).get(pid) or {}
    if r.get("date"):
        return _slot({"usual_pp": r.get("usual_pp") or r.get("pp"),
                      "usual_line": r.get("usual_line") or r.get("line")})
    return season_pp_slot(ctx, pid)


def takers(ctx: Context, pid: int, team: str, slot: list[str], latest: dict[str, str],
           on_slot_counts: bool, pp_gain_sec: float = 30.0) -> list[dict]:
    """Teammates in the team's latest game holding (part of) ``slot``
    beyond their own usual role (module docstring). ``on_slot_counts``:
    being on his line / unit with ice time above season level is enough
    (he's been out a while, so their new role already reads as usual)."""
    out = []
    for q, rq in (ctx.roles or {}).items():
        if q == pid or rq.get("team") != team or rq.get("date") != latest.get(team) \
                or q not in ctx.lines or ctx.lines[q].is_goalie:
            continue
        took = [s for s in slot
                if (s.startswith("PP") and rq.get("pp") == s and rq.get("usual_pp") != s)
                or (not s.startswith("PP") and rq.get("line") == s and rq.get("usual_line") != s)]
        pp_up = (rq.get("recent_pp_toi") or 0) - (rq.get("prior_pp_toi") or 0)
        gain = deploy_gain(ctx, q)
        on_slot = rq.get("pp") in slot or rq.get("line") in slot
        if not (rq.get("change") == "up" or pp_up >= pp_gain_sec or (took and gain > 0)
                or (on_slot_counts and on_slot and gain > 0)):
            continue
        out.append({"id": q, "name": ctx.lines[q].name, "pos": ctx.lines[q].position,
                    "role": label(rq), "was": ", ".join(x for x in (
                        rq.get("usual_line"), rq.get("usual_pp") or "no PP") if x),
                    "took": took, "pp_gain_sec": pp_up, "gain": gain,
                    "per_game": ctx.scorer.per_game.get(q, 0.0)})
    out.sort(key=lambda t: (-len(t["took"]), -t["gain"]))
    return out


def fallout(ctx: Context, injured: dict[int, dict] | None = None,
            pp_gain_sec: float = 30.0) -> list[dict]:
    """[{id, name, team, slot: [...], last_game, tag, takers: [...]}]."""
    injured = injured or {}
    roles = ctx.roles or {}
    latest = latest_games(roles)
    cands = {pid for pid, r in roles.items()
             if r.get("team") and r.get("date") and r["date"] < latest.get(r["team"], "")}
    cands |= {pid for pid in injured if pid in ctx.lines}
    out = []
    for pid in cands:
        if pid not in ctx.lines or ctx.lines[pid].is_goalie:
            continue
        r = roles.get(pid) or {}
        team = r.get("team") or ctx.lines[pid].team
        missing = bool(r.get("date")) and r["date"] < latest.get(team, "")
        slot = usual_slot(ctx, pid)
        if not slot or team not in latest:
            continue
        tk = takers(ctx, pid, team, slot, latest, on_slot_counts=not missing,
                    pp_gain_sec=pp_gain_sec)
        info = injured.get(pid) or {}
        out.append({"id": pid, "name": ctx.lines[pid].name, "team": team,
                    "slot": slot, "last_game": r.get("date"), "missing": missing,
                    "tag": info.get("tag"), "back": info.get("back"),
                    "per_game": ctx.scorer.per_game.get(pid, 0.0), "takers": tk[:5]})
    out.sort(key=lambda x: (min(SLOT_RANK.get(s, 9) for s in x["slot"]),
                            -sum(t["gain"] for t in x["takers"])))
    return out


def returning(ctx: Context, injured: dict[int, dict], start: str, days: int = 7,
              pp_gain_sec: float = 30.0) -> list[dict]:
    """Regulars coming back, and who loses minutes when they do.

      due back     listed injured with an expected return (tag estimate or
                   your own date, teams.trade_roster) within ``days`` of
                   ``start``, or day-to-day (back = start)
      just back    played his team's latest game after missing at least
                   two of the stored ones before it (a long absence or a
                   recent one -- both mean his stand-ins are about to slide)

    For "due back" (and not in his team's latest game), at risk = whoever
    holds his line / PP unit now beyond their usual role (the fallout's
    taker rule, being on his unit with above-season ice time counting,
    limited to players actually on that line or unit); each loses about
    what the extra ice time added (their ice-time adjustment, reversed). For "just back",
    teammates already moved down in that game (demoted, or 30+ s less PP
    time) are listed as having slid already.
    """
    import datetime
    roles = ctx.roles or {}
    latest = latest_games(roles)
    horizon = (datetime.date.fromisoformat(start) + datetime.timedelta(days=days)).isoformat()
    team_games: dict[str, int] = {}
    for r in roles.values():
        if r.get("team"):
            team_games[r["team"]] = max(team_games.get(r["team"], 0), r.get("games") or 0)
    out = []
    for pid, info in injured.items():
        if pid not in ctx.lines or ctx.lines[pid].is_goalie:
            continue
        back = info.get("back") or start
        if back > horizon:
            continue
        team = ctx.lines[pid].team
        slot = usual_slot(ctx, pid)
        if not slot or team not in latest:
            continue
        if (roles.get(pid) or {}).get("date") == latest[team]:
            continue        # played the team's latest game: not actually out
        # at risk = holding part of his slot (not just anyone promoted)
        risk = [t for t in takers(ctx, pid, team, slot, latest, on_slot_counts=True,
                                  pp_gain_sec=pp_gain_sec)
                if t["took"] or any(s in (t["role"] or "") for s in slot)]
        out.append({"id": pid, "name": ctx.lines[pid].name, "team": team, "slot": slot,
                    "status": "due", "back": back, "tag": info.get("tag"),
                    "per_game": ctx.scorer.per_game.get(pid, 0.0),
                    "at_risk": [{**t, "loss": -max(t["gain"], 0.0)} for t in risk[:5]]})
    for pid, r in roles.items():
        team = r.get("team")
        if pid in injured or pid not in ctx.lines or ctx.lines[pid].is_goalie or not team:
            continue
        if r.get("date") != latest.get(team) or (r.get("games") or 0) > team_games.get(team, 0) - 2:
            continue
        slot = _slot({"usual_pp": r.get("pp") or r.get("usual_pp"),
                      "usual_line": r.get("line") or r.get("usual_line")})
        if not slot:
            continue
        slid = []
        for q, rq in roles.items():
            if q == pid or rq.get("team") != team or rq.get("date") != latest.get(team) \
                    or q not in ctx.lines or ctx.lines[q].is_goalie:
                continue
            pp_down = (rq.get("prior_pp_toi") or 0) - (rq.get("recent_pp_toi") or 0)
            was_on = rq.get("usual_pp") in slot or rq.get("usual_line") in slot
            if (rq.get("change") == "down" and was_on) or (was_on and pp_down >= pp_gain_sec):
                slid.append({"id": q, "name": ctx.lines[q].name, "pos": ctx.lines[q].position,
                             "role": label(rq), "was": ", ".join(x for x in (
                                 rq.get("usual_line"), rq.get("usual_pp") or "no PP") if x),
                             "pp_gain_sec": -pp_down, "loss": min(deploy_gain(ctx, q), 0.0),
                             "per_game": ctx.scorer.per_game.get(q, 0.0)})
        out.append({"id": pid, "name": ctx.lines[pid].name, "team": team, "slot": slot,
                    "status": "back", "back": r["date"], "tag": None,
                    "per_game": ctx.scorer.per_game.get(pid, 0.0), "at_risk": slid[:5]})
    out.sort(key=lambda x: (x["status"] != "back", x["back"],
                            min(SLOT_RANK.get(s, 9) for s in x["slot"])))
    return [x for x in out if x["at_risk"] or x["status"] == "due"]
