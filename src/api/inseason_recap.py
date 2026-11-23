"""Weekly recap for your league, on the /api/inseason router:

GET /api/inseason/recap?league=

Built from data every Refresh already stores, over two pools: players
rostered in your league (with the team that has them) and your league's
free agents.

  top_week       most fantasy points over the last 7 days
  heating_up     last 14 days per game well above his blended rate
  cooling_off    ... well below (4+ games in the window)
  role_changes   promoted / demoted (line or PP unit)
  luck           running hot / due to bounce back
  bandwagon      biggest 7-day rises in % rostered (Yahoo-wide) among your
                 free agents, with whether the numbers back it up
  next_week      games per team Mon-Sun, the busiest and lightest, and the
                 best free-agent skater / goalie streams for that week
"""
from __future__ import annotations

import datetime

from fastapi import HTTPException

from src.api import inseason as ins
from src.api.inseason import LeagueName
from src.inseason import leagues as store
from src.inseason.evaluate import game_value
from src.inseason.roster import _rostered, luck_json, parse_list, role_json
from src.inseason.trend import value_of

TOP = 10
HEAT_MIN_GAMES = 4
HEAT_PCT = 0.20


def _owners(ctx, snap: dict | None) -> dict[int, str]:
    """{player id: fantasy team name} for every rostered player."""
    out: dict[int, str] = {}
    for t in (snap or {}).get("teams", {}).values():
        text = "\n".join(f"{p['name']} | {p.get('team', '')} | {','.join(p.get('positions') or [])}"
                         for p in t.get("players") or [])
        pl = parse_list(ctx, text)
        for pid in list(pl.active) + list(pl.inactive):
            out[pid] = t.get("name") or "?"
    return out


def _span(windows: dict) -> dict | None:
    w = next((w for w in windows.values() if w), None)
    return {"start": w["start"], "end": w["end"]} if w else None


def next_week(today: datetime.date) -> list[str]:
    mon = today + datetime.timedelta(days=(7 - today.weekday()) % 7 or 7)
    return [(mon + datetime.timedelta(days=i)).isoformat() for i in range(7)]


@ins.router.get("/recap")
def get_recap(league: LeagueName = None) -> dict:
    ctx = ins._ctx(league)
    folder = ins._dir(ctx)
    snap = store.read_json(folder / ins.LEAGUE_FILE)
    if snap is None:
        raise HTTPException(400, "no league rosters yet: press Refresh data")
    owners = _owners(ctx, snap)
    mine = ((snap.get("teams") or {}).get(str(snap.get("my_team_id"))) or {}).get("name")
    pl = parse_list(ctx, ins._read(ins.FA_FILE, ctx))
    fas = {p for p in pl.active if p not in owners}
    pool = set(owners) | fas

    def who(pid, **extra):
        ln = ctx.lines[pid]
        return {"id": pid, "name": ln.name, "team": ln.team, "pos": ln.position,
                "owner": owners.get(pid), "mine": owners.get(pid) == mine and mine is not None,
                "per_game": ctx.scorer.per_game.get(pid), **extra}

    def win(pid, days):
        rec = (ctx.lines[pid].extra.get("windows") or {}).get(days)
        if not rec or not rec["games"]:
            return None
        now, base = value_of(ctx, ctx.lines[pid], rec)
        return {"games": rec["games"], "per_game": now, "base": base,
                "total": now * rec["games"], "start": rec["start"], "end": rec["end"]}

    week = {p: win(p, 7) for p in pool if p in ctx.lines}
    top = sorted((p for p, w in week.items() if w), key=lambda p: -week[p]["total"])[:TOP]
    two = {p: win(p, 14) for p in pool if p in ctx.lines}
    heat = [(p, w) for p, w in two.items() if w and w["games"] >= HEAT_MIN_GAMES and w["base"] > 0]
    up = sorted((x for x in heat if x[1]["per_game"] >= x[1]["base"] * (1 + HEAT_PCT)),
                key=lambda x: -(x[1]["per_game"] - x[1]["base"]))[:TOP]
    down = sorted((x for x in heat if x[1]["per_game"] <= x[1]["base"] * (1 - HEAT_PCT)),
                  key=lambda x: x[1]["per_game"] - x[1]["base"])[:TOP]

    roles = []
    for p in pool:
        r = role_json(ctx, p) if p in ctx.lines else None
        if r and r.get("change"):
            roles.append(who(p, role=r))
    roles.sort(key=lambda x: (x["role"]["change"] != "up", x["owner"] is not None,
                              -(x["per_game"] or 0)))
    lucky = []
    for p in pool:
        lk = luck_json(ctx, p) if p in ctx.lines else None
        if lk and lk.get("status"):
            lucky.append(who(p, luck=lk))
    lucky.sort(key=lambda x: -abs(x["luck"]["z"]))

    band = []
    for p in fas:
        r = _rostered(ctx, p)
        if r and r.get("d7") and r["d7"] > 0:
            band.append((p, r))
    band.sort(key=lambda x: -x[1]["d7"])
    bandwagon = []
    for p, r in band[:TOP]:
        g = "G" if ctx.lines[p].is_goalie else "D" if ctx.lines[p].position == "D" else "F"
        bar = ctx.scorer.replacement.get(g)
        backed = bar is not None and (ctx.scorer.per_game.get(p) or 0) >= bar
        bandwagon.append(who(p, rostered=r, backed=backed, bar=bar))

    today = datetime.date.fromisoformat(ins._today())
    nxt = next_week(today)
    teams: dict[str, int] = {}
    for d, h, a in ctx.games:
        if nxt[0] <= d <= nxt[-1]:
            teams[h] = teams.get(h, 0) + 1
            teams[a] = teams.get(a, 0) + 1
    nights = {d: ctx.games_on(d) for d in nxt}

    def week_value(p):
        ln = ctx.lines[p]
        return sum(game_value(ctx, ln, opp, d) for d, opp in ctx.team_games(ln.team, nxt[0], nxt[-1]))
    stream_sk = sorted((p for p in fas if not ctx.lines[p].is_goalie
                        and teams.get(ctx.lines[p].team, 0) >= 4), key=lambda p: -week_value(p))[:TOP]
    stream_g = sorted((p for p in fas if ctx.lines[p].is_goalie and ctx.lines[p].start_share > 0.15),
                      key=lambda p: -week_value(p))[:5]

    def tag(p, w):
        return who(p, window=w, role=role_json(ctx, p), luck=luck_json(ctx, p))
    return {
        "as_of": ctx.as_of, "unit": "fpts" if ctx.scorer.points else "z", "my_team": mine,
        "week_window": _span(week),
        "top_week": [tag(p, week[p]) for p in top],
        "heating_up": [tag(p, w) for p, w in up],
        "cooling_off": [tag(p, w) for p, w in down],
        "role_changes": roles[:2 * TOP],
        "luck": lucky[:2 * TOP],
        "bandwagon": bandwagon,
        "next_week": {
            "dates": nxt, "nights": nights,
            "teams": sorted(({"team": t, "games": n} for t, n in teams.items()),
                            key=lambda x: (-x["games"], x["team"])),
            "skater_streams": [who(p, games=teams.get(ctx.lines[p].team, 0), value=week_value(p),
                                   role=role_json(ctx, p)) for p in stream_sk],
            "goalie_streams": [who(p, games=teams.get(ctx.lines[p].team, 0), value=week_value(p),
                                   start_share=ctx.lines[p].start_share) for p in stream_g],
        },
    }


@ins.router.get("/takes")
def get_takes(league: LeagueName = None, start: str | None = None) -> dict:
    """Buy low / sell high / trade targets / waiver adds / stashes for your
    league (src/inseason/takes.py)."""
    from src.inseason.takes import build
    from src.inseason.teams import Market

    ctx = ins._ctx(league)
    folder = ins._dir(ctx)
    snap = store.read_json(folder / ins.LEAGUE_FILE)
    if snap is None:
        raise HTTPException(400, "no league rosters yet: press Refresh data")
    from src.inseason.teams import trade_roster
    owners: dict[int, int] = {}
    names: dict[int, str] = {}
    out_until: dict[int, str] = {}
    day = ins._day(start)
    for tid, t in (snap.get("teams") or {}).items():
        names[int(tid)] = t.get("name") or "?"
        roster, out = trade_roster(ctx, t.get("players") or [], day)
        out_until.update(out)
        for pid in roster:
            owners[pid] = int(tid)
    my_team = snap.get("my_team_id")
    my_team = int(my_team) if my_team is not None else None
    fa = parse_list(ctx, ins._read(ins.FA_FILE, ctx))
    fas = {p for p in fa.active if p not in owners}
    injured = {p for p in fa.inactive if p not in owners}   # free agents listed OUT / IR
    mdata = store.read_json(folder / ins.MARKET_FILE)
    market = Market(ctx, (mdata or {}).get("players") or [])
    out = build(ctx, owners=owners, team_names=names, my_team=my_team, fas=fas,
                injured_fas=injured, market=market, rostered=lambda p: _rostered(ctx, p),
                start=day, out_until=out_until)
    return {"as_of": ctx.as_of, "my_team": names.get(my_team), "my_team_id": my_team,
            "has_market": bool(mdata), **out}



@ins.router.get("/standings")
def get_standings(league: LeagueName = None) -> dict:
    """Standings with playoff odds and the focus "auto" uses."""
    ctx = ins._ctx(league)
    st = ctx.__dict__.get("_standings")
    return {"standings": st, "trade_focus": ctx.icfg.get("trade_focus", "balanced"),
            "focus": ctx.focus()}
