"""Advanced stats from the NHL stats API (season to date), and the one that
moves the projection: on-ice shooting % at 5-on-5.

Reports (api.nhle.com/stats/rest/en):
  skater/percentages   Corsi (SAT%), relative Corsi, 5v5 TOI per game,
                       5v5 on-ice save %, PDO (on-ice SH% + SV% at 5v5),
                       5v5 offensive-zone start %
  skater/scoringRates  5v5 goals / assists / points and per-60 rates,
                       5v5 on-ice shooting %, primary assists
  skater/puckPossessions  individual shots and shot attempts per 60
  goalie/advanced      quality starts, quality start %, shots against / 60

Assist luck. A skater's assists at 5v5 come from his linemates' goals. How
often their shots go in with him on the ice (on-ice SH%) depends on who
they are -- skating with snipers runs high every year -- and beyond that
is mostly luck. So the target isn't the league average but what the guys
he actually plays with usually shoot:

  career SH%     each skater's goals / shots over his past seasons
                 (skater_seasons, this season left out), with
                 ``career_sh_pct_k_shots`` shots of his position's average
                 mixed in so a rookie's 3-for-10 doesn't count as 30%
  linemate mix   over this season's games (game_pairs, from shift charts),
                 everyone who was on the ice with him at 5v5 -- himself
                 included -- weighted by seconds together x his career
                 shots per second: each one's share of the shots taken
                 while he's out there
  baseline       that mix of career SH%s, scaled so the league as a whole
                 averages the league's 5v5 on-ice SH% (career SH% is all
                 situations, power plays included, and runs higher)
  on-ice shots   S = 5v5 minutes x ``onice_shots_per_60`` / 60 (the stats
                 API doesn't give on-ice shots, so a league-typical rate)
  regressed SH%  r = (baseline x k + actual x S) / (k + S), k by position
                 (``onice_sh_pct_k_shots``; defencemen higher, their on-ice
                 SH% is even less theirs)
  expected A5    5v5 assists x r / actual
  blended assists move by (expected A5 - A5) / (k_assists + gp): the change
                 to this season's total, at the weight the season has in
                 the blend (model.blend), so it fades out where the prior
                 dominates and never touches power-play assists.

``league`` is the 5v5-ice-time-weighted average on-ice SH% of everyone in
the report. A player with no shift-chart pairs yet falls back to the league
average. Players with no 5v5 assists or an on-ice SH% of 0 are left alone
(nothing to scale).
"""
from __future__ import annotations

import sqlite3

from .model import Line

SCHEMA = """
CREATE TABLE IF NOT EXISTS skater_advanced (
    player_id INTEGER PRIMARY KEY, season_id INTEGER, gp INTEGER,
    toi_5v5_pg REAL, sat_pct REAL, sat_rel REAL, usat_pct REAL,
    onice_sh_pct REAL, onice_sv_pct REAL, pdo REAL, oz_start_pct REAL,
    goals_5v5 INTEGER, assists_5v5 INTEGER, primary_assists_5v5 INTEGER,
    points_per60_5v5 REAL, ishots_per60 REAL, isat_per60 REAL);
CREATE TABLE IF NOT EXISTS goalie_advanced (
    player_id INTEGER PRIMARY KEY, season_id INTEGER, gs INTEGER,
    quality_starts INTEGER, qs_pct REAL, shots_against_per60 REAL,
    goals_for_avg REAL);
"""
SK_COLS = ("gp", "toi_5v5_pg", "sat_pct", "sat_rel", "usat_pct", "onice_sh_pct",
           "onice_sv_pct", "pdo", "oz_start_pct", "goals_5v5", "assists_5v5",
           "primary_assists_5v5", "points_per60_5v5", "ishots_per60", "isat_per60")
G_COLS = ("gs", "quality_starts", "qs_pct", "shots_against_per60", "goals_for_avg")


# fields that add up across a traded player's rows; everything else numeric
# is a rate or percentage and gets averaged
COUNTS = {"gamesPlayed", "gamesStarted", "goals5v5", "assists5v5", "points5v5",
          "primaryAssists5v5", "secondaryAssists5v5", "qualityStart", "completeGames",
          "incompleteGames", "goalsAgainst", "goalsFor", "regulationWins",
          "regulationLosses", "timeOnIce"}


def _merge(rows: list[dict], weight: str) -> dict[int, dict]:
    """One record per player: a traded player has a row per team. Counts
    add up; rates and percentages are averaged, weighted by ``weight``
    (5v5 TOI for skaters, games started for goalies)."""
    by: dict[int, list[dict]] = {}
    for r in rows:
        by.setdefault(r["playerId"], []).append(r)
    out = {}
    for pid, rs in by.items():
        if len(rs) == 1:
            out[pid] = dict(rs[0])
            continue
        w = [float(r.get(weight) or 0) * float(r.get("gamesPlayed") or 0) or 1e-9 for r in rs]
        tw = sum(w)
        m = {}
        for k in rs[0]:
            vals = [r.get(k) for r in rs]
            if k in COUNTS:
                m[k] = sum(v or 0 for v in vals)
            elif k not in ("playerId", "seasonId") and all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
                m[k] = sum(v * x for v, x in zip(vals, w)) / tw
            else:
                m[k] = vals[-1]
        out[pid] = m
    return out


def ingest_advanced(conn: sqlite3.Connection, fetcher, season: int, as_of: str) -> dict[str, int]:
    from .ingest import _BASE, _FILTER
    conn.executescript(SCHEMA)
    pct = _merge(fetcher.get_paged(f"{_BASE}/skater/percentages{_FILTER}",
                                   "adv_skater_percentages", season), "timeOnIcePerGame5v5")
    rates = _merge(fetcher.get_paged(f"{_BASE}/skater/scoringRates{_FILTER}",
                                     "adv_skater_scoring_rates", season), "timeOnIcePerGame5v5")
    poss = _merge(fetcher.get_paged(f"{_BASE}/skater/puckPossessions{_FILTER}",
                                    "adv_skater_possession", season), "timeOnIcePerGame5v5")
    gadv = _merge(fetcher.get_paged(f"{_BASE}/goalie/advanced{_FILTER}",
                                    "adv_goalie", season), "gamesStarted")
    rows = []
    for pid in set(pct) | set(rates):
        p, r, q = pct.get(pid, {}), rates.get(pid, {}), poss.get(pid, {})
        rows.append((pid, season, p.get("gamesPlayed") or r.get("gamesPlayed"),
                     p.get("timeOnIcePerGame5v5") or r.get("timeOnIcePerGame5v5"),
                     p.get("satPercentage"), p.get("satRelative"), p.get("usatPercentage"),
                     r.get("onIceShootingPct5v5"), p.get("skaterSavePct5v5"),
                     p.get("skaterShootingPlusSavePct5v5"), p.get("zoneStartPct5v5"),
                     r.get("goals5v5"), r.get("assists5v5"), r.get("primaryAssists5v5"),
                     r.get("pointsPer605v5"), q.get("individualShotsForPer60"),
                     q.get("individualSatForPer60")))
    grows = [(pid, season, g.get("gamesStarted"), g.get("qualityStart"), g.get("qualityStartsPct"),
              g.get("shotsAgainstPer60"), g.get("goalsForAverage")) for pid, g in gadv.items()]
    conn.execute("DELETE FROM skater_advanced")
    conn.execute("DELETE FROM goalie_advanced")
    conn.executemany(f"INSERT INTO skater_advanced VALUES ({','.join('?' * 17)})", rows)
    conn.executemany("INSERT INTO goalie_advanced VALUES (?,?,?,?,?,?,?)", grows)
    return {"advanced_rows": len(rows) + len(grows)}


def load_advanced(conn: sqlite3.Connection, season: int) -> dict[int, dict]:
    """{pid: {col: value}}; {} before the first refresh that fills it."""
    out: dict[int, dict] = {}
    try:
        for r in conn.execute(f"SELECT player_id, {', '.join(SK_COLS)} FROM skater_advanced"
                              " WHERE season_id=?", (season,)):
            out[r[0]] = dict(zip(SK_COLS, r[1:]))
        for r in conn.execute(f"SELECT player_id, {', '.join(G_COLS)} FROM goalie_advanced"
                              " WHERE season_id=?", (season,)):
            out[r[0]] = dict(zip(G_COLS, r[1:]))
    except sqlite3.OperationalError:
        return {}
    return out


def load_inputs(conn: sqlite3.Connection, season: int, adv: dict[int, dict],
                cfg: dict) -> dict[int, dict]:
    """Linemate baselines for everyone with pair data (see apply)."""
    lg = league_onice(adv)
    prs = pairs(conn, season)
    if lg is None or not prs:
        return {}
    try:
        car = career(conn, season, cfg.get("career_sh_pct_k_shots", 100))
    except sqlite3.OperationalError:
        car = {}
    positions = dict(conn.execute("SELECT player_id, position FROM players"))
    return linemate_baselines(prs, car, positions, lg)


def league_onice(adv: dict[int, dict]) -> float | None:
    """5v5-TOI-weighted average on-ice SH%."""
    num = den = 0.0
    for a in adv.values():
        if a.get("onice_sh_pct") is None or not a.get("toi_5v5_pg"):
            continue
        w = a["toi_5v5_pg"] * (a.get("gp") or 0)
        num += a["onice_sh_pct"] * w
        den += w
    return num / den if den else None


def career(conn: sqlite3.Connection, season: int, k: float) -> dict[int, dict]:
    """{pid: {"sh": career SH% (shrunk), "spm": shots per second, "shots"}}
    from past seasons; position averages for F and D fill thin records."""
    pos = dict(conn.execute("SELECT player_id, position FROM players"))
    rows = list(conn.execute(
        "SELECT player_id, SUM(goals), SUM(shots), SUM(toi_sec) FROM skater_seasons"
        " WHERE season_id < ? GROUP BY player_id", (season,)))
    tot: dict[str, list[float]] = {"F": [0, 0, 0], "D": [0, 0, 0]}
    for pid, g, sh, t in rows:
        grp = "D" if pos.get(pid) == "D" else "F"
        tot[grp][0] += g or 0
        tot[grp][1] += sh or 0
        tot[grp][2] += t or 0
    avg = {grp: (v[0] / v[1] if v[1] else 0.09, v[1] / v[2] if v[2] else 0.0015)
           for grp, v in tot.items()}
    out = {}
    for pid, g, sh, t in rows:
        a_sh, a_spm = avg["D" if pos.get(pid) == "D" else "F"]
        out[pid] = {"sh": ((g or 0) + k * a_sh) / ((sh or 0) + k),
                    "spm": (sh / t) if sh and t else a_spm, "shots": sh or 0}
    out["_avg"] = avg
    return out


def pairs(conn: sqlite3.Connection, season: int) -> dict[int, dict[int, int]]:
    """{pid: {mate (or himself): 5v5 seconds together}} over this season."""
    out: dict[int, dict[int, int]] = {}
    try:
        for p, q, sec in conn.execute(
                "SELECT gp.player_id, gp.mate_id, SUM(gp.sec) FROM game_pairs gp"
                " JOIN schedule s ON s.game_id = gp.game_id WHERE s.season_id=?"
                " GROUP BY gp.player_id, gp.mate_id", (season,)):
            out.setdefault(p, {})[q] = sec
    except sqlite3.OperationalError:
        return {}
    return out


def linemate_baselines(prs: dict[int, dict[int, int]], car: dict, positions: dict[int, str],
                       league: float) -> dict[int, dict]:
    """{pid: {"raw": mix of career SH%s, "baseline": scaled to the league's
    5v5 on-ice level, "mates": [(mate, share of shots), ...] biggest first}}."""
    avg = car.get("_avg") or {"F": (0.09, 0.0015), "D": (0.05, 0.0012)}

    def info(q):
        c = car.get(q)
        if c:
            return c["sh"], c["spm"]
        return avg["D" if positions.get(q) == "D" else "F"]

    raw = {}
    for p, with_ in prs.items():
        if not with_.get(p):
            continue
        w = {q: sec * info(q)[1] for q, sec in with_.items()}
        tw = sum(w.values())
        if tw <= 0:
            continue
        raw[p] = (sum(w[q] * info(q)[0] for q in w) / tw,
                  sorted(((q, x / tw) for q, x in w.items() if q != p), key=lambda t: -t[1]))
    own = {p: prs[p][p] for p in raw}
    tot = sum(own.values())
    mean = sum(raw[p][0] * own[p] for p in raw) / tot if tot else 0.0
    scale = league / mean if mean else 1.0
    return {p: {"raw": r, "baseline": r * scale, "mates": m[:4]} for p, (r, m) in raw.items()}


def onice_assists(line: Line, a: dict, league: float, cfg: dict, k_assists: float,
                  baseline: float | None = None) -> dict | None:
    """The assist adjustment for one skater (module docstring), or None.
    ``baseline``: what his linemates usually shoot (None: league average)."""
    actual, a5, gp = a.get("onice_sh_pct"), a.get("assists_5v5"), a.get("gp") or 0
    if not actual or not a5 or not gp or not a.get("toi_5v5_pg"):
        return None
    minutes = a["toi_5v5_pg"] * gp / 60
    shots = minutes * cfg.get("onice_shots_per_60", 28.0) / 60
    ks = cfg.get("onice_sh_pct_k_shots") or {}
    k = ks.get("D" if line.position == "D" else "F", 1000)
    target = league if baseline is None else baseline
    reg = (target * k + actual * shots) / (k + shots)
    expected = a5 * reg / actual
    delta = (expected - a5) / (k_assists + gp)
    return {"pct": actual, "league": league, "baseline": target,
            "basis": "league" if baseline is None else "linemates",
            "regressed": reg, "shots": shots,
            "assists_5v5": a5, "expected_5v5": expected, "delta_pg": delta}


def apply(lines: dict[int, Line], adv: dict[int, dict], cfg: dict, k_games: dict,
          bases: dict[int, dict] | None = None) -> None:
    """Attach ``extra["adv"]`` to every player with advanced stats and, when
    ``onice_sh_pct_k_shots`` is set, move skaters' blended assists for
    on-ice shooting luck (``extra["onice"]``). ``bases``: linemate_baselines
    (missing players use the league average)."""
    lg = league_onice(adv)
    bases = bases or {}
    for pid, a in adv.items():
        ln = lines.get(pid)
        if ln is None:
            continue
        ln.extra["adv"] = a
        if ln.is_goalie or lg is None or not cfg.get("onice_sh_pct_k_shots"):
            continue
        b = bases.get(pid)
        o = onice_assists(ln, a, lg, cfg, k_games.get("assists", 40),
                          b["baseline"] if b else None)
        if o is None:
            continue
        if b:
            o["mates"] = [{"id": q, "name": lines[q].name if q in lines else str(q),
                           "share": sh} for q, sh in b["mates"]]
        ln.blended["assists"] = max(0.0, ln.blended["assists"] + o["delta_pg"])
        ln.extra["onice"] = o
