"""FastAPI app for the draft board.

Endpoints:
- GET /api/board?league=<name> returns the entire ranked player list in
  one payload. All filtering, sorting, and draft state lives on the
  client (static/board.js).
- GET /api/player/{id} returns the right-side drill-down payload
  (trend chart, confidence band, projection breakdown, schedule load);
  its data helpers live in src/api/player_detail.py.
"""
from __future__ import annotations

import csv
import datetime
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from src.api.player_detail import (
    CATEGORY_LABELS,
    GOALIE_CAT_NAMES,
    SKATER_CAT_NAMES,
    _band_note,
    _breakdown_rows,
    _goalie_actuals,
    _goalie_seasons,
    _per60,
    _schedule_summary,
    _season_label,
    _skater_actuals,
    _skater_gps,
    _skater_seasons,
)
from src.projections.band import compute_band
from src.projections.projections import (
    _pos_group,
    explain,
    project_all,
)
from src.valuation.league import (
    DEFAULT_LEAGUE,
    LeagueConfig,
    _extract_stat,
    load_league,
)
from src.valuation.schedule_value import windowed_values
from src.valuation.valuation import auction_values, valuate

from src.draft.recommend import recommend

from src.api.export import board_to_csv

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "hockey.db"
LEAGUE_DIR = ROOT / "config" / "leagues"
ADP_PATH = ROOT / "data" / "adp.csv"
STATIC_DIR = ROOT / "static"

STALE_THRESHOLD_HOURS = 24.0
REFRESH_COMMAND = "uv run python -m src.ingest.ingest --seasons 1"
DB_NOT_READY_DETAIL = (
    "Database not ready: no ingested skater data. Run the ingest "
    f"({REFRESH_COMMAND}) to populate data/hockey.db."
)


def _require_db() -> None:
    """Raise a clear 500 when the DB is missing or has no skater data.

    Without this the board would either create an empty DB file and fail
    deep in the projection step, or serve a silent empty table. Returning a
    500 with a human-readable message lets the client show an error state
    with a retry path instead of a blank board.
    """
    if not DB_PATH.exists():
        raise HTTPException(status_code=500, detail=DB_NOT_READY_DETAIL)
    conn = sqlite3.connect(DB_PATH)
    try:
        n = conn.execute("SELECT COUNT(*) FROM skater_seasons").fetchone()[0]
    except sqlite3.OperationalError:
        raise HTTPException(status_code=500, detail=DB_NOT_READY_DETAIL)
    finally:
        conn.close()
    if n == 0:
        raise HTTPException(status_code=500, detail=DB_NOT_READY_DETAIL)


def data_freshness(now: datetime.datetime | None = None) -> dict:
    """Data age for the stale-data banner.

    Reads ``MAX(run_at)`` from ``ingest_log`` and reports how old the data
    is. ``stale`` is True when the newest ingest is older than
    ``STALE_THRESHOLD_HOURS`` or when there is no ingest log at all (a fresh,
    empty DB is treated as stale so the banner shows until data lands).
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if not DB_PATH.exists():
        return {"run_at": None, "data_age_hours": None, "stale": True,
                "refresh_command": REFRESH_COMMAND}
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT MAX(run_at) FROM ingest_log").fetchone()
    except sqlite3.OperationalError:
        row = (None,)
    finally:
        conn.close()
    last = row[0] if row and row[0] else None
    if last is None:
        return {"run_at": None, "data_age_hours": None, "stale": True,
                "refresh_command": REFRESH_COMMAND}
    run_at = datetime.datetime.fromisoformat(last)
    age_hours = (now - run_at).total_seconds() / 3600.0
    return {
        "run_at": last,
        "data_age_hours": round(age_hours, 2),
        "stale": age_hours > STALE_THRESHOLD_HOURS,
        "refresh_command": REFRESH_COMMAND,
    }


def _load_leagues() -> dict[str, LeagueConfig]:
    """Load every league config from config/leagues, keyed by name."""
    leagues: dict[str, LeagueConfig] = {}
    for path in sorted(LEAGUE_DIR.glob("*.yaml")):
        cfg = load_league(str(path))
        leagues[cfg.name] = cfg
    return leagues


def _next_season_id() -> int:
    """Next season to project = latest ingested season + 10001.

    season_id encodes start_year*10000 + end_year (e.g. 20252026 = 2025-26).
    The following season 2026-27 is 20262027 = 20252026 + 10001, so we
    add 10001 (not 10000) to roll from one season to the next.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT MAX(season_id) FROM skater_seasons").fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        raise HTTPException(
            status_code=500, detail="No skater_seasons rows in database"
        )
    return int(row[0]) + 10001


def _load_adp() -> dict[str, float] | None:
    """Load ADP values (full_name -> avg draft position) if data/adp.csv exists."""
    if not ADP_PATH.exists():
        return None
    adp: dict[str, float] = {}
    with open(ADP_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                name = row["full_name"].strip()
                value = float(row["adp"])
            except (KeyError, TypeError, ValueError):
                continue
            if name:
                adp[name] = value
    return adp or None


def build_board(league: str, adp: dict[str, float] | None = None) -> dict:
    """Build the full board payload for one league.

    ``adp`` may be passed explicitly (used by tests); when None it is
    loaded from data/adp.csv if that file exists, otherwise ADP is
    hidden (``has_adp`` False). Returns meta plus a flat player list
    sorted by VORP desc (rank asc).
    """
    leagues = _load_leagues()
    if league not in leagues:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown league '{league}'. Available: {sorted(leagues)}",
        )
    cfg = leagues[league]
    _require_db()

    season = _next_season_id()
    conn = sqlite3.connect(DB_PATH)
    try:
        players = {
            row[0]: (row[1], row[2], row[3])
            for row in conn.execute(
                "SELECT player_id, full_name, position, current_team_abbrev "
                "FROM players"
            )
        }
    finally:
        conn.close()

    projs = {p.player_id: p for p in project_all(season, str(DB_PATH))}
    vals = windowed_values(list(projs.values()), cfg)
    auction = auction_values(vals, cfg, key="total_value")

    if adp is None:
        adp = _load_adp()

    # Keep only players that can be shown (a players row with a name), then
    # rank among those so the rank column is gapless for the draft board.
    # 1-based total_value rank; ties broken by player_id for determinism.
    ordered = [
        v
        for v in sorted(vals, key=lambda v: (-v.total_value, v.player_id))
        if (info := players.get(v.player_id)) is not None and info[0] is not None
    ]
    value_rank = {v.player_id: i + 1 for i, v in enumerate(ordered)}

    adp_rank: dict[int, int] | None = None
    if adp:
        ranked = []
        for v in ordered:
            name = players[v.player_id][0]
            if adp.get(name) is not None:
                ranked.append((adp[name], v.player_id))
        ranked.sort(key=lambda t: (t[0], t[1]))
        adp_rank = {pid: i + 1 for i, (_, pid) in enumerate(ranked)}

    cat_keys = (
        cfg.categories
        if cfg.categories is not None
        else list(cfg.points_per_stat.keys())
    )
    out = []
    for v in ordered:
        name, pos, team = players[v.player_id]
        p = projs[v.player_id]
        a = adp.get(name) if adp else None
        delta = None
        if adp_rank is not None and a is not None:
            delta = adp_rank[v.player_id] - value_rank[v.player_id]
        out.append(
            {
                "id": v.player_id,
                "name": name,
                "pos": pos,
                "team": team or "-",
                "rank": value_rank[v.player_id],
                "vorp": round(v.total_value, 2),
                "vorp_regular": round(v.vorp_regular, 2),
                "vorp_playoff": round(v.vorp_playoff, 2),
                "dollars": round(auction.get(v.player_id, 0.0), 1),
                "gp_regular": round(v.gp_regular, 1),
                "gp_playoff": round(v.gp_playoff, 1),
                "stats": {k: round(_extract_stat(p, k), 1) for k in cat_keys},
                "adp": round(a, 1) if a is not None else None,
                "delta": delta,
            }
        )

    return {
        "league": cfg.name,
        "leagues": sorted(leagues),
        "season": season,
        "team_count": cfg.team_count,
        "scoring_type": cfg.scoring_type,
        "categories": cat_keys,
        "roster_slots": cfg.roster_slots,
        "has_adp": adp is not None and len(adp) > 0,
        "count": len(out),
        "players": out,
        "data_freshness": data_freshness(),
    }


def _parse_ids(raw: str) -> list[int]:
    out: list[int] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(int(tok))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid player id '{tok}'")
    return out


def build_recommendations(
    league: str,
    drafted_by_me: list[int],
    drafted_all: list[int],
    round_no: int,
) -> dict:
    """Full pipeline for the live draft board: values + marginal re-rank."""
    leagues = _load_leagues()
    if league not in leagues:
        raise HTTPException(status_code=404, detail=f"Unknown league '{league}'")
    cfg = leagues[league]
    season = _next_season_id()
    projs = {p.player_id: p for p in project_all(season, str(DB_PATH))}
    vals = valuate(list(projs.values()), cfg)
    recs = recommend(drafted_by_me, drafted_all, vals, cfg, round_no)
    return {
        "league": cfg.name,
        "round": round_no,
        "recommendations": [
            {
                "name": r.name,
                "player_id": r.player_id,
                "marginal_value": round(r.marginal_value, 2),
                "reason": r.reason,
            }
            for r in recs
        ],
    }


# ─── Player drill-down ──────────────────────────────────────────────
# Data helpers (per-season actuals, season labels, per-60 scaling, band
# note, schedule summary, breakdown rows) live in
# src/api/player_detail.py; only build_player_detail stays here.


def build_player_detail(player_id: int, league: str) -> dict:
    """Player drill-down payload: trend data, breakdown, schedule.

    Returns ``{}`` when the player has no projection data (no qualifying
    seasons). Otherwise returns the board row for ``league`` plus:

    - ``labels``: one x-axis label per point, one per past season
      (oldest->newest, e.g. "23-24") plus the trailing "proj" point.
    - ``band_note``: caption string when the band needs explaining
      (single season of data), else None.
    - ``categories``: per-category chart data. ``actuals``/``projection``/
      ``band`` are the raw season totals; ``actual``/``proj``/``hi``/``lo``
      are the chart values — per-60 rates for skaters, per-season values
      for goalies (save_pct a fraction).
    - the deterministic breakdown rows and the team schedule summary for
      the upcoming season.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT player_id, full_name, position, current_team_abbrev "
            "FROM players WHERE player_id=?", (player_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(
            status_code=404, detail=f"Unknown player id {player_id}")
    pid, name, pos, team = row
    season = _next_season_id()
    is_goalie = _pos_group(pos) == "G"

    terms = explain(pid, season, str(DB_PATH))
    if not terms:
        return {}

    board_row = next(
        (p for p in build_board(league)["players"] if p["id"] == pid), None)

    conn = sqlite3.connect(DB_PATH)
    try:
        if is_goalie:
            seasons = _goalie_seasons(conn, pid, season)
            cats, actuals_fn = GOALIE_CAT_NAMES, _goalie_actuals
            gps = []
        else:
            seasons = _skater_seasons(conn, pid, season)
            cats, actuals_fn = SKATER_CAT_NAMES, _skater_actuals
            gps = _skater_gps(conn, pid, season)
        labels = [_season_label(s) for s in seasons] + ["proj"]
        band_note = _band_note(seasons)
        categories = []
        for cat in cats:
            actuals = actuals_fn(conn, pid, cat, season)
            lo, hi = compute_band(
                final=terms["final"][cat],
                season_actuals=actuals,
                raw=terms["raw_rates"].get(cat),
                regressed=terms["regressed_rate"].get(cat),
                age_factor=terms["age_factor"],
                proj_gp=terms["proj_gp"],
                category=cat,
                is_goalie=is_goalie,
            )
            if is_goalie:
                # Goalie categories are per-season values (save_pct a
                # fraction); per-60 only applies to skater ice time.
                actual, proj, hi60, lo60 = (
                    actuals, terms["final"][cat], hi, lo)
            else:
                actual = [_per60(a, g) for a, g in zip(actuals, gps)]
                proj = _per60(terms["final"][cat], terms["proj_gp"])
                hi60 = _per60(hi, terms["proj_gp"])
                lo60 = _per60(lo, terms["proj_gp"])
            categories.append({
                "key": cat,
                "label": CATEGORY_LABELS[cat],
                "actuals": actuals,
                "projection": terms["final"][cat],
                "band": [round(lo, 6), round(hi, 6)],
                "actual": [round(v, 6) for v in actual],
                "proj": round(proj, 6),
                "hi": round(hi60, 6),
                "lo": round(lo60, 6),
            })
        schedule = _schedule_summary(conn, team, season)
    finally:
        conn.close()

    return {
        "id": pid,
        "name": name,
        "pos": pos,
        "team": team or "-",
        "season": season,
        "is_goalie": is_goalie,
        "board_row": board_row,
        "seasons": seasons,
        "labels": labels,
        "band_note": band_note,
        "categories": categories,
        "breakdown": _breakdown_rows(terms, is_goalie),
        "schedule": schedule,
    }


app = FastAPI(title="NHL Draft Board")


@app.get("/api/board")
def get_board(league: str | None = None) -> dict:
    """Full ranked board for one league (client handles everything else)."""
    default = Path(DEFAULT_LEAGUE).stem
    return build_board(league or default)


@app.get("/api/recommend")
def get_recommend(
    league: str | None = None,
    round: int = 1,
    drafted_by_me: str = "",
    drafted_all: str = "",
) -> dict:
    """Marginal-value re-rank of the available pool for the live draft.

    ?league=roto_14&round=3&drafted_by_me=101,202&drafted_all=101,202,303
    """
    default = Path(DEFAULT_LEAGUE).stem
    return build_recommendations(
        league or default,
        _parse_ids(drafted_by_me),
        _parse_ids(drafted_all),
        round,
    )


@app.get("/api/export")
def export_board(
    league: str | None = None,
    format: str = "csv",
) -> Response:
    """Export the board as CSV (default) or JSON.

    CSV: flat, one row per player, category stats as columns. JSON: the full
    board payload (same shape as /api/board).
    """
    default = Path(DEFAULT_LEAGUE).stem
    board = build_board(league or default)
    if format == "json":
        return JSONResponse(board)
    if format == "csv":
        return PlainTextResponse(
            board_to_csv(board), media_type="text/csv",
        )
    raise HTTPException(
        status_code=422,
        detail=f"Unknown format '{format}' (use 'csv' or 'json')",
    )


@app.get("/api/player/{player_id}")
def get_player(player_id: int, league: str | None = None) -> dict:
    """Right-side drill-down payload for one player id.

    ?league=roto_14 ; returns {} when the player has no projection data.
    """
    default = Path(DEFAULT_LEAGUE).stem
    return build_player_detail(player_id, league or default)


@app.get("/")
def index() -> FileResponse:
    # no-store: the HTML is the app shell; a browser-cached old copy with
    # fresh board.js breaks element lookups, so never serve it from cache.
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
