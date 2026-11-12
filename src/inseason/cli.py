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

from src.paths import DATA_DIR, DB_PATH, DEFAULT_LEAGUE_PATH, LEAGUE_DIR
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
          + ("" if ln.is_goalie else
             f" x plays {f['avail']:.0%} of games (preseason {ln.play_share_prior:.0%},"
             f" this season {ln.play_share_obs:.0%}) = {f['team_game']:.2f}")
          + f" | replacement {h['replacement_pg']:.2f}")
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
            print(f"    {label:<13} " + "  ".join(f"{d.get(s, 0.0):6.3f}" for s in ln.blended))
    print(f"  {h['start']}..{h['end']}: {len(w['games'])} games"
          f" (league avg {h['avg_window_games']:.1f}), {w['off_nights']} off-night,"
          f" worth {w['value']:.2f} {unit}")
    for g in w["games"]:
        print(f"      {g['date']} vs {g['opp']:<3} {g['value']:6.2f}"
              f"{'  off-night' if g['off_night'] else ''}")
    print(f"  window vs replacement on avg schedule: {h['window_vs_replacement']:+.2f}")
    print(f"  rest of season to {h['ros_end']} ({h['ros_games']} games, avg {h['avg_ros_games']:.1f}):"
          f" {h['ros_vs_replacement']:+.2f} vs replacement")
    print(f"  VERDICT: {h['verdict']}")


def _read_players(ctx, path: str) -> tuple[dict[int, set[str]], list[str]]:
    from src.inseason.roster import parse_players
    with open(path, encoding="utf-8") as f:
        return parse_players(ctx, f.read())


def _plan(ctx, args, unit: str) -> int:
    from src.inseason.roster import roster_plan

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
    for m in miss_r + miss_c:
        print(f"  warning: no player matches '{m}'")
    locked = {t.strip() for t in (args.locked or "").split(",") if t.strip()}
    r = roster_plan(ctx, roster, cands, hold[0].player_id, args.start,
                    args.days, [int(x) for x in args.adds.split(",")], locked,
                    force=args.force)
    d = r["dates"]
    print(f"Streaming plan for {r['hold']['name']}'s roster spot, {d[0]}..{d[-1]}"
          f" ({ctx.league.name}, {unit} added to your nightly lineup)")
    print(f"  keep {r['hold']['name']}: +{r['keep_value']:.2f}   best plan:"
          f" +{r['plan_value']:.2f}   adds per week {r['budget']}")
    if r["protected"]:
        print(f"  protected: {r['protected']} (--force to plan anyway)")
    for st in r["steps"]:
        print(f"  {st['date']}: drop {st['drop']['name']} -> add"
              f" {st['add']['name']} ({','.join(st['add']['elig'])},"
              f" {st['add']['team']})")
    print("  " + " ".join(f"{n['date'][5:]}:{n['value']:.1f}" for n in r["nightly"]))
    print("\n  best single adds (value added over the window, nights that count):")
    for c in r["singles"][:10]:
        print(f"    {c['name'][:22]:<23}{c['team']:<4}{','.join(c['elig']):<9}"
              f"+{c['adds']:6.2f}  {' '.join(n[5:] for n in c['nights'])}")
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
    s.add_argument("--group", choices=["F", "D", "G", "S"],
                   help="S = all skaters")
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
    pl.add_argument("--force", action="store_true",
                    help="plan even if --hold is a protected player")
    args = ap.parse_args(argv)

    cfg = load_league(str(LEAGUE_DIR / f"{args.league}.yaml"))
    try:
        ctx = build_context(args.db, cfg)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    unit = "fpts" if ctx.scorer.points else "z"
    fa_path = DATA_DIR / "fa.txt"
    if fa_path.exists():          # replacement = best free agent you can add
        from src.inseason.evaluate import apply_waiver_replacement
        from src.inseason.roster import parse_list
        by = apply_waiver_replacement(
            ctx, parse_list(ctx, fa_path.read_text(encoding="utf-8")).active)
        print("replacement (free agents): " + ", ".join(
            f"{g} {n} {ctx.scorer.replacement[g]:.2f}" for g, n in sorted(by.items())))

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
