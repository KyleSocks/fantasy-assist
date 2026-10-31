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
        start = int(req.url.params["count"])
        n = 25 if start == 0 else 3
        return httpx.Response(200, text="".join(
            _row(f"Free Agent {start + i}", "TB - C") for i in range(n)))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    roster, fas = fetch_lists(19725, 5, fa_pages=4, client=client)
    assert len(roster) == 4 and len(fas) == 28
    assert fas[0].team == "TBL" and len(calls) == 3   # team + 2 FA pages


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
    monkeypatch.setattr(api, "_ctx", lambda league: type("C", (), {"lines": {}})())
    monkeypatch.setattr(api, "parse_players", lambda ctx, text: ({}, []))
    r = TestClient(app).post("/api/inseason/refresh").json()
    assert r["ingest"]["skater_current"] == 5 and r["yahoo_error"] is None
    assert r["yahoo"] == {"roster": 1, "free_agents": 1, "unmatched": []}
    assert (tmp_path / "my_roster.txt").read_text().endswith("Sam Bennett | FLA | C\n")
    assert Path(tmp_path / "fa.txt").read_text().endswith("Viktor Arvidsson | DET | LW,RW\n")
