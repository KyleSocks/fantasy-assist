"""Tests for src/projections/band.py — confidence band computation.

Pure unit tests; band tests against the real database were removed
because its numbers change with every ingest.
"""
from __future__ import annotations


import pytest

from src.projections.band import pop_std


# ─── pop_std (pure unit) ───


def test_pop_std_basic():
    # Hand-computed: mean 40.6667, population variance 42.8889 -> std 6.548961
    assert pop_std([40, 33, 49]) == pytest.approx(6.548961, abs=1e-5)
    # Constant values -> zero spread
    assert pop_std([5, 5, 5]) == 0.0
    # Fewer than 2 values -> defined as 0.0
    assert pop_std([3]) == 0.0
    assert pop_std([]) == 0.0
