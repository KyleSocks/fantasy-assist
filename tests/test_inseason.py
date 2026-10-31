"""Tests for src/inseason -- hand-computed expected values.

Tiny 3-team world (AAA, BBB, CCC), season 20262027, every team 10 GP.
All K values are 10 (see ICFG) so every blend is (prior*10 + total)/(10 + n).

Skaters (season-to-date over 10 GP: G, A, SOG, BLK, HIT, PPP):
  1 Alex Ovechkin  L AAA   2  3  20   4  10  1   prior (per game) .5 .5 4 .5 1 .2
  2 Strong Center  C BBB   5  5  30   2   5  3   no prior
  3 Depth Wing     R CCC   1  1  10   1  20  0   no prior
  4 Dee Man        D BBB   0  4  15  15  10  2   no prior
  5 Dee Two        D CCC   1  1   8  10   5  0   no prior
League per-game fallback prior (50 GP): G .18 A .28 SOG 1.66 BLK .64 HIT 1 PPP .12

Goalies (GS, W, SV, SA, GA, SO):
  6 Goalie A  AAA   6  3  150  165  15  0   prior per start W .5 SV 25 GA 2.5
                                            SO .1, SV% .91, 2.4 season starts
  7 Goalie B  BBB   8  6  230  245  15  1   no prior

Schedule (regular season): 10-20 AAA-BBB, 10-22 BBB-CCC, 11-01 AAA-CCC,
11-02 BBB-CCC, 11-03 BBB-AAA, 11-04 CCC-BBB.

Points league: G 2, A 1, SOG .5, BLK .5, W 3, SV .1, GA -1; one team,
C1 D1 G1 -> pools F=1, D=1, G=1.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.ingest.models import init_db
from src.inseason.evaluate import (
    build_context,
    find_players,
    hold_check,
    schedule_grid,
    streams,
)
from src.inseason.ingest import ingest_current
from src.inseason.model import blend, opponent_factors
from src.inseason.scoring import GameScorer
from src.valuation.league import LeagueConfig

SEASON = 20262027
ICFG = {
    "skater_k_games": {s: 10 for s in
                       ("goals", "assists", "pp_points", "shots", "hits", "blocks")},
    "goalie_k_starts": {s: 10 for s in
                        ("wins", "saves", "goals_against", "shutouts")},
    "goalie_save_pct_k_shots": 100,
    "start_share_k_games": 10,
    "opponent_k_games": 10,
    "off_night_max_games": 1,
    "hold_margin": 0.15,
}
PRIORS = {
    1: {"pg": {"goals": .5, "assists": .5, "shots": 4.0, "blocks": .5,
               "hits": 1.0, "pp_points": .2}, "toi_pg": 1000.0},
    6: {"per_start": {"wins": .5, "saves": 25.0, "goals_against": 2.5,
                      "shutouts": .1}, "save_pct": .91, "starts": 2.4},
}
POINTS = LeagueConfig(
    name="t_points", team_count=1, scoring_type="points",
    roster_slots={"C": 1, "LW": 0, "RW": 0, "D": 1, "G": 1, "UTIL": 0, "BENCH": 0},
    points_per_stat={"G": 2, "A": 1, "SOG": 0.5, "BLK": 0.5,
                     "W": 3, "SV": 0.1, "GA": -1})


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "t.db")
    conn = init_db(path)
    players = [(1, "Alex Ovechkin", "L", "AAA"), (2, "Strong Center", "C", "BBB"),
               (3, "Depth Wing", "R", "CCC"), (4, "Dee Man", "D", "BBB"),
               (5, "Dee Two", "D", "CCC"), (6, "Goalie A", "G", "AAA"),
               (7, "Goalie B", "G", "BBB")]
    for pid, name, pos, team in players:
        conn.execute("INSERT INTO players (player_id, full_name, position,"
                     " current_team_abbrev) VALUES (?,?,?,?)",
                     (pid, name, pos, team))
    sk = [(1, "AAA", 2, 3, 20, 4, 10, 1), (2, "BBB", 5, 5, 30, 2, 5, 3),
          (3, "CCC", 1, 1, 10, 1, 20, 0), (4, "BBB", 0, 4, 15, 15, 10, 2),
          (5, "CCC", 1, 1, 8, 10, 5, 0)]
    for pid, team, g, a, s, b, h, pp in sk:
        conn.execute(
            "INSERT INTO skater_current (player_id, season_id, team_abbrev, gp,"
            " goals, assists, points, shots, blocks, hits, pp_points, pp_toi_sec,"
            " toi_sec, as_of) VALUES (?,?,?,10,?,?,?,?,?,?,?,0,9000,'2026-11-01')",
            (pid, SEASON, team, g, a, g + a, s, b, h, pp))
    for pid, team, gs, w, sv, sa, ga, so in [(6, "AAA", 6, 3, 150, 165, 15, 0),
                                             (7, "BBB", 8, 6, 230, 245, 15, 1)]:
        conn.execute(
            "INSERT INTO goalie_current (player_id, season_id, team_abbrev, gp,"
            " games_started, wins, losses, ot_losses, saves, shots_against,"
            " goals_against, shutouts, as_of)"
            " VALUES (?,?,?,?,?,?,0,0,?,?,?,?,'2026-11-01')",
            (pid, SEASON, team, gs, gs, w, sv, sa, ga, so))
    games = [("2026-10-20", "AAA", "BBB"), ("2026-10-22", "BBB", "CCC"),
             ("2026-11-01", "AAA", "CCC"), ("2026-11-02", "BBB", "CCC"),
             ("2026-11-03", "BBB", "AAA"), ("2026-11-04", "CCC", "BBB")]
    for i, (d, h, a) in enumerate(games, 1):
        conn.execute("INSERT INTO schedule VALUES (?,?,?,?,?,2)",
                     (i, SEASON, d, h, a))
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def ctx(db):
    return build_context(db, POINTS, priors=PRIORS, icfg=ICFG)


def test_blend():
    # (0.5*40 + 2) / (40 + 10) = 22/50
    assert blend(0.5, 2, 10, 40) == pytest.approx(0.44)
    assert blend(0.3, 0, 0, 0) == 0.3


def test_opponent_factors_hand_computed():
    sk = {1: {"team_abbrev": "AAA", "goals": 2, "shots": 20},
          2: {"team_abbrev": "BBB", "goals": 4, "shots": 40}}
    gl = {6: {"team_abbrev": "AAA", "goals_against": 30, "shots_against": 300},
          7: {"team_abbrev": "BBB", "goals_against": 10, "shots_against": 100}}
    f = opponent_factors(sk, gl, {"AAA": 10, "BBB": 10}, k=10)
    # league GA/g = 40/20 = 2. AAA: (30 + 20)/20 = 2.5 -> 1.25; BBB: 30/20 -> .75
    assert f["AAA"]["ga"] == pytest.approx(1.25)
    assert f["BBB"]["ga"] == pytest.approx(0.75)
    # league GF/g = 6/20 = .3. AAA: (2 + 3)/20 = .25 -> .8333
    assert f["AAA"]["gf"] == pytest.approx(0.25 / 0.3)


def test_skater_blend_and_fallback(ctx):
    ovi = ctx.lines[1]
    assert ovi.blended["goals"] == pytest.approx(0.35)    # (5 + 2)/20
    assert ovi.blended["shots"] == pytest.approx(3.0)     # (40 + 20)/20
    assert ovi.observed["goals"] == pytest.approx(0.2)
    assert ovi.toi_pg_obs == pytest.approx(900.0)
    center = ctx.lines[2]                                  # league fallback prior
    assert not center.has_projection
    assert center.prior["goals"] == pytest.approx(0.18)
    assert center.blended["shots"] == pytest.approx(2.33)  # (16.6 + 30)/20


def test_goalie_blend_and_start_share(ctx):
    a = ctx.lines[6]
    assert a.start_share_prior == pytest.approx(0.8)  # 2.4 / 3 AAA season games
    assert a.start_share_obs == pytest.approx(0.6)    # 6 / 10
    assert a.start_share == pytest.approx(0.7)        # (8 + 6)/20
    assert a.blended["wins"] == pytest.approx(0.5)    # (5 + 3)/16
    assert a.blended["save_pct"] == pytest.approx(241 / 265)
    b = ctx.lines[7]                                   # no prior -> 0 share prior
    assert b.start_share == pytest.approx(0.4)        # (0 + 8)/20
    assert b.blended["wins"] == pytest.approx((9 / 14 * 10 + 6) / 18)


def test_points_per_game_and_replacement(ctx):
    sc = ctx.scorer
    assert sc.per_game[1] == pytest.approx(2.825)   # .7 + .4 + 1.5 + .225
    assert sc.per_game[2] == pytest.approx(2.445)
    assert sc.per_game[6] == pytest.approx(1.05)    # .7 * (1.5 + 2.5 - 2.5)
    assert sc.per_game[7] == pytest.approx(0.4 * (3 * (9 / 14 * 10 + 6) / 18
                                                  + 0.1 * (380 / 14 * 10 + 230) / 18
                                                  - (30 / 14 * 10 + 15) / 18))
    assert sc.replacement == pytest.approx({"F": 2.445, "D": 1.495, "G": 1.05})
    assert sc.group_rank[1] == 1 and sc.group_rank[3] == 3


def test_hold_check_light_schedule(ctx):
    h = hold_check(ctx, 1, "2026-11-01", 4)
    assert [g["opp"] for g in h["window"]["games"]] == ["CCC", "BBB"]
    # vs CCC (ga .5, sa .5): .35 + .2 + .75 + .225
    assert h["window"]["games"][0]["value"] == pytest.approx(1.525)
    # vs BBB (ga 1.25, sa 19.0833/13.6667): .875 + .5 + 2.094512 + .225
    assert h["window"]["games"][1]["value"] == pytest.approx(3.694512, abs=1e-6)
    assert h["avg_window_games"] == pytest.approx(8 / 3)
    assert h["window_vs_replacement"] == pytest.approx(
        1.525 + 3.694512 - 2.445 * 8 / 3, abs=1e-6)
    assert h["window"]["off_nights"] == 2
    # 2.825 >= 2.445 * 1.15 = 2.81175 -> HOLD despite the light window
    assert h["verdict"] == "HOLD"
    assert h["form"]["prior"] == pytest.approx(3.75)
    assert h["form"]["observed"] == pytest.approx(1.9)
    assert h["form"]["vs_prior_pct"] == pytest.approx(-1.85 / 3.75)


def test_hold_verdicts_drop_and_borderline(ctx):
    assert hold_check(ctx, 3, "2026-11-01", 4)["verdict"] == "DROP CANDIDATE"
    assert hold_check(ctx, 2, "2026-11-01", 4)["verdict"] == "BORDERLINE"


def test_streams_likely_available(ctx):
    rows = streams(ctx, "2026-11-02")          # BBB vs CCC
    assert [r["player_id"] for r in rows] == [5, 3, 2]
    assert rows[0]["value"] == pytest.approx(1.85625)
    assert rows[1]["value"] == pytest.approx(1.701067, abs=1e-6)
    assert rows[2]["value"] == pytest.approx(1.3275)
    every = {r["player_id"] for r in streams(ctx, "2026-11-02",
                                             likely_available=False)}
    assert every == {2, 3, 4, 5, 7}


def test_schedule_grid(ctx):
    rows = schedule_grid(ctx, "2026-11-01", 4)
    assert [(r["team"], r["games"]) for r in rows] == [
        ("BBB", 3), ("CCC", 3), ("AAA", 2)]
    assert rows[2]["opponents"] == ["CCC", "BBB"]


def test_find_players_accent_and_case(ctx):
    ctx.lines[1].name = "Alex Ovečkin"
    assert [ln.player_id for ln in find_players(ctx, "ovecKIN")] == [1]


def test_category_scorer_uncentered():
    cfg = LeagueConfig(
        name="t_cat", team_count=2, scoring_type="roto",
        roster_slots={"C": 1, "LW": 0, "RW": 0, "D": 0, "G": 0,
                      "UTIL": 0, "BENCH": 0},
        categories=["G", "SOG"])
    sc = GameScorer(cfg, {1: ("C", {"goals": 1.0, "shots": 3.0}),
                          2: ("C", {"goals": 0.0, "shots": 1.0}),
                          3: ("C", {"goals": 0.5, "shots": 2.0})})
    # pool = top 2 by raw (players 1, 3): G mu .75 sd .25, SOG mu 2.5 sd .5
    assert sc.per_game == pytest.approx({1: 10.0, 2: 2.0, 3: 6.0})
    assert sc.replacement["F"] == pytest.approx(2.0)


class _FixtureFetcher:
    """Replays the real 2021-22 API rows in tests/fixtures."""

    def __init__(self):
        import json
        from pathlib import Path
        self.data = json.loads((Path(__file__).parent / "fixtures"
                                / "ingest_20212022.json").read_text())
        self.urls = []

    def get_paged(self, base_url, report, season):
        self.urls.append(base_url)
        return self.data[report.removeprefix("cur_")]


def test_ingest_current_writes_separate_tables(tmp_path):
    db = str(tmp_path / "i.db")
    f = _FixtureFetcher()
    counts = ingest_current(db, season_id=20212022, schedule=False,
                            rosters=False, fetcher=f, as_of="2022-01-15")
    assert counts["skater_current"] > 0 and counts["goalie_current"] == 2
    assert all("gameTypeId=2" in u for u in f.urls)
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT goals, assists, shots, hits, blocks, team_abbrev, as_of"
        " FROM skater_current WHERE player_id=8478402").fetchone()
    assert row == (54, 102, 375, 126, 30, "EDM", "2022-01-15")
    # The draft-board tables are untouched by the in-season ingest.
    assert conn.execute("SELECT COUNT(*) FROM skater_seasons").fetchone()[0] == 0
    conn.close()


def test_load_taken_and_streams_exclusion(ctx, tmp_path):
    from src.inseason.evaluate import load_taken
    f = tmp_path / "taken.txt"
    f.write_text("dee two   # my D\n\nNobody Here\n", encoding="utf-8")
    ids, missing = load_taken(ctx, str(f))
    assert ids == {5} and missing == ["Nobody Here"]
    # with an explicit taken list the rostered-pool guess is not applied,
    # so rank-1 players (4 Dee Man, 7 Goalie B) come back
    got = [r["player_id"] for r in streams(ctx, "2026-11-02", taken=ids)]
    assert 5 not in got and {2, 3, 4, 7} == set(got)


def test_api_endpoints(db, tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    import src.api.inseason as api
    from src.api.main import app
    leagues = tmp_path / "leagues"
    leagues.mkdir()
    (leagues / "t_points.yaml").write_text(
        Path("config/leagues/kyle_12_points.yaml").read_text())
    monkeypatch.setattr(api, "DB_PATH", Path(db))
    monkeypatch.setattr(api, "LEAGUE_DIR", leagues)
    api._CACHE.clear()
    c = TestClient(app)
    r = c.get("/api/inseason/streams?date=2026-11-02&league=t_points&all=true")
    assert r.status_code == 200
    body = r.json()
    assert body["nhl_games"] == 1 and body["unit"] == "fpts"
    assert {p["team"] for p in body["players"]} <= {"BBB", "CCC"}
    r = c.get("/api/inseason/player/1?league=t_points&start=2026-11-01&days=4")
    assert r.status_code == 200 and len(r.json()["window"]["games"]) == 2
    r = c.get("/api/inseason/schedule?league=t_points&start=2026-11-01&days=4")
    assert [t["games"] for t in r.json()["teams"]] == [3, 3, 2]
    assert c.get("/api/inseason/player/999?league=t_points").status_code == 404
    assert c.get("/api/inseason/streams?league=nope").status_code == 404


def test_goalie_streams_assume_he_starts(ctx):
    # Goalie B (BBB, blended start share .4) vs CCC on 11-02 (gf .8333,
    # sf .825301). Per START: 3*.690476 + .1*27.857143*.825301
    # - 2.023810*.833333 = 2.68398 -- not scaled down by the .4 share.
    rows = {r["player_id"]: r for r in streams(ctx, "2026-11-02",
                                               likely_available=False)}
    g = rows[7]
    assert g["value"] == pytest.approx(2.68398, abs=1e-4)
    assert g["start_share"] == pytest.approx(0.4)
    assert g["per_game"] == pytest.approx(3 * (9 / 14 * 10 + 6) / 18
                                          + 0.1 * (380 / 14 * 10 + 230) / 18
                                          - (30 / 14 * 10 + 15) / 18)
    # the season / lineup path still weights by start share
    assert ctx.scorer.per_game[7] == pytest.approx(0.4 * g["per_game"])


def test_ir_players_protected_and_small_samples_not_flagged(ctx):
    from src.inseason.roster import _player_row, audit, parse_players
    inactive = set()
    roster, _ = parse_players(ctx, "Alex Ovechkin | AAA | LW\nDee Two | CCC | D | IR\n",
                              inactive)
    assert set(roster) == {1} and inactive == {5}
    a = audit(ctx, roster, "2026-11-01", 4, inactive)
    ir = next(r for r in a["players"] if r["id"] == 5)
    assert ir["verdict"] == "IR" and ir["lineup_adds"] == 0.0
    assert ir["flags"][0] == "injured reserve: hold"
    # Ovi: 10 GP, 49% below projection -> flagged
    assert "producing below projection" in _player_row(ctx, 1, {"LW"}, "2026-11-01", 4)["flags"]
    # same line over 5 GP (hurt for the rest) -> small sample, not a flag
    ctx.lines[1].gp = 5
    flags = _player_row(ctx, 1, {"LW"}, "2026-11-01", 4)["flags"]
    assert "small sample (5 GP)" in flags and "producing below projection" not in flags


def test_goalie_streams_skip_rare_starters_and_skater_group(ctx):
    ctx.icfg = dict(ctx.icfg, stream_goalie_min_start_share=0.5)
    ids = {r["player_id"] for r in streams(ctx, "2026-11-02", likely_available=False)}
    assert 7 not in ids                       # Goalie B starts 40% < 50%
    sk = streams(ctx, "2026-11-02", group="S", likely_available=False)
    assert sk and all(r["pos"] != "G" for r in sk)


def test_waiver_and_out_markers_in_lists(ctx):
    from src.inseason.roster import parse_players
    avail, out = {}, set()
    fas, _ = parse_players(ctx, "Strong Center | BBB | C | W 2026-11-03\n"
                                "Depth Wing | CCC | RW | OUT  # IR-LT\n"
                                "Dee Two | CCC | D  # DTD\n", out, avail)
    assert set(fas) == {2, 5} and out == {3} and avail == {2: "2026-11-03"}
    # a waiver player can't be streamed before he clears
    ids = {r["player_id"] for r in streams(ctx, "2026-11-02", likely_available=False,
                                           unavailable={2})}
    assert 2 not in ids and 5 in ids
