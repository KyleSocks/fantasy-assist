"""Settings you can change from the app (the gear button).

config/inseason.yaml holds the defaults (with the long explanations) and is
read-only inside Docker. Changes made in the app go to data/settings.yaml,
which holds only the keys you changed; load_inseason_config() lays it over
the defaults. Delete that file (or press "Reset all") to go back.

FIELDS is the list the app shows: dotted key, label, one line of help,
type and limits. Only these keys can be written. Keys with no value in
inseason.yaml (e.g. takes.*) default to the code's own default.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

from src.paths import DATA_DIR

SETTINGS_PATH = Path(os.environ.get("INSEASON_SETTINGS_PATH", DATA_DIR / "settings.yaml"))


def _takes_default(k):
    from .takes import DEFAULTS
    return DEFAULTS[k]


def F(key, label, help, type="float", min=None, max=None, step=None, choices=None, default=None):
    return {"key": key, "label": label, "help": help, "type": type, "min": min, "max": max,
            "step": step, "choices": choices, "code_default": default}


GROUPS = [
    ("Projection: how fast this season counts", "How many games of the preseason projection "
     "are mixed in with this season (K). Bigger = trust the projection longer.", [
        F("skater_k_games.goals", "Goals K", "games of prior; only used if Shooting % K is 0 "
          "(otherwise goals = shots x shooting %)", "int", 0, 200),
        F("skater_k_games.assists", "Assists K", "games of prior", "int", 0, 200),
        F("skater_k_games.pp_points", "PP points K", "games of prior", "int", 0, 200),
        F("skater_k_games.sh_points", "SH points K", "games of prior", "int", 0, 300),
        F("skater_k_games.shots", "Shots K", "games of prior", "int", 0, 200),
        F("skater_k_games.hits", "Hits K", "games of prior", "int", 0, 200),
        F("skater_k_games.blocks", "Blocks K", "games of prior", "int", 0, 200),
        F("skater_sh_pct_k_shots", "Shooting % K", "shots of his projected shooting % mixed in; "
          "0 = blend goals per game with Goals K instead", "int", 0, 3000),
        F("goalie_k_starts.wins", "Goalie wins K", "starts of prior", "int", 0, 200),
        F("goalie_k_starts.saves", "Goalie saves K", "starts of prior", "int", 0, 200),
        F("goalie_k_starts.shutouts", "Goalie shutouts K", "starts of prior", "int", 0, 300),
        F("goalie_save_pct_k_shots", "Save % K", "shots of his projected save % mixed in", "int", 0, 5000),
        F("start_share_k_games", "Goalie start share K", "team games of prior", "int", 0, 82),
        F("opponent_k_games", "Opponent strength K", "games of league average mixed in", "int", 0, 82),
    ]),
    ("Projection: games, ice time and luck", None, [
        F("skater_play_share", "Discount skaters for missed games",
          "off: every skater counts every team game (IR slots and streaming cover injuries)", "bool"),
        F("goalie_b2b_starter_factor", "Starter on a back-to-back", "share of his usual start chance on the 2nd night",
          "float", 0, 1, 0.05),
        F("deployment.k_games", "Ice time K", "games of season average vs recent ice time", "int", 0, 40),
        F("deployment.last_game_weight", "Last game's PP time", "counts as this many games", "float", 0, 10, 0.5),
        F("deployment.min_factor", "Ice-time factor floor", "lowest scaling for less ice time", "float", 0.1, 1, 0.05),
        F("deployment.max_factor", "Ice-time factor cap", "highest scaling for more ice time", "float", 1, 3, 0.05),
        F("advanced.onice_sh_pct_k_shots.F", "On-ice SH% K (forwards)",
          "on-ice shots of his linemates' baseline mixed in; bigger = more of the gap is luck", "int", 0, 10000),
        F("advanced.onice_sh_pct_k_shots.D", "On-ice SH% K (defence)", "same for defencemen", "int", 0, 10000),
        F("advanced.career_sh_pct_k_shots", "Career SH% K", "position-average shots mixed into a short career",
          "int", 0, 2000),
        F("advanced.onice_shots_per_60", "5v5 shots for per 60", "league-typical, to estimate on-ice shots",
          "float", 10, 50, 0.5),
    ]),
    ("Keep, drop and stream", None, [
        F("hold_margin", "HOLD margin", "this much above the free-agent bar is always a HOLD", "float", 0, 1, 0.01),
        F("drop_margin", "DROP margin", "this much below the bar before DROP CANDIDATE", "float", 0, 1, 0.01),
        F("protect_min_gp", "Protect HOLDs until", "games played before a struggling HOLD can be streamed over",
          "int", 0, 82),
        F("replacement_fa_rank.F", "Free-agent bar: forwards", "Nth best free agent sets the bar", "int", 1, 50),
        F("replacement_fa_rank.D", "Free-agent bar: defence", "Nth best free agent", "int", 1, 50),
        F("replacement_fa_rank.G", "Free-agent bar: goalies", "Nth best free agent", "int", 1, 20),
        F("playoff_game_weight", "Fantasy playoff game weight", "a playoff-week game counts this many times",
          "float", 1, 5, 0.25),
        F("stream_goalie_min_start_share", "Stream goalies starting at least", "share of team games",
          "float", 0, 1, 0.05),
        F("off_night_max_games", "Off-night", "nights with this many NHL games or fewer", "int", 1, 16),
        F("trend_days", "Trend window", "days", "int", 7, 60),
        F("trend_min_games", "Trend minimum games", "fewer = no trend shown", "int", 1, 20),
    ]),
    ("Trades: the Yahoo market", "How a typical manager values a player.", [
        F("market_weights.rostered", "Weight: % rostered", "", "float", 0, 1, 0.05),
        F("market_weights.points", "Weight: season points", "", "float", 0, 1, 0.05),
        F("market_weights.name", "Weight: preseason rank", "", "float", 0, 1, 0.05),
        F("market_points", "Season points as", "total (as Yahoo shows them) or per game", "choice",
          choices=["total", "per_game"]),
        F("market_k_games", "Market prior", "games of projection for players with no Yahoo data", "int", 0, 40),
    ]),
    ("Takes", None, [
        F("takes.gap", "Price gap", "model vs Yahoo price before buy low / sell high", "float", 0, 1, 0.01,
          default=_takes_default("gap")),
        F("takes.start_bar", "Worth starting", "x the free-agent bar (buy lows, trade targets)", "float", 0.5, 3,
          0.05, default=_takes_default("start_bar")),
        F("takes.add_bar", "Worth adding", "x the free-agent bar (waiver adds)", "float", 0.5, 3, 0.05,
          default=_takes_default("add_bar")),
        F("takes.stash_bar", "Worth stashing", "x the free-agent bar (injured free agents)", "float", 0.5, 3, 0.05,
          default=_takes_default("stash_bar")),
        F("takes.heat", "Hot / cold", "last 14 days vs his blend", "float", 0, 2, 0.05,
          default=_takes_default("heat")),
        F("takes.rostered_move", "% rostered move", "points in a week worth a mention", "int", 0, 50,
          default=_takes_default("rostered_move")),
        F("takes.top", "Picks per list", "", "int", 1, 30, default=_takes_default("top")),
    ]),
    ("Alerts", None, [
        F("alerts.url", "Phone alerts URL", "ntfy topic or any URL that takes a plain-text POST; empty = off",
          "str"),
    ]),
]
FIELDS = {f["key"]: f for _, _, fs in GROUPS for f in fs}


def _get(d: dict, key: str):
    for part in key.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def _set(d: dict, key: str, value) -> None:
    parts = key.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value


def _delete(d: dict, key: str) -> None:
    parts = key.split(".")
    stack = [d]
    for part in parts[:-1]:
        if part not in stack[-1]:
            return
        stack.append(stack[-1][part])
    stack[-1].pop(parts[-1], None)
    for i in range(len(parts) - 2, -1, -1):      # drop emptied parents
        if not stack[i + 1]:
            stack[i].pop(parts[i], None)


def merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def read_overrides(path: Path | None = None) -> dict:
    p = path or SETTINGS_PATH
    try:
        with open(p) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def write_overrides(over: dict, path: Path | None = None) -> None:
    p = path or SETTINGS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    if not over:
        p.unlink(missing_ok=True)
        return
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w") as f:
        f.write("# Changed in the app (settings gear). Overrides config/inseason.yaml.\n")
        yaml.safe_dump(over, f, sort_keys=True)
    os.replace(tmp, p)


def coerce(field: dict, value):
    """The value as its field's type, within its limits; ValueError if not."""
    t = field["type"]
    if t == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError(f"{field['label']}: on or off")
    if t == "str":
        return str(value or "").strip()
    if t == "choice":
        if value not in field["choices"]:
            raise ValueError(f"{field['label']}: one of {', '.join(field['choices'])}")
        return value
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field['label']}: a number") from None
    if t == "int":
        if num != int(num):
            raise ValueError(f"{field['label']}: a whole number")
        num = int(num)
    if field["min"] is not None and num < field["min"]:
        raise ValueError(f"{field['label']}: at least {field['min']}")
    if field["max"] is not None and num > field["max"]:
        raise ValueError(f"{field['label']}: at most {field['max']}")
    return num


def default_of(base: dict, field: dict):
    v = _get(base, field["key"])
    return field["code_default"] if v is None else v


def describe(base: dict, over: dict) -> list[dict]:
    """GROUPS with each field's default (from inseason.yaml / the code) and
    current value."""
    cur = merge(base, over)
    out = []
    for title, note, fields in GROUPS:
        items = []
        for f in fields:
            d = default_of(base, f)
            v = _get(cur, f["key"])
            items.append({k: f[k] for k in ("key", "label", "help", "type", "min", "max", "step", "choices")}
                         | {"default": d, "value": d if v is None else v,
                            "changed": _get(over, f["key"]) is not None})
        out.append({"title": title, "note": note, "items": items})
    return out


def update(base: dict, over: dict, values: dict) -> dict:
    """New overrides after setting ``values`` ({key: value}; None = back to
    default). A value equal to the default is dropped rather than stored.
    Raises ValueError for unknown keys or bad values (nothing changes)."""
    new = copy.deepcopy(over)
    for key, value in values.items():
        f = FIELDS.get(key)
        if f is None:
            raise ValueError(f"{key}: not a setting")
        if value is None:
            _delete(new, key)
            continue
        v = coerce(f, value)
        if v == default_of(base, f):
            _delete(new, key)
        else:
            _set(new, key, v)
    return new
