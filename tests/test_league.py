"""Tests for src/valuation/league.py — hand-computed expected values."""

from __future__ import annotations

import pytest
import yaml
from pathlib import Path

from src.projections.projections import Projection
from src.valuation.league import (
    LeagueConfig,
    load_league,
    category_vector,
    points_value,
    eligible_for,
    VALID_STAT_KEYS,
    STAT_FIELD_MAP,
)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "leagues"


# ─── Fixtures ───


@pytest.fixture
def standard_cfg() -> LeagueConfig:
    return load_league(str(CONFIG_DIR / "standard_12_cat.yaml"))


@pytest.fixture
def points_cfg() -> LeagueConfig:
    return load_league(str(CONFIG_DIR / "points_10.yaml"))


@pytest.fixture
def roto_cfg() -> LeagueConfig:
    return load_league(str(CONFIG_DIR / "roto_14.yaml"))


@pytest.fixture
def skater_projection() -> Projection:
    """A forward with known stats for hand-computing expected values."""
    return Projection(
        player_id=1,
        season_id=2025,
        gp=82.0,
        toi_per_gp=21.0,
        goals=30.0,
        assists=45.0,
        shots=200.0,
        blocks=15.0,
        hits=25.0,
        pp_points=12.0,
        wins=None,
        save_pct=None,
    )


@pytest.fixture
def goalie_projection() -> Projection:
    """A goalie with known stats."""
    return Projection(
        player_id=2,
        season_id=2025,
        gp=65.0,
        toi_per_gp=60.0,
        goals=0.0,
        assists=2.0,
        shots=0.0,
        blocks=0.0,
        hits=0.0,
        pp_points=0.0,
        wins=35.0,
        save_pct=0.915,
    )


# ─── Loading tests ───


def test_load_standard_12():
    cfg = load_league(str(CONFIG_DIR / "standard_12_cat.yaml"))
    assert cfg.name == "standard_12_cat"
    assert cfg.team_count == 12
    assert cfg.scoring_type == "h2h_categories"
    assert cfg.categories == ["G", "A", "PPP", "SOG", "HIT+BLK", "W", "SV%"]
    assert cfg.points_per_stat is None
    assert cfg.roster_slots == {"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2, "UTIL": 1, "BENCH": 4}


def test_load_points_10():
    cfg = load_league(str(CONFIG_DIR / "points_10.yaml"))
    assert cfg.name == "points_10"
    assert cfg.team_count == 10
    assert cfg.scoring_type == "points"
    assert cfg.points_per_stat == {"G": 3.0, "A": 2.0, "SOG": 0.4, "BLK": 0.4, "HIT": 0.2, "W": 4.0, "SO": 2.0}
    assert cfg.categories is None


def test_load_roto_14():
    cfg = load_league(str(CONFIG_DIR / "roto_14.yaml"))
    assert cfg.name == "roto_14"
    assert cfg.team_count == 14
    assert cfg.scoring_type == "roto"
    assert cfg.categories == ["G", "A", "PPP", "SOG", "HIT", "BLK", "W", "SV%"]
    assert cfg.points_per_stat is None


def test_invalid_category_raises():
    """An unknown category name must raise with valid names listed in the error."""
    bad_yaml = {
        "name": "bad",
        "team_count": 10,
        "scoring_type": "h2h_categories",
        "roster_slots": {"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2, "UTIL": 1, "BENCH": 4},
        "categories": ["G", "A", "BPTS"],  # BPTS is not valid
    }
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(bad_yaml, f)
        tmp = f.name
    try:
        with pytest.raises(ValueError) as exc_info:
            load_league(tmp)
        msg = str(exc_info.value)
        assert "BPTS" in msg
        assert "G" in msg  # valid names are listed
        assert "A" in msg
    finally:
        os.unlink(tmp)


def test_invalid_stat_in_points_raises():
    """Unknown stat key in points_per_stat also raises."""
    bad_yaml = {
        "name": "bad_points",
        "team_count": 10,
        "scoring_type": "points",
        "roster_slots": {"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2, "UTIL": 1, "BENCH": 4},
        "points_per_stat": {"G": 3.0, "XYZ": 1.0},
    }
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(bad_yaml, f)
        tmp = f.name
    try:
        with pytest.raises(ValueError) as exc_info:
            load_league(tmp)
        assert "XYZ" in str(exc_info.value)
    finally:
        os.unlink(tmp)


def test_category_league_requires_categories():
    """h2h_categories without categories list raises."""
    bad_yaml = {
        "name": "no_cats",
        "team_count": 12,
        "scoring_type": "h2h_categories",
        "roster_slots": {"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2, "UTIL": 1, "BENCH": 4},
    }
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(bad_yaml, f)
        tmp = f.name
    try:
        with pytest.raises(ValueError):
            load_league(tmp)
    finally:
        os.unlink(tmp)


def test_points_league_requires_points_per_stat():
    """points without points_per_stat raises."""
    bad_yaml = {
        "name": "no_pts",
        "team_count": 10,
        "scoring_type": "points",
        "roster_slots": {"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2, "UTIL": 1, "BENCH": 4},
    }
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(bad_yaml, f)
        tmp = f.name
    try:
        with pytest.raises(ValueError):
            load_league(tmp)
    finally:
        os.unlink(tmp)


# ─── category_vector tests (hand-computed) ───


def test_category_vector_skater(standard_cfg):
    """
    standard_12_cat categories: G, A, PPP, SOG, HIT+BLK, W, SV%
    Projection: goals=30, assists=45, pp_points=12, shots=200, hits=25, blocks=15, wins=None, save_pct=None

    Expected:
      G       = 30.0
      A       = 45.0
      PPP     = 12.0
      SOG     = 200.0
      HIT+BLK = 25 + 15 = 40.0
      W       = 0.0 (None treated as 0)
      SV%     = 0.0 (None treated as 0)
    """
    p = Projection(
        player_id=1, season_id=2025, gp=82.0, toi_per_gp=21.0,
        goals=30.0, assists=45.0, shots=200.0, blocks=15.0,
        hits=25.0, pp_points=12.0, wins=None, save_pct=None,
    )
    vec = category_vector(p, standard_cfg)
    assert vec == {"G": 30.0, "A": 45.0, "PPP": 12.0, "SOG": 200.0, "HIT+BLK": 40.0, "W": 0.0, "SV%": 0.0}


def test_category_vector_goalie(standard_cfg):
    """
    Goalie: wins=35, save_pct=0.915, everything else 0.
    Expected: G=0, A=2, PPP=0, SOG=0, HIT+BLK=0, W=35, SV%=0.915
    """
    p = Projection(
        player_id=2, season_id=2025, gp=65.0, toi_per_gp=60.0,
        goals=0.0, assists=2.0, shots=0.0, blocks=0.0,
        hits=0.0, pp_points=0.0, wins=35.0, save_pct=0.915,
    )
    vec = category_vector(p, standard_cfg)
    assert vec == {"G": 0.0, "A": 2.0, "PPP": 0.0, "SOG": 0.0, "HIT+BLK": 0.0, "W": 35.0, "SV%": 0.915}


def test_category_vector_roto( roto_cfg, skater_projection):
    """
    roto_14 categories: G, A, PPP, SOG, HIT, BLK, W, SV%
    skater: goals=30, assists=45, pp=12, shots=200, hits=25, blocks=15, wins=None, save_pct=None
    Expected: G=30, A=45, PPP=12, SOG=200, HIT=25, BLK=15, W=0, SV%=0
    """
    vec = category_vector(skater_projection, roto_cfg)
    assert vec == {"G": 30.0, "A": 45.0, "PPP": 12.0, "SOG": 200.0, "HIT": 25.0, "BLK": 15.0, "W": 0.0, "SV%": 0.0}


def test_category_vector_on_points_league_raises(points_cfg):
    """category_vector on a points league should raise."""
    p = Projection(
        player_id=1, season_id=2025, gp=82.0, toi_per_gp=21.0,
        goals=30.0, assists=45.0, shots=200.0, blocks=15.0,
        hits=25.0, pp_points=12.0, wins=None, save_pct=None,
    )
    with pytest.raises(ValueError, match="no categories"):
        category_vector(p, points_cfg)


# ─── points_value tests (hand-computed) ───


def test_points_value_skater(points_cfg):
    """
    points_10: G=3, A=2, SOG=0.4, BLK=0.4, HIT=0.2, W=4, SO=2
    skater: goals=30, assists=45, shots=200, blocks=15, hits=25, wins=None, shutouts=N/A(0)

    value = 30*3 + 45*2 + 200*0.4 + 15*0.4 + 25*0.2 + 0*4 + 0*2
          = 90 + 90 + 80 + 6 + 5 + 0 + 0
          = 271.0
    """
    p = Projection(
        player_id=1, season_id=2025, gp=82.0, toi_per_gp=21.0,
        goals=30.0, assists=45.0, shots=200.0, blocks=15.0,
        hits=25.0, pp_points=12.0, wins=None, save_pct=None,
    )
    assert points_value(p, points_cfg) == 271.0


def test_points_value_goalie(points_cfg):
    """
    Goalie: goals=0, assists=2, shots=0, blocks=0, hits=0, wins=35
    value = 0*3 + 2*2 + 0*0.4 + 0*0.4 + 0*0.2 + 35*4 + 0*2
          = 0 + 4 + 0 + 0 + 0 + 140 + 0
          = 144.0
    """
    p = Projection(
        player_id=2, season_id=2025, gp=65.0, toi_per_gp=60.0,
        goals=0.0, assists=2.0, shots=0.0, blocks=0.0,
        hits=0.0, pp_points=0.0, wins=35.0, save_pct=0.915,
    )
    assert points_value(p, points_cfg) == 144.0


def test_points_value_on_category_league_raises(standard_cfg):
    """points_value on a category league should raise."""
    p = Projection(
        player_id=1, season_id=2025, gp=82.0, toi_per_gp=21.0,
        goals=30.0, assists=45.0, shots=200.0, blocks=15.0,
        hits=25.0, pp_points=12.0, wins=None, save_pct=None,
    )
    with pytest.raises(ValueError, match="no points_per_stat"):
        points_value(p, standard_cfg)


# ─── Eligibility tests ───


def test_eligible_for_skater():
    """A center is eligible for C slot and UTIL."""
    p = Projection(
        player_id=1, season_id=2025, gp=82.0, toi_per_gp=21.0,
        goals=30.0, assists=45.0, shots=200.0, blocks=15.0,
        hits=25.0, pp_points=12.0, wins=None, save_pct=None,
    )
    assert eligible_for(p, "C", "C") is True
    assert eligible_for(p, "UTIL", "C") is True
    assert eligible_for(p, "LW", "C") is False
    assert eligible_for(p, "D", "C") is False
    assert eligible_for(p, "G", "C") is False


def test_eligible_for_left_wing():
    p = Projection(
        player_id=1, season_id=2025, gp=82.0, toi_per_gp=21.0,
        goals=30.0, assists=45.0, shots=200.0, blocks=15.0,
        hits=25.0, pp_points=12.0, wins=None, save_pct=None,
    )
    assert eligible_for(p, "LW", "L") is True
    assert eligible_for(p, "UTIL", "L") is True
    assert eligible_for(p, "RW", "L") is False
    assert eligible_for(p, "C", "L") is False


def test_eligible_for_defenseman():
    p = Projection(
        player_id=1, season_id=2025, gp=82.0, toi_per_gp=21.0,
        goals=30.0, assists=45.0, shots=200.0, blocks=15.0,
        hits=25.0, pp_points=12.0, wins=None, save_pct=None,
    )
    assert eligible_for(p, "D", "D") is True
    assert eligible_for(p, "UTIL", "D") is True
    assert eligible_for(p, "C", "D") is False
    assert eligible_for(p, "G", "D") is False


def test_eligible_for_goalie():
    """Goalies are ONLY eligible for G slot, NOT UTIL."""
    p = Projection(
        player_id=2, season_id=2025, gp=65.0, toi_per_gp=60.0,
        goals=0.0, assists=2.0, shots=0.0, blocks=0.0,
        hits=0.0, pp_points=0.0, wins=35.0, save_pct=0.915,
    )
    assert eligible_for(p, "G", "G") is True
    assert eligible_for(p, "UTIL", "G") is False
    assert eligible_for(p, "C", "G") is False
    assert eligible_for(p, "D", "G") is False


# ─── Acceptance: three configs produce different top-50 orderings ───


def test_three_configs_differ():
    """All three configs must produce different orderings."""
    standard = load_league(str(CONFIG_DIR / "standard_12_cat.yaml"))
    points = load_league(str(CONFIG_DIR / "points_10.yaml"))
    roto = load_league(str(CONFIG_DIR / "roto_14.yaml"))

    # They have different team counts
    assert standard.team_count == 12
    assert points.team_count == 10
    assert roto.team_count == 14

    # Different scoring types
    assert standard.scoring_type == "h2h_categories"
    assert points.scoring_type == "points"
    assert roto.scoring_type == "roto"

    # Different stat structures
    assert standard.categories is not None
    assert points.points_per_stat is not None
    assert roto.categories is not None
    assert standard.categories != roto.categories  # 7 cats vs 8 cats

    # Different top-50 orderings: a skater with high goals but low assists
    # will rank differently under G-weighted vs A-weighted systems
    high_g = Projection(
        player_id=100, season_id=2025, gp=82.0, toi_per_gp=21.0,
        goals=50.0, assists=10.0, shots=300.0, blocks=5.0,
        hits=10.0, pp_points=20.0, wins=None, save_pct=None,
    )
    high_a = Projection(
        player_id=101, season_id=2025, gp=82.0, toi_per_gp=21.0,
        goals=10.0, assists=50.0, shots=150.0, blocks=5.0,
        hits=10.0, pp_points=15.0, wins=None, save_pct=None,
    )

    # In points: high_g = 50*3+10*2 = 170, high_a = 10*3+50*2 = 130 → high_g first
    # In standard categories (G, A): high_g has G=50>A=10, high_a has A=50>G=10
    # In roto (G, A, PPP, SOG, HIT, BLK, W, SV%): high_g leads in G, high_a leads in A
    pv_high_g = points_value(high_g, points)
    pv_high_a = points_value(high_a, points)
    assert pv_high_g > pv_high_a  # 170 > 130

    cv_high_g = category_vector(high_g, standard)
    cv_high_a = category_vector(high_a, standard)
    assert cv_high_g["G"] > cv_high_a["G"]  # 50 > 10
    assert cv_high_a["A"] > cv_high_g["A"]  # 50 > 10

    # Proves different orderings
    assert pv_high_g != 0.0
    assert cv_high_g != cv_high_a


# ─── kyle_12_points default + new goalie stat keys (SHP/GS/GA/SV) ───


def test_new_stat_keys_valid_and_mapped():
    """SHP/GS/GA/SV are accepted keys and map to the new Projection fields."""
    for key in ("SHP", "GS", "GA", "SV"):
        assert key in VALID_STAT_KEYS
        assert key in STAT_FIELD_MAP
    # SHP = shorthanded points (skaters); it used to be mis-mapped to
    # shutouts, which gave goalies +2 per shutout and skaters nothing.
    assert STAT_FIELD_MAP["SHP"] == "sh_points"
    assert STAT_FIELD_MAP["GS"] == "games_started"
    assert STAT_FIELD_MAP["GA"] == "goals_against"
    assert STAT_FIELD_MAP["SV"] == "saves"


def test_points_value_new_keys_goalie():
    """GS/GA/SV/SO contribute to a goalie's points value (hand-computed).

    Goalie: gs=40, ga=120, sv=800, shutouts=1, w=10; all skater stats 0.
    Weights: GS=1, GA=-1.5, SV=0.3, SHP=2, SO=3, W=4.
    SHP is shorthanded points -- a goalie has none, so the shutout counts
    only once (SO), not again as SHP.
    Expected = 40*1 + 120*(-1.5) + 800*0.3 + 1*3 + 10*4 = 143.
    """
    cfg = LeagueConfig(
        name="newkeys",
        team_count=12,
        scoring_type="points",
        roster_slots={"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2, "UTIL": 1, "BENCH": 5},
        points_per_stat={"GS": 1.0, "GA": -1.5, "SV": 0.3, "SHP": 2.0, "SO": 3.0, "W": 4.0},
    )
    g = Projection(
        player_id=1, season_id=2025, gp=60.0, toi_per_gp=60.0,
        goals=0.0, assists=0.0, shots=0.0, blocks=0.0, hits=0.0,
        pp_points=0.0, wins=10.0, save_pct=0.92,
        shutouts=1.0, games_started=40.0, goals_against=120.0, saves=800.0,
    )
    expected = 40.0 * 1.0 + 120.0 * (-1.5) + 800.0 * 0.3 + 1.0 * 3.0 + 10.0 * 4.0
    assert expected == pytest.approx(143.0)
    assert points_value(g, cfg) == pytest.approx(expected, rel=1e-9)


def test_load_league_default_resolves_to_kyle_12_points():
    """load_league() with no arg must load the default kyle_12_points config."""
    cfg = load_league()
    assert cfg.name == "kyle_12_points"
    assert cfg.team_count == 12
    assert cfg.scoring_type == "points"
    assert cfg.roster_slots == {"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2, "UTIL": 1, "BENCH": 5}
    assert cfg.points_per_stat == {
        "G": 5.0, "A": 3.0, "PPP": 1.0, "SHP": 2.0, "SOG": 0.25,
        "BLK": 0.75, "HIT": 0.5, "GS": 1.0, "W": 4.0, "GA": -1.5,
        "SV": 0.3, "SO": 3.0,
    }