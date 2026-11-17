"""Roster changes across the league, from successive roster snapshots.

Every Refresh stores each team's roster (league_rosters.json). Comparing the
new snapshot with the previous one gives what changed in between:

  add       a player nobody rostered is now on a team
  drop      a rostered player is on no team any more
  trade     players moved between two teams in BOTH directions
  claim     a player moved from one team to another with nothing going
            back: one team dropped him and the other picked him up
  ir        moved to an IR / IR+ / NA slot
  activate  moved back off one

The changes are appended to roster_moves.json. What this can't see: a player
added AND dropped between two refreshes, and the exact time of a move -- each
event carries the two snapshot times it happened between (``since``/``at``).

Players are matched by name. A name that two rostered players share (two
Sebastian Ahos) is matched by name + NHL team instead, so an NHL trade never
reads as a drop and an add.
"""
from __future__ import annotations

from collections import Counter

MAX_EVENTS = 4000          # oldest are dropped past this
TYPES = ("add", "claim", "drop", "trade", "ir", "activate")


def _name(p: dict) -> str:
    return " ".join(str(p.get("name", "")).lower().split())


def _owners(snapshot: dict, shared: set[str], skip: set[str]) -> dict[str, tuple[str, dict]]:
    """{player key: (team id, player)} for every rostered player."""
    out: dict[str, tuple[str, dict]] = {}
    for tid, team in (snapshot.get("teams") or {}).items():
        if str(tid) in skip:
            continue
        for p in team.get("players") or []:
            n = _name(p)
            if not n:
                continue
            key = f"{n}|{p.get('team', '')}" if n in shared else n
            out.setdefault(key, (str(tid), p))
    return out


def _shared_names(*snapshots: dict) -> set[str]:
    """Names carried by more than one rostered player in the same snapshot.
    (Across snapshots a second NHL team for one name is just an NHL trade.)"""
    out: set[str] = set()
    for snap in snapshots:
        seen = {(_name(p), str(p.get("team", "")))
                for team in (snap.get("teams") or {}).values()
                for p in team.get("players") or []}
        out |= {n for n, c in Counter(n for n, _ in seen).items() if c > 1 and n}
    return out


def emptied_teams(old: dict | None, new: dict) -> list[str]:
    """Names of teams that had players in ``old`` and have none (or are
    missing) in ``new`` -- a roster page that didn't load. The refresh
    refuses such a pull so it never becomes the next baseline."""
    if not old:
        return []
    n_teams = new.get("teams") or {}
    return [t.get("name") or f"team {tid}"
            for tid, t in (old.get("teams") or {}).items()
            if t.get("players") and not (n_teams.get(tid) or {}).get("players")]


def _slim(p: dict) -> dict:
    return {"name": p.get("name"), "team": p.get("team"),
            "positions": list(p.get("positions") or [])}


def diff(old: dict | None, new: dict) -> list[dict]:
    """Events between two league_rosters.json snapshots (see the module
    docstring), stamped with ``since`` = old's fetch time and ``at`` = new's.

    A team with no players in either snapshot is left out: a team that is new
    this pull has no "before" to compare with. (A team that had players and
    comes back empty never gets here -- see emptied_teams.) No previous
    snapshot -> no events: the first one is the baseline.
    """
    if not old or not old.get("teams"):
        return []
    o_teams, n_teams = old.get("teams") or {}, new.get("teams") or {}
    skip = {str(t) for t in set(o_teams) | set(n_teams)
            if not (o_teams.get(t) or {}).get("players")
            or not (n_teams.get(t) or {}).get("players")}
    shared = _shared_names(old, new)
    before, after = _owners(old, shared, skip), _owners(new, shared, skip)
    stamp = {"since": old.get("fetched"), "at": new.get("fetched")}
    events: list[dict] = []
    for key, (tid, p) in after.items():
        if key not in before:
            # a player on a skipped team isn't an add; he's just unreadable
            events.append({**stamp, "type": "add", "team_id": int(tid),
                           "player": _slim(p)})
            continue
        was_tid, was = before[key]
        if was_tid != tid:
            events.append({**stamp, "type": "trade", "team_id": int(tid),
                           "from_team_id": int(was_tid), "player": _slim(p)})
        elif bool(was.get("inactive")) != bool(p.get("inactive")):
            events.append({**stamp, "type": "ir" if p.get("inactive") else "activate",
                           "team_id": int(tid), "player": _slim(p)})
    for key, (tid, p) in before.items():
        if key not in after:
            events.append({**stamp, "type": "drop", "team_id": int(tid),
                           "player": _slim(p)})
    # one-way traffic between two teams isn't a trade: A dropped him, B added
    ways = {(e["from_team_id"], e["team_id"]) for e in events if e["type"] == "trade"}
    for e in events:
        if e["type"] == "trade" and (e["team_id"], e["from_team_id"]) not in ways:
            e["type"] = "claim"
    if skip:
        # a player who "left" for / "arrived" from an unreadable team is
        # unknown, not a drop or an add: only keep what both sides confirm
        gone = _owners(old, shared, set()), _owners(new, shared, set())
        events = [e for e in events if not _touches_skipped(e, gone, shared, skip)]
    events.sort(key=lambda e: (e["team_id"], TYPES.index(e["type"]),
                               e["player"]["name"] or ""))
    return events


def _touches_skipped(e: dict, full: tuple[dict, dict], shared: set[str],
                     skip: set[str]) -> bool:
    n = _name(e["player"])
    key = f"{n}|{e['player'].get('team', '')}" if n in shared else n
    before, after = full
    if e["type"] == "add":
        return key in before and before[key][0] in skip
    if e["type"] == "drop":
        return key in after and after[key][0] in skip
    return False


def append(log: dict | None, events: list[dict], old: dict | None,
           new: dict) -> dict:
    """The stored log with ``events`` added. ``started`` = the fetch time of
    the first snapshot ever compared against (when tracking began)."""
    log = dict(log or {})
    started = log.get("started") or (old or {}).get("fetched") or new.get("fetched")
    kept = (list(log.get("events") or []) + events)[-MAX_EVENTS:]
    return {"started": started, "updated": new.get("fetched"), "events": kept}


def summarize(events: list[dict], team_ids: list[int]) -> dict[int, dict]:
    """Per team: adds, drops, trades (deals, not players: a 2-for-2 is one
    trade for each side) and IR moves, plus the latest ``at``. A claim is an
    add for the team that got him and a drop for the team that let him go."""
    out = {t: {"add": 0, "drop": 0, "trade": 0, "ir": 0, "last": None}
           for t in team_ids}
    deals: set[tuple] = set()

    def bump(tid, kind, at):
        s = out.get(tid)
        if s is None:
            return
        s[kind] += 1
        if at and (s["last"] is None or at > s["last"]):
            s["last"] = at

    for e in events:
        if e["type"] == "trade":
            pair = tuple(sorted((e["team_id"], e.get("from_team_id") or 0)))
            if (e.get("at"), pair) not in deals:
                deals.add((e.get("at"), pair))
                for tid in pair:
                    bump(tid, "trade", e.get("at"))
            continue
        if e["type"] == "claim":
            bump(e["team_id"], "add", e.get("at"))
            bump(e.get("from_team_id"), "drop", e.get("at"))
            continue
        bump(e["team_id"], "ir" if e["type"] in ("ir", "activate") else e["type"],
             e.get("at"))
    return out
