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
    Projection, curve_for, projected_play_share)
from src.projections.goalie_allocation import _rostered_goalies
from src.projections.goalies import _finalize_goalie, _project_goalie, _project_goalies
from src.projections.projections import project_all
from src.projections.skaters import _rookie_weight
from src.projections.team_context import project_teams, pythag, team_lookup
from src.projections.walkforward import evaluate, score, split_error
from tests.pinned import PINNED, pinned_config

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
                               conn, pinned_config())
    assert out["season_shares"] == pytest.approx([70 / 82, 41 / 82])
    assert out["raw_share"] == pytest.approx(0.7210365853658537)
    assert out["share"] == pytest.approx(0.764390243902439)


def test_play_share_floor_and_ceiling():
    conn = _conn()
    _schedule(conn, S2)
    cfg = pinned_config()
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
    p = {q.player_id: q for q in project_all(S3, db, {**PINNED, "rookie_ramp_gp": 0})}[1]
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
    p = {q.player_id: q for q in project_all(S3, db, {**PINNED, "rookie_ramp_gp": 0})}[1]
    assert p.terms["toi"]["team_changed"] is True
    assert p.terms["toi"]["pp_pg"] == pytest.approx(167.14285714285714)
    assert p.pp_points == pytest.approx(8.825204761904763)


def test_rookie_prior_ramps_smoothly():
    cfg = pinned_config()
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
    ctx = project_teams(conn, [S2], pinned_config(),
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
    p = _project_goalie(1, S3, conn, pinned_config(), {"save_pct": 0.9117})
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
    _finalize_goalie(p, {"sa_pg": 30.0, "win_pg": 0.55}, pinned_config())
    assert p.wins == pytest.approx(34.2)
    assert p.saves == pytest.approx(1656.0)
    assert p.goals_against == pytest.approx(144.0)
    assert p.shutouts == pytest.approx(5.041153949011782)


def test_project_goalies_uses_team_context_and_sums_starts():
    conn = _conn()
    _goalie_league(conn)
    _schedule(conn, S3, extra=False)
    projs = _project_goalies(S3, conn, pinned_config(), {"save_pct": 0.9117})
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


def _age_db(young_shots, old_shots):
    """Two forwards, same TOI: A born 2000-06-01 (23 -> 24 by Feb-1 age),
    B born 1990-06-01 (33 -> 34)."""
    conn = _conn()
    for pid, bd in ((1, "2000-06-01"), (2, "1990-06-01")):
        conn.execute("INSERT INTO players (player_id, full_name, position,"
                     " birth_date) VALUES (?, 'P', 'C', ?)", (pid, bd))
    for pid, shots in ((1, young_shots), (2, old_shots)):
        for sid, n in zip((S1, S2), shots):
            conn.execute(
                "INSERT INTO skater_seasons (player_id, season_id, team_abbrev,"
                " gp, goals, assists, shots, blocks, hits, pp_points, sh_points,"
                " pp_toi_sec, toi_sec) VALUES (?, ?, 'AAA', 80, 20, 30, ?, 30,"
                " 40, 10, 1, 14400, 72000)", (pid, sid, n))
    return conn


def test_age_fit_on_paired_seasons():
    """League shots flat (400 -> 400): A +10% into 24, B -10% into 34.
    Levels: 1 through 23, 1.1 from 24 to 33, .99 at 34; / 1.1 ->
    23: .909, 24: 1.0, 34: .9."""
    from src.projections.age_fit import fit
    shots = fit(_age_db((200, 220), (200, 180)), min_toi_min=300,
                min_pairs=1)["level_curves_by_stat"]["forwards"]["shots"]
    assert shots[23] == pytest.approx(0.909)
    assert shots[24] == 1.0
    assert shots[34] == pytest.approx(0.9)


def test_age_fit_ignores_league_wide_change():
    """Both players +10% because the whole league shot 10% more: that is
    the era, not aging, so every level stays 1.0."""
    from src.projections.age_fit import fit
    shots = fit(_age_db((200, 220), (200, 220)), min_toi_min=300,
                min_pairs=1)["level_curves_by_stat"]["forwards"]["shots"]
    assert set(shots.values()) == {1.0}


def _pulled_starter_db():
    """AAA, 82 games, 2460 SA (30 per game). G1 60 GS facing 1620 SA (27 per
    start: pulled sometimes); G2 22 GS facing 840 (38.18: relief work)."""
    conn = _conn()
    _schedule(conn, S2, extra=False)
    _schedule(conn, S3, extra=False)
    for pid, gs, sa in ((1, 60, 1620), (2, 22, 840)):
        conn.execute("INSERT INTO players (player_id, full_name, position,"
                     " current_team_abbrev) VALUES (?, ?, 'G', 'AAA')",
                     (pid, f"G{pid}"))
        conn.execute(
            "INSERT INTO goalie_seasons (player_id, season_id, team_abbrev, gp,"
            " games_started, wins, saves, shots_against, goals_against,"
            " shutouts) VALUES (?, ?, 'AAA', ?, ?, ?, ?, ?, ?, 0)",
            (pid, S2, gs, gs, gs // 2, int(sa * .91), sa, sa - int(sa * .91)))
    return conn


def test_goalie_shots_per_start_ratio_hand_computed():
    """G1: 27 / 30 = .9, regressed 1 - .1 * 60/(60+40) = .94.
    G2: (840/22) / 30 = 1.272727 -> 1 + .272727 * 22/62 = 1.096774.
    Per-start SA = team SA/g x ratio, so GA/start = 30 * .94 * (1 - SV%)."""
    conn = _pulled_starter_db()
    cfg = pinned_config()
    g1 = _project_goalie(1, S3, conn, cfg, {"save_pct": 0.91})
    g2 = _project_goalie(2, S3, conn, cfg, {"save_pct": 0.91})
    assert g1.terms["raw_rates"]["sa_ratio"] == pytest.approx(0.94)
    assert g2.terms["raw_rates"]["sa_ratio"] == pytest.approx(1.0967741935483872)
    sa = g1.terms["team_context"]["sa_pg"] * 0.94
    assert g1.terms["raw_rates"]["ga_per_start"] == pytest.approx(
        sa * (1 - g1.save_pct))


def test_goalie_starts_reserve_leaves_starts_for_callups():
    """Reserve .05: the pool splits 82 * .95 = 77.9 starts in proportion to
    raw starts 60^2/85 = 42.352941 and 22^2/47 = 10.297872."""
    conn = _pulled_starter_db()
    cfg = pinned_config(goalie_starts_reserve=0.05)
    projs = {p.player_id: p for p in _project_goalies(S3, conn, cfg,
                                                       {"save_pct": 0.91})}
    assert projs[1].games_started + projs[2].games_started == pytest.approx(77.9)
    assert projs[1].games_started == pytest.approx(
        77.9 * 42.35294117647059 / (42.35294117647059 + 10.297872340425531))


def test_goalie_per_start_pooled_hand_computed():
    """Projected: 60 GS, 1650 SV, 150 GA, 33 W, 4 SO -> SA 30, SV% .91667,
    W .55, SO .06667. Actual: 50 GS, 1300 SV, 150 GA, 25 W, 2 SO ->
    SA 29, SV% .89655, W .5, SO .04."""
    from src.projections.walkforward import goalie_per_start

    def g(gs, sv, ga, w, so):
        return Projection(player_id=1, season_id=S3, gp=gs, toi_per_gp=0,
                          goals=0, assists=0, shots=0, blocks=0, hits=0,
                          pp_points=0, wins=w, save_pct=None, position="G",
                          games_started=gs, saves=sv, goals_against=ga,
                          shutouts=so)
    out = goalie_per_start([{"p": g(60, 1650, 150, 33, 4),
                             "a": g(50, 1300, 150, 25, 2)}])
    assert out["sa"] == pytest.approx((30.0, 29.0))
    assert out["sv_pct"] == pytest.approx((1650 / 1800, 1300 / 1450))
    assert out["wins"] == pytest.approx((0.55, 0.5))
    assert out["so"] == pytest.approx((4 / 60, 0.04))


def test_goalie_sv_age_curve_is_additive():
    """G1 born 1996-06-01: 28 at Feb 1 2025 (observed 2024-25), 29 at Feb 1
    2026 (projected). Curve 28: -.0085, 29: -.0107 -> SV% moves by
    -.0107 - (-.0085) = -.0022 from the regressed value."""
    conn = _conn()
    _goalie_league(conn)
    conn.execute("UPDATE players SET birth_date = '1996-06-01' WHERE player_id = 1")
    cfg = pinned_config(goalie_sv_age={28: -0.0085, 29: -0.0107})
    p = _project_goalie(1, S3, conn, cfg, {"save_pct": 0.9117})
    assert p.terms["sv_age_delta"] == pytest.approx(-0.0022)
    assert p.save_pct == pytest.approx(
        p.terms["regressed_rate"]["save_pct"] - 0.0022)


def test_league_mean_is_recency_weighted():
    """League SA/g 33 two seasons ago, 30 last season (shots falling).
    Pooled mean would be 31.5; 5/3 weighting gives (5*30 + 3*33)/8 = 31.125,
    so a team at the league rate both years projects 31.125."""
    from src.projections.team_context import _league
    hist = {S1: {"AAA": {"games": 82, "wins": 41, "sa": 82 * 33, "ga": 0, "gf": 0}},
            S2: {"AAA": {"games": 82, "wins": 41, "sa": 82 * 30, "ga": 0, "gf": 0}}}
    assert _league(hist)["sa_pg"] == pytest.approx(31.125)


def test_play_share_flat_unless_long_record_of_missed_time():
    """82-game seasons. Healthy share .88 unless 4 of the last 5 qualifying
    seasons are under 75% of team games.
      A: 41, 82, 82 GP -> one low season: flat .88.
      B: 50, 55, 40, 60, 82 -> four low (.61 .67 .49 .73): chronic, history
         formula (5*50 + 3*55 + 2*40)/82 = 6.036585, + 3*.88 -> /13 = .667430
      C: 5 GP call-ups are skipped, so 5, 5, 5, 5, 82 is not chronic."""
    conn = _conn()
    ids = [S3 - 10001 * i for i in range(5)]
    for s in ids:
        _schedule(conn, s, extra=False)
    cfg = pinned_config(gp_chronic_seasons=4, gp_chronic_lookback=5,
                        gp_chronic_share=0.75, gp_healthy_share=0.88)

    def share(gps):
        return projected_play_share([(s, g, "AAA") for s, g in zip(ids, gps)],
                                    conn, cfg)
    a, b, c = share([41, 82, 82]), share([50, 55, 40, 60, 82]), share([5, 5, 5, 5, 82])
    assert (a["chronic"], a["share"]) == (False, 0.88)
    assert b["chronic"] and b["share"] == pytest.approx(0.6674296435272045)
    assert (c["chronic"], c["share"]) == (False, 0.88)
