"""Advanced stats (src/inseason/advanced.py): ingest, merging a traded
player's rows, the league on-ice SH%, and the on-ice assist adjustment, all
worked out by hand."""
from __future__ import annotations

import sqlite3

import pytest

from src.inseason import advanced
from src.inseason.model import Line

CFG = {"onice_shots_per_60": 28, "onice_sh_pct_k_shots": {"F": 1000, "D": 2000}}


def line(pid=1, pos="C", assists=0.5):
    return Line(pid, "P", pos, "AAA", 20, {}, {}, {"goals": 0.3, "assists": assists,
                                                   "shots": 2.5, "pp_points": 0.2})


def test_onice_assists_hand_worked():
    """15 min a game at 5v5 over 20 games = 300 min -> 300 x 28/60 = 140
    on-ice shots. Regressed: (.08 x 1000 + .12 x 140) / 1140 = .0849123.
    Expected 5v5 assists 10 x .0849123/.12 = 7.07602; the season's total
    is 2.92398 too high, at weight 1/(40 + 20) = -.048733 a game."""
    a = {"gp": 20, "toi_5v5_pg": 900, "onice_sh_pct": 0.12, "assists_5v5": 10}
    o = advanced.onice_assists(line(), a, 0.08, CFG, 40)
    assert o["shots"] == pytest.approx(140)
    assert o["regressed"] == pytest.approx(96.8 / 1140)
    assert o["expected_5v5"] == pytest.approx(7.07602, abs=1e-5)
    assert o["delta_pg"] == pytest.approx(-0.048733, abs=1e-6)
    # a defenceman: k 2000 -> (160 + 16.8)/2140 = .0826168; 10 x .0826168/.12
    # = 6.88474 -> (6.88474 - 10)/60 = -.051921
    d = advanced.onice_assists(line(pos="D"), a, 0.08, CFG, 40)
    assert d["delta_pg"] == pytest.approx(-0.051921, abs=1e-6)
    # unlucky (on-ice 5%): assists go up
    cold = advanced.onice_assists(line(), {**a, "onice_sh_pct": 0.05}, 0.08, CFG, 40)
    assert cold["delta_pg"] > 0
    # nothing to scale
    assert advanced.onice_assists(line(), {**a, "assists_5v5": 0}, 0.08, CFG, 40) is None
    assert advanced.onice_assists(line(), {**a, "onice_sh_pct": 0}, 0.08, CFG, 40) is None


def test_league_onice_is_toi_weighted():
    """A: 20 games x 900 s at 12%; B: 10 x 600 s at 6%. Weights 18000 and
    6000: (.12 x 18000 + .06 x 6000)/24000 = .105."""
    adv = {1: {"gp": 20, "toi_5v5_pg": 900, "onice_sh_pct": 0.12},
           2: {"gp": 10, "toi_5v5_pg": 600, "onice_sh_pct": 0.06},
           3: {"gs": 5, "qs_pct": 0.6}}            # a goalie: ignored
    assert advanced.league_onice(adv) == pytest.approx(0.105)


def test_apply_moves_assists_and_attaches_stats():
    """League = the two skaters' weighted average (.105). Player 1 at 12%
    with 10 5v5 assists: regressed (.105 x 1000 + .12 x 140)/1140 =
    .1068421; expected 10 x .1068421/.12 = 8.90351; (8.90351 - 10)/60 =
    -.018275 off his .5 blended assists."""
    lines = {1: line(1), 2: line(2), 3: Line(3, "G", "G", "AAA", 5, {}, {}, {})}
    adv = {1: {"gp": 20, "toi_5v5_pg": 900, "onice_sh_pct": 0.12, "assists_5v5": 10},
           2: {"gp": 10, "toi_5v5_pg": 600, "onice_sh_pct": 0.06, "assists_5v5": 0},
           3: {"gs": 5, "qs_pct": 0.6}}
    advanced.apply(lines, adv, CFG, {"assists": 40})
    assert lines[1].blended["assists"] == pytest.approx(0.5 - 0.018275, abs=1e-6)
    assert lines[2].blended["assists"] == 0.5          # no 5v5 assists to scale
    assert lines[3].extra["adv"]["qs_pct"] == 0.6
    assert "onice" in lines[1].extra and "onice" not in lines[2].extra
    # turned off: stats attached, assists untouched
    off = {1: line(1)}
    advanced.apply(off, {1: adv[1]}, {}, {"assists": 40})
    assert off[1].blended["assists"] == 0.5 and off[1].extra["adv"]["assists_5v5"] == 10


class _Fetch:
    def get_paged(self, url, report, season):
        if "skater/percentages" in url:   # traded: two teams
            return [{"playerId": 1, "gamesPlayed": 10, "timeOnIcePerGame5v5": 900, "satPercentage": 0.55,
                     "satRelative": 0.02, "usatPercentage": 0.54, "skaterSavePct5v5": 0.91,
                     "skaterShootingPlusSavePct5v5": 1.02, "zoneStartPct5v5": 0.6, "teamAbbrevs": "AAA"},
                    {"playerId": 1, "gamesPlayed": 5, "timeOnIcePerGame5v5": 600, "satPercentage": 0.45,
                     "satRelative": -0.04, "usatPercentage": 0.46, "skaterSavePct5v5": 0.90,
                     "skaterShootingPlusSavePct5v5": 0.98, "zoneStartPct5v5": 0.4, "teamAbbrevs": "BBB"}]
        if "skater/scoringRates" in url:
            return [{"playerId": 1, "gamesPlayed": 15, "timeOnIcePerGame5v5": 800, "onIceShootingPct5v5": 0.1,
                     "goals5v5": 3, "assists5v5": 5, "primaryAssists5v5": 3, "pointsPer605v5": 2.0}]
        if "skater/puckPossessions" in url:
            return [{"playerId": 1, "gamesPlayed": 15, "timeOnIcePerGame5v5": 800,
                     "individualShotsForPer60": 8.5, "individualSatForPer60": 15.0}]
        if "goalie/advanced" in url:
            return [{"playerId": 9, "gamesStarted": 12, "qualityStart": 7, "qualityStartsPct": 0.583,
                     "shotsAgainstPer60": 29.5, "goalsForAverage": 3.1}]
        return []


def test_ingest_merges_traded_rows_and_loads():
    """Player 1's two percentage rows are weighted by 5v5 TOI x games:
    9000 and 3000. SAT% (.55 x 9000 + .45 x 3000)/12000 = .525; games add
    up to 15; 5v5 TOI per game (900 x 9000 + 600 x 3000)/12000 = 825."""
    conn = sqlite3.connect(":memory:")
    assert advanced.ingest_advanced(conn, _Fetch(), 20262027, "2026-11-20") == {"advanced_rows": 2}
    a = advanced.load_advanced(conn, 20262027)
    assert a[1]["sat_pct"] == pytest.approx(0.525)
    assert a[1]["gp"] == 15 and a[1]["toi_5v5_pg"] == pytest.approx(825)
    assert a[1]["onice_sh_pct"] == 0.1 and a[1]["assists_5v5"] == 5 and a[1]["ishots_per60"] == 8.5
    assert a[9] == {"gs": 12, "quality_starts": 7, "qs_pct": 0.583, "shots_against_per60": 29.5,
                    "goals_for_avg": 3.1}
    assert advanced.load_advanced(sqlite3.connect(":memory:"), 20262027) == {}
