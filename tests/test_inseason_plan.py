"""plan_slot -- hand-computed. Dates are Mon-Wed of one fantasy week."""
from src.inseason.plan import Step, plan_slot, week_key

D1, D2, D3 = "2026-11-02", "2026-11-03", "2026-11-04"
VALUES = {1: {D1: 1.0}, 2: {D2: 3.5}, 3: {D1: 2.0, D3: 2.0}}


def test_week_key_monday_start():
    assert week_key("2026-11-08") == "2026-11-02"   # Sunday -> its Monday
    assert week_key("2026-11-02") == "2026-11-02"


def test_one_add_picks_the_bigger_switch():
    # keep 1 (1) then 2 on D2 (3.5) = 4.5  beats  3 from D1 (2 + 2) = 4
    p = plan_slot(1, VALUES, [D1, D2, D3], 1)
    assert p.total == 4.5 and p.steps == [Step(D2, 2, 1)]


def test_two_adds_chain_without_readding():
    # 1 (1) -> 2 on D2 (3.5) -> 3 on D3 (2) = 6.5
    p = plan_slot(1, VALUES, [D1, D2, D3], 2)
    assert p.total == 6.5
    assert p.steps == [Step(D2, 2, 1), Step(D3, 3, 2)]
    assert p.holder_by_date == {D1: 1, D2: 2, D3: 3}


def test_waiver_player_not_addable_before_clearing():
    # 2 only clears on D3 (no game then): best is 3 from D1 = 4
    p = plan_slot(1, VALUES, [D1, D2, D3], 2, available_from={2: D3})
    assert p.total == 4.0 and p.steps == [Step(D1, 3, 1)]


def test_zero_adds_keeps_current():
    assert plan_slot(1, VALUES, [D1, D2, D3], 0).total == 1.0


def test_slot_eligibility_filters_pool():
    from src.inseason.plan import nhl_eligibility, slot_accepts
    # LW slot: 2 is C-only (Yahoo), 3 is C,LW -> only 3 can be added.
    # best: 3 from D1 (2 + 2) = 4, even with 2 adds
    p = plan_slot(1, VALUES, [D1, D2, D3], 2, slot_positions={"LW"},
                  eligibility={2: {"C"}, 3: {"C", "LW"}})
    assert p.total == 4.0 and p.steps == [Step(D1, 3, 1)]
    # no Yahoo data -> NHL primary position fallback (2 is a winger "L")
    p = plan_slot(1, VALUES, [D1, D2, D3], 1, slot_positions={"LW"},
                  positions={2: "L", 3: "C"})
    assert p.steps == [Step(D2, 2, 1)]
    assert nhl_eligibility("R") == {"RW"} and nhl_eligibility("X") == set()
    assert slot_accepts({"Util"}, {"D"}) and not slot_accepts({"Util"}, {"G"})
    assert not slot_accepts({"LW"}, {"C", "RW"})
