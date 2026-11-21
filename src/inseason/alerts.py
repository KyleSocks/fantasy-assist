"""Alerts about YOUR team, made on every Refresh and kept in alerts.json:

  injury     a Yahoo injury tag appears, changes or clears on your player
  ir         your player moved to / off an IR slot
  role       your player promoted or demoted (line or PP unit)
  pp1        a free agent just moved up to PP1 (worth a look)
  b2b        your goalie's team plays the second night of a back-to-back
             tomorrow: he's likely to sit

If config/inseason.yaml has ``alerts: {url: ...}``, each refresh's new
alerts are also POSTed there as one plain-text message -- an ntfy topic
(https://ntfy.sh/<topic> or a self-hosted ntfy) shows them as phone
notifications. The same alert isn't sent twice (``seen`` keys).
"""
from __future__ import annotations

import datetime

MAX_KEPT = 300


def _players(snap: dict | None, team_id) -> dict[str, dict]:
    t = ((snap or {}).get("teams") or {}).get(str(team_id)) or {}
    return {f"{p['name']}|{p.get('team', '')}": p for p in t.get("players") or []}


def make(old: dict | None, new: dict, ctx, my_pids: dict[str, int],
         fa_pids: set[int], today: datetime.date) -> list[dict]:
    """New alerts for this refresh (see the module docstring). ``my_pids``
    maps "name|TEAM" of your Yahoo roster to player ids."""
    from .evaluate import goalie_share
    at = new.get("fetched")
    me = new.get("my_team_id")
    before, after = _players(old, me), _players(new, me)
    out: list[dict] = []

    def add(kind, key, text, pid=None):
        out.append({"at": at, "kind": kind, "key": key, "text": text, "player_id": pid})

    if old:   # first pull: no "before" to compare with
        for k, p in after.items():
            was = before.get(k)
            if not was:
                continue
            if (was.get("injury") or None) != (p.get("injury") or None):
                now = p.get("injury")
                add("injury", f"injury:{k}:{now}",
                    f"{p['name']}: {'now ' + now if now else 'injury tag cleared'}"
                    + (f" (was {was['injury']})" if was.get("injury") and now else ""),
                    my_pids.get(k))
            if bool(was.get("inactive")) != bool(p.get("inactive")):
                add("ir", f"ir:{k}:{bool(p.get('inactive'))}",
                    f"{p['name']} {'moved to IR' if p.get('inactive') else 'back off IR'}",
                    my_pids.get(k))
    for k, pid in my_pids.items():
        r = ctx.roles.get(pid) or {}
        if r.get("change"):
            ln = ctx.lines[pid]
            now = ", ".join(x for x in (r.get("line"), r.get("pp")) if x) or "no PP"
            add("role", f"role:{pid}:{r.get('date')}:{now}",
                f"{ln.name} {'promoted' if r['change'] == 'up' else 'demoted'} to {now}"
                f" (last game {r.get('date')})", pid)
    for pid in fa_pids:
        r = ctx.roles.get(pid) or {}
        if r.get("change") == "up" and r.get("pp") == "PP1" and r.get("usual_pp") != "PP1":
            ln = ctx.lines[pid]
            add("pp1", f"pp1:{pid}:{r.get('date')}",
                f"Free agent {ln.name} ({ln.team}) moved up to PP1", pid)
    tomorrow = (today + datetime.timedelta(days=1)).isoformat()
    for k, pid in my_pids.items():
        ln = ctx.lines.get(pid)
        if not ln or not ln.is_goalie or ln.start_share <= 0:
            continue
        if not ctx.team_games(ln.team, tomorrow, tomorrow):
            continue
        share = goalie_share(ctx, ln, tomorrow)
        if share < ln.start_share - 1e-9:
            add("b2b", f"b2b:{pid}:{tomorrow}",
                f"{ln.name}: {ln.team} is on a back-to-back tomorrow, start chance"
                f" about {round(100 * share)}% (usually {round(100 * ln.start_share)}%)", pid)
    return out


def merge(log: dict | None, new: list[dict]) -> tuple[dict, list[dict]]:
    """(log with unseen alerts added, the unseen ones)."""
    log = dict(log or {})
    seen = set(log.get("seen") or [])
    fresh = [a for a in new if a["key"] not in seen]
    seen |= {a["key"] for a in fresh}
    items = (list(log.get("items") or []) + fresh)[-MAX_KEPT:]
    return {"items": items, "seen": sorted(seen)[-5 * MAX_KEPT:]}, fresh


def send(url: str, alerts: list[dict], title: str = "Fantasy Assist") -> None:
    """POST the alerts as one plain-text message (ntfy-style headers)."""
    import httpx
    if not alerts:
        return
    body = "\n".join(a["text"] for a in alerts)
    httpx.post(url, content=body.encode("utf-8"), timeout=15,
               headers={"Title": title, "Tags": "ice_hockey"}).raise_for_status()
