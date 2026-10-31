"""ASGI entry point: uv run uvicorn src.api.main:app

routes.py defines the draft-board app; the in-season router is mounted here
so routes.py stays under the 500-line limit.
"""
from src.api.inseason import router as inseason_router
from src.api.routes import app  # noqa: F401

app.include_router(inseason_router)
