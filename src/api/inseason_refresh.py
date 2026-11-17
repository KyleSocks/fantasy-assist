"""The work behind POST /api/inseason/refresh (src/api/inseason.py holds the
route and the lock).

1. Re-ingest this season's stats, rosters and schedule -- once.
2. For each Yahoo league to refresh: read your team, every available player
   and every team's roster; compare the team rosters with the previous
   snapshot and log the changes (src/inseason/moves.py); then replace the
   list files together. Then the trade-market data, separately.

Which leagues: ``league`` given -> that config's Yahoo league. Omitted ->
the default league plus every config with its own ``yahoo:`` block, so the
systemd timer keeps all of them current. Leagues without their own block
share the default's files, so they're covered by the default's pull.

The result keeps the single-league shape (``yahoo``, ``yahoo_error``,
``market``) for the first league and adds ``leagues`` with one entry per
league when more than one was pulled; ``yahoo_error`` then names every
league that failed, so ``strict`` still fails the timer.

Paths, the context and the Yahoo fetchers are read through their modules at
call time, so tests that monkeypatch them still apply.
"""
from __future__ import annotations

import datetime
import json
import time

from fastapi import HTTPException

from src.api import inseason as ins
from src.inseason import leagues as store
from src.inseason import moves

NO_LINK = ("no Yahoo league set for '{key}': add a yahoo: block (league_id, "
           "team_id) to config/leagues/{key}.yaml, or set yahoo.league_id / "
           "team_id in config/inseason.yaml")


def _targets(league: str | None, icfg: dict) -> list[tuple[str, object, dict | None]]:
    """[(config stem, config or None, yahoo link or None)], one per data
    folder: the requested league, or the default + every self-linked one."""
    key, cfg = ins._league_cfg(league)
    out = [(key, cfg, store.yahoo_link(cfg, icfg))]
    if league:
        return out
    for path in sorted(ins.LEAGUE_DIR.glob("*.yaml")):
        k, c = ins._league_cfg(path.stem)
        if k != key and c is not None and c.yahoo is not None:
            out.append((k, c, store.yahoo_link(c, icfg)))
    return out


def _snapshot(link: dict, teams: dict) -> dict:
    return {
        "league_id": link["league_id"], "my_team_id": link["team_id"],
        "fetched": datetime.datetime.now().isoformat(timespec="minutes"),
        "teams": {str(tid): {"name": t["name"], "players": [
            {"name": p.name, "team": p.team, "positions": list(p.positions),
             "inactive": p.inactive, "injury": p.injury}
            for p in t["players"]]} for tid, t in teams.items()},
    }


def _pull(key: str, cfg, link: dict, force: bool) -> dict:
    """One league's Yahoo pull. Failures are reported in the result (the old
    files stay), never raised."""
    from src.inseason import yahoo_public as yp

    folder = store.data_dir(ins.DATA_DIR, key, cfg)
    out: dict = {"yahoo": None, "yahoo_error": None}
    try:
        stats: dict = {}
        roster, fas = yp.fetch_lists(link["league_id"], link["team_id"],
                                     link["free_agent_pages"], stats=stats)
        teams = yp.fetch_league(link["league_id"])
        old = ins._list_count(folder / ins.FA_FILE)
        if (not force and old >= ins.FA_SHRINK_MIN_OLD
                and len(fas) < ins.FA_SHRINK_FLOOR * old):
            raise ValueError(
                f"free-agent list shrank from {old} to {len(fas)} players"
                " -- likely a partial Yahoo page; kept the old lists "
                "(refresh with force=true to accept it)")
        roster_text = yp.render(roster, "My roster")
        fa_text = yp.render(fas, "Free agents")
        snap = _snapshot(link, teams)
        before = store.read_json(folder / ins.LEAGUE_FILE)
        log = store.read_json(folder / ins.MOVES_FILE)
        # a snapshot of a DIFFERENT Yahoo league (the ids were edited) is not
        # a baseline: every player would read as dropped and added. Start
        # the log over from this pull.
        if before and before.get("league_id") not in (None, snap["league_id"]):
            before = log = None
        gone = moves.emptied_teams(before, snap)
        if gone and not force:
            raise ValueError(
                f"no players came back for {', '.join(gone)} -- likely a "
                "partial Yahoo page; kept the old lists (refresh with "
                "force=true to accept it)")
        events = moves.diff(before, snap)
        log = moves.append(log, events, before, snap)
        folder.mkdir(parents=True, exist_ok=True)
        ins._write_atomic({
            folder / ins.ROSTER_FILE: roster_text,
            folder / ins.FA_FILE: fa_text,
            folder / ins.LEAGUE_FILE: json.dumps(snap, ensure_ascii=False, indent=1),
            folder / ins.MOVES_FILE: json.dumps(log, ensure_ascii=False, indent=1)})
        ctx = ins._ctx(key)
        _, miss_r = ins.parse_players(ctx, roster_text)
        _, miss_f = ins.parse_players(ctx, fa_text)
        out["yahoo"] = {"roster": len(roster), "free_agents": len(fas),
                        "teams": len(teams), "unmatched": miss_r + miss_f,
                        "unparsed_rows": stats.get("unparsed", 0)}
        out["moves"] = len(events)
    except Exception as e:
        out["yahoo_error"] = f"Yahoo lists not updated: {e}"
    # trade-market data (% rostered, Fan Pts, ranks): optional extra
    try:
        rows = yp.fetch_market(link["league_id"])
        if rows:
            folder.mkdir(parents=True, exist_ok=True)
            ins._write_atomic({folder / ins.MARKET_FILE: json.dumps({
                "fetched": datetime.datetime.now().isoformat(timespec="minutes"),
                "players": rows}, ensure_ascii=False)})
        out["market"] = len(rows)
    except Exception as e:
        out["market_error"] = f"market data not updated: {e}"
    return out


def run(league: str | None, force: bool) -> dict:
    from src.inseason import ingest as ing
    from src.inseason.model import load_inseason_config

    t0 = time.monotonic()
    try:
        notes: list[str] = []
        counts = ing.ingest_current(str(ins.DB_PATH), notes_out=notes)
    except Exception as e:  # network / API errors surface to the page
        raise HTTPException(502, f"stats ingest failed: {e}")
    ins._CACHE.clear()
    out = {"ingest": counts, "yahoo": None, "yahoo_error": None,
           "ingest_notes": notes}
    results: dict[str, dict] = {}
    for key, cfg, link in _targets(league, load_inseason_config() or {}):
        results[key] = (_pull(key, cfg, link, force) if link else
                        {"yahoo": None, "yahoo_error": NO_LINK.format(key=key)})
    first = next(iter(results))
    out.update(results[first], league=first)
    if len(results) > 1:
        out["leagues"] = results
        errs = [f"{k}: {r['yahoo_error']}" for k, r in results.items()
                if r.get("yahoo_error")]
        out["yahoo_error"] = "; ".join(errs) or None
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out
