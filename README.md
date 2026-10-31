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
src/inseason/      mid-season assistant (ingest, blend, streams, hold check)
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

## Mid-season assistant

Season-to-date stats blended with the preseason projection, used to answer
"who do I stream tonight?" and "is this guy worth holding through a light
schedule?". Lives in `src/inseason/`; the draft board is untouched.

```bash
# daily refresh: this season's regular-season stats, rosters, schedule
uv run python -m src.inseason.ingest

# single-game streams for a date (default: today), opponent-adjusted
uv run python -m src.inseason streams --date 2026-11-01
uv run python -m src.inseason streams --group G            # goalies only
uv run python -m src.inseason streams --taken data/taken.txt  # exclude your league's rostered players

# hold / drop check over the next N days (several names = side-by-side)
uv run python -m src.inseason player "Ovechkin" --days 10
uv run python -m src.inseason player "Ovechkin" "Some Waiver Guy" --days 10

# games per team in the next N days, off-nights, opponent strength
uv run python -m src.inseason schedule --days 10
```

All commands take `--league <yaml stem>` (default `kyle_12_points`).
`data/taken.txt` is optional: one player name per line (`#` comments); without
it, "available" means ranked outside the league's rosterable pool, which is
only an approximation.

How the numbers are made (tunable in `config/inseason.yaml`):

- **Blended rate** per stat = `(preseason_rate * K + season_total) / (K + games)`.
  K is in games (starts for goalies). Shots/hits/blocks trust this season fast,
  goals/shutouts slowly. Goalies also blend their share of team starts.
- **Opponent adjustment** for a single game: goals/assists/PPP scale with the
  opponent's goals-against per game, shots with its shots-against (goalies:
  GA with opponent goals-for, saves with opponent shots-for), each regressed to
  the league mean.
- **Value** uses the league yaml: fantasy points for points leagues; per-game
  z-units for category leagues (counting cats uncentered so games add up).
- **Replacement** = per-game value of the (pool size + 1)th player at F / D / G.
  The hold check compares the player's next-N-days value with a replacement
  player on a league-average schedule, plus the same over the rest of the
  season, and gives HOLD / BORDERLINE / DROP CANDIDATE.

**GUI:** run the server (`uv run uvicorn src.api.main:app`, or `server-start.bat`) and open
http://127.0.0.1:8000/inseason -- tabs for the roster audit, the streaming plan, free
agents + tonight's streams, the schedule grid, and editing your roster / free-agent lists.
The **Refresh data** button re-ingests this season's stats, rosters and schedule and
rewrites `data/my_roster.txt` / `data/fa.txt` from your public Yahoo league
(`yahoo.league_id` / `team_id` in `config/inseason.yaml`; top 100 free agents by Yahoo
rank, IR and goalies excluded). Takes about a minute.

API (same data, for the UI): `/api/inseason/streams`, `/api/inseason/player/{id}`,
`/api/inseason/schedule`.
