"""Deployment: which line and power-play unit each skater is on, and how
his ice time is moving.

Two sources, both refreshed by the in-season ingest:

1. Shift charts (api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId=)
   for each team's last few games. From who is on the ice each second we
   get the strength (skaters per side, goalies excluded), then:
     - 5-on-5 lines: forwards ranked by 5v5 ice time; the top unassigned
       forward plus the two forwards he shared the most 5v5 ice with form a
       line, repeated -> F1..F4 (ordered by the line's 5v5 time). Defence
       the same in pairs -> D1..D3.
     - power-play units: the same on power-play seconds (his side has more
       skaters), five at a time -> PP1, PP2 (a unit needs >= 20 s).
   Stored per game in game_deployment, with each player's 5v5 linemates.

2. The time-on-ice report summed over two windows: the last RECENT_DAYS
   days and the PRIOR_DAYS before them (skater_toi_window), for PP time
   per game moving up or down.

load_roles() turns those into one dict per player: his line and PP unit
in his latest game, what they usually were before it, and his recent vs
prior ice time. "Promoted" means a better line or PP unit than usual.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from collections import Counter

SHIFT_URL = "https://api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId={gid}"
TOI_URL = "https://api.nhle.com/stats/rest/en/skater/timeonice"
GAMES_PER_TEAM = 5         # games of deployment kept per team
RECENT_DAYS = 7
PRIOR_DAYS = 21
MIN_UNIT_SEC = 20          # a PP unit needs this much PP time together
PERIOD_SEC = 1200
PAIRS_BACKFILL = 120       # older games' shift charts fetched per refresh for game_pairs

LINE_RANK = {"F1": 1, "F2": 2, "F3": 3, "F4": 4, "D1": 1, "D2": 2, "D3": 3}
PP_RANK = {"PP1": 1, "PP2": 2, None: 3}

SCHEMA = """
CREATE TABLE IF NOT EXISTS game_deployment (
    game_id INTEGER NOT NULL,
    game_date TEXT NOT NULL,
    team TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    line TEXT,            -- F1..F4 / D1..D3, NULL if he didn't fit a unit
    pp TEXT,              -- PP1 / PP2 / NULL
    es_sec INTEGER,       -- 5-on-5 seconds
    pp_sec INTEGER,       -- power-play seconds
    mates TEXT,           -- JSON list of 5v5 linemate ids
    PRIMARY KEY (game_id, player_id)
);
-- 5v5 seconds each skater shared with each teammate (mate_id = player_id:
-- his own 5v5 seconds), every game of the season -- for linemates' shooting
-- talent behind his on-ice shooting % (advanced.py)
CREATE TABLE IF NOT EXISTS game_pairs (
    game_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    mate_id INTEGER NOT NULL,
    sec INTEGER NOT NULL,
    PRIMARY KEY (game_id, player_id, mate_id)
);
CREATE TABLE IF NOT EXISTS game_pairs_done (game_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS skater_toi_window (
    player_id INTEGER NOT NULL,
    window TEXT NOT NULL,          -- 'recent' or 'prior'
    gp INTEGER, toi INTEGER, ev_toi INTEGER, pp_toi INTEGER, sh_toi INTEGER,
    window_start TEXT, window_end TEXT,
    PRIMARY KEY (player_id, window)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _sec(mmss: str) -> int:
    m, s = str(mmss).split(":")
    return int(m) * 60 + int(s)


# ── one game ──────────────────────────────────────────────────────────

def game_units(shifts: list[dict], positions: dict[int, str]) -> dict[int, dict]:
    """Line, PP unit and linemates per skater for one game.

    ``shifts``: rows of the shift-chart report (typeCode 517 = a shift;
    others are goal events and are ignored). ``positions``: player id ->
    C / L / R / D / G; a player missing from it who is on the ice for a
    whole period is taken to be a goalie.
    """
    on: dict[tuple[int, str], list[set[int]]] = {}   # (period, team) -> per-second
    team_of: dict[int, str] = {}
    periods: set[int] = set()
    long_shift: set[int] = set()
    for r in shifts:
        if r.get("typeCode", 517) != 517 or r.get("playerId") is None:
            continue
        try:
            a, b = _sec(r["startTime"]), _sec(r["endTime"])
        except (KeyError, ValueError):
            continue
        per, team, pid = int(r["period"]), r["teamAbbrev"], int(r["playerId"])
        if per > 3:            # overtime is 3-on-3: not lines
            continue
        periods.add(per)
        team_of[pid] = team
        if b - a >= PERIOD_SEC - 60:
            long_shift.add(pid)
        sec = on.setdefault((per, team), [set() for _ in range(PERIOD_SEC)])
        for t in range(max(0, a), min(PERIOD_SEC, b)):
            sec[t].add(pid)

    def is_goalie(p: int) -> bool:
        pos = positions.get(p)
        return pos == "G" if pos else p in long_shift

    teams = sorted(set(team_of.values()))
    es: Counter = Counter()
    pp: Counter = Counter()
    es_pair: Counter = Counter()
    pp_pair: Counter = Counter()
    goalies = {p for p in team_of if is_goalie(p)}
    empty = [set() for _ in range(PERIOD_SEC)]
    for per in periods if len(teams) == 2 else ():
        cols = {tm: on.get((per, tm), empty) for tm in teams}
        for t in range(PERIOD_SEC):
            sk = {tm: [p for p in cols[tm][t] if p not in goalies] for tm in teams}
            n = {tm: len(v) for tm, v in sk.items()}
            a_, b_ = teams
            for tm, other in ((a_, b_), (b_, a_)):
                players = sk[tm]
                if n[tm] == 5 and n[other] == 5:
                    for p in players:
                        es[p] += 1
                    for i, p in enumerate(players):
                        for q in players[i + 1:]:
                            es_pair[frozenset((p, q))] += 1
                elif n[tm] > n[other] >= 3:
                    for p in players:
                        pp[p] += 1
                    for i, p in enumerate(players):
                        for q in players[i + 1:]:
                            pp_pair[frozenset((p, q))] += 1

    out: dict[int, dict] = {}
    for team in teams:
        skaters = [p for p, t in team_of.items() if t == team and not is_goalie(p)]
        fwd = [p for p in skaters if positions.get(p, "C") != "D"]
        dee = [p for p in skaters if positions.get(p) == "D"]
        lines = _group(fwd, es, es_pair, 3, 4) + _group(dee, es, es_pair, 2, 3)
        units = _group(skaters, pp, pp_pair, 5, 2, min_sec=MIN_UNIT_SEC)
        for p in skaters:
            out[p] = {"team": team, "line": None, "pp": None,
                      "es_sec": es[p], "pp_sec": pp[p], "mates": [],
                      "pairs": {q: es_pair[frozenset((p, q))] for q in skaters
                                if q != p and es_pair[frozenset((p, q))]}}
        f_lines = [g for g in lines if g and positions.get(g[0], "C") != "D"]
        d_pairs = [g for g in lines if g and positions.get(g[0]) == "D"]
        for prefix, groups in (("F", f_lines), ("D", d_pairs)):
            groups.sort(key=lambda g: -sum(es[p] for p in g))
            for i, g in enumerate(groups):
                for p in g:
                    out[p]["line"] = f"{prefix}{i + 1}"
                    out[p]["mates"] = [q for q in g if q != p]
        units.sort(key=lambda g: -sum(pp[p] for p in g))
        for i, g in enumerate(units):
            for p in g:
                out[p]["pp"] = f"PP{i + 1}"
    return out


def _group(players: list[int], tot: Counter, pair: Counter, size: int,
           count: int, min_sec: int = 1) -> list[list[int]]:
    """Up to ``count`` groups of ``size``: the busiest unassigned player and
    the ``size - 1`` unassigned players he shared the most time with."""
    left = sorted((p for p in players if tot[p] >= min_sec), key=lambda p: -tot[p])
    groups: list[list[int]] = []
    while left and len(groups) < count:
        lead = left.pop(0)
        mates = sorted(left, key=lambda q: -pair[frozenset((lead, q))])[:size - 1]
        mates = [q for q in mates if pair[frozenset((lead, q))] >= min_sec]
        if not mates and size > 1:
            continue
        for q in mates:
            left.remove(q)
        groups.append([lead, *mates])
    return groups


# ── ingest ────────────────────────────────────────────────────────────

def ingest_deployment(conn: sqlite3.Connection, fetcher, season: int,
                      as_of: str) -> dict[str, int]:
    """Shift charts for each team's last GAMES_PER_TEAM finished games not
    stored yet, then drop games older than that. Returns counts."""
    ensure_schema(conn)
    rows = list(conn.execute(
        "SELECT game_id, game_date, home_abbrev, away_abbrev FROM schedule"
        " WHERE season_id=? AND game_type=2 AND game_date < ?"
        " ORDER BY game_date DESC, game_id DESC", (season, as_of)))
    want: dict[int, str] = {}
    per_team: Counter = Counter()
    for gid, d, h, a in rows:
        if per_team[h] < GAMES_PER_TEAM or per_team[a] < GAMES_PER_TEAM:
            want[gid] = d
        per_team[h] += 1
        per_team[a] += 1
        if all(v >= GAMES_PER_TEAM for v in per_team.values()) and len(per_team) >= 32:
            break
    have = {g for (g,) in conn.execute("SELECT DISTINCT game_id FROM game_deployment")}
    paired = {g for (g,) in conn.execute("SELECT game_id FROM game_pairs_done")}
    # older games of the season still missing pair data, newest first, a
    # batch per refresh so the first one doesn't fetch the whole season
    backfill = [(gid, d) for gid, d, _, _ in rows if gid not in want and gid not in paired]
    backfill = backfill[:PAIRS_BACKFILL]
    positions = dict(conn.execute("SELECT player_id, position FROM players"))
    fetched = pairs_games = 0
    todo = [(gid, d, True) for gid, d in sorted(want.items(), key=lambda x: x[1])
            if gid not in have or gid not in paired]
    todo += [(gid, d, False) for gid, d in backfill]
    for gid, d, recent in todo:
        data = fetcher.get_json(SHIFT_URL.format(gid=gid))
        shifts = data.get("data", []) if isinstance(data, dict) else []
        units = game_units(shifts, positions)
        if not units:
            continue
        if recent and gid not in have:
            conn.executemany(
                "INSERT OR REPLACE INTO game_deployment VALUES (?,?,?,?,?,?,?,?,?)",
                [(gid, d, u["team"], p, u["line"], u["pp"], u["es_sec"], u["pp_sec"],
                  json.dumps(u["mates"])) for p, u in units.items()])
            fetched += 1
        conn.executemany("INSERT OR REPLACE INTO game_pairs VALUES (?,?,?,?)",
                         [(gid, p, q, sec) for p, u in units.items()
                          for q, sec in ({p: u["es_sec"]} | u["pairs"]).items() if sec])
        conn.execute("INSERT OR REPLACE INTO game_pairs_done VALUES (?)", (gid,))
        pairs_games += 1
    if want:
        conn.execute(f"DELETE FROM game_deployment WHERE game_id NOT IN"
                     f" ({','.join('?' * len(want))})", list(want))
    return {"deployment_games": fetched, "pair_games": pairs_games}


def ingest_toi_windows(conn: sqlite3.Connection, fetcher, season: int,
                       as_of: str) -> dict[str, int]:
    """Time-on-ice report summed over the recent and prior windows."""
    ensure_schema(conn)
    end = datetime.date.fromisoformat(as_of) - datetime.timedelta(days=1)
    windows = {
        "recent": (end - datetime.timedelta(days=RECENT_DAYS - 1), end),
        "prior": (end - datetime.timedelta(days=RECENT_DAYS + PRIOR_DAYS - 1),
                  end - datetime.timedelta(days=RECENT_DAYS)),
    }
    conn.execute("DELETE FROM skater_toi_window")
    n = 0
    for name, (a, b) in windows.items():
        f = (f'?isAggregate=true&isGame=true&cayenneExp=gameTypeId=2'
             f' and gameDate>="{a}" and gameDate<="{b} 23:59:59" and seasonId=')
        for r in fetcher.get_paged(f"{TOI_URL}{f}", f"toi_window_{name}", season):
            if r.get("playerId") is None:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO skater_toi_window VALUES (?,?,?,?,?,?,?,?,?)",
                (r["playerId"], name, r.get("gamesPlayed"), r.get("timeOnIce"),
                 r.get("evTimeOnIce"), r.get("ppTimeOnIce"), r.get("shTimeOnIce"),
                 a.isoformat(), b.isoformat()))
            n += 1
    return {"toi_windows": n}


# ── read ──────────────────────────────────────────────────────────────

def _usual(values: list) -> object:
    """Most common value (ties: the better rank)."""
    if not values:
        return None
    c = Counter(values)
    top = max(c.values())
    best = [v for v, k in c.items() if k == top]
    return min(best, key=lambda v: LINE_RANK.get(v, PP_RANK.get(v, 9)))


def load_roles(conn: sqlite3.Connection, as_of: str | None = None) -> dict[int, dict]:
    """{player_id: role} (see the module docstring). Empty when neither
    table has been filled yet."""
    try:
        dep = list(conn.execute(
            "SELECT player_id, game_date, line, pp, es_sec, pp_sec, mates"
            " FROM game_deployment ORDER BY game_date DESC, game_id DESC"))
        toi = list(conn.execute(
            "SELECT player_id, window, gp, toi, ev_toi, pp_toi FROM skater_toi_window"))
    except sqlite3.OperationalError:     # tables not created yet
        return {}
    games: dict[int, list] = {}
    for pid, d, line, pp, es, pps, mates in dep:
        games.setdefault(pid, []).append((d, line, pp, es, pps, mates))
    out: dict[int, dict] = {}
    for pid, gl in games.items():
        last = gl[0]
        before = gl[1:]
        usual_line = _usual([g[1] for g in before if g[1]])
        usual_pp = _usual([g[2] for g in before]) if before else None
        r = {"date": last[0], "line": last[1], "pp": last[2],
             "es_sec": last[3], "pp_sec": last[4],
             "mates": json.loads(last[5] or "[]"),
             "usual_line": usual_line, "usual_pp": usual_pp,
             "games": len(gl), "change": None}
        if before:
            r["change"] = _change(last[1], usual_line, last[2], usual_pp)
        out[pid] = r
    for pid, window, gp, t, ev, pp in toi:
        r = out.setdefault(pid, {"line": None, "pp": None, "mates": [], "games": 0,
                                 "change": None})
        if gp:
            r[f"{window}_toi"] = t / gp
            r[f"{window}_ev_toi"] = (ev or 0) / gp
            r[f"{window}_pp_toi"] = (pp or 0) / gp
            r[f"{window}_gp"] = gp
    return out


def _change(line, usual_line, pp, usual_pp) -> str | None:
    """'up' / 'down' / None: the PP unit decides, then the line."""
    a, b = PP_RANK.get(pp, 3), PP_RANK.get(usual_pp, 3)
    if a != b:
        return "up" if a < b else "down"
    if line and usual_line and line[0] == usual_line[0] and line != usual_line:
        return "up" if LINE_RANK[line] < LINE_RANK[usual_line] else "down"
    return None


def label(role: dict | None) -> str | None:
    """Short tag for tables: 'F2, PP1' (None when nothing is known)."""
    if not role or not (role.get("line") or role.get("pp")):
        return None
    return ", ".join(x for x in (role.get("line"), role.get("pp")) if x)
