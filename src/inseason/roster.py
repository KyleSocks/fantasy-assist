"""Your roster and the free-agent pool: parsing, audit, and the roster-aware
streaming plan. Shared by the CLI (`plan`) and the GUI (/api/inseason/...).

List format (data/my_roster.txt, data/fa.txt): one player per line,
"Name | TEAM | C,LW" -- Yahoo eligibility; team and positions optional;
# comments.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from .evaluate import Context, expected_line, find_players, form, hold_check
from .lineup import best_lineup, marginal_values
from .plan import nhl_eligibility, plan_slot, week_key

# EV TOI/GP drop (seconds) that flags a possible role change.
TOI_FLAG_SEC = 90
# Below this many games a skater's season-to-date line is a small sample:
# no "below projection" / "ice time down" flags (the blend already leans
# on the preseason projection). Skaters only miss games when hurt.
MIN_GP_FLAGS = 8
INACTIVE_MARKERS = {"IR", "IR+", "NA", "INJ", "OUT"}


@dataclass
class PlayerList:
    """A parsed roster / free-agent list file."""

    active: dict[int, set[str]] = field(default_factory=dict)
    inactive: dict[int, set[str]] = field(default_factory=dict)  # IR / OUT
    available_from: dict[int, str] = field(default_factory=dict)  # waivers
    tags: dict[int, str] = field(default_factory=dict)  # "# IR-LT", "# DTD"
    missing: list[str] = field(default_factory=list)

    def status(self, pid: int) -> str:
        """FA, W (waivers), OUT (injured free agent) or IR (your IR slot)."""
        if pid in self.inactive:
            return self.inactive_kind.get(pid, "OUT")
        return "W" if pid in self.available_from else "FA"

    inactive_kind: dict[int, str] = field(default_factory=dict)


def _match(ctx: Context, parts: list[str]):
    hits = find_players(ctx, parts[0])
    if len(parts) > 1 and parts[1] and len(hits) > 1:
        hits = [h for h in hits if h.team == parts[1]] or hits
    if len(parts) > 2 and parts[2] and len(hits) > 1:
        # same name, same team (two Elias Petterssons on VAN): match the
        # position family -- D vs G vs forward
        want = {x.strip() for x in parts[2].split(",")}
        fam = "D" if "D" in want else "G" if "G" in want else "F"
        hits = [h for h in hits if {"D": "D", "G": "G"}.get(h.position, "F")
                == fam] or hits
    return hits[0] if hits else None


def parse_list(ctx: Context, text: str) -> PlayerList:
    """Parse "Name | TEAM | C,LW [| IR | OUT | W 2026-11-02]  # tag" lines.

    IR / OUT / NA players go to ``inactive`` (kept with their positions, so
    they can be listed -- but they never enter lineups, plans or streams);
    "W <date>" records when a waiver player can be added; the "# ..."
    comment (Yahoo's injury tag) is kept in ``tags``.
    """
    pl = PlayerList()
    for raw in text.splitlines():
        body, _, comment = raw.partition("#")
        parts = [x.strip() for x in body.split("|")]
        if not parts[0]:
            continue
        # tolerate a hand-written "Name | TEAM | W 2026-11-02" (no positions)
        if (len(parts) == 3 and parts[2]
                and parts[2].split()[0].upper() in INACTIVE_MARKERS | {"W"}):
            parts = [parts[0], parts[1], "", parts[2]]
        ln = _match(ctx, parts)
        if ln is None:
            pl.missing.append(parts[0])
            continue
        pid = ln.player_id
        elig = ({x.strip() for x in parts[2].split(",") if x.strip()}
                if len(parts) > 2 and parts[2] else nhl_eligibility(ln.position))
        if comment.strip():
            pl.tags[pid] = comment.strip()
        status = parts[3].split() if len(parts) > 3 else []
        if status and status[0].upper() in INACTIVE_MARKERS:
            pl.inactive[pid] = elig
            pl.inactive_kind[pid] = "IR" if status[0].upper().startswith("IR") else "OUT"
            continue
        if len(status) > 1 and status[0].upper() == "W":
            pl.available_from[pid] = status[1]
        pl.active[pid] = elig
    return pl


def parse_players(ctx: Context, text: str, inactive: set[int] | None = None,
                  available_from: dict[int, str] | None = None,
                  ) -> tuple[dict[int, set[str]], list[str]]:
    """{player_id: eligible positions} for the ACTIVE players in list text,
    plus unmatched names (see parse_list). IR / OUT players are left out and
    added to ``inactive``; waiver dates go to ``available_from``."""
    pl = parse_list(ctx, text)
    if inactive is not None:
        inactive.update(pl.inactive)
    if available_from is not None:
        available_from.update(pl.available_from)
    return pl.active, pl.missing


def skater_slots(ctx: Context) -> dict[str, int]:
    slots = {k: v for k, v in ctx.league.roster_slots.items()
             if k not in ("BENCH", "G", "IR", "IR+")}
    slots["Util"] = slots.pop("UTIL", 0)
    return slots


def dates_from(start: str, days: int) -> list[str]:
    s = datetime.date.fromisoformat(start)
    return [(s + datetime.timedelta(days=i)).isoformat() for i in range(days)]


def game_values(ctx: Context, pid: int, dates: list[str],
                locked: set[str] = frozenset()) -> dict[str, float]:
    """Expected value of each of ``pid``'s games in ``dates``. Games on the
    first date for teams in ``locked`` (already started) are dropped."""
    ln = ctx.lines[pid]
    out = {}
    for d, opp in ctx.team_games(ln.team, dates[0], dates[-1]):
        if d == dates[0] and ln.team in locked:
            continue
        out[d] = ctx.scorer.value_stats(pid, ln.position,
                                        expected_line(ln, ctx.factors.get(opp)))
    return out


def _player_row(ctx: Context, pid: int, elig: set[str], start: str,
                days: int) -> dict:
    ln = ctx.lines[pid]
    f = form(ctx, ln)
    h = hold_check(ctx, pid, start, days)
    toi_drop = (ln.toi_pg_prior - ln.toi_pg_obs
                if ln.toi_pg_prior and ln.toi_pg_obs else None)
    flags = []
    small = not ln.is_goalie and ln.gp < MIN_GP_FLAGS
    if small:
        flags.append(f"small sample ({ln.gp} GP)")
    if not small and toi_drop is not None and toi_drop >= TOI_FLAG_SEC:
        flags.append("ice time down")
    if (not small and f["vs_prior_pct"] is not None
            and f["vs_prior_pct"] <= -0.15):
        flags.append("producing below projection")
    if h["verdict"] != "HOLD":
        flags.append(h["verdict"].lower())
    return {
        "id": pid, "name": ln.name, "team": ln.team, "pos": ln.position,
        "elig": sorted(elig), "gp": ln.gp,
        "prior": f["prior"], "now": f["observed"], "blended": f["blended"],
        "vs_prior_pct": f["vs_prior_pct"],
        "toi_prior": ln.toi_pg_prior, "toi_now": ln.toi_pg_obs,
        "rank": h["group_rank"], "pool": h["pool_size"],
        "ros_vs_repl": h["ros_vs_replacement"],
        "window_games": len(h["window"]["games"]),
        "verdict": h["verdict"], "flags": flags,
    }


def audit(ctx: Context, roster: dict[int, set[str]], start: str,
          days: int = 9, inactive: set[int] = frozenset()) -> dict:
    """Per-player audit plus how much each adds to your nightly lineup.
    ``inactive`` (IR) players are listed as protected holds and kept out of
    the lineup simulation."""
    dates = dates_from(start, days)
    slots = skater_slots(ctx)
    gv = {p: game_values(ctx, p, dates) for p in roster}

    def per_night(r: dict[int, set[str]]):
        return [best_lineup([(p, r[p], gv[p].get(d, 0.0)) for p in r
                             if gv[p].get(d, 0.0) > 0], slots) for d in dates]

    nights = per_night(roster)
    full = sum(t for t, _ in nights)
    rows = []
    for p, elig in roster.items():
        without = per_night({q: e for q, e in roster.items() if q != p})
        row = _player_row(ctx, p, elig, start, days)
        row["lineup_adds"] = full - sum(t for t, _ in without)
        row["starts"] = sum(1 for _, a in nights if p in a)
        rows.append(row)
    rows.sort(key=lambda r: r["blended"])
    for p in sorted(inactive, key=lambda q: -ctx.scorer.per_game.get(q, 0)):
        if p not in ctx.lines:
            continue
        row = _player_row(ctx, p, nhl_eligibility(ctx.lines[p].position),
                          start, days)
        row.update(verdict="IR", lineup_adds=0.0, starts=0, window_games=0,
                   flags=["injured reserve: hold"]
                   + [f for f in row["flags"] if f.startswith("small")])
        rows.append(row)
    night_rows = []
    for d, (total, assign) in zip(dates, nights):
        used: dict[str, int] = {}
        for s in assign.values():
            used[s] = used.get(s, 0) + 1
        playing = [p for p in roster if gv[p].get(d, 0.0) > 0]
        night_rows.append({
            "date": d, "playing": len(playing), "lineup": total,
            "open": {s: n - used.get(s, 0) for s, n in slots.items()
                     if n - used.get(s, 0) > 0},
            "benched": [ctx.lines[p].name for p in playing if p not in assign]})
    return {"players": rows, "nights": night_rows,
            "replacement": ctx.scorer.replacement}


def free_agent_table(ctx: Context, fas: dict[int, set[str]] | PlayerList,
                     start: str, days: int = 9,
                     available_from: dict[int, str] | None = None) -> list[dict]:
    """Every available player, best blended value first. Pass a PlayerList
    to include injured (OUT) free agents -- listed with ``status`` "OUT" so
    you can stash one in an IR slot; they never start in plans or streams.
    Each row has ``status`` (FA / W / OUT), ``waiver_until`` and ``tag``."""
    if isinstance(fas, PlayerList):
        pl = fas
    else:
        pl = PlayerList(active=dict(fas), available_from=dict(available_from or {}))
    rows = []
    for p, e in [*pl.active.items(), *pl.inactive.items()]:
        if p not in ctx.lines:
            continue
        row = _player_row(ctx, p, e, start, days)
        row.update(status=pl.status(p), waiver_until=pl.available_from.get(p),
                   tag=pl.tags.get(p))
        if row["status"] == "OUT":
            # can't play now: no keep/drop verdict. Worth an IR slot if he's
            # better than replacement once healthy.
            grp_repl = ctx.scorer.replacement[
                "G" if ctx.lines[p].is_goalie else
                "D" if ctx.lines[p].position == "D" else "F"]
            row["verdict"] = "IR STASH" if row["blended"] > grp_repl else "OUT"
            row["flags"] = [f"injured ({row['tag'] or 'out'})"] + [
                f for f in row["flags"] if f.startswith("small")]
        rows.append(row)
    rows.sort(key=lambda r: -r["blended"])
    return rows


def roster_plan(ctx: Context, roster: dict[int, set[str]],
                cands: dict[int, set[str]], hold: int, start: str, days: int,
                adds: list[int], locked: set[str] = frozenset(),
                available_from: dict[int, str] | None = None) -> dict:
    """Roster-aware add/drop plan for ``hold``'s roster spot."""
    dates = dates_from(start, days)
    roster = dict(roster)
    hold_elig = (cands.get(hold) or roster.get(hold)
                 or nhl_eligibility(ctx.lines[hold].position))
    roster.pop(hold, None)
    cands = dict(cands)
    cands[hold] = hold_elig
    games = {p: game_values(ctx, p, dates) for p in roster}
    games.update({p: game_values(ctx, p, dates, locked) for p in cands})
    mv = marginal_values(roster, cands, games, dates, skater_slots(ctx))
    weeks = sorted({week_key(d) for d in dates})
    budget = {w: (adds[i] if i < len(adds) else adds[-1])
              for i, w in enumerate(weeks)}
    # waiver players can't be added before they clear
    pl = plan_slot(hold, mv, dates, budget, available_from=available_from)

    def who(p):
        ln = ctx.lines[p]
        return {"id": p, "name": ln.name, "team": ln.team,
                "elig": sorted(cands.get(p) or roster.get(p) or [])}

    return {
        "hold": who(hold), "dates": dates, "budget": budget,
        "keep_value": sum(mv[hold].values()), "plan_value": pl.total,
        "steps": [{"date": s.date, "drop": who(s.drop), "add": who(s.add)}
                  for s in pl.steps],
        "nightly": [{"date": d, "holder": who(pl.holder_by_date[d]),
                     "value": mv[pl.holder_by_date[d]].get(d, 0.0)}
                    for d in dates],
        "singles": [dict(who(p), adds=sum(mv[p].values()), nights=list(mv[p]))
                    for p in sorted(mv, key=lambda p: -sum(mv[p].values()))
                    if p != hold][:12],
    }
