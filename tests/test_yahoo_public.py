"""Yahoo public-page parser, on row HTML captured from the real league pages
(10/31/2026; images and long attributes trimmed), plus the refresh endpoint."""
import httpx

from src.inseason.yahoo_public import YPlayer, fetch_lists, parse_roster, parse_rows, render


def _row(name, team_pos, slot=None):
    pos = (f'<td class="Alt Ta-c pos Bdrend headcol"><div><span class="pos-label '
           f'Miwpx-40 Block Nowrap" data-pos="{slot}" style="width: 42px;">{slot}'
           f'</span></div></td>') if slot else ""
    return (f'<tr class="First">{pos}<td style="width: 270px;" class="Ta-start player '
            f'Bdrend"><div class="ysf-player-name Nowrap Relative Lh-xs"> <a class="Nowrap '
            f'name F-link playernote" href="https://sports.yahoo.com/nhl/players/6370" '
            f'target="_blank" data-ys-playerid="6370" id="playernote-6370" '
            f'title="{name}">{name}</a><span class="player-status D-ib Pstart-sm Nowrap">'
            f'</span> <span class="D-b"><span class="Fz-xxs">{team_pos}</span> </span>'
            f'</div></td><td class="Bdrend"><div>STL</div></td><td><div>FA</div></td></tr>')


TEAM_PAGE = ('<table id="statTable0"><tbody>'
             + _row("Sam Bennett", "FLA - C", "C")
             + _row("Jake Sanderson", "OTT - D", "D")
             + _row("Will Cuylle", "NYR - LW,RW", "BN")
             + _row("Aleksander Barkov", "FLA - C", "IR+")
             + _row("Brandt Clarke", "LA - D", "BN")
             + '</tbody></table><table id="statTable1"><tbody>'
             + _row("Jake Oettinger", "DAL - G", "G")
             + "</tbody></table>")


def test_parse_roster_skips_ir_and_goalies_and_maps_teams():
    r = parse_roster(TEAM_PAGE)
    assert [p.name for p in r] == ["Sam Bennett", "Jake Sanderson", "Will Cuylle",
                                   "Brandt Clarke"]
    assert r[2] == YPlayer("Will Cuylle", "NYR", ("LW", "RW"), "BN")
    assert r[3].team == "LAK"          # Yahoo LA -> NHL LAK


def test_parse_rows_free_agent_page_and_entities():
    page = _row("Viktor Arvidsson", "DET - LW,RW") + _row("Jean-Gabriel Pageau", "NYI - C") \
        + _row("Mikey Anderson", "LA - D").replace("Mikey Anderson", "Mikey O&#39;Anderson")
    got = parse_rows(page)
    assert [p.line() for p in got] == ["Viktor Arvidsson | DET | LW,RW",
                                       "Jean-Gabriel Pageau | NYI | C",
                                       "Mikey O'Anderson | LAK | D"]


def test_render_is_the_list_file_format():
    text = render([YPlayer("Sam Bennett", "FLA", ("C",))], "My roster")
    assert text.startswith("# My roster, from Yahoo ") and text.endswith("Sam Bennett | FLA | C\n")


def test_fetch_lists_pages_until_short_page():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if req.url.path.endswith("/19725/5"):
            return httpx.Response(200, text=TEAM_PAGE)
        assert req.url.params["status"] == "A"           # free agents + waivers
        start = int(req.url.params["count"])
        n = 25 if start == 0 else 3
        # the preseason-rank sort (OR) finds 2 extra players beyond the AR list
        extra = [_row(f"Slow Starter {i}", "MTL - D") for i in range(2)] \
            if req.url.params["sort"] == "OR" and start == 0 else []
        return httpx.Response(200, text="".join(
            _row(f"Free Agent {start + i}", "TB - C") for i in range(n)) + "".join(extra))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    roster, fas = fetch_lists(19725, 5, fa_pages=4, client=client)
    assert len(roster) == 5                           # IR player kept (marked)
    assert [p.name for p in roster if p.inactive] == ["Aleksander Barkov"]
    # 28 by current rank + 2 only found by preseason rank, no duplicates
    assert len(fas) == 30 and fas[0].team == "TBL"
    assert {p.name for p in fas[-2:]} == {"Slow Starter 0", "Slow Starter 1"}
    assert len(calls) == 5                            # team + 2 pages x 2 sorts


def test_refresh_endpoint(monkeypatch, tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    import src.api.inseason as api
    import src.inseason.ingest as ing
    import src.inseason.yahoo_public as yp
    from src.api.main import app
    from tests.test_inseason import db as _db_fixture  # noqa: F401

    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ing, "ingest_current", lambda db: {"skater_current": 5,
                                                            "goalie_current": 2})
    monkeypatch.setattr(yp, "fetch_lists", lambda *a, **k: (
        [YPlayer("Sam Bennett", "FLA", ("C",), "C")],
        [YPlayer("Viktor Arvidsson", "DET", ("LW", "RW"))]))
    monkeypatch.setattr(yp, "fetch_league", lambda league_id: {
        5: {"name": "Mine", "players": [YPlayer("Sam Bennett", "FLA", ("C",), "C")]}})
    monkeypatch.setattr(api, "_ctx", lambda league: type("C", (), {"lines": {}})())
    monkeypatch.setattr(api, "parse_players", lambda ctx, text: ({}, []))
    r = TestClient(app).post("/api/inseason/refresh").json()
    assert r["ingest"]["skater_current"] == 5 and r["yahoo_error"] is None
    assert r["yahoo"] == {"roster": 1, "free_agents": 1, "teams": 1, "unmatched": [],
                          "unparsed_rows": 0}
    import json
    stored = json.loads((tmp_path / "league_rosters.json").read_text(encoding="utf-8"))
    assert stored["teams"]["5"]["players"][0]["name"] == "Sam Bennett"
    assert (tmp_path / "my_roster.txt").read_text().endswith("Sam Bennett | FLA | C\n")
    assert Path(tmp_path / "fa.txt").read_text().endswith("Viktor Arvidsson | DET | LW,RW\n")


def test_ir_players_kept_and_marked():
    r = parse_roster(TEAM_PAGE, include_inactive=True)
    barkov = next(p for p in r if p.name == "Aleksander Barkov")
    assert barkov.inactive and barkov.line() == "Aleksander Barkov | FLA | C | IR"
    assert not r[0].inactive and r[0].line() == "Sam Bennett | FLA | C"


def test_waiver_and_injury_tags():
    import datetime
    w = _row("Alex Ovechkin", "WSH - LW,RW").replace(
        "<td><div>FA</div></td>",
        '<td class="Alt Ta-start Nowrap Bdrend"><div style="text-overflow: ellipsis;">'
        'W (Nov 2)</div></td>')
    hurt = _row("Matt Duchene", "DAL - C").replace(
        '</a><span class="player-status',
        '</a><span class="ysf-player-status Nowrap F-injury Fz-xxs"><span class="Pstart-sm"'
        ' title="Injured Reserve – Long Term">IR-LT</span></span><span class="player-status')
    dtd = hurt.replace("Matt Duchene", "Day Today").replace("IR-LT", "DTD")
    ov, du, dt = parse_rows(w + hurt + dtd, today=datetime.date(2026, 10, 31))
    assert ov.waiver_until == "2026-11-02" and not ov.out
    assert ov.line() == "Alex Ovechkin | WSH | LW,RW | W 2026-11-02"
    assert du.out and du.line() == "Matt Duchene | DAL | C | OUT  # IR-LT"
    assert not dt.out and dt.line() == "Day Today | DAL | C  # DTD"
    # a December waiver date read in late December stays this season; one
    # read in January for "Jan 3" is next calendar year
    from src.inseason.yahoo_public import _waiver_date
    assert _waiver_date("Jan", "3", datetime.date(2026, 12, 30)) == "2027-01-03"


# ── review fixes: walls, unparseable rows, shrink guard, atomic writes ──

import pytest  # noqa: E402

from src.inseason.yahoo_public import YahooPageError  # noqa: E402


def _fa_client(pages, bad_row_on=None, wall_on=None):
    """pages[i] = player rows on page i. bad_row_on: page whose first row
    has a team/pos cell we can't parse. wall_on: page served as a consent
    redirect."""
    def handler(req):
        if req.url.host.startswith("consent."):
            return httpx.Response(200, text="<html>consent</html>")
        if "/players" not in req.url.path:
            return httpx.Response(200, text=TEAM_PAGE)
        i = int(req.url.params["count"]) // 25
        if i == wall_on:
            return httpx.Response(302, headers={
                "location": "https://consent.yahoo.com/v2/collectConsent"})
        n = pages[i] if i < len(pages) else 0
        rows = [_row(f"FA {i}-{j}", "TB - C") for j in range(n)]
        if i == bad_row_on and rows:
            rows[0] = _row("Odd Row", "??? ")
        return httpx.Response(200, text="".join(rows))
    return httpx.Client(transport=httpx.MockTransport(handler),
                        follow_redirects=True)


def test_unparseable_row_does_not_end_pagination():
    stats = {}
    _, fas = fetch_lists(19725, 5, client=_fa_client([25, 25, 25, 10], bad_row_on=1),
                         stats=stats)
    assert len(fas) == 84 and stats["unparsed"] == 1


def test_empty_first_page_raises_instead_of_empty_list():
    with pytest.raises(YahooPageError):
        fetch_lists(19725, 5, client=_fa_client([0]))


def test_consent_wall_mid_pagination_raises():
    with pytest.raises(YahooPageError, match="consent"):
        fetch_lists(19725, 5, client=_fa_client([25, 25, 25], wall_on=2))


def _refresh_env(monkeypatch, tmp_path, fas, league_fails=False):
    import src.api.inseason as api
    import src.inseason.ingest as ing
    import src.inseason.yahoo_public as yp
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ing, "ingest_current", lambda db: {})
    monkeypatch.setattr(yp, "fetch_lists", lambda *a, **k: (
        [YPlayer("Sam Bennett", "FLA", ("C",), "C")], fas))

    def league(league_id):
        if league_fails:
            raise RuntimeError("league page 503")
        return {5: {"name": "Mine", "players": []}}
    monkeypatch.setattr(yp, "fetch_league", league)
    monkeypatch.setattr(api, "_ctx", lambda league: object())
    monkeypatch.setattr(api, "parse_players", lambda ctx, text: ({}, []))
    from fastapi.testclient import TestClient

    from src.api.main import app
    return TestClient(app)


def test_refresh_refuses_shrunken_free_agent_list(monkeypatch, tmp_path):
    old = render([YPlayer(f"Old {i}", "BOS", ("C",)) for i in range(200)], "Free agents")
    (tmp_path / "fa.txt").write_text(old, encoding="utf-8")
    client = _refresh_env(monkeypatch, tmp_path,
                          [YPlayer(f"New {i}", "BOS", ("C",)) for i in range(49)])
    r = client.post("/api/inseason/refresh").json()
    assert "shrank from 200 to 49" in r["yahoo_error"]
    assert (tmp_path / "fa.txt").read_text(encoding="utf-8") == old
    assert not (tmp_path / "my_roster.txt").exists()
    r = client.post("/api/inseason/refresh?force=true").json()
    assert r["yahoo_error"] is None and r["yahoo"]["free_agents"] == 49


def test_refresh_writes_nothing_if_league_fetch_fails(monkeypatch, tmp_path):
    (tmp_path / "fa.txt").write_text("old fa\n", encoding="utf-8")
    (tmp_path / "my_roster.txt").write_text("old roster\n", encoding="utf-8")
    client = _refresh_env(monkeypatch, tmp_path,
                          [YPlayer("New", "BOS", ("C",))], league_fails=True)
    r = client.post("/api/inseason/refresh").json()
    assert "league page 503" in r["yahoo_error"]
    assert (tmp_path / "fa.txt").read_text(encoding="utf-8") == "old fa\n"
    assert (tmp_path / "my_roster.txt").read_text(encoding="utf-8") == "old roster\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_utah_four_letter_team_code_parses():
    (p,) = parse_rows(_row("Clayton Keller", "UTAH - C,RW"))
    assert p.team == "UTA" and p.positions == ("C", "RW")
