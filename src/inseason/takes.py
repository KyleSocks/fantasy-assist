"""Weekly "takes" for your league, like a fantasy column but about the
players in YOUR league: who to trade for, who to pick up, who to buy low,
who to sell high and who to stash.

Two views of a player, both in value per team game (so a player who sits
out counts less):

  model   ctx.scorer.per_game -- the blended projection (luck-adjusted
          shooting, deployment, goalie starts)
  market  teams.Market.per_game -- how Yahoo managers rank him (% rostered,
          season points, preseason rank), turned back into our units: the
          model value of the player at that rank in his group

  buy_low      on another team in your league, model above market by
               ``gap`` or more, and worth starting (model >= bar x
               ``start_bar``); edge = (model - market) x his team's games
               left, i.e. how much more he's worth than his price
  sell_high    yours, market above model by ``gap`` or more
  trade_target on another team, hot over the last 14 days (``heat``,
               4+ games) or just promoted, and worth starting
  waiver_add   a free agent in your league who is hot or just promoted and
               worth rostering (model >= bar x ``add_bar``)
  stash        an injured (OUT) free agent in your league worth at least
               bar x ``stash_bar`` when he's back

``bar`` is the replacement level for his group (the best free agents'
value). Every pick comes with its reasons (luck, last 14 days, line / PP
change, % rostered moving, games missed) and one sentence saying the take.
"""
from __future__ import annotations

from collections.abc import Callable

from src.valuation.valuation import value_group

from .evaluate import Context
from .roster import luck_json, role_json
from .trend import value_of

DEFAULTS = {
    "gap": 0.10,          # model vs market, as a share of the higher one
    "start_bar": 1.10,    # buy-low / trade targets: worth starting
    "add_bar": 0.95,      # waiver adds: about free-agent level or better
    "stash_bar": 1.10,    # stashes: clearly better than what's out there
    "heat": 0.20,         # last 14 days vs blend
    "heat_games": 4,
    "rostered_move": 3,   # % rostered points over 7 days worth a mention
    "missed_games": 3,
    "top": 8,
}


def _cfg(ctx: Context) -> dict:
    return {**DEFAULTS, **(ctx.icfg.get("takes") or {})}


def bar(ctx: Context, pid: int) -> float:
    return ctx.scorer.replacement[value_group(ctx.lines[pid].position)]


def recent(ctx: Context, pid: int, days: int = 14) -> dict | None:
    rec = (ctx.lines[pid].extra.get("windows") or {}).get(days)
    if not rec or not rec["games"]:
        return None
    now, base = value_of(ctx, ctx.lines[pid], rec)
    return {"days": days, "games": rec["games"], "per_game": now, "base": base}


def reasons(ctx: Context, pid: int, rostered: dict | None, today: str,
            cfg: dict | None = None) -> list[dict]:
    """Facts behind a take. ``sign`` +1 = good news (or the crowd will like
    it), -1 = bad news."""
    cfg = cfg or _cfg(ctx)
    ln = ctx.lines[pid]
    out: list[dict] = []
    lk = luck_json(ctx, pid)
    if lk and lk.get("status"):
        out.append({"kind": "luck", "sign": 1 if lk["status"] == "hot" else -1,
                    "text": lk["text"]})
    w = recent(ctx, pid)
    if w and w["games"] >= cfg["heat_games"] and w["base"] > 0:
        d = w["per_game"] - w["base"]
        if abs(d) >= cfg["heat"] * w["base"]:
            per = "per start" if ln.is_goalie else "per game"
            out.append({"kind": "recent", "sign": 1 if d > 0 else -1,
                        "text": f"{w['per_game']:.2f} {per} over the last 14 days "
                                f"({w['games']} games) vs {w['base']:.2f} usual"})
    on = ln.extra.get("onice")
    acfg = ctx.icfg.get("advanced") or {}
    if on and on["shots"] >= acfg.get("onice_flag_shots", 100) \
            and abs(on["pct"] - on["league"]) >= acfg.get("onice_flag_pts", 0.025):
        hot = on["pct"] > on["league"]
        out.append({"kind": "onice", "sign": 1 if hot else -1,
                    "text": f"linemates shooting {on['pct'] * 100:.1f}% at 5v5 with him on the ice "
                            f"vs {on['league'] * 100:.1f}% league average, so his assists should "
                            f"{'cool off' if hot else 'pick up'}"})
    r = role_json(ctx, pid)
    if r and r.get("change"):
        was = ", ".join(x for x in (r.get("usual_line"), r.get("usual_pp") or "no PP") if x)
        out.append({"kind": "role", "sign": 1 if r["change"] == "up" else -1,
                    "text": f"{'promoted' if r['change'] == 'up' else 'demoted'} to "
                            f"{r.get('label') or 'no PP'} (was {was})"})
    if rostered and rostered.get("d7") is not None \
            and abs(rostered["d7"]) >= cfg["rostered_move"]:
        d = rostered["d7"]
        out.append({"kind": "rostered", "sign": 1 if d > 0 else -1,
                    "text": f"% rostered on Yahoo {'up' if d > 0 else 'down'} "
                            f"{abs(d):.0f} this week (now {rostered['pct']:.0f}%)"})
    if not ln.is_goalie and ctx.games:
        played = len(ctx.team_games(ln.team, ctx.games[0][0], ctx.as_of))
        missed = played - (ln.gp or 0)
        if missed >= cfg["missed_games"] and played:
            out.append({"kind": "missed", "sign": -1,
                        "text": f"missed {missed} of {ln.team}'s {played} games, "
                                "so his season totals look small"})
    return out


def _why(rs: list[dict], sign: int | None = None) -> str:
    pick = [r["text"] for r in rs if sign is None or r["sign"] == sign]
    return "; ".join(pick)


def build(ctx: Context, *, owners: dict[int, int], team_names: dict[int, str],
          my_team: int | None, fas: set[int], injured_fas: set[int],
          market, rostered: Callable[[int], dict | None], start: str) -> dict:
    """All five lists for one league (see module docstring)."""
    cfg = _cfg(ctx)
    end = ctx.fantasy_end()
    unit = "fpts" if ctx.scorer.points else "value"

    def left(pid):
        return len(ctx.team_games(ctx.lines[pid].team, start, end))

    def row(pid, **extra):
        ln = ctx.lines[pid]
        model = ctx.scorer.per_game.get(pid) or 0.0
        mk = market.per_game(pid) if market is not None else model
        rs = rostered(pid)
        owner = owners.get(pid)
        return {"id": pid, "name": ln.name, "team": ln.team, "pos": ln.position,
                "owner_id": owner, "owner": team_names.get(owner) if owner is not None else None,
                "model": model, "market": mk, "bar": bar(ctx, pid),
                "games_left": left(pid), "rostered": rs,
                "reasons": reasons(ctx, pid, rs, ctx.as_of, cfg), **extra}

    others = [p for p, t in owners.items() if t != my_team and p in ctx.lines]
    mine = [p for p, t in owners.items() if t == my_team and p in ctx.lines]

    def gap_ok(hi, lo):
        return hi > 0 and hi - lo >= cfg["gap"] * hi

    # ── buy low / sell high: we and the crowd disagree ─────────────────
    buy, sell = [], []
    for p in others:
        x = row(p)
        if x["model"] >= x["bar"] * cfg["start_bar"] and gap_ok(x["model"], x["market"]):
            x["edge"] = (x["model"] - x["market"]) * x["games_left"]
            low, plus = _why(x["reasons"], -1), _why(x["reasons"], 1)
            x["take"] = (f"Buy low from {x['owner'].rstrip('.')}. Your model has him at {x['model']:.2f} per team game, "
                         f"Yahoo managers at {x['market']:.2f}: about {x['edge']:.0f} {unit} more than "
                         f"his price over {x['games_left']} games left."
                         + (f" Why the price is low: {low}." if low else "")
                         + (f" Also: {plus}." if plus else ""))
            buy.append(x)
    for p in mine:
        x = row(p)
        if gap_ok(x["market"], x["model"]):
            x["edge"] = (x["market"] - x["model"]) * x["games_left"]
            high, minus = _why(x["reasons"], 1), _why(x["reasons"], -1)
            x["take"] = (f"Sell high. Yahoo managers value him at {x['market']:.2f} per team game, your "
                         f"model at {x['model']:.2f}: a trade at his price gets you about "
                         f"{x['edge']:.0f} {unit} more than he's worth over {x['games_left']} games left."
                         + (f" Why the price is high: {high}." if high else "")
                         + (f" Before the crowd notices: {minus}." if minus else ""))
            sell.append(x)
    buy.sort(key=lambda x: -x["edge"])
    sell.sort(key=lambda x: -x["edge"])

    # ── risers: hot or promoted, split by who has them ─────────────────
    def rising(x):
        return any(r["kind"] in ("recent", "role") and r["sign"] > 0 for r in x["reasons"])

    def heat(x):
        w = recent(ctx, x["id"])
        return (w["per_game"] - w["base"]) if w else 0.0

    targets, adds = [], []
    bought = {x["id"] for x in buy}
    for p in others:
        if p in bought:           # already a buy-low: the stronger take
            continue
        x = row(p)
        if rising(x) and x["model"] >= x["bar"] * cfg["start_bar"]:
            pricey = x["market"] >= x["model"]
            x["take"] = (f"Trade target on {x['owner']}: {_why(x['reasons'], 1)}. "
                         + ("Yahoo managers already rate him at least as high as your model does, "
                            "so expect to pay full price."
                            if pricey else
                            f"Still priced below your model ({x['market']:.2f} vs {x['model']:.2f} per game)."))
            targets.append(x)
    for p in fas:
        if p not in ctx.lines or p in injured_fas:
            continue
        x = row(p)
        if rising(x) and x["model"] >= x["bar"] * cfg["add_bar"]:
            worth = x["model"] >= x["bar"]
            x["take"] = (f"Free agent in your league: {_why(x['reasons'], 1)}. "
                         + (f"At {x['model']:.2f} per game he's above the free-agent bar ({x['bar']:.2f})."
                            if worth else
                            f"{x['model']:.2f} per game is just under the free-agent bar "
                            f"({x['bar']:.2f}), so it's a bet on the new role or hot streak."))
            adds.append(x)
    targets.sort(key=lambda x: -heat(x) - x["model"])
    adds.sort(key=lambda x: -x["model"])

    # ── stashes: injured free agents worth an IR spot ──────────────────
    stash = []
    for p in injured_fas:
        if p not in ctx.lines:
            continue
        x = row(p)
        if x["model"] >= x["bar"] * cfg["stash_bar"]:
            x["take"] = (f"Injured free agent worth an IR spot: {x['model']:.2f} per game when he "
                         f"plays vs {x['bar']:.2f} for the best healthy free agents.")
            stash.append(x)
    stash.sort(key=lambda x: -x["model"])

    n = cfg["top"]
    return {"unit": unit, "buy_low": buy[:n], "sell_high": sell[:n],
            "trade_targets": targets[:n], "waiver_adds": adds[:n], "stashes": stash[:n]}
