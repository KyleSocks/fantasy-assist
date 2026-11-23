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

Assist luck. A skater's assists at 5v5 come from his linemates' goals, and
the share of on-ice shots that go in (on-ice SH%) is mostly luck: it swings
a lot and barely carries from one half-season to the next. So, like
shooting % for goals (model.py), it's regressed toward the league average:

  on-ice shots   S = 5v5 minutes x ``onice_shots_per_60`` / 60 (the stats
                 API doesn't give on-ice shots, so a league-typical rate)
  regressed SH%  r = (league x k + actual x S) / (k + S), k by position
                 (``onice_sh_pct_k_shots``; defencemen higher, their on-ice
                 SH% is even less theirs)
  expected A5    5v5 assists x r / actual
  blended assists move by (expected A5 - A5) / (k_assists + gp): the change
                 to this season's total, at the weight the season has in
                 the blend (model.blend), so it fades out where the prior
                 dominates and never touches power-play assists.

``league`` is the 5v5-ice-time-weighted average on-ice SH% of everyone in
the report. Players with no 5v5 assists or an on-ice SH% of 0 are left
alone (nothing to scale).
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


def onice_assists(line: Line, a: dict, league: float, cfg: dict, k_assists: float) -> dict | None:
    """The assist adjustment for one skater (module docstring), or None."""
    actual, a5, gp = a.get("onice_sh_pct"), a.get("assists_5v5"), a.get("gp") or 0
    if not actual or not a5 or not gp or not a.get("toi_5v5_pg"):
        return None
    minutes = a["toi_5v5_pg"] * gp / 60
    shots = minutes * cfg.get("onice_shots_per_60", 28.0) / 60
    ks = cfg.get("onice_sh_pct_k_shots") or {}
    k = ks.get("D" if line.position == "D" else "F", 1000)
    reg = (league * k + actual * shots) / (k + shots)
    expected = a5 * reg / actual
    delta = (expected - a5) / (k_assists + gp)
    return {"pct": actual, "league": league, "regressed": reg, "shots": shots,
            "assists_5v5": a5, "expected_5v5": expected, "delta_pg": delta}


def apply(lines: dict[int, Line], adv: dict[int, dict], cfg: dict, k_games: dict) -> None:
    """Attach ``extra["adv"]`` to every player with advanced stats and, when
    ``onice_sh_pct_k_shots`` is set, move skaters' blended assists for
    on-ice shooting luck (``extra["onice"]``)."""
    lg = league_onice(adv)
    for pid, a in adv.items():
        ln = lines.get(pid)
        if ln is None:
            continue
        ln.extra["adv"] = a
        if ln.is_goalie or lg is None or not cfg.get("onice_sh_pct_k_shots"):
            continue
        o = onice_assists(ln, a, lg, cfg, k_games.get("assists", 40))
        if o is None:
            continue
        ln.blended["assists"] = max(0.0, ln.blended["assists"] + o["delta_pg"])
        ln.extra["onice"] = o
