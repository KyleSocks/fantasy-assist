"""Answers for the mid-season assistant: single-game streams, hold/drop
checks over a schedule window, and the team schedule grid."""
from __future__ import annotations

import datetime
import sqlite3
import unicodedata
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from src.cache import project_all_cached
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

    def _index(self) -> "_ScheduleIndex":
        """Per-date counts and per-team game lists, built once per
        ``games`` list (rebuilt if ``games`` is reassigned). Every lookup
        below used to scan the whole season's schedule."""
        idx = self.__dict__.get("_sched_idx")
        if idx is None or idx.games is not self.games:
            idx = _ScheduleIndex(self.games)
            self.__dict__["_sched_idx"] = idx
        return idx

    def team_games(self, team: str | None, start: str, end: str,
                   ) -> list[tuple[str, str]]:
        """(date, opponent) for ``team`` with start <= date <= end."""
        if not team:
            return []
        idx = self._index()
        dates, opps = idx.by_team.get(team, ((), ()))
        lo, hi = bisect_left(dates, start), bisect_right(dates, end)
        return list(zip(dates[lo:hi], opps[lo:hi]))

    def games_on(self, date: str) -> int:
        return self._index().per_date.get(date, 0)

    def avg_team_games(self, start: str, end: str) -> float:
        idx = self._index()
        n = bisect_right(idx.dates, end) - bisect_left(idx.dates, start)
        return 2 * n / len(idx.by_team) if idx.by_team else 0.0

    def schedule_warning(self) -> str | None:
        """Non-None when some date has more games than 32 teams can play
        (a mis-dated schedule ingest)."""
        per: dict[str, int] = {}
        for d, _, _ in self.games:
            per[d] = per.get(d, 0) + 1
        bad = sorted(d for d, n in per.items() if n > 16)
        if not bad:
            return None
        return (f"schedule looks wrong: {per[bad[0]]} games on {bad[0]}"
                f" ({len(bad)} date(s) over 16). Press Refresh data / re-run"
                " uv run python -m src.inseason.ingest")

    def season_end(self) -> str:
        """Last NHL regular-season date."""
        dates = self._index().dates
        return dates[-1] if dates else self.as_of

    # ── the fantasy season: what "rest of season" means ──────────────────
    # Rest-of-season values run to the end of the league's fantasy playoffs
    # (league yaml playoff_window), not the NHL's last day, and a playoff
    # game counts ``playoff_game_weight`` times (inseason.yaml): those weeks
    # decide the title. Without a playoff_window: NHL season end, weight 1.

    def fantasy_end(self) -> str:
        end = self.season_end()
        po = self.league.playoff_window
        return min(end, po[1]) if po else end

    def game_weight(self, date: str) -> float:
        if date > self.fantasy_end():
            return 0.0
        po = self.league.playoff_window
        if po and po[0] <= date <= po[1]:
            return float(self.icfg.get("playoff_game_weight", 1.0))
        return 1.0

    def playoff_games(self, team: str | None) -> int | None:
        """Games ``team`` plays in the fantasy playoffs (None: no window)."""
        po = self.league.playoff_window
        return len(self.team_games(team, po[0], po[1])) if po else None

    def weighted_team_games(self, start: str, end: str) -> float:
        """avg_team_games with each game weighted by game_weight."""
        idx = self._index()
        if not idx.by_team:
            return 0.0
        lo, hi = bisect_left(idx.dates, start), bisect_right(idx.dates, end)
        return 2 * sum(self.game_weight(d) for d in idx.dates[lo:hi]) / len(idx.by_team)


class _ScheduleIndex:
    """Sorted views of a regular-season schedule for O(log n) lookups."""

    def __init__(self, games: list[tuple[str, str, str]]):
        self.games = games
        self.dates = sorted(d for d, _, _ in games)       # one per game
        self.per_date: dict[str, int] = {}
        per_team: dict[str, list[tuple[str, str]]] = {}
        for d, h, a in games:
            self.per_date[d] = self.per_date.get(d, 0) + 1
            per_team.setdefault(h, []).append((d, a))
            per_team.setdefault(a, []).append((d, h))
        # stable sort keeps same-day games in schedule (game_id) order
        self.by_team = {t: (tuple(d for d, _ in g), tuple(o for _, o in g))
                        for t, g in ((t, sorted(g, key=lambda x: x[0]))
                                     for t, g in per_team.items())}


def build_context(db_path: str, league: LeagueConfig,
                  priors: dict[int, dict] | None = None,
                  icfg: dict | None = None) -> Context:
    icfg = icfg or load_inseason_config()
    conn = sqlite3.connect(db_path)
    try:
        season, as_of = season_and_as_of(conn)
        if priors is None:
            # include_inactive: free agents off NHL rosters (call-ups,
            # waiver players) get their own projection as the prior; the
            # league-average fallback made one of them "the best free
            # agent" and set replacement level far too high.
            priors = priors_from_projections(
                project_all_cached(season, db_path, include_inactive=True))
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


def expected_line(line: Line, opp: dict[str, float] | None,
                  assume_start: bool = False) -> dict[str, float]:
    """Expected stats for one TEAM game, weighted by the share of games he
    starts (goalies) or plays (skaters); ``assume_start``: he's confirmed in
    tonight's lineup (single-game streams), share 1.

    ``opp`` is the opponent's factor dict (None = league-average opponent).
    Skaters: goals/assists/PPP scale with opponent goals-against, shots with
    shots-against. Goalies: GA scales with opponent goals-for, saves with
    opponent shots-for; wins, shutouts and SV% are not opponent-adjusted.
    """
    f = opp or {"ga": 1.0, "sa": 1.0, "gf": 1.0, "sf": 1.0}
    b = line.blended
    if line.is_goalie:
        # assume_start: value of a game he starts (single-game streams --
        # you'd only stream a confirmed starter); otherwise weight by the
        # share of team games he starts.
        s = 1.0 if assume_start else line.start_share
        return {"wins": s * b["wins"], "saves": s * b["saves"] * f["sf"],
                "goals_against": s * b["goals_against"] * f["gf"],
                "shutouts": s * b["shutouts"], "games_started": s,
                "save_pct": b["save_pct"]}
    s = 1.0 if assume_start else line.play_share
    return {"goals": s * b["goals"] * f["ga"], "assists": s * b["assists"] * f["ga"],
            "pp_points": s * b["pp_points"] * f["ga"], "shots": s * b["shots"] * f["sa"],
            "hits": s * b["hits"], "blocks": s * b["blocks"],
            "sh_points": s * b.get("sh_points", 0.0)}


def _value(ctx: Context, line: Line, stats: dict[str, float]) -> float:
    return ctx.scorer.value_stats(line.player_id, line.position, stats)


def form(ctx: Context, line: Line) -> dict[str, float | None]:
    """Per-game value: preseason prior vs season-to-date vs blended.

    Goalies: per TEAM game (each weighted by its start share). Skaters: per
    game PLAYED, so an injured-but-productive player isn't shown as
    underperforming; ``avail`` (share of team games he plays) is separate,
    and ``team_game`` = blended x avail is what every decision uses."""
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
    team_game = ctx.scorer.per_game[line.player_id]
    blended = (team_game if line.is_goalie
               else _value(ctx, line, expected_line(line, None, assume_start=True)))
    diff = (obs - prior) if obs is not None else None
    # A % change is only meaningful against a positive, natural-zero value
    # (fantasy points). Category z-values can sit near 0, so report the
    # difference instead.
    pct = (diff / prior if diff is not None and ctx.scorer.points and prior > 0
           else None)
    return {"prior": prior, "observed": obs, "blended": blended,
            "vs_prior_pct": pct, "vs_prior_diff": diff,
            "avail": line.start_share if line.is_goalie else line.play_share,
            "team_game": team_game}


def game_value(ctx: Context, line: Line, opp: str | None) -> float:
    """Value of one team game for ``line`` against ``opp``, memoised per
    context: it depends only on the player and the opponent, and windows
    re-ask it for every game of every candidate."""
    memo = ctx.__dict__.setdefault("_game_value", {})
    key = (line.player_id, opp)
    v = memo.get(key)
    if v is None:
        v = memo[key] = _value(ctx, line,
                               expected_line(line, ctx.factors.get(opp)))
    return v


def player_window(ctx: Context, pid: int, start: str, end: str) -> dict:
    line = ctx.lines[pid]
    off = ctx.icfg["off_night_max_games"]
    games = []
    for d, opp in ctx.team_games(line.team, start, end):
        v = game_value(ctx, line, opp)
        games.append({"date": d, "opp": opp, "value": v,
                      "off_night": ctx.games_on(d) <= off})
    return {"games": games, "value": sum(g["value"] for g in games),
            "off_nights": sum(g["off_night"] for g in games)}


def _fa_rank(ctx: Context, group: str) -> int:
    r = ctx.icfg.get("replacement_fa_rank", 1)
    return max(1, int(r.get(group, 1) if isinstance(r, dict) else r))


def apply_waiver_replacement(ctx: Context, available) -> dict[str, str]:
    """Replacement level = a healthy player you could actually pick up in
    YOUR league, per group (F / D / G), instead of the formula's
    (pool size + 1)th player: the Nth best free agent, N =
    ``replacement_fa_rank`` (the last one if the group has fewer).

    N = 1 (the single best free agent) sets the bar with the hottest start
    on the wire -- the max of a few hundred noisy values -- and by
    definition no free agent can then beat it. ``available``: player ids of
    the healthy free agents / waiver players (data/fa.txt's active list). A
    group with none keeps the formula value. Re-applying starts from the
    formula values. Returns {group: name of the player who sets it}."""
    sc = ctx.scorer
    base = ctx.__dict__.setdefault("_formula_replacement", dict(sc.replacement))
    groups: dict[str, list[int]] = {}
    for p in available:
        ln = ctx.lines.get(p)
        if ln is None or not ln.team:
            continue
        groups.setdefault(value_group(ln.position), []).append(p)
    setter: dict[str, int] = {}
    for g, ps in groups.items():
        ps.sort(key=lambda p: (-sc.per_game[p], p))
        setter[g] = ps[min(_fa_rank(ctx, g), len(ps)) - 1]
    sc.replacement = {g: (sc.per_game[setter[g]] if g in setter else v)
                      for g, v in base.items()}
    ctx.__dict__["replacement_from"] = {g: ctx.lines[p].name
                                        for g, p in setter.items()}
    return ctx.__dict__["replacement_from"]


def ros_value(ctx: Context, pid: int, start: str) -> float:
    """Weighted value of every game from ``start`` to the fantasy end."""
    line = ctx.lines[pid]
    return sum(game_value(ctx, line, opp) * ctx.game_weight(d)
               for d, opp in ctx.team_games(line.team, start, ctx.fantasy_end()))


def ros_over_replacement(ctx: Context, pid: int, start: str) -> float:
    """ros_value minus a replacement player's on a league-average schedule
    (same weights)."""
    repl = ctx.scorer.replacement[value_group(ctx.lines[pid].position)]
    return (ros_value(ctx, pid, start)
            - repl * ctx.weighted_team_games(start, ctx.fantasy_end()))


def _end(start: str, days: int) -> str:
    return (datetime.date.fromisoformat(start)
            + datetime.timedelta(days=days - 1)).isoformat()


def streams(ctx: Context, date: str, top: int = 25, group: str | None = None,
            likely_available: bool = True,
            taken: set[int] | None = None,
            unavailable: set[int] = frozenset()) -> list[dict]:
    """Best single-game plays on ``date`` (opponent-adjusted).

    ``group``: "F", "D", "G", or "S" (all skaters). Goalies are valued as
    if they start (assume_start) but only listed when their blended start
    share is at least ``stream_goalie_min_start_share``.
    ``taken``: player ids rostered in your league -- excluded when given.
    Otherwise ``likely_available`` keeps only players ranked outside the
    league's rosterable pool by blended per-game value, an approximation of
    the waiver wire.
    """
    sc = ctx.scorer
    min_share = ctx.icfg.get("stream_goalie_min_start_share", 0.0)
    out = []
    for pid, line in ctx.lines.items():
        grp = value_group(line.position)
        if group == "S":
            if grp == "G":
                continue
        elif group and grp != group:
            continue
        if line.is_goalie and line.start_share < min_share:
            continue
        if pid in unavailable:      # on waivers that day, or injured (OUT)
            continue
        if taken is not None:
            if pid in taken:
                continue
        elif likely_available and sc.group_rank[pid] <= sc.pool_sizes[grp]:
            continue
        g = ctx.team_games(line.team, date, date)
        if not g:
            continue
        opp = g[0][1]
        out.append({
            "player_id": pid, "name": line.name, "pos": line.position,
            "team": line.team, "opp": opp, "gp": line.gp,
            "value": _value(ctx, line, expected_line(
                line, ctx.factors.get(opp), assume_start=True)),
            # per game he plays / starts, comparable with tonight's value
            "per_game": (goalie_per_start(ctx)[pid] if line.is_goalie
                         else form(ctx, line)["blended"]),
            "group_rank": sc.group_rank[pid],
            "pool_size": sc.pool_sizes[grp],
            "replacement": (goalie_start_replacement(ctx) if line.is_goalie
                            else sc.replacement[grp]),
            "start_share": line.start_share if line.is_goalie else None,
            **{k: v for k, v in form(ctx, line).items()
               if k in ("vs_prior_pct", "vs_prior_diff")}})
    out.sort(key=lambda r: (-r["value"], r["player_id"]))
    return out[:top]


def goalie_per_start(ctx: Context) -> dict[int, float]:
    """Opponent-neutral value of a game each goalie STARTS (cached)."""
    cache = getattr(ctx, "_g_per_start", None)
    if cache is None:
        cache = {pid: _value(ctx, ln, expected_line(ln, None, assume_start=True))
                 for pid, ln in ctx.lines.items() if ln.is_goalie}
        ctx._g_per_start = cache
    return cache


def goalie_start_replacement(ctx: Context) -> float:
    """Per-start value of the first goalie outside the rosterable pool."""
    sc = ctx.scorer
    n = sc.pool_sizes["G"]
    ranked = sorted((p for p, ln in ctx.lines.items() if ln.is_goalie),
                    key=lambda p: sc.group_rank[p])
    return goalie_per_start(ctx)[ranked[n]] if n < len(ranked) else 0.0


def hold_check(ctx: Context, pid: int, start: str, days: int) -> dict:
    """Short-window and rest-of-season value vs a replacement-level player
    on a league-average schedule, plus a verdict."""
    line = ctx.lines[pid]
    grp = value_group(line.position)
    sc = ctx.scorer
    repl = sc.replacement[grp]
    end = _end(start, days)
    win = player_window(ctx, pid, start, end)
    ros = player_window(ctx, pid, start, ctx.fantasy_end())
    avg_win = ctx.avg_team_games(start, end)
    avg_ros = ctx.avg_team_games(start, ctx.fantasy_end())
    vpg = sc.per_game[pid]
    margin = ctx.icfg["hold_margin"]
    if vpg >= repl + margin * abs(repl) and vpg > 0:
        verdict = "HOLD"
    elif vpg < repl - ctx.icfg.get("drop_margin", 0.0) * abs(repl):
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
        # weighted (playoff games count extra), to the fantasy end
        "ros_vs_replacement": ros_over_replacement(ctx, pid, start),
        "ros_end": ctx.fantasy_end(),
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


def load_taken(ctx: Context, path: str) -> tuple[set[int], list[str]]:
    """Player ids from a text file of names (one per line, # comments).

    Returns (ids, names that matched no player).
    """
    ids: set[int] = set()
    missing: list[str] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            name = raw.split("#", 1)[0].strip()
            if not name:
                continue
            hits = find_players(ctx, name)
            if hits:
                ids.add(hits[0].player_id)
            else:
                missing.append(name)
    return ids, missing


def _name_index(ctx: Context) -> dict[str, list[Line]]:
    idx = getattr(ctx, "_names", None)
    if idx is None:
        idx = {}
        for ln in ctx.lines.values():
            idx.setdefault(_fold(ln.name), []).append(ln)
        ctx._names = idx
    return idx


def find_players(ctx: Context, query: str) -> list[Line]:
    """Accent/case-insensitive match on full name: exact (indexed) first,
    then substring."""
    q = _fold(query.strip())
    exact = _name_index(ctx).get(q)
    if exact:
        return list(exact)
    return sorted((ln for ln in ctx.lines.values() if q in _fold(ln.name)),
                  key=lambda ln: -ctx.scorer.per_game[ln.player_id])
