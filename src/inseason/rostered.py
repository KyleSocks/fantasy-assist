"""% rostered across Yahoo leagues over time ("bandwagon" adds and drops).

Every Refresh reads % rostered for every player on the free-agent pages
and every player rostered in your league. Each pull is one snapshot in
market_history.json:

    {"snapshots": [{"at": "2026-11-20T09:10", "pct": {"name|TEAM": 37.0, ...}}]}

kept for KEEP_DAYS days. A player's change over N days = his % now minus
his % in the newest snapshot at least N days old (or the oldest one, if
history is shorter: ``since`` says which).
"""
from __future__ import annotations

import datetime

KEEP_DAYS = 30


def key(name: str, team: str | None) -> str:
    return f"{' '.join(str(name).lower().split())}|{team or ''}"


def update(history: dict | None, rows: list[dict], at: str) -> dict:
    """history with a snapshot for ``rows`` (dicts with name, team,
    pct_ros) added; snapshots older than KEEP_DAYS dropped."""
    snaps = list((history or {}).get("snapshots") or [])
    pct = {key(r["name"], r.get("team")): float(r["pct_ros"])
           for r in rows if r.get("pct_ros") is not None and r.get("name")}
    if not pct:
        return {"snapshots": snaps}
    snaps = [s for s in snaps if s.get("at") != at] + [{"at": at, "pct": pct}]
    snaps.sort(key=lambda s: s["at"])
    cutoff = (datetime.datetime.fromisoformat(at)
              - datetime.timedelta(days=KEEP_DAYS)).isoformat(timespec="minutes")
    return {"snapshots": [s for s in snaps if s["at"] >= cutoff]}


def changes(history: dict | None, days: int) -> dict[str, dict]:
    """{player key: {"pct", "delta", "since"}} from the latest snapshot."""
    snaps = (history or {}).get("snapshots") or []
    if not snaps:
        return {}
    now = snaps[-1]
    target = (datetime.datetime.fromisoformat(now["at"])
              - datetime.timedelta(days=days)).isoformat(timespec="minutes")
    older = [s for s in snaps[:-1] if s["at"] <= target] or snaps[:1]
    base = older[-1]
    out = {}
    for k, p in now["pct"].items():
        was = base["pct"].get(k) if base is not now else None
        out[k] = {"pct": p, "delta": None if was is None else p - was,
                  "since": base["at"] if base is not now else None}
    return out


def by_player(lines: dict, ch: dict[str, dict]) -> dict[int, dict]:
    """changes() keyed by player id: name + NHL team, else name alone when
    only one player carries it."""
    idx: dict[str, list[int]] = {}
    for pid, ln in lines.items():
        idx.setdefault(key(ln.name, ln.team), []).append(pid)
        idx.setdefault(key(ln.name, None), []).append(pid)
    out: dict[int, dict] = {}
    for k, v in ch.items():
        hit = idx.get(k) or []
        if not hit:
            loose = idx.get(k.split("|")[0] + "|") or []
            hit = loose if len(loose) == 1 else []
        if len(hit) == 1:
            out[hit[0]] = v
    return out
