"""Answers for the mid-season assistant: single-game streams, hold/drop
checks over a schedule window, and the team schedule grid."""
from __future__ import annotations

import contextlib
import datetime
import io
import sqlite3
import unicodedata
from dataclasses import dataclass

from src.projections.projections import project_all
from src.valuation.league import LeagueConfig
from src.valuation.valuation import value_group

from .model import (
    Line,
    build_lines,
    load_current,
    load_inseason_config,
    opponent_factors,
    priors_from_projections,
    season_and_as_of,
    team_games_played,
)
from .scoring import GameScorer


@dataclass
class Context:
    league: LeagueConfig
    icfg: dict
    season: int
    as_of: str
    lines: dict[int, Line]
    factors: dict[str, dict[str, float]]
    scorer: GameScorer
    games: list[tuple[str, str, str]]   # (date, home, away), regular season

    def team_games(self, team: str | None, start: str, end: str,
                   ) -> list[tuple[str, str]]:
        """(date, opponent) for ``team`` with start <= date <= end."""
        if not team:
            return []
        out = []
        for d, h, a in self.games:
            if start <= d <= end and team in (h, a):
                out.append((d, a if h == team else h))
        return out

    def games_on(self, date: str) -> int:
        return sum(1 for d, _, _ in self.games if d == date)

    def avg_team_games(self, start: str, end: str) -> float:
        teams = {t for _, h, a in self.games for t in (h, a)}
        n = sum(1 for d, _, _ in self.games if start <= d <= end)
        return 2 * n / len(teams) if teams else 0.0

    def season_end(self) -> str:
        return max((d for d, _, _ in self.games), default=self.as_of)


def build_context(db_path: str, league: LeagueConfig,
                  priors: dict[int, dict] | None = None,
                  icfg: dict | None = None) -> Context:
    icfg = icfg or load_inseason_config()
    conn = sqlite3.connect(db_path)
    try:
        season, as_of = season_and_as_of(conn)
        if priors is None:
            # project_all prints the goalie allocation report; not wanted here.
            with contextlib.redirect_stdout(io.StringIO()):
                priors = priors_from_projections(project_all(season, db_path))
        lines = build_lines(conn, season, priors, icfg)
        skaters, goalies = load_current(conn, season)
        factors = opponent_factors(skaters, goalies, team_games_played(skaters),
                                   icfg["opponent_k_games"])
        games = [tuple(r) for r in conn.execute(
            "SELECT game_date, home_abbrev, away_abbrev FROM schedule"
            " WHERE season_id=? AND game_type=2 ORDER BY game_date, game_id",
            (season,))]
    finally:
        conn.close()
    scorer = GameScorer(league, {pid: (ln.position, expected_line(ln, None))
                                 for pid, ln in lines.items()})
    return Context(league, icfg, season, as_of, lines, factors, scorer, games)


def expected_line(line: Line, opp: dict[str, float] | None) -> dict[str, float]:
    """Expected stats for one TEAM game (goalies weighted by start share).

    ``opp`` is the opponent's factor dict (None = league-average opponent).
    Skaters: goals/assists/PPP scale with opponent goals-against, shots with
    shots-against. Goalies: GA scales with opponent goals-for, saves with
    opponent shots-for; wins, shutouts and SV% are not opponent-adjusted.
    """
    f = opp or {"ga": 1.0, "sa": 1.0, "gf": 1.0, "sf": 1.0}
    b = line.blended
    if line.is_goalie:
        s = line.start_share
        return {"wins": s * b["wins"], "saves": s * b["saves"] * f["sf"],
                "goals_against": s * b["goals_against"] * f["gf"],
                "shutouts": s * b["shutouts"], "games_started": s,
                "save_pct": b["save_pct"]}
    return {"goals": b["goals"] * f["ga"], "assists": b["assists"] * f["ga"],
            "pp_points": b["pp_points"] * f["ga"], "shots": b["shots"] * f["sa"],
            "hits": b["hits"], "blocks": b["blocks"]}


def _value(ctx: Context, line: Line, stats: dict[str, float]) -> float:
    return ctx.scorer.value_stats(line.player_id, line.position, stats)


def form(ctx: Context, line: Line) -> dict[str, float | None]:
    """Per-game value: preseason prior vs season-to-date vs blended."""
    if line.is_goalie:
        def g(rates, share):
            return dict({k: share * v for k, v in rates.items()
                         if k != "save_pct"},
                        games_started=share, save_pct=rates["save_pct"])
        prior = _value(ctx, line, g(line.prior, line.start_share_prior))
        obs = (_value(ctx, line, g(line.observed, line.start_share_obs))
               if line.gp else None)
    else:
        prior = _value(ctx, line, line.prior)
        obs = _value(ctx, line, line.observed) if line.gp else None
    blended = ctx.scorer.per_game[line.player_id]
    diff = (obs - prior) if obs is not None else None
    # A % change is only meaningful against a positive, natural-zero value
    # (fantasy points). Category z-values can sit near 0, so report the
    # difference instead.
    pct = (diff / prior if diff is not None and ctx.scorer.points and prior > 0
           else None)
    return {"prior": prior, "observed": obs, "blended": blended,
            "vs_prior_pct": pct, "vs_prior_diff": diff}


def player_window(ctx: Context, pid: int, start: str, end: str) -> dict:
    line = ctx.lines[pid]
    off = ctx.icfg["off_night_max_games"]
    games = []
    for d, opp in ctx.team_games(line.team, start, end):
        v = _value(ctx, line, expected_line(line, ctx.factors.get(opp)))
        games.append({"date": d, "opp": opp, "value": v,
                      "off_night": ctx.games_on(d) <= off})
    return {"games": games, "value": sum(g["value"] for g in games),
            "off_nights": sum(g["off_night"] for g in games)}


def _end(start: str, days: int) -> str:
    return (datetime.date.fromisoformat(start)
            + datetime.timedelta(days=days - 1)).isoformat()


def streams(ctx: Context, date: str, top: int = 25, group: str | None = None,
            likely_available: bool = True) -> list[dict]:
    """Best single-game plays on ``date`` (opponent-adjusted).

    ``likely_available`` keeps only players ranked outside the league's
    rosterable pool by blended per-game value -- an approximation of the
    waiver wire, since league rosters aren't ingested.
    """
    sc = ctx.scorer
    out = []
    for pid, line in ctx.lines.items():
        grp = value_group(line.position)
        if group and grp != group:
            continue
        if likely_available and sc.group_rank[pid] <= sc.pool_sizes[grp]:
            continue
        g = ctx.team_games(line.team, date, date)
        if not g:
            continue
        opp = g[0][1]
        out.append({
            "player_id": pid, "name": line.name, "pos": line.position,
            "team": line.team, "opp": opp, "gp": line.gp,
            "value": _value(ctx, line, expected_line(line, ctx.factors.get(opp))),
            "per_game": sc.per_game[pid], "group_rank": sc.group_rank[pid],
            "pool_size": sc.pool_sizes[grp],
            "replacement": sc.replacement[grp],
            "start_share": line.start_share if line.is_goalie else None,
            **{k: v for k, v in form(ctx, line).items()
               if k in ("vs_prior_pct", "vs_prior_diff")}})
    out.sort(key=lambda r: (-r["value"], r["player_id"]))
    return out[:top]


def hold_check(ctx: Context, pid: int, start: str, days: int) -> dict:
    """Short-window and rest-of-season value vs a replacement-level player
    on a league-average schedule, plus a verdict."""
    line = ctx.lines[pid]
    grp = value_group(line.position)
    sc = ctx.scorer
    repl = sc.replacement[grp]
    end = _end(start, days)
    win = player_window(ctx, pid, start, end)
    ros = player_window(ctx, pid, start, ctx.season_end())
    avg_win = ctx.avg_team_games(start, end)
    avg_ros = ctx.avg_team_games(start, ctx.season_end())
    vpg = sc.per_game[pid]
    margin = ctx.icfg["hold_margin"]
    if vpg >= repl + margin * abs(repl) and vpg > 0:
        verdict = "HOLD"
    elif vpg < repl:
        verdict = "DROP CANDIDATE"
    else:
        verdict = "BORDERLINE"
    return {
        "player_id": pid, "name": line.name, "pos": line.position,
        "team": line.team, "gp": line.gp, "line": line,
        "form": form(ctx, line), "replacement_pg": repl,
        "group_rank": sc.group_rank[pid], "pool_size": sc.pool_sizes[grp],
        "start": start, "end": end, "window": win, "avg_window_games": avg_win,
        "window_vs_replacement": win["value"] - repl * avg_win,
        "ros_games": len(ros["games"]), "avg_ros_games": avg_ros,
        "ros_vs_replacement": ros["value"] - repl * avg_ros,
        "verdict": verdict}


def schedule_grid(ctx: Context, start: str, days: int) -> list[dict]:
    end = _end(start, days)
    off = ctx.icfg["off_night_max_games"]
    teams = sorted({t for _, h, a in ctx.games for t in (h, a)})
    rows = []
    for t in teams:
        g = ctx.team_games(t, start, end)
        rows.append({
            "team": t, "games": len(g),
            "off_nights": sum(1 for d, _ in g if ctx.games_on(d) <= off),
            "opponents": [o for _, o in g],
            "opp_ga_factor": (sum(ctx.factors.get(o, {}).get("ga", 1.0)
                                  for _, o in g) / len(g)) if g else None})
    rows.sort(key=lambda r: (-r["games"], -r["off_nights"], r["team"]))
    return rows


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c)).lower()


def find_players(ctx: Context, query: str) -> list[Line]:
    """Accent/case-insensitive substring match on full name."""
    q = _fold(query.strip())
    exact = [ln for ln in ctx.lines.values() if _fold(ln.name) == q]
    if exact:
        return exact
    return sorted((ln for ln in ctx.lines.values() if q in _fold(ln.name)),
                  key=lambda ln: -ctx.scorer.per_game[ln.player_id])
