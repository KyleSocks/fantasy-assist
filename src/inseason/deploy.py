"""Deployment in the projection: ice time from here on, and the rates tied
to it.

Inputs per skater (all per game played):
  season   power-play and even-strength TOI so far (skater_current)
  recent   the same over the last 7 days (skater_toi_window 'recent')
  last     power-play seconds in his last game (game_deployment, shifts)

Expected from here on, each a weighted mean with ``k_games`` of season:

  pp = (season_pp * k + recent_pp * recent_gp + last_pp * w) / (k + recent_gp + w)
  ev = (season_ev * k + recent_ev * recent_gp) / (k + recent_gp)

(w = last_game_weight; the last-game term only when shift data has it.)
Factors = expected / season, clamped to [min_factor, max_factor]:

  pp_points            x pp factor
  goals and assists    their power-play share scales with the pp factor,
                       the rest with the ev factor (the PP share of each
                       is his pp_points share of his points)
  shots                x the factor for his total EV + PP time
  hits, blocks         unchanged (role on the ice, not minutes)

A player with no season TOI (or no window data) keeps factor 1. The change
in each stat is kept in Line.extra["deploy"] for the player page.
"""
from __future__ import annotations

from .model import Line


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def expected_toi(season: float, recent: float | None, recent_gp: int,
                 k: float, last: float | None = None, w: float = 0.0) -> float:
    num, den = season * k, k
    if recent is not None and recent_gp:
        num += recent * recent_gp
        den += recent_gp
    if last is not None and w:
        num += last * w
        den += w
    return num / den


def factors(season_pp: float, season_ev: float, role: dict | None,
            cfg: dict) -> dict | None:
    """{"pp", "ev", "toi", "exp_pp", "exp_ev"} or None when nothing is
    known beyond the season averages."""
    if not role or (role.get("recent_gp") is None and role.get("pp_sec") is None):
        return None
    k = float(cfg.get("k_games", 6))
    lo, hi = float(cfg.get("min_factor", 0.6)), float(cfg.get("max_factor", 1.6))
    rgp = int(role.get("recent_gp") or 0)
    last_pp = role.get("pp_sec") if role.get("games") else None
    exp_pp = expected_toi(season_pp, role.get("recent_pp_toi"), rgp, k,
                          last_pp, float(cfg.get("last_game_weight", 2)))
    recent_ev = role.get("recent_ev_toi")
    exp_ev = expected_toi(season_ev, recent_ev, rgp, k)
    f_pp = _clamp(exp_pp / season_pp, lo, hi) if season_pp > 0 else 1.0
    f_ev = _clamp(exp_ev / season_ev, lo, hi) if season_ev > 0 else 1.0
    tot = season_pp + season_ev
    f_toi = _clamp((exp_pp + exp_ev) / tot, lo, hi) if tot > 0 else 1.0
    return {"pp": f_pp, "ev": f_ev, "toi": f_toi, "exp_pp": exp_pp, "exp_ev": exp_ev}


def adjust(line: Line, f: dict) -> dict[str, float]:
    """Scale ``line.blended`` in place; returns the change per stat."""
    b = line.blended
    before = dict(b)
    pts = b["goals"] + b["assists"]
    pp_share = min(b["pp_points"] / pts, 1.0) if pts > 0 else 0.0
    for s in ("goals", "assists"):
        b[s] = b[s] * (pp_share * f["pp"] + (1 - pp_share) * f["ev"])
    b["pp_points"] *= f["pp"]
    b["shots"] *= f["toi"]
    return {s: b[s] - before[s] for s in ("goals", "assists", "pp_points", "shots")}


def apply(lines: dict[int, Line], roles: dict[int, dict],
          season_rows: dict[int, dict], cfg: dict) -> None:
    """Adjust every skater's blended rates for his deployment (see above)."""
    if not cfg:
        return
    for pid, ln in lines.items():
        if ln.is_goalie:
            continue
        row = season_rows.get(pid) or {}
        gp = row.get("gp") or 0
        if not gp:
            continue
        season_pp = (row.get("pp_toi_sec") or 0) / gp
        season_ev = (row.get("toi_sec") or 0) / gp
        ln.extra["deploy_base"] = {"season_pp": season_pp, "season_ev": season_ev}
        f = factors(season_pp, season_ev, roles.get(pid), cfg)
        if f is None:
            continue
        delta = adjust(ln, f)
        ln.extra["deploy"] = {**f, "season_pp": season_pp, "season_ev": season_ev,
                              "delta": delta}
