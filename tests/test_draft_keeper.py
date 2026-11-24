"""Tests for src/draft/keeper.py -- hand-computed keeper values.

Curves (peak 1.00): forwards 20 .86, 21 .90, 22 .94, 23-33 1.00, 34 .77,
35 .72, 36 .67.
Two future seasons, discount .5, raw 100 and replacement 40 unless noted.
"""
from __future__ import annotations

import pytest

from src.draft.keeper import age_ratio, apply_keeper, future_values, load_curves
from src.projections.common import age_at
from src.valuation.valuation import PlayerValue

CURVES = {"F": {20: .86, 21: .90, 22: .94, **{a: 1.0 for a in range(23, 34)},
                34: .77, 35: .72, 36: .67},
          "D": {20: .82, 21: .86}}


def pv(pid, pos, raw, repl, total=0.0):
    return PlayerValue(player_id=pid, position=pos, raw_value=raw,
                       vorp=raw - repl, cat_zscores={}, replacement_used=repl,
                       total_value=total)


def test_future_values_young_old_goalie_and_below_replacement():
    vals = [pv(1, "C", 100, 40), pv(2, "L", 100, 40), pv(3, "G", 50, 20),
            pv(4, "R", 30, 40), pv(5, "C", 100, 40)]
    ages = {1: 20.0, 2: 34.0, 3: 30.0, 4: 20.0, 5: None}
    f = future_values(vals, ages, years=2, discount=0.5, curves=CURVES)
    # age 20: .5*(100*.90/.86 - 40) + .25*(100*.94/.86 - 40)
    #       = .5*64.651 + .25*69.302 = 49.651
    assert f[1] == pytest.approx(0.5 * (9000 / 86 - 40) + 0.25 * (9400 / 86 - 40))
    assert f[1] == pytest.approx(49.651, abs=1e-3)
    # age 34: .5*(100*.72/.77 - 40) + .25*(100*.67/.77 - 40) = 38.506
    assert f[2] == pytest.approx(38.506, abs=1e-3)
    # goalie: flat curve, 30 a season -> .5*30 + .25*30
    assert f[3] == pytest.approx(22.5)
    # raw 30 under replacement 40 even after aging up: worth nothing
    assert f[4] == 0.0
    # unknown age: no aging -> .75 * 60
    assert f[5] == pytest.approx(45.0)
    # the young forward is now worth more than the veteran
    assert f[1] > f[2]


def test_zero_years_and_clamped_ages():
    assert future_values([pv(1, "C", 100, 40)], {1: 20.0}, 0, .5, CURVES) == {1: 0.0}
    # below the table clamps to its first age (20): 19 -> 20 is flat
    assert age_ratio("F", 19.0, 1, CURVES) == pytest.approx(1.0)
    assert age_ratio("D", 20.0, 1, CURVES) == pytest.approx(.86 / .82)
    assert age_ratio("G", 25.0, 3, CURVES) == 1.0


def test_apply_keeper_copies():
    vals = [pv(1, "C", 100, 40, total=60.0)]
    out = apply_keeper(vals, {1: 10.0})
    assert out[0].total_value == 70.0 and vals[0].total_value == 60.0


def test_real_curves_load_and_age():
    c = load_curves()
    assert c["F"][25] == 1.0 and c["D"][26] == 1.0
    # born 2005-06-01, season 2026-27 -> age at 2027-02-01 is 21
    assert age_at("2005-06-01", 20262027) == 21.0
