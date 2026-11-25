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


def fallout(ctx: Context, injured: dict[int, dict] | None = None,
            pp_gain_sec: float = 30.0) -> list[dict]:
    """[{id, name, team, slot: [...], last_game, tag, takers: [...]}]."""
    injured = injured or {}
    roles = ctx.roles or {}
    latest: dict[str, str] = {}
    for r in roles.values():
        if r.get("team") and r.get("date"):
            latest[r["team"]] = max(latest.get(r["team"], ""), r["date"])
    def season_pp_slot(pid: int) -> list[str]:
        team = ctx.lines[pid].team
        ranked = sorted(((ctx.lines[q].extra.get("deploy") or {}).get("season_pp") or 0.0, q)
                        for q in ctx.lines if ctx.lines[q].team == team
                        and not ctx.lines[q].is_goalie)
        order = [q for v, q in reversed(ranked) if v > 0]
        if pid not in order:
            return []
        i = order.index(pid)
        return ["PP1"] if i < 5 else ["PP2"] if i < 10 else []

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
        if r.get("date"):
            # his usual role: before his last game, or that game if it's all we have
            usual = {"usual_pp": r.get("usual_pp") or r.get("pp"),
                     "usual_line": r.get("usual_line") or r.get("line")}
            slot = _slot(usual)
        else:
            slot = season_pp_slot(pid)
        if not slot or team not in latest:
            continue
        takers = []
        for q, rq in roles.items():
            if q == pid or rq.get("team") != team or rq.get("date") != latest.get(team) \
                    or q not in ctx.lines or ctx.lines[q].is_goalie:
                continue
            took = [s for s in slot
                    if (s.startswith("PP") and rq.get("pp") == s and rq.get("usual_pp") != s)
                    or (not s.startswith("PP") and rq.get("line") == s and rq.get("usual_line") != s)]
            pp_up = (rq.get("recent_pp_toi") or 0) - (rq.get("prior_pp_toi") or 0)
            gain = deploy_gain(ctx, q)
            # out a while: the new role is already "usual" by now, so being on
            # his unit with ice time above the player's season level counts
            on_slot = rq.get("pp") in slot or rq.get("line") in slot
            if not (rq.get("change") == "up" or pp_up >= pp_gain_sec or (took and gain > 0)
                    or (not missing and on_slot and gain > 0)):
                continue
            takers.append({"id": q, "name": ctx.lines[q].name, "pos": ctx.lines[q].position,
                           "role": label(rq), "was": ", ".join(x for x in (
                               rq.get("usual_line"), rq.get("usual_pp") or "no PP") if x),
                           "took": took, "pp_gain_sec": pp_up, "gain": gain,
                           "per_game": ctx.scorer.per_game.get(q, 0.0)})
        takers.sort(key=lambda t: (-len(t["took"]), -t["gain"]))
        info = injured.get(pid) or {}
        out.append({"id": pid, "name": ctx.lines[pid].name, "team": team,
                    "slot": slot, "last_game": r.get("date"), "missing": missing,
                    "tag": info.get("tag"), "back": info.get("back"),
                    "per_game": ctx.scorer.per_game.get(pid, 0.0), "takers": takers[:5]})
    out.sort(key=lambda x: (min(SLOT_RANK.get(s, 9) for s in x["slot"]),
                            -sum(t["gain"] for t in x["takers"])))
    return out
