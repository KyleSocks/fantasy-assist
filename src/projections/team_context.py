"""Team context for goalie projections.

A goalie's wins, saves and goals-against depend on the team in front of
him. For every team this module projects, for the target season:

- shots against per game: recency-weighted history, regressed toward the
  league mean by ``team_sa_regress`` (the share of a team's deviation from
  league average that carries into next season);
- win rate per game, a blend (``team_hist_weight``) of
    * history: recency-weighted wins / games, regressed by team_win_regress
    * roster: a Pythagorean expectation GF^x / (GF^x + GA^x), where GF is
      the league's goals per game scaled by the team's projected skater
      goals (its current roster's projections, so offseason moves count)
      and GA = projected shots against x (1 - start-weighted projected SV%).

History comes from goalie_seasons (wins, shots and goals against summed by
team: every game's win goes to exactly one goalie) and skater_seasons
(goals for). A traded player's season row sits under his final team, a
small known distortion.
"""
from __future__ import annotations

import sqlite3

from .common import WEIGHTS

# Relocations: history under the old abbreviation belongs to the new team.
TEAM_ALIASES = {"ARI": "UTA"}


def _canon(team: str | None) -> str | None:
    return TEAM_ALIASES.get(team, team) if team else team


def team_history(conn: sqlite3.Connection,
                 season_ids: list[int]) -> dict[int, dict[str, dict]]:
    """{season: {team: {games, wins, sa, ga, gf}}} for regular seasons."""
    out: dict[int, dict[str, dict]] = {}
    for sid in season_ids:
        teams: dict[str, dict] = {}

        def bucket(t):
            return teams.setdefault(_canon(t), {"games": 0, "wins": 0.0,
                                                "sa": 0.0, "ga": 0.0, "gf": 0.0})
        for h, a in conn.execute(
                "SELECT home_abbrev, away_abbrev FROM schedule"
                " WHERE season_id = ? AND game_type = 2", (sid,)):
            bucket(h)["games"] += 1
            bucket(a)["games"] += 1
        for team, w, sa, ga in conn.execute(
                "SELECT team_abbrev, SUM(wins), SUM(shots_against),"
                " SUM(goals_against) FROM goalie_seasons WHERE season_id = ?"
                " GROUP BY team_abbrev", (sid,)):
            b = bucket(team)
            b["wins"] += w or 0
            b["sa"] += sa or 0
            b["ga"] += ga or 0
        try:
            gf_rows = conn.execute(
                "SELECT team_abbrev, SUM(goals) FROM skater_seasons"
                " WHERE season_id = ? GROUP BY team_abbrev", (sid,)).fetchall()
        except sqlite3.OperationalError:       # goalie-only test databases
            gf_rows = []
        for team, gf in gf_rows:
            bucket(team)["gf"] += gf or 0
        out[sid] = {t: b for t, b in teams.items() if t and b["games"] > 0}
    return out


def _league(hist: dict[int, dict[str, dict]]) -> dict[str, float]:
    games = sum(b["games"] for s in hist.values() for b in s.values())
    if games <= 0:
        return {"sa_pg": 0.0, "gf_pg": 0.0, "ga_pg": 0.0, "win_pg": 0.5}
    tot = {m: sum(b[m] for s in hist.values() for b in s.values())
           for m in ("sa", "gf", "ga", "wins")}
    return {"sa_pg": tot["sa"] / games, "gf_pg": tot["gf"] / games,
            "ga_pg": tot["ga"] / games, "win_pg": tot["wins"] / games}


def _weighted_team(hist: dict[int, dict[str, dict]], team: str,
                   metric: str) -> float | None:
    """5/3/2 recency-weighted per-game metric for one team (None if the
    team has no history)."""
    seasons = sorted(hist, reverse=True)
    num = den = 0.0
    for w, sid in zip(WEIGHTS, seasons):
        b = hist[sid].get(team)
        if b and b["games"] > 0:
            num += w * b[metric] / b["games"]
            den += w
    return num / den if den > 0 else None


def _regress(value: float | None, mean: float, keep: float) -> float:
    return mean if value is None else mean + keep * (value - mean)


def pythag(gf: float, ga: float, x: float) -> float:
    """Pythagorean win expectation; 0.5 when either side is zero."""
    if gf <= 0 or ga <= 0:
        return 0.5
    return gf ** x / (gf ** x + ga ** x)


def project_teams(conn: sqlite3.Connection, season_ids: list[int], cfg: dict,
                  team_goals: dict[str, float] | None = None,
                  team_sv: dict[str, float] | None = None,
                  hist: dict | None = None) -> dict[str, dict]:
    """Projected per-game context for every team with history or a roster.

    ``team_goals``: projected skater goals by (current) team, from the
    skater projections. ``team_sv``: start-weighted projected SV% by team.
    Either missing for a team -> that side of the Pythagorean falls back to
    the team's regressed history.
    """
    if hist is None:
        hist = team_history(conn, season_ids)
    lg = _league(hist)
    keep_sa = float(cfg.get("team_sa_regress", 0.6))
    keep_win = float(cfg.get("team_win_regress", 0.6))
    keep_gf = float(cfg.get("team_gf_regress", 0.6))
    w_hist = float(cfg.get("team_hist_weight", 0.5))
    x = float(cfg.get("pythag_exponent", 2.0))
    teams = {t for s in hist.values() for t in s}
    teams |= {_canon(t) for t in (team_goals or {})}
    teams |= {_canon(t) for t in (team_sv or {})}
    goals = {}
    for t, g in (team_goals or {}).items():
        goals[_canon(t)] = goals.get(_canon(t), 0.0) + g
    mean_goals = (sum(goals.values()) / len(goals)) if goals else 0.0
    out: dict[str, dict] = {}
    for t in sorted(tm for tm in teams if tm):
        sa_pg = _regress(_weighted_team(hist, t, "sa"), lg["sa_pg"], keep_sa)
        win_hist = _regress(_weighted_team(hist, t, "wins"), lg["win_pg"],
                            keep_win)
        if t in goals and mean_goals > 0:
            gf_pg = lg["gf_pg"] * goals[t] / mean_goals
        else:
            gf_pg = _regress(_weighted_team(hist, t, "gf"), lg["gf_pg"], keep_gf)
        sv = (team_sv or {}).get(t)
        if sv is not None and sa_pg > 0:
            ga_pg = sa_pg * (1.0 - sv)
        else:
            ga_pg = _regress(_weighted_team(hist, t, "ga"), lg["ga_pg"], keep_win)
        win_roster = pythag(gf_pg, ga_pg, x)
        out[t] = {"sa_pg": sa_pg, "gf_pg": gf_pg, "ga_pg": ga_pg,
                  "win_hist": win_hist, "win_roster": win_roster,
                  "win_pg": w_hist * win_hist + (1 - w_hist) * win_roster}
    out["_league"] = lg
    return out


def team_lookup(ctx: dict[str, dict], team: str | None) -> dict:
    """Context for a team, the league average for an unknown one."""
    t = _canon(team)
    if t and t in ctx:
        return ctx[t]
    lg = ctx.get("_league", {})
    return {"sa_pg": lg.get("sa_pg", 0.0), "gf_pg": lg.get("gf_pg", 0.0),
            "ga_pg": lg.get("ga_pg", 0.0), "win_hist": lg.get("win_pg", 0.5),
            "win_roster": 0.5, "win_pg": lg.get("win_pg", 0.5)}
