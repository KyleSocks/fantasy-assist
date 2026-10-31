"""Lineup optimiser + marginal values -- hand-computed.

Slots: LW 1, D 1, Util 1.
"""
from src.inseason.lineup import best_lineup, marginal_values

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
