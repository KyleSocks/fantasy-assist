# Fantasy Hockey Draft Assistant

NHL fantasy hockey draft assistant. Local-first, single-user.

## Stack

- **Python 3.11** with `uv` for dependency management
- **SQLite** via `sqlite3` stdlib (no ORM)
- **numpy** for numerical computation
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
- **Rest of season** runs to the end of your fantasy playoffs (`playoff_window` in the
  league yaml), not the NHL's last day, and a playoff game counts `playoff_game_weight`
  times (inseason.yaml, default 2).
- **Trades** are valued as the change in your best nightly lineup over that span, not a
  sum of player values: a player who'd sit on your bench adds nothing, and one below
  replacement never starts (so dumping him on someone gains them nothing). The market
  view ("They'll see") floors each player at 0 for the same reason.
- **Replacement** is the best healthy free agent in your league per group (from
  `data/fa.txt`, written by Refresh), not a formula; a trade that opens a roster spot
  fills it with the best free agent for your lineup.
- **Skaters' availability**: value per team game = per game played x the share of
  games he plays (projected durability blended with this season, `play_share_k_games`).
- **Goalies**: saves and goals against both come from blended shots against x blended
  save % (1000-shot prior), so a hot or cold stretch doesn't swing GA on its own.
- **Market view** ("They'll see") uses season-total Fan Pts, as Yahoo's trade screen
  shows them (`market_points` in inseason.yaml).

**GUI:** run the server (`uv run uvicorn src.api.main:app`, or `server-start.bat`) and open
http://127.0.0.1:8000/inseason -- tabs for the roster audit, the streaming plan, free
agents + tonight's streams (the Roster tab has a Team picker to view any team's roster), the league (team strengths and trades), **Moves** (every
team's adds, drops, trades and IR moves), the schedule grid, and editing your roster /
free-agent lists. The **Refresh data** button re-ingests this season's stats, rosters and
schedule and rewrites the selected league's roster, free-agent and league files from its
public Yahoo league. Takes about a minute.

**Matchup tab (points leagues):** this week's head-to-head from the scores Yahoo showed at the
last Refresh plus each team's best nightly lineup for the days left: projected finals, win
chance, night by night, every matchup in the league, and "swing adds" (free agents who'd
raise your total most, replacing your weakest non-HOLD player). Model: `src/inseason/matchup.py`.

**Lines, PP units and luck:** Refresh also reads NHL shift charts for each team's last five
games to place every skater on a line (F1-F4 / D1-D3) and PP unit, plus his ice time over
the last week vs the three before (`src/inseason/lines.py`); promotions and demotions are
flagged in every table (Role column, "Just promoted" filter on Free agents). Shooting and
save % far from a player's usual are flagged as running hot / due to bounce back
(`src/inseason/luck.py`); a cold player is never marked a drop candidate.

**What feeds the in-season projection** (`src/inseason/model.py`, `deploy.py`): each stat
blends the preseason projection with this season; goals are blended shots x blended
shooting % (shooting % needs ~375 shots to count fully, so finishing luck washes out);
expected ice time from here on (season, last 7 days, last game's PP time) scales PPP,
goals/assists and shots; goalies' start chance drops on the second night of a
back-to-back. Everything downstream (verdicts, plan, trades, matchup) uses these rates.
Each player page lists what moved his projection.

**Roster moves:** every Refresh stores each team's roster; comparing it with the previous
one gives the adds, drops, claims (dropped by one team, picked up by another), trades and
IR moves in between, appended to `roster_moves.json`. Moves are only seen at refreshes,
so a player added and dropped again between two of them won't show. Endpoint:
`/api/inseason/moves?days=30&league=<config>`.

**More than one league:** every config in `config/leagues/` shows up in the mid-season
league picker. Give a config its own Yahoo league with a `yahoo:` block:

```yaml
# config/leagues/work_league.yaml
yahoo:
  league_id: 41234   # hockey.fantasysports.yahoo.com/hockey/<league_id>/<team_id>
  team_id: 7
  free_agent_pages: 0
team_name: Office Grinders   # optional; default: your team's name on Yahoo
```

A league with its own block keeps its files in `data/leagues/<config name>/`
(`my_roster.txt`, `fa.txt`, `league_rosters.json`, `market.json`,
`roster_moves.json`). A league without one follows `yahoo:` in `config/inseason.yaml`
and shares the files in `data/`. The Refresh button pulls the selected league; the
twice-daily timer (no `league`) pulls the default league and every league with its own
block.

API (same data, for the UI): `/api/inseason/streams`, `/api/inseason/player/{id}`,
`/api/inseason/schedule`.

**Trade with a team** (League tab): pick a partner and get every 1-1, 2-1, 1-2 and 2-2
package you win by your model (rest of season above replacement), with how it looks to
them by Yahoo's market numbers and each lineup's fit over the window; tick players on
both sides to score an offer you received. Taking back more players than you send cuts
your worst. Endpoint: `/api/inseason/partner-trades?partner=<team id>&give=ids&get=ids`.

## Running on a home server

`docker compose up -d --build` runs the whole app (draft board + mid-season
page) on port 8420, with a systemd timer for twice-daily refreshes. See
[docs/DEPLOY.md](docs/DEPLOY.md).
