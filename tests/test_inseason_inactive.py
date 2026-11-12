"""Off-roster players (is_active = 0: call-ups, waiver players) get their
own projection as the in-season prior.

Regression test for PR #18 fallout: once backfilled players had names, they
matched the free-agent list, but the projection skipped them, so their
prior was the league-average fallback with a 100% play share. One of them
became "the best free agent", replacement level jumped, and almost every
rostered player read DROP CANDIDATE.
"""
from __future__ import annotations

from src.ingest.models import init_db
from src.inseason.evaluate import build_context
from src.inseason.model import priors_from_projections
from src.projections.goalies import _project_goalies
from src.projections.projections import project_all
from tests.pinned import pinned_config
from tests.test_inseason import ICFG, POINTS

PREV, SEASON = 20252026, 20262027


def _db(path):
    conn = init_db(path)
    conn.execute("INSERT INTO players (player_id, full_name, position,"
                 " current_team_abbrev, is_active) VALUES"
                 " (1, 'Top Guy', 'C', 'AAA', 1),"
                 " (2, 'Waiver Guy', 'C', NULL, 0),"
                 " (6, 'Goalie A', 'G', 'AAA', 1),"
                 " (7, 'Callup Goalie', 'G', NULL, 0)")
    # last season: Top Guy 80 GP, 30 G on 240 SOG; Waiver Guy 60 GP, 2 G on 40
    for pid, gp, g, a, sog in ((1, 80, 30, 40, 240), (2, 60, 2, 3, 40)):
        conn.execute(
            "INSERT INTO skater_seasons (player_id, season_id, team_abbrev, gp,"
            " goals, assists, shots, blocks, hits, pp_points, sh_points,"
            " pp_toi_sec, toi_sec) VALUES (?, ?, 'AAA', ?, ?, ?, ?, 20, 30, 5, 0,"
            " ?, ?)", (pid, PREV, gp, g, a, sog, gp * 60, gp * 700))
    for pid, gs in ((6, 60), (7, 15)):
        conn.execute(
            "INSERT INTO goalie_seasons (player_id, season_id, team_abbrev, gp,"
            " games_started, wins, saves, shots_against, goals_against, shutouts)"
            " VALUES (?, ?, 'AAA', ?, ?, ?, ?, ?, ?, 1)",
            (pid, PREV, gs, gs, gs // 2, gs * 27, gs * 30, gs * 3))
    for i in range(82):
        conn.execute("INSERT INTO schedule VALUES (?, ?, '2026-01-01', 'AAA',"
                     " 'BBB', 2)", (PREV * 1000 + i, PREV))
    conn.execute("INSERT INTO schedule VALUES (1, ?, '2026-12-01', 'AAA', 'CCC', 2)",
                 (SEASON,))
    # this season: both skaters 10 GP, Waiver Guy now with CCC
    for pid, team in ((1, "AAA"), (2, "CCC")):
        conn.execute(
            "INSERT INTO skater_current (player_id, season_id, team_abbrev, gp,"
            " goals, assists, points, shots, blocks, hits, pp_points, pp_toi_sec,"
            " toi_sec, as_of) VALUES (?, ?, ?, 10, 1, 1, 2, 10, 2, 3, 0, 0, 7000,"
            " '2026-11-01')", (pid, SEASON, team))
    conn.commit()
    conn.close()


def test_off_roster_skater_gets_his_own_prior(tmp_path):
    db = str(tmp_path / "i.db")
    _db(db)
    ctx = build_context(db, POINTS, icfg=ICFG)
    waiver = ctx.lines[2]
    proj = next(p for p in project_all(SEASON, db, include_inactive=True)
                if p.player_id == 2)
    assert waiver.has_projection
    # the prior is HIS projection per game, not the league-average fallback
    # (this season: 2 G in 20 GP = .1) with a 100% play share
    assert waiver.prior["goals"] == proj.goals / proj.gp
    assert waiver.prior["goals"] != 0.1
    assert waiver.play_share_prior == min(proj.gp / 82.0, 1.0) < 1.0


def test_draft_board_projection_still_excludes_off_roster_players(tmp_path):
    db = str(tmp_path / "b.db")
    _db(db)
    default = {p.player_id for p in project_all(SEASON, db)}
    inseason = {p.player_id for p in project_all(SEASON, db, include_inactive=True)}
    assert 2 not in default and 7 not in default
    assert {1, 2, 6, 7} <= inseason


def test_zero_start_goalie_prior_uses_projected_per_start_rates(tmp_path):
    db = str(tmp_path / "g.db")
    _db(db)
    import sqlite3
    conn = sqlite3.connect(db)
    projs = _project_goalies(SEASON, conn, pinned_config(), {"save_pct": 0.9},
                             include_inactive=True)
    callup = next(p for p in projs if p.player_id == 7)
    assert callup.games_started == 0                 # no team -> no starts
    per = priors_from_projections([callup])[7]["per_start"]
    raw = callup.terms["raw_rates"]
    assert per == {"wins": raw["wins_pct"], "saves": raw["sv_per_start"],
                   "goals_against": raw["ga_per_start"],
                   "shutouts": raw["so_per_start"]}
    assert per["saves"] > 0
