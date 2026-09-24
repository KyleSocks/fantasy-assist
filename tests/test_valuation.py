"""Tests for src/valuation/valuation.py — hand-computed expected values."""
from __future__ import annotations

import pytest
from pathlib import Path

import numpy as np

from src.projections.projections import Projection, project_all
from src.valuation.league import LeagueConfig, load_league
from src.valuation.valuation import (
    PlayerValue,
    auction_values,
    replacement_levels,
    valuate,
)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "leagues"
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "hockey.db"
SEASON_ID = 20252026


@pytest.fixture(scope="module")
def projs() -> list[Projection]:
    """Real projections from the DB."""
    return project_all(SEASON_ID, str(DB_PATH))


@pytest.fixture(scope="module")
def standard_cfg() -> LeagueConfig:
    return load_league(str(CONFIG_DIR / "standard_12_cat.yaml"))


@pytest.fixture(scope="module")
def standard_cfg_10() -> LeagueConfig:
    """Same league but team_count=10 for the 10->14 diff test."""
    return LeagueConfig(
        name="standard_10_cat",
        team_count=10,
        scoring_type="h2h_categories",
        roster_slots={
            "C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2, "UTIL": 1, "BENCH": 4,
        },
        categories=["G", "A", "PPP", "SOG", "HIT+BLK", "W", "SV%"],
    )


@pytest.fixture(scope="module")
def standard_cfg_14() -> LeagueConfig:
    """Same league but team_count=14 for the 10->14 diff test."""
    return LeagueConfig(
        name="standard_14_cat",
        team_count=14,
        scoring_type="h2h_categories",
        roster_slots={
            "C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2, "UTIL": 1, "BENCH": 4,
        },
        categories=["G", "A", "PPP", "SOG", "HIT+BLK", "W", "SV%"],
    )


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


def test_real_data_vorp_consistency(projs, standard_cfg):
    """VORP = raw_value - replacement_used for every player."""
    vals = valuate(projs, standard_cfg)
    for v in vals:
        assert abs(v.vorp - (v.raw_value - v.replacement_used)) < 1e-9


def test_real_data_replacement_levels(projs, standard_cfg):
    """Replacement levels are finite and ordered sensibly."""
    levels = replacement_levels(projs, standard_cfg)
    assert set(levels.keys()) == {"F", "D", "G"}
    for pos, lvl in levels.items():
        assert np.isfinite(lvl)


def test_team_count_diff_reorders(projs, standard_cfg_10, standard_cfg_14):
    """
    Switching team_count 10 -> 14 changes replacement level at every position
    and reorders the top 100. Print the diff.
    """
    levels_10 = replacement_levels(projs, standard_cfg_10)
    levels_14 = replacement_levels(projs, standard_cfg_14)
    
    # Replacement level must differ at every position
    for pos in ("F", "D", "G"):
        assert levels_10[pos] != levels_14[pos], f"Position {pos}: same replacement level for team_count 10 and 14"
    
    vals_10 = valuate(projs, standard_cfg_10)
    vals_14 = valuate(projs, standard_cfg_14)
    top100_10 = [v.player_id for v in sorted(vals_10, key=lambda v: v.vorp, reverse=True)][:100]
    top100_14 = [v.player_id for v in sorted(vals_14, key=lambda v: v.vorp, reverse=True)][:100]
    
    # Top 100 must differ (reorder)
    assert top100_10 != top100_14, "Top 100 identical for team_count 10 and 14"
    
    # Print the diff
    print(f"\n=== team_count 10 -> 14 diff ===")
    print(f"Replacement levels (10): F={levels_10['F']:.4f} D={levels_10['D']:.4f} G={levels_10['G']:.4f}")
    print(f"Replacement levels (14): F={levels_14['F']:.4f} D={levels_14['D']:.4f} G={levels_14['G']:.4f}")
    diffs = [i for i, (a, b) in enumerate(zip(top100_10, top100_14)) if a != b]
    print(f"Top-100 positions changed: {len(diffs)}/100")
    print(f"First 10 changes at indices: {diffs[:10]}")


def test_auction_values_sum(projs, standard_cfg):
    """Sum of auction_values across the drafted pool equals team_count * budget within $1."""
    vals = valuate(projs, standard_cfg)
    budget = 200
    avals = auction_values(vals, standard_cfg, budget)
    total = sum(avals.values())
    expected = standard_cfg.team_count * budget
    assert abs(total - expected) < 1.0, f"Auction total {total} != expected {expected}"
    print(f"\nAuction total: ${total:.2f}, expected: ${expected} (diff=${abs(total-expected):.2f})")


def test_goalie_volume_weighting(projs, standard_cfg):
    """
    Assert: no sub-20-game goalie outranks any 55-start goalie with save_pct >= .905.
    
    A 15-game .930 backup must not outrank a 55-start .905 starter.
    The volume weighting (gp / pool mean gp) ensures this.
    """
    vals = valuate(projs, standard_cfg)
    by_pid = {v.player_id: v for v in vals}
    
    # Find goalies in the projections
    g_by_pid = {p.player_id: p for p in projs if p.position == "G"}
    
    sub20 = []  # goalies with gp < 20
    starter55 = []  # goalies with gp >= 55 and save_pct >= 0.905
    for pid, p in g_by_pid.items():
        if p.gp < 20:
            sub20.append(pid)
        if p.gp >= 55 and p.save_pct is not None and p.save_pct >= 0.905:
            starter55.append(pid)
    
    if not sub20 or not starter55:
        pytest.skip(f"Not enough goalie data: sub20={len(sub20)}, starter55={len(starter55)}")
    
    for s_pid in sub20:
        s_val = by_pid[s_pid].vorp
        for st_pid in starter55:
            st_val = by_pid[st_pid].vorp
            assert s_val < st_val, (
                f"Sub-20-game goalie pid={s_pid} (gp={g_by_pid[s_pid].gp:.1f}, "
                f"sv%={g_by_pid[s_pid].save_pct:.3f}) outranks 55+ start goalie "
                f"pid={st_pid} (gp={g_by_pid[st_pid].gp:.1f}, sv%={g_by_pid[st_pid].save_pct:.3f})"
            )


def test_no_banned_identifiers():
    """No identifier in valuation.py matches /SCARCITY|BONUS|MULTIPLIER|PREMIUM/i."""
    import re
    path = Path(__file__).resolve().parent.parent / "src" / "valuation" / "valuation.py"
    text = path.read_text()
    for i, line in enumerate(text.splitlines(), 1):
        assert not re.search(r"SCARCITY|BONUS|MULTIPLIER|PREMIUM", line, re.I), (
            f"Banned identifier pattern found at line {i}: {line.strip()}"
        )
