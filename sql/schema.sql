-- =====================================================================
-- NHL Analytics Hub — relational schema (SQLite)
-- 7 tables, normalised around `teams` and `players`.
-- Foreign keys are enforced at runtime with: PRAGMA foreign_keys = ON;
-- =====================================================================

DROP TABLE IF EXISTS goalie_season_stats;
DROP TABLE IF EXISTS skater_season_stats;
DROP TABLE IF EXISTS game_stats;
DROP TABLE IF EXISTS games;
DROP TABLE IF EXISTS players;
DROP TABLE IF EXISTS standings;
DROP TABLE IF EXISTS teams;

-- ---------------------------------------------------------------------
-- 1. teams  (source: /v1/standings/now)
--    The API has no numeric team id in standings, so we generate one.
-- ---------------------------------------------------------------------
CREATE TABLE teams (
    team_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    team_abbrev      VARCHAR(10)  NOT NULL UNIQUE,
    team_name        VARCHAR(100) NOT NULL,
    conference_name  VARCHAR(50)  NOT NULL,
    division_name    VARCHAR(50)  NOT NULL,
    logo_url         TEXT
);

-- ---------------------------------------------------------------------
-- 2. standings  (source: /v1/standings/now)
-- ---------------------------------------------------------------------
CREATE TABLE standings (
    standing_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id        INTEGER     NOT NULL,
    season         VARCHAR(20) NOT NULL,
    games_played   INTEGER     NOT NULL DEFAULT 0,
    wins           INTEGER     NOT NULL DEFAULT 0,
    losses         INTEGER     NOT NULL DEFAULT 0,
    ot_losses      INTEGER     NOT NULL DEFAULT 0,
    points         INTEGER     NOT NULL DEFAULT 0,
    goals_for      INTEGER     NOT NULL DEFAULT 0,
    goals_against  INTEGER     NOT NULL DEFAULT 0,
    home_wins      INTEGER     NOT NULL DEFAULT 0,
    road_wins      INTEGER     NOT NULL DEFAULT 0,
    streak_type    VARCHAR(20),
    streak_count   INTEGER,
    UNIQUE (team_id, season),
    FOREIGN KEY (team_id) REFERENCES teams (team_id)
);

-- ---------------------------------------------------------------------
-- 3. players  (source: /v1/roster/{team}/{season|current})
--    player_id comes straight from the API (not generated).
-- ---------------------------------------------------------------------
CREATE TABLE players (
    player_id       BIGINT       PRIMARY KEY,
    team_id         INTEGER      NOT NULL,
    first_name      VARCHAR(100) NOT NULL,
    last_name       VARCHAR(100) NOT NULL,
    position        VARCHAR(10)  NOT NULL,
    jersey_number   INTEGER,
    birth_date      DATE,
    birth_country   VARCHAR(10),
    height_cm       REAL,
    weight_kg       REAL,
    shoots_catches  VARCHAR(5),
    headshot_url    TEXT,
    FOREIGN KEY (team_id) REFERENCES teams (team_id)
);

-- ---------------------------------------------------------------------
-- 4. games  (source: /v1/club-schedule-season/{team}/{season})
--    Each game is returned twice (home + away schedule) → INSERT OR IGNORE.
-- ---------------------------------------------------------------------
CREATE TABLE games (
    game_id       BIGINT      PRIMARY KEY,
    season        VARCHAR(20) NOT NULL,
    game_type     INTEGER     NOT NULL,
    game_date     DATE        NOT NULL,
    home_team_id  INTEGER     NOT NULL,
    away_team_id  INTEGER     NOT NULL,
    home_score    INTEGER,
    away_score    INTEGER,
    game_state    VARCHAR(20) NOT NULL,
    venue_name    VARCHAR(150),
    FOREIGN KEY (home_team_id) REFERENCES teams (team_id),
    FOREIGN KEY (away_team_id) REFERENCES teams (team_id)
);

-- ---------------------------------------------------------------------
-- 5. game_stats — skaters only (dataset, or /v1/gamecenter/{id}/boxscore)
--    game_id intentionally has no FK (per spec: the dataset may reference
--    games outside the loaded schedule); it is indexed instead.
-- ---------------------------------------------------------------------
CREATE TABLE game_stats (
    stat_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id        BIGINT      NOT NULL,
    player_id      BIGINT      NOT NULL,
    team_id        INTEGER     NOT NULL,
    goals          INTEGER     NOT NULL DEFAULT 0,
    assists        INTEGER     NOT NULL DEFAULT 0,
    points         INTEGER     NOT NULL DEFAULT 0,
    shots_on_goal  INTEGER     NOT NULL DEFAULT 0,
    penalty_min    INTEGER     NOT NULL DEFAULT 0,
    toi            VARCHAR(10),
    plus_minus     INTEGER     NOT NULL DEFAULT 0,
    UNIQUE (game_id, player_id),
    FOREIGN KEY (player_id) REFERENCES players (player_id),
    FOREIGN KEY (team_id)   REFERENCES teams (team_id)
);

-- ---------------------------------------------------------------------
-- 6. skater_season_stats (dataset, or /v1/club-stats/{team}/{season}/2)
--    A traded player has one row per team → unique on (player, season, team).
-- ---------------------------------------------------------------------
CREATE TABLE skater_season_stats (
    stat_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id     BIGINT      NOT NULL,
    season        VARCHAR(20) NOT NULL,
    team_id       INTEGER     NOT NULL,
    games_played  INTEGER     NOT NULL DEFAULT 0,
    goals         INTEGER     NOT NULL DEFAULT 0,
    assists       INTEGER     NOT NULL DEFAULT 0,
    points        INTEGER     NOT NULL DEFAULT 0,
    plus_minus    INTEGER     NOT NULL DEFAULT 0,
    penalty_min   INTEGER     NOT NULL DEFAULT 0,
    shots         INTEGER     NOT NULL DEFAULT 0,
    avg_toi       VARCHAR(10),
    UNIQUE (player_id, season, team_id),
    FOREIGN KEY (player_id) REFERENCES players (player_id),
    FOREIGN KEY (team_id)   REFERENCES teams (team_id)
);

-- ---------------------------------------------------------------------
-- 7. goalie_season_stats (dataset, or /v1/club-stats/{team}/{season}/2)
-- ---------------------------------------------------------------------
CREATE TABLE goalie_season_stats (
    stat_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id          BIGINT      NOT NULL,
    season             VARCHAR(20) NOT NULL,
    team_id            INTEGER     NOT NULL,
    games_played       INTEGER     NOT NULL DEFAULT 0,
    wins               INTEGER     NOT NULL DEFAULT 0,
    losses             INTEGER     NOT NULL DEFAULT 0,
    ot_losses          INTEGER     NOT NULL DEFAULT 0,
    save_pct           FLOAT,
    goals_against_avg  FLOAT,
    shutouts           INTEGER     NOT NULL DEFAULT 0,
    saves              INTEGER     NOT NULL DEFAULT 0,
    UNIQUE (player_id, season, team_id),
    FOREIGN KEY (player_id) REFERENCES players (player_id),
    FOREIGN KEY (team_id)   REFERENCES teams (team_id)
);

-- Indexes for the joins/filters the dashboard uses most
CREATE INDEX idx_standings_team      ON standings (team_id);
CREATE INDEX idx_players_team        ON players (team_id);
CREATE INDEX idx_games_date          ON games (game_date);
CREATE INDEX idx_games_home          ON games (home_team_id);
CREATE INDEX idx_games_away          ON games (away_team_id);
CREATE INDEX idx_game_stats_game     ON game_stats (game_id);
CREATE INDEX idx_game_stats_player   ON game_stats (player_id);
CREATE INDEX idx_skater_stats_player ON skater_season_stats (player_id);
CREATE INDEX idx_goalie_stats_player ON goalie_season_stats (player_id);
