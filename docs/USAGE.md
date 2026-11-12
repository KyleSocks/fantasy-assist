# Usage Guide

## 1. What this is

A local-first, single-user draft assistant for NHL fantasy hockey. It pulls historical skater, goalie, schedule, and roster data from the NHL Stats API into data\hockey.db, projects next-season per-category totals, and values them under a YAML league config as VORP and auction dollars, served by a FastAPI app with a draft-board UI. It does not do live scoring, league sync, or an ADP feed.

## 2. First-time setup

uv sync creates the .venv and installs the dependencies pinned in pyproject.toml and uv.lock.

```
uv sync
```

Confirm the environment with the test suite.

```
uv run pytest
```

Then run the initial ingest. It pulls four seasons of skater, goalie, schedule, and roster data from the NHL Stats API and writes data\hockey.db.

```
uv run python -m src.ingest.ingest --seasons 4
```

Measured on this machine, a full 4-season ingest takes about 5.5 minutes (327 s) and leaves data\hockey.db at about 0.95 MB (991,232 bytes): three completed seasons (2023-24 through 2025-26), roughly 1,300 players, and the upcoming 2026-27 schedule.

## 3. Refreshing data

The refresh command, which is also printed in the freshness payload.

```
uv run python -m src.ingest.ingest --seasons 1
```

--dry-run fetches and counts but writes nothing. It prints the planned row counts per table for the first season only.

```
uv run python -m src.ingest.ingest --dry-run
```

Rerun at least once a day. The stale banner is driven by STALE_THRESHOLD_HOURS = 24.0 in src\api\routes.py: stale becomes true when the newest ingest is older than 24 hours. The timestamp is run_at, read as MAX(run_at) from the ingest_log table in data\hockey.db, which log_ingest() in src\ingest\models.py writes on every successful ingest. A database with no ingest_log rows is also reported stale. The /api/board payload carries this state as data_freshness, with run_at, data_age_hours, stale, and refresh_command.

## 4. Configuring your league

The shipped default, config\leagues\kyle_12_points.yaml, line by line.

```yaml
name: kyle_12_points
team_count: 12
scoring_type: points
roster_slots:
  C: 2
  LW: 2
  RW: 2
  D: 4
  G: 2
  UTIL: 1
  BENCH: 5
points_per_stat:
  G: 5
  A: 3
  PPP: 1
  SHP: 2
  SOG: 0.25
  BLK: 0.75
  HIT: 0.5
  GS: 1
  W: 4
  GA: -1.5
  SV: 0.3
  SO: 3
```

- name (string): the league key used by the client and the ?league= parameter. For the default league it must equal the file name stem.
- team_count (int): the number of teams in the league. Drives pool sizes, replacement level, and total auction budget.
- scoring_type: one of h2h_categories, roto, points. h2h_categories and roto require categories; points requires points_per_stat.
- roster_slots (map of slot to int): slot keys used are C, LW, RW, D, G, UTIL, BENCH.
- categories (list, required for h2h_categories and roto): each entry must be one of the valid stat keys G, A, PPP, SOG, HIT, BLK, HIT+BLK, W, SO, SV%, SHP, GS, GA, SV. HIT+BLK sums hits and blocks.
- points_per_stat (map of stat key to float, required for points): keys from the same valid set; weights may be negative, as with GA: -1.5.

For a custom league, copy config\leagues\kyle_12_points.yaml to a new file in config\leagues and change name to your league key plus the scoring values above to match your league. Every *.yaml in that directory is loaded and selectable on each request. To make the new file the default served when ?league= is absent, point DEFAULT_LEAGUE in src\valuation\league.py at it.

## 5. Running it

Start the API.

```
uv run uvicorn src.api.main:app --reload
```

Open http://127.0.0.1:8000, the uvicorn default port. The / route serves static\index.html.

With no query string the board loads config\leagues\kyle_12_points.yaml, because DEFAULT_LEAGUE in src\valuation\league.py points at that file. The client reads ?league= from the URL and the header dropdown sets it. The API accepts any loaded config name: kyle_12_points, points_10, roto_14, standard_12_cat.

## 6. Using it during a draft

Draft picks: every board row has a fixed leftmost Draft column with two text buttons, Me and Taken. Me adds the player to your roster (capped at the total roster slots from the league config); Taken records the player as drafted by someone else. Either button removes the player from the board entirely and renumbers the ranks so the best remaining player is always rank 1, and both are logged so every pick is undoable.

Read the board: the columns are Rank, Draft, Player, Pos, Team, VORP, $, then one column per scoring stat (for a points league, the keys of points_per_stat). ADP and +/- columns appear only when data\adp.csv exists. Sort, filter, and search apply to available players only: click a column header to sort, use the position chips and search box to filter, and read the status line for available player count, season, drafted count, and current round.

The left sidebar has three tabs. Recommendations (active on load) shows the top three players by marginal value from GET /api/recommend, each card with the player, position, marginal value, a one-line reason, and its own Me / Taken buttons. It refetches after every draft and undo; the previous three stay visible and dim while the request is in flight, so the panel is never blank. The API takes the league, the current round, and comma-separated drafted_by_me and drafted_all player ids; the route docstring in src\api\routes.py shows ?league=roto_14&round=3&drafted_by_me=101,202&drafted_all=101,202,303. My Roster fills the league config's slots (C, LW, RW, D, G, UTIL, BENCH) with your Me picks only, and holds the paste box: paste or type a full name and press Enter to draft the best fuzzy match, or click one of the suggestions under the input. The Me / Taken toggle above the input picks the destination and defaults to Taken. If nothing matches, the status line shows No match found. Drafted lists every pick newest-first, each tagged Me or Taken, each with its own Undo button.

Undo and reset: per-entry Undo buttons on the Drafted tab, Ctrl+Z for the most recent pick, and the Reset button in the header to clear the whole draft. Draft state persists in localStorage as {version: 2, drafted_by_me, drafted_all, log}; any state without version 2 or that cross-checks wrong against its log (for example a duplicated pick) is discarded and the draft starts clean.

The round readout is floor(drafted_all / team_count) + 1, with team_count read from the /api/board payload (12 for kyle_12_points, 14 for roto_14).

Open a player's detail panel: a row click opens the right panel; draft-button clicks do not, because they are explicit controls and stop propagation. It fetches GET /api/player/{player_id} for the selected league and renders a per-season trend chart with a confidence band ending at the proj point, one tab per category, a breakdown table of the projection terms, and the team's upcoming schedule load. Close it with the x button or Escape.

## 7. Reading the numbers

VORP is value over replacement in the league's currency. For a points league it is projected fantasy points minus the replacement level for the position group. For a category or roto league it is the sum of per-category z-scores minus that replacement level. It is not points per game and not a per-60 rate.

The $ column is a VORP-proportional auction value. Total spend across the board equals team_count times 200, and only players with positive VORP receive dollars; everyone else is $0.0.

Replacement level moves with team_count because it is defined as the raw value of the (pool_size + 1)th player in each position group, and the pool sizes are team_count times the starter slots plus the UTIL and BENCH allocation. More teams mean a larger pool and a deeper, cheaper replacement player, so the bar falls and the same player's VORP rises. The same ingested data shows different VORP in a 10-team league than in a 14-team league.

The projection point on the detail chart is a per-60 rate for skaters, not a season total. Season totals are not comparable across players with different ice time, so the chart values are each season's total and the projection scaled by _per60() (value divided by games played, times 60) in src\api\player_detail.py. The raw season totals and the projection total remain in the payload and in the board's stat columns. Goalie chart values are per-season values, with save_pct as a fraction.

## 7b. Testing and tuning the projections

Season stats are regular season only (`gameTypeId=2`). A database ingested
before that fix has playoff games folded into every season row, so
re-ingest once, with enough history for the backtest and age fitting:

```
uv run python -m src.ingest.ingest --seasons 10
```

Walk-forward backtest, scored in your league's fantasy points on the
players you'd draft (top 250 skaters, top 64 goalies):

```
uv run python -m src.projections.walkforward --db data/hockey.db
uv run python -m src.projections.walkforward --grid goals=150,200,300 --grid rookie_ramp_gp=100,200,300
```

It splits each miss into a per-game rate part and a games-played part,
compares against "repeat last season", and prints residual SD by draft
tier. `--set key=value` and `--grid key=v1,v2` override
`config/regression.yaml` without editing it.

Fit per-stat aging curves from your data and paste the output into
`config/regression.yaml`:

```
uv run python -m src.projections.age_fit --db data/hockey.db
```

## 8. Exporting

GET /api/export?format=csv returns a flat CSV, one row per player. The header is id,name,pos,team,rank,vorp,dollars followed by one column per category in the board's category order. Missing stats become empty cells, and stats are already rounded to one decimal by the board. The league parameter is respected exactly like /api/board, and rank is the board's VORP rank, not any client-side sort or filter. The ADP columns are not in the CSV. format=json returns the full board payload; any other format value returns 422.

## 9. Troubleshooting

| Symptom | Fix |
| --- | --- |
| Ingest exits with ERROR: network failure | Check connectivity, then rerun uv run python -m src.ingest.ingest --seasons 1. Endpoint, timeout, and paging live in src\ingest\models.py. |
| Board fails with Database not ready: no ingested skater data | Run uv run python -m src.ingest.ingest --seasons 1 and confirm data\hockey.db has skater_seasons rows. |
| Detail panel shows No projection data for this player | Rerun uv run python -m src.ingest.ingest --seasons 1. If it persists, check skater_seasons and goalie_seasons in data\hockey.db for that player_id. |
| data_freshness in the /api/board payload reports stale: true | Run the refresh_command it contains (uv run python -m src.ingest.ingest --seasons 1) so run_at in ingest_log updates. |

## 10. Known limits

- No ADP unless you supply data\adp.csv with full_name and adp columns.
- Projections are rate-based: per-GP and per-60 rates regressed toward positional means. They will miss role changes beyond projected games and TOI.
- The NHL Stats endpoints are undocumented and may break without notice; ingest then fails until src\ingest is updated.
