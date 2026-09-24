"""Tests for src/draft/recommend.py — marginal-value draft recommendations.

Expected values are hand-computed from data/hockey.db (season 20262027)
through the same pipeline the API uses (project_all + valuate) and
cross-checked against the live scratch run of recommend()
(scratch/scratch_recommend.py).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.routes import app
from src.draft.recommend import (
    BENCH_SCALE,
    Recommendation,
    _scarcity,
    recommend,
)
from src.projections.projections import project_all
from src.valuation.league import LeagueConfig, load_league
from src.valuation.valuation import PlayerValue, valuate

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "hockey.db"
CONFIG_DIR = ROOT / "config" / "leagues"
SEASON_ID = 20262027


# ─── Fixtures (module-scoped: pipeline runs once per league) ───


@pytest.fixture(scope="module")
def roto_vals():
    """(PlayerValue list, LeagueConfig) for the 14-team category league."""
    cfg = load_league(str(CONFIG_DIR / "roto_14.yaml"))
    vals = valuate(project_all(SEASON_ID, str(DB_PATH)), cfg)
    return vals, cfg


@pytest.fixture(scope="module")
def kyle_vals():
    """(PlayerValue list, LeagueConfig) for the default 12-team points league."""
    cfg = load_league(str(CONFIG_DIR / "kyle_12_points.yaml"))
    vals = valuate(project_all(SEASON_ID, str(DB_PATH)), cfg)
    return vals, cfg


def _pv(pid: int, pos: str, vorp: float) -> PlayerValue:
    return PlayerValue(pid, pos, vorp, vorp, {}, 0.0)


# ─── Empty roster: neutral multipliers, raw VORP top 3 ───


def test_empty_roster_top3_raw_vorp(roto_vals):
    """No roster, no drafts: every multiplier is 1.0 / 0.0, so the output
    is the raw-VORP top 3 of the available pool.
    Hand-computed: McDavid C 11.1160, MacKinnon C 10.6651, Makar D 9.2221
    (same numbers test_api pins on the board endpoint)."""
    vals, cfg = roto_vals
    recs = recommend([], [], vals, cfg, 1)
    assert [r.player_id for r in recs] == [8478402, 8477492, 8480069]
    assert [r.name for r in recs] == [
        "Connor McDavid", "Nathan MacKinnon", "Cale Makar",
    ]
    assert recs[0].marginal_value == pytest.approx(11.1160, rel=1e-4)
    assert recs[1].marginal_value == pytest.approx(10.6651, rel=1e-4)
    assert recs[2].marginal_value == pytest.approx(9.2221, rel=1e-4)


def test_empty_roster_points_league(kyle_vals):
    """Same neutral-state check on the points league: VORP is points here,
    so the top 3 are McDavid 341.3408, MacKinnon 336.5532, Draisaitl 280.2083."""
    vals, cfg = kyle_vals
    recs = recommend([], [], vals, cfg, 1)
    assert [r.player_id for r in recs] == [8478402, 8477492, 8477934]
    assert recs[0].marginal_value == pytest.approx(341.3408, rel=1e-4)
    assert recs[1].marginal_value == pytest.approx(336.5532, rel=1e-4)
    assert recs[2].marginal_value == pytest.approx(280.2083, rel=1e-4)


# ─── Drafted players are excluded from the pool ───


def test_drafted_never_recommended(roto_vals):
    """Anything in drafted_by_me or drafted_all must never be recommended,
    and the pool must still yield a full top 3."""
    vals, cfg = roto_vals
    drafted_all = [8478402, 8477492, 8480069, 8477934, 8480027]
    recs = recommend([8478402, 8477492], drafted_all, vals, cfg, 1)
    assert len(recs) == 3
    assert not any(r.player_id in set(drafted_all) for r in recs)


# ─── Roster state changes the output ───


def test_drafting_top_g_shifts_output(roto_vals):
    """Drafting the best available G makes the team sit below -0.5 in several
    categories, so the top remaining C earns category pickup and his
    marginal value rises above his raw VORP.
    Hand-computed: McDavid 21.7953 vs 11.1160 in the empty state (scratch
    run); the reason names the real team deficit (-1.7 in A)."""
    vals, cfg = roto_vals
    empty = recommend([], [], vals, cfg, 1)
    g_id = 8479979  # top available G by VORP (3.2742)
    after = recommend([g_id], [g_id], vals, cfg, 2)
    assert after[0].player_id == 8478402
    assert after[0].marginal_value > empty[0].marginal_value
    assert "-1.7 in A" in after[0].reason


def test_deficit_position_boosted(roto_vals):
    """With 2C drafted and 0D, at least one D must land in the top 3.
    Hand-computed: Makar 9.2221 x urgency 1.0 (D runway 70 >= need 14)
    + BLK pickup 1.7142 (team BLK z = -0.6 < -0.5, Makar BLK z = +1.7142)
    = 10.9363, which outranks Bouchard 9.9198 — marginal re-ordering."""
    vals, cfg = roto_vals
    drafted = [8478402, 8477492]
    pos_of = {v.player_id: v.position for v in vals}
    recs = recommend(drafted, drafted, vals, cfg, 3)
    assert any(pos_of[r.player_id] == "D" for r in recs)
    assert recs[0].player_id == 8480069
    assert recs[0].marginal_value == pytest.approx(10.9363, rel=1e-3)


def test_all_starters_full_bench_scale(roto_vals):
    """13-player roster: C2/LW2/RW2/D4/G2 starters all filled plus the 13th
    C filling UTIL, so every remaining player is a bench asset.
    Hand-computed: urgency 1.0 for all groups (runway 129/70/26 >= need
    13/13/13) and no team category below -0.5 (lowest HIT -0.2044), so
    marginal = vorp x 1.0 x 0.35 exactly:
      Suzuki C 7.4301 -> 2.600535, Kucherov R 7.0107 -> 2.453745,
      Hughes D 7.0006 -> 2.450210."""
    vals, cfg = roto_vals
    roster = [
        8478402, 8477492, 8480027, 8480801, 8477956, 8478420,
        8480069, 8480803, 8481542, 8480839, 8479979, 8476883, 8477934,
    ]
    recs = recommend(roster, roster, vals, cfg, 14)
    assert [r.player_id for r in recs] == [8480018, 8476453, 8480800]
    assert recs[0].marginal_value == pytest.approx(7.4301 * BENCH_SCALE, rel=1e-4)
    assert recs[1].marginal_value == pytest.approx(7.0107 * BENCH_SCALE, rel=1e-4)
    assert recs[2].marginal_value == pytest.approx(7.0006 * BENCH_SCALE, rel=1e-4)
    for r in recs:
        assert r.reason.endswith("(bench asset)")


FULL_ROSTER = [
    8478402, 8477492, 8480027, 8480801, 8477956, 8478420,
    8480069, 8480803, 8481542, 8480839, 8479979, 8476883, 8477934,
]


def test_four_roster_states_distinct(roto_vals):
    """Acceptance criterion: no two of four roster states produce identical
    recommendation output."""
    vals, cfg = roto_vals
    states = [
        recommend([], [], vals, cfg, 1),
        recommend([8479979], [8479979], vals, cfg, 2),
        recommend([8478402, 8477492], [8478402, 8477492], vals, cfg, 3),
        recommend(FULL_ROSTER, FULL_ROSTER, vals, cfg, 14),
    ]
    keys = [
        tuple((r.player_id, round(r.marginal_value, 6)) for r in s)
        for s in states
    ]
    assert len(set(keys)) == 4


# ─── Rationale templates: real digits from the data ───


def test_rationale_has_real_digits(roto_vals):
    """Every rationale must carry at least one real digit from the data:
    the player's own VORP formatted as .1f, plus a decimal somewhere."""
    vals, cfg = roto_vals
    by_id = {v.player_id: v for v in vals}
    states = [
        recommend([], [], vals, cfg, 1),
        recommend([8479979], [8479979], vals, cfg, 2),
        recommend([8478402, 8477492], [8477492, 8478402], vals, cfg, 3),
        recommend(FULL_ROSTER, FULL_ROSTER, vals, cfg, 14),
    ]
    for state in states:
        for r in state:
            assert re.search(r"\d+\.\d", r.reason)
            assert f"{by_id[r.player_id].vorp:.1f}" in r.reason


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


def test_scarcity_end_to_end():
    """End-to-end on the synthetic pool: marginal = vorp x urgency x 1.0
    (points league -> no pickup; empty roster -> no bench scaling).
    Hand-computed: p1 = 5.0 x 2.0 = 10.0; p2 = 0.0 x 2.0 = 0.0, and the
    reason must name the runway count (1)."""
    cfg = _synth_cfg()
    vals = [_pv(900001, "C", 5.0)] + [_pv(i, "C", 0.0) for i in range(900002, 900011)]
    recs = recommend([], list(range(900011, 900071)), vals, cfg, 10)
    assert recs[0].player_id == 900001
    assert recs[0].name == "Player 900001"
    assert recs[0].marginal_value == pytest.approx(10.0, abs=1e-9)
    assert "with only 1 C above replacement left" in recs[0].reason
    assert recs[1].player_id == 900002
    assert recs[1].marginal_value == pytest.approx(0.0, abs=1e-9)


def test_recommendation_shape(roto_vals):
    """Recommendation carries exactly the four payload fields."""
    vals, cfg = roto_vals
    recs = recommend([], [], vals, cfg, 1)
    assert isinstance(recs[0], Recommendation)
    assert set(vars(recs[0]).keys()) == {
        "name", "player_id", "marginal_value", "reason",
    }


# ─── HTTP endpoint ───


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_api_default_league(client):
    """Default: kyle_12_points, round 1, empty roster -> raw VORP top 3 with
    values rounded to 2 dp: McDavid 341.34, MacKinnon 336.55, Draisaitl 280.21."""
    resp = client.get("/api/recommend")
    assert resp.status_code == 200
    data = resp.json()
    assert data["league"] == "kyle_12_points"
    assert data["round"] == 1
    recs = data["recommendations"]
    assert len(recs) == 3
    assert recs[0] == {
        "name": "Connor McDavid",
        "player_id": 8478402,
        "marginal_value": 341.34,
        "reason": "Top remaining C by VORP (341.3)",
    }
    assert recs[1]["marginal_value"] == 336.55
    assert recs[2]["marginal_value"] == 280.21


def test_api_roto_with_drafts(client):
    """roto_14 after 2C drafted: top pick is Makar (10.94, hand-computed
    above) and no drafted player is recommended."""
    resp = client.get(
        "/api/recommend",
        params={
            "league": "roto_14",
            "round": 3,
            "drafted_by_me": "8478402,8477492",
            "drafted_all": "8478402,8477492",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["league"] == "roto_14"
    assert data["round"] == 3
    recs = data["recommendations"]
    assert recs[0]["player_id"] == 8480069
    assert recs[0]["marginal_value"] == 10.94
    assert not any(r["player_id"] in {8478402, 8477492} for r in recs)


def test_api_unknown_league(client):
    resp = client.get("/api/recommend", params={"league": "nope"})
    assert resp.status_code == 404


def test_api_bad_id(client):
    resp = client.get("/api/recommend", params={"drafted_by_me": "8478402,abc"})
    assert resp.status_code == 422