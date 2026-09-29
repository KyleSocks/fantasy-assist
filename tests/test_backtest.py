"""Tests for src/projections/backtest.py — OLD-vs-NEW age handling validation.

Expected values are hand-computed:

- ``age_factor`` (OLD single-point factor) follows the pre level-curve
  piecewise formula:
    age <= 22 -> 1.0 (rookie prior); age <= 27 -> 1.00;
    age <= 31 -> 0.97 ** (age - 27); else -> 0.93 ** (age - 31) * 0.97 ** 4.
  E.g. age_factor(30) = 0.97 ** 3 = 0.912673;
       age_factor(32) = 0.93 * 0.97 ** 4 = 0.8233223133;
       age_factor(34) = 0.93 ** 3 * 0.97 ** 4 = 0.71209146877317.

- ``_ranks`` assigns 1-based ranks with average ties:
    _ranks([1, 2, 2, 4]) == [1, 2.5, 2.5, 4].

- ``_spearman`` is the cosine of the deviation-from-identity rank vectors:
    identical non-identity arrays -> 1.0;
    _spearman([1, 3, 2, 4], [1, 4, 2, 3]) -> num=3, den=sqrt(12) -> sqrt(3)/2.

- ``_metrics`` returns (MAE, RMSE, Spearman):
    _metrics([1, 3, 2, 4], [1, 4, 2, 3]) == (0.5, sqrt(0.5), sqrt(3)/2).

- ``_old_final`` is regressed_rate * age_factor(age) * proj_gp:
    with regressed_rate=2.0, proj_gp=80.0:
      age=20 -> 160.0; age=30 -> 160 * 0.912673; age=32 -> 160 * 0.8233223133.

- The last test checks the sum-back invariant on the real database (the same
  invariant the CLI Section 5 enforces): for a named player,
  regressed_rate * age_adj * proj_gp must equal final within 1e-6.
"""
from __future__ import annotations

import math

import pytest

from src.projections.backtest import (
    age_factor, _ranks, _spearman, _metrics, _old_final,
)
from src.projections.projections import explain

DB_PATH = "data/hockey.db"
CATS = ("goals", "assists", "shots", "blocks", "hits", "pp_points")


class _FakeProj:
    """Minimal stand-in carrying only the ``terms`` dict ``_old_final`` reads."""

    def __init__(self, terms):
        self.terms = terms


# ─── age_factor (OLD single-point factor) ───


def test_age_factor_young_and_peak():
    # age <= 22 handled by rookie prior -> 1.0; peak band (<= 27) -> 1.00
    assert age_factor(None) == 1.0
    assert age_factor(21) == 1.0
    assert age_factor(22) == 1.0
    assert age_factor(27) == 1.00


def test_age_factor_decline_band():
    # 28..31: 0.97 ** (age - 27)
    assert age_factor(28) == pytest.approx(0.97, abs=1e-12)
    assert age_factor(29) == pytest.approx(0.97 ** 2, abs=1e-12)
    assert age_factor(30) == pytest.approx(0.912673, abs=1e-6)
    assert age_factor(31) == pytest.approx(0.97 ** 4, abs=1e-12)


def test_age_factor_veteran_band():
    # 31+: 0.93 ** (age - 31) * 0.97 ** 4, checked both against the closed
    # form and the hand-computed decimals.
    assert age_factor(32) == pytest.approx(0.93 ** 1 * 0.97 ** 4, abs=1e-12)
    assert age_factor(33) == pytest.approx(0.93 ** 2 * 0.97 ** 4, abs=1e-12)
    assert age_factor(34) == pytest.approx(0.93 ** 3 * 0.97 ** 4, abs=1e-12)
    assert age_factor(32) == pytest.approx(0.8233223133, abs=1e-6)
    assert age_factor(33) == pytest.approx(0.765689751369, abs=1e-8)
    assert age_factor(34) == pytest.approx(0.71209146877317, abs=1e-8)


def test_age_factor_veteran_monotone_decay():
    # The OLD factor decays strictly with age through the veteran band.
    assert age_factor(34) < age_factor(33) < age_factor(32) < age_factor(31) < 1.0


# ─── _ranks ───


def test_ranks_no_ties():
    assert _ranks([1, 2, 3]) == [1, 2, 3]
    assert _ranks([5, 3, 1]) == [3, 2, 1]
    # non-identity but no ties
    assert _ranks([1, 3, 2, 4]) == [1, 3, 2, 4]


def test_ranks_average_ties():
    # middle two values tie -> average rank (0 + 1)/2 + 1 = 2.5... the group
    # spans sorted positions 1..2 -> (1 + 2 - 1)/2 + 1 = 2.5
    assert _ranks([1, 2, 2, 4]) == [1, 2.5, 2.5, 4]
    # all tied -> every element gets the average rank (0 + 2)/2 + 1 = 2
    assert _ranks([2, 2, 2]) == [2, 2, 2]


# ─── _spearman ───


def test_spearman_identical_non_identity():
    # Identical arrays that are NOT the identity permutation -> 1.0
    assert _spearman([1, 2, 4, 3], [1, 2, 4, 3]) == pytest.approx(
        1.0, abs=1e-12)


def test_spearman_zero_inner_product():
    # Orthogonal deviation vectors -> 0.0 (num = 0)
    assert _spearman([1, 2, 4, 3], [3, 4, 1, 2]) == pytest.approx(
        0.0, abs=1e-12)


def test_spearman_hand_value():
    # _spearman([1,3,2,4],[1,4,2,3]): num = 3, den = sqrt(2 * 6) = sqrt(12)
    # -> 3 / sqrt(12) = sqrt(3) / 2
    assert _spearman([1, 3, 2, 4], [1, 4, 2, 3]) == pytest.approx(
        math.sqrt(3) / 2, abs=1e-9)


def test_spearman_short_series():
    # Fewer than 3 points -> defined as 0.0
    assert _spearman([1, 2], [2, 1]) == 0.0
    assert _spearman([], []) == 0.0


# ─── _metrics ───


def test_metrics_empty_and_single():
    assert _metrics([], []) == (0.0, 0.0, 0.0)
    assert _metrics([5.0], [5.0]) == (0.0, 0.0, 0.0)


def test_metrics_perfect_match():
    # Identical identity arrays -> MAE 0, RMSE 0, Spearman 0.0 (degenerate)
    mae, rmse, rho = _metrics([1, 2, 3, 4], [1, 2, 3, 4])
    assert mae == 0.0
    assert rmse == 0.0
    assert rho == 0.0


def test_metrics_hand_value():
    # preds [1,3,2,4] vs actuals [1,4,2,3]:
    #   MAE = (0 + 1 + 0 + 1)/4 = 0.5
    #   RMSE = sqrt(2/4) = sqrt(0.5)
    #   Spearman = sqrt(3)/2 (see test_spearman_hand_value)
    mae, rmse, rho = _metrics([1, 3, 2, 4], [1, 4, 2, 3])
    assert mae == pytest.approx(0.5, abs=1e-12)
    assert rmse == pytest.approx(math.sqrt(0.5), abs=1e-12)
    assert rho == pytest.approx(math.sqrt(3) / 2, abs=1e-9)


def test_metrics_mae_rmse_known():
    # preds [2,1,4,3] vs actuals [1,2,3,4]: every value off by 1
    #   MAE = 1.0, RMSE = sqrt(4/4) = 1.0
    mae, rmse, _ = _metrics([2, 1, 4, 3], [1, 2, 3, 4])
    assert mae == pytest.approx(1.0, abs=1e-12)
    assert rmse == pytest.approx(1.0, abs=1e-12)


# ─── _old_final ───


def test_old_final_hand_values():
    terms = {"regressed_rate": {"goals": 2.0}, "proj_gp": 80.0}
    proj = _FakeProj(terms)
    # age=20 -> factor 1.0 -> 2.0 * 1.0 * 80.0 = 160.0
    assert _old_final(proj, "goals", 20) == pytest.approx(160.0, abs=1e-9)
    # age=None -> factor 1.0 -> 160.0
    assert _old_final(proj, "goals", None) == pytest.approx(160.0, abs=1e-9)
    # age=30 -> 0.97 ** 3 = 0.912673 -> 160 * 0.912673 = 146.02768
    assert _old_final(proj, "goals", 30) == pytest.approx(146.02768, abs=1e-5)
    # age=32 -> 0.93 * 0.97 ** 4 = 0.8233223133 -> 160 * 0.8233223133
    assert _old_final(proj, "goals", 32) == pytest.approx(
        160.0 * 0.8233223133, abs=1e-6)


# ─── sum-back invariant on the real DB (CLI Section 5) ───


def test_sum_back_benn_real_db():
    """explain() must satisfy regressed_rate * age_adj * proj_gp == final."""
    terms = explain(8473994, 20242025, DB_PATH)
    assert terms, "Benn (8473994) should have a projection for 20242025"
    worst = max(
        abs(terms["regressed_rate"][cat] * terms["age_adj"]
            * terms["proj_gp"] - terms["final"][cat])
        for cat in CATS
    )
    assert worst < 1e-6
