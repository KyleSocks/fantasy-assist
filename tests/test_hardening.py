"""Hardening + optimisation regression tests: input limits, cross-site write
guard, security headers, refresh lock, HTTP retries, file-stamp caches and
the schedule index. No real data, no network."""
from __future__ import annotations

import datetime
import os
import sqlite3
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.api.inseason as api
from src import cache, net
from src.api.main import app
from src.inseason.evaluate import Context
from tests.test_inseason import db  # noqa: F401  (pytest fixture)


def _client(db, tmp_path, monkeypatch):  # noqa: F811
    leagues = tmp_path / "leagues"
    leagues.mkdir()
    (leagues / "t.yaml").write_text(
        Path("config/leagues/kyle_12_points.yaml").read_text())
    monkeypatch.setattr(api, "DB_PATH", Path(db))
    monkeypatch.setattr(api, "LEAGUE_DIR", leagues)
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    api._CACHE.clear()
    api._FA_CACHE.clear()
    return TestClient(app)


# ── input limits ────────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "audit?league=t&days=0",
    "audit?league=t&days=61",
    "schedule?league=t&start=garbage",
    "schedule?league=t&start=2026-13-01",
    "audit?league=../../etc/passwd",
    "streams?league=t&top=201",
    "streams?league=t&group=X",
    "plan?league=t&hold=1&adds=99",
    "plan?league=t&hold=1&adds=1,x",
])
def test_bad_input_is_422(db, tmp_path, monkeypatch, query):  # noqa: F811
    c = _client(db, tmp_path, monkeypatch)
    assert c.get(f"/api/inseason/{query}").status_code == 422


def test_empty_inputs_fall_back_to_defaults(db, tmp_path,  # noqa: F811
                                            monkeypatch):
    """The page sends league= and start= empty before its inputs fill in."""
    c = _client(db, tmp_path, monkeypatch)
    monkeypatch.setattr(api, "DEFAULT_LEAGUE_PATH", Path("t.yaml"))
    r = c.get("/api/inseason/schedule?league=&start=&days=3")
    assert r.status_code == 200
    assert r.json()["start"] == datetime.date.today().isoformat()


def test_draft_id_lists_are_capped():
    from src.api.routes import MAX_IDS, _parse_ids
    from fastapi import HTTPException
    assert _parse_ids("1, 2,,3") == [1, 2, 3]
    with pytest.raises(HTTPException):
        _parse_ids(",".join(["1"] * (MAX_IDS + 1)))


def test_lists_too_long_is_rejected_and_unknown_league_writes_nothing(
        db, tmp_path, monkeypatch):  # noqa: F811
    c = _client(db, tmp_path, monkeypatch)
    big = "x" * (api.MAX_LIST_CHARS + 1)
    assert c.put("/api/inseason/lists?league=t",
                 json={"roster": big, "free_agents": ""}).status_code == 422
    r = c.put("/api/inseason/lists?league=nope",
              json={"roster": "a", "free_agents": "b"})
    assert r.status_code == 404
    assert not (tmp_path / api.ROSTER_FILE).exists()


# ── cross-site writes + headers ─────────────────────────────────────────

def test_cross_site_writes_are_forbidden(db, tmp_path, monkeypatch):  # noqa: F811
    c = _client(db, tmp_path, monkeypatch)
    body = {"roster": "", "free_agents": ""}
    r = c.put("/api/inseason/lists?league=t", json=body,
              headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = c.post("/api/inseason/refresh", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    r = c.put("/api/inseason/lists?league=t", json=body,
              headers={"Origin": "null"})
    assert r.status_code == 403
    # same origin (TestClient's host is "testserver") and no-Origin clients
    # like curl from the refresh timer are allowed
    r = c.put("/api/inseason/lists?league=t", json=body,
              headers={"Origin": "http://testserver",
                       "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200
    assert c.put("/api/inseason/lists?league=t", json=body).status_code == 200
    # reads are never blocked
    assert c.get("/api/inseason/leagues",
                 headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_security_headers_on_every_response(db, tmp_path,  # noqa: F811
                                            monkeypatch):
    c = _client(db, tmp_path, monkeypatch)
    for path in ("/", "/inseason", "/api/inseason/leagues", "/no-such-page"):
        h = c.get(path).headers
        assert h["x-frame-options"] == "DENY", path
        assert h["x-content-type-options"] == "nosniff"
        assert "script-src 'self'" in h["content-security-policy"]


def test_second_refresh_while_one_runs_is_409(db, tmp_path,  # noqa: F811
                                              monkeypatch):
    c = _client(db, tmp_path, monkeypatch)
    assert api._REFRESH_LOCK.acquire(blocking=False)
    try:
        assert c.post("/api/inseason/refresh").status_code == 409
    finally:
        api._REFRESH_LOCK.release()


# ── HTTP retries ────────────────────────────────────────────────────────

def _flaky(statuses):
    seen = []

    def handler(req):
        seen.append(req.url)
        code = statuses[min(len(seen), len(statuses)) - 1]
        if code == "drop":
            raise httpx.ConnectError("reset", request=req)
        return httpx.Response(code, headers={"Retry-After": "2"} if code == 429
                              else {}, text="ok")
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_net_retries_transient_failures():
    waits = []
    client, seen = _flaky(["drop", 503, 429, 200])
    r = net.get(client, "https://x.test/a", sleep=waits.append)
    assert r.status_code == 200 and len(seen) == 4
    assert waits == [1.0, 2.0, 2.0]          # backoff, backoff, Retry-After


def test_net_does_not_retry_client_errors():
    client, seen = _flaky([404, 200])
    with pytest.raises(httpx.HTTPStatusError):
        net.get(client, "https://x.test/a", sleep=lambda s: None)
    assert len(seen) == 1


def test_net_gives_up_after_attempts():
    client, seen = _flaky([503])
    with pytest.raises(httpx.HTTPStatusError):
        net.get(client, "https://x.test/a", attempts=3, sleep=lambda s: None)
    assert len(seen) == 3


# ── caches ──────────────────────────────────────────────────────────────

def test_db_stamp_sees_wal_commits_but_not_an_idle_connection(tmp_path):
    path = tmp_path / "x.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (v INT)")
    conn.commit()
    conn.close()
    before = cache.db_stamp(path)
    reader = sqlite3.connect(path)          # opening creates an empty -wal
    reader.execute("SELECT * FROM t").fetchall()
    assert cache.db_stamp(path) == before
    writer = sqlite3.connect(path)
    writer.execute("INSERT INTO t VALUES (1)")
    writer.commit()                          # lands in -wal, not x.db
    assert cache.db_stamp(path) != before
    writer.close()
    reader.close()


def test_memo_recomputes_only_on_new_key():
    calls = []
    m = cache.Memo(maxsize=2)
    for key in ("a", "a", "b", "a", "c", "b"):
        m.get(key, lambda k=key: calls.append(k) or k.upper())
    # "b" was evicted when "c" arrived (LRU: a was used more recently)
    assert calls == ["a", "b", "c", "b"]


def test_memo_builds_once_under_concurrency():
    calls = []
    gate = threading.Event()
    m = cache.Memo()

    def slow():
        gate.wait(1)
        calls.append(1)
        return 42

    ts = [threading.Thread(target=m.get, args=("k", slow)) for _ in range(4)]
    for t in ts:
        t.start()
    gate.set()
    for t in ts:
        t.join()
    assert calls == [1]


def test_inseason_context_rebuilds_when_config_changes(db, tmp_path,  # noqa: F811
                                                        monkeypatch):
    _client(db, tmp_path, monkeypatch)
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "a.yaml").write_text("x: 1\n")
    monkeypatch.setattr(api, "config_stamp", lambda: cache.config_stamp(cfg_dir))
    a = api._ctx("t")
    assert api._ctx("t") is a
    st = os.stat(cfg_dir / "a.yaml")
    (cfg_dir / "a.yaml").write_text("x: 2\n")
    os.utime(cfg_dir / "a.yaml", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert api._ctx("t") is not a


# ── schedule index ──────────────────────────────────────────────────────

def test_schedule_index_matches_a_full_scan():
    games = [("2026-11-01", "AAA", "BBB"), ("2026-11-01", "CCC", "DDD"),
             ("2026-11-02", "BBB", "CCC"), ("2026-11-04", "AAA", "DDD"),
             ("2026-11-04", "BBB", "AAA")]
    ctx = Context(None, {}, 20262027, "2026-10-31", {}, {}, None, games)

    def scan_team(team, s, e):
        return [(d, a if h == team else h) for d, h, a in games
                if s <= d <= e and team in (h, a)]

    for team in ("AAA", "BBB", "CCC", "DDD", "ZZZ"):
        for s, e in (("2026-11-01", "2026-11-04"), ("2026-11-02", "2026-11-03"),
                     ("2026-11-05", "2026-11-09")):
            assert ctx.team_games(team, s, e) == scan_team(team, s, e)
    assert [ctx.games_on(d) for d in ("2026-11-01", "2026-11-03")] == [2, 0]
    assert ctx.avg_team_games("2026-11-01", "2026-11-02") == 2 * 3 / 4
    assert ctx.season_end() == "2026-11-04"
    ctx.games = games[:2]                    # reassigning rebuilds the index
    assert ctx.season_end() == "2026-11-01"
