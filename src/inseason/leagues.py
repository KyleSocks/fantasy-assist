"""Which Yahoo league a league config follows, and where its files live.

Every league config (config/leagues/<key>.yaml) can be used mid-season.

- A config WITH a ``yahoo:`` block (league_id, team_id) is its own league:
  Refresh pulls that Yahoo league and its files live in
  ``data/leagues/<key>/``.
- A config WITHOUT one follows the default Yahoo league (``yahoo:`` in
  config/inseason.yaml) and shares the files in ``data/`` -- the same roster
  under different scoring, which is how it has always worked.

Files (same names in either place): my_roster.txt, fa.txt,
league_rosters.json, market.json, roster_moves.json, matchups.json, market_history.json, alerts.json.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.valuation.league import LeagueConfig

ROSTER_FILE = "my_roster.txt"
FA_FILE = "fa.txt"
LEAGUE_FILE = "league_rosters.json"
MARKET_FILE = "market.json"
MOVES_FILE = "roster_moves.json"
MATCHUPS_FILE = "matchups.json"
MARKET_HISTORY_FILE = "market_history.json"
ALERTS_FILE = "alerts.json"


def yahoo_link(cfg: LeagueConfig | None, icfg: dict | None) -> dict | None:
    """{league_id, team_id, free_agent_pages, own} for a league config, or
    None when neither it nor config/inseason.yaml names a Yahoo league.
    ``own``: the ids come from the league's own ``yahoo:`` block."""
    y = getattr(cfg, "yahoo", None)
    if y is not None:
        return {"league_id": y.league_id, "team_id": y.team_id,
                "free_agent_pages": y.free_agent_pages, "own": True}
    d = (icfg or {}).get("yahoo") or {}
    if d.get("league_id") and d.get("team_id"):
        return {"league_id": int(d["league_id"]), "team_id": int(d["team_id"]),
                "free_agent_pages": int(d.get("free_agent_pages", 0)),
                "own": False}
    return None


def data_dir(root: Path, key: str | None, cfg: LeagueConfig | None) -> Path:
    """Folder holding a league's list files (see the module docstring)."""
    if key and getattr(cfg, "yahoo", None) is not None:
        return Path(root) / "leagues" / key
    return Path(root)


def read_json(path: Path) -> dict | None:
    """A stored JSON file, or None if it's missing or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def my_team_name(league_file: dict | None) -> str | None:
    """Your team's name as Yahoo shows it, from league_rosters.json."""
    if not league_file:
        return None
    team = (league_file.get("teams") or {}).get(str(league_file.get("my_team_id")))
    return (team or {}).get("name") or None


def describe(key: str, cfg: LeagueConfig, icfg: dict | None, root: Path) -> dict:
    """What the league picker shows for one config."""
    link = yahoo_link(cfg, icfg)
    stored = read_json(data_dir(root, key, cfg) / LEAGUE_FILE)
    return {
        "scoring_type": cfg.scoring_type,
        "team_count": cfg.team_count,
        "yahoo": link,
        "team_name": cfg.team_name or my_team_name(stored),
        "rosters_fetched": (stored or {}).get("fetched"),
    }
