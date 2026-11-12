"""Historical players (no longer rostered) are backfilled from the stats
API's bios reports, projected in backtests only, and never crash the goalie
pool when they have no name."""
import sqlite3
from pathlib import Path

from src.ingest.bios import ingest_bios
from tests.pinned import PINNED, pinned_config
from src.projections.goalies import _project_goalies
from src.projections.projections import project_all

SCHEMA = (Path(__file__).resolve().parents[1] / "db" / "schema.sql").read_text()
S1, S2, S3 = 20232024, 20242025, 20252026


class _Bios:
    urls: list[str] = []

    def get_paged(self, base, report, season):
        self.urls.append(base)
        return {"skater_bios": [
                    {"playerId": 1, "skaterFullName": "Old Timer",
                     "positionCode": "D", "birthDate": "1985-03-03"},
                    {"playerId": 2, "skaterFullName": "Rostered Guy",
                     "positionCode": "C", "birthDate": "1999-09-09"},
                    {"playerId": 3, "skaterFullName": "No Birthday",
                     "positionCode": "L"}],
                "goalie_bios": [{"playerId": 9, "goalieFullName": "Old Goalie",
                                 "birthDate": "1986-01-01"}]}[report]


def test_bios_backfill_adds_inactive_rows_and_keeps_roster_data():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO players (player_id, full_name, position, birth_date,"
                 " current_team_abbrev, is_active) VALUES"
                 " (2, 'Rostered Guy', 'C', NULL, 'NYR', 1)")
    f = _Bios()
    counts, notes = ingest_bios(conn, f, S2)
    assert counts == {"player_bios": 4}
    assert notes == [f"bios {S2}: 1 rows without birthDate"]
    assert all("gameTypeId=2 and seasonId=" in u for u in f.urls)
    rows = {r[0]: r[1:] for r in conn.execute(
        "SELECT player_id, full_name, position, birth_date,"
        " current_team_abbrev, is_active FROM players")}
    assert rows[1] == ("Old Timer", "D", "1985-03-03", None, 0)
    # rostered player keeps team / active flag, gains the missing birth date
    assert rows[2] == ("Rostered Guy", "C", "1999-09-09", "NYR", 1)
    assert rows[3] == ("No Birthday", "L", None, None, 0)
    assert rows[9] == ("Old Goalie", "G", "1986-01-01", None, 0)


def _db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for s in (S1, S2, S3):
        conn.executemany("INSERT INTO schedule VALUES (?, ?, '2025-01-01', 'AAA',"
                         " 'BBB', 2)", [(s * 1000 + i, s) for i in range(82)])
    for pid, active, team in ((1, 0, None), (2, 1, "AAA")):
        conn.execute("INSERT INTO players (player_id, full_name, position,"
                     " current_team_abbrev, is_active) VALUES (?, ?, 'C', ?, ?)",
                     (pid, f"P{pid}", team, active))
        for s in (S1, S2):
            conn.execute(
                "INSERT INTO skater_seasons (player_id, season_id, team_abbrev,"
                " gp, goals, assists, shots, blocks, hits, pp_points, sh_points,"
                " pp_toi_sec, toi_sec) VALUES (?, ?, 'AAA', 80, 20, 30, 200, 30,"
                " 40, 10, 1, 14400, 72000)", (pid, s))
    conn.commit()
    return conn


def test_inactive_players_projected_in_backtests_only(tmp_path):
    db = str(tmp_path / "b.db")
    conn = _db(db)
    # production season (no rows yet): only the rostered player
    assert {p.player_id for p in project_all(S3, db, PINNED)} == {2}
    # backtest season: S3 rows exist, so the retired player is projected too
    conn.execute("INSERT INTO skater_seasons (player_id, season_id, team_abbrev,"
                 " gp) VALUES (1, ?, 'AAA', 50)", (S3,))
    conn.commit()
    assert {p.player_id for p in project_all(S3, db, PINNED)} == {1, 2}


def test_backtest_goalie_pool_without_players_rows_does_not_crash():
    """Four AAA goalies played S3 (forces the cap-to-three path, which joins
    names); two have no players row at all."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO schedule VALUES (?, ?, '2025-01-01', 'AAA',"
                     " 'BBB', 2)", [(s * 1000 + i, s) for s in (S2, S3)
                                    for i in range(82)])
    for pid in (11, 12):
        conn.execute("INSERT INTO players (player_id, full_name, position)"
                     " VALUES (?, ?, 'G')", (pid, f"G{pid}"))
    for pid, gs in ((11, 50), (12, 20), (13, 10), (14, 2)):
        for s in (S2, S3):
            conn.execute(
                "INSERT INTO goalie_seasons (player_id, season_id, team_abbrev,"
                " gp, games_started, wins, saves, shots_against, goals_against,"
                " shutouts) VALUES (?, ?, 'AAA', ?, ?, ?, ?, ?, ?, 0)",
                (pid, s, gs, gs, gs // 2, gs * 27, gs * 30, gs * 3))
    projs = _project_goalies(S3, conn, pinned_config(), {"save_pct": 0.9})
    starts = sorted((p.player_id, round(p.games_started, 6)) for p in projs)
    assert [pid for pid, _ in starts] == [11, 12, 13, 14]
    assert sum(gs for _, gs in starts) == 82          # 14 is capped out at 0
    assert dict(starts)[14] == 0
