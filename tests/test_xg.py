"""Expected goals (src/inseason/xg.py). The play-by-play fixture follows the
api-web.nhle.com gamecenter play-by-play layout (plays[] with typeDescKey,
periodDescriptor, timeInPeriod, situationCode, homeTeamDefendingSide and
details{xCoord, yCoord, zoneCode, shotType, eventOwnerTeamId, ...})."""
from __future__ import annotations

import math
import sqlite3
from types import SimpleNamespace as NS

import numpy as np
import pytest

from src.inseason import xg

HOME, AWAY = 10, 20


def play(eid, kind, t, team, x, y, per=1, side="left", sit="1551", zone="O", **d):
    return {"eventId": eid, "typeDescKey": kind, "timeInPeriod": t, "situationCode": sit,
            "homeTeamDefendingSide": side, "periodDescriptor": {"number": per},
            "details": {"eventOwnerTeamId": team, "xCoord": x, "yCoord": y, "zoneCode": zone, **d}}


GAME = {"id": 2026020001, "homeTeam": {"id": HOME, "abbrev": "AAA"},
        "awayTeam": {"id": AWAY, "abbrev": "BBB"}, "plays": [
    # home defends left in period 1 -> home attacks right (+x): no flip
    play(1, "faceoff", "00:00", HOME, 0, 0, zone="N"),
    play(2, "shot-on-goal", "00:10", HOME, 79, 0, shotType="wrist", shootingPlayerId=1, goalieInNetId=9),
    # 2 s later, same team, unblocked before -> rebound; a goal
    play(3, "goal", "00:12", HOME, 85, 3, shotType="tip-in", scoringPlayerId=2, goalieInNetId=9),
    # away attacks left: flipped, (-80, 6) -> (80, -6); on the power play (5 away v 4 home)
    play(4, "missed-shot", "05:00", AWAY, -80, 6, sit="1541", shotType="slap", shootingPlayerId=5),
    # a blocked shot: kept, no distance
    play(5, "blocked-shot", "06:00", AWAY, -60, 0, shootingPlayerId=5),
    # takeaway in the neutral zone then a shot 3 s later -> rush
    play(6, "takeaway", "08:00", HOME, 0, 10, zone="N"),
    play(7, "shot-on-goal", "08:03", HOME, 60, 20, shotType="snap", shootingPlayerId=1),
    # period 2: home defends right -> home attacks left: flipped; away net empty
    play(8, "goal", "19:50", HOME, -70, 0, per=2, side="right", sit="0651", shotType="wrist",
         scoringPlayerId=1),
]}


def test_parse_game():
    rows = {r[1]: r for r in xg.parse_game(GAME)}
    assert set(rows) == {2, 3, 4, 5, 7, 8}
    # (gid, eid, period, sec, team, shooter, goalie, kind, type, x, y, dist, angle, rebound, rush,
    #  strength, empty_net, is_goal)
    r2 = rows[2]
    assert r2[9:13] == (79.0, 0.0, 10.0, 0.0) and r2[5] == 1 and r2[7] == "shot-on-goal"
    r3 = rows[3]
    assert r3[13] == 1 and r3[17] == 1 and r3[5] == 2 and r3[8] == "tip-in"
    assert r3[11] == pytest.approx(math.hypot(4, 3)) and r3[12] == pytest.approx(math.degrees(math.atan(3 / 4)))
    r4 = rows[4]
    assert (r4[9], r4[10]) == (80.0, -6.0) and r4[15] == "pp" and r4[13] == 0
    assert rows[5][11] is None and rows[5][7] == "blocked-shot"
    assert rows[7][14] == 1 and rows[2][14] == 0          # rush after the neutral-zone takeaway
    r8 = rows[8]
    assert (r8[9], r8[10]) == (70.0, 0.0) and r8[3] == 1200 + 1190 and r8[16] == 1   # empty net


def test_strength_codes():
    assert xg._strength("1551", True) == ("ev", False)
    assert xg._strength("1451", True) == ("pp", False)       # away has 4
    assert xg._strength("1451", False) == ("sh", False)
    assert xg._strength("0651", True) == ("sh", True)        # away pulled the goalie for a 6th skater
    assert xg._strength("bad", True) == ("ev", False)


def test_fit_recovers_known_coefficients():
    """Shots drawn from a known model; the fit lands near it."""
    rng = np.random.default_rng(3)
    n = 20000
    dist = rng.uniform(5, 60, n)
    ang = rng.uniform(0, 60, n)
    true = np.array([xg.DEFAULT[k] for k in xg.NAMES])
    true[1] = -0.07
    X = np.array([xg.features(d, a, 0, 0, "ev", "wrist") for d, a in zip(dist, ang)])
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-(X @ true)))).astype(float)
    b = xg.fit(X, y, np.array([xg.DEFAULT[k] for k in xg.NAMES]), l2=0.01)
    assert b[1] == pytest.approx(-0.07, abs=0.02)


def test_default_model_is_sensible():
    c = dict(xg.DEFAULT)
    slot = xg.prob(c, 12, 5, 0, 0, "ev", "wrist")
    point = xg.prob(c, 55, 20, 0, 0, "ev", "slap")
    assert 0.08 < slot < 0.25 and point < 0.03
    assert xg.prob(c, 12, 5, 1, 0, "ev", "wrist") > slot        # rebound
    assert xg.prob(c, 12, 5, 0, 0, "pp", "wrist") > slot


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE schedule (game_id INTEGER, season_id INTEGER, game_date TEXT,"
                 " game_type INTEGER, home_abbrev TEXT, away_abbrev TEXT)")
    conn.execute("INSERT INTO schedule VALUES (2026020001, 20262027, '2026-10-10', 2, 'AAA', 'BBB')")
    conn.execute("INSERT INTO schedule VALUES (2026020002, 20262027, '2026-12-10', 2, 'AAA', 'BBB')")
    return conn


def test_ingest_and_player_xg():
    """Only the played game is fetched; once stored it isn't again. Player 1:
    shots at 10 ft (on goal) and the rush snap (on goal); his empty-net goal
    is left out. Player 2: the rebound tip goal."""
    conn = _db()
    urls = []

    class F:
        def get_json(self, url):
            urls.append(url)
            return GAME

    out = xg.ingest_shots(conn, F(), 20262027, "2026-11-20")
    assert out["xg_games"] == 1 and not out["xg_fitted"] and len(urls) == 1
    assert xg.ingest_shots(conn, F(), 20262027, "2026-11-20")["xg_games"] == 0
    p = xg.player_xg(conn, 20262027)
    c = xg.coefs(conn)
    assert p[1]["sog"] == 2 and p[1]["goals"] == 0 and p[1]["attempts"] == 2
    assert p[1]["ixg"] == pytest.approx(xg.prob(c, 10, 0, 0, 0, "ev", "wrist")
                                        + xg.prob(c, math.hypot(29, 20), math.degrees(math.atan(20 / 29)),
                                                  0, 1, "ev", "snap"))
    assert p[2] == {"ixg": pytest.approx(xg.prob(c, 5, math.degrees(math.atan(3 / 4)), 1, 0, "ev", "tip-in")),
                    "goals": 1, "sog": 1, "attempts": 1}
    assert xg.player_xg(sqlite3.connect(":memory:"), 20262027) == {}


def test_apply_mixes_shot_quality_into_the_shooting_prior():
    """Preseason SH% 10%, this season 20 SOG with 3.0 xG (15% a shot):
    starting SH% (10% x 150 + 15% x 20)/170 = 10.588%. Season: 2 goals on
    20 shots in 10 games; k 375: (0.10588 x 375 + 2)/(375 + 20) = 0.105584;
    goals = 2.5 shots x that = 0.26396 a game."""
    ln = NS(is_goalie=False, gp=10, observed={"goals": 0.2, "shots": 2.0},
            blended={"shots": 2.5, "goals": 0.25}, extra={"sh_pct": {"prior": 0.10, "blended": 0.1}})
    xg.apply({1: ln}, {1: {"ixg": 3.0, "goals": 2, "sog": 20, "attempts": 30}},
             {"xg_k_shots": 150}, 375)
    prior = (0.10 * 150 + 0.15 * 20) / 170
    assert ln.extra["xg"]["prior_used"] == pytest.approx(prior)
    assert ln.blended["goals"] == pytest.approx(2.5 * (prior * 375 + 2) / 395)
    # no xg_k_shots: shown, projection untouched
    ln2 = NS(is_goalie=False, gp=10, observed={"goals": 0.2, "shots": 2.0},
             blended={"shots": 2.5, "goals": 0.25}, extra={"sh_pct": {"prior": 0.10}})
    xg.apply({1: ln2}, {1: {"ixg": 3.0, "goals": 2, "sog": 20, "attempts": 30}}, {}, 375)
    assert ln2.blended["goals"] == 0.25 and ln2.extra["xg"]["ixg"] == 3.0
