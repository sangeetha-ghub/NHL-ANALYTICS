from pathlib import Path

import pandas as pd
import pytest

from nhl_pipeline import datasets, parsers
from nhl_pipeline.query_builder import WhereBuilder

TEAM_IDS = {"BOS": 1, "TOR": 2}


# ------------------------------------------------------------------ helpers
@pytest.mark.parametrize("raw,expected", [
    ("2025-10-07", "2025-10-07"), ("2025-10-07T23:00:00Z", "2025-10-07"),
    ("03/15/2026", "2026-03-15"), (None, None), ("garbage", None)])
def test_to_date(raw, expected):
    assert parsers.to_date(raw) == expected


@pytest.mark.parametrize("raw,expected", [("8:05", "08:05"), ("18:32", "18:32"), (1234.5, "20:34"), (None, None)])
def test_normalise_toi(raw, expected):
    assert parsers.normalise_toi(raw) == expected


def test_loc_unwraps_localised_names():
    assert parsers.loc({"default": "Montréal", "fr": "Montréal"}) == "Montréal"
    assert parsers.loc("plain") == "plain"


# ------------------------------------------------------------------ parsers
def test_parse_teams_sorted_and_unique(standings_json):
    teams = parsers.parse_teams(standings_json)
    assert [t["team_abbrev"] for t in teams] == ["BOS", "TOR"]
    assert teams[1]["team_name"] == "Toronto Maple Leafs"


def test_parse_standings_maps_fields(standings_json):
    rows = {r["team_id"]: r for r in parsers.parse_standings(standings_json, TEAM_IDS, "20252026")}
    tor = rows[2]
    assert (tor["wins"], tor["ot_losses"], tor["points"], tor["goals_for"], tor["road_wins"]) == (6, 1, 13, 30, 2)
    assert tor["streak_type"] == "W" and tor["season"] == "20252026"


def test_parse_roster_flattens_three_groups_and_converts_units(roster_json):
    rows = {r["player_id"]: r for r in parsers.parse_roster(roster_json, team_id=2)}
    assert {r["position"] for r in rows.values()} == {"RW", "D", "G"}
    assert rows[8478483]["height_cm"] == pytest.approx(182.9)   # from inches
    assert rows[8478483]["weight_kg"] == pytest.approx(81.6)    # from pounds
    assert rows[8476853]["height_cm"] == 185                     # metric used as-is
    assert rows[8478483]["first_name"] == "Mitch"


def test_parse_schedule_dedupes_and_separates_orphans(schedule_json):
    valid, orphans = parsers.parse_schedule(schedule_json, TEAM_IDS, "20252026")
    assert [g["game_id"] for g in valid] == [2025020001, 2025020050]
    assert valid[1]["home_score"] is None and valid[1]["game_state"] == "FUT"
    assert len(orphans) == 1 and orphans[0]["away_abbrev"] == "EHC"


def test_parse_boxscore_skaters_only(boxscore_json):
    rows = parsers.parse_boxscore(boxscore_json, TEAM_IDS)
    assert len(rows) == 3                           # 2 TOR skaters + 1 BOS, goalies excluded
    marner = next(r for r in rows if r["player_id"] == 8478483)
    assert (marner["points"], marner["team_id"], marner["toi"]) == (3, 2, "18:05")


def test_parse_club_stats_routes_by_position(club_stats_json):
    skaters, goalies = parsers.parse_club_stats(club_stats_json, team_id=2, season="20252026")
    assert [s["player_id"] for s in skaters] == [8478483]
    assert skaters[0]["avg_toi"] == "20:34"
    assert goalies[0]["save_pct"] == 0.9154 and goalies[0]["ot_losses"] == 1


# ------------------------------------------------------------ query builder
def test_where_builder_skips_unset_filters():
    w = (WhereBuilder().add("a = ?", "All").add("b = ?", None).add("c = ?", "x")
         .add_in("d", []).add_in("e", ["OFF", "FINAL"]).add_like("f", "  "))
    assert w.sql() == " WHERE c = ? AND e IN (?, ?)"
    assert w.params == ["x", "OFF", "FINAL"]


def test_where_builder_escapes_like_wildcards():
    w = WhereBuilder().add_like("name", "50%_x")
    assert w.params == [r"%50\%\_x%"]


def test_where_builder_empty():
    assert WhereBuilder().sql() == ""


# ---------------------------------------------------------- dataset loader
def test_dataset_loader_camelcase_and_abbrev(tmp_path):
    f = tmp_path / "game_stats_data.csv"
    pd.DataFrame({"gameId": [1, 1], "playerId": [10, 20], "teamAbbrev": ["TOR", "BOS"],
                  "goals": [1, 0], "assists": [1, 2], "sog": [4, 1], "PIM": [0, 2],
                  "toi": ["9:05", "20:00"], "plusMinus": [1, -1]}).to_csv(f, index=False)
    assert datasets.find_dataset("game_stats", tmp_path) == f
    rows = datasets.load_game_stats(f, TEAM_IDS, player_team={10: 2, 20: 1})
    assert rows[0] == {"game_id": 1, "player_id": 10, "team_id": 2, "goals": 1, "assists": 1, "points": 2,
                       "shots_on_goal": 4, "penalty_min": 0, "toi": "09:05", "plus_minus": 1}


def test_dataset_loader_routes_goalies_and_fixes_foreign_team_ids(tmp_path):
    f = tmp_path / "season_stats.csv"
    # numeric team ids from another numbering scheme (NHL franchise ids) → must be remapped
    pd.DataFrame({"player_id": [10, 30], "team_id": [10, 6], "position": ["C", "G"], "games_played": [5, 4],
                  "goals": [2, None], "assists": [3, None], "save_pct": [None, 91.5], "wins": [None, 3]}
                 ).to_csv(f, index=False)
    skaters, goalies = datasets.load_season_stats([f], TEAM_IDS, {10: 2, 30: 1}, {}, "20252026")
    assert skaters[0]["team_id"] == 2 and skaters[0]["points"] == 5
    assert goalies[0]["team_id"] == 1 and goalies[0]["save_pct"] == 0.915   # 91.5 → 0.915


def test_live_winger_codes_count_as_forwards():
    """The live API codes wingers L/R (the demo uses LW/RW); the app must treat both as forwards."""
    import ast
    src = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    line = next(l for l in src.splitlines() if l.startswith("FORWARD_CODES"))
    codes = ast.literal_eval(line.split("=", 1)[1].split("#")[0].strip())
    assert {"C", "L", "R", "LW", "RW"} <= set(codes)
    roster = {"forwards": [{"id": 1, "firstName": {"default": "A"}, "lastName": {"default": "B"}, "positionCode": "L"}]}
    assert parsers.parse_roster(roster, team_id=1)[0]["position"] in codes
