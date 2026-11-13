-- NHL Draft Assistant — Database Schema
-- SQLite 3

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
    sh_points INTEGER,
    pp_toi_sec INTEGER,
    -- even-strength TOI (the API's evTimeOnIce), not total ice time
    toi_sec INTEGER,
    sh_toi_sec INTEGER,
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

-- ── In-season (season-to-date, regular season only) ─────────────────
-- Written by src/inseason/ingest.py. Kept separate from skater_seasons /
-- goalie_seasons so a mid-season refresh never changes the draft board's
-- "next season" (MAX(skater_seasons.season_id) + 10001).
CREATE TABLE IF NOT EXISTS skater_current (
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
    sh_points INTEGER,
    pp_toi_sec INTEGER,
    toi_sec INTEGER,
    as_of TEXT NOT NULL,
    PRIMARY KEY (player_id, season_id)
);

CREATE TABLE IF NOT EXISTS goalie_current (
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
    as_of TEXT NOT NULL,
    PRIMARY KEY (player_id, season_id)
);

-- Last ~3 weeks of regular-season totals (config/inseason.yaml trend_days),
-- replaced on every in-season refresh. Feeds the Trend column.
CREATE TABLE IF NOT EXISTS skater_recent (
    player_id INTEGER PRIMARY KEY,
    gp INTEGER, goals INTEGER, assists INTEGER, shots INTEGER,
    blocks INTEGER, hits INTEGER, pp_points INTEGER, sh_points INTEGER,
    window_start TEXT NOT NULL, window_end TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goalie_recent (
    player_id INTEGER PRIMARY KEY,
    gp INTEGER, games_started INTEGER, wins INTEGER, saves INTEGER,
    shots_against INTEGER, goals_against INTEGER, shutouts INTEGER,
    window_start TEXT NOT NULL, window_end TEXT NOT NULL
);
