"""Availability: waiver dates, injury tags, list parsing (parse_list /
PlayerList), the full free-agent pull, the free-agent table, the API and the
planner respecting waiver dates.

Uses the hand-computed world from tests/test_inseason.py (points league;
C1 D1 G1). Plan values below are worked from that file's numbers:
  Ovi (AAA)  11-01 vs CCC 1.525, 11-03 vs BBB 3.694512
  Strong Center (BBB) 11-02 vs CCC 1.3275, 11-03 vs AAA 2.833317,
                      11-04 vs CCC 1.3275
"""
import datetime
import json

import httpx
import pytest

from src.inseason.roster import PlayerList, free_agent_table, parse_list, parse_players, roster_plan
from src.inseason.yahoo_public import YPlayer, _waiver_date, fetch_lists, parse_rows
from tests.test_inseason import ICFG, POINTS, PRIORS, ctx, db  # noqa: F401
from tests.test_yahoo_public import TEAM_PAGE, _row

OCT31 = datetime.date(2026, 10, 31)


# ── waiver dates ───────────────────────────────────────────────────────

@pytest.mark.parametrize("mon,day,today,expected", [
    ("Nov", "2", OCT31, "2026-11-02"),
    ("Jan", "3", datetime.date(2026, 12, 30), "2027-01-03"),   # across New Year
    ("Dec", "28", datetime.date(2027, 1, 2), "2026-12-28"),    # read in January
    ("Oct", "31", OCT31, "2026-10-31"),
])
def test_waiver_date(mon, day, today, expected):
    assert _waiver_date(mon, day, today) == expected


# ── YPlayer list lines ────────────────────────────────────────────────

@pytest.mark.parametrize("player,line", [
    (YPlayer("A B", "NYR", ("C",)), "A B | NYR | C"),
    (YPlayer("A B", "NYR", ("C", "LW"), slot="IR+", injury="IR"), "A B | NYR | C,LW | IR"),
    (YPlayer("A B", "NYR", ("D",), injury="O"), "A B | NYR | D | OUT  # O"),
    (YPlayer("A B", "NYR", ("D",), injury="IR-LT"), "A B | NYR | D | OUT  # IR-LT"),
    (YPlayer("A B", "NYR", ("RW",), injury="DTD"), "A B | NYR | RW  # DTD"),
    (YPlayer("A B", "NYR", ("RW",), waiver_until="2026-11-02"), "A B | NYR | RW | W 2026-11-02"),
    # injured AND on waivers: OUT wins (can't play either way)
    (YPlayer("A B", "NYR", ("RW",), injury="O", waiver_until="2026-11-02"),
     "A B | NYR | RW | OUT  # O"),
])
def test_yplayer_line(player, line):
    assert player.line() == line


def test_out_codes():
    assert YPlayer("x", "T", ("C",), injury="ir-lt").out
    assert not YPlayer("x", "T", ("C",), injury="DTD").out
    assert not YPlayer("x", "T", ("C",)).out


def test_parse_rows_no_status_or_injury():
    (p,) = parse_rows(_row("Plain Player", "BOS - C"), today=OCT31)
    assert p.injury is None and p.waiver_until is None and p.slot is None


# ── parse_list / PlayerList ───────────────────────────────────────────

LIST = """# header comment
Alex Ovechkin | AAA | LW,RW | W 2026-11-03
Strong Center | BBB | C          # DTD
Depth Wing | CCC | RW | OUT       # IR-LT
Dee Man | BBB | D | IR
Dee Two
Nobody Real | ZZZ | C
"""


def test_parse_list_buckets(ctx):  # noqa: F811
    pl = parse_list(ctx, LIST)
    assert set(pl.active) == {1, 2, 5}
    assert set(pl.inactive) == {3, 4}
    assert pl.available_from == {1: "2026-11-03"}
    assert pl.tags == {2: "DTD", 3: "IR-LT"}
    assert pl.missing == ["Nobody Real"]
    assert pl.active[1] == {"LW", "RW"}
    assert pl.active[5] == {"D"}                  # no positions -> NHL position
    assert pl.inactive[3] == {"RW"}               # injured keep their positions
    assert [pl.status(p) for p in (1, 2, 3, 4)] == ["W", "FA", "OUT", "IR"]


def test_parse_list_status_without_positions(ctx):  # noqa: F811
    pl = parse_list(ctx, "Alex Ovechkin | AAA | W 2026-11-03\n"
                         "Depth Wing | CCC | OUT  # O\n"
                         "Strong Center | BBB | C\n")
    assert pl.available_from == {1: "2026-11-03"} and pl.active[1] == {"LW"}
    assert set(pl.inactive) == {3} and pl.tags[3] == "O"
    assert pl.active[2] == {"C"}                 # a real position is untouched


def test_parse_players_wrapper_matches_parse_list(ctx):  # noqa: F811
    inactive, avail = set(), {}
    active, missing = parse_players(ctx, LIST, inactive, avail)
    pl = parse_list(ctx, LIST)
    assert active == pl.active and missing == pl.missing
    assert inactive == set(pl.inactive) and avail == pl.available_from


# ── free_agent_table ──────────────────────────────────────────────────

def test_free_agent_table_lists_waivers_and_injured(ctx):  # noqa: F811
    rows = free_agent_table(ctx, parse_list(ctx, LIST), "2026-11-01", 4)
    by = {r["id"]: r for r in rows}
    assert set(by) == {1, 2, 3, 4, 5}
    assert by[1]["status"] == "W" and by[1]["waiver_until"] == "2026-11-03"
    assert by[3]["status"] == "OUT" and by[3]["tag"] == "IR-LT"
    assert by[3]["flags"][0] == "injured (IR-LT)"
    # Depth Wing (1.32/g) is below F replacement (2.445): not worth an IR slot
    assert by[3]["verdict"] == "OUT"
    assert by[2]["status"] == "FA" and by[2]["tag"] == "DTD"
    blended = [r["blended"] for r in rows]
    assert blended == sorted(blended, reverse=True)
    # legacy call (dict of active players) still works, no injured rows
    legacy = free_agent_table(ctx, {2: {"C"}}, "2026-11-01", 4, {2: "2026-11-02"})
    assert [(r["id"], r["status"]) for r in legacy] == [(2, "W")]


def test_free_agent_table_skips_unknown_ids(ctx):  # noqa: F811
    assert free_agent_table(ctx, PlayerList(active={999: {"C"}}), "2026-11-01", 4) == []


# ── fetch_lists: every page ──────────────────────────────────────────

def _client(pages):
    calls = []

    def handler(req):
        calls.append(req.url)
        if "/players" not in req.url.path:
            return httpx.Response(200, text=TEAM_PAGE)
        assert req.url.params["status"] == "A"
        i = int(req.url.params["count"]) // 25
        n = pages[i] if i < len(pages) else 0
        return httpx.Response(200, text="".join(
            _row(f"P{i}-{k}", "BOS - C") for k in range(n)))
    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_fetch_lists_all_pages_until_short_page():
    client, calls = _client([25, 25, 7])
    _, fas = fetch_lists(19725, 5, fa_pages=0, client=client)
    assert len(fas) == 57
    assert [c.params["sort"] for c in calls[1:]] == ["AR", "AR", "AR"]


def test_fetch_lists_all_pages_safety_stop(monkeypatch):
    import src.inseason.yahoo_public as yp
    monkeypatch.setattr(yp, "MAX_PAGES", 3)
    client, calls = _client([25] * 10)
    _, fas = fetch_lists(19725, 5, fa_pages=0, client=client)
    assert len(fas) == 75 and len(calls) == 4         # team page + 3


# ── planner respects waiver dates ─────────────────────────────────────

def test_roster_plan_waits_for_waiver_clear(ctx):  # noqa: F811
    roster, cands = {}, {2: {"C"}}
    free = roster_plan(ctx, {1: {"C"}, **roster}, cands, 1, "2026-11-01", 4, [2])
    # Ovi 11-01, Strong Center from 11-02: 1.525 + 1.3275 + 2.833317 + 1.3275
    assert free["plan_value"] == pytest.approx(7.013317, abs=1e-4)
    assert [s["date"] for s in free["steps"]] == ["2026-11-02"]
    waits = roster_plan(ctx, {1: {"C"}}, cands, 1, "2026-11-01", 4, [2],
                        available_from={2: "2026-11-04"})
    # can't add him before 11-04: Ovi 11-01 + 11-03, then him 11-04
    assert waits["plan_value"] == pytest.approx(1.525 + 3.694512 + 1.3275, abs=1e-4)
    assert [s["date"] for s in waits["steps"]] == ["2026-11-04"]


# ── API: free agents + streams availability ───────────────────────────

def test_api_free_agents_and_streams(db, tmp_path, monkeypatch):  # noqa: F811
    from pathlib import Path

    from fastapi.testclient import TestClient

    import src.api.inseason as api
    from src.api.main import app
    leagues = tmp_path / "leagues"
    leagues.mkdir()
    (leagues / "t.yaml").write_text(Path("config/leagues/kyle_12_points.yaml").read_text())
    monkeypatch.setattr(api, "DB_PATH", Path(db))
    monkeypatch.setattr(api, "LEAGUE_DIR", leagues)
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    api._CACHE.clear()
    (tmp_path / "fa.txt").write_text(LIST, encoding="utf-8")
    (tmp_path / "league_rosters.json").write_text(json.dumps({"my_team_id": 1, "teams": {
        "1": {"name": "x", "players": [{"name": "Dee Man", "team": "BBB",
                                        "positions": ["D"], "inactive": True}]}}}))
    c = TestClient(app)
    f = c.get("/api/inseason/free-agents?league=t&start=2026-11-01&days=4").json()
    assert f["counts"] == {"FA": 2, "W": 1, "OUT": 1}   # Dee Man is IR (not OUT)
    assert f["unmatched_count"] == 1
    assert {r["name"] for r in f["players"] if r["status"] == "OUT"} == {"Depth Wing"}

    def stream_ids(date):
        return {p["player_id"] for p in c.get(
            f"/api/inseason/streams?league=t&date={date}").json()["players"]}
    # 11-02: Depth Wing OUT and Ovi still on waivers (clears 11-03) -> neither;
    # Dee Man rostered in the league -> no
    assert stream_ids("2026-11-02").isdisjoint({1, 3, 4})
    # 11-03: Ovi has cleared waivers and AAA plays -> streamable
    assert 1 in stream_ids("2026-11-03")


def test_injured_star_is_an_ir_stash(ctx):  # noqa: F811
    # Strong Center (2.445/g) injured; replacement F is the 2nd-best F, so
    # make him the best F by removing nobody -- Ovi (2.825) is above repl.
    rows = free_agent_table(ctx, parse_list(ctx, "Alex Ovechkin | AAA | LW | OUT  # O\n"),
                            "2026-11-01", 4)
    assert rows[0]["status"] == "OUT" and rows[0]["verdict"] == "IR STASH"
