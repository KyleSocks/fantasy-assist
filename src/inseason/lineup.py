"""Nightly lineup optimiser and roster-aware marginal values.

best_lineup(): pick which of tonight's playing roster players start, given
the league's active slots (C, LW, RW, D, Util, G) and each player's position
eligibility, to maximise total expected value. Starting sets form a
transversal matroid, so adding players in descending value and keeping each
one only if a valid slot assignment still exists (bipartite augmenting path)
is exactly optimal.

marginal_values(): for a candidate in the streaming roster spot, his value on
a date is lineup(roster + him) - lineup(roster) -- so a winger is worth 0 on a
night every wing and Util slot is already taken by better players, while a D
who fills an empty D slot that night is worth his whole game.
"""
from __future__ import annotations

UTIL_OK = {"C", "LW", "RW", "D"}


def _fits(pos: set[str], slot: str) -> bool:
    if slot == "Util":
        return bool(pos & UTIL_OK)
    return slot in pos


def best_lineup(players: list[tuple[int, set[str], float]],
                slots: dict[str, int]) -> tuple[float, dict[int, str]]:
    """players: (pid, eligible positions, value tonight). Returns (total,
    {pid: slot}) over the starters. Players with value <= 0 never start."""
    seats = [s for s, n in sorted(slots.items()) for _ in range(n)]
    owner: list[int | None] = [None] * len(seats)   # seat -> index in chosen
    chosen: list[tuple[int, set[str], float]] = []

    def augment(i: int, seen: set[int]) -> bool:
        pos = chosen[i][1]
        for s, slot in enumerate(seats):
            if s in seen or not _fits(pos, slot):
                continue
            seen.add(s)
            if owner[s] is None or augment(owner[s], seen):
                owner[s] = i
                return True
        return False

    for p in sorted(players, key=lambda t: (-t[2], t[0])):
        if p[2] <= 0:
            break
        chosen.append(p)
        snapshot = owner[:]
        if not augment(len(chosen) - 1, set()):
            owner[:] = snapshot
            chosen.pop()
    assign = {chosen[i][0]: seats[s] for s, i in enumerate(owner) if i is not None}
    return sum(p[2] for p in chosen), assign


def marginal_values(
    roster: dict[int, set[str]],
    candidates: dict[int, set[str]],
    game_values: dict[int, dict[str, float]],
    dates: list[str],
    slots: dict[str, int],
) -> dict[int, dict[str, float]]:
    """Value each candidate adds to the nightly lineup, per date.

    ``roster``: the players who stay (the streaming spot's current holder is
    NOT in it -- pass him as a candidate). ``game_values[pid][date]``:
    expected value of his game that night (missing = no game).
    """
    out: dict[int, dict[str, float]] = {c: {} for c in candidates}
    for d in dates:
        base = [(p, e, game_values.get(p, {}).get(d, 0.0))
                for p, e in roster.items()]
        base = [t for t in base if t[2] > 0]
        base_total, _ = best_lineup(base, slots)
        for c, elig in candidates.items():
            v = game_values.get(c, {}).get(d, 0.0)
            if v <= 0:
                continue
            total, _ = best_lineup(base + [(c, elig, v)], slots)
            gain = total - base_total
            if gain > 1e-9:
                out[c][d] = gain
    return out
