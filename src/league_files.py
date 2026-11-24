"""Where league configs live.

Hand-written ones in config/leagues/, and ones read from a Yahoo league's
settings page in data/league_configs/ (src/draft/yahoo_settings.py). A
name in both resolves to config/leagues/ -- your own file always wins.

Each function takes the config folder as ``primary`` so callers (and tests
that point a module's LEAGUE_DIR elsewhere) keep control of it.
"""
from __future__ import annotations

from pathlib import Path

from src import paths


def league_paths(primary: Path) -> list[Path]:
    """Every league config file, sorted by name, primary folder first."""
    found: dict[str, Path] = {}
    for folder in (Path(primary), paths.IMPORTED_LEAGUE_DIR):
        if folder.is_dir():
            for p in sorted(folder.glob("*.yaml")):
                found.setdefault(p.stem, p)
    return [found[k] for k in sorted(found)]


def league_path(name: str, primary: Path) -> Path:
    """The config file for ``name`` (the primary folder's path when neither
    has it, so callers' "missing file" handling stays the same)."""
    own = Path(primary) / f"{name}.yaml"
    if own.exists():
        return own
    imported = paths.IMPORTED_LEAGUE_DIR / f"{name}.yaml"
    return imported if imported.exists() else own
