"""Projection fixes from the Nov 2026 review, on tiny synthetic databases:

- goalie wins are a PER-START rate (wins / team games was multiplied by
  starts, counting the start share twice);
- career GP and durability ignore the projected season and later ones
  (no look-ahead);
- SHP survives windowed_projection (the draft board's path).
"""
import sqlite3
from pathlib import Path

import pytest

from src.projections.common import Projection
from tests.pinned import pinned_config
from src.projections.goalie_allocation import _apply_goalie_starts
from src.projections.goalies import _project_goalie
from src.projections.skaters import _project_skater
from src.valuation.schedule_value import windowed_projection

SCHEMA = (Path(__file__).resolve().parents[1] / "db" / "schema.sql").read_text()
S1, S2, S3 = 20232024, 20242025, 20252026


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    return conn


def _schedule(conn, season: int, team: str = "AAA", games: int = 82) -> None:
    base = season * 1000
    conn.executemany(
        "INSERT INTO schedule VALUES (?, ?, ?, ?, ?, 2)",
        [(base + i, season, f"{season // 10000}-11-{1 + i % 28:02d}", team, "BBB")
         for i in range(games)])


# ── goalie wins ────────────────────────────────────────────────────────

def _goalie(conn, pid: int, season: int, gs: int, wins: int) -> None:
    conn.execute(
        "INSERT INTO goalie_seasons (player_id, season_id, team_abbrev, gp,"
        " games_started, wins, saves, shots_against, goals_against, shutouts,"
        " toi_sec) VALUES (?, ?, 'AAA', ?, ?, ?, ?, ?, ?, 0, ?)",
        (pid, season, gs, gs, wins, gs * 27, gs * 29, gs * 2.8, gs * 3600))


def test_goalie_wins_are_per_start_not_per_team_game():
    """Wins stay a PER-START rate (wins / team games x starts counted the
    start share twice). League: AAA vs BBB, 82 games. AAA's goalie: 60 GS,
    33 W; nobody else won for AAA, BBB has no goalies, so team AAA's win
    rate is 33/82 = .402439 and the league's 33/164 = .201220.
      team win (history, regressed .6): .201220 + .6*.201220 = .321951
      no goals for -> Pythagorean side .5; blend .5/.5 -> .410976
      edge: 33/60 - .402439 = .147561, x 60/(60+40) = .088537
      wins per start = .499512 -> x 62 starts = 30.97 W
    """
    conn = _conn()
    _schedule(conn, S2)
    _goalie(conn, 1, S2, 60, 33)
    cfg = pinned_config()
    p = _project_goalie(1, S3, conn, cfg, {"save_pct": 0.905})
    assert p.terms["raw_rates"]["win_edge"] == pytest.approx(0.0885365853658536)
    assert p.terms["raw_rates"]["wins_pct"] == pytest.approx(0.4995121951219513)
    _apply_goalie_starts(p, 62)
    assert p.wins == pytest.approx(0.4995121951219513 * 62)


def test_backup_moving_into_starter_role_keeps_per_start_rate():
    """13 W in 25 GS: wins scale with the new start count, not with his old
    share of team games (the bug gave 7.9 W at 50 starts).
      team AAA 13/82 = .158537, league 13/164 = .079268
      team win .079268 + .6*.079268 = .126829 -> blend with .5 = .313415
      edge (13/25 - .158537) * 25/65 = .139024 -> .452439 per start
    """
    conn = _conn()
    _schedule(conn, S2)
    _goalie(conn, 2, S2, 25, 13)
    p = _project_goalie(2, S3, conn, pinned_config(), {"save_pct": 0.905})
    _apply_goalie_starts(p, 50)
    assert p.wins == pytest.approx(50 * (0.3134146341463415 + 0.1390243902439024))


# ── skater look-ahead ──────────────────────────────────────────────────

def _skater_db(future_gp: int | None) -> sqlite3.Connection:
    conn = _conn()
    conn.execute("INSERT INTO players (player_id, full_name, position, birth_date)"
                 " VALUES (7, 'Test Skater', 'C', '1998-01-01')")
    for s in (S1, S2, S3):
        _schedule(conn, s)
    conn.execute(
        "INSERT INTO skater_seasons (player_id, season_id, team_abbrev, gp, goals,"
        " assists, points, shots, blocks, hits, pp_points, sh_points,"
        " pp_toi_sec, toi_sec) VALUES (7, ?, 'AAA', 40, 10, 12, 22, 90, 20, 30,"
        " 6, 1, 6000, 40*900)", (S1,))
    if future_gp is not None:
        # seasons at / after the projected one must not matter
        for s in (S2, S3):
            conn.execute(
                "INSERT INTO skater_seasons (player_id, season_id, team_abbrev,"
                " gp, goals, assists, points, shots, blocks, hits, pp_points,"
                " sh_points, pp_toi_sec, toi_sec) VALUES"
                " (7, ?, 'AAA', ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?)",
                (s, future_gp, future_gp * 900))
    return conn


def test_projection_ignores_projected_and_later_seasons():
    cfg = pinned_config()
    means = {c: 0.1 for c in ("goals", "assists", "shots", "blocks", "hits",
                              "pp_points", "sh_points")}
    out = []
    for future in (None, 1, 82):
        p = _project_skater(7, S2, _skater_db(future), cfg, means, 900.0)
        out.append((p.terms["proj_gp"], p.goals, p.assists))
    assert out[1] == pytest.approx(out[0])
    assert out[2] == pytest.approx(out[0])


# ── SHP through the windowed (board) path ──────────────────────────────

def test_windowed_projection_keeps_sh_points():
    p = Projection(player_id=1, season_id=S3, gp=82.0, toi_per_gp=20.0,
                   goals=30, assists=40, shots=200, blocks=20, hits=30,
                   pp_points=20, wins=None, save_pct=None, sh_points=8.0)
    w = windowed_projection(p, 41)
    assert w.sh_points == pytest.approx(4.0)
