"""Zero-sum per-team allocation of projected goalie starts.

Team membership comes from players.current_team_abbrev; goalie_seasons only
supplies statistics. See _allocate_goalie_starts for the invariants.
"""
from __future__ import annotations

import logging
import sqlite3

from .common import Projection

# Same logger name as before the split so existing log config still applies.
log = logging.getLogger("src.projections.projections")


def _team_games_for(season_id: int, conn: sqlite3.Connection) -> dict[str, int]:
    """Regular-season games per team from the schedule (game_type=2)."""
    team_games: dict[str, int] = {}
    rows = conn.execute(
        "SELECT home_abbrev, away_abbrev FROM schedule"
        " WHERE season_id = ? AND game_type = 2", (season_id,)).fetchall()
    for home, away in rows:
        if home:
            team_games[home] = team_games.get(home, 0) + 1
        if away:
            team_games[away] = team_games.get(away, 0) + 1
    return team_games


def _apply_goalie_starts(p: Projection, starts: float) -> None:
    """Scale a goalie's count totals from the allocated projected starts.

    Wins, saves, goals-against and shutouts scale with projected starts;
    save_pct and the per-start rates are untouched.
    """
    starts = max(starts, 0.0)
    raw = p.terms["raw_rates"]
    final = p.terms["final"]
    final["games_started"] = starts
    final["wins"] = raw["wins_pct"] * starts
    final["goals_against"] = raw["ga_per_start"] * starts
    final["saves"] = raw["sv_per_start"] * starts
    final["shutouts"] = raw["so_per_start"] * starts
    p.gp = starts
    p.games_started = starts
    p.wins = final["wins"]
    p.goals_against = final["goals_against"]
    p.saves = final["saves"]
    p.shutouts = final["shutouts"]
    p.terms["proj_gp"] = starts


def _rostered_goalies(conn: sqlite3.Connection, season_id: int | None = None,
                      ) -> list[tuple[int, str, str | None]]:
    """Goalies in the projection pool: (player_id, full_name, team).

    Production: the current roster (players.current_team_abbrev) IS the
    source of team membership. Backtest: when ``season_id`` already has
    goalie_seasons rows (a past season being re-projected), today's roster
    would be look-ahead, so the pool is the goalies who played that season
    with that season's team. Prior seasons' goalie_seasons teams are never
    used to decide membership.
    """
    if season_id is not None:
        try:
            rows = conn.execute(
                "SELECT g.player_id, p.full_name, g.team_abbrev"
                " FROM goalie_seasons g LEFT JOIN players p"
                " ON p.player_id = g.player_id WHERE g.season_id = ?"
                " ORDER BY g.player_id", (season_id,)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if rows:
            return [(int(r[0]), r[1], r[2]) for r in rows]
    cols = [r[1] for r in conn.execute("PRAGMA table_info(players)")]
    if "full_name" in cols:
        rows = conn.execute(
            "SELECT player_id, full_name, current_team_abbrev"
            " FROM players WHERE position = ? ORDER BY player_id", ("G",)).fetchall()
        return [(int(r[0]), r[1], r[2]) for r in rows]
    rows = conn.execute(
        "SELECT player_id, current_team_abbrev"
        " FROM players WHERE position = ? ORDER BY player_id", ("G",)).fetchall()
    return [(int(r[0]), None, r[1]) for r in rows]


def _recent_games_started(conn: sqlite3.Connection, pid: int) -> int:
    """Most recent season games_started from goalie_seasons (0 if none)."""
    row = conn.execute(
        "SELECT games_started FROM goalie_seasons"
        " WHERE player_id = ? AND gp > 0 ORDER BY season_id DESC LIMIT 1",
        (pid,)).fetchone()
    if row and row[0]:
        return int(row[0])
    return 0


def _goalie_season_history(conn: sqlite3.Connection, pid: int) -> list[tuple]:
    """All goalie_seasons rows for a goalie, ascending by season_id."""
    return conn.execute(
        "SELECT season_id, team_abbrev, games_started FROM goalie_seasons"
        " WHERE player_id = ? AND gp > 0 ORDER BY season_id ASC",
        (pid,)).fetchall()


def _cap_team_to_three(team: str, members: list[Projection],
                       name_of: dict[int, str],
                       conn: sqlite3.Connection) -> list[Projection]:
    """HARD INVARIANT: a team carries at most 3 goalies in the allocation.

    If the roster has more than 3, keep the 3 with the most recent-season
    games_started (tie-break: lower player_id first). Dropped goalies are
    printed by name with their season history and get 0 projected starts;
    this is a visible data-quality report, never a silent trim.
    """
    if len(members) <= 3:
        return members
    scored = []
    for p in members:
        recent = _recent_games_started(conn, p.player_id)
        scored.append((recent, p.player_id, p))
    scored.sort(key=lambda x: (-x[0], x[1]))
    kept = [item[2] for item in scored[:3]]
    dropped = scored[3:]
    kept_names = ", ".join(name_of.get(p.player_id, str(p.player_id))
                           for p in kept)
    log.debug("[cap>3] %s: keeping %s, dropping %d rostered goalie(s)" % (
        team, kept_names, len(dropped)))
    for recent, pid, p in dropped:
        pname = name_of.get(pid, str(pid))
        log.debug("  DROPPED: %s | team=%s | recent_starts=%d"
              % (pname, team, recent))
        history = _goalie_season_history(conn, pid)
        if history:
            for row in history:
                log.debug("    %d %s starts=%s" % (row[0], row[1], row[2]))
        else:
            log.debug("    (no goalie_seasons history)")
        _apply_goalie_starts(p, 0.0)
    return kept


def _allocate_goalie_starts(projs: list[Projection],
                            name_of: dict[int, str],
                            conn: sqlite3.Connection,
                            season_id: int, cfg: dict) -> None:
    """Zero-sum per-team allocation of projected goalie starts.

    The goalie pool and every team membership come from
    players.current_team_abbrev (see _rostered_goalies); goalie_seasons
    only supplies statistics. A goalie with no current_team_abbrev gets 0
    projected starts and is printed by name. Each team keeps at most 3
    goalies (see _cap_team_to_three). A team plays a fixed number of
    regular-season games, shared among its goalies, so the team's projected
    starts must sum to exactly that game count and no goalie may start more
    games than the team plays. Each goalie's raw starts become a share of
    the team total; a zero raw sum splits the team's games evenly and is
    logged. Capping at max_goalie_starts (regression.yaml, default 68)
    redistributes any remainder proportionally among the team's uncapped
    goalies, re-checking both invariants, at most 3 times. A cap or sum
    violation raises with the team abbreviation and the offending numbers;
    it is never clamped silently.
    """
    goalies = [p for p in projs if p.position == "G"]
    if not goalies:
        return
    cap = float(cfg.get("max_goalie_starts", 68))
    team_games = _team_games_for(season_id, conn)
    rostered = _rostered_goalies(conn, season_id)
    roster_pid = {pid for pid, _, _ in rostered}
    team_of: dict[int, str] = {}
    for pid, _, team in rostered:
        if team:
            team_of[pid] = team
    unassigned = [p for p in goalies
                  if p.player_id not in roster_pid
                  or p.player_id not in team_of]
    unassigned_ids = {p.player_id for p in unassigned}
    for p in unassigned:
        pname = name_of.get(p.player_id, str(p.player_id))
        log.debug("[unassigned] %s | team=NONE | starts=0.00" % pname)
        _apply_goalie_starts(p, 0.0)
    by_team: dict[str, list[Projection]] = {}
    for p in goalies:
        if p.player_id in unassigned_ids:
            continue
        team = team_of.get(p.player_id)
        if team is None:
            pname = name_of.get(p.player_id, str(p.player_id))
            log.debug("[unassigned] %s | team=NONE | starts=0.00" % pname)
            _apply_goalie_starts(p, 0.0)
            continue
        by_team.setdefault(team, []).append(p)
    log.debug("[per-team goalie count from players, pre-cap]:")
    for team in sorted(by_team):
        count = len(by_team[team])
        flag = "  <== FLAG >3" if count > 3 else ""
        log.debug("  %s %d%s" % (team, count, flag))
    for team in list(by_team.keys()):
        by_team[team] = _cap_team_to_three(team, by_team[team], name_of, conn)
    log.debug("[per-team goalie allocation, post-cap]:")
    for team in sorted(by_team):
        members = by_team[team]
        total = team_games.get(team, 0)
        if total <= 0:
            for p in members:
                _apply_goalie_starts(p, 0.0)
            log.debug("  %s games=%d: %s" % (
                team, total,
                "; ".join("%s=%.2f" % (name_of.get(p.player_id, str(p.player_id)),
                                        0.0) for p in members)))
            continue
        raw = [p.terms["final"]["games_started"] for p in members]
        raw_sum = sum(raw)
        if raw_sum > 0:
            starts = [r / raw_sum * total for r in raw]
        else:
            starts = [total / len(members)] * len(members)
            log.warning(
                "team %s goalies have no start history; splitting %d games "
                "evenly across %d goalies", team, total, len(members))
        for _iteration in range(3):
            over = [i for i, s in enumerate(starts) if s > cap]
            if not over:
                break
            for i in over:
                starts[i] = cap
            remainder = total - sum(starts)
            uncapped = [i for i in range(len(starts)) if starts[i] < cap]
            if not uncapped:
                raise ValueError(
                    "goalie starts for %s: every goalie is at max starts "
                    "%g but the team plays %d games, so the remainder "
                    "cannot be allocated" % (team, cap, total))
            base = sum(starts[i] for i in uncapped)
            if base > 0:
                for i in uncapped:
                    starts[i] += remainder * starts[i] / base
            else:
                for i in uncapped:
                    starts[i] += remainder / len(uncapped)
        for p, s in zip(members, starts):
            if s > cap:
                raise ValueError(
                    "goalie starts for %s: player %s projected at %.4f "
                    "starts exceeds max_goalie_starts %g"
                    % (team, name_of.get(p.player_id, str(p.player_id)),
                       s, cap))
        starts_sum = sum(starts)
        if abs(starts_sum - total) > 0.01:
            raise ValueError(
                "goalie starts for %s: goalies sum to %.4f starts but the "
                "team plays %d games" % (team, starts_sum, total))
        for p, s in zip(members, starts):
            _apply_goalie_starts(p, s)
        detail = "; ".join("%s=%.2f"
                           % (name_of.get(p.player_id, str(p.player_id)), s)
                           for p, s in zip(members, starts))
        log.debug("  %s games=%d sum=%.2f: %s" % (team, total, starts_sum, detail))
