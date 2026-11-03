"""plan_slot -- hand-computed. Dates are Mon-Wed of one fantasy week."""
import pytest

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


# ── review fixes: protected holds, goalie slots ───────────────────────

from tests.test_inseason import ctx, db  # noqa: E402,F401,F811


def test_protected_hold_is_never_dropped_for_streamers(ctx):  # noqa: F811
    """Drops are free, but a HOLD isn't cut for a light week: Dee Man
    (1.84/gm vs 1.50 replacement, 10 GP) keeps his spot even when a
    streamer adds more inside the window -- unless forced."""
    from src.inseason.roster import roster_plan
    r = roster_plan(ctx, {}, {5: {"D"}}, 4, "2026-11-01", 4, [4])
    assert r["protected"] and "Dee Man" in r["protected"]
    assert r["steps"] == [] and r["plan_value"] == pytest.approx(r["keep_value"])
    forced = roster_plan(ctx, {}, {5: {"D"}}, 4, "2026-11-01", 4, [4], force=True)
    assert forced["protected"] is None


def test_severe_underperformer_loses_protection(ctx, monkeypatch):  # noqa: F811
    """Ovi: a HOLD on his blend, but producing below replacement so far.
    Under protect_min_gp games he's protected; past it he isn't."""
    from src.inseason.roster import drop_protection
    assert drop_protection(ctx, 1, "2026-11-01")          # 10 GP < 15
    monkeypatch.setitem(ctx.icfg, "protect_min_gp", 10)
    assert drop_protection(ctx, 1, "2026-11-01") is None
    # a hold producing above replacement stays protected at any GP
    assert drop_protection(ctx, 4, "2026-11-01")


def test_goalies_fill_g_slots_in_plan_and_audit(ctx):  # noqa: F811
    """Goalies used to be worth 0 (no G slot), so a plan would drop one."""
    from src.inseason.roster import active_slots, audit, roster_plan
    assert active_slots(ctx).get("G") == 1
    a = audit(ctx, {6: {"G"}, 7: {"G"}}, "2026-11-01", 4)
    goalie_rows = {r["id"]: r for r in a["players"]}
    assert sum(goalie_rows[g]["lineup_adds"] for g in (6, 7)) > 0
    assert sum(goalie_rows[g]["starts"] for g in (6, 7)) > 0
    # holding a goalie against a skater candidate: the skater can't take
    # a G slot, so the plan keeps the goalie
    r = roster_plan(ctx, {}, {5: {"D"}}, 7, "2026-11-01", 4, [4], force=True)
    assert r["keep_value"] > 0
    assert all(s["add"]["id"] != 5 or r["plan_value"] >= r["keep_value"]
               for s in r["steps"])
