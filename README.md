# Fantasy Hockey Draft Assistant

NHL fantasy hockey draft assistant. Local-first, single-user.

## Stack

- **Python 3.11** with `uv` for dependency management
- **SQLite** via `sqlite3` stdlib (no ORM)
- **pandas + numpy** for numerical computation
- **FastAPI + uvicorn** for the API
- **pytest** for tests
- **Frontend:** plain HTML/CSS/JS served by FastAPI from `static/` (`board.js`, `detail.js`), with uPlot (vendored in `static/vendor/`) for the trend charts. `web/` is reserved for a future Vite app and is currently empty.

## Layout

```
data/raw/          downloaded source files, gitignored
data/hockey.db     sqlite
src/paths.py       shared filesystem paths + ingest command
src/db.py          small shared DB lookups
src/ingest/        one module per data source
src/projections/   projection model (common / skaters / goalies / goalie_allocation; projections.py is the entry point)
src/valuation/     z-scores, replacement level, VORP, $ values
src/draft/         live draft state + recommendation engine
src/api/           fastapi app
tests/
static/            draft-board UI (served at /)
tools/             diagnostics (uv run python -m tools.diag, node tools/e2e_client_check.mjs)
web/               reserved for a future Vite app (empty)
```

## Getting Started

```bash
# install dependencies
uv sync

# run tests
uv run pytest

# populate data/hockey.db (needed by most tests and the app)
uv run python -m src.ingest.ingest --seasons 4

# run the API + board at http://127.0.0.1:8000
uv run uvicorn src.api.main:app --reload
```