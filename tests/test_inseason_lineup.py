"""Lineup optimiser + marginal values -- hand-computed.

Slots: LW 1, D 1, Util 1.
"""
import pytest

from src.inseason.lineup import best_lineup, can_fit, marginal_values

SLOTS = {"LW": 1, "D": 1, "Util": 1}


def test_greedy_reassigns_to_fit_more_starters():
    # A (LW,RW) 5 and B (LW) 4: A must move to Util so B can take LW.
    total, assign = best_lineup([(1, {"LW", "RW"}, 5.0), (2, {"LW"}, 4.0),
                                 (3, {"D"}, 3.0), (4, {"C"}, 2.0)], SLOTS)
    assert total == 12.0                     # 5 + 4 + 3; C sits (no slot left)
    assert assign[2] == "LW" and assign[1] == "Util" and assign[3] == "D"
    assert 4 not in assign


def test_goalie_never_util_and_zero_value_sits():
    total, assign = best_lineup([(1, {"G"}, 9.0), (2, {"D"}, 0.0)], SLOTS)
    assert total == 0.0 and assign == {}


def test_marginal_value_d_man_beats_winger_when_wings_full():
    roster = {1: {"LW"}, 2: {"LW", "RW"}}      # fill LW + Util on night 1
    games = {1: {"n1": 4.0}, 2: {"n1": 3.0, "n2": 3.0},
             10: {"n1": 3.5, "n2": 3.5},       # winger candidate
             11: {"n1": 2.0, "n2": 2.0}}       # D candidate
    m = marginal_values(roster, {10: {"LW"}, 11: {"D"}}, games,
                        ["n1", "n2"], SLOTS)
    # night 1: LW=4, Util=3.5 (bumps the 3.0) -> winger adds only 0.5
    assert m[10] == {"n1": 0.5, "n2": 3.5}
    # D slot is empty both nights -> full game value
    assert m[11] == {"n1": 2.0, "n2": 2.0}


# ── can_fit: open seats after re-arranging multi-position starters ──────

KYLE = {"C": 2, "LW": 2, "RW": 2, "D": 4, "Util": 1, "G": 2}


def test_can_fit_slides_a_dual_winger_to_free_the_other_wing():
    """Kyle, Sun 11/8: Hischier/Carlsson/Bennett (C), Pospisil (C,RW),
    Verhaeghe (LW), Tippett (LW,RW), McAvoy (D). One arrangement shows
    '1 RW open' -- but Tippett can slide LW -> RW, so a C/LW like Granlund
    fits too. One more forward, at LW or RW; no C-only player fits."""
    tonight = [(1, {"C"}, 4.7), (2, {"C"}, 4.4), (3, {"LW"}, 4.5),
               (4, {"C", "RW"}, 4.3), (5, {"C"}, 5.2), (6, {"LW", "RW"}, 4.2),
               (7, {"D"}, 4.9)]
    fit = can_fit(tonight, KYLE)
    assert fit == {"C": 0, "LW": 1, "RW": 1, "D": 3, "G": 2, "F": 1}
    # and the optimiser agrees: Granlund (C,LW) adds his whole game
    base, _ = best_lineup(tonight, KYLE)
    with_g, a = best_lineup(tonight + [(8, {"C", "LW"}, 4.6)], KYLE)
    assert with_g - base == pytest.approx(4.6) and a[6] == "RW"


def test_can_fit_counts_overlap_not_sum():
    """Two empty wing seats reachable only one at a time stay 'one more'."""
    fit = can_fit([(1, {"LW", "RW"}, 1.0)], {"LW": 1, "RW": 1})
    assert fit["LW"] == 1 and fit["RW"] == 1 and fit["F"] == 1
    fit = can_fit([], {"LW": 1, "RW": 1, "Util": 1})
    assert fit["LW"] == 2 and fit["RW"] == 2 and fit["F"] == 3 and fit["D"] == 1


def test_can_fit_ignores_benched_players():
    """Players who wouldn't start (no seat) don't block anything."""
    tonight = [(1, {"G"}, 6.0), (2, {"G"}, 5.0), (3, {"G"}, 4.0)]
    assert can_fit(tonight, {"G": 2})["G"] == 0
    assert can_fit(tonight[:1], {"G": 2})["G"] == 1
