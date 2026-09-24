# Fantasy Hockey Draft Assistant

NHL fantasy hockey draft assistant. Local-first, single-user.

## Stack

- **Python 3.11** with `uv` for dependency management
- **SQLite** via `sqlite3` stdlib (no ORM)
- **pandas + numpy** for numerical computation
- **FastAPI + uvicorn** for the API
- **pytest** for tests
- **Frontend:** Vite + React + TypeScript, Tailwind, Recharts. No component library. No state manager beyond React context.

## Layout

```
data/raw/          downloaded source files, gitignored
data/hockey.db     sqlite
src/ingest/        one module per data source
src/projections/   projection model
src/valuation/     z-scores, replacement level, VORP, $ values
src/draft/         live draft state + recommendation engine
src/api/           fastapi app
tests/
web/               vite app
```

## Getting Started

```bash
# install dependencies
uv sync

# run tests
uv run pytest

# run the API (once modules are in place)
uv run uvicorn src.api.main:app --reload
```