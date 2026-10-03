"""Shared fixtures: tiny payloads in the same shape as the real NHL API."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def standings_json():
    return {"standings": [
        {"seasonId": 20252026, "teamAbbrev": {"default": "TOR"}, "teamName": {"default": "Toronto Maple Leafs"},
         "conferenceName": "Eastern", "divisionName": "Atlantic", "teamLogo": "https://x/TOR.svg",
         "gamesPlayed": 10, "wins": 6, "losses": 3, "otLosses": 1, "points": 13, "goalFor": 30,
         "goalAgainst": 25, "homeWins": 4, "roadWins": 2, "streakCode": "W", "streakCount": 2},
        {"seasonId": 20252026, "teamAbbrev": {"default": "BOS"}, "teamName": {"default": "Boston Bruins"},
         "conferenceName": "Eastern", "divisionName": "Atlantic", "teamLogo": "https://x/BOS.svg",
         "gamesPlayed": 10, "wins": 4, "losses": 5, "otLosses": 1, "points": 9, "goalFor": 25,
         "goalAgainst": 30, "homeWins": 2, "roadWins": 2, "streakCode": "L", "streakCount": 1},
    ]}


@pytest.fixture
def roster_json():
    return {
        "forwards": [{"id": 8478483, "headshot": "https://x/h.png", "firstName": {"default": "Mitch"},
                      "lastName": {"default": "Marner"}, "sweaterNumber": 16, "positionCode": "RW",
                      "shootsCatches": "R", "heightInInches": 72, "weightInPounds": 180,
                      "birthDate": "1997-05-05", "birthCountry": "CAN"}],
        "defensemen": [{"id": 8476853, "firstName": {"default": "Morgan"}, "lastName": {"default": "Rielly"},
                        "sweaterNumber": 44, "positionCode": "D", "shootsCatches": "L",
                        "heightInCentimeters": 185, "weightInKilograms": 99, "birthDate": "1994-03-09",
                        "birthCountry": "CAN"}],
        "goalies": [{"id": 8479361, "firstName": {"default": "Joseph"}, "lastName": {"default": "Woll"},
                     "sweaterNumber": 60, "positionCode": "G", "shootsCatches": "L",
                     "birthDate": "1998-07-12", "birthCountry": "USA"}],
    }


@pytest.fixture
def schedule_json():
    return {"games": [
        {"id": 2025020001, "season": 20252026, "gameType": 2, "gameDate": "2025-10-08",
         "venue": {"default": "Scotiabank Arena"}, "gameState": "OFF",
         "homeTeam": {"abbrev": "TOR", "score": 4}, "awayTeam": {"abbrev": "BOS", "score": 2}},
        {"id": 2025020001, "season": 20252026, "gameType": 2, "gameDate": "2025-10-08",  # duplicate
         "venue": {"default": "Scotiabank Arena"}, "gameState": "OFF",
         "homeTeam": {"abbrev": "TOR", "score": 4}, "awayTeam": {"abbrev": "BOS", "score": 2}},
        {"id": 2025020050, "season": 20252026, "gameType": 2, "gameDate": "2025-12-01",
         "venue": {"default": "TD Garden"}, "gameState": "FUT",
         "homeTeam": {"abbrev": "BOS"}, "awayTeam": {"abbrev": "TOR"}},
        {"id": 2025010099, "season": 20252026, "gameType": 1, "gameDate": "2025-09-28",
         "venue": {"default": "Somewhere"}, "gameState": "OFF",
         "homeTeam": {"abbrev": "TOR", "score": 3}, "awayTeam": {"abbrev": "EHC", "score": 1}},  # non-NHL club
    ]}


@pytest.fixture
def boxscore_json():
    line = lambda pid, pos, g, a: {"playerId": pid, "position": pos, "goals": g, "assists": a,  # noqa: E731
                                   "points": g + a, "sog": 3, "pim": 2, "plusMinus": 1, "toi": "18:05"}
    return {"id": 2025020001, "homeTeam": {"abbrev": "TOR"}, "awayTeam": {"abbrev": "BOS"},
            "playerByGameStats": {
                "homeTeam": {"forwards": [line(8478483, "RW", 1, 2)], "defense": [line(8476853, "D", 0, 1)],
                             "goalies": [{"playerId": 8479361, "saves": 28}]},
                "awayTeam": {"forwards": [line(1111, "C", 1, 0)], "defense": [], "goalies": []}}}


@pytest.fixture
def club_stats_json():
    return {"season": "20252026", "gameType": 2,
            "skaters": [{"playerId": 8478483, "positionCode": "RW", "gamesPlayed": 10, "goals": 5,
                         "assists": 9, "points": 14, "plusMinus": 3, "penaltyMinutes": 4, "shots": 30,
                         "avgTimeOnIcePerGame": 1234.4}],
            "goalies": [{"playerId": 8479361, "positionCode": "G", "gamesPlayed": 8, "wins": 5, "losses": 2,
                         "overtimeLosses": 1, "savePercentage": 0.9154, "goalsAgainstAverage": 2.511,
                         "shutouts": 1, "saves": 210}]}
