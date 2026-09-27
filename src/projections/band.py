"""Confidence band for a projected per-category total.

Expresses the uncertainty of a projection as a half-width ``hw`` around the
projected total ``final``:

    hw = pop_std(season_actuals) + shrink
    band = [final - hw, final + hw]

``pop_std`` is the population standard deviation (ddof=0) of the per-season
actuals. ``shrink`` is the regression shrinkage, which depends on the player
type and category:

- Skater (any category): ``abs(raw - regressed) * age_adj * proj_gp``
- Goalie save_pct (a rate): ``abs(raw - regressed)``
- Goalie count category (no per-season rate regression): ``0``

For count categories with a single season (or no regression terms), ``hw`` is
0 and the band degenerates to ``[final, final]``.
"""
from __future__ import annotations


def pop_std(values: list[float]) -> float:
    """Population standard deviation (ddof=0). Returns 0.0 if fewer than 2 values."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return (sum((v - mean) ** 2 for v in values) / n) ** 0.5


def compute_band(
    final: float,
    season_actuals: list[float],
    raw: float | None,
    regressed: float | None,
    age_adj: float,
    proj_gp: float,
    category: str,
    is_goalie: bool,
) -> tuple[float, float]:
    """Return the ``(lo, hi)`` confidence band around a projected total.

    ``final`` is the projected total for ``category``. ``season_actuals`` are
    the per-season actuals (oldest->newest, up to 3 seasons). ``raw`` and
    ``regressed`` are the raw and regressed per-season rates for the category
    (``None`` for goalie count categories, which have no rate regression).
    """
    sigma = pop_std(season_actuals)
    if is_goalie and category != "save_pct":
        # Goalie count categories (wins, shutouts, ...): no rate regression.
        shrink = 0.0
    elif raw is None or regressed is None:
        shrink = 0.0
    else:
        # Skater categories scale with the age adjustment and projected
        # games; goalie save_pct is a rate (scale 1.0).
        scale = 1.0 if is_goalie else age_adj * proj_gp
        shrink = abs(raw - regressed) * scale
    hw = sigma + shrink
    return (final - hw, final + hw)
