"""End-to-end ETL: NHL API (or datasets / demo) → validated rows → SQLite.

Run from the project root::

    python run_pipeline.py                  # live NHL API
    python run_pipeline.py --source demo    # offline synthetic season

Design choices
--------------
* **Full rebuild into a temp file, then atomic swap.** The dashboard never
  sees a half-loaded database, and re-running always reflects the latest
  standings/scores (INSERT OR IGNORE alone would keep stale rows).
* **Referential integrity is checked in Python before insert**; orphans are
  counted in the run report instead of silently vanishing.
* Tables 5-7 prefer the provided datasets in ``data/raw/datasets``; if a file
  isn't there they are built from the API (box scores / club-stats).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import config, datasets, parsers
from .api_client import NHLApiError, NHLClient
from .db import connect, create_schema, insert_rows, lookup, split_by_fk, table_counts

log = logging.getLogger("nhl_pipeline")


@dataclass
class RunReport:
    source: str
    season: str
    started: float = field(default_factory=time.time)
    loaded: dict[str, int] = field(default_factory=dict)
    duplicates_skipped: dict[str, int] = field(default_factory=dict)
    orphans_dropped: dict[str, int] = field(default_factory=dict)
    table5_7_source: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    api_calls: int = 0
    cache_hits: int = 0
    seconds: float = 0.0
    quality_checks: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d.pop("started")
        return d


def _load(conn: sqlite3.Connection, report: RunReport, table: str, rows: list[dict],
          fk: dict[str, set] | None = None) -> None:
    if fk:
        rows, bad = split_by_fk(rows, fk)
        if bad:
            report.orphans_dropped[table] = report.orphans_dropped.get(table, 0) + len(bad)
    inserted = insert_rows(conn, table, rows)
    report.loaded[table] = report.loaded.get(table, 0) + inserted
    dupes = len(rows) - inserted
    if dupes:
        report.duplicates_skipped[table] = report.duplicates_skipped.get(table, 0) + dupes


def _progress(label: str, i: int, n: int, every: int = 8) -> None:
    if i == n or i % every == 0:
        log.info("  %s %d/%d", label, i, n)


def build_database(
    source: str = "api",
    db_path: Path | str = config.DB_PATH,
    datasets_dir: Path | str = config.DATASETS_DIR,
    season: str = config.SEASON,
    client: Any = None,
    max_boxscores: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> RunReport:
    """Build all 7 tables. Returns a :class:`RunReport`.

    ``client`` may be injected (tests / notebooks); otherwise it is chosen
    from ``source`` ("api" or "demo").
    """
    say = progress or log.info
    report = RunReport(source=source, season=season)
    if client is None:
        if source == "demo":
            from .demo import DemoClient
            client = DemoClient(season=season)
        else:
            client = NHLClient()

    db_path = Path(db_path)
    tmp_path = db_path.with_suffix(".building.db")
    if tmp_path.exists():
        tmp_path.unlink()
    conn = connect(tmp_path)
    try:
        create_schema(conn)

        # 1-2. teams + standings ------------------------------------------------
        say("Fetching standings …")
        standings_json = client.standings()
        teams = parsers.parse_teams(standings_json)
        if not teams:
            raise NHLApiError("standings/now returned no teams — cannot continue")
        _load(conn, report, "teams", teams)
        team_ids: dict[str, int] = lookup(conn, "SELECT team_abbrev, team_id FROM teams")
        valid_teams = set(team_ids.values())
        _load(conn, report, "standings", parsers.parse_standings(standings_json, team_ids, season),
              fk={"team_id": valid_teams})

        # 3. players ---------------------------------------------------------
        say(f"Fetching {len(team_ids)} rosters …")
        for i, abbrev in enumerate(sorted(team_ids), 1):
            try:
                rows = parsers.parse_roster(client.roster(abbrev), team_ids[abbrev])
                _load(conn, report, "players", rows, fk={"team_id": valid_teams})
            except NHLApiError as err:
                report.warnings.append(f"roster {abbrev}: {err}")
            _progress("rosters", i, len(team_ids))
        player_team: dict[int, int] = lookup(conn, "SELECT player_id, team_id FROM players")
        player_pos: dict[int, str] = lookup(conn, "SELECT player_id, position FROM players")
        valid_players = set(player_team)

        # 4. games -------------------------------------------------------------
        say(f"Fetching {len(team_ids)} team schedules …")
        orphan_games = 0
        for i, abbrev in enumerate(sorted(team_ids), 1):
            try:
                valid, orphans = parsers.parse_schedule(client.schedule(abbrev, season), team_ids, season)
                orphan_games += len({o["game_id"] for o in orphans})
                _load(conn, report, "games", valid)
            except NHLApiError as err:
                report.warnings.append(f"schedule {abbrev}: {err}")
            _progress("schedules", i, len(team_ids))
        if orphan_games:
            report.orphans_dropped["games"] = orphan_games
            report.warnings.append(f"{orphan_games} game rows reference non-NHL clubs (e.g. international "
                                   "preseason) and were skipped to keep FOREIGN KEYs valid")

        fk_stats = {"player_id": valid_players, "team_id": valid_teams}

        # 5. game_stats --------------------------------------------------------
        gs_file = datasets.find_dataset("game_stats", Path(datasets_dir))
        if gs_file and source != "demo":
            say(f"Loading game_stats from dataset {gs_file.name} …")
            _load(conn, report, "game_stats", datasets.load_game_stats(gs_file, team_ids, player_team), fk=fk_stats)
            report.table5_7_source["game_stats"] = f"dataset:{gs_file.name}"
        else:
            placeholders = ",".join("?" * len(config.STATS_GAME_TYPES))
            game_ids = [r[0] for r in conn.execute(
                f"SELECT game_id FROM games WHERE game_state IN ('OFF','FINAL') "
                f"AND game_type IN ({placeholders}) ORDER BY game_date, game_id",
                config.STATS_GAME_TYPES)]
            if max_boxscores:
                game_ids = game_ids[:max_boxscores]
            say(f"Fetching {len(game_ids)} box scores (skater game lines) …")
            batch: list[dict] = []
            for i, gid in enumerate(game_ids, 1):
                try:
                    batch.extend(parsers.parse_boxscore(client.boxscore(gid), team_ids))
                except NHLApiError as err:
                    report.warnings.append(f"boxscore {gid}: {err}")
                if len(batch) > 5000 or i == len(game_ids):
                    _load(conn, report, "game_stats", batch, fk=fk_stats)
                    batch = []
                _progress("box scores", i, len(game_ids), every=100)
            report.table5_7_source["game_stats"] = "api:gamecenter/{id}/boxscore" if source != "demo" else "demo"

        # 6-7. season stats ----------------------------------------------------
        season_files = [f for f in (datasets.find_dataset("skater_season_stats", Path(datasets_dir)),
                                    datasets.find_dataset("goalie_season_stats", Path(datasets_dir))) if f]
        if season_files and source != "demo":
            say(f"Loading season stats from {', '.join(f.name for f in season_files)} …")
            skaters, goalies = datasets.load_season_stats(season_files, team_ids, player_team, player_pos, season)
            _load(conn, report, "skater_season_stats", skaters, fk=fk_stats)
            _load(conn, report, "goalie_season_stats", goalies, fk=fk_stats)
            for f in season_files:
                report.table5_7_source[f.stem] = f"dataset:{f.name}"
        else:
            say(f"Fetching {len(team_ids)} club-stats payloads …")
            for i, abbrev in enumerate(sorted(team_ids), 1):
                try:
                    skaters, goalies = parsers.parse_club_stats(client.club_stats(abbrev, season), team_ids[abbrev], season)
                    _load(conn, report, "skater_season_stats", skaters, fk=fk_stats)
                    _load(conn, report, "goalie_season_stats", goalies, fk=fk_stats)
                except NHLApiError as err:
                    report.warnings.append(f"club-stats {abbrev}: {err}")
                _progress("club stats", i, len(team_ids))
            src = "demo" if source == "demo" else "api:club-stats/{team}/{season}/2"
            report.table5_7_source["skater_season_stats"] = report.table5_7_source["goalie_season_stats"] = src

        report.quality_checks = run_quality_checks(conn)
        report.loaded = table_counts(conn)
        conn.close()
        os.replace(tmp_path, db_path)  # atomic swap
    except Exception:
        conn.close()
        if tmp_path.exists():
            tmp_path.unlink()
        raise

    report.api_calls = getattr(client, "live_calls", 0)
    report.cache_hits = getattr(client, "cache_hits", 0)
    report.seconds = round(time.time() - report.started, 1)
    for table, n in report.orphans_dropped.items():
        if table != "games":
            report.warnings.append(f"{n} {table} rows dropped: player/team not in parent table "
                                   "(e.g. player no longer on a roster — see NHL_ROSTER_SCOPE)")
    # run report sits next to the DB; the dashboard reads it for its "data source" note
    run_file = db_path.with_suffix(".run.json")
    run_file.write_text(json.dumps({**report.as_dict(), "built_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                                   indent=2), encoding="utf-8")
    return report


def run_quality_checks(conn: sqlite3.Connection) -> dict[str, Any]:
    """Post-load assertions; values should all be 0 / empty for a clean load."""
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "foreign_key_violations": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        "empty_tables": [t for t, n in table_counts(conn).items() if n == 0],
        "game_stats_points_mismatch": q("SELECT COUNT(*) FROM game_stats WHERE points <> goals + assists"),
        "skater_points_mismatch": q("SELECT COUNT(*) FROM skater_season_stats WHERE points <> goals + assists"),
        "standings_points_mismatch": q("SELECT COUNT(*) FROM standings WHERE points <> 2*wins + ot_losses"),
        "games_same_home_away": q("SELECT COUNT(*) FROM games WHERE home_team_id = away_team_id"),
        "final_games_missing_score": q("SELECT COUNT(*) FROM games WHERE game_state IN ('OFF','FINAL') "
                                       "AND (home_score IS NULL OR away_score IS NULL)"),
        "save_pct_out_of_range": q("SELECT COUNT(*) FROM goalie_season_stats WHERE save_pct NOT BETWEEN 0 AND 1"),
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Build the NHL Analytics Hub SQLite database")
    ap.add_argument("--source", choices=["api", "demo"], default="api",
                    help="'api' = live NHL API (default); 'demo' = offline synthetic season")
    ap.add_argument("--db", default=str(config.DB_PATH), help="output SQLite file")
    ap.add_argument("--datasets", default=str(config.DATASETS_DIR), help="folder with the provided datasets")
    ap.add_argument("--max-boxscores", type=int, default=None, help="cap box-score calls (quick test runs)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    report = build_database(args.source, args.db, args.datasets, max_boxscores=args.max_boxscores)
    print(json.dumps(report.as_dict(), indent=2))


if __name__ == "__main__":
    main()
