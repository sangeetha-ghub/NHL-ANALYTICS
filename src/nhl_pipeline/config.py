"""Central configuration. Every value can be overridden with an environment variable."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# --- NHL API -------------------------------------------------------------
BASE_URL = os.getenv("NHL_BASE_URL", "https://api-web.nhle.com/v1")
SEASON = os.getenv("NHL_SEASON", "20252026")

# Roster source:
#   "season"  -> /roster/{team}/{SEASON}   (default: rosters line up with SEASON's stats)
#   "current" -> /roster/{team}/current    (the endpoint named in the brief; in the
#                off-season it returns NEXT season's roster, so players who left
#                show up as orphaned stat rows)
ROSTER_SCOPE = os.getenv("NHL_ROSTER_SCOPE", "season")

# Game types to pull box scores for when building game_stats from the API
# (1 = preseason, 2 = regular season, 3 = playoffs).
STATS_GAME_TYPES = tuple(int(x) for x in os.getenv("NHL_STATS_GAME_TYPES", "2").split(","))

REQUEST_DELAY_SEC = float(os.getenv("NHL_REQUEST_DELAY", "0.35"))
REQUEST_TIMEOUT_SEC = float(os.getenv("NHL_REQUEST_TIMEOUT", "20"))
MAX_RETRIES = int(os.getenv("NHL_MAX_RETRIES", "4"))

# --- Paths ---------------------------------------------------------------
DATA_DIR = PROJECT_ROOT / "data"
RAW_CACHE_DIR = DATA_DIR / "raw" / "api_cache"      # raw JSON responses
DATASETS_DIR = DATA_DIR / "raw" / "datasets"        # the 3 provided datasets
DB_PATH = Path(os.getenv("NHL_DB_PATH", str(DATA_DIR / "nhl.db")))
SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"
QUERIES_PATH = PROJECT_ROOT / "sql" / "analysis_queries.sql"

# Game-state codes returned by the API → friendly labels (display layer only)
GAME_STATE_LABELS = {
    "OFF": "Final",
    "FINAL": "Final",
    "FUT": "Upcoming",
    "PRE": "Pre-game",
    "LIVE": "In Progress",
    "CRIT": "In Progress (critical)",
    "PPD": "Postponed",
}
COMPLETED_STATES = ("OFF", "FINAL")
GAME_TYPE_LABELS = {1: "Preseason", 2: "Regular Season", 3: "Playoffs"}
