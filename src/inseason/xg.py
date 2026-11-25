"""Expected goals (xG) from the NHL play-by-play, and what it does to the
projection.

Data: api-web.nhle.com/v1/gamecenter/{game}/play-by-play for every played
game of the season (stored in ``shots``; older games back-filled
``BACKFILL`` a refresh, newest first). Every shot attempt keeps: shooter,
team, period and second, rink coordinates, shot type, strength (from
``situationCode``: away goalie, away skaters, home skaters, home goalie),
and whether it was a goal.

Coordinates: the NHL rink is x -100..100, y -42.5..42.5 with the nets at
x = +-89. ``homeTeamDefendingSide`` says which end the home team defends in
that period; every shot is flipped so the shooter attacks the net at
(+89, 0). Then

  distance   feet from the net
  angle      degrees off the centre line (0 = straight on)
  rebound    the same team's previous unblocked attempt was 3 s or less ago
  rush       the previous event (4 s or less before) was in the shooting
             team's own end or the neutral zone

The model: logistic regression on every unblocked attempt (shots on goal,
misses, goals; blocked shots are recorded where they're blocked, so they
get no xG) except empty-net ones, fitted on this season's stored shots
once there are ``MIN_FIT`` of them:

  logit p = b0 + b1 dist + b2 dist^2 + b3 angle + b4 rebound + b5 rush
            + b6 power play + b7 short-handed + shot-type terms

Until then, and as the starting point for the fit, fixed coefficients that
give league-typical rates (about 8% of unblocked attempts in the slot band,
falling with distance and angle).

In the projection: his expected shooting % per shot on goal this season
(ixG / shots on goal) says how good his looks are. It's mixed into the
shooting % the projection starts from (the preseason one, model.py) with
``xg_k_shots`` shots of the preseason figure, then the season's actual
goals are blended on top exactly as before (skater_sh_pct_k_shots). So a
player suddenly getting better chances is credited for it, and finishing
luck still regresses.
"""
from __future__ import annotations

import json
import math
import sqlite3

import numpy as np

PBP_URL = "https://api-web.nhle.com/v1/gamecenter/{gid}/play-by-play"
BACKFILL = 120
MIN_FIT = 5000
SHOT_TYPES = ("wrist", "snap", "slap", "backhand", "tip-in", "deflected", "wrap-around")
EVENTS = {"shot-on-goal", "missed-shot", "blocked-shot", "goal"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS shots (
    game_id INTEGER NOT NULL, event_id INTEGER NOT NULL, period INTEGER, sec INTEGER,
    team_id INTEGER, shooter INTEGER, goalie INTEGER, kind TEXT, shot_type TEXT,
    x REAL, y REAL, dist REAL, angle REAL, rebound INTEGER, rush INTEGER,
    strength TEXT, empty_net INTEGER, is_goal INTEGER,
    PRIMARY KEY (game_id, event_id));
CREATE TABLE IF NOT EXISTS shots_done (game_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS xg_model (id INTEGER PRIMARY KEY CHECK (id = 1), coefs TEXT, n INTEGER,
    fitted TEXT);
"""

# starting coefficients (and the fallback): roughly league-typical
DEFAULT = {"const": -1.20, "dist": -0.055, "dist2": 0.00012, "angle": -0.012, "rebound": 0.85,
           "rush": 0.30, "pp": 0.30, "sh": 0.10,
           "wrist": 0.0, "snap": 0.08, "slap": 0.05, "backhand": -0.10, "tip-in": 0.10,
           "deflected": 0.10, "wrap-around": -0.30}


def _sec(t: str | None) -> int:
    try:
        m, s = (t or "0:0").split(":")
        return int(m) * 60 + int(s)
    except ValueError:
        return 0


def _strength(code: str | None, home_shoots: bool) -> tuple[str, bool]:
    """('ev' / 'pp' / 'sh', defending net empty) from a 4-digit situation
    code: away goalie, away skaters, home skaters, home goalie."""
    if not code or len(code) != 4 or not code.isdigit():
        return "ev", False
    ag, ask, hsk, hg = (int(c) for c in code)
    own, opp = (hsk, ask) if home_shoots else (ask, hsk)
    empty = (ag == 0) if home_shoots else (hg == 0)
    return ("pp" if own > opp else "sh" if own < opp else "ev"), empty


def parse_game(data: dict) -> list[tuple]:
    """Shot rows for one game's play-by-play JSON (see module docstring)."""
    gid = data.get("id")
    home = (data.get("homeTeam") or {}).get("id")
    rows = []
    prev = None                # (team, abs sec, unblocked?) of the last shot attempt
    prev_ev = None             # (team, abs sec, zone) of the last event with an owner
    for p in data.get("plays") or []:
        d = p.get("details") or {}
        per = (p.get("periodDescriptor") or {}).get("number") or 0
        t = (per - 1) * 1200 + _sec(p.get("timeInPeriod"))
        team = d.get("eventOwnerTeamId")
        kind = p.get("typeDescKey")
        if kind in EVENTS and team and d.get("xCoord") is not None and d.get("yCoord") is not None:
            home_shoots = team == home
            x, y = float(d["xCoord"]), float(d["yCoord"])
            side = p.get("homeTeamDefendingSide")
            # the shooter attacks the end the other team defends
            attacks_right = (side == "left") == home_shoots if side in ("left", "right") else x >= 0
            if not attacks_right:
                x, y = -x, -y
            if kind == "blocked-shot":
                # recorded where it was blocked; keep it for counts only
                dist, ang = None, None
            else:
                dx = max(89.0 - x, 0.5)
                dist = math.hypot(dx, y)
                ang = math.degrees(math.atan(abs(y) / dx))
            strength, empty = _strength(p.get("situationCode"), home_shoots)
            rebound = int(bool(prev and prev[0] == team and prev[2] and 0 <= t - prev[1] <= 3))
            # rush: the event just before (4 s or less) happened in the
            # shooting team's own end or the neutral zone. zoneCode is from
            # the event owner's view, so the other team's "O" is our "D".
            rush = 0
            if prev_ev and 0 <= t - prev_ev[1] <= 4:
                ours = prev_ev[0] == team
                rush = int(prev_ev[2] == "N" or prev_ev[2] == ("D" if ours else "O"))
            shooter = d.get("scoringPlayerId") if kind == "goal" else d.get("shootingPlayerId")
            rows.append((gid, p.get("eventId"), per, t, team, shooter, d.get("goalieInNetId"),
                         kind, (d.get("shotType") or "").lower() or None, x, y, dist, ang,
                         rebound, rush, strength, int(empty), int(kind == "goal")))
            prev = (team, t, kind != "blocked-shot")
        if team:
            prev_ev = (team, t, d.get("zoneCode"))
    return rows


def features(dist: float, angle: float, rebound: int, rush: int, strength: str,
             shot_type: str | None) -> np.ndarray:
    st = [1.0 if shot_type == s else 0.0 for s in SHOT_TYPES[1:]]   # wrist = baseline
    return np.array([1.0, dist, dist * dist, angle, rebound, rush,
                     1.0 if strength == "pp" else 0.0, 1.0 if strength == "sh" else 0.0, *st])


NAMES = ["const", "dist", "dist2", "angle", "rebound", "rush", "pp", "sh", *SHOT_TYPES[1:]]


def fit(X: np.ndarray, y: np.ndarray, start: np.ndarray, l2: float = 1.0,
        iters: int = 25) -> np.ndarray:
    """Logistic regression by Newton steps, a little L2 pull toward the
    starting coefficients so rare shot types stay sensible."""
    b = start.copy()
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ b)))
        w = p * (1 - p)
        g = X.T @ (y - p) - l2 * (b - start)
        H = (X * w[:, None]).T @ X + l2 * np.eye(len(b))
        step = np.linalg.solve(H, g)
        b = b + step
        if np.max(np.abs(step)) < 1e-6:
            break
    return b


def _unblocked(conn: sqlite3.Connection):
    return list(conn.execute(
        "SELECT dist, angle, rebound, rush, strength, shot_type, is_goal, shooter, kind FROM shots"
        " WHERE kind != 'blocked-shot' AND empty_net = 0 AND dist IS NOT NULL"))


def refit(conn: sqlite3.Connection, as_of: str) -> dict:
    rows = _unblocked(conn)
    start = np.array([DEFAULT[n] for n in NAMES])
    if len(rows) < MIN_FIT:
        coefs = start
    else:
        X = np.array([features(*r[:6]) for r in rows])
        y = np.array([r[6] for r in rows], dtype=float)
        coefs = fit(X, y, start)
    conn.execute("INSERT OR REPLACE INTO xg_model VALUES (1, ?, ?, ?)",
                 (json.dumps(dict(zip(NAMES, map(float, coefs)))), len(rows), as_of))
    return {"xg_shots": len(rows), "xg_fitted": len(rows) >= MIN_FIT}


def coefs(conn: sqlite3.Connection) -> dict:
    try:
        r = conn.execute("SELECT coefs FROM xg_model WHERE id=1").fetchone()
    except sqlite3.OperationalError:
        r = None
    return json.loads(r[0]) if r else dict(DEFAULT)


def prob(c: dict, dist, angle, rebound, rush, strength, shot_type) -> float:
    b = np.array([c[n] for n in NAMES])
    z = float(features(dist, angle, rebound, rush, strength, shot_type) @ b)
    return 1 / (1 + math.exp(-z))


def ingest_shots(conn: sqlite3.Connection, fetcher, season: int, as_of: str) -> dict:
    """Fetch play-by-play for played games not stored yet (the newest
    ``BACKFILL``), then refit the model."""
    conn.executescript(SCHEMA)
    done = {g for (g,) in conn.execute("SELECT game_id FROM shots_done")}
    games = [g for (g,) in conn.execute(
        "SELECT game_id FROM schedule WHERE season_id=? AND game_date < ? AND game_type=2"
        " ORDER BY game_date DESC, game_id DESC", (season, as_of))]
    todo = [g for g in games if g not in done][:BACKFILL]
    n = 0
    for gid in todo:
        data = fetcher.get_json(PBP_URL.format(gid=gid))
        if not isinstance(data, dict) or not data.get("plays"):
            continue
        rows = parse_game(data)
        conn.executemany(f"INSERT OR REPLACE INTO shots VALUES ({','.join('?' * 18)})", rows)
        conn.execute("INSERT OR REPLACE INTO shots_done VALUES (?)", (gid,))
        n += 1
    return {"xg_games": n, **refit(conn, as_of)}


def player_xg(conn: sqlite3.Connection, season: int) -> dict[int, dict]:
    """{shooter: {"ixg", "goals", "sog", "attempts"}} over this season's
    stored games (empty-net shots left out)."""
    try:
        c = coefs(conn)
        rows = list(conn.execute(
            "SELECT s.shooter, s.dist, s.angle, s.rebound, s.rush, s.strength, s.shot_type,"
            " s.is_goal, s.kind FROM shots s JOIN schedule g ON g.game_id = s.game_id"
            " WHERE g.season_id=? AND s.empty_net = 0 AND s.kind != 'blocked-shot'"
            " AND s.dist IS NOT NULL", (season,)))
    except sqlite3.OperationalError:
        return {}
    rows = [r for r in rows if r[0] is not None]
    if not rows:
        return {}
    b = np.array([c[n] for n in NAMES])
    X = np.array([features(*r[1:7]) for r in rows])
    p = 1 / (1 + np.exp(-(X @ b)))
    out: dict[int, dict] = {}
    for (shooter, *_rest, goal, kind), v in zip(rows, p):
        o = out.setdefault(shooter, {"ixg": 0.0, "goals": 0, "sog": 0, "attempts": 0})
        o["ixg"] += float(v)
        o["goals"] += goal
        o["sog"] += kind in ("shot-on-goal", "goal")
        o["attempts"] += 1
    return out


def apply(lines: dict, xg: dict[int, dict], cfg: dict, kss: float | None) -> None:
    """Mix his xG per shot on goal into the shooting % the projection
    starts from and redo goals (module docstring); attaches extra["xg"]."""
    kq = cfg.get("xg_k_shots")
    for pid, x in xg.items():
        ln = lines.get(pid)
        if ln is None or ln.is_goalie:
            continue
        ln.extra["xg"] = dict(x)
        sh = ln.extra.get("sh_pct")
        if not kq or not kss or not sh or not x["sog"] or ln.blended.get("shots", 0) <= 0:
            continue
        quality = x["ixg"] / x["sog"]
        prior = (sh["prior"] * kq + quality * x["sog"]) / (kq + x["sog"])
        gp = ln.gp or 0
        goals_now = ln.observed.get("goals", 0.0) * gp
        shots_now = ln.observed.get("shots", 0.0) * gp
        new = (prior * kss + goals_now) / (kss + shots_now) if kss + shots_now > 0 else prior
        ln.blended["goals"] = ln.blended["shots"] * new
        ln.extra["xg"].update(quality=quality, prior_used=prior)
        sh.update(xg_prior=prior, blended=new)
