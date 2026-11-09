"""Tests for the NHL ingest pipeline.

Model parsing and schema only; the live-API ingest tests were removed
(network + real data). Hand-computed expected values:
- Connor McDavid (8478402) 2021-22 (reg+playoff): 54 goals, 102 assists,
  156 points, 375 shots, pp=52; TOI 105016 sec EV, 21504 sec PP
- Igor Shesterkin (8478048) 2021-22 (reg+playoff): 46W, 22L, 4OTL, 6SO
The NHL stats API returns regular + playoffs combined per season, so games
played can exceed 82 (e.g. McDavid 2021-22: 80 reg + 16 playoff = 96).
"""
from __future__ import annotations

import tempfile
from pathlib import Path


from src.ingest.models import (
    GoalieSummary,
    SkaterSummary,
    SkaterTOI,
    init_db,
)


# ── Model parsing ──────────────────────────────────────────────────────


class TestSkaterSummary:
    def test_known_player_stats(self):
        """McDavid 2021-22 (reg+playoff): 54G, 102A, 156P, 52 PP, 375 shots."""
        raw = {
            "playerId": 8478402,
            "seasonId": 20212022,
            "gamesPlayed": 96,
            "goals": 54,
            "assists": 102,
            "points": 156,
            "shots": 375,
            "ppPoints": 52,
            "teamAbbrevs": "EDM",
        }
        s = SkaterSummary(**raw)
        assert s.goals == 54
        assert s.assists == 102
        assert s.points == 156
        assert s.shots == 375
        assert s.ppPoints == 52
        assert s.teamAbbrevs == "EDM"

    def test_extra_fields_ignored(self):
        raw = {
            "playerId": 1,
            "seasonId": 20202021,
            "goals": 5,
            "assists": 10,
            "points": 15,
            "someUnknownField": "x",
        }
        s = SkaterSummary(**raw)
        assert s.goals == 5
        assert s.points == 15

    def test_missing_optional_fields(self):
        s = SkaterSummary(playerId=1)
        assert s.goals is None
        assert s.ppPoints is None


class TestSkaterTOI:
    def test_known_toi_values(self):
        """McDavid 2021-22 (reg+playoff): 105016 sec EV, 21504 sec PP."""
        raw = {
            "playerId": 8478402,
            "seasonId": 20212022,
            "evTimeOnIce": 105016.0,
            "ppTimeOnIce": 21504.0,
        }
        t = SkaterTOI(**raw)
        assert t.evTimeOnIce == 105016.0
        assert t.ppTimeOnIce == 21504.0


class TestGoalieSummary:
    def test_known_goalie_stats(self):
        """Igor Shesterkin 2021-22 (reg+playoff): 46W, 22L, 4OTL, 6SO."""
        raw = {
            "playerId": 8478048,
            "seasonId": 20212022,
            "gamesPlayed": 73,
            "gamesStarted": 72,
            "wins": 46,
            "losses": 22,
            "otLosses": 4,
            "saves": 2185,
            "shotsAgainst": 2342,
            "goalsAgainst": 157,
            "shutouts": 6,
            "teamAbbrevs": "NYR",
        }
        g = GoalieSummary(**raw)
        assert g.wins == 46
        assert g.losses == 22
        assert g.otLosses == 4
        assert g.shutouts == 6


# ── DB init ────────────────────────────────────────────────────────────


class TestInitDb:
    def test_creates_all_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            conn = init_db(db_path)
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in cur.fetchall()}
            conn.close()
        expected = {
            "players", "skater_seasons", "goalie_seasons",
            "teams", "schedule", "ingest_log",
        }
        assert expected <= tables

    def test_pp_toi_column_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            conn = init_db(db_path)
            cur = conn.execute("PRAGMA table_info(skater_seasons)")
            cols = {row[1] for row in cur.fetchall()}
            conn.close()
        assert "pp_toi_sec" in cols
        assert "pp_points" in cols


# ── Full ingest (network) ──────────────────────────────────────────────
