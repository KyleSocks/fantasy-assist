"""League configuration and fantasy value computation."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

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
    "SHP": "shutouts",
    "GS": "games_started",
    "GA": "goals_against",
    "SV": "saves",
}

ROSTER_SLOTS: dict[str, int] = {
    "C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2, "UTIL": 1, "BENCH": 4,
}


# ─── LeagueConfig ───

_DOW_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class LeagueConfig(BaseModel):
    """Validated league configuration loaded from YAML."""

    name: str
    team_count: int
    scoring_type: Literal["h2h_categories", "roto", "points"]
    roster_slots: dict[str, int]
    categories: list[str] | None = None
    points_per_stat: dict[str, float] | None = None
    # ── Windowed (regular vs playoff week) valuation ──
    # Optional: a config without any of these is valued season-long only.
    fantasy_start_date: str | None = None
    week_start_dow: int = 1
    regular_weeks: list[int] | None = None
    playoff_weeks: list[int] | None = None
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
            self.fantasy_start_date is None
            and self.regular_weeks is None
            and self.playoff_weeks is None
        ):
            return  # no window configured: season-long valuation only
        if (
            self.fantasy_start_date is None
            or self.regular_weeks is None
            or self.playoff_weeks is None
        ):
            raise ValueError(
                "window config is incomplete: fantasy_start_date, "
                "regular_weeks and playoff_weeks must all be set together"
            )
        try:
            start = datetime.date.fromisoformat(self.fantasy_start_date)
        except ValueError as e:
            raise ValueError(
                f"fantasy_start_date '{self.fantasy_start_date}' is not a valid "
                f"ISO date (YYYY-MM-DD): {e}"
            )
        if not 1 <= self.week_start_dow <= 7:
            raise ValueError(
                f"week_start_dow {self.week_start_dow} is out of range 1-7 "
                "(1 = Monday)"
            )
        if start.weekday() != self.week_start_dow - 1:
            raise ValueError(
                f"fantasy_start_date {self.fantasy_start_date} falls on a "
                f"{_DOW_NAMES[start.weekday()]} but week_start_dow="
                f"{self.week_start_dow} declares the window to start on "
                f"{_DOW_NAMES[self.week_start_dow - 1]}"
            )
        if len(self.regular_weeks) != 2 or len(self.playoff_weeks) != 2:
            raise ValueError(
                "regular_weeks and playoff_weeks must each be a [first, last] "
                "inclusive week range"
            )
        reg_lo, reg_hi = self.regular_weeks
        po_lo, po_hi = self.playoff_weeks
        if not (1 <= reg_lo <= reg_hi):
            raise ValueError(
                f"regular_weeks {self.regular_weeks} must satisfy 1 <= first <= last"
            )
        if not (po_lo <= po_hi):
            raise ValueError(
                f"playoff_weeks {self.playoff_weeks} must satisfy first <= last"
            )
        if po_lo != reg_hi + 1:
            raise ValueError(
                f"playoff_weeks {self.playoff_weeks} must start the week after "
                f"regular_weeks ends (expected first = {reg_hi + 1}, got {po_lo})"
            )
        if not 0.0 <= self.playoff_weight <= 1.0:
            raise ValueError(
                f"playoff_weight {self.playoff_weight} must be in [0, 1]"
            )


# ─── load_league ───

# Default league config (kyle_12_points) — used when load_league() is called
# without an explicit path (e.g. from the API / CLI).
DEFAULT_LEAGUE: str = str(
    Path(__file__).resolve().parent.parent.parent
    / "config" / "leagues" / "kyle_12_points.yaml"
)


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