"""League standings from the public Yahoo league pages, playoff odds, and
the "Playing for" focus they imply.

Sources (no login; same pages the rest of yahoo_public reads):
  league home page   the standings table (id="standingstable"): rank, team
                     id (row link), name, W-L-T, Pct, Pts (points for)
  /settings          "Playoffs: 6 teams - Week 24, 25 and 26 (ends ...)"

Playoff odds: the regular season is every week before the first playoff
week, so weeks left = (first playoff week - 1) - weeks already played
(W + L + T). Each team's weekly score is normal around its points-for per
week, pulled toward the league average by ``k_weeks`` weeks (a 3-week hot
start isn't who they are), with a spread of ``week_sd_pct`` of the league
average. Every remaining week pairs the teams at random (the real schedule
isn't on the public pages), the higher score wins, and the top N by wins
(then points for, Yahoo's default tiebreak) make it. ``sims`` seasons, a
fixed seed so the numbers don't jump between refreshes with the same data.

Focus: odds >= ``safe_odds`` -> "playoffs" (play for the title), >=
``bubble_odds`` -> "balanced", else "make_playoffs". Rotisserie and
category leagues without W-L-T get no odds (focus stays balanced).
"""
from __future__ import annotations

import html
import random
import re

_TABLE = re.compile(r'<table[^>]*id="standingstable"[^>]*>(.*?)</table>', re.S)
_ROW = re.compile(r'<tr[^>]*data-target="/hockey/\d+/(\d+)"[^>]*>(.*?)</tr>', re.S)
_RANK = re.compile(r'Tst-rank[^"]*">.*?<span>(\d+)</span>', re.S)
_NAME = re.compile(r'<a class="Grid-u F-reset[^"]*"[^>]*>(.*?)</a>', re.S)
_WLT = re.compile(r'Tst-wlt">\s*(\d+)-(\d+)-(\d+)\s*<', re.S)
_TDS = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
_PLAYOFFS = re.compile(r'Playoffs:</td>\s*<td[^>]*>\s*<b>\s*(\d+)\s*teams\s*-\s*Weeks?\s*([\d ,and]+?)'
                       r'(?:\s*\(ends[^)]*\))?\s*</b>', re.S)

DEFAULTS = {"sims": 4000, "week_sd_pct": 0.18, "k_weeks": 3,
            "safe_odds": 0.90, "bubble_odds": 0.50}


def _text(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _num(s: str) -> float | None:
    try:
        return float(_text(s).replace(",", ""))
    except ValueError:
        return None


def parse_standings(page: str) -> list[dict]:
    """[{team_id, name, rank, w, l, t, pct, pts}] in standings order; [] when
    the page has no head-to-head standings table."""
    m = _TABLE.search(page)
    if not m:
        return []
    out = []
    for tid, row in _ROW.findall(m.group(1)):
        wlt = _WLT.search(row)
        if not wlt:
            continue
        tds = _TDS.findall(row)
        # cells: rank, team, W-L-T, Pct, Pts, waiver, moves
        rank = _RANK.search(row)
        name = _NAME.search(row)
        w, l, t = (int(x) for x in wlt.groups())
        out.append({"team_id": int(tid), "name": _text(name.group(1)) if name else f"Team {tid}",
                    "rank": int(rank.group(1)) if rank else len(out) + 1,
                    "w": w, "l": l, "t": t,
                    "pct": _num(tds[3]) if len(tds) > 3 else None,
                    "pts": _num(tds[4]) if len(tds) > 4 else None})
    return out


def parse_playoffs(page: str) -> dict | None:
    """{"teams": 6, "weeks": [24, 25, 26]} from the league settings page."""
    m = _PLAYOFFS.search(page)
    if not m:
        return None
    weeks = [int(x) for x in re.findall(r"\d+", m.group(2))]
    return {"teams": int(m.group(1)), "weeks": weeks} if weeks else None


def fetch_standings(league_id: int, client=None) -> dict:
    """{"teams": parse_standings, "playoffs": parse_playoffs, "fetched"}."""
    import datetime

    import httpx

    from .yahoo_public import BASE, HEADERS, _get
    own = client is None
    client = client or httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True)
    try:
        teams = parse_standings(_get(client, f"{BASE}/{league_id}"))
        if not teams:
            raise ValueError("no head-to-head standings on the league page")
        playoffs = parse_playoffs(_get(client, f"{BASE}/{league_id}/settings"))
        return {"teams": teams, "playoffs": playoffs,
                "fetched": datetime.datetime.now().isoformat(timespec="minutes")}
    finally:
        if own:
            client.close()


def odds(st: dict, cfg: dict | None = None) -> dict:
    """{"weeks_played", "weeks_left", "spots", "teams": {team_id: {"odds",
    "mean_seed", "per_week"}}} -- see the module docstring. Needs the
    playoff settings; without them, odds are None."""
    cfg = {**DEFAULTS, **(cfg or {})}
    teams = st.get("teams") or []
    po = st.get("playoffs")
    if not teams or not po:
        return {"teams": {}, "weeks_left": None, "spots": None, "weeks_played": None}
    played = max(t["w"] + t["l"] + t["t"] for t in teams)
    left = max(min(po["weeks"]) - 1 - played, 0)
    spots = po["teams"]
    per = {t["team_id"]: (t["pts"] or 0.0) / played if played else 0.0 for t in teams}
    lg = sum(per.values()) / len(per) if per else 0.0
    k = cfg["k_weeks"]
    mean = {tid: (p * played + lg * k) / (played + k) for tid, p in per.items()}
    sd = cfg["week_sd_pct"] * lg
    rng = random.Random(7)
    ids = [t["team_id"] for t in teams]
    made = dict.fromkeys(ids, 0)
    seeds = dict.fromkeys(ids, 0.0)
    base = {t["team_id"]: (t["w"] + 0.5 * t["t"], t["pts"] or 0.0) for t in teams}
    n = int(cfg["sims"])
    for _ in range(n):
        wins = {tid: base[tid][0] for tid in ids}
        pts = {tid: base[tid][1] for tid in ids}
        for _w in range(left):
            order = ids[:]
            rng.shuffle(order)
            score = {tid: rng.gauss(mean[tid], sd) for tid in ids}
            for tid in ids:
                pts[tid] += score[tid]
            for a, b in zip(order[::2], order[1::2]):
                wins[a if score[a] >= score[b] else b] += 1
        rank = sorted(ids, key=lambda tid: (-wins[tid], -pts[tid]))
        for i, tid in enumerate(rank):
            seeds[tid] += i + 1
            if i < spots:
                made[tid] += 1
    return {"weeks_played": played, "weeks_left": left, "spots": spots,
            "teams": {tid: {"odds": made[tid] / n, "mean_seed": seeds[tid] / n,
                            "per_week": per[tid]} for tid in ids}}


def focus_for(o: float | None, cfg: dict | None = None) -> str:
    cfg = {**DEFAULTS, **(cfg or {})}
    if o is None:
        return "balanced"
    if o >= cfg["safe_odds"]:
        return "playoffs"
    if o >= cfg["bubble_odds"]:
        return "balanced"
    return "make_playoffs"
