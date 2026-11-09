"""Tests for src/valuation/valuation.py — hand-computed expected values."""
from __future__ import annotations

import pytest

import numpy as np
from pathlib import Path

from src.projections.projections import Projection
from src.valuation.league import LeagueConfig
from src.valuation.valuation import replacement_levels, valuate


# ─── Hand-computed small-pool tests ───


def _small_pool_category() -> tuple[list[Projection], LeagueConfig]:
    """
    6 forwards + 2 goalies, 2 categories (G, A), team_count=1,
    roster_slots: C=1, LW=1, RW=1, D=1, G=1, UTIL=0, BENCH=0.
    So: F pool = 3 + 0 = 3, D pool = 1 + 0 = 1, G pool = 1 + 0 = 1.

    Forward values (raw = G + A):
      F1: G=10, A=5  -> 15
      F2: G=8,  A=8  -> 16
      F3: G=6,  A=6  -> 12
      F4: G=4,  A=4  -> 8
      F5: G=2,  A=2  -> 4
      F6: G=1,  A=1  -> 2

    D1: G=3, A=3  -> 6

    Goalies (raw = G + A):
      G1: G=0, A=2  -> 2
      G2: G=0, A=1  -> 1

    F pool = top 3 by raw: F2(16), F1(15), F3(12).
    D pool = top 1 by raw: D1(6).
    G pool = top 1 by raw: G1(2).

    Z-scores: mean/std are computed from the pool (F2, F1, F3, D1, G1)
    but applied to ALL players, so non-pool players get genuine
    (negative) z-scores, not 0.
      G: [8, 10, 6, 3, 0]  mean=5.4, std=sqrt(12.64)=3.5553...
      A: [8, 5, 6, 3, 2]   mean=4.8, std=sqrt(4.56)=2.1354...

    Two-pass: recompute pool using pass1 total z-scores.
      F2: 2.2298, F1: 1.3875, F3: 0.7307 -> top 3 F (unchanged pool)
      D1: -1.5180, G1: -2.8301 -> same D/G pool.
    Same pool, so pass 2 z-scores are identical.

    Final raw values (G_z + A_z for every player):
      F2: 2.2298, F1: 1.3875, F3: 0.7307
      F4: -0.7684, F5: -2.2675, F6: -3.0171
      D1: -1.5180, G1: -2.8301, G2: -3.2984

    Replacement levels (raw_value of (pool_size+1)th ranked at position):
      F: 4th F by raw_value = F4 = (4-5.4)/sqrt(12.64) + (4-4.8)/sqrt(4.56)
         = -0.76841509
      D: only D1 exists, so no 2nd D = 0.0
      G: 2nd G by raw_value = G2 = (0-5.4)/sqrt(12.64) + (1-4.8)/sqrt(4.56)
         = -3.2983817083
    """
    cfg = LeagueConfig(
        name="small",
        team_count=1,
        scoring_type="h2h_categories",
        roster_slots={"C": 1, "LW": 1, "RW": 1, "D": 1, "G": 1, "UTIL": 0, "BENCH": 0},
        categories=["G", "A"],
    )
    projs = [
        Projection(player_id=1, season_id=2025, gp=82, toi_per_gp=21,
                   goals=10, assists=5, shots=50, blocks=5, hits=5,
                   pp_points=3, wins=None, save_pct=None, position="C"),
        Projection(player_id=2, season_id=2025, gp=82, toi_per_gp=21,
                   goals=8, assists=8, shots=40, blocks=4, hits=4,
                   pp_points=2, wins=None, save_pct=None, position="C"),
        Projection(player_id=3, season_id=2025, gp=82, toi_per_gp=21,
                   goals=6, assists=6, shots=30, blocks=3, hits=3,
                   pp_points=1, wins=None, save_pct=None, position="L"),
        Projection(player_id=4, season_id=2025, gp=82, toi_per_gp=21,
                   goals=4, assists=4, shots=20, blocks=2, hits=2,
                   pp_points=1, wins=None, save_pct=None, position="R"),
        Projection(player_id=5, season_id=2025, gp=82, toi_per_gp=21,
                   goals=2, assists=2, shots=10, blocks=1, hits=1,
                   pp_points=0, wins=None, save_pct=None, position="C"),
        Projection(player_id=6, season_id=2025, gp=82, toi_per_gp=21,
                   goals=1, assists=1, shots=5, blocks=0, hits=0,
                   pp_points=0, wins=None, save_pct=None, position="L"),
        Projection(player_id=7, season_id=2025, gp=82, toi_per_gp=18,
                   goals=3, assists=3, shots=15, blocks=3, hits=3,
                   pp_points=1, wins=None, save_pct=None, position="D"),
        Projection(player_id=8, season_id=2025, gp=65, toi_per_gp=60,
                   goals=0, assists=2, shots=0, blocks=0, hits=0,
                   pp_points=0, wins=20, save_pct=0.92, position="G"),
        Projection(player_id=9, season_id=2025, gp=50, toi_per_gp=60,
                   goals=0, assists=1, shots=0, blocks=0, hits=0,
                   pp_points=0, wins=10, save_pct=0.90, position="G"),
    ]
    return projs, cfg


def test_small_pool_zscores():
    """Hand-computed z-scores for a 9-player pool with 2 categories."""
    projs, cfg = _small_pool_category()
    vals = valuate(projs, cfg)
    
    # F pool = top 3 by raw (G+A): F2(16), F1(15), F3(12)
    # D pool = top 1: D1(6)
    # G pool = top 1: G1(2)
    # Pool = [F2, F1, F3, D1, G1]
    
    # G: [8, 10, 6, 3, 0] mean=5.4, std=sqrt(12.64)=3.5553...
    # A: [8, 5, 6, 3, 2] mean=4.8, std=sqrt(4.56)=2.1354...
    
    # Two-pass: same pool both passes, so final z-scores = pass1 z-scores.
    # F2: G_z=(8-5.4)/std_g, A_z=(8-4.8)/std_a
    # F1: G_z=(10-5.4)/std_g, A_z=(5-4.8)/std_a
    # etc.
    
    g_vals = np.array([8.0, 10.0, 6.0, 3.0, 0.0])
    a_vals = np.array([8.0, 5.0, 6.0, 3.0, 2.0])
    g_mean, g_std = g_vals.mean(), g_vals.std()
    a_mean, a_std = a_vals.mean(), a_vals.std()
    
    # F2 (pid=2): G_z = (8-5.4)/g_std, A_z = (8-4.8)/a_std
    expected_f2 = (8 - g_mean) / g_std + (8 - a_mean) / a_std
    # F1 (pid=1): G_z = (10-5.4)/g_std, A_z = (5-4.8)/a_std
    expected_f1 = (10 - g_mean) / g_std + (5 - a_mean) / a_std
    # F3 (pid=3): G_z = (6-5.4)/g_std, A_z = (6-4.8)/a_std
    expected_f3 = (6 - g_mean) / g_std + (6 - a_mean) / a_std
    # D1 (pid=7): G_z = (3-5.4)/g_std, A_z = (3-4.8)/a_std
    expected_d1 = (3 - g_mean) / g_std + (3 - a_mean) / a_std
    # G1 (pid=8): G_z = (0-5.4)/g_std, A_z = (2-4.8)/a_std
    expected_g1 = (0 - g_mean) / g_std + (2 - a_mean) / a_std
    
    by_pid = {v.player_id: v for v in vals}
    assert abs(by_pid[2].raw_value - expected_f2) < 1e-6
    assert abs(by_pid[1].raw_value - expected_f1) < 1e-6
    assert abs(by_pid[3].raw_value - expected_f3) < 1e-6
    assert abs(by_pid[7].raw_value - expected_d1) < 1e-6
    assert abs(by_pid[8].raw_value - expected_g1) < 1e-6


def test_small_pool_replacement():
    """Replacement level = raw_value of (pool_size+1)th ranked player.

    Z-scores use the pool's mean/std but are applied to ALL players, so
    the (pool_size+1)th player has a genuine (negative) z-score sum, not 0.
    Hand-computed (see _small_pool_category docstring):
      F: 4th F by raw_value = F4 = (4-5.4)/sqrt(12.64) + (4-4.8)/sqrt(4.56)
         = -0.76841509
      D: only D1 exists, so no 2nd D = 0.0
      G: 2nd G by raw_value = G2 = (0-5.4)/sqrt(12.64) + (1-4.8)/sqrt(4.56)
         = -3.2983817083
    """
    projs, cfg = _small_pool_category()
    levels = replacement_levels(projs, cfg)

    assert levels["F"] == pytest.approx(-0.76841509, abs=1e-6)
    assert levels["D"] == 0.0
    assert levels["G"] == pytest.approx(-3.2983817083, abs=1e-6)


def test_small_pool_vorp():
    """VORP = raw_value - replacement_used, with non-zero replacements.

    Replacement levels (hand-computed in test_small_pool_replacement):
    F = -0.76841509, D = 0.0, G = -3.2983817083. Players sitting exactly
    at their position's replacement level (F4, G2) have VORP of 0.
    """
    projs, cfg = _small_pool_category()
    vals = valuate(projs, cfg)

    by_pid = {v.player_id: v for v in vals}
    for v in vals:
        assert abs(v.vorp - (v.raw_value - v.replacement_used)) < 1e-9
    # F group (pids 1-6)
    for pid in (1, 2, 3, 4, 5, 6):
        assert by_pid[pid].replacement_used == pytest.approx(-0.76841509, abs=1e-6)
    # D group (pid 7)
    assert by_pid[7].replacement_used == 0.0
    # G group (pids 8-9)
    for pid in (8, 9):
        assert by_pid[pid].replacement_used == pytest.approx(-3.2983817083, abs=1e-6)
    # Exactly-at-replacement players have VORP 0
    assert by_pid[4].vorp == pytest.approx(0.0, abs=1e-9)
    assert by_pid[9].vorp == pytest.approx(0.0, abs=1e-9)


# ─── Real-data tests ───


def test_no_banned_identifiers():
    """No identifier in valuation.py matches /SCARCITY|BONUS|MULTIPLIER|PREMIUM/i."""
    import re
    path = Path(__file__).resolve().parent.parent / "src" / "valuation" / "valuation.py"
    text = path.read_text()
    for i, line in enumerate(text.splitlines(), 1):
        assert not re.search(r"SCARCITY|BONUS|MULTIPLIER|PREMIUM", line, re.I), (
            f"Banned identifier pattern found at line {i}: {line.strip()}"
        )
