"""Filesystem locations and shared commands used across the app.

Every module used to recompute ``Path(__file__).resolve().parents[2]`` and
hard-code ``data/hockey.db``; they now import from here instead.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "hockey.db"
ADP_PATH = DATA_DIR / "adp.csv"

CONFIG_DIR = ROOT / "config"
LEAGUE_DIR = CONFIG_DIR / "leagues"
REGRESSION_CONFIG_PATH = CONFIG_DIR / "regression.yaml"
DEFAULT_LEAGUE_PATH = LEAGUE_DIR / "kyle_12_points.yaml"

SCHEMA_PATH = ROOT / "db" / "schema.sql"
STATIC_DIR = ROOT / "static"

# Relative default for CLI flags (``--db``); resolved against the CWD.
DEFAULT_DB_ARG = "data/hockey.db"

# Shown to the user whenever the DB is missing or stale.
INGEST_COMMAND = "uv run python -m src.ingest.ingest --seasons 1"
