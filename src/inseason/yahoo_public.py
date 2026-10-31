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
# injury tag next to the name, e.g. <span ... F-injury ...><span ...>IR-LT</span>
_INJURY = re.compile(r'F-injury[^"]*"[^>]*>\s*<span[^>]*>([^<]+)</span>')
# roster-status cell on player lists: "FA" or "W (Nov 2)"
_WAIVER = re.compile(r'>\s*W \(([A-Z][a-z]{2}) (\d{1,2})\)\s*<')
# injury codes that mean "not playing": don't stream / add them
OUT_CODES = {"O", "IR", "IR-LT", "IR-NR", "NA", "SUSP", "IR+"}
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
     "Nov", "Dec"], start=1)}


def _waiver_date(mon: str, day: str, today: datetime.date | None = None) -> str:
    """'Nov', '2' -> ISO date in the current hockey season."""
    today = today or datetime.date.today()
    d = datetime.date(today.year, _MONTHS[mon], int(day))
    if (d - today).days < -180:          # Dec list read in Jan, etc.
        d = d.replace(year=today.year + 1)
    elif (d - today).days > 180:
        d = d.replace(year=today.year - 1)
    return d.isoformat()


@dataclass(frozen=True)
class YPlayer:
    name: str
    team: str          # NHL abbreviation
    positions: tuple[str, ...]
    slot: str | None = None   # roster lineup slot (C, BN, IR+ ...)
    injury: str | None = None  # Yahoo tag: DTD, O, IR-LT ...
    waiver_until: str | None = None  # ISO date he clears waivers

    @property
    def inactive(self) -> bool:
        return self.slot in INACTIVE_SLOTS

    @property
    def out(self) -> bool:
        return (self.injury or "").upper() in OUT_CODES

    def line(self) -> str:
        """List-file line. 4th field: IR (your IR slot), OUT (injured free
        agent), or W <date> (on waivers until then). DTD is a comment."""
        base = f"{self.name} | {self.team} | {','.join(self.positions)}"
        if self.inactive:
            base += " | IR"
        elif self.out:
            base += " | OUT"
        elif self.waiver_until:
            base += f" | W {self.waiver_until}"
        if self.injury and not self.inactive:
            base += f"  # {self.injury}"
        return base


def parse_rows(page: str, today: datetime.date | None = None) -> list[YPlayer]:
    """Every player row in an HTML fragment."""
    out = []
    for row in _ROW.findall(page):
        name, tp = _NAME.search(row), _TEAM_POS.search(row)
        if not name or not tp:
            continue
        team = tp.group(1).upper()
        slot, inj, w = _SLOT.search(row), _INJURY.search(row), _WAIVER.search(row)
        out.append(YPlayer(
            html.unescape(name.group(1)).strip(),
            YAHOO_TO_NHL.get(team, team),
            tuple(p for p in tp.group(2).split(",") if p),
            slot.group(1) if slot else None,
            inj.group(1).strip() if inj else None,
            _waiver_date(w.group(1), w.group(2), today) if w else None))
    return out


def parse_roster(page: str, include_goalies: bool = False,
                 include_inactive: bool = False) -> list[YPlayer]:
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
    return [p for p in players if include_inactive or not p.inactive]


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
                    _get(client, f"{BASE}/{league_id}/{tid}"), include_goalies=True,
                    include_inactive=True)}
                for tid, name in sorted(teams.items())}
    finally:
        if own:
            client.close()


def _get(client: httpx.Client, url: str) -> str:
    r = client.get(url)
    r.raise_for_status()
    return r.text


def fetch_lists(league_id: int, team_id: int, fa_pages: int = 6,
                client: httpx.Client | None = None,
                ) -> tuple[list[YPlayer], list[YPlayer]]:
    """(your roster skaters, available skaters).

    Available = free agents AND waiver players (status=A), the union of
    Yahoo's top ``fa_pages`` pages by current-season rank (AR) and by
    preseason rank (OR) -- current rank alone buries slow starters (Mike
    Matheson was 234th) that the blended model may still like.
    """
    own = client is None
    client = client or httpx.Client(headers=HEADERS, timeout=30,
                                    follow_redirects=True)
    try:
        # IR players are kept (marked "| IR") so they're protected, not lost
        roster = parse_roster(_get(client, f"{BASE}/{league_id}/{team_id}"),
                              include_inactive=True)
        fas: list[YPlayer] = []
        seen: set[str] = set()
        for sort in ("AR", "OR"):
            for page in range(fa_pages):
                rows = parse_rows(_get(client, (
                    f"{BASE}/{league_id}/players?status=A&pos=P&sort={sort}"
                    f"&sdir=1&count={page * PAGE_SIZE}")))
                new = [p for p in rows if (p.name, p.team) not in seen]
                seen.update((p.name, p.team) for p in new)
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
    return (f"# {title}, from Yahoo {stamp} (Name | TEAM | Yahoo positions"
            f" | IR / OUT / W <clears>)\n"
            + "".join(p.line() + "\n" for p in players))
