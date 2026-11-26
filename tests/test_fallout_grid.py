"""Injury fallout, team ice-time shares and the goalie grid, on small
hand-built inputs."""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

import src.inseason.goalie_grid as gg
from src.inseason import lines
from src.inseason.fallout import fallout


def L(pid, name, team="AAA", pos="C", deploy=None):
    return NS(player_id=pid, name=name, team=team, position=pos, is_goalie=pos == "G",
              start_share=0.0, extra={"deploy": deploy} if deploy else {})


class Scorer:
    """value = 2 x goals + 1 x assists + 1 x pp_points"""
    points = True
    per_game = {}

    def value_stats(self, pid, pos, stats):
        return 2 * stats.get("goals", 0) + stats.get("assists", 0) + stats.get("pp_points", 0)


def test_team_ice_time_shares():
    """Two games for AAA. Game 1: P1 600 s at 5v5 and 120 s PP; four
    teammates 600 / 120 each, so the team had 3000/5 = 600 s at 5v5 and
    600/5 = 120 s of PP -> P1 on for 100% of both (all five skaters were).
    Game 2: P1 300 / 60, two teammates 900 / 240: team 2100/5 = 420 at
    5v5 and 540/5 = 108 PP. Over both: P1 900 of 1020 at 5v5 (88.2%),
    180 of 228 on the PP (78.9%)."""
    conn = sqlite3.connect(":memory:")
    lines.ensure_schema(conn)
    rows = [(1, "2026-11-01", "AAA", 1, "F1", "PP1", 600, 120, "[]")]
    rows += [(1, "2026-11-01", "AAA", q, "F1", "PP1", 600, 120, "[]") for q in (2, 3, 4, 5)]
    rows += [(2, "2026-11-03", "AAA", 1, "F1", "PP1", 300, 60, "[]"),
             (2, "2026-11-03", "AAA", 6, "F2", "PP1", 900, 240, "[]"),
             (2, "2026-11-03", "AAA", 7, "F2", "PP1", 900, 240, "[]")]
    conn.executemany("INSERT INTO game_deployment VALUES (?,?,?,?,?,?,?,?,?)", rows)
    r = lines.load_roles(conn)[1]
    assert r["es_share"] == pytest.approx(900 / 1020)
    assert r["pp_share"] == pytest.approx(180 / 228)
    assert r["team"] == "AAA"


def _ctx():
    """AAA's latest game is 11-05. Star (1) last played 11-01, usually F1
    + PP1. Up (2) moved from F3/PP2 to F1/PP1 with PP time up 60 s and an
    ice-time bump worth 0.1 goals -> +0.2 a game. Same (3) was already on
    F1/PP1, nothing changed: not a taker. Depth (4) is on PP2 as before but
    PP time up 40 s (>= 30). BBB's player (9) never counts."""
    roles = {
        1: {"team": "AAA", "date": "2026-11-01", "line": "F1", "pp": "PP1",
            "usual_line": "F1", "usual_pp": "PP1"},
        2: {"team": "AAA", "date": "2026-11-05", "line": "F1", "pp": "PP1", "usual_line": "F3",
            "usual_pp": "PP2", "change": "up", "recent_pp_toi": 180, "prior_pp_toi": 120},
        3: {"team": "AAA", "date": "2026-11-05", "line": "F1", "pp": "PP1", "usual_line": "F1",
            "usual_pp": "PP1", "change": None, "recent_pp_toi": 200, "prior_pp_toi": 200},
        4: {"team": "AAA", "date": "2026-11-05", "line": "F4", "pp": "PP2", "usual_line": "F4",
            "usual_pp": "PP2", "change": None, "recent_pp_toi": 100, "prior_pp_toi": 60},
        9: {"team": "BBB", "date": "2026-11-05", "line": "F1", "pp": "PP1", "usual_line": "F3",
            "usual_pp": "PP2", "change": "up", "recent_pp_toi": 200, "prior_pp_toi": 0},
    }
    ls = {1: L(1, "Star"), 2: L(2, "Up", deploy={"delta": {"goals": 0.1}}),
          3: L(3, "Same", deploy={"delta": {}}), 4: L(4, "Depth"), 9: L(9, "Other", team="BBB")}
    sc = Scorer()
    sc.per_game = {1: 5.0, 2: 3.0, 3: 4.0, 4: 1.5, 9: 2.0}
    return NS(roles=roles, lines=ls, scorer=sc)


def test_fallout_finds_the_missing_regular_and_who_took_over():
    out = fallout(_ctx())
    assert [x["name"] for x in out] == ["Star"]
    s = out[0]
    assert s["slot"] == ["PP1", "F1"] and s["missing"] and s["last_game"] == "2026-11-01"
    assert [t["name"] for t in s["takers"]] == ["Up", "Depth"]
    up = s["takers"][0]
    assert up["took"] == ["PP1", "F1"] and up["gain"] == pytest.approx(0.2)
    assert up["role"] == "F1, PP1" and up["was"] == "F3, PP2" and up["pp_gain_sec"] == 60
    assert s["takers"][1]["took"] == [] and s["takers"][1]["pp_gain_sec"] == 40


def test_fallout_long_injury_uses_season_pp_rank():
    """Gone (5) has no shift data any more (out longer than the stored
    games) but Yahoo lists him injured; his season PP time is the most on
    AAA, so his slot is PP1. Same (3), on PP1 with ice time above his
    season level, counts as a taker now."""
    ctx = _ctx()
    ctx.lines[5] = L(5, "Gone", deploy={"season_pp": 240})
    ctx.lines[3].extra["deploy"] = {"delta": {"assists": 0.1}, "season_pp": 200}
    out = {x["name"]: x for x in fallout(ctx, {5: {"tag": "IR-LT", "back": "2027-01-25"}})}
    g = out["Gone"]
    assert g["slot"] == ["PP1"] and not g["missing"] and g["tag"] == "IR-LT"
    assert "Same" in [t["name"] for t in g["takers"]]


def test_goalie_grid(monkeypatch):
    """AAA plays 11-16 (vs BBB) and 11-17 (vs CCC). Starter S (60% of
    starts, 6.0 a start) and backup B (40%, 4.0); on 11-17 the B2B rule
    flips them to 30/70. Injured third goalie X's 10% is ignored."""
    S = NS(player_id=1, name="S", team="AAA", is_goalie=True, start_share=0.6)
    B = NS(player_id=2, name="B", team="AAA", is_goalie=True, start_share=0.4)
    X = NS(player_id=3, name="X", team="AAA", is_goalie=True, start_share=0.1)
    ctx = NS(lines={1: S, 2: B, 3: X}, factors={},
             team_games=lambda team, a, b: {"2026-11-16": [(a, "BBB")],
                                            "2026-11-17": [(a, "CCC")]}.get(a, []))
    monkeypatch.setattr(gg, "goalie_share", lambda c, g, d: (
        {1: 0.3, 2: 0.7}[g.player_id] if d == "2026-11-17" else g.start_share))
    monkeypatch.setattr(gg, "expected_line", lambda g, opp, assume_start=False: g.player_id)
    monkeypatch.setattr(gg, "_value", lambda c, g, stats: {1: 6.0, 2: 4.0}[stats])
    out = gg.grid(ctx, "2026-11-16", 3, injured={3})
    t = out["teams"][0]
    assert out["dates"] == ["2026-11-16", "2026-11-17", "2026-11-18"] and t["games"] == 2
    d1 = t["cells"]["2026-11-16"]
    assert d1["opp"] == "BBB" and [g["name"] for g in d1["goalies"]] == ["S", "B"]
    assert d1["goalies"][0]["chance"] == pytest.approx(0.6)
    assert d1["goalies"][0]["expected"] == pytest.approx(3.6) and not d1["goalies"][0]["b2b"]
    d2 = t["cells"]["2026-11-17"]
    assert [g["name"] for g in d2["goalies"]] == ["B", "S"] and d2["goalies"][0]["b2b"]
    # likely starts (40%+): S on 11-16 (6.0), B on both nights (4.0, 4.0).
    # Grade = share of those starts that are better: S none -> A; B one of
    # three (33%) -> B
    assert d1["goalies"][0]["grade"] == "A"
    assert d2["goalies"][0]["grade"] == "B"


def test_returning_due_back_puts_stand_ins_at_risk():
    """Star (1) is due back 11-20 (within 7 days of 11-16): Up (2), who took
    his F1/PP1 spot with ice time worth +0.2 a game, would lose that. Late
    (5) is due back 12-01: too far out."""
    from src.inseason.fallout import returning
    ctx = _ctx()
    ctx.lines[5] = L(5, "Late", deploy={"season_pp": 300})
    out = returning(ctx, {1: {"tag": "IR", "back": "2026-11-20"},
                          5: {"tag": "IR-LT", "back": "2026-12-01"}}, "2026-11-16")
    assert [x["name"] for x in out] == ["Star"]
    s = out[0]
    assert s["status"] == "due" and s["back"] == "2026-11-20" and s["slot"] == ["PP1", "F1"]
    assert s["at_risk"][0]["name"] == "Up" and s["at_risk"][0]["loss"] == pytest.approx(-0.2)
    # day-to-day: back = the start date
    dtd = returning(ctx, {1: {"tag": "DTD", "back": None}}, "2026-11-16")
    assert dtd[0]["back"] == "2026-11-16"


def test_returning_just_back_lists_who_slid():
    """Vet (7) played AAA's latest game (11-05) after missing 2 of the 4
    stored ones (2 games vs the team's 4): just back on F1/PP1. Slid (8)
    was usually F1/PP1 and is demoted in that game -> listed with his
    ice-time loss (-0.15 a game from his adjustment)."""
    from src.inseason.fallout import returning
    ctx = _ctx()
    for r in ctx.roles.values():
        r.setdefault("games", 4)
    ctx.roles[7] = {"team": "AAA", "date": "2026-11-05", "line": "F1", "pp": "PP1",
                    "usual_line": "F1", "usual_pp": "PP1", "games": 2}
    ctx.roles[8] = {"team": "AAA", "date": "2026-11-05", "line": "F3", "pp": "PP2", "usual_line": "F1",
                    "usual_pp": "PP1", "change": "down", "games": 4, "recent_pp_toi": 100,
                    "prior_pp_toi": 200}
    ctx.lines[7] = L(7, "Vet")
    ctx.lines[8] = L(8, "Slid", deploy={"delta": {"goals": -0.075}})
    out = {x["name"]: x for x in returning(ctx, {}, "2026-11-16")}
    v = out["Vet"]
    assert v["status"] == "back" and v["back"] == "2026-11-05"
    assert [t["name"] for t in v["at_risk"]] == ["Slid"]
    assert v["at_risk"][0]["loss"] == pytest.approx(-0.15) and v["at_risk"][0]["pp_gain_sec"] == -100
