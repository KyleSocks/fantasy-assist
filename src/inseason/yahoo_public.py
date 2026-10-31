"""Read your roster and the free-agent list from a public Yahoo league.

Works when the league is publicly viewable (no login, no Yahoo developer
app): the team page and the player-list page are server-rendered HTML. Only
the few fields we need are pulled out of each player row -- name, Yahoo team,
eligible positions, and (roster) the lineup slot -- so cosmetic page changes
don't matter, but a structural redesign would need this parser updated.

For a private league use the Yahoo Fantasy API (OAuth) instead.
"""
from __future__ import annotations

import datetime
import html
import re
from dataclasses import dataclass

import httpx

BASE = "https://hockey.fantasysports.yahoo.com/hockey"
HEADERS = {"User-Agent": (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")}
PAGE_SIZE = 25
# Yahoo abbreviations that differ from the NHL's.
YAHOO_TO_NHL = {"TB": "TBL", "SJ": "SJS", "LA": "LAK", "NJ": "NJD"}
# Lineup slots that aren't part of the active roster.
INACTIVE_SLOTS = {"IR", "IR+", "NA"}

_ROW = re.compile(r"<tr\b.*?</tr>", re.S)
_NAME = re.compile(r'<a class="Nowrap name F-link[^"]*"[^>]*>([^<]+)</a>')
_TEAM_POS = re.compile(r'<span class="Fz-xxs">([A-Za-z]{2,3}) - ([A-Za-z,+]+)</span>')
_SLOT = re.compile(r'data-pos="([^"]+)"')


@dataclass(frozen=True)
class YPlayer:
    name: str
    team: str          # NHL abbreviation
    positions: tuple[str, ...]
    slot: str | None = None   # roster lineup slot (C, BN, IR+ ...)

    def line(self) -> str:
        return f"{self.name} | {self.team} | {','.join(self.positions)}"


def parse_rows(page: str) -> list[YPlayer]:
    """Every player row in an HTML fragment."""
    out = []
    for row in _ROW.findall(page):
        name, tp = _NAME.search(row), _TEAM_POS.search(row)
        if not name or not tp:
            continue
        team = tp.group(1).upper()
        slot = _SLOT.search(row)
        out.append(YPlayer(
            html.unescape(name.group(1)).strip(),
            YAHOO_TO_NHL.get(team, team),
            tuple(p for p in tp.group(2).split(",") if p),
            slot.group(1) if slot else None))
    return out


def parse_roster(page: str, include_goalies: bool = False) -> list[YPlayer]:
    """Players on a team page, excluding IR / IR+ / NA slots.

    Skaters come from statTable0; goalies (statTable1) only when
    ``include_goalies`` -- the streaming planner covers skater slots.
    """
    start = page.find('id="statTable0"')
    if start < 0:
        raise ValueError("team page has no roster table (statTable0); is the "
                         "league public and the team id right?")
    g = page.find('id="statTable1"', start)
    players = parse_rows(page[start:g if g > 0 else None])
    if include_goalies and g > 0:
        end = page.find("</table>", g)
        players += parse_rows(page[g:end if end > 0 else None])
    return [p for p in players if p.slot not in INACTIVE_SLOTS]


_TEAM_LINK = re.compile(
    r'href="(?:https://hockey\.fantasysports\.yahoo\.com)?/hockey/(\d+)/(\d+)"'
    r'[^>]*>([^<]{2,80})</a>')


def parse_team_links(page: str, league_id: int) -> dict[int, str]:
    """{team_id: team name} from the league home page."""
    out: dict[int, str] = {}
    for lid, tid, name in _TEAM_LINK.findall(page):
        if int(lid) == league_id and int(tid) not in out:
            out[int(tid)] = html.unescape(name).strip()
    return out


def fetch_league(league_id: int, client: httpx.Client | None = None,
                 ) -> dict[int, dict]:
    """Every team: {team_id: {"name": str, "players": [YPlayer]}} (goalies
    included, IR excluded). One request for the league page + one per team."""
    own = client is None
    client = client or httpx.Client(headers=HEADERS, timeout=30,
                                    follow_redirects=True)
    try:
        teams = parse_team_links(_get(client, f"{BASE}/{league_id}"), league_id)
        if not teams:
            raise ValueError("no teams found on the league page; is it public?")
        return {tid: {"name": name, "players": parse_roster(
                    _get(client, f"{BASE}/{league_id}/{tid}"), include_goalies=True)}
                for tid, name in sorted(teams.items())}
    finally:
        if own:
            client.close()


def _get(client: httpx.Client, url: str) -> str:
    r = client.get(url)
    r.raise_for_status()
    return r.text


def fetch_lists(league_id: int, team_id: int, fa_pages: int = 4,
                client: httpx.Client | None = None,
                ) -> tuple[list[YPlayer], list[YPlayer]]:
    """(your roster skaters, free-agent skaters ranked by Yahoo)."""
    own = client is None
    client = client or httpx.Client(headers=HEADERS, timeout=30,
                                    follow_redirects=True)
    try:
        roster = parse_roster(_get(client, f"{BASE}/{league_id}/{team_id}"))
        fas: list[YPlayer] = []
        seen: set[str] = set()
        for page in range(fa_pages):
            rows = parse_rows(_get(client, (
                f"{BASE}/{league_id}/players?status=FA&pos=P&sort=AR&sdir=1"
                f"&count={page * PAGE_SIZE}")))
            new = [p for p in rows if p.name not in seen]
            seen.update(p.name for p in new)
            fas += new
            if len(rows) < PAGE_SIZE:
                break
    finally:
        if own:
            client.close()
    if not roster:
        raise ValueError("no skaters found on the team page")
    return roster, fas


def render(players: list[YPlayer], title: str) -> str:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return (f"# {title}, from Yahoo {stamp} (Name | TEAM | Yahoo positions)\n"
            + "".join(p.line() + "\n" for p in players))
