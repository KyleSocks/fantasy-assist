"""Deployment (src/inseason/lines.py) and luck (src/inseason/luck.py), with
every expected number worked out by hand."""
import json
import math
import sqlite3

import pytest

from src.inseason import lines, luck


def sh(pid, team, per, a, b):
    m = lambda s: f"{s // 60:02d}:{s % 60:02d}"   # noqa: E731
    return {"playerId": pid, "teamAbbrev": team, "period": per, "typeCode": 517,
            "startTime": m(a), "endTime": m(b)}


# Team A: goalie 100, forwards 1-6, defence 11-14. Team B: goalie 200,
# forwards 21-26, defence 31-34.
POS = {100: "G", 200: "G", **{i: "C" for i in (1, 2, 3, 4, 5, 6, 21, 22, 23, 24, 25, 26)},
       **{i: "D" for i in (11, 12, 13, 14, 31, 32, 33, 34)}}


def game():
    s = []
    for per in (1, 2):
        s += [sh(100, "A", per, 0, 1200), sh(200, "B", per, 0, 1200)]
    # period 1: line 1 + pair 1 for 0-600, line 2 + pair 2 for 600-1200 (both teams)
    for team, l1, l2, p1, p2 in (("A", (1, 2, 3), (4, 5, 6), (11, 12), (13, 14)),
                                 ("B", (21, 22, 23), (24, 25, 26), (31, 32), (33, 34))):
        for p in l1 + p1:
            s.append(sh(p, team, 1, 0, 600))
        for p in l2 + p2:
            s.append(sh(p, team, 1, 600, 1200))
    # period 2: A on the power play 0-120 with 1, 2, 3, 4 and 11 against
    # B's 21, 22, 31, 32 (four); then 5-on-5: line 1/pair 1 120-500, line
    # 2/pair 2 500-1200
    for p in (1, 2, 3, 4, 11):
        s.append(sh(p, "A", 2, 0, 120))
    for p in (21, 22, 31, 32):
        s.append(sh(p, "B", 2, 0, 120))
    for team, l1, l2, p1, p2 in (("A", (1, 2, 3), (4, 5, 6), (11, 12), (13, 14)),
                                 ("B", (21, 22, 23), (24, 25, 26), (31, 32), (33, 34))):
        for p in l1 + p1:
            s.append(sh(p, team, 2, 120, 500))
        for p in l2 + p2:
            s.append(sh(p, team, 2, 500, 1200))
    s.append({"typeCode": 505, "playerId": 1, "teamAbbrev": "A", "period": 1,
              "startTime": "05:00", "endTime": "05:00"})        # a goal event: ignored
    return s


def test_game_units_hand_worked():
    """5v5 seconds: line 1 = 600 + 380 = 980, line 2 = 600 + 700 = 1300, so
    the second unit to take the ice is F1 / D1. A's power play (120 s, five
    skaters vs four) is PP1 = 1, 2, 3, 4, 11; nobody on B has PP time."""
    u = lines.game_units(game(), POS)
    assert 100 not in u and 200 not in u                    # goalies left out
    assert {p: u[p]["line"] for p in (1, 4, 11, 13, 21, 24)} == {
        1: "F2", 4: "F1", 11: "D2", 13: "D1", 21: "F2", 24: "F1"}
    assert u[1]["es_sec"] == 980 and u[4]["es_sec"] == 1300
    assert sorted(u[4]["mates"]) == [5, 6] and u[13]["mates"] == [14]
    assert sorted(p for p, v in u.items() if v["pp"] == "PP1") == [1, 2, 3, 4, 11]
    assert u[1]["pp_sec"] == 120 and u[21]["pp_sec"] == 0 and u[21]["pp"] is None
    # 5v5 seconds with each teammate: 1 was out with 2, 3, 11, 12 for all
    # his 980; never with line 2 or pair 2. PP time doesn't count.
    assert u[1]["pairs"] == {2: 980, 3: 980, 11: 980, 12: 980}
    assert u[13]["pairs"] == {4: 1300, 5: 1300, 6: 1300, 14: 1300}


def test_goalie_found_without_a_position():
    pos = {k: v for k, v in POS.items() if v != "G"}
    u = lines.game_units(game(), pos)       # whole-period shifts = goalie
    assert 100 not in u and u[4]["line"] == "F1"


def _db():
    conn = sqlite3.connect(":memory:")
    lines.ensure_schema(conn)
    return conn


def test_load_roles_change_and_toi():
    """Player 7: F3 / no PP in the four games before, F2 / PP1 in the
    latest -> promoted. Player 8: PP1 before, PP2 now -> down. TOI windows:
    recent 3 GP, 300 s PP = 100 s/game; prior 9 GP, 450 s = 50 s/game."""
    conn = _db()
    rows = []
    for i, d in enumerate(["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]):
        rows += [(i, d, "T", 7, "F3", None, 600, 0, "[]"),
                 (i, d, "T", 8, "F1", "PP1", 800, 200, "[]")]
    rows += [(9, "2026-11-15", "T", 7, "F2", "PP1", 700, 150, json.dumps([8, 9])),
             (9, "2026-11-15", "T", 8, "F1", "PP2", 800, 60, "[]")]
    conn.executemany("INSERT INTO game_deployment VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO skater_toi_window VALUES (?,?,?,?,?,?,?,?,?)", [
        (7, "recent", 3, 3600, 3000, 300, 0, "a", "b"),
        (7, "prior", 9, 9900, 9000, 450, 0, "a", "b")])
    r = lines.load_roles(conn)
    assert (r[7]["line"], r[7]["pp"], r[7]["usual_line"], r[7]["usual_pp"]) == ("F2", "PP1", "F3", None)
    assert r[7]["change"] == "up" and r[7]["mates"] == [8, 9] and r[7]["games"] == 5
    assert r[8]["change"] == "down"
    assert r[7]["recent_pp_toi"] == 100 and r[7]["prior_pp_toi"] == 50
    assert r[7]["recent_toi"] == 1200 and r[7]["prior_toi"] == 1100
    assert lines.label(r[7]) == "F2, PP1" and lines.label(None) is None


def test_load_roles_without_tables_is_empty():
    assert lines.load_roles(sqlite3.connect(":memory:")) == {}


class _Fake:
    def __init__(self, shifts):
        self.shifts, self.urls = shifts, []

    def get_json(self, url):
        self.urls.append(url)
        return {"data": self.shifts}

    def get_paged(self, base, report, season):
        self.urls.append(base)
        return [{"playerId": 4, "gamesPlayed": 2, "timeOnIce": 2400, "evTimeOnIce": 2000,
                 "ppTimeOnIce": 240, "shTimeOnIce": 0}]


def test_ingest_fetches_only_new_recent_games():
    conn = _db()
    conn.execute("CREATE TABLE schedule (game_id INTEGER PRIMARY KEY, season_id INTEGER,"
                 " game_date TEXT, home_abbrev TEXT, away_abbrev TEXT, game_type INTEGER)")
    conn.execute("CREATE TABLE players (player_id INTEGER PRIMARY KEY, position TEXT)")
    conn.executemany("INSERT INTO players VALUES (?,?)", POS.items())
    # A plays 7 finished games (ids 1..7) and one tomorrow (8)
    conn.executemany("INSERT INTO schedule VALUES (?,?,?,?,?,2)", [
        (g, 20262027, f"2026-11-{g:02d}", "A", "B") for g in range(1, 9)])
    f = _Fake(game())
    # deployment for the last 5 (3..7); pair seconds for those plus the
    # older games, newest first (2, 1)
    assert lines.ingest_deployment(conn, f, 20262027, "2026-11-08") == {
        "deployment_games": 5, "pair_games": 7}
    assert [u.rsplit("=", 1)[1] for u in f.urls] == ["3", "4", "5", "6", "7", "2", "1"]
    assert {g for (g,) in conn.execute("SELECT DISTINCT game_id FROM game_pairs")} == set(range(1, 8))
    f.urls.clear()
    assert lines.ingest_deployment(conn, f, 20262027, "2026-11-08") == {
        "deployment_games": 0, "pair_games": 0}
    assert f.urls == []                                   # nothing new
    assert lines.ingest_toi_windows(conn, f, 20262027, "2026-11-16")["toi_windows"] == 2
    assert 'gameDate>="2026-11-09"' in f.urls[0] and 'gameDate<="2026-11-15 23:59:59"' in f.urls[0]
    assert 'gameDate>="2026-10-19"' in f.urls[1] and 'gameDate<="2026-11-08 23:59:59"' in f.urls[1]


# ── luck ──

def test_skater_luck_hand_worked():
    """Prior 30 goals on 300 shots (10%), forward rate 10%: usual = 10%.
    This season 9 goals on 40 shots: expected 4.0, luck +5.0,
    sd = sqrt(40 * .1 * .9) = 1.897, z = 2.64 -> hot."""
    r = luck.skater_luck(9, 40, 30, 300, 0.10)
    assert r["usual_pct"] == pytest.approx(0.10)
    assert r["expected"] == pytest.approx(4.0) and r["luck"] == pytest.approx(5.0)
    assert r["z"] == pytest.approx(5 / math.sqrt(3.6))
    assert r["status"] == "hot"
    # no history: all league rate. 0 on 50 shots at 10%: luck -5, z -2.36
    cold = luck.skater_luck(0, 50, 0, 0, 0.10)
    assert cold["luck"] == pytest.approx(-5.0) and cold["status"] == "cold"
    # z past the line but under 2 goals of luck: not flagged
    assert luck.skater_luck(2, 4, 0, 0, 0.10)["status"] is None
    assert luck.describe(r) == "shooting hot: 22.5% vs 10.0% usual"


def test_goalie_luck_hand_worked():
    """Usual .910 (prior 2730/3000 regressed to .900 with 3000 shots: 5430
    / 6000 = .905). 400 shots, 20 against: expected 38.0, luck +18 -> hot."""
    r = luck.goalie_luck(380, 400, 2730, 3000, 0.900)
    assert r["usual_pct"] == pytest.approx(0.905)
    assert r["expected"] == pytest.approx(38.0) and r["luck"] == pytest.approx(18.0)
    assert r["status"] == "hot"
    assert luck.describe(r) == "saving hot: .950 vs .905 usual"
