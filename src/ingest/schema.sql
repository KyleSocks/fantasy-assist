-- Fantasy Hockey Draft Assistant — Database Schema
-- SQLite 3

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Players
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    team TEXT NOT NULL DEFAULT '',
    position TEXT NOT NULL CHECK (position IN ('C', 'W', 'D', 'G')),
    jersey_number TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'A'
);

-- Season stats (one row per player per season)
CREATE TABLE IF NOT EXISTS player_stats (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    season TEXT NOT NULL,
    games INTEGER NOT NULL DEFAULT 0,
    goals INTEGER NOT NULL DEFAULT 0,
    assists INTEGER NOT NULL DEFAULT 0,
    points INTEGER NOT NULL DEFAULT 0,
    plus_minus INTEGER NOT NULL DEFAULT 0,
    powerplay_goals INTEGER NOT NULL DEFAULT 0,
    shorthanded_goals INTEGER NOT NULL DEFAULT 0,
    hits INTEGER NOT NULL DEFAULT 0,
    blocked_shots INTEGER NOT NULL DEFAULT 0,
    penalty_minutes INTEGER NOT NULL DEFAULT 0,
    shots_against INTEGER NOT NULL DEFAULT 0,
    saves INTEGER NOT NULL DEFAULT 0,
    goals_against INTEGER NOT NULL DEFAULT 0,
    win INTEGER NOT NULL DEFAULT 0,
    UNIQUE (player_id, season)
);

-- Draft picks (live during draft)
CREATE TABLE IF NOT EXISTS draft_picks (
    id INTEGER PRIMARY KEY,
    round INTEGER NOT NULL,
    pick_order INTEGER NOT NULL,
    team_name TEXT NOT NULL,
    player_id INTEGER NOT NULL REFERENCES players(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (round, pick_order)
);

-- League configuration
CREATE TABLE IF NOT EXISTS league_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_players_name ON players(name);
CREATE INDEX IF NOT EXISTS idx_players_position ON players(position);
CREATE INDEX IF NOT EXISTS idx_player_stats_player ON player_stats(player_id);
CREATE INDEX IF NOT EXISTS idx_player_stats_season ON player_stats(season);
CREATE INDEX IF NOT EXISTS idx_draft_picks_player ON draft_picks(player_id);