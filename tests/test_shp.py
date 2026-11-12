"""Shorthanded points (league stat SHP) end to end: league mapping, category
membership, schema migration, ingest, projection (hand-computed), in-season.
"""
import sqlite3

import pytest

from src.ingest.models import init_db
from src.projections.projections import Projection, project_all
from src.valuation.league import LeagueConfig, STAT_FIELD_MAP, category_vector, points_value
from src.valuation.valuation import _GOALIE_CAT_NAMES, _SKATER_CAT_NAMES, _cats_for_position

PTS = LeagueConfig(name="p", team_count=12, scoring_type="points",
                   roster_slots={"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2},
                   points_per_stat={"G": 5, "SHP": 2, "SO": 3})


def _proj(**kw):
    base = dict(player_id=1, season_id=2027, gp=82.0, toi_per_gp=900.0, goals=0.0,
                assists=0.0, shots=0.0, blocks=0.0, hits=0.0, pp_points=0.0,
                wins=None, save_pct=None)
    base.update(kw)
    return Projection(**base)


def test_shp_scores_skater_shorthanded_points_not_goalie_shutouts():
    assert STAT_FIELD_MAP["SHP"] == "sh_points"
    # skater: 10 G, 3 SHP -> 10*5 + 3*2 = 56
    assert points_value(_proj(goals=10.0, sh_points=3.0), PTS) == pytest.approx(56.0)
    # goalie with 4 shutouts: SO only -> 4*3 = 12 (was 4*3 + 4*2 = 20)
    g = _proj(position="G", wins=20.0, save_pct=0.91, shutouts=4.0)
    assert points_value(g, PTS) == pytest.approx(12.0)


def test_shp_is_a_skater_category():
    assert "SHP" in _SKATER_CAT_NAMES and "SHP" not in _GOALIE_CAT_NAMES
    cfg = LeagueConfig(name="c", team_count=12, scoring_type="roto",
                       roster_slots={"C": 2, "D": 4, "G": 2}, categories=["G", "SHP", "W"])
    assert _cats_for_position(cfg, "C") == ["G", "SHP"]
    assert _cats_for_position(cfg, "G") == ["W"]
    assert category_vector(_proj(sh_points=2.5), cfg)["SHP"] == 2.5


def test_old_db_gets_sh_points_column(tmp_path):
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE skater_seasons (player_id INTEGER NOT NULL, season_id "
                 "INTEGER NOT NULL, team_abbrev TEXT NOT NULL, gp INTEGER, goals INTEGER,"
                 " assists INTEGER, points INTEGER, shots INTEGER, blocks INTEGER, hits "
                 "INTEGER, pp_points INTEGER, pp_toi_sec INTEGER, toi_sec INTEGER, "
                 "PRIMARY KEY (player_id, season_id))")
    conn.execute("INSERT INTO skater_seasons VALUES (1, 20252026, 'AAA', 10, 1, 1, 2, 5, 1, 1, 0, 0, 9000)")
    conn.commit()
    conn.close()
    conn = init_db(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(skater_seasons)")}
    assert "sh_points" in cols and "sh_points" in {
        r[1] for r in conn.execute("PRAGMA table_info(skater_current)")}
    assert conn.execute("SELECT goals, sh_points FROM skater_seasons").fetchone() == (1, None)
    init_db(db).close()                            # idempotent


def test_ingest_stores_shpoints(tmp_path):
    from src.inseason.ingest import ingest_current

    class F:
        def get_paged(self, base, report, season):
            return {
                "cur_skater_summary": [{"playerId": 7, "seasonId": season, "gamesPlayed": 10,
                                        "goals": 2, "assists": 3, "points": 5, "shots": 20,
                                        "ppPoints": 1, "shPoints": 2, "teamAbbrevs": "AAA"}],
                "cur_skater_realtime": [{"playerId": 7, "seasonId": season, "hits": 4,
                                         "blockedShots": 3}],
                "cur_skater_toi": [{"playerId": 7, "seasonId": season,
                                    "evTimeOnIce": 9000, "ppTimeOnIce": 600}],
                "cur_goalie_summary": [],
            }[report]
    db = str(tmp_path / "i.db")
    ingest_current(db, season_id=20262027, schedule=False, rosters=False,
                   fetcher=F(), as_of="2026-11-01")
    row = sqlite3.connect(db).execute(
        "SELECT pp_points, sh_points FROM skater_current WHERE player_id=7").fetchone()
    assert row == (1, 2)


def test_projection_sh_points_hand_computed(tmp_path):
    """Two centres on AAA, 2025-26 only (one season -> weight 1), 50 GP each,
    63000 s total TOI each (60000 EV + 3000 PP; SH TOI not ingested yet).
      positional SHP per 60 = 3600 * 5 / 126000 = .142857
      k = 600 TOI-min (config), n = 63000 / 60 = 1050 min
      A: raw 3600*5/63000 = .285714 -> (.285714*1050 + .142857*600)/1650
         = .233766; rookie weight .5*(1 - 50/200) = .375
         -> .625*.233766 + .375*.142857 = .199675 per 60
      B: raw 0 -> 85.714/1650 = .051948 -> .086039 per 60
      per game at 1260 s TOI = per60 * .35
      GP: share 50/82 regressed: (5*.609756 + 3*.88)/8 = .711098 -> 58.31
      A: .199675 * .35 * 58.31 = 4.07507;  B: 1.75593
    No birth dates -> age adjustment 1.0.
    """
    db = str(tmp_path / "p.db")
    conn = init_db(db)
    for pid, sh in ((1, 5), (2, 0)):
        conn.execute("INSERT INTO players (player_id, full_name, position, current_team_abbrev)"
                     " VALUES (?, ?, 'C', 'AAA')", (pid, f"P{pid}"))
        conn.execute("INSERT INTO skater_seasons (player_id, season_id, team_abbrev, gp, goals,"
                     " assists, points, shots, blocks, hits, pp_points, sh_points, pp_toi_sec,"
                     " toi_sec) VALUES (?, 20252026, 'AAA', 50, 5, 5, 10, 50, 10, 10, 2, ?, 3000,"
                     " 60000)", (pid, sh))
    for g in range(82):
        conn.execute("INSERT INTO schedule VALUES (?, 20252026, '2026-01-01', ?, ?, 2)",
                     (g + 1, "AAA" if g % 2 else "BBB", "BBB" if g % 2 else "AAA"))
    conn.commit()
    conn.close()
    by = {p.player_id: p for p in project_all(20262027, db)}
    assert by[1].gp == pytest.approx(58.31)
    assert by[1].sh_points == pytest.approx(4.075073863636364)
    assert by[2].sh_points == pytest.approx(1.7559261363636365)
    # positional mean per game at A's TOI: .142857 * .35 = .05
    assert by[1].terms["sh_points"]["pos_mean"] == pytest.approx(0.05)


def test_inseason_line_carries_sh_points():
    from src.inseason.model import SKATER_STATS
    from src.inseason.scoring import to_projection
    assert "sh_points" in SKATER_STATS
    p = to_projection(1, "C", {"goals": 0.2, "sh_points": 0.05})
    assert points_value(p, PTS) == pytest.approx(0.2 * 5 + 0.05 * 2)
