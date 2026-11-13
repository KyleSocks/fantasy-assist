"""Engine refinements fitted on the real database (8-season walk-forward):
season length, era adjustment, the goalie start-share model, the pool-cap
look-ahead fix, season weights and the team-change ice-time factor.
Expected values are hand-computed in each docstring."""
import sqlite3
from pathlib import Path

import pytest

from src.projections.common import era_factors, league_levels, season_games
from src.projections.goalie_allocation import _recent_games_started
from src.projections.goalies import (
    _project_goalie, _project_goalies, start_share_model)
from src.projections.projections import project_all
from src.projections.team_context import project_teams
from tests.pinned import PINNED, pinned_config

SCHEMA = (Path(__file__).resolve().parents[1] / "db" / "schema.sql").read_text()
S1, S2, S3 = 20242025, 20252026, 20262027
MODEL = {"base": 0.176, "last": 0.585, "prev": 0.05, "age_over_30": -0.013,
         "sv": 8.0, "no_history": -0.083}


def _conn(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def _schedule(conn, season, games=82, teams=("AAA", "BBB")):
    conn.executemany("INSERT INTO schedule VALUES (?, ?, '2026-01-01', ?, ?, 2)",
                     [(season * 1000 + i, season, teams[0], teams[1])
                      for i in range(games)])


# ── season length ──────────────────────────────────────────────────────

def test_season_games_from_schedule():
    conn = _conn()
    _schedule(conn, S3, games=84)
    _schedule(conn, S2, games=12)            # partly loaded -> 82
    assert season_games(conn, S3) == 84
    assert season_games(conn, S2) == 82
    assert season_games(conn, S1) == 82      # no schedule at all


def _two_centres(path, s1_shots=200, team3="AAA"):
    """P1 and P2, centres, 80 GP both seasons, TOI 72000 EV + 14400 PP +
    4800 SH = 91200 s (P1) and 54000 EV only (P2)."""
    conn = _conn(path)
    for pid in (1, 2):
        conn.execute("INSERT INTO players (player_id, full_name, position,"
                     " current_team_abbrev) VALUES (?, 'P', 'C', ?)",
                     (pid, team3 if pid == 1 else "AAA"))
    for sid, shots in ((S1, s1_shots), (S2, 150)):
        _schedule(conn, sid)
        for pid, ev, pp, sh in ((1, 72000, 14400, 4800), (2, 54000, 0, 0)):
            conn.execute(
                "INSERT INTO skater_seasons (player_id, season_id, team_abbrev,"
                " gp, goals, assists, shots, blocks, hits, pp_points, sh_points,"
                " pp_toi_sec, toi_sec, sh_toi_sec) VALUES (?, ?, 'AAA', 80, 20,"
                " 30, ?, 30, 40, 10, 0, ?, ?, ?)", (pid, sid, shots, pp, ev, sh))
    conn.commit()
    return conn


def test_projected_games_use_the_84_game_schedule(tmp_path):
    """Share (5*80/82 + 3*80/82 + 3*.88)/11 = .949534; x 84 = 79.76 GP
    (82 games would give 77.86)."""
    db = str(tmp_path / "s.db")
    conn = _two_centres(db)
    _schedule(conn, S3, games=84)
    conn.commit()
    p = {q.player_id: q for q in project_all(S3, db, PINNED)}[1]
    assert p.terms["season_games"] == 84
    assert p.gp == pytest.approx(84 * (8 * 80 / 82 + 3 * 0.88) / 11)


# ── era adjustment ─────────────────────────────────────────────────────

def test_era_factors_restate_at_latest_league_level(tmp_path):
    """League shots: 400 in S1, 300 in S2, same ice time -> S1 shots count
    for 300/400 = .75; the latest season is always 1. Off -> {}."""
    conn = _two_centres(str(tmp_path / "e.db"))
    f = era_factors(conn, S3, {"era_adjust": True})
    assert f[S1]["shots"] == pytest.approx(0.75)
    assert f[S2]["shots"] == pytest.approx(1.0)
    assert f[S1]["assists"] == pytest.approx(1.0)
    # shooting %: 40/400 = .10 in S1, 40/300 = .1333 in S2 -> x 1.3333
    assert f[S1]["sh_pct"] == pytest.approx(4 / 3)
    assert era_factors(conn, S3, {"era_adjust": False}) == {}
    assert league_levels(conn, S3)[S2]["sh_pct"] == pytest.approx(40 / 300)


def test_skater_shot_rate_is_era_adjusted(tmp_path):
    """P1 shots/60: S2 3600*150/91200 = 5.921053; S1 3600*200/91200 x .75 =
    the same, so the 5/3 weighted rate is 5.921053 (6.661184 unadjusted)."""
    db = str(tmp_path / "e.db")
    _two_centres(db)
    on = {q.player_id: q for q in project_all(S3, db, {**PINNED, "era_adjust": True})}[1]
    off = {q.player_id: q for q in project_all(S3, db, PINNED)}[1]
    assert on.terms["per60"]["raw"]["shots"] == pytest.approx(5.921052631578948)
    assert off.terms["per60"]["raw"]["shots"] == pytest.approx(6.661184210526316)


def _two_team_goalies(conn):
    """League SA/game 32 in S1 (AAA 30, BBB 34) and 28 in S2 (AAA 27, BBB 29)."""
    for sid, sa in ((S1, {"AAA": 30, "BBB": 34}), (S2, {"AAA": 27, "BBB": 29})):
        _schedule(conn, sid)
        for pid, team in ((1, "AAA"), (2, "BBB")):
            conn.execute(
                "INSERT INTO goalie_seasons (player_id, season_id, team_abbrev,"
                " gp, games_started, wins, saves, shots_against, goals_against,"
                " shutouts) VALUES (?, ?, ?, 82, 82, 41, ?, ?, ?, 0)",
                (pid, sid, team, int(82 * sa[team] * .9), 82 * sa[team],
                 82 * sa[team] - int(82 * sa[team] * .9)))


def test_team_shots_against_era_adjusted():
    """Latest league 28; AAA's deviation (5*(27-28) + 3*(30-32))/8 = -1.375,
    kept .6 -> 28 - .825 = 27.175. Without era: league (5*28 + 3*32)/8 =
    29.5, team 28.125 -> 28.675."""
    conn = _conn()
    _two_team_goalies(conn)
    cfg = pinned_config()
    assert project_teams(conn, [S1, S2], {**cfg, "era_adjust": True})["AAA"][
        "sa_pg"] == pytest.approx(27.175)
    assert project_teams(conn, [S1, S2], cfg)["AAA"]["sa_pg"] == pytest.approx(28.675)


def test_goalie_save_pct_era_adjusted():
    """League SV% .910 (S1) -> .900 (S2). Goalie: .920 on 2000 SA in S1,
    .905 on 2000 in S2. S1 counts as .920 - .010; weights 5/8, 3/8 ->
    .906875; regressed to the latest league mark with k 1000:
    (.906875*4000 + .900*1000)/5000 = .9055."""
    conn = _conn()
    for sid, lg_sv, own in ((S1, .910, .920), (S2, .900, .905)):
        _schedule(conn, sid)
        conn.execute(
            "INSERT INTO goalie_seasons (player_id, season_id, team_abbrev, gp,"
            " games_started, wins, saves, shots_against, goals_against, shutouts)"
            " VALUES (1, ?, 'AAA', 50, 50, 25, ?, 2000, ?, 0)",
            (sid, round(2000 * own), 2000 - round(2000 * own)))
        # the rest of the league, so the league mark comes out at lg_sv
        rest_sv = round(lg_sv * 12000) - round(2000 * own)
        conn.execute(
            "INSERT INTO goalie_seasons (player_id, season_id, team_abbrev, gp,"
            " games_started, wins, saves, shots_against, goals_against, shutouts)"
            " VALUES (2, ?, 'BBB', 82, 82, 41, ?, 10000, ?, 0)",
            (sid, rest_sv, 10000 - rest_sv))
    p = _project_goalie(1, S3, conn, pinned_config(era_adjust=True),
                        {"save_pct": 0.95})
    assert p.terms["pos_mean"]["save_pct"] == pytest.approx(0.900)
    assert p.terms["raw_rates"]["save_pct"] == pytest.approx(0.906875)
    assert p.save_pct == pytest.approx(0.9055)


# ── goalie starts ──────────────────────────────────────────────────────

def test_start_share_model_hand_computed():
    """60 of 82 last season, 50 of 82 before, age 33, SV% .004 over league:
    .176 + .585*.731707 + .05*.609756 - .013*3 + 8*.004 = .627537.
    No history: .176 - .083 = .093. Capped at .85."""
    assert start_share_model(MODEL, 60 / 82, 50 / 82, 33, 0.004, True) == \
        pytest.approx(0.6275365853658535)
    assert start_share_model(MODEL, 0, 0, None, 0, False) == pytest.approx(0.093)
    assert start_share_model(MODEL, 1.0, 1.0, 25, 0.03, True) == 0.85


def _starter_backup_rookie(conn):
    """AAA: G1 60 GS, G2 22 GS last season (82-game schedule), G3 no NHL
    games. All .900 on 30 shots a start."""
    _schedule(conn, S2)
    _schedule(conn, S3)
    for pid, gs in ((1, 60), (2, 22), (3, 0)):
        conn.execute("INSERT INTO players (player_id, full_name, position,"
                     " current_team_abbrev) VALUES (?, ?, 'G', 'AAA')",
                     (pid, f"G{pid}"))
        if gs:
            conn.execute(
                "INSERT INTO goalie_seasons (player_id, season_id, team_abbrev,"
                " gp, games_started, wins, saves, shots_against, goals_against,"
                " shutouts) VALUES (?, ?, 'AAA', ?, ?, ?, ?, ?, ?, 0)",
                (pid, S2, gs, gs, gs // 2, gs * 27, gs * 30, gs * 3))


def test_start_model_feeds_allocation_and_rookies_get_starts():
    """No birth dates, SV% edge 0 (everyone at the pool mean .900):
      G1 .176 + .585*60/82 = .604049; G2 .176 + .585*22/82 = .332951;
      G3 (no history) .093. Sum 1.03 -> scaled to 82 starts:
      48.0894, 26.5068, 7.4039. The rookie's starts carry per-start rates."""
    conn = _conn()
    _starter_backup_rookie(conn)
    cfg = pinned_config(goalie_start_model=MODEL)
    by = {p.player_id: p for p in _project_goalies(S3, conn, cfg, {"save_pct": 0.9})}
    raw = [0.176 + 0.585 * 60 / 82, 0.176 + 0.585 * 22 / 82, 0.093]
    for pid, r in zip((1, 2, 3), raw):
        assert by[pid].games_started == pytest.approx(82 * r / sum(raw))
    assert by[1].games_started == pytest.approx(48.08939, abs=1e-4)
    assert by[3].saves > 0 and by[3].goals_against > 0


def test_pool_cap_ignores_the_projected_and_later_seasons():
    """Backtest of S2: a goalie's S2 and S3 starts must not decide who is
    kept in the pool (that was look-ahead)."""
    conn = _conn()
    for sid, gs in ((S1, 10), (S2, 55), (S3, 60)):
        conn.execute("INSERT INTO goalie_seasons (player_id, season_id,"
                     " team_abbrev, gp, games_started) VALUES (1, ?, 'AAA', ?, ?)",
                     (sid, gs, gs))
    assert _recent_games_started(conn, 1, S2) == 10
    assert _recent_games_started(conn, 1) == 60          # production: latest
    assert _recent_games_started(conn, 1, S1) == 0


# ── skater weights and team change ─────────────────────────────────────

def test_skater_season_weights_override(tmp_path):
    """Weights [5, 2, 1] over two 80-GP seasons -> 5/7 and 2/7."""
    db = str(tmp_path / "w.db")
    _two_centres(db)
    p = {q.player_id: q for q in project_all(
        S3, db, {**PINNED, "skater_season_weights": [5, 2, 1]})}[1]
    assert p.terms["season_weights"] == pytest.approx([5 / 7, 2 / 7])
    # shots/60: (5*5.921053 + 2*7.894737)/7 = 6.484962
    assert p.terms["per60"]["raw"]["shots"] == pytest.approx(6.484962406015038)


def test_team_change_scales_ice_time(tmp_path):
    """P1 moved to BBB. EV 900 s and SH 60 s x .94; PP 180 s blended .25
    toward the positional 90 s first: (135 + 22.5) x .94 = 148.05."""
    db = str(tmp_path / "t.db")
    _two_centres(db, team3="BBB")
    p = {q.player_id: q for q in project_all(
        S3, db, {**PINNED, "team_change_toi_factor": 0.94})}[1]
    t = p.terms["toi"]
    assert t["team_changed"] is True
    assert t["ev_pg"] == pytest.approx(900 * 0.94)
    assert t["sh_pg"] == pytest.approx(60 * 0.94)
    assert t["pp_pg"] == pytest.approx((0.75 * 180 + 0.25 * 90) * 0.94)
