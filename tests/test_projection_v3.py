"""Projection engine v3, on small synthetic databases with hand-computed
expected values:

- regular-season-only ingest (gameTypeId=2) and SH TOI stored;
- games played: recency-weighted, call-ups skipped, playoffs/preseason
  never counted, regressed toward the prior share;
- skaters: per-60 x projected TOI, goals = shots x regressed shooting %,
  PP points on PP TOI, team-change PP blend, smooth rookie ramp;
- goalies: team context (shots against, Pythagorean + history win rate),
  win edge over own team, Poisson shutouts;
- backtest-aware goalie pool, walk-forward scoring, age-curve chaining.
"""
import sqlite3
from pathlib import Path

import pytest

from src.projections.age_fit import chain_levels
from src.projections.common import (
    Projection, _load_config, curve_for, projected_play_share)
from src.projections.goalie_allocation import _rostered_goalies
from src.projections.goalies import _finalize_goalie, _project_goalie, _project_goalies
from src.projections.projections import project_all
from src.projections.skaters import _rookie_weight
from src.projections.team_context import project_teams, pythag, team_lookup
from src.projections.walkforward import evaluate, score, split_error

SCHEMA = (Path(__file__).resolve().parents[1] / "db" / "schema.sql").read_text()
S1, S2, S3 = 20232024, 20242025, 20252026


def _conn(path=":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def _schedule(conn, season, teams=("AAA", "BBB"), games=82, extra=True):
    """``games`` regular-season games between two teams; with ``extra``
    also 10 preseason (type 1) and 8 playoff (type 3) games that must
    never count."""
    base = season * 1000
    rows = [(base + i, season, "2025-01-01", teams[0], teams[1], 2)
            for i in range(games)]
    if extra:
        rows += [(base + 500 + i, season, "2024-09-25", teams[0], teams[1], 1)
                 for i in range(10)]
        rows += [(base + 800 + i, season, "2025-04-25", teams[0], teams[1], 3)
                 for i in range(8)]
    conn.executemany("INSERT INTO schedule VALUES (?, ?, ?, ?, ?, ?)", rows)


# ── ingest: regular season only, SH TOI ────────────────────────────────

def test_ingest_requests_regular_season_only_and_stores_sh_toi():
    from src.ingest.ingest import _ingest_goalies, _ingest_skaters

    class F:
        urls = []

        def get_paged(self, base, report, season):
            self.urls.append(base)
            return {"skater_summary": [{"playerId": 7, "seasonId": season,
                                        "gamesPlayed": 10, "goals": 1,
                                        "teamAbbrevs": "AAA"}],
                    "skater_realtime": [],
                    "skater_toi": [{"playerId": 7, "evTimeOnIce": 9000,
                                    "ppTimeOnIce": 600, "shTimeOnIce": 450}],
                    "goalie_summary": []}[report]
    conn = _conn()
    f = F()
    _ingest_skaters(conn, f, S2)
    _ingest_goalies(conn, f, S2)
    assert len(f.urls) == 4
    assert all("gameTypeId=2 and seasonId=" in u for u in f.urls)
    assert conn.execute("SELECT toi_sec, pp_toi_sec, sh_toi_sec FROM"
                        " skater_seasons").fetchone() == (9000, 600, 450)


# ── games played ───────────────────────────────────────────────────────

def test_play_share_recency_weighted_skips_callups_ignores_playoffs():
    """S3 70 GP (w5), S2 5 GP (call-up, skipped), S1 41 GP (w3); 82
    regular-season games each (the 18 preseason/playoff games don't count).
      raw   = (5*70/82 + 3*41/82) / 8 = .721037
      share = (5*.853659 + 3*.5 + 3*.88) / (8 + 3) = .764390
    """
    conn = _conn()
    for s in (S1, S2, S3):
        _schedule(conn, s)
    out = projected_play_share([(S3, 70, "AAA"), (S2, 5, "AAA"), (S1, 41, "AAA")],
                               conn, _load_config())
    assert out["season_shares"] == pytest.approx([70 / 82, 41 / 82])
    assert out["raw_share"] == pytest.approx(0.7210365853658537)
    assert out["share"] == pytest.approx(0.764390243902439)


def test_play_share_floor_and_ceiling():
    conn = _conn()
    _schedule(conn, S2)
    cfg = _load_config()
    assert projected_play_share([(S2, 82, "AAA")], conn, cfg)["share"] == \
        pytest.approx((5 * 1.0 + 3 * 0.88) / 8)
    low = projected_play_share([(S2, 10, "AAA")], conn,
                               {**cfg, "gp_prior_weight": 0.0})
    assert low["share"] == pytest.approx(0.40)        # 10/82 clamps to floor


# ── skaters ────────────────────────────────────────────────────────────

def _skater_db(path, new_team="AAA"):
    """Two centres, 2024-25 only.
      P1: 80 GP, 20 G, 30 A, 200 SOG, 10 PPP; TOI 72000 EV / 14400 PP / 4800 SH
      P2: 60 GP, 6 G, 12 A, 100 SOG, 2 PPP; TOI 43200 / 3600 / 7200
    """
    conn = _conn(path)
    conn.execute("INSERT INTO players (player_id, full_name, position,"
                 " current_team_abbrev) VALUES (1, 'P1', 'C', ?)", (new_team,))
    conn.execute("INSERT INTO players (player_id, full_name, position,"
                 " current_team_abbrev) VALUES (2, 'P2', 'C', 'AAA')")
    rows = [(1, 80, 20, 30, 50, 200, 30, 40, 10, 0, 14400, 72000, 4800),
            (2, 60, 6, 12, 18, 100, 20, 60, 2, 2, 3600, 43200, 7200)]
    conn.executemany(
        "INSERT INTO skater_seasons (player_id, season_id, team_abbrev, gp,"
        " goals, assists, points, shots, blocks, hits, pp_points, sh_points,"
        " pp_toi_sec, toi_sec, sh_toi_sec) VALUES"
        " (?, 20242025, 'AAA', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    _schedule(conn, S2)
    conn.commit()
    conn.close()


def test_skater_per60_times_toi_hand_computed(tmp_path):
    """Rookie ramp off (rookie_ramp_gp=0) for clean numbers. P1, C pool:
      pool TOI 145200 s; SOG/60 = 3600*300/145200 = 7.438017; PPP/60 on PP
      TOI = 3600*12/18000 = 2.4; sh% = 26/300 = .086667
      shots: raw 3600*200/91200 = 7.894737, n 1520 min, k 150
             -> (7.894737*1520 + 7.438017*150)/1670 = 7.853714 /60
             x 1140 s TOI/GP / 3600 = 2.487010 per game
      GP: (5*80/82 + 3*.88)/8 = .939756 -> 77.06
      shots 2.487010 * 77.06 = 191.649
      sh%: (20 + .086667*200)/(200 + 200) = .093333 -> goals 17.8872
      PPP: raw 2.5, n 240, k 120 -> 2.466667 /60 x 180 s PP = .123333 pg
           -> 9.50407
      assists: raw 3600*30/91200, n 1520, k 300, pool 1.041322 -> 28.3228
    """
    db = str(tmp_path / "s.db")
    _skater_db(db)
    p = {q.player_id: q for q in project_all(S3, db, {"rookie_ramp_gp": 0})}[1]
    assert p.gp == pytest.approx(77.06)
    assert p.shots == pytest.approx(191.6489483842233)
    assert p.terms["sh_pct"]["regressed"] == pytest.approx(0.09333333333333334)
    assert p.goals == pytest.approx(17.887235182527508)
    assert p.pp_points == pytest.approx(9.504066666666667)
    assert p.assists == pytest.approx(28.322751793660885)
    assert p.toi_per_gp == pytest.approx(900.0)       # EV TOI/GP, in seconds
    t = p.terms
    for cat in ("goals", "assists", "shots", "pp_points"):
        assert (t["regressed_rate"][cat] * t["age_adj_by_stat"][cat]
                * t["proj_gp"]) == pytest.approx(t["final"][cat])


def test_team_change_blends_pp_toi_toward_position_mean(tmp_path):
    """P1 now on BBB: PP TOI/GP .75*180 + .25*(18000/140) = 167.142857 s
      -> PPP 2.466667/60 * 167.142857/3600 * 77.06 = 8.82520"""
    db = str(tmp_path / "t.db")
    _skater_db(db, new_team="BBB")
    p = {q.player_id: q for q in project_all(S3, db, {"rookie_ramp_gp": 0})}[1]
    assert p.terms["toi"]["team_changed"] is True
    assert p.terms["toi"]["pp_pg"] == pytest.approx(167.14285714285714)
    assert p.pp_points == pytest.approx(8.825204761904763)


def test_rookie_prior_ramps_smoothly():
    cfg = _load_config()
    assert [_rookie_weight(cfg, g) for g in (0, 100, 199, 200, 400)] == \
        pytest.approx([0.5, 0.25, 0.0025, 0.0, 0.0])


def test_curve_for_prefers_fitted_per_stat_curve():
    cfg = {"level_curves": {"forwards": {25: 1.0}},
           "level_curves_by_stat": {"forwards": {"hits": {"25": 0.8}}}}
    assert curve_for(cfg, "forwards", "hits") == {25: 0.8}
    assert curve_for(cfg, "forwards", "goals") == {25: 1.0}


# ── goalies and team context ───────────────────────────────────────────

def _goalie_league(conn):
    """2024-25, AAA vs BBB, 82 games. AAA: G1 60 GS/36 W/1800 SA/1656 SV,
    G2 22/9/660/594. BBB: G3 62/30/2046/1860, G4 20/7/660/600.
    Team W: AAA 45, BBB 37 (league .5/game). SA: AAA 2460, BBB 2706.
    Skater goals: AAA 260, BBB 220."""
    _schedule(conn, S2)
    for pid, team, gs, w, sa, sv, so in ((1, "AAA", 60, 36, 1800, 1656, 5),
                                         (2, "AAA", 22, 9, 660, 594, 1),
                                         (3, "BBB", 62, 30, 2046, 1860, 2),
                                         (4, "BBB", 20, 7, 660, 600, 0)):
        conn.execute("INSERT INTO players (player_id, full_name, position,"
                     " current_team_abbrev) VALUES (?, ?, 'G', ?)",
                     (pid, f"G{pid}", team))
        conn.execute(
            "INSERT INTO goalie_seasons (player_id, season_id, team_abbrev, gp,"
            " games_started, wins, saves, shots_against, goals_against,"
            " shutouts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, S2, team, gs, gs, w, sv, sa, sa - sv, so))
    for pid, team, g in ((101, "AAA", 260), (102, "BBB", 220)):
        conn.execute("INSERT INTO skater_seasons (player_id, season_id,"
                     " team_abbrev, gp, goals) VALUES (?, ?, ?, 82, ?)",
                     (pid, S2, team, g))


def test_project_teams_hand_computed():
    """League SA/g 5166/164 = 31.5; GF/g 480/164 = 2.926829; win/g .5.
    AAA: SA 31.5 + .6*(30 - 31.5) = 30.6
         GF from roster goals 250 of mean 225: 2.926829*250/225 = 3.252033
         GA 30.6 * (1 - .915) = 2.601
         Pythagorean 3.252033^2/(3.252033^2 + 2.601^2) = .609871
         history .5 + .6*(45/82 - .5) = .529268 -> win .5/.5 = .569569
    BBB: SA 32.4, Pythagorean .416712, history .470732 -> .443722
    """
    conn = _conn()
    _goalie_league(conn)
    ctx = project_teams(conn, [S2], _load_config(),
                        {"AAA": 250.0, "BBB": 200.0}, {"AAA": .915, "BBB": .905})
    a, b = ctx["AAA"], ctx["BBB"]
    assert a["sa_pg"] == pytest.approx(30.6)
    assert a["gf_pg"] == pytest.approx(3.2520325203252036)
    assert a["ga_pg"] == pytest.approx(2.601)
    assert a["win_roster"] == pytest.approx(0.6098706204582826)
    assert a["win_hist"] == pytest.approx(0.5292682926829269)
    assert a["win_pg"] == pytest.approx(0.5695694565706046)
    assert b["sa_pg"] == pytest.approx(32.4)
    assert b["win_pg"] == pytest.approx(0.4437217242570005)
    assert team_lookup(ctx, "ZZZ")["win_pg"] == pytest.approx(0.5)


def test_pythag_edges():
    assert pythag(3.0, 3.0, 2.0) == pytest.approx(0.5)
    assert pythag(0.0, 3.0, 2.0) == 0.5
    assert pythag(4.0, 2.0, 2.0) == pytest.approx(0.8)


def test_goalie_win_edge_over_own_team():
    """G1: 36/60 - 45/82 = .051220, regressed x 60/(60 + 40) = .030732."""
    conn = _conn()
    _goalie_league(conn)
    p = _project_goalie(1, S3, conn, _load_config(), {"save_pct": 0.9117})
    assert p.terms["raw_rates"]["win_edge"] == pytest.approx(0.030731707317073132)


def test_finalize_goalie_per_start_rates_hand_computed():
    """Team 30 SA/g, .55 win/g; goalie SV% .92, edge .02, own SO .08 per
    start over 100 starts:
      W/start .57; SV/start 27.6; GA/start 2.4
      SO: Poisson exp(-2.4) = .090718, (.08*100 + .090718*60)/160 = .084019
      at 60 starts: 34.2 W, 1656 SV, 144 GA, 5.04115 SO"""
    p = Projection(player_id=1, season_id=S3, gp=60, toi_per_gp=0, goals=0,
                   assists=0, shots=0, blocks=0, hits=0, pp_points=0, wins=0,
                   save_pct=0.92, position="G", games_started=60)
    p.terms = {"raw_rates": {"win_edge": 0.02, "own_so_per_start": 0.08},
               "n": {"starts": 100}, "final": {"games_started": 60.0}}
    _finalize_goalie(p, {"sa_pg": 30.0, "win_pg": 0.55}, _load_config())
    assert p.wins == pytest.approx(34.2)
    assert p.saves == pytest.approx(1656.0)
    assert p.goals_against == pytest.approx(144.0)
    assert p.shutouts == pytest.approx(5.041153949011782)


def test_project_goalies_uses_team_context_and_sums_starts():
    conn = _conn()
    _goalie_league(conn)
    _schedule(conn, S3, extra=False)
    projs = _project_goalies(S3, conn, _load_config(), {"save_pct": 0.9117})
    by_team: dict[str, float] = {}
    for p in projs:
        team = {1: "AAA", 2: "AAA", 3: "BBB", 4: "BBB"}[p.player_id]
        by_team[team] = by_team.get(team, 0.0) + p.games_started
        tc = p.terms["team_context"]
        assert p.terms["raw_rates"]["wins_pct"] == pytest.approx(
            tc["win_pg"] + p.terms["raw_rates"]["win_edge"])
        assert p.goals_against == pytest.approx(
            p.games_started * tc["sa_pg"] * (1 - p.save_pct))
    assert by_team == pytest.approx({"AAA": 82.0, "BBB": 82.0})


def test_goalie_pool_uses_projected_seasons_team_in_backtests():
    conn = _conn()
    conn.execute("INSERT INTO players (player_id, full_name, position,"
                 " current_team_abbrev) VALUES (9, 'G9', 'G', 'ZZZ')")
    conn.execute("INSERT INTO goalie_seasons (player_id, season_id, team_abbrev,"
                 " gp, games_started) VALUES (9, ?, 'AAA', 30, 30)", (S3,))
    assert _rostered_goalies(conn, S3) == [(9, "G9", "AAA")]
    assert _rostered_goalies(conn, S3 + 10001) == [(9, "G9", "ZZZ")]


# ── walk-forward scoring and age fitting ───────────────────────────────

def test_score_and_error_split_hand_computed():
    s = score([10, 20, 30], [12, 18, 33])
    assert s["mae"] == pytest.approx(7 / 3)
    assert s["rmse"] == pytest.approx((17 / 3) ** 0.5)
    assert s["bias"] == pytest.approx(-1.0)              # (-2 + 2 - 3) / 3
    rate, games = split_error(100, 80, 90, 75)
    assert (rate, games) == pytest.approx((3.75, 6.25))   # .05*75, 1.25*5


def test_walkforward_runs_end_to_end(tmp_path):
    db = str(tmp_path / "w.db")
    conn = _conn(db)
    for i, s in enumerate((S1, S2, S3)):
        _schedule(conn, s, extra=False)
        for pid, g in ((1, 20 + i), (2, 10)):
            conn.execute(
                "INSERT INTO skater_seasons (player_id, season_id, team_abbrev,"
                " gp, goals, assists, shots, blocks, hits, pp_points, sh_points,"
                " pp_toi_sec, toi_sec) VALUES (?, ?, 'AAA', 80, ?, 30, 200, 30,"
                " 40, 10, 1, 14400, 72000)", (pid, s, g))
    for pid in (1, 2):
        conn.execute("INSERT INTO players (player_id, full_name, position,"
                     " current_team_abbrev) VALUES (?, 'P', 'C', 'AAA')", (pid,))
    conn.commit()
    conn.close()
    from src.valuation.league import load_league
    res = evaluate(db, [S3], load_league(), None, 250, 64)
    assert res["skaters"]["n"] == 2
    # naive baseline = S2 actual totals; P2 repeats exactly -> 0 error for him
    assert res["skaters_naive"]["n"] == 2


def test_chain_levels_peak_normalized():
    """ratios 19: 1.1, 20: 1.0, 21: .9 -> levels 1, 1.1, 1.1, .99 / 1.1"""
    assert chain_levels({19: 1.1, 20: 1.0, 21: 0.9}, 18, 21) == {
        18: 0.909, 19: 1.0, 20: 1.0, 21: 0.9}


def test_age_fit_on_paired_seasons():
    """Forward born 2000-06-01 is 23 then 24 (Feb-1 ages), shots/60 up 10%:
    the forwards shots curve rises 1.1x into 24 and is peak-normalized."""
    from src.projections.age_fit import fit
    conn = _conn()
    conn.execute("INSERT INTO players (player_id, full_name, position,"
                 " birth_date) VALUES (1, 'P', 'C', '2000-06-01')")
    for sid, shots in ((S1, 200), (S2, 220)):
        conn.execute(
            "INSERT INTO skater_seasons (player_id, season_id, team_abbrev, gp,"
            " goals, assists, shots, blocks, hits, pp_points, sh_points,"
            " pp_toi_sec, toi_sec) VALUES (1, ?, 'AAA', 80, 20, 30, ?, 30, 40,"
            " 10, 1, 14400, 72000)", (sid, shots))
    curves = fit(conn, min_toi_min=300, min_pairs=1)
    shots = curves["level_curves_by_stat"]["forwards"]["shots"]
    assert shots[23] == pytest.approx(round(1 / 1.1, 3))
    assert shots[24] == 1.0
