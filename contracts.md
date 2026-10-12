# contracts.md — Schema DDL and Public API

## DDL (db/schema.sql)

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS players (
    player_id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL,
    position TEXT,
    shoots TEXT,
    birth_date TEXT,
    height_in INTEGER,
    weight_lb INTEGER,
    current_team_abbrev TEXT,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS skater_seasons (
    player_id INTEGER NOT NULL,
    season_id INTEGER NOT NULL,
    team_abbrev TEXT NOT NULL,
    gp INTEGER,
    goals INTEGER,
    assists INTEGER,
    points INTEGER,
    shots INTEGER,
    blocks INTEGER,
    hits INTEGER,
    pp_points INTEGER,
    pp_toi_sec INTEGER,
    toi_sec INTEGER,
    PRIMARY KEY (player_id, season_id)
);

CREATE TABLE IF NOT EXISTS goalie_seasons (
    player_id INTEGER NOT NULL,
    season_id INTEGER NOT NULL,
    team_abbrev TEXT NOT NULL,
    gp INTEGER,
    games_started INTEGER,
    wins INTEGER,
    losses INTEGER,
    ot_losses INTEGER,
    saves INTEGER,
    shots_against INTEGER,
    goals_against INTEGER,
    shutouts INTEGER,
    toi_sec INTEGER,
    PRIMARY KEY (player_id, season_id)
);

CREATE TABLE IF NOT EXISTS teams (
    abbrev TEXT PRIMARY KEY,
    team_id INTEGER,
    full_name TEXT,
    conference TEXT,
    division TEXT
);

CREATE TABLE IF NOT EXISTS schedule (
    game_id INTEGER PRIMARY KEY,
    season_id INTEGER NOT NULL,
    game_date TEXT NOT NULL,
    home_abbrev TEXT NOT NULL,
    away_abbrev TEXT NOT NULL,
    game_type INTEGER
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    season_id INTEGER,
    rows_written INTEGER,
    run_at TEXT,
    status TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_skater_player ON skater_seasons(player_id);
CREATE INDEX IF NOT EXISTS idx_skater_season ON skater_seasons(season_id);
CREATE INDEX IF NOT EXISTS idx_goalie_player ON goalie_seasons(player_id);
CREATE INDEX IF NOT EXISTS idx_goalie_season ON goalie_seasons(season_id);
CREATE INDEX IF NOT EXISTS idx_schedule_season ON schedule(season_id);
```

## Public Functions

| Module | Signature |
|--------|-----------|
| `src/ingest/ingest.py` | `def ingest_seasons(n: int, db_path: str = "data/hockey.db", season_ids: list[int] | None = None) -> IngestReport` |
| `src/ingest/ingest.py` | `def ingest_rosters(db_path: str = "data/hockey.db") -> IngestReport` |
| `src/ingest/ingest.py` | `def current_season_id() -> int` |
| `src/ingest/ingest.py` | `def past_season_ids(n: int) -> list[int]` |
| `src/ingest/ingest.py` | `def main() -> None` |
| `src/ingest/rosters.py` | `def ingest_rosters(conn: sqlite3.Connection, fetcher: Fetcher, dry_run: bool = False) -> tuple[dict[str, int], list[str]]` |
| `src/ingest/schedule.py` | `def ingest_schedule(conn: sqlite3.Connection, fetcher: Fetcher, season_id: int, dry_run: bool = False) -> tuple[dict[str, int], list[str]]` |
| `src/ingest/models.py` | `def init_db(db_path: str) -> sqlite3.Connection` |
| `src/ingest/models.py` | `def log_ingest(conn: sqlite3.Connection, source: str, season_id: int \| None, rows: int, notes: list[str] \| None = None, status: str = "OK") -> None` |
| `src/ingest/models.py` | `class Fetcher: def get_paged(self, base_url: str, report: str, season: int) -> list[dict]` |
| `src/ingest/models.py` | `class Fetcher: def get_json(self, url: str) -> Any` |
| `src/ingest/models.py` | `class Fetcher: def close(self) -> None` |