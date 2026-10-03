"""SQLite helpers (connector: Python's built-in ``sqlite3``)."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from . import config

TABLE_COLUMNS: dict[str, list[str]] = {
    "teams": ["team_abbrev", "team_name", "conference_name", "division_name", "logo_url"],
    "standings": ["team_id", "season", "games_played", "wins", "losses", "ot_losses", "points",
                  "goals_for", "goals_against", "home_wins", "road_wins", "streak_type", "streak_count"],
    "players": ["player_id", "team_id", "first_name", "last_name", "position", "jersey_number",
                "birth_date", "birth_country", "height_cm", "weight_kg", "shoots_catches", "headshot_url"],
    "games": ["game_id", "season", "game_type", "game_date", "home_team_id", "away_team_id",
              "home_score", "away_score", "game_state", "venue_name"],
    "game_stats": ["game_id", "player_id", "team_id", "goals", "assists", "points", "shots_on_goal",
                   "penalty_min", "toi", "plus_minus"],
    "skater_season_stats": ["player_id", "season", "team_id", "games_played", "goals", "assists",
                            "points", "plus_minus", "penalty_min", "shots", "avg_toi"],
    "goalie_season_stats": ["player_id", "season", "team_id", "games_played", "wins", "losses",
                            "ot_losses", "save_pct", "goals_against_avg", "shutouts", "saves"],
}
LOAD_ORDER = list(TABLE_COLUMNS)  # parents before children


def connect(path: Path | str = config.DB_PATH, read_only: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if read_only:
        if not path.exists():
            raise FileNotFoundError(f"Database not found: {path}")
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        conn.execute("PRAGMA query_only = ON")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection, schema_path: Path = config.SCHEMA_PATH) -> None:
    conn.executescript(Path(schema_path).read_text(encoding="utf-8"))
    conn.commit()


def insert_rows(conn: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]]) -> int:
    """INSERT OR IGNORE — duplicate keys (e.g. a game in both teams' schedules) are skipped.

    Returns the number of rows actually inserted.
    """
    cols = TABLE_COLUMNS[table]
    sql = f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    before = conn.total_changes
    conn.executemany(sql, ([r.get(c) for c in cols] for r in rows))
    conn.commit()
    return conn.total_changes - before


def split_by_fk(rows: list[dict[str, Any]], checks: dict[str, set]) -> tuple[list[dict], list[dict]]:
    """Referential-integrity gate *before* insert.

    ``checks`` maps a column to the set of valid parent keys. Rows failing any
    check are returned separately (SQLite's OR IGNORE does not cover FK errors,
    so an orphan would otherwise abort the whole batch).
    """
    ok, bad = [], []
    for r in rows:
        (ok if all(r.get(col) in valid for col, valid in checks.items()) else bad).append(r)
    return ok, bad


def lookup(conn: sqlite3.Connection, sql: str) -> dict:
    return {k: v for k, v in conn.execute(sql).fetchall()}


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in LOAD_ORDER}


# --------------------------------------------------------- analysis queries
def load_named_queries(path: Path = config.QUERIES_PATH) -> list[dict[str, str]]:
    """Parse ``sql/analysis_queries.sql`` into [{"id", "title", "description", "sql"}].

    Blocks look like::

        -- name: Q01 | Teams ranked by total goals
        -- description: aggregation + ORDER BY
        SELECT ...;
    """
    text = Path(path).read_text(encoding="utf-8")
    queries = []
    for block in re.split(r"^-- name:", text, flags=re.M)[1:]:
        header, _, body = block.partition("\n")
        qid, _, title = header.partition("|")
        desc_lines, sql_lines = [], []
        for line in body.splitlines():
            if line.startswith("-- description:"):
                desc_lines.append(line.split(":", 1)[1].strip())
            elif line.startswith("-- ") and not sql_lines:
                desc_lines.append(line[3:].strip())
            else:
                sql_lines.append(line)
        sql = "\n".join(sql_lines).strip().rstrip(";").strip()
        queries.append({"id": qid.strip(), "title": title.strip(),
                        "description": " ".join(desc_lines), "sql": sql})
    return queries
