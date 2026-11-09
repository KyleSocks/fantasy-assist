"""Tests for src/draft/recommend.py — marginal-value draft recommendations.

Synthetic pools with hand-computed values, plus HTTP error handling.
Tests against the real database were removed because its numbers change
with every ingest.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src.api.routes import app
from src.draft.recommend import _scarcity, recommend
from src.valuation.league import LeagueConfig
from src.valuation.valuation import PlayerValue


# ─── Fixtures (module-scoped: pipeline runs once per league) ───


def _pv(pid: int, pos: str, vorp: float) -> PlayerValue:
    return PlayerValue(pid, pos, vorp, vorp, {}, 0.0, total_value=vorp)


# ─── Roster state changes the output ───


FULL_ROSTER = [
    8478402, 8477492, 8480027, 8480801, 8477956, 8478420,
    8480069, 8480803, 8481542, 8480839, 8479979, 8476883, 8477934,
]


# ─── Scarcity multiplier: derived from the need/runway ratio ───


def _synth_cfg() -> LeagueConfig:
    return LeagueConfig(
        name="synth",
        team_count=12,
        scoring_type="points",
        roster_slots={"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2,
                      "UTIL": 1, "BENCH": 4},
        points_per_stat={"G": 1.0},
    )


def test_scarcity_derived_from_ratio():
    """Synthetic pool proving urgency = teams_needing / runway.
    Hand-computed: F starter slots = 12 teams x 6 = 72; 60 drafted F leave
    12 -> teams_needing = ceil(12/6) = 2; only one F above replacement
    (runway 1) -> urgency = 2/1 = 2.0. D and G have runway 0 -> 1.0."""
    cfg = _synth_cfg()
    vals = [_pv(900001, "C", 5.0)] + [_pv(i, "C", 0.0) for i in range(900002, 900011)]
    pos_of = {v.player_id: v.position for v in vals}
    drafted = set(range(900011, 900071))  # 60 drafted F, all off-pool
    sc = _scarcity(drafted, vals, pos_of, cfg)
    assert sc["F"] == (2.0, 1, 2)
    assert sc["D"][0] == 1.0
    assert sc["G"][0] == 1.0


def test_scarcity_end_to_end(monkeypatch):
    """End-to-end on the synthetic pool: marginal = vorp x urgency x 1.0
    (points league -> no pickup; empty roster -> no bench scaling).
    Hand-computed: p1 = 5.0 x 2.0 = 10.0; p2 = 0.0 x 2.0 = 0.0, and the
    reason must name the runway count (1)."""
    # synthetic ids: no name lookup in data/hockey.db
    monkeypatch.setattr(importlib.import_module("src.draft.recommend"),
                        "_load_names", lambda: {})
    cfg = _synth_cfg()
    vals = [_pv(900001, "C", 5.0)] + [_pv(i, "C", 0.0) for i in range(900002, 900011)]
    recs = recommend([], list(range(900011, 900071)), vals, cfg, 10)
    assert recs[0].player_id == 900001
    assert recs[0].name == "Player 900001"
    assert recs[0].marginal_value == pytest.approx(10.0, abs=1e-9)
    assert "with only 1 C above replacement left" in recs[0].reason
    assert recs[1].player_id == 900002
    assert recs[1].marginal_value == pytest.approx(0.0, abs=1e-9)


# ─── HTTP endpoint ───


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_api_unknown_league(client):
    resp = client.get("/api/recommend", params={"league": "nope"})
    assert resp.status_code == 404


def test_api_bad_id(client):
    resp = client.get("/api/recommend", params={"drafted_by_me": "8478402,abc"})
    assert resp.status_code == 422
