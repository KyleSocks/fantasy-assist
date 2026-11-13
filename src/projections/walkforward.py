"""Walk-forward backtest scored in your league's fantasy points.

For each of the last N seasons that have at least two prior seasons, the
model projects that season using only the seasons before it, then is
scored against what actually happened -- in fantasy points from your
league file, on the players you would actually draft (top N by projected
points; goalies separately). Errors are split exactly into a per-game RATE
part and a GAMES-PLAYED part:

    proj_pts - act_pts = (rate_p - rate_a) * gp_a  +  rate_p * (gp_p - gp_a)

so you can see whether misses come from talent or from health.

Usage:
    uv run python -m src.projections.walkforward --db data/hockey.db
    ... --set goals=300 --set team_hist_weight=0.3
    ... --grid goals=150,200,300 --grid rookie_ramp_gp=100,200,300

--set overrides config/regression.yaml for every run; --grid runs every
combination and prints one summary line each (lower MAE is better), so
the parameters can be fitted instead of hand-set. Needs regular-season
data (re-ingest after the gameTypeId=2 fix) and ideally 8+ seasons.
Shortened seasons are handled: projected games follow each season's
schedule length (common.season_games).
"""
from __future__ import annotations

import argparse
import itertools
import math

import sqlite3

import yaml

from src.paths import DEFAULT_DB_ARG
from src.valuation.league import load_league, points_value

from .backtest_support import _spearman
from .common import Projection
from .projections import project_all

SK_CATS = ("goals", "assists", "shots", "blocks", "hits", "pp_points",
           "sh_points")
G_CATS = ("games_started", "wins", "saves", "goals_against", "shutouts")


def score(preds: list[float], acts: list[float]) -> dict[str, float]:
    """MAE, RMSE, mean bias (pred - actual) and Spearman rho."""
    n = len(preds)
    if n == 0:
        return {"n": 0, "mae": 0.0, "rmse": 0.0, "bias": 0.0, "rho": 0.0}
    err = [p - a for p, a in zip(preds, acts)]
    return {"n": n, "mae": sum(abs(e) for e in err) / n,
            "rmse": math.sqrt(sum(e * e for e in err) / n),
            "bias": sum(err) / n,
            "rho": _spearman(preds, acts) if n > 1 else 0.0}


def split_error(pts_p: float, gp_p: float, pts_a: float,
                gp_a: float) -> tuple[float, float]:
    """(rate part, games part) of pts_p - pts_a; they sum exactly to it."""
    rate_p = pts_p / gp_p if gp_p > 0 else 0.0
    rate_a = pts_a / gp_a if gp_a > 0 else 0.0
    return (rate_p - rate_a) * gp_a, rate_p * (gp_p - gp_a)


def _actual_skaters(conn, season: int) -> dict[int, Projection]:
    out = {}
    for r in conn.execute(
            "SELECT player_id, gp, goals, assists, shots, blocks, hits,"
            " pp_points, sh_points FROM skater_seasons"
            " WHERE season_id = ? AND gp > 0", (season,)):
        out[r[0]] = Projection(
            player_id=r[0], season_id=season, gp=float(r[1]), toi_per_gp=0.0,
            goals=r[2] or 0, assists=r[3] or 0, shots=r[4] or 0,
            blocks=r[5] or 0, hits=r[6] or 0, pp_points=r[7] or 0,
            sh_points=r[8] or 0, wins=None, save_pct=None)
    return out


def _actual_goalies(conn, season: int) -> dict[int, Projection]:
    out = {}
    for r in conn.execute(
            "SELECT player_id, games_started, wins, saves, shots_against,"
            " goals_against, shutouts FROM goalie_seasons"
            " WHERE season_id = ? AND gp > 0", (season,)):
        gs, sa, sv = r[1] or 0, r[4] or 0, r[3] or 0
        out[r[0]] = Projection(
            player_id=r[0], season_id=season, gp=float(gs), toi_per_gp=0.0,
            goals=0, assists=0, shots=0, blocks=0, hits=0, pp_points=0,
            wins=r[2] or 0, save_pct=(sv / sa if sa else None),
            games_started=gs, saves=sv, goals_against=r[5] or 0,
            shutouts=r[6] or 0, position="G")
    return out


def target_seasons(conn: sqlite3.Connection, n: int) -> list[int]:
    """The most recent n seasons with at least 2 prior seasons, oldest first."""
    seasons = [r[0] for r in conn.execute(
        "SELECT DISTINCT season_id FROM skater_seasons ORDER BY season_id")]
    return [s for i, s in enumerate(seasons) if i >= 2][-n:]


def evaluate(db: str, seasons: list[int], league, overrides: dict | None,
             top: int, top_g: int) -> dict:
    """Pooled metrics over every target season for one config."""
    conn = sqlite3.connect(db)
    rows_s, rows_g = [], []
    try:
        for season in seasons:
            projs = project_all(season, db, overrides)
            act_s, act_g = _actual_skaters(conn, season), _actual_goalies(conn, season)
            prev = _actual_skaters(conn, season - 10001)
            sk = [(p, act_s[p.player_id]) for p in projs
                  if p.position != "G" and p.player_id in act_s]
            sk.sort(key=lambda t: -points_value(t[0], league))
            for p, a in sk[:top]:
                b = prev.get(p.player_id)
                rows_s.append({"season": season, "p": p, "a": a,
                               "pp": points_value(p, league),
                               "pa": points_value(a, league),
                               "base": points_value(b, league) if b else 0.0})
            gl = [(p, act_g[p.player_id]) for p in projs
                  if p.position == "G" and p.player_id in act_g]
            gl.sort(key=lambda t: -points_value(t[0], league))
            for p, a in gl[:top_g]:
                rows_g.append({"season": season, "p": p, "a": a,
                               "pp": points_value(p, league),
                               "pa": points_value(a, league)})
    finally:
        conn.close()
    return summarize(rows_s, rows_g)


def summarize(rows_s: list[dict], rows_g: list[dict]) -> dict:
    out: dict = {}
    if rows_s:
        out["skaters"] = score([r["pp"] for r in rows_s], [r["pa"] for r in rows_s])
        out["skaters_naive"] = score([r["base"] for r in rows_s],
                                     [r["pa"] for r in rows_s])
        parts = [split_error(r["pp"], r["p"].gp, r["pa"], r["a"].gp) for r in rows_s]
        out["rate_part_mae"] = sum(abs(x) for x, _ in parts) / len(parts)
        out["gp_part_mae"] = sum(abs(y) for _, y in parts) / len(parts)
        out["gp"] = score([r["p"].gp for r in rows_s], [r["a"].gp for r in rows_s])
        out["cats"] = {c: score([getattr(r["p"], c) for r in rows_s],
                                [getattr(r["a"], c) for r in rows_s])
                       for c in SK_CATS}
        ranked = sorted(rows_s, key=lambda r: -r["pp"])
        tiers = {}
        for lo, hi in ((0, 50), (50, 100), (100, 250), (250, 10 ** 9)):
            tier = ranked[lo:hi]
            if tier:
                res = [r["pa"] - r["pp"] for r in tier]
                m = sum(res) / len(res)
                tiers[f"{lo + 1}-{min(hi, len(ranked))}"] = math.sqrt(
                    sum((x - m) ** 2 for x in res) / len(res))
        out["residual_sd_by_tier"] = tiers
    if rows_g:
        out["goalies"] = score([r["pp"] for r in rows_g], [r["pa"] for r in rows_g])
        out["goalie_cats"] = {c: score([getattr(r["p"], c) or 0 for r in rows_g],
                                       [getattr(r["a"], c) or 0 for r in rows_g])
                              for c in G_CATS}
        out["goalie_per_start"] = goalie_per_start(rows_g)
    return out


def goalie_per_start(rows_g: list[dict]) -> dict[str, tuple[float, float]]:
    """Pooled per-start rates, (projected, actual): separates a starts
    miss from a per-start miss (shots against, save %, wins, shutouts)."""
    def pooled(side: str) -> dict[str, float]:
        gs = sum(r[side].games_started or 0 for r in rows_g)
        sv = sum(r[side].saves or 0 for r in rows_g)
        ga = sum(r[side].goals_against or 0 for r in rows_g)
        if gs <= 0:
            return {k: 0.0 for k in ("sa", "sv_pct", "wins", "so")}
        return {"sa": (sv + ga) / gs,
                "sv_pct": sv / (sv + ga) if sv + ga > 0 else 0.0,
                "wins": sum(r[side].wins or 0 for r in rows_g) / gs,
                "so": sum(r[side].shutouts or 0 for r in rows_g) / gs}
    p, a = pooled("p"), pooled("a")
    return {k: (p[k], a[k]) for k in p}


def _parse_kv(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        out[k.strip()] = yaml.safe_load(v)
    return out


def _print_full(res: dict) -> None:
    def line(name, s):
        print(f"  {name:<16} n={s['n']:>5}  MAE {s['mae']:8.2f}  RMSE {s['rmse']:8.2f}"
              f"  bias {s['bias']:+8.2f}  rho {s['rho']:.3f}")
    if "skaters" in res:
        print("Skaters (fantasy points):")
        line("model", res["skaters"])
        line("naive last-yr", res["skaters_naive"])
        print(f"  error split: rate part MAE {res['rate_part_mae']:.2f}, "
              f"games part MAE {res['gp_part_mae']:.2f}")
        line("games played", res["gp"])
        for c, s in res["cats"].items():
            line(c, s)
        print("  residual SD by projected rank (band width data): " + ", ".join(
            f"{k}: {v:.1f}" for k, v in res["residual_sd_by_tier"].items()))
    if "goalies" in res:
        print("Goalies (fantasy points):")
        line("model", res["goalies"])
        for c, s in res["goalie_cats"].items():
            line(c, s)
        ps = res["goalie_per_start"]
        print("  per start, projected vs actual: " + ", ".join(
            f"{k} {p:.3f} vs {a:.3f}" for k, (p, a) in ps.items()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=DEFAULT_DB_ARG)
    ap.add_argument("--league", default=None, help="league yaml (default: yours)")
    ap.add_argument("--seasons", type=int, default=8,
                    help="target seasons to score (default 8)")
    ap.add_argument("--top", type=int, default=250, help="skaters scored")
    ap.add_argument("--top-goalies", type=int, default=64)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--grid", action="append", default=[], metavar="KEY=V1,V2")
    args = ap.parse_args()
    league = load_league(args.league)
    conn = sqlite3.connect(args.db)
    seasons = target_seasons(conn, args.seasons)
    conn.close()
    if not seasons:
        raise SystemExit("need at least 3 seasons in skater_seasons; ingest more"
                         " (python -m src.ingest.ingest --seasons 10)")
    print(f"Walk-forward seasons: {', '.join(map(str, seasons))}  league: {league.name}")
    base = _parse_kv(args.set)
    if not args.grid:
        _print_full(evaluate(args.db, seasons, league, base, args.top,
                             args.top_goalies))
        return
    grid = {k: [yaml.safe_load(x) for x in str(v).split(",")]
            for k, v in ((g.partition("=")[0], g.partition("=")[2]) for g in args.grid)}
    combos = list(itertools.product(*grid.values()))
    print(f"{len(combos)} combinations")
    results = []
    for combo in combos:
        ov = {**base, **dict(zip(grid, combo))}
        r = evaluate(args.db, seasons, league, ov, args.top, args.top_goalies)
        results.append((r.get("skaters", {}).get("mae", 0.0),
                        r.get("goalies", {}).get("mae", 0.0), dict(zip(grid, combo))))
        print(f"  {dict(zip(grid, combo))}: skater MAE {results[-1][0]:.2f}"
              f"  goalie MAE {results[-1][1]:.2f}")
    best_s = min(results, key=lambda t: t[0])
    best_g = min(results, key=lambda t: t[1])
    print(f"best skater MAE {best_s[0]:.2f} at {best_s[2]}")
    print(f"best goalie MAE {best_g[1]:.2f} at {best_g[2]}")


if __name__ == "__main__":
    main()
