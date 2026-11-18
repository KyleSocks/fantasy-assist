"""Shooting and save luck: who is running hot or cold on percentages.

Skaters: this season's goals against the goals his shots "should" have
produced at his usual shooting %. The usual % is his last three seasons,
regressed toward his position's league rate by ``K_SHOTS`` shots, so a
player with little history leans on the league rate:

    usual = (prior goals + K_SHOTS * pos_rate) / (prior shots + K_SHOTS)
    expected goals = shots * usual
    luck (goals) = goals - expected goals
    z = luck / sqrt(shots * usual * (1 - usual))      (binomial spread)

Goalies: the same on saves. Usual SV% = last three seasons regressed toward
the league rate by ``K_SHOTS_AGAINST``; luck in goals = expected goals
against - goals against (positive = he's allowed fewer than usual: hot).

``status``: "hot" when z >= Z_FLAG and luck >= MIN_GOALS, "cold" when
z <= -Z_FLAG and luck <= -MIN_GOALS, else None. Hot = sell-high / don't pay
for it; cold = buy-low / don't drop for it (src.inseason.roster uses it to
keep an unlucky player off the drop list).
"""
from __future__ import annotations

import math
import sqlite3

K_SHOTS = 300.0
K_SHOTS_AGAINST = 3000.0
Z_FLAG = 1.5
MIN_GOALS = 2.0
PRIOR_SEASONS = 3


def _rate(made: float, tries: float, fallback: float) -> float:
    return made / tries if tries > 0 else fallback


def skater_luck(goals: int, shots: int, prior_goals: float, prior_shots: float,
                pos_rate: float) -> dict:
    """Luck numbers for one skater (see the module docstring)."""
    usual = (prior_goals + K_SHOTS * pos_rate) / (prior_shots + K_SHOTS)
    xg = shots * usual
    luck = goals - xg
    sd = math.sqrt(shots * usual * (1 - usual)) if shots else 0.0
    z = luck / sd if sd else 0.0
    return {"sh_pct": _rate(goals, shots, 0.0), "usual_pct": usual,
            "goals": goals, "shots": shots, "expected": xg, "luck": luck,
            "z": z, "status": _status(z, luck)}


def goalie_luck(saves: int, shots_against: int, prior_saves: float,
                prior_sa: float, league_rate: float) -> dict:
    usual = (prior_saves + K_SHOTS_AGAINST * league_rate) / (prior_sa + K_SHOTS_AGAINST)
    ga = shots_against - saves
    xga = shots_against * (1 - usual)
    luck = xga - ga                       # goals saved above his usual
    sd = math.sqrt(shots_against * usual * (1 - usual)) if shots_against else 0.0
    z = luck / sd if sd else 0.0
    return {"sv_pct": _rate(saves, shots_against, 0.0), "usual_pct": usual,
            "goals_against": ga, "shots_against": shots_against,
            "expected": xga, "luck": luck, "z": z, "status": _status(z, luck)}


def _status(z: float, luck: float) -> str | None:
    if z >= Z_FLAG and luck >= MIN_GOALS:
        return "hot"
    if z <= -Z_FLAG and luck <= -MIN_GOALS:
        return "cold"
    return None


def load_luck(conn: sqlite3.Connection, season: int) -> dict[int, dict]:
    """{player_id: luck dict} for every player with stats this season."""
    pos = dict(conn.execute("SELECT player_id, position FROM players"))
    prior = {pid: (g or 0, s or 0) for pid, g, s in conn.execute(
        "SELECT player_id, SUM(goals), SUM(shots) FROM skater_seasons"
        " WHERE season_id < ? AND season_id >= ? GROUP BY player_id",
        (season, season - 10001 * PRIOR_SEASONS))}
    cur = list(conn.execute(
        "SELECT player_id, goals, shots FROM skater_current WHERE season_id=?",
        (season,)))
    # position rates from the prior seasons (forwards / defense)
    tot = {"F": [0, 0], "D": [0, 0]}
    for pid, (g, s) in prior.items():
        grp = "D" if pos.get(pid) == "D" else "F"
        tot[grp][0] += g
        tot[grp][1] += s
    pos_rate = {k: _rate(g, s, 0.10 if k == "F" else 0.05) for k, (g, s) in tot.items()}
    out: dict[int, dict] = {}
    for pid, g, s in cur:
        if not s:
            continue
        pg, ps = prior.get(pid, (0, 0))
        grp = "D" if pos.get(pid) == "D" else "F"
        out[pid] = skater_luck(g or 0, s, pg, ps, pos_rate[grp])

    gprior = {pid: (sv or 0, sa or 0) for pid, sv, sa in conn.execute(
        "SELECT player_id, SUM(saves), SUM(shots_against) FROM goalie_seasons"
        " WHERE season_id < ? AND season_id >= ? GROUP BY player_id",
        (season, season - 10001 * PRIOR_SEASONS))}
    sv_all = sum(v[0] for v in gprior.values())
    sa_all = sum(v[1] for v in gprior.values())
    league = _rate(sv_all, sa_all, 0.900)
    for pid, sv, sa in conn.execute(
            "SELECT player_id, saves, shots_against FROM goalie_current"
            " WHERE season_id=?", (season,)):
        if not sa:
            continue
        psv, psa = gprior.get(pid, (0, 0))
        out[pid] = goalie_luck(sv or 0, sa, psv, psa, league)
    return out


def describe(l: dict | None) -> str | None:
    """One-line flag for a player row, or None when he's not flagged."""
    if not l or not l.get("status"):
        return None
    hot = l["status"] == "hot"
    if "sh_pct" in l:
        return (f"{'shooting hot' if hot else 'shooting cold'}: "
                f"{100 * l['sh_pct']:.1f}% vs {100 * l['usual_pct']:.1f}% usual")
    return (f"{'saving hot' if hot else 'saving cold'}: "
            f".{round(1000 * l['sv_pct']):03d} vs .{round(1000 * l['usual_pct']):03d} usual")
