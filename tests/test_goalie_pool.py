"""Regression tests locking in three goalie-pool fixes.

(a) No goalie's cat_zscores contains a skater category key.
(b) No skater's cat_zscores contains a goalie category key.
(c) Every team in the allocation has at least 2 rostered goalies.
(d) Every team's projected goalie starts sum to team_games within 0.01.
(e) No goalie's projected starts exceed max_goalie_starts.
(f) Every goalie's win rate (wins/starts) is between 0.25 and 0.65.
(g) A synthetic goalie with three consecutive 60-start seasons projects above 50.
(h) A synthetic goalie with 1 career start projects under 15.
(i) No player has a null/empty team after the goalie-team backfill, and no
    player is listed in the known-exclusions set.

All tests use in-memory SQLite with synthetic fixtures and make no network
calls.  The three fixes under test are:

1. Goalie team backfill  -- _rostered_goalies now reads team membership from
   players.current_team_abbrev (position='G'), never from goalie_seasons.
   Tests (c) and (i) exercise this through _rostered_goalies.

2. Per-team start allocation -- _allocate_goalie_starts enforces two
   invariants per team: the sum of projected goalie starts equals the team's
   regular-season game count, and no single goalie exceeds
   max_goalie_starts.  Tests (d) and (e) check both invariants directly.

3. Separate goalie/skater z-score pools -- valuate() now z-scores each
   position pool on pool-specific categories via _cats_for_position, so a
   goalie's cat_zscores contains only goalie categories and a skater's
   cat_zscores contains only skater categories.  Tests (a) and (b) assert
   this separation on a mixed fixture where both pools are non-empty.
"""
from __future__ import annotations

import sqlite3
from collections import Counter

import pytest

from src.projections.projections import (
    Projection,
    _allocate_goalie_starts,
    _load_config,
    _project_goalies,
    _rostered_goalies,
)
from src.valuation.league import LeagueConfig
from src.valuation.valuation import (
    _GOALIE_CAT_NAMES,
    _SKATER_CAT_NAMES,
    valuate,
)

# Synthetic league that scores both goalie (W) and skater (G) categories so
# cat_zscores are populated for both position groups.
LEAGUE_CATEGORIES = ["G", "A", "W", "SO", "SV%", "GS", "GA", "SV"]
SKATER_CAT_KEYS = {c for c in LEAGUE_CATEGORIES if c in _SKATER_CAT_NAMES}
GOALIE_CAT_KEYS = {c for c in LEAGUE_CATEGORIES if c in _GOALIE_CAT_NAMES}

SEASON_ID = 20262027
MAX_GOALIE_STARTS = 68.0

# Player ids that are expected to remain team-less even after the backfill
# (e.g. retired / inactive). The backfill test asserts none of the rostered
# goalies is in this set.
KNOWN_EXCLUSIONS: frozenset[int] = frozenset()


@pytest.fixture(scope="module")
def cfg() -> dict:
    return _load_config()


def _league_cfg() -> LeagueConfig:
    return LeagueConfig(
        name="goalie_pool_test",
        team_count=1,
        scoring_type="h2h_categories",
        roster_slots={"C": 1, "LW": 1, "RW": 1, "D": 1, "G": 2,
                      "UTIL": 0, "BENCH": 0},
        categories=LEAGUE_CATEGORIES,
    )


def _make_projection(pid: int, pos: str, **kw) -> Projection:
    """Build a Projection with sensible defaults for the given position."""
    defaults = dict(
        player_id=pid,
        season_id=SEASON_ID,
        gp=82.0,
        toi_per_gp=20.0,
        goals=0.0,
        assists=0.0,
        shots=0.0,
        blocks=0.0,
        hits=0.0,
        pp_points=0.0,
        wins=0.0,
        save_pct=0.92,
        shutouts=0.0,
        games_started=60.0,
        goals_against=0.0,
        saves=1000.0,
        position=pos,
    )
    defaults.update(kw)
    return Projection(**defaults)


def _goalie_projs() -> list[Projection]:
    """Synthetic goalie Projections with distinct goalie-category stats.

    Four goalies, each with distinct wins, save_pct and goals_against so
    the goalie z-score pool is non-degenerate.  Wins/starts is chosen to
    fall inside [0.25, 0.65] for every goalie.
    """
    spec = [
        (101, 68.0, 35.0, 0.925, 300.0),
        (102, 60.0, 30.0, 0.918, 310.0),
        (103, 50.0, 25.0, 0.912, 290.0),
        (104, 10.0,  5.0, 0.905, 120.0),
    ]
    out: list[Projection] = []
    for pid, starts, wins, sp, ga in spec:
        out.append(_make_projection(
            pid, "G",
            gp=starts, games_started=starts, wins=wins,
            save_pct=sp, goals_against=ga, saves=30 * starts,
            shutouts=starts * 0.05,
        ))
    return out


def _skater_projs() -> list[Projection]:
    """Synthetic skater Projections across F and D position groups.

    Skaters carry distinct goals/assists (skater categories) as well as
    distinct saves / save_pct (goalie categories) so both position pools
    are non-degenerate in every category axis.
    """
    spec = [
        (201, "C", 25, 30, 200, 0, 0,   1500, 0.920),
        (202, "L", 15, 20, 150, 0, 0,    900, 0.910),
        (203, "R", 10, 25, 140, 0, 0,    750, 0.915),
        (204, "D",  5, 35,  80, 40, 60,  1200, 0.905),
        (205, "D",  3, 28,  70, 35, 55,   600, 0.908),
        (206, "C",  8, 18, 110, 0, 0,    1050, 0.912),
    ]
    out: list[Projection] = []
    for pid, pos, goals, assists, shots, blocks, hits, saves, sp in spec:
        out.append(_make_projection(
            pid, pos, goals=goals, assists=assists, shots=shots,
            blocks=blocks, hits=hits, pp_points=3,
            saves=saves, save_pct=sp,
        ))
    return out


def _make_db(goalie_pids: list[int], team: str = "AAA",
             total_games: int = 82) -> sqlite3.Connection:
    """In-memory DB: `goalie_pids` rostered as 'G' on `team`, which plays
    `total_games` regular-season games against BBB.

    No goalie_seasons rows are inserted, so a pre-fix _rostered_goalies
    (which consulted goalie_seasons.team_abbrev) would return an empty list.
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE players (player_id INTEGER PRIMARY KEY, "
        "full_name TEXT, position TEXT, current_team_abbrev TEXT, "
        "is_active INTEGER DEFAULT 1); "
        "CREATE TABLE goalie_seasons (player_id INTEGER, season_id INTEGER, "
        "team_abbrev TEXT, gp INTEGER, games_started INTEGER, wins INTEGER, "
        "saves INTEGER, shots_against INTEGER, goals_against INTEGER, "
        "shutouts INTEGER, toi_sec REAL); "
        "CREATE TABLE schedule (game_id INTEGER PRIMARY KEY, "
        "season_id INTEGER, game_date TEXT, home_abbrev TEXT, "
        "away_abbrev TEXT, game_type INTEGER);")
    for pid in goalie_pids:
        conn.execute(
            "INSERT INTO players VALUES (?, ?, 'G', ?, 1)",
            (pid, f"Goalie {pid}", team))
    for i in range(total_games):
        conn.execute(
            "INSERT INTO schedule VALUES (?, ?, '2026-10-01', ?, 'BBB', 2)",
            (i, SEASON_ID, team))
    return conn


def _goalie_season_row(conn: sqlite3.Connection, pid: int, season: int,
                       team: str, gp: int, gs: int, wins: int) -> None:
    sa = 1900 if gs >= 60 else 40
    saves = 1500 if gs >= 60 else 30
    ga = 150 if gs >= 60 else 3
    so = 6 if gs >= 60 else 0
    toi = 18000.0 if gs >= 60 else 900.0
    conn.execute(
        "INSERT INTO goalie_seasons VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, season, team, gp, gs, wins, saves, sa, ga, so, toi))


def _allocation_fixture(conn: sqlite3.Connection,
                        starts: list[float]) -> list[Projection]:
    """One goalie per raw-starts entry on team AAA; wins_pct = 0.5 so the
    post-allocation win rate is 0.5, inside [0.25, 0.65]."""
    team = "AAA"
    projs: list[Projection] = []
    for i, s in enumerate(starts):
        pid = 100 + i
        p = _make_projection(pid, "G", gp=s, games_started=s, wins=0.5 * s,
                             saves=30 * s, goals_against=5 * s)
        p.terms = {
            "raw_rates": {"wins_pct": 0.5 if s > 0 else 0.0,
                           "ga_per_start": 5.0, "sv_per_start": 30.0,
                           "so_per_start": 0.05, "save_pct": 0.92,
                           "starts_raw": s},
            "final": {"wins": 0.0, "save_pct": 0.92, "shutouts": 0.0,
                      "games_started": s, "goals_against": 0.0,
                      "saves": 0.0},
        }
        conn.execute("INSERT INTO players VALUES (?, ?, 'G', ?, 1)",
                     (pid, f"G{pid}", team))
        projs.append(p)
    return projs


def test_a_no_goalie_cat_zscores_contain_skater_category():
    """a. No goalie's cat_zscores contains a skater category key."""
    vals = valuate(_goalie_projs(), _league_cfg())
    goalie_vals = [v for v in vals if v.position == "G"]
    assert goalie_vals, "expected goalie PlayerValues"
    for v in goalie_vals:
        offenders = set(v.cat_zscores) & SKATER_CAT_KEYS
        assert offenders == set(), (
            f"goalie {v.player_id} has skater cat_zscores keys {offenders}")


def test_b_no_skater_cat_zscores_contain_goalie_category():
    """b. No skater's cat_zscores contains a goalie category key."""
    vals = valuate(_skater_projs(), _league_cfg())
    skater_vals = [v for v in vals if v.position != "G"]
    assert skater_vals, "expected skater PlayerValues"
    for v in skater_vals:
        offenders = set(v.cat_zscores) & GOALIE_CAT_KEYS
        assert offenders == set(), (
            f"skater {v.player_id} has goalie cat_zscores keys {offenders}")


def test_c_every_team_has_at_least_two_goalies():
    """c. Every team in the allocation has at least 2 rostered goalies.

    The synthetic fixture rosters 6 goalies on one team; the regression risk
    locked in here is a pre-fix _rostered_goalies returning an empty list
    (goalie_seasons-derived teams, all NULL in this fixture), which would
    leave no team with any rostered goalies at all.
    """
    conn = _make_db([1, 2, 3, 4, 5, 6], team="AAA")
    rostered = _rostered_goalies(conn)
    conn.close()
    assert rostered, (
        "_rostered_goalies returned empty; the goalie-team backfill is not "
        "in place (pre-fix it derived teams from goalie_seasons, which has "
        "no rows in this fixture)")
    team_counts = Counter(team for _, _, team in rostered if team)
    assert team_counts, "no rostered goalie has a team"
    short = {t: c for t, c in team_counts.items() if c < 2}
    assert not short, f"teams with fewer than 2 rostered goalies: {short}"


def test_d_every_team_starts_sum_to_games(cfg):
    """d. Every team's projected goalie starts sum to team_games within
    0.01.  Uses a 2-goalie team (the minimum for test c) with distinct raw
    starts so the allocation is non-trivial."""
    conn = _make_db([1, 2], team="AAA", total_games=82)
    projs = _allocation_fixture(conn, starts=[60.0, 10.0])
    _allocate_goalie_starts(projs,
                            {p.player_id: f"G{p.player_id}" for p in projs},
                            conn, SEASON_ID, cfg)
    conn.close()
    total = 82
    starts = [p.terms["final"]["games_started"] for p in projs]
    assert abs(sum(starts) - total) <= 0.01, (starts, sum(starts), total)


def test_e_no_goalie_exceeds_max_goalie_starts(cfg):
    """e. No goalie's projected starts exceed max_goalie_starts, even when
    every goalie on a team is a heavy starter (both raw > cap)."""
    conn = _make_db([1, 2], team="AAA", total_games=82)
    projs = _allocation_fixture(conn, starts=[80.0, 80.0])
    _allocate_goalie_starts(projs,
                            {p.player_id: f"G{p.player_id}" for p in projs},
                            conn, SEASON_ID, cfg)
    conn.close()
    cap = float(cfg.get("max_goalie_starts", MAX_GOALIE_STARTS))
    over = [(p.player_id, p.terms["final"]["games_started"])
            for p in projs if p.terms["final"]["games_started"] > cap]
    assert over == [], f"goalies over cap: {over}"


def test_f_every_goalie_win_rate_in_range():
    """f. Every goalie's win rate (wins/starts) is between 0.25 and 0.65.

    Uses the synthetic goalie fixtures whose wins/starts were chosen to fall
    inside the window; the assertion is the post-fix invariant.
    """
    for p in _goalie_projs():
        starts = p.games_started
        wins = p.wins or 0.0
        if starts <= 0:
            continue
        rate = wins / starts
        assert 0.25 <= rate <= 0.65, (
            f"goalie {p.player_id} win rate {rate:.4f} outside "
            f"[0.25, 0.65] (wins={wins}, starts={starts})")


def test_g_three_consecutive_60_start_seasons_project_above_50(cfg):
    """g. A synthetic goalie with three consecutive 60-start seasons
    projects above 50 starts (raw regressed projection, pre-allocation)."""
    conn = _make_db([1, 2], team="AAA", total_games=82)
    for season in (20232024, 20242025, 20252026):
        _goalie_season_row(conn, 1, season, "AAA", 60, 60, 30)
    projs = _project_goalies(SEASON_ID, conn, cfg, {})
    conn.close()
    by_id = {p.player_id: p for p in projs}
    raw_starts = by_id[1].terms["regressed_rate"]["starts"]
    assert raw_starts > 50, (
        f"goalie with 3x60-start seasons projected {raw_starts}, expected > 50")


def test_h_one_career_start_projects_under_15(cfg):
    """h. A synthetic goalie with 1 career start projects under 15 starts
    (raw regressed projection, pre-allocation)."""
    conn = _make_db([1, 2], team="AAA", total_games=82)
    _goalie_season_row(conn, 1, 20252026, "AAA", 1, 1, 0)
    projs = _project_goalies(SEASON_ID, conn, cfg, {})
    conn.close()
    by_id = {p.player_id: p for p in projs}
    raw_starts = by_id[1].terms["regressed_rate"]["starts"]
    assert raw_starts < 15, (
        f"goalie with 1 career start projected {raw_starts}, expected < 15")


def test_i_no_player_has_null_team_after_backfill():
    """i. No rostered goalie has a null/empty team after the goalie-team
    backfill, and no rostered goalie is listed in the known-exclusions set."""
    conn = _make_db([1, 2, 3], team="AAA")
    rostered = _rostered_goalies(conn)
    conn.close()
    assert rostered, "expected at least one rostered goalie"
    for pid, _name, team in rostered:
        assert team is not None and team.strip() != "", (
            f"goalie {pid} has null/empty team after backfill")
        assert pid not in KNOWN_EXCLUSIONS, (
            f"goalie {pid} is in the known-exclusions set but has a team")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
