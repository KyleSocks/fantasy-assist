"""Mid-season assistant CLI.

    uv run python -m src.inseason streams  [--date YYYY-MM-DD] [--group F|D|G] [--all]
    uv run python -m src.inseason player   "Ovechkin" ["Other Name" ...] [--days 10]
    uv run python -m src.inseason schedule [--days 10]
    uv run python -m src.inseason plan --roster my_roster.txt --free-agents fa.txt \
        --hold "Ovechkin" --adds 1,4 [--days 9] [--locked DET,STL]

plan files: one player per line, "Name | TEAM | C,LW" (Yahoo eligibility;
team and positions optional, # comments). --roster is everyone who stays
(not the --hold player, not IR); --adds is adds left per fantasy week from
--start onward.

Common flags: --league <yaml stem> (default kyle_12_points), --start / --date
(default today), --db. Run `uv run python -m src.inseason.ingest` first.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

from src.paths import DB_PATH, DEFAULT_LEAGUE_PATH, LEAGUE_DIR
from src.valuation.league import load_league

from .evaluate import (
    _end,
    build_context,
    find_players,
    hold_check,
    load_taken,
    schedule_grid,
    streams,
)


def _mmss(sec: float | None) -> str:
    if sec is None:
        return "  -  "
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def _pct(x: float | None) -> str:
    return "   n/a" if x is None else f"{x * 100:+5.0f}%"


def _vs(pct: float | None, diff: float | None) -> str:
    """% change in points leagues, raw difference in category leagues."""
    if pct is not None:
        return _pct(pct)
    return "   n/a" if diff is None else f"{diff:+6.2f}"


def _print_streams(ctx, args, unit: str) -> None:
    taken = None
    if args.taken:
        taken, missing = load_taken(ctx, args.taken)
        if missing:
            print(f"  warning: {len(missing)} name(s) in {args.taken} matched no"
                  f" player: {', '.join(missing[:8])}")
    rows = streams(ctx, args.date, args.top, args.group,
                   likely_available=not args.all, taken=taken)
    n_games = ctx.games_on(args.date)
    scope = ("excluding players in " + args.taken if taken is not None
             else "all players" if args.all
             else "likely available (outside rostered pool)")
    print(f"Streams for {args.date} -- {n_games} NHL games -- {ctx.league.name}"
          f" ({unit}/game) -- {scope}, stats as of {ctx.as_of}")
    if not rows:
        print("  no games / no candidates")
        return
    print(f"  {'#':>2}  {'player':<24} {'pos':<3} {'tm':<3} {'opp':<4}"
          f" {'tonight':>7} {'neutral':>7} {'repl':>6} {'GP':>3}"
          f" {'vs pre':>7}  note")
    for i, r in enumerate(rows, 1):
        note = (f"start share {r['start_share']:.0%}"
                if r["start_share"] is not None else "")
        print(f"  {i:>2}  {r['name'][:24]:<24} {r['pos']:<3} {r['team'] or '-':<3}"
              f" {r['opp']:<4} {r['value']:7.2f} {r['per_game']:7.2f}"
              f" {r['replacement']:6.2f} {r['gp']:>3} {_vs(r['vs_prior_pct'], r['vs_prior_diff']):>7}"
              f"  {note}")


def _print_hold(ctx, h: dict, unit: str) -> None:
    ln, f, w = h["line"], h["form"], h["window"]
    print(f"\n{h['name']} ({h['pos']}, {h['team'] or 'no team'}) -- "
          f"{ctx.league.name}, stats as of {ctx.as_of}")
    obs = "  n/a" if f["observed"] is None else f"{f['observed']:.2f}"
    print(f"  per-game {unit}: preseason {f['prior']:.2f} | this season {obs}"
          f" ({_vs(f['vs_prior_pct'], f['vs_prior_diff']).strip()}) over {h['gp']}"
          f" {'starts' if ln.is_goalie else 'GP'} | blended {f['blended']:.2f}"
          f" | replacement {h['replacement_pg']:.2f}")
    print(f"  rank {h['group_rank']} in {'G' if ln.is_goalie else ('D' if ln.position == 'D' else 'F')}"
          f" (rostered pool = top {h['pool_size']})")
    if ln.is_goalie:
        print(f"  start share: preseason {ln.start_share_prior:.0%}, "
              f"this season {ln.start_share_obs:.0%}, blended {ln.start_share:.0%}")
    else:
        print(f"  EV TOI/GP: preseason {_mmss(ln.toi_pg_prior)}, "
              f"this season {_mmss(ln.toi_pg_obs)}")
        print("  per game        " + "  ".join(f"{s[:5]:>6}" for s in ln.blended))
        for label, d in (("preseason", ln.prior), ("this season", ln.observed),
                         ("blended", ln.blended)):
            print(f"    {label:<13} " + "  ".join(f"{d[s]:6.3f}" for s in ln.blended))
    print(f"  {h['start']}..{h['end']}: {len(w['games'])} games"
          f" (league avg {h['avg_window_games']:.1f}), {w['off_nights']} off-night,"
          f" worth {w['value']:.2f} {unit}")
    for g in w["games"]:
        print(f"      {g['date']} vs {g['opp']:<3} {g['value']:6.2f}"
              f"{'  off-night' if g['off_night'] else ''}")
    print(f"  window vs replacement on avg schedule: {h['window_vs_replacement']:+.2f}")
    print(f"  rest of season ({h['ros_games']} games, avg {h['avg_ros_games']:.1f}):"
          f" {h['ros_vs_replacement']:+.2f} vs replacement")
    print(f"  VERDICT: {h['verdict']}")


def _read_players(ctx, path: str) -> tuple[dict[int, set[str]], list[str]]:
    from src.inseason.plan import nhl_eligibility
    out: dict[int, set[str]] = {}
    missing: list[str] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            parts = [x.strip() for x in raw.split("#", 1)[0].split("|")]
            if not parts[0]:
                continue
            hits = find_players(ctx, parts[0])
            if len(parts) > 1 and parts[1] and len(hits) > 1:
                hits = [h for h in hits if h.team == parts[1]] or hits
            if not hits:
                missing.append(parts[0])
                continue
            ln = hits[0]
            out[ln.player_id] = ({x.strip() for x in parts[2].split(",")}
                                 if len(parts) > 2 and parts[2]
                                 else nhl_eligibility(ln.position))
    return out, missing


def _plan(ctx, args, unit: str) -> int:
    from src.inseason.evaluate import expected_line
    from src.inseason.lineup import marginal_values
    from src.inseason.plan import nhl_eligibility, plan_slot, week_key

    start = datetime.date.fromisoformat(args.start)
    dates = [(start + datetime.timedelta(days=i)).isoformat()
             for i in range(args.days)]
    locked = {t.strip() for t in (args.locked or "").split(",") if t.strip()}
    for path in (args.roster, args.free_agents):
        if not os.path.exists(path):
            print(f"error: file not found: {path} (paths are relative to "
                  f"{os.getcwd()})", file=sys.stderr)
            return 2
    roster, miss_r = _read_players(ctx, args.roster)
    cands, miss_c = _read_players(ctx, args.free_agents)
    hold = find_players(ctx, args.hold)
    if not hold:
        print(f"error: no player matches --hold '{args.hold}'", file=sys.stderr)
        return 2
    hold = hold[0]
    for m in miss_r + miss_c:
        print(f"  warning: no player matches '{m}'")
    hold_elig = (cands.get(hold.player_id) or roster.get(hold.player_id)
                 or nhl_eligibility(hold.position))
    roster.pop(hold.player_id, None)
    cands[hold.player_id] = hold_elig

    def game_values(pid: int, can_lock: bool) -> dict[str, float]:
        ln = ctx.lines[pid]
        out = {}
        for d, opp in ctx.team_games(ln.team, dates[0], dates[-1]):
            if can_lock and d == dates[0] and ln.team in locked:
                continue
            out[d] = ctx.scorer.value_stats(pid, ln.position,
                                            expected_line(ln, ctx.factors.get(opp)))
        return out

    games = {p: game_values(p, False) for p in roster}
    games.update({p: game_values(p, True) for p in cands})
    slots = {k: v for k, v in ctx.league.roster_slots.items()
             if k not in ("BENCH", "G")}
    slots["Util"] = slots.pop("UTIL", 0)
    mv = marginal_values(roster, cands, games, dates, slots)
    adds = [int(x) for x in args.adds.split(",")]
    weeks = sorted({week_key(d) for d in dates})
    budget = {w: (adds[i] if i < len(adds) else adds[-1]) for i, w in enumerate(weeks)}
    pl = plan_slot(hold.player_id, mv, dates, budget)

    name = lambda p: ctx.lines[p].name  # noqa: E731
    keep = sum(mv[hold.player_id].values())
    print(f"Streaming plan for {hold.name}'s roster spot, {dates[0]}..{dates[-1]}"
          f" ({ctx.league.name}, {unit} added to your nightly lineup)")
    print(f"  keep {hold.name}: +{keep:.2f}   best plan: +{pl.total:.2f}"
          f"   adds per week {budget}")
    for st in pl.steps:
        print(f"  {st.date}: drop {name(st.drop)} -> add {name(st.add)}"
              f" ({','.join(sorted(cands[st.add]))}, {ctx.lines[st.add].team})")
    print("  " + " ".join(f"{d[5:]}:{mv[pl.holder_by_date[d]].get(d, 0):.1f}"
                          for d in dates))
    print("\n  best single adds (value added over the window, nights that count):")
    for p in sorted(mv, key=lambda p: -sum(mv[p].values()))[:10]:
        print(f"    {name(p)[:22]:<23}{ctx.lines[p].team:<4}"
              f"{','.join(sorted(cands[p])):<9}+{sum(mv[p].values()):6.2f}"
              f"  {' '.join(d[5:] for d in mv[p])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    today = datetime.date.today().isoformat()
    ap = argparse.ArgumentParser(description="Mid-season assistant")
    ap.add_argument("--league", default=DEFAULT_LEAGUE_PATH.stem)
    ap.add_argument("--db", default=str(DB_PATH))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("streams", help="best single-game streams for a date")
    s.add_argument("--date", default=today)
    s.add_argument("--top", type=int, default=25)
    s.add_argument("--group", choices=["F", "D", "G"])
    s.add_argument("--all", action="store_true",
                   help="include players inside the rostered pool")
    s.add_argument("--taken", metavar="FILE",
                   help="text file of rostered player names (one per line)"
                        " to exclude, instead of the rostered-pool guess")
    p = sub.add_parser("player", help="hold/drop check for one or more players")
    p.add_argument("names", nargs="+")
    p.add_argument("--start", default=today)
    p.add_argument("--days", type=int, default=10)
    g = sub.add_parser("schedule", help="team games in the next N days")
    g.add_argument("--start", default=today)
    g.add_argument("--days", type=int, default=10)
    pl = sub.add_parser("plan", help="roster-aware add/drop plan for one spot")
    pl.add_argument("--roster", required=True)
    pl.add_argument("--free-agents", required=True)
    pl.add_argument("--hold", required=True, help="player whose spot to stream")
    pl.add_argument("--adds", default="1,4",
                    help="adds left per fantasy week, this week first")
    pl.add_argument("--start", default=today)
    pl.add_argument("--days", type=int, default=9)
    pl.add_argument("--locked", help="NHL teams whose game today has started")
    args = ap.parse_args(argv)

    cfg = load_league(str(LEAGUE_DIR / f"{args.league}.yaml"))
    try:
        ctx = build_context(args.db, cfg)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    unit = "fpts" if ctx.scorer.points else "z"

    if args.cmd == "plan":
        return _plan(ctx, args, unit)
    if args.cmd == "streams":
        _print_streams(ctx, args, unit)
    elif args.cmd == "player":
        summary = []
        for q in args.names:
            hits = find_players(ctx, q)
            if not hits:
                print(f"\nno player matches '{q}'")
                continue
            if len(hits) > 1 and hits[0].name.lower() != q.lower():
                others = ", ".join(h.name for h in hits[1:4])
                print(f"\n'{q}' -> {hits[0].name} (also matched: {others})")
            h = hold_check(ctx, hits[0].player_id, args.start, args.days)
            _print_hold(ctx, h, unit)
            summary.append(h)
        if len(summary) > 1:
            print(f"\n  {'player':<24} {'games':>5} {'window':>7} {'vs repl':>8}"
                  f" {'ROS vs repl':>11}  verdict")
            for h in sorted(summary, key=lambda h: -h["window"]["value"]):
                print(f"  {h['name'][:24]:<24} {len(h['window']['games']):>5}"
                      f" {h['window']['value']:7.2f} {h['window_vs_replacement']:+8.2f}"
                      f" {h['ros_vs_replacement']:+11.2f}  {h['verdict']}")
    else:
        rows = schedule_grid(ctx, args.start, args.days)
        end = _end(args.start, args.days)
        print(f"Team games {args.start}..{end}"
              f" (league avg {ctx.avg_team_games(args.start, end):.1f},"
              f" off-night = <= {ctx.icfg['off_night_max_games']} NHL games)")
        print(f"  {'tm':<3} {'G':>2} {'off':>3} {'opp GA x':>8}  opponents")
        for r in rows:
            fac = "-" if r["opp_ga_factor"] is None else f"{r['opp_ga_factor']:.2f}"
            print(f"  {r['team']:<3} {r['games']:>2} {r['off_nights']:>3} {fac:>8}"
                  f"  {' '.join(r['opponents'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
