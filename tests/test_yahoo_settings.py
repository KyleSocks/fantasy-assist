"""Tests for src/draft/yahoo_settings.py on pages trimmed from Yahoo
(tests/fixtures/yahoo_*_19725.html: Kyle's league; yahoo_settings_mock_cat:
a mock draft room)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.draft.yahoo_settings import (
    differences,
    league_teams,
    my_team_from,
    parse_settings,
    roster_slots,
)
from src.valuation.league import LeagueConfig, load_league

FIX = Path(__file__).parent / "fixtures"


def page(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_points_league_matches_the_hand_written_config():
    cfg, notes, name = parse_settings(page("yahoo_settings_19725.html"),
                                      name="kyle_12_points",
                                      season_start="2026-09-28",
                                      season_end_year=2027)
    assert name == "Marshall & Co." and notes == []
    assert cfg["scoring_type"] == "points" and cfg["team_count"] == 12
    # 2 C, 2 LW, 2 RW, 4 D, Util, 2 G, 5 BN; the two IR+ slots don't count
    assert cfg["roster_slots"] == {"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2,
                                   "UTIL": 1, "BENCH": 5}
    # Yahoo's SHO is this app's SO
    assert cfg["points_per_stat"]["SO"] == 3 and cfg["points_per_stat"]["GA"] == -1.5
    # weeks 24-26 ending Sunday Apr 4: Monday Mar 15 .. Apr 4
    assert cfg["playoff_window"] == ["2027-03-15", "2027-04-04"]
    assert cfg["regular_window"] == ["2026-09-28", "2027-03-14"]
    mine = load_league("config/leagues/kyle_12_points.yaml")
    assert differences(mine, cfg) == []


def test_differences_name_what_changed():
    cfg, _, _ = parse_settings(page("yahoo_settings_19725.html"), name="x",
                               season_start="2026-09-28", season_end_year=2027)
    mine = load_league("config/leagues/kyle_12_points.yaml")
    changed = LeagueConfig.model_validate(
        mine.model_dump() | {"points_per_stat": {**mine.points_per_stat, "SOG": 0.2},
                             "team_count": 10})
    assert differences(changed, cfg) == ["teams 10 vs Yahoo 12",
                                         "SOG 0.2 vs Yahoo 0.25"]


def test_mock_room_categories():
    cfg, notes, name = parse_settings(page("yahoo_settings_mock_cat.html"),
                                      name="yahoo_mock",
                                      season_start="2026-10-06",
                                      season_end_year=2027)
    assert name == "Into the Boards - H2H"
    assert cfg["scoring_type"] == "h2h_categories"
    assert cfg["categories"] == ["G", "A", "PPP", "SOG", "HIT", "W", "SV%", "SO"]
    assert cfg["roster_slots"]["BENCH"] == 4 and cfg["roster_slots"]["UTIL"] == 0
    # +/- and GAA aren't projected; "Playoffs: None" -> season-long
    assert notes == [("not modeled, left out: Plus/Minus (+/-), "
                      "Goals Against Average (GAA)"),
                     "no playoff weeks found: valued season-long only"]
    assert "playoff_window" not in cfg


def test_not_a_settings_page():
    with pytest.raises(ValueError):
        parse_settings(page("yahoo_draftresults_19725.html"), name="x",
                       season_start="2026-10-01", season_end_year=2027)


def test_roster_slots_ignore_injury_and_na():
    assert roster_slots("C, Util, BN, IR, IR+, NA")[0]["UTIL"] == 1
    assert roster_slots("C, F, BN")[1] == ["F"]


def test_league_page_teams_and_my_team():
    nav = page("yahoo_league_nav_19725.html")
    teams = league_teams(nav, 19725)
    assert len(teams) == 12
    assert teams[5] == "Robertson’s Rules of Hockey"
    assert teams[12] == "Staalin' for Aho"
    assert my_team_from(nav, 19725) == 5
    assert my_team_from(nav, 99) is None
