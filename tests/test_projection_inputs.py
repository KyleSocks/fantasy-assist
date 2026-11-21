"""New inputs to the in-season projection, each worked out by hand:
goals from shooting %, deployment (ice time) factors, goalie starts on
back-to-backs."""
import pytest

from src.ingest.models import init_db
from src.inseason import deploy
from src.inseason.evaluate import goalie_share
from src.inseason.model import Line, build_lines, load_inseason_config


def _db(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    conn.execute("INSERT INTO players (player_id, full_name, position, current_team_abbrev)"
                 " VALUES (1, 'Sniper', 'C', 'AAA'), (2, 'Mate', 'C', 'AAA')")
    # 20 games, 50 shots, 10 goals (20% shooting) for Sniper
    conn.execute("INSERT INTO skater_current (player_id, season_id, team_abbrev, gp, goals,"
                 " assists, points, shots, blocks, hits, pp_points, sh_points, pp_toi_sec,"
                 " toi_sec, as_of) VALUES (1, 20262027, 'AAA', 20, 10, 5, 15, 50, 4, 6, 3, 0,"
                 " 2400, 18000, '2026-11-16'), (2, 20262027, 'AAA', 20, 2, 2, 4, 40, 4, 6, 0,"
                 " 0, 0, 16000, '2026-11-16')")
    return conn


PRIOR = {1: {"pg": {"goals": 0.3, "assists": 0.3, "shots": 3.0, "blocks": .2, "hits": .3,
                    "pp_points": .15, "sh_points": 0.0}, "toi_pg": 1000, "gp_share": 1.0}}


def test_goals_are_shots_times_blended_shooting_pct(tmp_path):
    """Shots: (3.0 x 15 + 50) / (15 + 20) = 2.7143 a game. Shooting %:
    projected .3 / 3.0 = 10%; (0.10 x 375 + 10) / (375 + 50) = 11.18%.
    Goals = 2.7143 x .11176 = .3034 a game -- blending goals directly
    ((.3 x 40 + 10) / 60) would give .3667, mostly from a 20% shooting run."""
    cfg = load_inseason_config()
    ln = build_lines(_db(tmp_path), 20262027, PRIOR, cfg)[1]
    assert ln.blended["shots"] == pytest.approx(95 / 35)
    assert ln.blended["goals"] == pytest.approx(95 / 35 * 47.5 / 425)
    cfg2 = {k: v for k, v in cfg.items() if k != "skater_sh_pct_k_shots"}
    assert build_lines(_db(tmp_path / "x"), 20262027, PRIOR, cfg2)[1].blended["goals"] == \
        pytest.approx((0.3 * 40 + 10) / 60)


def test_unprojected_skater_uses_league_shooting_pct(tmp_path):
    """Mate has no projection: league rates this season are the prior
    (12 goals on 90 shots = 13.33%; 2.25 shots a game). Shooting %:
    (.1333 x 375 + 2) / 415 = 12.53%; shots (2.25 x 15 + 40) / 35."""
    ln = build_lines(_db(tmp_path), 20262027, {}, load_inseason_config())[2]
    shots = (2.25 * 15 + 40) / 35
    assert ln.blended["goals"] == pytest.approx(shots * (12 / 90 * 375 + 2) / 415)


CFG = {"k_games": 6, "last_game_weight": 2, "min_factor": 0.6, "max_factor": 1.6}


def test_deployment_factors_hand_worked():
    """Season: 60 s PP, 900 s EV a game. Last 7 days (3 games): 150 s PP,
    1000 s EV; last game (shifts) 180 s PP.
    PP expected = (60 x 6 + 150 x 3 + 180 x 2) / 11 = 1170 / 11 = 106.36 s -> x1.773, clamped 1.6.
    EV expected = (900 x 6 + 1000 x 3) / 9 = 933.3 s -> x1.0370.
    Total: (106.36 + 933.3) / 960 = x1.0830."""
    role = {"recent_gp": 3, "recent_pp_toi": 150, "recent_ev_toi": 1000,
            "pp_sec": 180, "games": 5}
    f = deploy.factors(60, 900, role, CFG)
    assert f["exp_pp"] == pytest.approx(1170 / 11) and f["pp"] == 1.6
    assert f["ev"] == pytest.approx(8400 / 9 / 900)
    assert f["toi"] == pytest.approx((1170 / 11 + 8400 / 9) / 960)
    assert deploy.factors(60, 900, None, CFG) is None
    # no PP time at all this season: PP factor stays 1
    assert deploy.factors(0, 900, role, CFG)["pp"] == 1.0


def test_deployment_adjusts_rates():
    """Goals .4, assists .6, PPP .25 -> PP share .25. Factors pp 1.6, ev
    1.0, toi 1.1: goals .4 x (.25 x 1.6 + .75) = .46, assists .69, PPP .4,
    shots 3 x 1.1 = 3.3; hits / blocks unchanged."""
    ln = Line(1, "X", "C", "AAA", 20, {}, {}, {"goals": .4, "assists": .6, "pp_points": .25,
                                              "shots": 3.0, "hits": 1.0, "blocks": .5,
                                              "sh_points": 0.0})
    d = deploy.adjust(ln, {"pp": 1.6, "ev": 1.0, "toi": 1.1})
    assert ln.blended["goals"] == pytest.approx(.46) and ln.blended["assists"] == pytest.approx(.69)
    assert ln.blended["pp_points"] == pytest.approx(.4) and ln.blended["shots"] == pytest.approx(3.3)
    assert ln.blended["hits"] == 1.0 and d["goals"] == pytest.approx(.06)


class _Ctx:
    def __init__(self, lines, games):
        self.lines, self._games, self.icfg = lines, games, {"goalie_b2b_starter_factor": 0.5}

    def team_games(self, team, start, end):
        return [(d, "BBB") for d in self._games if start <= d <= end]


def _g(pid, share):
    return Line(pid, f"G{pid}", "G", "AAA", 10, {}, {}, {}, start_share=share)


def test_goalie_share_on_back_to_backs():
    """Starter .65, backup .30, third .05. Second night of a back-to-back:
    starter .325; the freed .325 goes .30/.35 and .05/.35 of it to the
    others: backup .30 + .2786 = .5786, third .05 + .0464 = .0964 (sum 1)."""
    lines = {1: _g(1, .65), 2: _g(2, .30), 3: _g(3, .05)}
    ctx = _Ctx(lines, ["2026-11-20", "2026-11-21", "2026-11-23"])
    shares = {p: goalie_share(ctx, lines[p], "2026-11-21") for p in lines}
    assert shares[1] == pytest.approx(.325)
    assert shares[2] == pytest.approx(.30 + .325 * .30 / .35)
    assert sum(shares.values()) == pytest.approx(1.0)
    # not a back-to-back: the usual shares
    assert goalie_share(ctx, lines[1], "2026-11-23") == .65
