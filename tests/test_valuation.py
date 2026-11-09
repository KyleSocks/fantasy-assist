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
    6 forwards, 1 D, 2 goalies; 2 categories (G, A); team_count=1;
    roster_slots: C=1, LW=1, RW=1, D=1, G=1, UTIL=0, BENCH=0.
    So: F pool = 3, D pool = 1, G pool = 1.

    Each position group is z-scored on its OWN pool (F, D and G never share
    mean/std), and goalies are scored only on goalie categories -- G and A
    are skater categories, so goalies have no category z-scores at all.
    The pool's mean/std are applied to EVERY player in the group, so
    players outside the pool get genuine negative z-scores, not 0.

    Forwards (G, A): F1 (10, 5), F2 (8, 8), F3 (6, 6), F4 (4, 4),
    F5 (2, 2), F6 (1, 1). F pool = top 3 by raw G+A: F2, F1, F3.
      G: [8, 10, 6]  mean 8,     std sqrt(8/3)   = 1.632993
      A: [8, 5, 6]   mean 19/3,  std sqrt(14/9)  = 1.247219
      F2:  0/1.632993 + (8-19/3)/1.247219 =  1.336306
      F1:  2/1.632993 + (5-19/3)/1.247219 =  0.155700
      F3: -2/1.632993 + (6-19/3)/1.247219 = -1.492006
      F4: -4/1.632993 + (4-19/3)/1.247219 = -4.320318
      F5: -7.148631   F6: -8.562787
    Pass 2 re-ranks on pass-1 values: same top 3, so same numbers.

    D pool = D1 alone: std 0 -> every D z-score is 0.
    Goalies: no categories -> 0.

    Replacement levels ((pool_size+1)th player in the group):
      F: F4 = -4.320318      D: no 2nd D -> 0.0      G: G2 = 0.0
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


def _f_expected() -> dict[int, float]:
    """Hand formula for forwards: mean/std from the F pool (F2, F1, F3)."""
    g = np.array([8.0, 10.0, 6.0])
    a = np.array([8.0, 5.0, 6.0])
    line = {1: (10, 5), 2: (8, 8), 3: (6, 6), 4: (4, 4), 5: (2, 2), 6: (1, 1)}
    return {pid: (G - g.mean()) / g.std() + (A - a.mean()) / a.std()
            for pid, (G, A) in line.items()}


def test_small_pool_zscores():
    """Per-group z-scores, applied to every player in the group."""
    projs, cfg = _small_pool_category()
    by_pid = {v.player_id: v for v in valuate(projs, cfg)}
    exp = _f_expected()
    for pid, want in exp.items():
        assert by_pid[pid].raw_value == pytest.approx(want, abs=1e-9)
    # spot-check the hand-computed numbers in the docstring
    assert by_pid[2].raw_value == pytest.approx(1.336306, abs=1e-6)
    assert by_pid[4].raw_value == pytest.approx(-4.320318, abs=1e-6)
    # players outside the F pool are genuinely negative, not 0
    for pid in (4, 5, 6):
        assert by_pid[pid].raw_value < 0
    # lone D: zero spread -> 0; goalies: no goalie categories -> 0, no z-scores
    assert by_pid[7].raw_value == 0.0
    assert by_pid[8].raw_value == 0.0 and by_pid[8].cat_zscores == {}
    assert by_pid[9].raw_value == 0.0 and by_pid[9].cat_zscores == {}


def test_small_pool_replacement():
    """Replacement level = raw_value of the (pool_size+1)th player in the
    group: F4 for forwards; 0.0 for D (no 2nd D) and G (no categories)."""
    projs, cfg = _small_pool_category()
    levels = replacement_levels(projs, cfg)
    assert levels["F"] == pytest.approx(_f_expected()[4], abs=1e-9)
    assert levels["F"] == pytest.approx(-4.320318, abs=1e-6)
    assert levels["D"] == 0.0
    assert levels["G"] == 0.0


def test_small_pool_vorp():
    """VORP = raw_value - replacement_used; F4 (the replacement player)
    sits at exactly 0, and the pool's VORPs keep their order."""
    projs, cfg = _small_pool_category()
    vals = valuate(projs, cfg)
    by_pid = {v.player_id: v for v in vals}
    for v in vals:
        assert v.vorp == pytest.approx(v.raw_value - v.replacement_used, abs=1e-9)
    repl = _f_expected()[4]
    for pid in (1, 2, 3, 4, 5, 6):
        assert by_pid[pid].replacement_used == pytest.approx(repl, abs=1e-9)
    assert by_pid[4].vorp == pytest.approx(0.0, abs=1e-9)
    assert by_pid[2].vorp > by_pid[1].vorp > by_pid[3].vorp > 0
    assert by_pid[5].vorp < 0 and by_pid[6].vorp < by_pid[5].vorp
    for pid in (7, 8, 9):
        assert by_pid[pid].replacement_used == 0.0 and by_pid[pid].vorp == 0.0


def test_no_banned_identifiers():
    """No identifier in valuation.py matches /SCARCITY|BONUS|MULTIPLIER|PREMIUM/i."""
    import re
    path = Path(__file__).resolve().parent.parent / "src" / "valuation" / "valuation.py"
    text = path.read_text()
    for i, line in enumerate(text.splitlines(), 1):
        assert not re.search(r"SCARCITY|BONUS|MULTIPLIER|PREMIUM", line, re.I), (
            f"Banned identifier pattern found at line {i}: {line.strip()}"
        )
