"""Tests for src/projections/band.py — confidence band computation.

Expected values are hand-computed from the ingested database (data/hockey.db)
for season 20262027 and verified against ``uv run python scratch/tmp_detail.py``
and ``scratch/tmp_bands.py``:

- McDavid (8478402, skater) goals band [27.784282, 42.722749].
- Tomkins (8477035, goalie) save_pct band [0.891892, 0.905644].
- Tomkins count categories (single season) degenerate to [final, final].
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.projections.band import compute_band, pop_std
from src.projections.projections import explain

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "hockey.db"
SEASON = 20262027


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def _mcdavid_goals_inputs():
    """Exact DB inputs for McDavid's goals projection."""
    terms = explain(8478402, SEASON, str(DB_PATH))
    rows = _conn().execute(
        "SELECT goals FROM skater_seasons "
        "WHERE player_id=8478402 AND season_id < ? AND gp > 0 "
        "ORDER BY season_id DESC LIMIT 3", (SEASON,)).fetchall()[::-1]
    actuals = [r[0] for r in rows]
    return terms, actuals


def _tomkins_goalie_inputs(cat: str):
    """Exact DB inputs for Tomkins' goalie projection (per category)."""
    terms = explain(8477035, SEASON, str(DB_PATH))
    rows = _conn().execute(
        "SELECT saves, shots_against, wins, shutouts, games_started, "
        "goals_against FROM goalie_seasons "
        "WHERE player_id=8477035 AND season_id < ? AND gp > 0 "
        "ORDER BY season_id DESC LIMIT 3", (SEASON,)).fetchall()[::-1]
    actuals = []
    for r in rows:
        if cat == "save_pct":
            sv, sa = r[0], r[1]
            actuals.append(sv / sa if sa > 0 else 0.0)
        else:
            idx = {"wins": 2, "shutouts": 3, "games_started": 4,
                   "goals_against": 5, "saves": 1}[cat]
            actuals.append(r[idx] or 0)
    raw = terms["raw_rates"].get(cat)
    regressed = terms["regressed_rate"].get(cat)
    return terms, actuals, raw, regressed


# ─── pop_std (pure unit) ───


def test_pop_std_basic():
    # Hand-computed: mean 40.6667, population variance 42.8889 -> std 6.548961
    assert pop_std([40, 33, 49]) == pytest.approx(6.548961, abs=1e-5)
    # Constant values -> zero spread
    assert pop_std([5, 5, 5]) == 0.0
    # Fewer than 2 values -> defined as 0.0
    assert pop_std([3]) == 0.0
    assert pop_std([]) == 0.0


# ─── skater band (McDavid) ───


def test_mcdavid_goals_band():
    """McDavid goals band [27.784282, 42.722749] (hand-verified)."""
    terms, actuals = _mcdavid_goals_inputs()
    assert actuals == [40, 33, 49]
    lo, hi = compute_band(
        final=terms["final"]["goals"],
        season_actuals=actuals,
        raw=terms["raw_rates"]["goals"],
        regressed=terms["regressed_rate"]["goals"],
        age_factor=terms["age_factor"],
        proj_gp=terms["proj_gp"],
        category="goals",
        is_goalie=False,
    )
    assert (lo, hi) == pytest.approx((27.784282, 42.722749), abs=1e-5)


def test_mcdavid_all_skater_categories():
    """All six skater category bands match hand-computed values."""
    expected = {
        "goals": (27.784282, 42.722749),
        "assists": (64.713810, 105.451831),
        "shots": (226.800040, 293.177868),
        "blocks": (23.300046, 43.460658),
        "hits": (10.900836, 115.189401),
        "pp_points": (31.073024, 54.836802),
    }
    terms = explain(8478402, SEASON, str(DB_PATH))
    for cat, exp in expected.items():
        rows = _conn().execute(
            f"SELECT {cat} FROM skater_seasons "
            "WHERE player_id=8478402 AND season_id < ? AND gp > 0 "
            "ORDER BY season_id DESC LIMIT 3", (SEASON,)).fetchall()[::-1]
        actuals = [r[0] for r in rows]
        lo, hi = compute_band(
            final=terms["final"][cat],
            season_actuals=actuals,
            raw=terms["raw_rates"][cat],
            regressed=terms["regressed_rate"][cat],
            age_factor=terms["age_factor"],
            proj_gp=terms["proj_gp"],
            category=cat,
            is_goalie=False,
        )
        assert (lo, hi) == pytest.approx(exp, abs=1e-5), cat


# ─── goalie band (Tomkins) ───


def test_tomkins_save_pct_band():
    """Tomkins save_pct band [0.891892, 0.905644] (hand-verified)."""
    terms, actuals, raw, regressed = _tomkins_goalie_inputs("save_pct")
    assert actuals == pytest.approx([0.8918918918918919])
    lo, hi = compute_band(
        final=terms["final"]["save_pct"],
        season_actuals=actuals,
        raw=raw,
        regressed=regressed,
        age_factor=terms["age_factor"],
        proj_gp=terms["proj_gp"],
        category="save_pct",
        is_goalie=True,
    )
    assert (lo, hi) == pytest.approx((0.891892, 0.905644), abs=1e-5)


def test_tomkins_count_categories_degenerate():
    """Tomkins count categories (1 season, no rate regression) degenerate: lo == hi == final."""
    terms = explain(8477035, SEASON, str(DB_PATH))
    for cat in ("wins", "shutouts", "games_started", "goals_against", "saves"):
        _, actuals, raw, regressed = _tomkins_goalie_inputs(cat)
        assert raw is None and regressed is None, cat
        lo, hi = compute_band(
            final=terms["final"][cat],
            season_actuals=actuals,
            raw=raw,
            regressed=regressed,
            age_factor=terms["age_factor"],
            proj_gp=terms["proj_gp"],
            category=cat,
            is_goalie=True,
        )
        assert lo == hi == pytest.approx(terms["final"][cat], abs=1e-9), cat
