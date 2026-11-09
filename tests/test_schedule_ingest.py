"""ingest_schedule dates each game by its gameWeek day, not the request date
(response shape from api-web.nhle.com/v1/schedule/2026-11-01, trimmed)."""
import datetime

from src.ingest.models import init_db
from src.ingest.schedule import ingest_schedule


def _game(gid, home, away):
    return {"id": gid, "gameType": 2, "homeTeam": {"abbrev": home},
            "awayTeam": {"abbrev": away}}


class _Fetcher:
    """Each request returns 7 days starting at the requested date."""

    GAMES = {"2026-11-01": [_game(1, "CAR", "WPG")],
             "2026-11-02": [_game(2, "CGY", "SJS"), _game(3, "COL", "CHI")],
             "2026-11-07": [_game(4, "WSH", "FLA")],
             "2026-11-08": [_game(5, "NYR", "BOS")]}

    def __init__(self):
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        start = datetime.date.fromisoformat(url.rsplit("/", 1)[1])
        days = [(start + datetime.timedelta(days=i)).isoformat() for i in range(7)]
        return {"nextStartDate": days[-1], "gameWeek": [
            {"date": d, "dayAbbrev": "X", "games": self.GAMES.get(d, [])} for d in days]}


def _dates(conn):
    return dict(conn.execute("SELECT game_id, game_date FROM schedule"))


def test_weekly_step_keeps_each_games_own_date(tmp_path, monkeypatch):
    import src.ingest.schedule as sch
    monkeypatch.setattr(sch.time, "sleep", lambda s: None)
    conn = init_db(str(tmp_path / "s.db"))
    f = _Fetcher()
    ingest_schedule(conn, f, 20262027, start=datetime.date(2026, 11, 1), step_days=7)
    assert _dates(conn) == {1: "2026-11-01", 2: "2026-11-02", 3: "2026-11-02",
                            4: "2026-11-07", 5: "2026-11-08"}
    assert f.urls[:2] == ["https://api-web.nhle.com/v1/schedule/2026-11-01",
                          "https://api-web.nhle.com/v1/schedule/2026-11-08"]


def test_daily_step_same_result(tmp_path, monkeypatch):
    import src.ingest.schedule as sch
    monkeypatch.setattr(sch.time, "sleep", lambda s: None)
    conn = init_db(str(tmp_path / "s.db"))
    ingest_schedule(conn, _Fetcher(), 20262027, start=datetime.date(2026, 11, 1))
    assert _dates(conn)[4] == "2026-11-07" and _dates(conn)[2] == "2026-11-02"


def test_schedule_warning_on_impossible_day(tmp_path):
    from src.inseason.evaluate import Context
    ctx = Context(None, {}, 20262027, "2026-10-31", {}, {}, None,
                  [("2026-11-07", f"H{i}", f"A{i}") for i in range(17)])
    assert "17 games on 2026-11-07" in ctx.schedule_warning()
    ctx.games = ctx.games[:16]
    assert ctx.schedule_warning() is None
