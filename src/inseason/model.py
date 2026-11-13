"""Blended in-season rates and opponent strength.

Every player gets an expected per-game line (skaters) or per-start line
(goalies) that blends the preseason projection with what he has actually
done this season:

    blended = (preseason_rate * K + season_to_date_total) / (K + games)

with a per-stat K from config/inseason.yaml. Opponent factors scale a single
game's expected line by how much the opponent allows (goals / shots against)
or generates (goals / shots for), each regressed to the league mean.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.paths import CONFIG_DIR

INSEASON_CONFIG_PATH = CONFIG_DIR / "inseason.yaml"
INSEASON_INGEST_COMMAND = "uv run python -m src.inseason.ingest"

SKATER_STATS: tuple[str, ...] = (
    "goals", "assists", "shots", "blocks", "hits", "pp_points", "sh_points")
GOALIE_STATS: tuple[str, ...] = ("wins", "saves", "goals_against", "shutouts")


def load_inseason_config(path: str | Path | None = None) -> dict:
    with open(path or INSEASON_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def blend(prior_rate: float, total: float, games: float, k: float) -> float:
    """(prior_rate * k + total) / (k + games); prior_rate when both are 0."""
    if k + games <= 0:
        return float(prior_rate)
    return (prior_rate * k + total) / (k + games)


@dataclass
class Line:
    """One player's in-season state. Rates are per game (skaters) or per
    start (goalies); ``start_share`` is the share of team games started."""

    player_id: int
    name: str
    position: str
    team: str | None
    gp: int                      # season-to-date games (starts for goalies)
    prior: dict[str, float]
    observed: dict[str, float]   # season-to-date per game / per start
    blended: dict[str, float]
    toi_pg_prior: float | None = None
    toi_pg_obs: float | None = None
    start_share: float = 0.0
    start_share_prior: float = 0.0
    start_share_obs: float = 0.0
    # skaters: share of team games he plays (injuries, healthy scratches)
    play_share: float = 1.0
    play_share_prior: float = 1.0
    play_share_obs: float = 1.0
    has_projection: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def is_goalie(self) -> bool:
        return self.position == "G"


# ── Data access ────────────────────────────────────────────────────────

def season_and_as_of(conn: sqlite3.Connection) -> tuple[int, str]:
    try:
        row = conn.execute(
            "SELECT MAX(season_id), MAX(as_of) FROM skater_current").fetchone()
    except sqlite3.OperationalError:
        row = None
    if not row or row[0] is None:
        raise ValueError(
            "no in-season data in the database; run "
            f"{INSEASON_INGEST_COMMAND}")
    return int(row[0]), row[1]


def _rows(conn, sql: str, args=()) -> list[dict]:
    cur = conn.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_current(conn, season_id: int) -> tuple[dict[int, dict], dict[int, dict]]:
    sk = {r["player_id"]: r for r in _rows(
        conn, "SELECT * FROM skater_current WHERE season_id=?", (season_id,))}
    gl = {r["player_id"]: r for r in _rows(
        conn, "SELECT * FROM goalie_current WHERE season_id=?", (season_id,))}
    return sk, gl


def load_players(conn) -> dict[int, tuple[str, str, str | None]]:
    return {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT player_id, full_name, position, current_team_abbrev FROM players")}


def team_games_played(skaters: dict[int, dict]) -> dict[str, int]:
    """Games each team has played = the highest gp at least two of its
    skaters share (every regular who hasn't missed a game has it).

    Derived from the same stats snapshot rather than the schedule, so it is
    consistent with the totals even if the ingest ran mid-slate.

    Why not simply the max: the stats API gives a traded player ONE row
    with his combined gp, filed under his new team, which can exceed the
    games that team has played. That value is his alone, so requiring two
    skaters skips it. A team with no shared value falls back to the max.
    """
    by_team: dict[str, dict[int, int]] = {}
    for r in skaters.values():
        t, gp = r["team_abbrev"], r["gp"] or 0
        if t and gp > 0:
            counts = by_team.setdefault(t, {})
            counts[gp] = counts.get(gp, 0) + 1
    return {t: max((g for g, n in c.items() if n >= 2), default=max(c))
            for t, c in by_team.items()}


def team_share(row_gp: int | None, team_gp: int) -> float:
    """Share of a stats row that belongs to its listed team: 1.0 normally,
    team_gp / row_gp for a traded player whose combined row has more games
    than his new team has played (the rest came with his old team)."""
    gp = row_gp or 0
    return min(1.0, team_gp / gp) if gp > team_gp > 0 else 1.0


def season_team_games(conn, season_id: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for h, a in conn.execute(
            "SELECT home_abbrev, away_abbrev FROM schedule"
            " WHERE season_id=? AND game_type=2", (season_id,)):
        for t in (h, a):
            if t:
                out[t] = out.get(t, 0) + 1
    return out


# ── Opponent strength ──────────────────────────────────────────────────

def opponent_factors(skaters: dict[int, dict], goalies: dict[int, dict],
                     team_gp: dict[str, int], k: float) -> dict[str, dict[str, float]]:
    """Per-team multipliers, each regressed to the league mean.

    ga/sa: goals / shots the team ALLOWS per game (from its goalies) --
    applied to an opposing skater's scoring and shots.
    gf/sf: goals / shots the team SCORES per game (from its skaters) --
    applied to an opposing goalie's goals-against and saves.
    factor = ((total + league_pg * k) / (gp + k)) / league_pg

    A traded player's combined row is pro-rated to his new team
    (team_share): the stats API has no per-team split, so his old team's
    games are dropped rather than credited to the wrong team.
    """
    totals: dict[str, dict[str, float]] = {
        t: {"ga": 0.0, "sa": 0.0, "gf": 0.0, "sf": 0.0} for t in team_gp}
    for r in goalies.values():
        t = totals.get(r["team_abbrev"])
        if t is not None:
            w = team_share(r.get("gp"), team_gp[r["team_abbrev"]])
            t["ga"] += w * (r["goals_against"] or 0)
            t["sa"] += w * (r["shots_against"] or 0)
    for r in skaters.values():
        t = totals.get(r["team_abbrev"])
        if t is not None:
            w = team_share(r.get("gp"), team_gp[r["team_abbrev"]])
            t["gf"] += w * (r["goals"] or 0)
            t["sf"] += w * (r["shots"] or 0)
    games = sum(team_gp.values())
    out: dict[str, dict[str, float]] = {}
    if games <= 0:
        return out
    league = {m: sum(t[m] for t in totals.values()) / games
              for m in ("ga", "sa", "gf", "sf")}
    for team, t in totals.items():
        gp = team_gp[team]
        out[team] = {
            m: (((t[m] + league[m] * k) / (gp + k)) / league[m]
                if league[m] > 0 else 1.0)
            for m in ("ga", "sa", "gf", "sf")}
    return out


# ── Priors ─────────────────────────────────────────────────────────────

def _per_start_rates(p) -> dict[str, float] | None:
    """Per-start rates of a goalie projected to 0 starts (off a roster, or
    the odd man out of his team's pool) from his projection terms; None
    when he has no history to project from."""
    raw = (p.terms or {}).get("raw_rates") or {}
    if not (p.terms or {}).get("season_weights") or "sv_per_start" not in raw:
        return None
    return {"wins": raw["wins_pct"], "saves": raw["sv_per_start"],
            "goals_against": raw["ga_per_start"],
            "shutouts": raw["so_per_start"]}


def priors_from_projections(projs) -> dict[int, dict]:
    """Preseason per-game (skater) / per-start (goalie) rates from project_all."""
    out: dict[int, dict] = {}
    for p in projs:
        if p.position == "G":
            gs = p.games_started or 0.0
            per = ({s: (getattr(p, s) or 0.0) / gs for s in GOALIE_STATS}
                   if gs > 0 else _per_start_rates(p))
            out[p.player_id] = {"per_start": per, "save_pct": p.save_pct,
                                "starts": gs}
        elif p.gp and p.gp > 0:
            out[p.player_id] = {
                "pg": {s: (getattr(p, s, 0.0) or 0.0) / p.gp for s in SKATER_STATS},
                "toi_pg": p.toi_per_gp,
                # projected gp = season games x share of them he plays
                "gp_share": min(p.gp / float(
                    (p.terms or {}).get("season_games") or 82), 1.0)}
    return out


def _skater_fallback(skaters: dict[int, dict]) -> dict[str, float]:
    """League per-game rates this season: the prior for unprojected skaters."""
    gp = sum(r["gp"] or 0 for r in skaters.values())
    return {s: (sum(r.get(s) or 0 for r in skaters.values()) / gp if gp else 0.0)
            for s in SKATER_STATS}


def _goalie_fallback(goalies: dict[int, dict]) -> tuple[dict[str, float], float]:
    gs = sum(r["games_started"] or 0 for r in goalies.values())
    sa = sum(r["shots_against"] or 0 for r in goalies.values())
    sv = sum(r["saves"] or 0 for r in goalies.values())
    per = {s: (sum(r[s] or 0 for r in goalies.values()) / gs if gs else 0.0)
           for s in GOALIE_STATS}
    return per, (sv / sa if sa else 0.0)


# ── Lines ──────────────────────────────────────────────────────────────

def load_recent(conn) -> dict[int, dict]:
    """{pid: {"games", "stats", "start", "end"}} from skater_recent /
    goalie_recent: totals over the trend window. Goalies' games are starts.
    Empty when the tables don't exist yet (no refresh since they were added)."""
    out: dict[int, dict] = {}
    try:
        for r in conn.execute(
                "SELECT player_id, gp, goals, assists, shots, blocks, hits,"
                " pp_points, sh_points, window_start, window_end FROM skater_recent"):
            out[r[0]] = {"games": r[1] or 0, "start": r[9], "end": r[10],
                         "stats": {k: float(v or 0) for k, v in zip(
                             ("goals", "assists", "shots", "blocks", "hits",
                              "pp_points", "sh_points"), r[2:9])}}
        for r in conn.execute(
                "SELECT player_id, games_started, wins, saves, shots_against,"
                " goals_against, shutouts, window_start, window_end"
                " FROM goalie_recent"):
            out[r[0]] = {"games": r[1] or 0, "start": r[7], "end": r[8],
                         "stats": {k: float(v or 0) for k, v in zip(
                             ("wins", "saves", "shots_against", "goals_against",
                              "shutouts"), r[2:7])}}
    except sqlite3.OperationalError:
        return {}
    return out


def build_lines(conn, season_id: int, priors: dict[int, dict], cfg: dict,
                ) -> dict[int, Line]:
    """Blended Line for every rostered player and every player with stats."""
    skaters, goalies = load_current(conn, season_id)
    players = load_players(conn)
    team_gp = team_games_played(skaters)
    team_total = season_team_games(conn, season_id)
    sk_fb = _skater_fallback(skaters)
    g_fb, sv_fb = _goalie_fallback(goalies)
    kg = cfg["skater_k_games"]
    kgs = cfg["goalie_k_starts"]
    lines: dict[int, Line] = {}

    pids = set(skaters) | set(goalies) | {
        pid for pid, (_, _, team) in players.items() if team}
    for pid in sorted(pids):
        name, pos, team = players.get(pid, (None, None, None))
        cur_s, cur_g = skaters.get(pid), goalies.get(pid)
        if pos is None:
            pos = "G" if cur_g and not cur_s else "C"
        team = team or (cur_g or cur_s or {}).get("team_abbrev")
        name = name or f"Player {pid}"
        pr = priors.get(pid)

        if pos == "G":
            gs = (cur_g or {}).get("games_started") or 0
            per_prior = (pr or {}).get("per_start")
            has_proj = per_prior is not None
            per_prior = per_prior or g_fb
            obs = {s: ((cur_g or {}).get(s) or 0) / gs if gs else 0.0
                   for s in GOALIE_STATS}
            bl = {s: blend(per_prior[s], ((cur_g or {}).get(s) or 0), gs, kgs[s])
                  for s in GOALIE_STATS}
            sa = (cur_g or {}).get("shots_against") or 0
            sv = (cur_g or {}).get("saves") or 0
            sv_prior = (pr or {}).get("save_pct") or sv_fb
            prior = dict(per_prior, save_pct=sv_prior)
            obs["save_pct"] = sv / sa if sa else 0.0
            bl["save_pct"] = blend(sv_prior, sv, sa, cfg["goalie_save_pct_k_shots"])
            # Saves and goals against come from ONE workload and ONE save %:
            # shots against per start blend fast (k = the saves k), save %
            # slowly (goalie_save_pct_k_shots). Blending GA per start on its
            # own let a hot or cold 15-start stretch move it as much as ~400
            # shots would, while save % (the same skill) needed 1000.
            sa_prior = per_prior["saves"] + per_prior["goals_against"]
            obs["shots_against"] = sa / gs if gs else 0.0
            prior["shots_against"] = sa_prior
            bl["shots_against"] = blend(sa_prior, sa, gs, kgs["saves"])
            bl["saves"] = bl["shots_against"] * bl["save_pct"]
            bl["goals_against"] = bl["shots_against"] * (1 - bl["save_pct"])
            season_games = team_total.get(team or "", 0)
            share_prior = ((pr or {}).get("starts", 0.0) / season_games
                           if season_games else 0.0)
            tgp = team_gp.get(team or "", 0)
            # a traded goalie's starts include his old team's games
            gs_here = (gs * team_share((cur_g or {}).get("gp"), tgp)
                       if (cur_g or {}).get("team_abbrev") == team else gs)
            share_obs = min(gs_here / tgp, 1.0) if tgp else 0.0
            share = blend(share_prior, gs_here, tgp, cfg["start_share_k_games"])
            lines[pid] = Line(pid, name, pos, team, gs, prior, obs, bl,
                              start_share=min(share, 1.0),
                              start_share_prior=share_prior,
                              start_share_obs=share_obs,
                              has_projection=has_proj)
            continue

        gp = (cur_s or {}).get("gp") or 0
        has_proj = pr is not None and "pg" in pr
        prior = pr["pg"] if has_proj else sk_fb
        obs = {s: ((cur_s or {}).get(s) or 0) / gp if gp else 0.0
               for s in SKATER_STATS}
        bl = {s: blend(prior.get(s, 0.0), ((cur_s or {}).get(s) or 0), gp,
                       kg.get(s, 40))
              for s in SKATER_STATS}
        toi_obs = ((cur_s or {}).get("toi_sec") or 0) / gp if gp else None
        # Share of his team's games he plays. Rates above are per game
        # PLAYED; a player who misses games is worth less per TEAM game.
        # Prior: projected durability (unprojected: 1). Observed: gp over
        # the team's games, a traded player's old-team games pro-rated out.
        tgp = team_gp.get(team or "", 0)
        gp_here = (gp * team_share(gp, tgp)
                   if (cur_s or {}).get("team_abbrev") == team else gp)
        ps_prior = float((pr or {}).get(
            "gp_share", cfg.get("play_share_unprojected", 1.0)))
        ps_obs = min(gp_here / tgp, 1.0) if tgp else ps_prior
        # play_share_use_season false: games missed so far this season say
        # little about games missed from here on, so keep the prior.
        ps = (min(blend(ps_prior, gp_here, tgp,
                        cfg.get("play_share_k_games", 20)), 1.0)
              if cfg.get("play_share_use_season", True) else ps_prior)
        lines[pid] = Line(pid, name, pos, team, gp, dict(prior), obs, bl,
                          toi_pg_prior=(pr or {}).get("toi_pg"),
                          toi_pg_obs=toi_obs, has_projection=has_proj,
                          play_share=ps, play_share_prior=ps_prior,
                          play_share_obs=ps_obs)
    return lines
