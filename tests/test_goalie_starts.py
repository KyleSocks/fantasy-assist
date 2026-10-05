"""Tests for goalie start allocation in src/projections/projections.py.

A team plays a fixed number of regular-season games, shared among all of
its goalies, so a team's projected goalie starts must sum to exactly its
game count and no goalie may exceed max_goalie_starts (regression.yaml,
68). Real-DB tests use data/hockey.db; synthetic tests use an in-memory
SQLite DB. No network.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.projections.projections import (
    Projection,
    _load_config,
    _project_goalies,
    _team_games_for,
    project_all,
)
from src.api.routes import build_board

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "hockey.db"
SEASON_ID = 20262027

TOLERANCE = 0.01


@pytest.fixture(scope="module")
def cfg() -> dict:
    return _load_config()


@pytest.fixture(scope="module")
def projs() -> list[Projection]:
    """Real projections for the 2026-27 season from the DB."""
    return project_all(SEASON_ID, str(DB_PATH))


def _synthetic_conn(goalies, team: str = "AAA", total: int = 82) -> sqlite3.Connection:
    """In-memory DB with one team playing `total` games and the given goalies.

    `goalies` is a list of (player_id, seasons) where seasons is a list of
    (season_id, gp, games_started, wins, saves, shots_against,
    goals_against, shutouts, toi_sec) rows.
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE players (player_id INTEGER PRIMARY KEY, "
        "position TEXT, current_team_abbrev TEXT); "
        "CREATE TABLE goalie_seasons (player_id INTEGER, season_id INTEGER, "
        "team_abbrev TEXT, gp INTEGER, games_started INTEGER, wins INTEGER, "
        "saves INTEGER, shots_against INTEGER, goals_against INTEGER, "
        "shutouts INTEGER, toi_sec REAL); "
        "CREATE TABLE schedule (game_id INTEGER PRIMARY KEY, "
        "season_id INTEGER, game_date TEXT, home_abbrev TEXT, "
        "away_abbrev TEXT, game_type INTEGER);")
    for pid, seasons in goalies:
        conn.execute("INSERT INTO players VALUES (?, 'G', ?)", (pid, team))
        for (season, gp, gs, wins, saves, sa, ga, so, toi) in seasons:
            conn.execute(
                "INSERT INTO goalie_seasons VALUES "
                " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, season, team, gp, gs, wins, saves, sa, ga, so, toi))
    for i in range(total):
        conn.execute(
            "INSERT INTO schedule VALUES (?, ?, '2026-10-01', ?, ?, 2)",
            (i, SEASON_ID, team, "BBB"))
    return conn


def test_no_goalie_exceeds_max_starts(projs, cfg):
    """a. No goalie in the entire projection exceeds max_goalie_starts."""
    cap = float(cfg["max_goalie_starts"])
    over = [(p.player_id, p.terms["final"]["games_started"])
            for p in projs if p.position == "G"
            if p.terms["final"]["games_started"] > cap]
    assert over == []


def test_every_team_starts_sum_to_games(projs):
    """b. For every one of the 32 teams, projected goalie starts sum to
    that team's game count within 0.01."""
    conn = sqlite3.connect(str(DB_PATH))
    team_games = _team_games_for(SEASON_ID, conn)
    team_of = dict(conn.execute(
        "SELECT player_id, current_team_abbrev FROM players").fetchall())
    conn.close()
    assert len(team_games) == 32
    for team, total in sorted(team_games.items()):
        starts = [p.terms["final"]["games_started"] for p in projs
                  if p.position == "G"
                  and team_of.get(p.player_id) == team]
        assert abs(sum(starts) - total) <= TOLERANCE, (team, sum(starts), total)


def test_synthetic_starter_majority_backups_single_digits(cfg):
    """c. One 60-start goalie and two 1-start goalies: the starter gets
    the majority, the backups get single digits, and the three sum to
    the team's game count."""
    conn = _synthetic_conn([
        (101, [(20252026, 60, 60, 30, 1500, 1900, 150, 6, 18000.0)]),
        (102, [(20252026, 2, 1, 0, 30, 40, 3, 0, 900.0)]),
        (103, [(20252026, 2, 1, 0, 30, 40, 3, 0, 900.0)]),
    ])
    projs = _project_goalies(SEASON_ID, conn, cfg, {})
    starts = {p.player_id: p.terms["final"]["games_started"] for p in projs}
    starter = starts[101]
    backups = [starts[102], starts[103]]
    assert starter > sum(backups)
    assert starter <= float(cfg["max_goalie_starts"])
    assert all(b < 10 for b in backups)
    assert abs(sum(starts.values()) - 82) <= TOLERANCE
    conn.close()


def test_synthetic_zero_history_splits_evenly(cfg):
    """d. All goalies have zero start history: starts split evenly and no
    goalie receives the team's whole game count."""
    conn = _synthetic_conn([
        (201, [(20252026, 20, 0, 0, 0, 0, 0, 0, 0.0)]),
        (202, [(20252026, 20, 0, 0, 0, 0, 0, 0, 0.0)]),
        (203, [(20252026, 20, 0, 0, 0, 0, 0, 0, 0.0)]),
    ])
    projs = _project_goalies(SEASON_ID, conn, cfg, {})
    starts = [p.terms["final"]["games_started"] for p in projs]
    assert len(starts) == 3
    assert all(abs(s - 82 / 3) < 1e-6 for s in starts)
    assert all(s < 82 for s in starts)
    assert abs(sum(starts) - 82) <= TOLERANCE
    conn.close()


def test_synthetic_three_60_starters_capped_sum(cfg):
    """e. Three goalies who each started 60 last season: after capping at
    max_goalie_starts the sum still equals the team's game count and no
    goalie exceeds the cap."""
    conn = _synthetic_conn([
        (301, [(20252026, 60, 60, 30, 1500, 1900, 150, 6, 18000.0)]),
        (302, [(20252026, 60, 60, 30, 1500, 1900, 150, 6, 18000.0)]),
        (303, [(20252026, 60, 60, 30, 1500, 1900, 150, 6, 18000.0)]),
    ])
    projs = _project_goalies(SEASON_ID, conn, cfg, {})
    starts = [p.terms["final"]["games_started"] for p in projs]
    cap = float(cfg["max_goalie_starts"])
    assert all(s <= cap for s in starts)
    assert abs(sum(starts) - 82) <= TOLERANCE
    conn.close()


def test_named_regression_starts(projs):
    """f. Portillo, Garand and Sergeev each project under 15 starts;
    Gustavsson projects above 40."""
    by_id = {p.player_id: p for p in projs if p.position == "G"}
    portillo = by_id[8481707].terms["final"]["games_started"]
    garand = by_id[8482193].terms["final"]["games_started"]
    sergeev = by_id[8482949].terms["final"]["games_started"]
    gustavsson = by_id[8479406].terms["final"]["games_started"]
    assert portillo < 15
    assert garand < 15
    assert sergeev < 15
    assert gustavsson > 40


def test_no_sub30_start_goalie_in_top10_vorp():
    """g. No goalie appears in the overall top 10 by VORP with fewer than
    30 projected starts."""
    board = build_board("kyle_12_points")
    top10 = board["players"][:10]
    offenders = [
        (row["rank"], row["name"], row["stats"]["GS"])
        for row in top10 if row["pos"] == "G" and row["stats"]["GS"] < 30.0
    ]
    assert offenders == []
