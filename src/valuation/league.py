"""League configuration and fantasy value computation."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

from src.paths import DEFAULT_LEAGUE_PATH
from src.projections.projections import Projection

# ─── Valid stat keys and their mapping to Projection attributes ───

VALID_STAT_KEYS: frozenset[str] = frozenset({
    "G", "A", "PPP", "SOG", "HIT", "BLK", "HIT+BLK", "W", "SO", "SV%",
    "SHP", "GS", "GA", "SV",
})

# Alias for backward compatibility / clarity
VALID_CATEGORIES: frozenset[str] = VALID_STAT_KEYS

STAT_FIELD_MAP: dict[str, str] = {
    "G": "goals",
    "A": "assists",
    "PPP": "pp_points",
    "SOG": "shots",
    "HIT": "hits",
    "BLK": "blocks",
    "W": "wins",
    "SO": "shutouts",
    "SV%": "save_pct",
    "SHP": "sh_points",          # shorthanded points (skaters)
    "GS": "games_started",
    "GA": "goals_against",
    "SV": "saves",
}

ROSTER_SLOTS: dict[str, int] = {
    "C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2, "UTIL": 1, "BENCH": 4,
}


# ─── LeagueConfig ───




class LeagueConfig(BaseModel):
    """Validated league configuration loaded from YAML."""

    name: str
    team_count: int
    scoring_type: Literal["h2h_categories", "roto", "points"]
    roster_slots: dict[str, int]
    categories: list[str] | None = None
    points_per_stat: dict[str, float] | None = None
    # ── Windowed (regular vs playoff) valuation ──
    # Optional: a config without any of these is valued season-long only.
    regular_window: tuple[str, str] | None = None
    playoff_window: tuple[str, str] | None = None
    playoff_weight: float = 0.0

    @model_validator(mode="after")
    def _validate(self) -> LeagueConfig:
        if self.scoring_type in ("h2h_categories", "roto"):
            if self.categories is None:
                raise ValueError(
                    f"scoring_type '{self.scoring_type}' requires a 'categories' list"
                )
            self._check_stat_keys(self.categories, "categories")
        elif self.scoring_type == "points":
            if self.points_per_stat is None:
                raise ValueError(
                    "scoring_type 'points' requires a 'points_per_stat' map"
                )
            self._check_stat_keys(list(self.points_per_stat.keys()), "points_per_stat")
        self._validate_window()
        return self

    @staticmethod
    def _check_stat_keys(keys: list[str], field_name: str) -> None:
        invalid = [k for k in keys if k not in VALID_STAT_KEYS]
        if invalid:
            valid_sorted = sorted(VALID_STAT_KEYS)
            raise ValueError(
                f"Invalid {field_name}: {invalid}. Valid names: {valid_sorted}"
            )

    def _validate_window(self) -> None:
        """Validate the schedule-driven valuation window, if present."""
        if (
            self.regular_window is None
            and self.playoff_window is None
        ):
            return  # no window configured: season-long valuation only
        if (
            self.regular_window is None
            or self.playoff_window is None
        ):
            raise ValueError(
                "window config is incomplete: regular_window and "
                "playoff_window must both be set together"
            )
        for name, win in (
            ("regular_window", self.regular_window),
            ("playoff_window", self.playoff_window),
        ):
            if len(win) != 2:
                raise ValueError(
                    f"{name} {win} must be a [start, end] ISO date pair"
                )
            try:
                start = datetime.date.fromisoformat(win[0])
                end = datetime.date.fromisoformat(win[1])
            except (ValueError, IndexError) as e:
                raise ValueError(
                    f"{name} {win} is not a valid ISO date pair "
                    f"(YYYY-MM-DD, YYYY-MM-DD): {e}"
                )
            if start > end:
                raise ValueError(
                    f"{name} {win} must satisfy start <= end"
                )
        reg_start, reg_end = self.regular_window
        po_start, po_end = self.playoff_window
        reg_end_plus_1 = (
            datetime.date.fromisoformat(reg_end)
            + datetime.timedelta(days=1)
        ).isoformat()
        if po_start != reg_end_plus_1:
            raise ValueError(
                f"playoff_window {self.playoff_window} must start the day "
                f"after regular_window ends (expected start = {reg_end_plus_1}, "
                f"got {po_start})"
            )
        if not 0.0 <= self.playoff_weight <= 1.0:
            raise ValueError(
                f"playoff_weight {self.playoff_weight} must be in [0, 1]"
            )


# ─── load_league ───

# Default league config (kyle_12_points) — used when load_league() is called
# without an explicit path (e.g. from the API / CLI).
DEFAULT_LEAGUE: str = str(DEFAULT_LEAGUE_PATH)


def load_league(path: str | None = None) -> LeagueConfig:
    """Load and validate a league config from a YAML file.

    When ``path`` is omitted, the default league
    (``config/leagues/kyle_12_points.yaml``) is loaded.
    """
    p = Path(path) if path is not None else Path(DEFAULT_LEAGUE)
    raw = yaml.safe_load(p.read_text())
    return LeagueConfig.model_validate(raw)


# ─── Eligibility ───


def eligible_for(p: Projection, slot: str, position: str) -> bool:
    """Determine if a player is eligible for a roster slot.

    Rules:
    - Everyone (except goalies) is UTIL-eligible.
    - Goalies (position G) are only eligible for the G slot.
    - Otherwise, only the slot matching their position is eligible.
      C -> C, L -> LW, R -> RW, D -> D.
    """
    if position == "G":
        return slot == "G"
    if slot == "UTIL":
        return True
    # Position-specific slot
    pos_slot_map = {"C": "C", "L": "LW", "R": "RW", "D": "D"}
    return pos_slot_map.get(position) == slot


# ─── Stat extraction helper ───


def _extract_stat(p: Projection, key: str) -> float:
    """Extract a stat value from a Projection by key name."""
    if key == "HIT+BLK":
        return p.hits + p.blocks
    attr = STAT_FIELD_MAP[key]
    val = getattr(p, attr, None)
    return val if val is not None else 0.0


# ─── category_vector ───


def category_vector(p: Projection, cfg: LeagueConfig) -> dict[str, float]:
    """Return the category vector for a projection under a category/roto league.

    For compound categories (e.g. HIT+BLK), the value is the sum of the
    underlying stats. For all others it is the single stat value.
    Z-scoring is intentionally NOT performed here.
    """
    if cfg.categories is None:
        raise ValueError(
            f"League '{cfg.name}' has no categories (scoring_type={cfg.scoring_type})"
        )
    return {cat: _extract_stat(p, cat) for cat in cfg.categories}


# ─── points_value ───


def points_value(p: Projection, cfg: LeagueConfig) -> float:
    """Compute total fantasy points for a projection under a points league.

    value = sum(stat * weight) for each (stat, weight) in points_per_stat.
    """
    if cfg.points_per_stat is None:
        raise ValueError(
            f"League '{cfg.name}' has no points_per_stat (scoring_type={cfg.scoring_type})"
        )
    return sum(_extract_stat(p, stat) * weight for stat, weight in cfg.points_per_stat.items())