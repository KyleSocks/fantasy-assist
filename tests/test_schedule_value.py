"""Tests for src/valuation/schedule_value.py — hand-computed expected values."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from src.projections.projections import Projection
from src.valuation.league import LeagueConfig
from src.valuation.schedule_value import (
    INGEST_COMMAND,
    assert_schedule_covers,
    window_games,
    windowed_projection,
    windowed_values,
)


def window_cfg(weight: float = 0.5) -> LeagueConfig:
    """A minimal category league with date windows.

    Regular window 2026-09-28..2026-10-11, playoff window
    2026-10-12..2026-10-25 (adjacent, as the config validator requires).
    """
    return LeagueConfig(
        name="win",
        team_count=1,
        scoring_type="h2h_categories",
        roster_slots={"C": 2, "LW": 0, "RW": 0, "D": 0, "G": 0, "UTIL": 0, "BENCH": 0},
        categories=["G"],
        regular_window=("2026-09-28", "2026-10-11"),
        playoff_window=("2026-10-12", "2026-10-25"),
        playoff_weight=weight,
    )


# ─── window_games requires a window config ─────────────────────────


def test_window_games_requires_window():
    """window_games raises before any DB work when no windows are set."""
    cfg = LeagueConfig(
        name="nowin",
        team_count=1,
        scoring_type="h2h_categories",
        roster_slots={"C": 2, "LW": 0, "RW": 0, "D": 0, "G": 0, "UTIL": 0, "BENCH": 0},
        categories=["G"],
    )
    with pytest.raises(ValueError) as excinfo:
        window_games(cfg, "regular", "unused.db")
    assert "no window config" in str(excinfo.value)


# ─── synthetic schedule DB ────────────────────────────────────────


@pytest.fixture
def schedule_db(tmp_path) -> Path:
    """Synthetic schedule around the 2026-09-28..2026-10-25 windows
    (see window_cfg).

    Games (season 20262027):
      1  2026-09-30  MTL @ EDM  type 2   regular window
      2  2026-10-01  EDM @ MTL  type 2   regular window
      3  2026-10-04  EDM @ MTL  type 2   regular window
      4  2026-10-05  EDM @ TOR  type 2   regular window
      5  2026-10-11  TOR @ EDM  type 2   regular window (last day)
      6  2026-10-12  EDM @ MTL  type 2   playoff window (first day)
      7  2026-10-18  MTL @ EDM  type 2   playoff window
      8  2026-10-19  EDM @ TOR  type 2   playoff window
      9  2026-10-25  TOR @ EDM  type 2   playoff window (last day)
      10 2026-09-30  EDM @ MTL  type 1   preseason -> excluded
      11 2026-10-26  EDM @ MTL  type 2   after window end (tail coverage)
      12 2026-09-27  EDM @ MTL  type 2   before window start -> excluded

    Expected window game counts (per team, per window):
      EDM: regular 5, playoff 4
      MTL: regular 3, playoff 2
      TOR: regular 2, playoff 2
    """
    db = tmp_path / "sched.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE schedule ("
        "game_id INTEGER PRIMARY KEY, season_id INTEGER NOT NULL, "
        "game_date TEXT NOT NULL, home_abbrev TEXT NOT NULL, "
        "away_abbrev TEXT NOT NULL, game_type INTEGER NOT NULL)"
    )
    rows = [
        (1, 20262027, "2026-09-30", "MTL", "EDM", 2),
        (2, 20262027, "2026-10-01", "EDM", "MTL", 2),
        (3, 20262027, "2026-10-04", "EDM", "MTL", 2),
        (4, 20262027, "2026-10-05", "EDM", "TOR", 2),
        (5, 20262027, "2026-10-11", "TOR", "EDM", 2),
        (6, 20262027, "2026-10-12", "EDM", "MTL", 2),
        (7, 20262027, "2026-10-18", "MTL", "EDM", 2),
        (8, 20262027, "2026-10-19", "EDM", "TOR", 2),
        (9, 20262027, "2026-10-25", "TOR", "EDM", 2),
        (10, 20262027, "2026-09-30", "EDM", "MTL", 1),
        (11, 20262027, "2026-10-26", "EDM", "MTL", 2),
        (12, 20262027, "2026-09-27", "EDM", "MTL", 2),
    ]
    conn.executemany("INSERT INTO schedule VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return db

# ─── window_games ─────────────────────────────────────────────────


def test_window_games_counts(schedule_db):
    cfg = window_cfg()
    assert window_games(cfg, "regular", schedule_db) == {
        "EDM": 5, "MTL": 3, "TOR": 2,
    }
    assert window_games(cfg, "playoff", schedule_db) == {
        "EDM": 4, "MTL": 2, "TOR": 2,
    }


def test_window_games_unknown_window_raises(schedule_db):
    with pytest.raises(ValueError) as excinfo:
        window_games(window_cfg(), "offseason", schedule_db)
    assert "offseason" in str(excinfo.value)


# ─── assert_schedule_covers ───────────────────────────────────────


def test_assert_schedule_covers_passes(schedule_db):
    assert_schedule_covers(window_cfg(), schedule_db)


def test_assert_schedule_covers_empty_playoff_window(schedule_db):
    """Dropping every playoff-window game -> ValueError naming the
    window and the ingest command."""
    conn = sqlite3.connect(schedule_db)
    conn.execute("DELETE FROM schedule WHERE game_id IN (6, 7, 8, 9)")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError) as excinfo:
        assert_schedule_covers(window_cfg(), schedule_db)
    msg = str(excinfo.value)
    assert "window 'playoff'" in msg
    assert "2026-10-12..2026-10-25" in msg
    assert INGEST_COMMAND in msg


def test_assert_schedule_covers_truncated_tail(schedule_db):
    """The schedule's last regular-season game is 2026-10-20, which ends
    before the window end (2026-10-25)."""
    conn = sqlite3.connect(schedule_db)
    conn.execute("DELETE FROM schedule WHERE game_id IN (8, 9, 11)")
    conn.execute(
        "INSERT INTO schedule VALUES (13, 20262027, '2026-10-20', 'EDM', 'TOR', 2)"
    )
    conn.commit()
    conn.close()
    with pytest.raises(ValueError) as excinfo:
        assert_schedule_covers(window_cfg(), schedule_db)
    msg = str(excinfo.value)
    assert "2026-10-20" in msg  # the schedule's last RS game
    assert "2026-10-25" in msg  # what the window needs
    assert INGEST_COMMAND in msg


def test_assert_schedule_covers_single_day_window_ok(schedule_db):
    """A window with games on only one day still passes coverage."""
    conn = sqlite3.connect(schedule_db)
    conn.execute("DELETE FROM schedule WHERE game_id IN (1, 2)")  # regular keeps game 3
    conn.commit()
    conn.close()
    assert_schedule_covers(window_cfg(), schedule_db)

# ─── windowed_projection (hand-computed) ──────────────────────────


def test_windowed_projection_skater():
    """Every count scales by window_count/82 (66/82); rates untouched.

    season: gp=82, goals=30, assists=45, shots=200, pp_points=12.
      gp        = 82*66/82 = 66
      goals     = 30*66/82 = 24.146341...
      assists   = 45*66/82 = 36.219512195...
      shots     = 200*66/82 = 160.975609756...
      pp_points = 12*66/82 = 9.658536585...
      toi_per_gp = 21 (rate, preserved); wins/save_pct stay None.
    """
    p = Projection(
        player_id=1, season_id=2025, gp=82.0, toi_per_gp=21.0,
        goals=30.0, assists=45.0, shots=200.0, blocks=15.0,
        hits=25.0, pp_points=12.0, wins=None, save_pct=None,
    )
    w = windowed_projection(p, 66)
    assert w.gp == pytest.approx(66.0, abs=1e-9)
    assert w.goals == pytest.approx(30 * 66 / 82, abs=1e-9)
    assert w.assists == pytest.approx(45 * 66 / 82, abs=1e-9)
    assert w.shots == pytest.approx(200 * 66 / 82, abs=1e-9)
    assert w.pp_points == pytest.approx(12 * 66 / 82, abs=1e-9)
    assert w.toi_per_gp == 21.0
    assert w.wins is None
    assert w.save_pct is None


def test_windowed_projection_zero_games():
    """gp=0 -> gp and every count 0; rates preserved."""
    p = Projection(
        player_id=2, season_id=2025, gp=0.0, toi_per_gp=21.0,
        goals=30.0, assists=45.0, shots=200.0, blocks=15.0,
        hits=25.0, pp_points=12.0, wins=None, save_pct=None,
    )
    w = windowed_projection(p, 66)
    assert w.gp == 0.0
    assert w.goals == 0.0
    assert w.assists == 0.0
    assert w.pp_points == 0.0
    assert w.toi_per_gp == 21.0  # rates preserved
    assert w.save_pct is None


def test_windowed_projection_goalie():
    """Goalie scale = window_gp/gp = (61*65/82)/65 = 61/82.

    season: gp=65, assists=2, wins=35, save_pct=0.915, shutouts=2,
    gs=65, ga=260, sv=2300.
      gp = 65*61/82, wins = 35*61/82, shutouts = 2*61/82,
      games_started = 65*61/82, goals_against = 260*61/82,
      saves = 2300*61/82; save_pct (0.915) and toi_per_gp (60) untouched.
    """
    g = Projection(
        player_id=3, season_id=2025, gp=65.0, toi_per_gp=60.0,
        goals=0.0, assists=2.0, shots=0.0, blocks=0.0,
        hits=0.0, pp_points=0.0, wins=35.0, save_pct=0.915,
        shutouts=2.0, games_started=65.0, goals_against=260.0, saves=2300.0,
    )
    w = windowed_projection(g, 61)
    assert w.gp == pytest.approx(65 * 61 / 82, abs=1e-9)
    assert w.assists == pytest.approx(2 * 61 / 82, abs=1e-9)
    assert w.wins == pytest.approx(35 * 61 / 82, abs=1e-9)
    assert w.shutouts == pytest.approx(2 * 61 / 82, abs=1e-9)
    assert w.games_started == pytest.approx(65 * 61 / 82, abs=1e-9)
    assert w.goals_against == pytest.approx(260 * 61 / 82, abs=1e-9)
    assert w.saves == pytest.approx(2300 * 61 / 82, abs=1e-9)
    assert w.save_pct == 0.915
    assert w.toi_per_gp == 60.0

# --- windowed_values (hand-computed) ---


@pytest.fixture
def window_db(tmp_path) -> Path:
    """Clean schedule giving per-team window games with clean means.

    Regular window 2026-09-28..2026-10-11, playoff window
    2026-10-12..2026-10-25. All 12 games are regular-season (game_type=2).
    Team A plays every game, B plays 8, C plays 4. Each window is exactly
    half the schedule:
      regular: A=6, B=4, C=2   sum 12, mean 4
      playoff: A=6, B=4, C=2   sum 12, mean 4
    """
    db = tmp_path / "window.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE schedule ("
        "game_id INTEGER PRIMARY KEY, season_id INTEGER NOT NULL, "
        "game_date TEXT NOT NULL, home_abbrev TEXT NOT NULL, "
        "away_abbrev TEXT NOT NULL, game_type INTEGER NOT NULL)"
    )
    rows = [
        (1, 20262027, "2026-09-30", "A", "B", 2),
        (2, 20262027, "2026-10-01", "A", "B", 2),
        (3, 20262027, "2026-10-02", "A", "B", 2),
        (4, 20262027, "2026-10-06", "A", "B", 2),
        (5, 20262027, "2026-10-07", "A", "C", 2),
        (6, 20262027, "2026-10-08", "A", "C", 2),
        (7, 20262027, "2026-10-13", "A", "B", 2),
        (8, 20262027, "2026-10-14", "A", "B", 2),
        (9, 20262027, "2026-10-15", "A", "B", 2),
        (10, 20262027, "2026-10-20", "A", "B", 2),
        (11, 20262027, "2026-10-21", "A", "C", 2),
        (12, 20262027, "2026-10-25", "A", "C", 2),
    ]
    conn.executemany("INSERT INTO schedule VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return db


def test_windowed_values_points_hand_computed(window_db, caplog):
    """Points league: raw_value == goals, and with an F pool of 5 but only
    3 players the replacement level is 0, so VORP == raw == windowed goals.

    team A is in the schedule (reg=6, po=6); players 2 and 3 fall back to the
    league-mean window games (reg=4, po=4) and are logged.
      p1 (team A):  gp 6/6, vorp 6/6, total (1-.5)*6 + .5*6 = 6
      p2 (no team): gp 4/4, vorp 4/4, total 4
      p3 (team X):  gp 4/4, vorp 4/4, total 4
    """
    cfg = LeagueConfig(
        name="wv",
        team_count=1,
        scoring_type="points",
        roster_slots={"C": 5, "LW": 0, "RW": 0, "D": 0, "G": 0, "UTIL": 0, "BENCH": 0},
        points_per_stat={"G": 1.0},
        regular_window=("2026-09-28", "2026-10-11"),
        playoff_window=("2026-10-12", "2026-10-25"),
        playoff_weight=0.5,
    )
    projs = [
        Projection(
            player_id=i, season_id=2025, gp=82.0, toi_per_gp=21.0,
            goals=82.0, assists=0.0, shots=0.0, blocks=0.0,
            hits=0.0, pp_points=0.0, wins=None, save_pct=None,
            position="C",
        )
        for i in (1, 2, 3)
    ]
    team_of = {1: "A", 3: "X"}
    with caplog.at_level(logging.WARNING, logger="src.valuation.schedule_value"):
        vals = windowed_values(projs, cfg, window_db, team_of=team_of)
    by_id = {v.player_id: v for v in vals}
    assert set(by_id) == {1, 2, 3}
    p1, p2, p3 = by_id[1], by_id[2], by_id[3]
    # team A is in the schedule: uses its own window games (reg=6, po=6)
    assert p1.gp_regular == pytest.approx(6.0, abs=1e-9)
    assert p1.gp_playoff == pytest.approx(6.0, abs=1e-9)
    assert p1.vorp_regular == pytest.approx(6.0, abs=1e-9)
    assert p1.vorp_playoff == pytest.approx(6.0, abs=1e-9)
    assert p1.total_value == pytest.approx(6.0, abs=1e-9)
    # players 2 and 3 fall back to the league-mean window games (reg=4, po=4)
    for p in (p2, p3):
        assert p.gp_regular == pytest.approx(4.0, abs=1e-9)
        assert p.gp_playoff == pytest.approx(4.0, abs=1e-9)
        assert p.vorp_regular == pytest.approx(4.0, abs=1e-9)
        assert p.vorp_playoff == pytest.approx(4.0, abs=1e-9)
        assert p.total_value == pytest.approx(4.0, abs=1e-9)
    # the two fallback players are logged, named by id
    assert "2 player(s)" in caplog.text
    assert "[2, 3]" in caplog.text

# --- league.py window validation ---


def _window_league(**overrides) -> LeagueConfig:
    """A valid windowed league config, with fields overridable per test."""
    base = dict(
        name="winval",
        team_count=1,
        scoring_type="h2h_categories",
        roster_slots={"C": 2, "LW": 0, "RW": 0, "D": 0, "G": 0, "UTIL": 0, "BENCH": 0},
        categories=["G"],
        regular_window=("2026-09-28", "2026-10-11"),
        playoff_window=("2026-10-12", "2026-10-25"),
        playoff_weight=0.5,
    )
    base.update(overrides)
    return LeagueConfig(**base)


def test_league_window_valid():
    """The base windowed config validates and keeps its window fields."""
    cfg = _window_league()
    assert cfg.playoff_weight == 0.5
    assert cfg.regular_window == ("2026-09-28", "2026-10-11")
    assert cfg.playoff_window == ("2026-10-12", "2026-10-25")


def test_league_window_weight_below_zero():
    with pytest.raises(ValueError) as excinfo:
        _window_league(playoff_weight=-0.1)
    assert "playoff_weight -0.1 must be in [0, 1]" in str(excinfo.value)


def test_league_window_weight_above_one():
    with pytest.raises(ValueError) as excinfo:
        _window_league(playoff_weight=1.1)
    assert "playoff_weight 1.1 must be in [0, 1]" in str(excinfo.value)


def test_league_window_start_after_end():
    """A regular window whose start is after its end is rejected."""
    with pytest.raises(ValueError) as excinfo:
        _window_league(regular_window=("2026-10-11", "2026-09-28"))
    msg = str(excinfo.value)
    assert "must satisfy start <= end" in msg


def test_league_window_non_contiguous():
    """Playoff window starts 2026-10-13, one day after the regular end."""
    with pytest.raises(ValueError) as excinfo:
        _window_league(playoff_window=("2026-10-13", "2026-10-25"))
    msg = str(excinfo.value)
    assert "must start the day after regular_window ends" in msg
    assert "expected start = 2026-10-12, got 2026-10-13" in msg


def test_league_window_incomplete():
    """regular_window set, but playoff_window missing."""
    with pytest.raises(ValueError) as excinfo:
        _window_league(playoff_window=None)
    assert "window config is incomplete" in str(excinfo.value)


# --- CLI smoke test ---


def test_cli_help_exits_zero():
    """main() --help prints usage and exits 0 without touching the DB."""
    from src.valuation.schedule_value import main

    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0


