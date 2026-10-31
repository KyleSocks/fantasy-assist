"""Mid-season assistant CLI.

    uv run python -m src.inseason streams  [--date YYYY-MM-DD] [--group F|D|G] [--all]
    uv run python -m src.inseason player   "Ovechkin" ["Other Name" ...] [--days 10]
    uv run python -m src.inseason schedule [--days 10]

Common flags: --league <yaml stem> (default kyle_12_points), --start / --date
(default today), --db. Run `uv run python -m src.inseason.ingest` first.
"""
from __future__ import annotations

import argparse
import datetime
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
    args = ap.parse_args(argv)

    cfg = load_league(str(LEAGUE_DIR / f"{args.league}.yaml"))
    try:
        ctx = build_context(args.db, cfg)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    unit = "fpts" if ctx.scorer.points else "z"

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
