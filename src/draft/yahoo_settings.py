"""Read a Yahoo league's settings page into a league config.

hockey.fantasysports.yahoo.com/hockey/<id>/settings has a "Setting / Value"
table (league name, Max Teams, Scoring Type, Roster Positions, Playoffs,
...) and one table per stat group: "Forwards/Defensemen Stat Category"
and "Goaltenders Stat Category", with a Value column, in points leagues;
category leagues (and Yahoo's mock draft rooms, which are leagues too)
list theirs in two Setting rows, "... Stat Categories: Goals (G), ...".
``parse_settings`` turns that into the dict a config/leagues/*.yaml holds
(see src/valuation/league.py), plus notes on anything it couldn't map.

- scoring: "...Points" -> points with points_per_stat; Roto -> roto;
  other head-to-head -> h2h_categories with the category list.
- stats: by the abbreviation in parentheses, "Goals (G)". Yahoo's SHO is
  this app's SO; a stat the projections don't model (+/-, PIM, FW, ...)
  is dropped and listed in ``notes``. In a category league, Yahoo's
  separate HIT and BLK stay separate (HIT+BLK is only for leagues that
  combine them).
- roster: Roster Positions counted; Util -> UTIL, BN -> BENCH, IR / IR+ /
  NA slots ignored (they don't hold active players).
- windows: the playoff weeks ("Week 24, 25 and 26 (ends Sunday, Apr 4)")
  give the playoff window: it ends that Sunday and starts on the Monday of
  its first week (weeks are Monday-Sunday). The regular season runs from
  the first game (``season_start``) to the day before. playoff_weight is
  not a Yahoo setting; the default here matches the shipped configs.
"""
from __future__ import annotations

import datetime
import html as htmllib
import re

from src.inseason.standings import parse_playoffs
from src.valuation.league import VALID_STAT_KEYS, LeagueConfig

DEFAULT_PLAYOFF_WEIGHT = 0.15
_TABLE = re.compile(r"<table\b.*?</table>", re.DOTALL | re.IGNORECASE)
_TR = re.compile(r"<tr\b.*?</tr>", re.DOTALL | re.IGNORECASE)
_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_ABBR = re.compile(r"\(([^()]+)\)\s*$")
_ENDS = re.compile(r"ends\s+\w+,\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})", re.IGNORECASE)
YAHOO_STAT = {"SHO": "SO", "SV%": "SV%", "PPP": "PPP", "SHP": "SHP",
              "SOG": "SOG", "HIT": "HIT", "BLK": "BLK", "GS": "GS", "W": "W",
              "GA": "GA", "SV": "SV", "G": "G", "A": "A"}
SLOT = {"C": "C", "LW": "LW", "RW": "RW", "D": "D", "G": "G", "UTIL": "UTIL",
        "BN": "BENCH"}
MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
     "nov", "dec"), 1)}


def _text(fragment: str) -> str:
    return " ".join(htmllib.unescape(_TAG.sub(" ", fragment)).split())


def _rows(table: str) -> list[list[str]]:
    return [[_text(c) for c in _CELL.findall(tr)] for tr in _TR.findall(table)]


def settings_table(page: str) -> dict[str, str]:
    """{"League Name": "Marshall & Co.", ...} from the Setting/Value table."""
    out = {}
    for table in _TABLE.findall(page):
        for cells in _rows(table):
            if len(cells) == 2 and cells[0] and cells[0] != "Setting":
                out[cells[0].rstrip(":").strip()] = cells[1]
    return out


def stat_values(page: str) -> tuple[dict[str, float | None], list[str]]:
    """({app stat key: points value, None in category leagues}, unmapped)."""
    stats: dict[str, float | None] = {}
    unmapped: list[str] = []
    for table in _TABLE.findall(page):
        rows = _rows(table)
        if not rows or not any("Stat Category" in c for c in rows[0]):
            continue
        for cells in rows[1:]:
            if not cells or not cells[0]:
                continue
            m = _ABBR.search(cells[0])
            abbr = (m.group(1) if m else cells[0]).strip().upper()
            key = YAHOO_STAT.get(abbr)
            if key is None or key not in VALID_STAT_KEYS:
                unmapped.append(cells[0])
                continue
            val = None
            if len(cells) > 1:
                try:
                    val = float(cells[1].replace(",", ""))
                except ValueError:
                    val = None
            stats[key] = val
    # category leagues: "Forwards/Defensemen Stat Categories: Goals (G), ..."
    for label, value in settings_table(page).items():
        if not label.endswith("Stat Categories"):
            continue
        for item in re.findall(r"[^,()]+\([^()]+\)", value):
            m = _ABBR.search(item)
            key = YAHOO_STAT.get(m.group(1).strip().upper()) if m else None
            if key is None or key not in VALID_STAT_KEYS:
                unmapped.append(item.strip())
            elif key not in stats:
                stats[key] = None
    return stats, unmapped


def roster_slots(positions: str) -> tuple[dict[str, int], list[str]]:
    slots = dict.fromkeys(("C", "LW", "RW", "D", "G", "UTIL", "BENCH"), 0)
    other = []
    for p in (x.strip().upper() for x in positions.split(",")):
        if not p:
            continue
        if p in SLOT:
            slots[SLOT[p]] += 1
        elif not p.startswith("IR") and p != "NA":
            other.append(p)
    return slots, other


def playoff_window(page: str, season_end_year: int) -> tuple[str, str] | None:
    """(Monday of the first playoff week, the Sunday it ends)."""
    po = parse_playoffs(page)
    m = _ENDS.search(page)
    if not po or not m or m.group(1).lower()[:3] not in MONTHS:
        return None
    end = datetime.date(season_end_year, MONTHS[m.group(1).lower()[:3]],
                        int(m.group(2)))
    weeks = max(po["weeks"]) - min(po["weeks"]) + 1
    start = end - datetime.timedelta(days=7 * weeks - 1)
    return start.isoformat(), end.isoformat()


def parse_settings(page: str, *, name: str, season_start: str,
                   season_end_year: int, league_id: int | None = None,
                   team_id: int | None = None) -> tuple[dict, list[str], str | None]:
    """(league config dict, notes, Yahoo league name) from a settings page.

    Raises ValueError when the page has no usable scoring or roster (not a
    settings page, or a league type this app can't value)."""
    table = settings_table(page)
    notes: list[str] = []
    stype = table.get("Scoring Type", "")
    stats, unmapped = stat_values(page)
    if unmapped:
        notes.append("not modeled, left out: " + ", ".join(unmapped))
    if not stats:
        raise ValueError("no stat categories on the page")
    if "point" in stype.lower():
        scoring = "points"
        missing = [k for k, v in stats.items() if v is None]
        if missing:
            raise ValueError(f"points league without values for {missing}")
    elif "roto" in stype.lower() or "rotisserie" in stype.lower():
        scoring = "roto"
    else:
        scoring = "h2h_categories"
    slots, other = roster_slots(table.get("Roster Positions", ""))
    if other:
        notes.append("roster slots not modeled: " + ", ".join(other))
    if not sum(slots.values()):
        raise ValueError("no roster positions on the page")
    try:
        teams = int(re.sub(r"\D", "", table.get("Max Teams", "")))
    except ValueError:
        raise ValueError("no team count on the page")
    cfg: dict = {"name": name, "team_count": teams, "scoring_type": scoring,
                 "roster_slots": slots}
    if scoring == "points":
        cfg["points_per_stat"] = {k: v for k, v in stats.items()}
    else:
        cfg["categories"] = list(stats)
    win = playoff_window(page, season_end_year)
    if win:
        before = (datetime.date.fromisoformat(win[0])
                  - datetime.timedelta(days=1)).isoformat()
        cfg["regular_window"] = [season_start, before]
        cfg["playoff_window"] = list(win)
        cfg["playoff_weight"] = DEFAULT_PLAYOFF_WEIGHT
    else:
        notes.append("no playoff weeks found: valued season-long only")
    if league_id and team_id:
        cfg["yahoo"] = {"league_id": league_id, "team_id": team_id}
    LeagueConfig.model_validate(cfg)
    return cfg, notes, table.get("League Name") or None


def differences(cfg: LeagueConfig, yahoo: dict) -> list[str]:
    """How a league config differs from Yahoo's settings (empty = same)."""
    out = []
    if cfg.team_count != yahoo["team_count"]:
        out.append(f"teams {cfg.team_count} vs Yahoo {yahoo['team_count']}")
    if cfg.scoring_type != yahoo["scoring_type"]:
        out.append(f"scoring {cfg.scoring_type} vs Yahoo {yahoo['scoring_type']}")
    mine = {k: v for k, v in cfg.roster_slots.items() if v}
    theirs = {k: v for k, v in yahoo["roster_slots"].items() if v}
    if mine != theirs:
        out.append(f"roster {mine} vs Yahoo {theirs}")
    if yahoo["scoring_type"] == "points":
        a, b = cfg.points_per_stat or {}, yahoo["points_per_stat"]
        for k in sorted(set(a) | set(b)):
            if abs(a.get(k, 0.0) - b.get(k, 0.0)) > 1e-9:
                out.append(f"{k} {a.get(k, 0)} vs Yahoo {b.get(k, 0)}")
    elif set(cfg.categories or []) != set(yahoo["categories"]):
        out.append(f"categories {cfg.categories} vs Yahoo {yahoo['categories']}")
    if yahoo.get("playoff_window") and cfg.playoff_window and \
            tuple(cfg.playoff_window) != tuple(yahoo["playoff_window"]):
        out.append(f"playoffs {list(cfg.playoff_window)} vs Yahoo "
                   f"{yahoo['playoff_window']}")
    return out


_TEAM_A = re.compile(
    r'<a\b[^>]*href="[^"]*/hockey/(\d+)/(\d+)"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)


def league_teams(page: str, league_id: int) -> dict[int, str]:
    """{team id: name} from the league home page's team links."""
    out: dict[int, str] = {}
    for lid, tid, inner in _TEAM_A.findall(page):
        name = _text(inner)
        if int(lid) == league_id and name and int(tid) not in out:
            out[int(tid)] = name
    return out


_MY_TEAM = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*slk:My Team[;"]', re.IGNORECASE)


def my_team_from(page: str, league_id: int) -> int | None:
    """Your team id from a league page fetched while signed in: Yahoo's nav
    "My Team" link (``href="/hockey/<league>/<team>" ... slk:My Team``)."""
    for href in _MY_TEAM.findall(page):
        m = re.search(r"/hockey/(\d+)/(\d+)(?:$|[/?#])", htmllib.unescape(href))
        if m and int(m.group(1)) == league_id:
            return int(m.group(2))
    return None
