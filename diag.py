"""Acceptance harness for the goalie projection + valuation fix.

Prints six numbered checks; any FAIL exits non-zero.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "hockey.db"

from src.api.routes import build_board
from src.projections.projections import (
    _compute_pos_means,
    _team_games_for,
    project_all,
)
from src.valuation.valuation import _is_startable, _min_startable_starts


def main() -> int:
    failures: list[str] = []

    def fail(msg: str) -> None:
        failures.append(msg)
        print(f"FAIL: {msg}")

    conn = sqlite3.connect(str(DB))
    season = conn.execute(
        "SELECT MAX(season_id) FROM skater_seasons").fetchone()[0] + 10001
    projs = project_all(season, str(DB))
    projs_by_id = {p.player_id: p for p in projs}
    goalies = [p for p in projs if p.position == "G"]
    min_starts = _min_startable_starts()

    team_of: dict[int, str] = {}
    names: dict[int, str] = {}
    for pid, team, name in conn.execute(
            "SELECT player_id, current_team_abbrev, full_name FROM players"):
        if team:
            team_of[pid] = team
        if name:
            names[pid] = name

    # ITEM 1: per-team starts must sum to the team's game count
    print(f"ITEM 1: per-team goalie starts vs team games (season {season})")
    team_games = _team_games_for(season, conn)
    for team in ("LAK", "MIN", "CGY"):
        members = [p for p in goalies
                   if team_of.get(p.player_id) == team]
        total = sum(p.games_started for p in members)
        games = team_games.get(team, 0)
        print(f"  team {team}: {games} games")
        for p in sorted(members, key=lambda q: -q.games_started):
            print(f"    {names.get(p.player_id, '?'):<22} "
                  f"{p.games_started:8.4f} starts")
        print(f"    {'team goalie total':<22} {total:8.4f} starts")
        if total > games + 1e-9:
            fail(f"team {team}: goalie starts {total:.4f} exceed "
                 f"team games {games}")
        elif abs(total - games) > 1e-9:
            fail(f"team {team}: goalie starts {total:.4f} do not sum to "
                 f"team games {games}")

    # ITEM 2: named third-strings must rank below the established starter
    print("ITEM 2: named goalies -- starts, wins, SV%, VORP, final rank")
    board = build_board("kyle_12_points")
    board_rows = {r["id"]: r for r in board["players"]}
    named: dict[str, tuple] = {}
    for label in ("Portillo", "Garand", "Sergeev", "Gustavsson"):
        row = conn.execute(
            "SELECT player_id, full_name FROM players WHERE full_name LIKE ?"
            " ORDER BY player_id LIMIT 1", (f"%{label}%",)).fetchone()
        if row is None:
            fail(f"named goalie {label} not found in players table")
            continue
        pid, full = row
        p = projs_by_id.get(pid)
        b = board_rows.get(pid)
        if p is None or b is None:
            fail(f"{full} missing from projections or board")
            continue
        named[label] = (full, p, b)
        print(f"  {full:<22} starts={p.games_started:8.4f} "
              f"wins={p.wins:6.2f} sv%={p.save_pct:.4f} "
              f"vorp={b['vorp']:<8} rank={b['rank']}")
    if "Gustavsson" in named:
        gust_rank = named["Gustavsson"][2]["rank"]
        for label in ("Portillo", "Garand", "Sergeev"):
            if label in named and named[label][2]["rank"] <= gust_rank:
                fail(f"{named[label][0]} rank {named[label][2]['rank']} "
                     f"not below Gustavsson rank {gust_rank}")

    # ITEM 3: top 20 goalies by VORP, none under min starts
    print(f"ITEM 3: top 20 goalies by VORP "
          f"(min_startable_starts={min_starts:.0f})")
    g_rows = [r for r in board["players"] if r["pos"] == "G"][:20]
    for r in g_rows:
        starts = projs_by_id[r["id"]].games_started
        print(f"  rank {r['rank']:>4}  {r['name']:<22} "
              f"starts={starts:7.2f}  vorp={r['vorp']}")
        if starts < min_starts:
            fail(f"goalie {r['name']} in top 20 with {starts:.2f} starts")

    # ITEM 4: exclusion counts
    print("ITEM 4: exclusion counts")
    excluded = [p for p in goalies if not _is_startable(p, min_starts)]
    no_team = [p for p in goalies if p.player_id not in team_of]
    print(f"  excluded by min_startable_starts: "
          f"{len(excluded)} of {len(goalies)} goalies")
    print(f"  goalies with no current team: {len(no_team)}")

    # ITEM 5: top 20 overall board before vs after, with rank changes
    print("ITEM 5: top 20 overall board before vs after")
    before = json.loads((ROOT / "before_board.json").read_text())
    before_by_id = {r["id"]: r for r in before["players"]}
    after = board["players"][:20]
    after_rank = {r["id"]: r["rank"] for r in after}
    print("  BEFORE top 20: rank, name, pos, vorp, after-rank")
    for r in before["players"][:20]:
        new_rank = after_rank.get(r["id"], "out of top 20")
        print(f"  {r['rank']:>4}  {r['name']:<22} {r['pos']:<3} "
              f"vorp={r['vorp']:<8} after={new_rank}")
    print("  AFTER top 20: rank, name, pos, vorp, old-rank, change")
    for r in after:
        old = before_by_id.get(r["id"])
        if old is not None:
            change = old["rank"] - r["rank"]
            old_rank = old["rank"]
        else:
            change = r["rank"]
            old_rank = "new"
        print(f"  {r['rank']:>4}  {r['name']:<22} {r['pos']:<3} "
              f"vorp={r['vorp']:<8} old={old_rank:<6} change={change:+d}")

    # ITEM 6: per-stat n check for the named 1-game goalie
    print("ITEM 6: save_pct per-stat n check (Portillo, 1-game goalie)")
    prior_seasons = [r[0] for r in conn.execute(
        "SELECT DISTINCT season_id FROM skater_seasons"
        " WHERE season_id < ? ORDER BY season_id DESC LIMIT 3",
        (season,)).fetchall()]
    pool_mean = _compute_pos_means(conn, prior_seasons, "G")["save_pct"]
    if "Portillo" in named:
        p = named["Portillo"][1]
        n = p.terms.get("n", {}).get("save_pct")
        k = p.terms.get("k", {}).get("save_pct")
        diff = abs(p.save_pct - pool_mean)
        print(f"  Portillo: n={n:.0f} shots against, k={k:.0f}, "
              f"pool mean={pool_mean:.4f}, projected sv%={p.save_pct:.4f}, "
              f"|sv% - pool mean|={diff:.4f} (limit 0.003)")
        if diff > 0.003:
            fail(f"Portillo sv% {p.save_pct:.4f} deviates {diff:.4f} from "
                 f"pool mean {pool_mean:.4f} (limit 0.003)")
    conn.close()

    print(f"RESULT: {'PASS' if not failures else 'FAIL'} "
          f"({len(failures)} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())