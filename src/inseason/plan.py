"""Streaming planner for one roster slot under a weekly add limit.

Given the player currently in the slot, a pool of available players, each
player's expected value for every date in the horizon, and the league's adds
per week, find the sequence of holds that maximises total value:

    hold A Sat-Sun, add B Mon (1 add), add C Thu (1 add), ...

Rules modelled:
- Every switch costs one add, counted against the week the switch happens in
  (weeks start on ``week_start`` weekday, Monday by default, like Yahoo).
- A player you drop can't be re-added inside the horizon (he goes to waivers).
- A player can't be added before his ``available_from`` date (waiver clears).
- The slot's player is assumed to START whenever his team plays. That holds
  when the slot is a streaming spot you keep open; it overstates value on
  nights your other starters already fill every eligible slot.

Beam search over (current holder, adds used per week, players used): exact
for small pools, and the beam keeps it fast for ~50 candidates x 14 days.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Step:
    date: str
    add: int
    drop: int


@dataclass
class Plan:
    total: float
    steps: list[Step]
    holder_by_date: dict[str, int]
    adds_by_week: dict[str, int] = field(default_factory=dict)


def week_key(date: str, week_start: int = 0) -> str:
    """ISO date of the first day of ``date``'s fantasy week (0 = Monday)."""
    d = datetime.date.fromisoformat(date)
    return (d - datetime.timedelta(days=(d.weekday() - week_start) % 7)).isoformat()


def plan_slot(
    current: int,
    values: dict[int, dict[str, float]],
    dates: list[str],
    adds_per_week: int | dict[str, int],
    available_from: dict[int, str] | None = None,
    week_start: int = 0,
    beam: int = 3000,
) -> Plan:
    """Best hold/switch sequence for one slot.

    ``values[pid][date]`` = expected value if ``pid`` is in the slot that day
    (0 or missing = no game). ``adds_per_week`` is either a constant or a
    per-week remaining budget keyed by week_key(). ``current`` must be in
    ``values`` (use {} if he has no games).
    """
    available_from = available_from or {}
    weeks = sorted({week_key(d, week_start) for d in dates})
    budget = ({w: adds_per_week for w in weeks} if isinstance(adds_per_week, int)
              else {w: adds_per_week.get(w, 0) for w in weeks})
    widx = {w: i for i, w in enumerate(weeks)}
    pool = [p for p in values if p != current]

    # state: (score, holder, adds_used tuple, used frozenset, steps tuple)
    states = [(0.0, current, tuple(0 for _ in weeks), frozenset([current]), ())]
    for d in dates:
        wi = widx[week_key(d, week_start)]
        nxt: dict[tuple, tuple] = {}

        def push(score, holder, used_adds, used, steps):
            key = (holder, used_adds, used)
            best = nxt.get(key)
            if best is None or score > best[0]:
                nxt[key] = (score, holder, used_adds, used, steps)

        for score, holder, used_adds, used, steps in states:
            # stay
            push(score + values.get(holder, {}).get(d, 0.0),
                 holder, used_adds, used, steps)
            # switch (add today, he can play today)
            if used_adds[wi] >= budget[weeks[wi]]:
                continue
            for p in pool:
                if p in used or available_from.get(p, d) > d:
                    continue
                v = values[p].get(d, 0.0)
                if v <= 0:
                    continue  # never add someone on a day he doesn't play
                ua = list(used_adds)
                ua[wi] += 1
                push(score + v, p, tuple(ua), used | {p},
                     steps + (Step(d, p, holder),))
        states = sorted(nxt.values(), key=lambda s: -s[0])[:beam]

    score, holder, used_adds, _, steps = states[0]
    by_date: dict[str, int] = {}
    h = current
    queue = list(steps)
    for d in dates:
        while queue and queue[0].date == d:
            h = queue.pop(0).add
        by_date[d] = h
    return Plan(score, list(steps), by_date,
                {w: used_adds[i] for i, w in enumerate(weeks)})
