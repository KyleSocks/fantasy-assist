PROJECT: fantasy hockey draft assistant (NHL), local-first, single user.

HARD RULES — violating any of these means the output is rejected:
1. Write files to disk via the filesystem tool. Never paste a full
   file into chat.
2. No file over 500 lines. If you're heading past that, stop and
   propose a split.
3. No invented data. No placeholder player arrays, no mock stats, no
   `TODO: fetch real data`. If the data isn't available yet, the task
   is blocked — say so and stop.
4. No new dependency without naming it and why. Stack is fixed below.
5. Every module that computes a number ships with a pytest file that
   asserts against hand-computed expected values.
6. If a requirement is ambiguous, ask ONE question and stop. Do not
   guess and proceed.

STACK (fixed, do not substitute):
- Python 3.11, uv for deps
- SQLite via sqlite3 stdlib (no ORM)
- pandas + numpy for the math
- FastAPI + uvicorn for the API
- pytest for tests
- Frontend: Vite + React + TypeScript, Tailwind, Recharts.
  No component library. No state manager beyond React context.

REPO LAYOUT (create exactly this, nothing else):
  data/raw/          downloaded source files, gitignored
  data/hockey.db     sqlite
  src/ingest/        one module per data source
  src/projections/   projection model
  src/valuation/     z-scores, replacement level, VORP, $ values
  src/draft/         live draft state + recommendation engine
  src/api/           fastapi app
  tests/
  web/               vite app

DEFINITION OF DONE for any task: pytest passes, the module runs from
CLI on real ingested data, and you report the actual numbers it
produced — not a description of them.