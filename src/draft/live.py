"""Live draft sync: read the picks made so far from a Yahoo draft-results
page and match them to the board's players.

The browser extension (extension/) runs in your signed-in Yahoo draft
room, fetches the league's draft-results page every few seconds (your own
session, so private leagues work too) and posts the raw HTML here. All the
parsing lives on this side so there is one parser, tested with pytest, and
a Yahoo page change is fixed by updating this file -- not by reinstalling
the extension. A public league can also be pulled by the server itself
(``fetch_public``) without the extension.

Parsing is deliberately structural rather than tied to Yahoo's CSS class
names: every table row that holds a player link (``/players/<id>``, or the
``name F-link`` anchor Yahoo uses on its league pages) is a pick, the
fantasy team is the row's ``/hockey/<league>/<team>`` link or, as on
Yahoo's results page, the name in the row's last cell (matched to an id
with the league page's team links: ``resolve_teams``), and the NHL team /
positions come from the "(TOR - C)" tag. Picks are kept in page order:
Yahoo lists them round by round, and its pick numbers restart each round.
"""
from __future__ import annotations

import html as htmllib
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

_ROW = re.compile(r"<tr\b.*?</tr>", re.DOTALL | re.IGNORECASE)
_CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_PLAYER_LINK = re.compile(
    r'<a\b[^>]*href="[^"]*/players/(\d+)[^"]*"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_NAME_LINK = re.compile(
    r'<a\b[^>]*class="[^"]*\bname\b[^"]*"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_TEAM_LINK = re.compile(
    r'<a\b[^>]*href="[^"]*/hockey/(\d+)/(\d+)(?:[/?#"])[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE)
_TEAM_POS = re.compile(r">\s*\(?([A-Za-z]{2,4})\s+-\s+([A-Za-z,+/ ]+?)\)?\s*<")
_TAG = re.compile(r"<[^>]+>")
YAHOO_TO_NHL = {"TB": "TBL", "SJ": "SJS", "LA": "LAK", "NJ": "NJD",
                "UTAH": "UTA"}


@dataclass
class Pick:
    pick: int                 # 1-based order on the page
    name: str
    team_id: int | None       # fantasy team (Yahoo team id), if linked
    team_name: str | None
    nhl_team: str | None
    positions: tuple[str, ...]
    yahoo_id: int | None
    player_id: int | None = None   # board player id once matched

    def to_json(self) -> dict:
        d = asdict(self)
        d["positions"] = list(self.positions)
        return d


def _text(fragment: str) -> str:
    return " ".join(htmllib.unescape(_TAG.sub(" ", fragment)).split())


def parse_draft_results(page: str) -> list[Pick]:
    """Every pick on a Yahoo draft-results page, in page order."""
    picks: list[Pick] = []
    seen: set = set()
    for row in _ROW.findall(page):
        name = yid = None
        for m in _PLAYER_LINK.finditer(row):
            if _text(m.group(2)):
                yid, name = int(m.group(1)), _text(m.group(2))
                break
        if name is None:
            m = _NAME_LINK.search(row)
            if not m or not _text(m.group(1)):
                continue
            name = _text(m.group(1))
        key = yid or name.lower()
        if key in seen:
            continue
        seen.add(key)
        team = _TEAM_LINK.search(row)
        tp = _TEAM_POS.search(row)
        nhl = tp.group(1).upper() if tp else None
        team_name = _text(team.group(3)) or None if team else None
        if team_name is None:
            # Yahoo's results page: the drafting team is the last cell's
            # text (no link), after the player cell
            cells = _CELL.findall(row)
            if len(cells) >= 3 and "<a" not in cells[-1].lower():
                team_name = _text(cells[-1]) or None
        picks.append(Pick(
            pick=len(picks) + 1, name=name,
            team_id=int(team.group(2)) if team else None,
            team_name=team_name,
            nhl_team=YAHOO_TO_NHL.get(nhl, nhl) if nhl else None,
            positions=tuple(p.strip() for p in tp.group(2).split(",")
                            if p.strip()) if tp else (),
            yahoo_id=yid))
    return picks


# ── matching to board players ─────────────────────────────────────────

def fold(s: str) -> str:
    """Accent/case/punctuation-insensitive name key."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z ]", " ", s).split())


def _family(pos: str | None) -> str:
    return {"D": "D", "G": "G"}.get((pos or "").upper(), "F")


@dataclass(frozen=True)
class Roster:
    """Board players as (id, name, position, NHL team)."""
    rows: tuple[tuple[int, str, str, str | None], ...]

    @classmethod
    def from_db(cls, db_path: str | Path) -> Roster:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT player_id, full_name, position, current_team_abbrev"
                " FROM players WHERE full_name IS NOT NULL").fetchall()
        finally:
            conn.close()
        return cls(tuple(rows))


def _narrow(hits: list, nhl_team: str | None, fam: set[str]) -> list:
    """Several players with a name: the one on the pick's NHL team, then
    the right position family; unchanged when a filter would empty it."""
    if nhl_team and len(hits) > 1:
        hits = [h for h in hits if h[3] == nhl_team] or hits
    if fam and len(hits) > 1:
        hits = [h for h in hits if _family(h[2]) in fam] or hits
    return hits


def match_picks(picks: list[Pick], roster: Roster) -> list[Pick]:
    """Fill ``player_id`` on each pick (None when nothing matches).

    Exact folded name first; several players with that name -> the one on
    the pick's NHL team, then the right position family (D / G / F). No
    exact hit -> same last name and first initial, narrowed the same way,
    accepted only when one player is left ("Matt Boldy" vs "Matthew")."""
    exact: dict[str, list] = {}
    loose: dict[tuple[str, str], list] = {}
    for row in roster.rows:
        f = fold(row[1])
        exact.setdefault(f, []).append(row)
        parts = f.split()
        if parts:
            loose.setdefault((parts[-1], parts[0][:1]), []).append(row)
    for p in picks:
        f = fold(p.name)
        fam = ({_family(x) for x in p.positions} if p.positions else set())
        hits = _narrow(exact.get(f, []), p.nhl_team, fam)
        if not hits:
            parts = f.split()
            cand = (_narrow(loose.get((parts[-1], parts[0][:1]), []), p.nhl_team, fam)
                    if parts else [])
            if p.nhl_team:
                cand = [h for h in cand if h[3] == p.nhl_team]
            hits = cand if len(cand) == 1 else []
        p.player_id = hits[0][0] if hits else None
    return picks


_TABLE = re.compile(r"<table\b.*?</table>", re.DOTALL | re.IGNORECASE)


def first_round_teams(page: str) -> dict[int, str]:
    """{draft slot: team name} from the first round of a results page,
    empty picks included. In a Yahoo mock draft room the slot IS the team
    id (the draft client lives at /draftclient/hockey/<room>/<slot>)."""
    m = _TABLE.search(page)
    out: dict[int, str] = {}
    for row in _ROW.findall(m.group(0) if m else ""):
        cells = _CELL.findall(row)
        if len(cells) >= 3:
            name = _text(cells[-1])
            if name:
                out[len(out) + 1] = name
    return out


def resolve_teams(picks: list[Pick], teams: dict[int, str]) -> list[Pick]:
    """Fill ``team_id`` from the team name (Yahoo's results page names the
    drafting team without linking it; the league page links every team)."""
    by_name = {fold(n): tid for tid, n in teams.items()}
    for p in picks:
        if p.team_id is None and p.team_name:
            p.team_id = by_name.get(fold(p.team_name))
    return picks


def board_log(picks: list[Pick], my_team_id: int | None) -> list[dict]:
    """Matched picks as board log entries ({player_id, dest}), in order."""
    out, seen = [], set()
    for p in picks:
        if p.player_id is None or p.player_id in seen:
            continue
        seen.add(p.player_id)
        mine = my_team_id is not None and p.team_id == my_team_id
        out.append({"player_id": p.player_id, "dest": "me" if mine else "taken",
                    "pick": p.pick})
    return out
