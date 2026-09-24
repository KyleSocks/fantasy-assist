"""Tests for src/api/routes.py — build_board payload and HTTP endpoints.

Expected values are hand-computed from the ingested database
(data/hockey.db) for season 20262027 and verified against live output of
``uv run python data/_more_facts.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.player_detail import _breakdown_rows
from src.api.routes import (
    app,
    build_board,
    build_player_detail,
)
from src.projections.projections import explain

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "hockey.db"


# ─── Fixtures (module-scoped: each board is built once and reused) ───


@pytest.fixture(scope="module")
def kyle_board() -> dict:
    """Full board payload for the default points league (kyle_12_points)."""
    return build_board("kyle_12_points")


@pytest.fixture(scope="module")
def roto_board() -> dict:
    """Full board payload for the category (roto) league (roto_14)."""
    return build_board("roto_14")


@pytest.fixture(scope="module")
def points_board() -> dict:
    return build_board("points_10")


@pytest.fixture(scope="module")
def standard_board() -> dict:
    return build_board("standard_12_cat")


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# ─── build_board: kyle_12_points (points) ───


def test_kyle_count(kyle_board):
    """975 displayable players (valuated players INNER JOINed to players rows)."""
    assert kyle_board["count"] == 975
    assert len(kyle_board["players"]) == 975


def test_kyle_top_player(kyle_board):
    """Top player is McDavid: rank 1, VORP 341.34, $47.0 (hand-verified)."""
    top = kyle_board["players"][0]
    assert top["name"] == "Connor McDavid"
    assert top["pos"] == "C"
    assert top["rank"] == 1
    assert top["vorp"] == pytest.approx(341.34, abs=0.01)
    assert top["dollars"] == pytest.approx(47.0, abs=0.1)


def test_kyle_top3(kyle_board):
    """Ranks 2-3 are MacKinnon and Draisaitl with hand-verified values."""
    p1, p2, p3 = kyle_board["players"][:3]
    assert [p["name"] for p in (p1, p2, p3)] == [
        "Connor McDavid",
        "Nathan MacKinnon",
        "Leon Draisaitl",
    ]
    assert [p["rank"] for p in (p1, p2, p3)] == [1, 2, 3]
    assert p1["vorp"] == pytest.approx(341.34, abs=0.01)
    assert p2["vorp"] == pytest.approx(336.55, abs=0.01)
    assert p3["vorp"] == pytest.approx(280.21, abs=0.01)
    assert p1["dollars"] == pytest.approx(47.0, abs=0.1)
    assert p2["dollars"] == pytest.approx(46.3, abs=0.1)
    assert p3["dollars"] == pytest.approx(38.6, abs=0.1)


def test_kyle_categories(kyle_board):
    """12 category keys, exact order from the points_per_stat YAML."""
    assert kyle_board["categories"] == [
        "G", "A", "PPP", "SHP", "SOG", "BLK", "HIT", "GS", "W", "GA", "SV", "SO",
    ]
    assert len(kyle_board["categories"]) == 12


def test_kyle_meta(kyle_board):
    """No ADP data yet -> has_adp False; season 20262027; points scoring."""
    assert kyle_board["has_adp"] is False
    assert kyle_board["season"] == 20262027
    assert kyle_board["scoring_type"] == "points"
    assert kyle_board["league"] == "kyle_12_points"


def test_kyle_roster_slots(kyle_board):
    assert kyle_board["roster_slots"] == {
        "C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2, "UTIL": 1, "BENCH": 5,
    }


def test_kyle_ranks_sequential(kyle_board):
    """Rank column is gapless 1..N (filter-then-rank refactor)."""
    ranks = [p["rank"] for p in kyle_board["players"]]
    assert ranks == list(range(1, kyle_board["count"] + 1))


def test_kyle_rank1_is_top_vorp(kyle_board):
    """Rank 1 is the max VORP and VORP is non-increasing down the list."""
    vorps = [p["vorp"] for p in kyle_board["players"]]
    assert vorps[0] == max(vorps)
    assert vorps == sorted(vorps, reverse=True)


def test_kyle_adp_columns_absent(kyle_board):
    """With no ADP data, adp and delta are None for every player."""
    for p in kyle_board["players"]:
        assert p["adp"] is None
        assert p["delta"] is None


def test_kyle_player_shape(kyle_board):
    """Each player row has exactly the documented keys and 12 stat keys."""
    required = {
        "id", "name", "pos", "team", "rank", "vorp", "dollars", "stats",
        "adp", "delta",
    }
    cat_keys = set(kyle_board["categories"])
    for p in kyle_board["players"]:
        assert set(p.keys()) == required
        assert set(p["stats"].keys()) == cat_keys


def test_kyle_leagues_list(kyle_board):
    """The leagues dropdown lists all four configured leagues."""
    assert set(kyle_board["leagues"]) == {
        "kyle_12_points", "points_10", "roto_14", "standard_12_cat",
    }


# ─── build_board: roto_14 (categories) ───


def test_roto_count_and_scoring(roto_board):
    assert roto_board["count"] == 975
    assert roto_board["scoring_type"] == "roto"
    assert roto_board["league"] == "roto_14"


def test_roto_categories(roto_board):
    """8 category keys, exact order from the categories YAML."""
    assert roto_board["categories"] == [
        "G", "A", "PPP", "SOG", "HIT", "BLK", "W", "SV%",
    ]
    assert len(roto_board["categories"]) == 8


def test_roto_top3(roto_board):
    p1, p2, p3 = roto_board["players"][:3]
    assert [p["name"] for p in (p1, p2, p3)] == [
        "Connor McDavid",
        "Nathan MacKinnon",
        "Cale Makar",
    ]
    assert [p["rank"] for p in (p1, p2, p3)] == [1, 2, 3]
    # Roto VORP is on a different scale than points (category z-scores)
    assert p1["vorp"] == pytest.approx(11.12, abs=0.01)
    assert p2["vorp"] == pytest.approx(10.67, abs=0.01)
    assert p3["vorp"] == pytest.approx(9.22, abs=0.01)
    assert p1["dollars"] == pytest.approx(51.0, abs=0.1)
    assert p2["dollars"] == pytest.approx(48.9, abs=0.1)
    assert p3["dollars"] == pytest.approx(42.3, abs=0.1)


def test_roto_ordering_differs_from_points(kyle_board, roto_board):
    """Category scoring and points scoring produce different full orderings."""
    kyle_order = [p["id"] for p in kyle_board["players"]]
    roto_order = [p["id"] for p in roto_board["players"]]
    assert kyle_order != roto_order


def test_roto_rank1_is_top_vorp(roto_board):
    vorps = [p["vorp"] for p in roto_board["players"]]
    assert vorps[0] == max(vorps)
    assert vorps == sorted(vorps, reverse=True)
    assert [p["rank"] for p in roto_board["players"]] == list(
        range(1, roto_board["count"] + 1)
    )


# ─── build_board: other scoring systems ───


def test_points_10_top3(points_board):
    """points_10: different VORP scale and a defense enters the top 3."""
    p1, p2, p3 = points_board["players"][:3]
    assert p1["name"] == "Nathan MacKinnon"
    assert p2["name"] == "Connor McDavid"
    assert p3["name"] == "Cale Makar"
    assert p3["pos"] == "D"
    assert points_board["scoring_type"] == "points"
    assert p1["vorp"] == pytest.approx(227.99, abs=0.01)
    assert p2["vorp"] == pytest.approx(213.58, abs=0.01)
    assert p3["vorp"] == pytest.approx(174.07, abs=0.01)


def test_standard_top(standard_board):
    """h2h_categories scoring: McDavid still rank 1, VORP on the category scale."""
    assert standard_board["scoring_type"] == "h2h_categories"
    assert standard_board["count"] == 975
    assert standard_board["players"][0]["name"] == "Connor McDavid"
    assert standard_board["players"][0]["rank"] == 1
    assert standard_board["players"][0]["vorp"] == pytest.approx(10.74, abs=0.01)


# ─── build_board: unknown league -> 404 ───


def test_unknown_league_raises_404():
    with pytest.raises(HTTPException) as excinfo:
        build_board("nope")
    exc = excinfo.value
    assert exc.status_code == 404
    assert "nope" in exc.detail


# ─── HTTP layer (TestClient) ───


def test_http_default_board(client):
    res = client.get("/api/board")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 975
    assert data["league"] == "kyle_12_points"
    assert data["players"][0]["name"] == "Connor McDavid"


def test_http_league_param(client):
    res = client.get("/api/board", params={"league": "roto_14"})
    assert res.status_code == 200
    assert res.json()["scoring_type"] == "roto"


def test_http_unknown_league_404(client):
    res = client.get("/api/board", params={"league": "nope"})
    assert res.status_code == 404


def test_http_index_serves_board_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"/static/board.js" in res.content


def test_http_static_board_js(client):
    res = client.get("/static/board.js")
    assert res.status_code == 200
    # sanity: the client bundle served is the real board.js (has draft logic)
    assert b"drafted" in res.content


# ─── build_player_detail: player drill-down ────────────────────────


@pytest.fixture(scope="module")
def mcdavid_detail() -> dict:
    """Drill-down for skater 8478402 (Connor McDavid, C, EDM)."""
    return build_player_detail(8478402, "roto_14")


@pytest.fixture(scope="module")
def tomkins_detail() -> dict:
    """Drill-down for goalie 8477035 (Matt Tomkins, G, EDM)."""
    return build_player_detail(8477035, "roto_14")


@pytest.fixture(scope="module")
def geertsen_detail() -> dict:
    """Drill-down for skater 8477419 (Mason Geertsen, L, BUF): 1 season."""
    return build_player_detail(8477419, "roto_14")


def test_skater_detail_shape(mcdavid_detail):
    """Skater: 6 categories, 40 deterministic breakdown rows, is_goalie False."""
    d = mcdavid_detail
    assert d["name"] == "Connor McDavid"
    assert d["pos"] == "C"
    assert d["is_goalie"] is False
    assert d["seasons"] == [20232024, 20242025, 20252026]
    assert len(d["categories"]) == 6
    assert len(d["breakdown"]) == 40


def test_skater_detail_goals(mcdavid_detail):
    """McDavid goals: actuals [40,33,49], projection 35.2567, band hand-checked."""
    goals = next(c for c in mcdavid_detail["categories"] if c["key"] == "goals")
    assert goals["actuals"] == [40, 33, 49]
    assert goals["projection"] == pytest.approx(35.2567, abs=0.001)
    assert goals["band"] == [27.790579, 42.722749]
    assert goals["band"][0] < goals["projection"] < goals["band"][1]


def test_skater_detail_board_row(mcdavid_detail):
    """board_row mirrors the ranked board: McDavid is rank 1 in roto."""
    row = mcdavid_detail["board_row"]
    assert row is not None
    assert row["rank"] == 1
    assert row["vorp"] == pytest.approx(11.12, abs=0.01)
    assert row["dollars"] == pytest.approx(51.0, abs=0.1)
    assert row["stats"]["G"] == pytest.approx(35.3, abs=0.1)


def test_goalie_detail_shape(tomkins_detail):
    """Goalie: 6 categories, 17 deterministic breakdown rows, is_goalie True."""
    d = tomkins_detail
    assert d["name"] == "Matt Tomkins"
    assert d["pos"] == "G"
    assert d["is_goalie"] is True
    assert d["seasons"] == [20232024]
    assert len(d["categories"]) == 6
    assert len(d["breakdown"]) == 17


def test_goalie_detail_degenerate_bands(tomkins_detail):
    """Count categories have raw/regressed=None -> shrink=0 -> [final, final]."""
    d = tomkins_detail
    by_key = {c["key"]: c for c in d["categories"]}
    # Rate category (save_pct) gets a real band: lo < proj < hi
    sp = by_key["save_pct"]
    assert sp["band"][0] < sp["projection"] < sp["band"][1]
    assert sp["band"] == [0.891892, 0.905644]
    # Count categories degenerate to [final, final]
    for key in ("wins", "shutouts", "games_started", "goals_against", "saves"):
        c = by_key[key]
        assert c["band"][0] == c["band"][1]
        assert c["band"] == [round(c["projection"], 6), round(c["projection"], 6)]


def test_goalie_detail_schedule(tomkins_detail):
    """Schedule summary for the upcoming season: EDM, 83 games, month buckets."""
    sched = tomkins_detail["schedule"]
    assert sched is not None
    assert sched["team"] == "EDM"
    assert sched["total"] == 83
    assert sched["heaviest_month"] == "2026-11"
    assert sched["heaviest_games"] == 15
    assert sched["lightest_month"] == "2027-04"
    assert sched["lightest_games"] == 5
    assert len(sched["monthly"]) == 7


def test_skater_detail_chart_payload(mcdavid_detail):
    """Chart x labels and per-60 series values (hand-verified from the DB)."""
    d = mcdavid_detail
    assert d["labels"] == ["23-24", "24-25", "25-26", "proj"]
    assert d["band_note"] is None
    goals = next(c for c in d["categories"] if c["key"] == "goals")
    # GP per season from the DB: 101, 89, 88; per-60 = value / gp * 60
    assert goals["actual"] == [23.762376, 22.247191, 33.409091]
    # projection 35.256664 over proj_gp 82 -> 25.797559 per 60
    assert goals["proj"] == 25.797559
    assert goals["hi"] == 31.260548
    assert goals["lo"] == 20.33457
    # every chart series has one point per past season (labels minus proj)
    for c in d["categories"]:
        assert len(c["actual"]) == len(d["labels"]) - 1


def test_goalie_detail_chart_payload(tomkins_detail):
    """Goalie: one season, raw per-season chart values, widened band note."""
    d = tomkins_detail
    assert d["labels"] == ["23-24", "proj"]
    assert d["band_note"] == "Band widened: only one season of qualifying data."
    by_key = {c["key"]: c for c in d["categories"]}
    sp = by_key["save_pct"]
    assert sp["actual"] == [0.891892]
    assert sp["proj"] == 0.898768
    assert sp["hi"] == 0.905644
    assert sp["lo"] == 0.891892
    # count categories have no rate history: chart band collapses to proj
    wins = by_key["wins"]
    assert wins["actual"] == [3]
    assert wins["proj"] == wins["hi"] == wins["lo"]


def test_geertsen_single_season(geertsen_detail):
    """Geertsen: one season, zero goals, per-60 band starting at 0."""
    d = geertsen_detail
    assert d["name"] == "Mason Geertsen"
    assert d["pos"] == "L"
    assert d["is_goalie"] is False
    assert d["seasons"] == [20252026]
    assert d["labels"] == ["25-26", "proj"]
    assert d["band_note"] == "Band widened: only one season of qualifying data."
    goals = next(c for c in d["categories"] if c["key"] == "goals")
    assert goals["actuals"] == [0]
    assert goals["projection"] == pytest.approx(8.626575, abs=0.001)
    assert goals["band"] == [0.0, 17.25315]
    assert goals["actual"] == [0.0]
    assert goals["proj"] == 11.476596
    assert goals["hi"] == 22.953192
    assert goals["lo"] == 0.0


def test_breakdown_rows_match_terms(mcdavid_detail, tomkins_detail):
    """Breakdown payload length equals _breakdown_rows over the live terms."""
    terms = explain(8478402, 20262027, str(DB_PATH))
    assert len(mcdavid_detail["breakdown"]) == len(
        _breakdown_rows(terms, is_goalie=False))
    terms = explain(8477035, 20262027, str(DB_PATH))
    assert len(tomkins_detail["breakdown"]) == len(
        _breakdown_rows(terms, is_goalie=True))


def test_no_data_returns_empty_dict():
    """A player with no qualifying seasons -> {} (drill-down shows no data)."""
    assert build_player_detail(8475968, "roto_14") == {}


def test_unknown_player_raises_404():
    with pytest.raises(HTTPException) as excinfo:
        build_player_detail(99999999, "roto_14")
    exc = excinfo.value
    assert exc.status_code == 404
    assert "99999999" in exc.detail


# ─── HTTP layer: /api/player/{id} ──────────────────────────────────


def test_http_player_skater(client):
    res = client.get("/api/player/8478402", params={"league": "roto_14"})
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "Connor McDavid"
    assert data["is_goalie"] is False
    assert len(data["categories"]) == 6
    assert len(data["breakdown"]) == 40


def test_http_player_goalie(client):
    res = client.get("/api/player/8477035", params={"league": "roto_14"})
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "Matt Tomkins"
    assert data["is_goalie"] is True
    assert len(data["categories"]) == 6
    assert len(data["breakdown"]) == 17


def test_http_player_no_data(client):
    res = client.get("/api/player/8475968", params={"league": "roto_14"})
    assert res.status_code == 200
    assert res.json() == {}


def test_http_player_unknown_404(client):
    res = client.get("/api/player/99999999", params={"league": "roto_14"})
    assert res.status_code == 404


def test_http_player_chart_fields(client):
    """HTTP payload carries the chart fields: labels, band_note, per-cat."""
    res = client.get("/api/player/8477035", params={"league": "roto_14"})
    assert res.status_code == 200
    data = res.json()
    assert data["labels"] == ["23-24", "proj"]
    assert data["band_note"] == "Band widened: only one season of qualifying data."
    sp = next(c for c in data["categories"] if c["key"] == "save_pct")
    assert sp["actual"] == [0.891892]
    assert sp["proj"] == 0.898768
    assert sp["hi"] == 0.905644
    assert sp["lo"] == 0.891892


def test_http_index_serves_detail_scripts(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"/static/vendor/uPlot.iife.min.js" in res.content
    assert b"/static/detail.js" in res.content


def test_http_static_detail_js(client):
    res = client.get("/static/detail.js")
    assert res.status_code == 200
    assert b"uPlot" in res.content
