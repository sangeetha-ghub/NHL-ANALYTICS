"""End-to-end: build a full database from the offline demo source, then check
the schema, integrity, every analysis query, and every dashboard page."""
import sqlite3
from pathlib import Path

import pytest

from nhl_pipeline.db import load_named_queries
from nhl_pipeline.pipeline import build_database

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "nhl.db"
    report = build_database(source="demo", db_path=db)
    return db, report


def test_all_seven_tables_loaded(demo_db):
    _, report = demo_db
    assert set(report.loaded) == {"teams", "standings", "players", "games", "game_stats",
                                  "skater_season_stats", "goalie_season_stats"}
    assert all(n > 0 for n in report.loaded.values())
    assert report.loaded["teams"] == 32


def test_duplicate_games_were_ignored(demo_db):
    _, report = demo_db
    # every game appears in both teams' schedules → exactly one copy skipped per game
    assert report.duplicates_skipped["games"] == report.loaded["games"]


def test_quality_checks_clean(demo_db):
    _, report = demo_db
    qc = report.quality_checks
    assert qc["foreign_key_violations"] == 0 and qc["empty_tables"] == []
    assert all(v == 0 for k, v in qc.items() if isinstance(v, int))


def test_schema_constraints(demo_db):
    db, _ = demo_db
    conn = sqlite3.connect(db)
    fks = {row[2] for row in conn.execute("PRAGMA foreign_key_list(game_stats)")}
    assert fks == {"players", "teams"}
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO players (player_id, team_id, first_name, last_name, position) "
                     "VALUES (1, 9999, 'x', 'y', 'C')")


def test_rebuild_is_idempotent(demo_db, tmp_path):
    _, first = demo_db
    again = build_database(source="demo", db_path=tmp_path / "again.db")
    assert again.loaded == first.loaded


@pytest.mark.parametrize("query", load_named_queries(), ids=lambda q: q["id"])
def test_every_analysis_query_runs(demo_db, query):
    db, _ = demo_db
    conn = sqlite3.connect(db)
    conn.execute(query["sql"]).fetchall()


def test_at_least_ten_queries():
    assert len(load_named_queries()) >= 10


@pytest.mark.parametrize("page", ["Home", "Standings", "Team Info", "Player Search",
                                  "Game Results", "Leaderboards", "SQL Query"])
def test_dashboard_page_renders(demo_db, page, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    db, _ = demo_db
    monkeypatch.setenv("NHL_DB_PATH", str(db))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.query_params["page"] = page
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
