"""Player drill-down data helpers for the draft board API.

Data functions behind ``GET /api/player/{id}`` (``build_player_detail``
in ``routes.py``): per-season actuals for skater and goalie categories,
season labels, per-60 scaling, the confidence-band caption, the team
schedule load, and the deterministic projection-terms breakdown rows.
"""
from __future__ import annotations

import sqlite3

from src.projections.projections import SKATER_CATEGORIES

SKATER_CAT_NAMES = [c[0] for c in SKATER_CATEGORIES]
GOALIE_CAT_NAMES = ["wins", "save_pct", "shutouts", "games_started",
                    "goals_against", "saves"]

CATEGORY_LABELS = {
    "goals": "Goals", "assists": "Assists", "shots": "Shots",
    "blocks": "Blocks", "hits": "Hits", "pp_points": "PP Points",
    "wins": "Wins", "save_pct": "Save %", "shutouts": "Shutouts",
    "games_started": "Games Started", "goals_against": "Goals Against",
    "saves": "Saves",
}


def _skater_seasons(conn: sqlite3.Connection, pid: int, season: int) -> list[int]:
    """The player's up-to-3 most recent ingested seasons (oldest->newest)."""
    rows = conn.execute(
        "SELECT season_id FROM skater_seasons "
        "WHERE player_id=? AND season_id < ? AND gp > 0 "
        "ORDER BY season_id DESC LIMIT 3", (pid, season)).fetchall()
    return [r[0] for r in reversed(rows)]


def _skater_actuals(conn: sqlite3.Connection, pid: int, cat: str,
                    season: int) -> list[float]:
    """Per-season totals for a skater category (oldest->newest, <=3 seasons)."""
    rows = conn.execute(
        f"SELECT {cat} FROM skater_seasons "
        "WHERE player_id=? AND season_id < ? AND gp > 0 "
        "ORDER BY season_id DESC LIMIT 3", (pid, season)).fetchall()
    return [r[0] or 0 for r in reversed(rows)]


def _goalie_seasons(conn: sqlite3.Connection, pid: int, season: int) -> list[int]:
    rows = conn.execute(
        "SELECT season_id FROM goalie_seasons "
        "WHERE player_id=? AND season_id < ? AND gp > 0 "
        "ORDER BY season_id DESC LIMIT 3", (pid, season)).fetchall()
    return [r[0] for r in reversed(rows)]


def _goalie_actuals(conn: sqlite3.Connection, pid: int, cat: str,
                    season: int) -> list[float]:
    """Per-season actuals for a goalie category (oldest->newest, <=3 seasons).

    save_pct is a per-season fraction (saves / shots_against); other
    categories are per-season totals.
    """
    rows = conn.execute(
        "SELECT saves, shots_against, wins, shutouts, games_started, "
        "goals_against FROM goalie_seasons "
        "WHERE player_id=? AND season_id < ? AND gp > 0 "
        "ORDER BY season_id DESC LIMIT 3", (pid, season)).fetchall()
    out: list[float] = []
    for r in reversed(rows):
        if cat == "save_pct":
            sv, sa = r[0], r[1]
            out.append(sv / sa if sa > 0 else 0.0)
        else:
            idx = {"wins": 2, "shutouts": 3, "games_started": 4,
                   "goals_against": 5, "saves": 0}[cat]
            out.append(r[idx] or 0)
    return out


def _skater_gps(conn: sqlite3.Connection, pid: int, season: int) -> list[int]:
    """Per-season games played for a skater (oldest->newest, <=3 seasons).

    Same row set and order as ``_skater_seasons`` so the lists zip safely.
    """
    rows = conn.execute(
        "SELECT gp FROM skater_seasons "
        "WHERE player_id=? AND season_id < ? AND gp > 0 "
        "ORDER BY season_id DESC LIMIT 3", (pid, season)).fetchall()
    return [r[0] for r in reversed(rows)]


def _season_label(season_id: int) -> str:
    """Human label for a season_id, e.g. 20232024 -> '23-24'."""
    return f"{(season_id // 10000) % 100:02d}-{season_id % 10000 % 100:02d}"


def _per60(value: float, gp: float) -> float:
    """Scale a season total to per-60 (per 60 minutes of ice time)."""
    return value / gp * 60 if gp > 0 else 0.0


def _band_note(seasons: list[int]) -> str | None:
    """Caption for the confidence band; None when nothing needs explaining."""
    if len(seasons) == 1:
        return "Band widened: only one season of qualifying data."
    return None


def _schedule_summary(conn: sqlite3.Connection, team: str | None,
                      season: int) -> dict | None:
    """Upcoming-season schedule load for a team, bucketed by month."""
    if not team:
        return None
    total = conn.execute(
        "SELECT COUNT(*) FROM schedule WHERE season_id=? AND "
        "(home_abbrev=? OR away_abbrev=?)", (season, team, team)).fetchone()[0]
    monthly = conn.execute(
        "SELECT substr(game_date,1,7) m, COUNT(*) c FROM schedule WHERE "
        "season_id=? AND (home_abbrev=? OR away_abbrev=?) GROUP BY 1 ORDER BY 1",
        (season, team, team)).fetchall()
    if not monthly:
        return {"team": team, "total": total, "monthly": [],
                "heaviest_month": None, "heaviest_games": 0,
                "lightest_month": None, "lightest_games": 0}
    heavy = max(monthly, key=lambda r: r[1])
    light = min(monthly, key=lambda r: r[1])
    return {
        "team": team,
        "total": total,
        "monthly": [{"month": r[0], "games": r[1]} for r in monthly],
        "heaviest_month": heavy[0],
        "heaviest_games": heavy[1],
        "lightest_month": light[0],
        "lightest_games": light[1],
    }


def _breakdown_rows(terms: dict, is_goalie: bool) -> list[dict]:
    """Deterministic explanation rows for the projection terms table."""
    rows: list[dict] = []
    weights = terms.get("season_weights", [])
    rows.append({"term": "obs_age (weighted mean age)",
                 "value": terms["obs_age"]})
    rows.append({"term": "L(obs_age)", "value": terms["L_obs"]})
    rows.append({"term": "L(proj_age)", "value": terms["L_proj"]})
    rows.append({"term": "age adjustment L(proj)/L(obs)",
                 "value": terms["age_adj"]})
    rows.append({"term": "projected GP", "value": terms["proj_gp"]})
    rows.append({"term": "projected TOI/GP", "value": terms["proj_toi_per_gp"]})
    rows.append({"term": "season weights",
                 "value": " / ".join(f"{w:.2f}" for w in weights)})
    final = terms["final"]
    if is_goalie:
        raw, regressed = terms["raw_rates"], terms["regressed_rate"]
        rows.append({"term": "save_pct: raw rate", "value": raw["save_pct"]})
        rows.append({"term": "save_pct: regressed rate",
                     "value": regressed["save_pct"]})
        rows.append({"term": "save_pct: final", "value": final["save_pct"]})
        rate_key = {"goals_against": "ga_per_start", "saves": "sv_per_start"}
        for cat in GOALIE_CAT_NAMES:
            if cat == "save_pct":
                continue
            rows.append({"term": f"{cat}: final", "value": final[cat]})
            rows.append({"term": f"{cat}: rate",
                         "value": raw.get(rate_key.get(cat), 0.0)})
    else:
        raw, regressed = terms["raw_rates"], terms["regressed_rate"]
        pos_mean, k, n = terms["pos_mean"], terms["k"], terms["n"]
        for cat in SKATER_CAT_NAMES:
            rows.append({"term": f"{cat}: raw rate", "value": raw[cat]})
            rows.append({"term": f"{cat}: position mean",
                         "value": pos_mean[cat]})
            rows.append({"term": f"{cat}: k (regression)", "value": k[cat]})
            rows.append({"term": f"{cat}: n (season GP)", "value": n[cat]})
            rows.append({"term": f"{cat}: regressed rate",
                         "value": regressed[cat]})
            rows.append({"term": f"{cat}: final", "value": final[cat]})
    return rows

