"""Uvicorn entry point: `uv run uvicorn src.api.main:app --reload`.

The FastAPI app object lives in src/api/routes.py; this module only
re-exports it so the README command works.
"""
from src.api.routes import app  # noqa: F401
