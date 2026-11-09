"""ASGI entry point: uv run uvicorn src.api.main:app

routes.py defines the draft-board app; the in-season router and page are
mounted here so routes.py stays under the 500-line limit.
"""
from fastapi.responses import FileResponse

from src.api.inseason import router as inseason_router
from src.api.routes import STATIC_DIR, app  # noqa: F401
from src.api.security import SecurityMiddleware

app.include_router(inseason_router)
app.add_middleware(SecurityMiddleware)


@app.get("/inseason")
def inseason_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "inseason.html",
                        headers={"Cache-Control": "no-store"})
