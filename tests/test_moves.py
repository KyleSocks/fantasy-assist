"""Roster-change log (src/inseason/moves.py): every expected event below is
worked out by hand from the two snapshots.

Before (fetched 09:00)             After (fetched 21:00)
  1 Aces:   Ann F, Bob D, Cy G       1 Aces:   Ann F, Cy G (IR), Dee F
  2 Bolts:  Dee F, Eve D             2 Bolts:  Bob D, Eve D, Fay F
  3 Comets: Gus F                    3 Comets: Gus F (now on NHL team NYR)

Aces:   Bob D left for Bolts and Dee F arrived from Bolts (a 1-for-1 trade),
        Cy G went to IR.
Bolts:  Fay F added (she was on no team).
Comets: nothing -- Gus only changed NHL teams.
"""
from src.inseason import moves


def P(name, team="BOS", pos=("C",), inactive=False):
    return {"name": name, "team": team, "positions": list(pos),
            "inactive": inactive, "injury": None}


def snap(fetched, teams):
    return {"league_id": 1, "my_team_id": 1, "fetched": fetched,
            "teams": {str(t): {"name": n, "players": ps}
                      for t, (n, ps) in teams.items()}}


BEFORE = snap("2026-11-16T09:00", {
    1: ("Aces", [P("Ann F"), P("Bob D", pos=("D",)), P("Cy G", pos=("G",))]),
    2: ("Bolts", [P("Dee F"), P("Eve D", pos=("D",))]),
    3: ("Comets", [P("Gus F", team="CHI")])})
AFTER = snap("2026-11-16T21:00", {
    1: ("Aces", [P("Ann F"), P("Cy G", pos=("G",), inactive=True), P("Dee F")]),
    2: ("Bolts", [P("Bob D", pos=("D",)), P("Eve D", pos=("D",)), P("Fay F")]),
    3: ("Comets", [P("Gus F", team="NYR")])})


def _short(events):
    return [(e["type"], e["team_id"], e.get("from_team_id"), e["player"]["name"])
            for e in events]


def test_diff_hand_computed():
    events = moves.diff(BEFORE, AFTER)
    assert _short(events) == [
        ("trade", 1, 2, "Dee F"),      # Aces got Dee from Bolts
        ("ir", 1, None, "Cy G"),
        ("add", 2, None, "Fay F"),
        ("trade", 2, 1, "Bob D"),      # Bolts got Bob from Aces
    ]
    assert all(e["since"] == "2026-11-16T09:00" and e["at"] == "2026-11-16T21:00"
               for e in events)
    assert events[0]["player"] == {"name": "Dee F", "team": "BOS", "positions": ["C"]}


def test_drop_and_activate():
    later = snap("2026-11-17T09:00", {
        1: ("Aces", [P("Ann F"), P("Cy G", pos=("G",)), P("Dee F")]),
        2: ("Bolts", [P("Bob D", pos=("D",)), P("Eve D", pos=("D",))]),
        3: ("Comets", [P("Gus F", team="NYR")])})
    assert _short(moves.diff(AFTER, later)) == [
        ("activate", 1, None, "Cy G"), ("drop", 2, None, "Fay F")]


def test_one_way_move_between_teams_is_a_claim_not_a_trade():
    """Bolts let Dee F go and the Aces picked her up; nothing went back."""
    after = snap("2026-11-16T21:00", {
        1: ("Aces", [P("Ann F"), P("Bob D", pos=("D",)), P("Cy G", pos=("G",)), P("Dee F")]),
        2: ("Bolts", [P("Eve D", pos=("D",))]),
        3: ("Comets", [P("Gus F", team="CHI")])})
    events = moves.diff(BEFORE, after)
    assert _short(events) == [("claim", 1, 2, "Dee F")]
    counts = moves.summarize(events, [1, 2, 3])
    assert (counts[1]["add"], counts[1]["trade"]) == (1, 0)
    assert (counts[2]["drop"], counts[2]["trade"]) == (1, 0)


def test_first_snapshot_is_the_baseline():
    assert moves.diff(None, AFTER) == []
    assert moves.diff({"teams": {}}, AFTER) == []
    assert moves.diff(AFTER, AFTER) == []


def test_team_that_comes_back_empty_is_flagged_not_logged_as_drops():
    """Bolts' page comes back empty. emptied_teams names it (the refresh then
    keeps the old files), and even if diffed, nobody on it reads as dropped
    and Dee F -- last seen on the unreadable team -- isn't called an add."""
    broken = snap("2026-11-16T21:00", {
        1: ("Aces", [P("Ann F"), P("Cy G", pos=("G",), inactive=True), P("Dee F")]),
        2: ("Bolts", []),
        3: ("Comets", [P("Gus F", team="NYR")])})
    assert moves.emptied_teams(BEFORE, broken) == ["Bolts"]
    assert moves.emptied_teams(BEFORE, AFTER) == []
    assert moves.emptied_teams(None, AFTER) == []
    names = [e["player"]["name"] for e in moves.diff(BEFORE, broken)]
    assert "Dee F" not in names and "Eve D" not in names


def test_new_team_has_no_before_so_its_players_are_not_adds():
    grown = snap("2026-11-16T21:00", {
        1: ("Aces", [P("Ann F"), P("Bob D", pos=("D",)), P("Cy G", pos=("G",))]),
        2: ("Bolts", [P("Dee F"), P("Eve D", pos=("D",))]),
        3: ("Comets", [P("Gus F", team="CHI")]),
        4: ("Dynamo", [P("Hal F"), P("Ivy D", pos=("D",))])})
    assert moves.diff(BEFORE, grown) == []


def test_shared_name_is_matched_with_the_nhl_team():
    """Two Sebastian Ahos rostered: the CAR one stays put, the NYI one is
    dropped. Matching on the name alone would see no change."""
    a = snap("t1", {1: ("Aces", [P("Sebastian Aho", "CAR")]),
                    2: ("Bolts", [P("Sebastian Aho", "NYI", ("D",))])})
    b = snap("t2", {1: ("Aces", [P("Sebastian Aho", "CAR")]),
                    2: ("Bolts", [P("Eve D", pos=("D",))])})
    assert _short(moves.diff(a, b)) == [
        ("add", 2, None, "Eve D"), ("drop", 2, None, "Sebastian Aho")]
    assert moves.diff(a, b)[1]["player"]["team"] == "NYI"


def test_append_keeps_start_and_caps_length(monkeypatch):
    events = moves.diff(BEFORE, AFTER)
    log = moves.append(None, events, BEFORE, AFTER)
    assert log["started"] == "2026-11-16T09:00" and log["updated"] == "2026-11-16T21:00"
    assert len(log["events"]) == 4
    monkeypatch.setattr(moves, "MAX_EVENTS", 5)
    log2 = moves.append(log, events, AFTER, AFTER)
    assert log2["started"] == "2026-11-16T09:00"      # never moves forward
    # 4 old + 4 new = 8, capped at 5: the 3 oldest go, so the first one
    # kept is the 4th of the original batch
    assert len(log2["events"]) == 5 and log2["events"][0] == events[3]
    # no previous snapshot at all: tracking starts with this pull
    assert moves.append(None, [], None, AFTER)["started"] == "2026-11-16T21:00"


def test_summarize_counts_a_trade_once_per_side():
    counts = moves.summarize(moves.diff(BEFORE, AFTER), [1, 2, 3])
    # the 1-for-1 is ONE trade for each side, not two
    assert counts[1] == {"add": 0, "drop": 0, "trade": 1, "ir": 1,
                         "last": "2026-11-16T21:00"}
    assert counts[2] == {"add": 1, "drop": 0, "trade": 1, "ir": 0,
                         "last": "2026-11-16T21:00"}
    assert counts[3] == {"add": 0, "drop": 0, "trade": 0, "ir": 0, "last": None}
