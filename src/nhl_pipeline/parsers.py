"""Pure functions that flatten raw NHL API JSON into table rows (list[dict]).

No I/O happens here, which keeps every parser unit-testable with a small
JSON fixture. Each function tolerates missing keys and nulls: the API is
not perfectly consistent (e.g. localised name objects vs. plain strings,
scores absent for future games, height only in inches for some players).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

Row = dict[str, Any]


# ----------------------------------------------------------------- helpers
def loc(value: Any) -> Any:
    """Unwrap the API's localised objects: {"default": "Toronto", "fr": ...} -> "Toronto"."""
    if isinstance(value, dict):
        if "default" in value:
            return value["default"]
        return next(iter(value.values()), None) if value else None
    return value


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def to_float(value: Any, ndigits: int | None = None) -> float | None:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(f, ndigits) if ndigits is not None else f


def to_date(value: Any) -> str | None:
    """Normalise anything date-like to ISO 'YYYY-MM-DD' (SQLite's DATE convention)."""
    if value in (None, ""):
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    try:  # ISO date or ISO timestamp ("2025-10-07", "2025-10-07T23:00:00Z")
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        pass
    for fmt in ("%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def seconds_to_mmss(value: Any) -> str | None:
    """1234.5 -> '20:34' (club-stats returns average TOI in seconds)."""
    secs = to_float(value)
    if secs is None:
        return None
    secs = int(round(secs))
    return f"{secs // 60:02d}:{secs % 60:02d}"


def normalise_toi(value: Any) -> str | None:
    """Accept 'MM:SS', 'M:SS' or seconds and always return 'MM:SS'."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if ":" in text:
        mins, _, secs = text.partition(":")
        m, s = to_int(mins), to_int(secs)
        return f"{m:02d}:{s:02d}" if m is not None and s is not None else None
    return seconds_to_mmss(text)


def dedupe(rows: Iterable[Row], key: str | tuple[str, ...]) -> list[Row]:
    """Keep the first row per key (mirrors INSERT OR IGNORE semantics)."""
    keys = (key,) if isinstance(key, str) else key
    seen: set[tuple] = set()
    out: list[Row] = []
    for r in rows:
        k = tuple(r.get(x) for x in keys)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


# ------------------------------------------------------ 1. teams / 2. standings
def parse_teams(standings_json: dict[str, Any]) -> list[Row]:
    rows = []
    for t in standings_json.get("standings", []) or []:
        abbrev = loc(t.get("teamAbbrev"))
        if not abbrev:
            continue
        rows.append(
            {
                "team_abbrev": abbrev,
                "team_name": loc(t.get("teamName")) or abbrev,
                "conference_name": t.get("conferenceName") or "Unknown",
                "division_name": t.get("divisionName") or "Unknown",
                "logo_url": t.get("teamLogo"),
            }
        )
    # sort → team_id assignment is deterministic across runs
    return sorted(dedupe(rows, "team_abbrev"), key=lambda r: r["team_abbrev"])


def parse_standings(standings_json: dict[str, Any], team_ids: dict[str, int], season: str) -> list[Row]:
    rows = []
    for t in standings_json.get("standings", []) or []:
        team_id = team_ids.get(loc(t.get("teamAbbrev")))
        if team_id is None:
            continue
        rows.append(
            {
                "team_id": team_id,
                "season": str(t.get("seasonId") or season),
                "games_played": to_int(t.get("gamesPlayed")) or 0,
                "wins": to_int(t.get("wins")) or 0,
                "losses": to_int(t.get("losses")) or 0,
                "ot_losses": to_int(t.get("otLosses")) or 0,
                "points": to_int(t.get("points")) or 0,
                "goals_for": to_int(t.get("goalFor")) or 0,
                "goals_against": to_int(t.get("goalAgainst")) or 0,
                "home_wins": to_int(t.get("homeWins")) or 0,
                "road_wins": to_int(t.get("roadWins")) or 0,
                "streak_type": t.get("streakCode"),
                "streak_count": to_int(t.get("streakCount")),
            }
        )
    return dedupe(rows, ("team_id", "season"))


# ------------------------------------------------------------- 3. players
def parse_roster(roster_json: dict[str, Any], team_id: int) -> list[Row]:
    """Flatten forwards + defensemen + goalies into one list; `position` tells them apart."""
    rows = []
    for group in ("forwards", "defensemen", "goalies"):
        for p in roster_json.get(group, []) or []:
            pid = to_int(p.get("id"))
            if pid is None:
                continue
            height = to_float(p.get("heightInCentimeters"))
            if height is None and p.get("heightInInches") is not None:
                height = round(to_float(p["heightInInches"]) * 2.54, 1)
            weight = to_float(p.get("weightInKilograms"))
            if weight is None and p.get("weightInPounds") is not None:
                weight = round(to_float(p["weightInPounds"]) * 0.453592, 1)
            position = p.get("positionCode") or {"goalies": "G", "defensemen": "D"}.get(group, "F")
            rows.append(
                {
                    "player_id": pid,
                    "team_id": team_id,
                    "first_name": loc(p.get("firstName")) or "",
                    "last_name": loc(p.get("lastName")) or "",
                    "position": position,
                    "jersey_number": to_int(p.get("sweaterNumber")),
                    "birth_date": to_date(p.get("birthDate")),
                    "birth_country": p.get("birthCountry"),
                    "height_cm": height,
                    "weight_kg": weight,
                    "shoots_catches": p.get("shootsCatches"),
                    "headshot_url": p.get("headshot"),
                }
            )
    return dedupe(rows, "player_id")


# --------------------------------------------------------------- 4. games
def parse_schedule(schedule_json: dict[str, Any], team_ids: dict[str, int], season: str) -> tuple[list[Row], list[Row]]:
    """Return (valid_rows, orphan_rows).

    Orphans are games whose home/away club isn't in `teams` — e.g. preseason
    games against European clubs. They would violate the FOREIGN KEY, so they
    are separated out (and reported) instead of being inserted.
    """
    valid, orphans = [], []
    for g in schedule_json.get("games", []) or []:
        gid = to_int(g.get("id"))
        if gid is None:
            continue
        home, away = g.get("homeTeam") or {}, g.get("awayTeam") or {}
        row = {
            "game_id": gid,
            "season": str(g.get("season") or season),
            "game_type": to_int(g.get("gameType")) or 0,
            "game_date": to_date(g.get("gameDate") or g.get("startTimeUTC")),
            "home_team_id": team_ids.get(home.get("abbrev")),
            "away_team_id": team_ids.get(away.get("abbrev")),
            "home_score": to_int(home.get("score")),
            "away_score": to_int(away.get("score")),
            "game_state": g.get("gameState") or "UNKNOWN",
            "venue_name": loc(g.get("venue")),
        }
        if row["home_team_id"] is None or row["away_team_id"] is None or row["game_date"] is None:
            orphans.append({**row, "home_abbrev": home.get("abbrev"), "away_abbrev": away.get("abbrev")})
        else:
            valid.append(row)
    return dedupe(valid, "game_id"), orphans


# ---------------------------------------------------------- 5. game_stats
def parse_boxscore(box_json: dict[str, Any], team_ids: dict[str, int]) -> list[Row]:
    """Skater lines (forwards + defense) from /gamecenter/{id}/boxscore. Goalies excluded."""
    gid = to_int(box_json.get("id"))
    by_game = box_json.get("playerByGameStats") or {}
    rows = []
    for side in ("homeTeam", "awayTeam"):
        team_id = team_ids.get((box_json.get(side) or {}).get("abbrev"))
        if team_id is None:
            continue
        side_stats = by_game.get(side) or {}
        for group in ("forwards", "defense"):
            for p in side_stats.get(group, []) or []:
                goals, assists = to_int(p.get("goals")) or 0, to_int(p.get("assists")) or 0
                points = to_int(p.get("points"))
                rows.append(
                    {
                        "game_id": gid,
                        "player_id": to_int(p.get("playerId")),
                        "team_id": team_id,
                        "goals": goals,
                        "assists": assists,
                        "points": points if points is not None else goals + assists,
                        "shots_on_goal": to_int(p.get("sog")) or 0,
                        "penalty_min": to_int(p.get("pim")) or 0,
                        "toi": normalise_toi(p.get("toi")),
                        "plus_minus": to_int(p.get("plusMinus")) or 0,
                    }
                )
    return [r for r in rows if r["player_id"] is not None]


# ------------------------------------------ 6. skaters / 7. goalies (season)
def _skater_row(p: Row, team_id: int, season: str) -> Row:
    goals, assists = to_int(p.get("goals")) or 0, to_int(p.get("assists")) or 0
    points = to_int(p.get("points"))
    return {
        "player_id": to_int(p.get("playerId")),
        "season": season,
        "team_id": team_id,
        "games_played": to_int(p.get("gamesPlayed")) or 0,
        "goals": goals,
        "assists": assists,
        "points": points if points is not None else goals + assists,
        "plus_minus": to_int(p.get("plusMinus")) or 0,
        "penalty_min": to_int(p.get("penaltyMinutes")) or 0,
        "shots": to_int(p.get("shots")) or 0,
        "avg_toi": normalise_toi(p.get("avgTimeOnIcePerGame")),
    }


def _goalie_row(p: Row, team_id: int, season: str) -> Row:
    return {
        "player_id": to_int(p.get("playerId")),
        "season": season,
        "team_id": team_id,
        "games_played": to_int(p.get("gamesPlayed")) or 0,
        "wins": to_int(p.get("wins")) or 0,
        "losses": to_int(p.get("losses")) or 0,
        "ot_losses": to_int(p.get("overtimeLosses") if p.get("overtimeLosses") is not None else p.get("otLosses")) or 0,
        "save_pct": to_float(p.get("savePercentage") if p.get("savePercentage") is not None else p.get("savePctg"), 4),
        "goals_against_avg": to_float(p.get("goalsAgainstAverage"), 3),
        "shutouts": to_int(p.get("shutouts")) or 0,
        "saves": to_int(p.get("saves")) or 0,
    }


def parse_club_stats(club_json: dict[str, Any], team_id: int, season: str) -> tuple[list[Row], list[Row]]:
    """Route every player by position: 'G' -> goalie table, anything else -> skater table."""
    season = str(club_json.get("season") or season)
    skaters, goalies = [], []
    for p in (club_json.get("skaters") or []) + (club_json.get("goalies") or []):
        pos = p.get("positionCode") or p.get("position")
        if pos is None:  # fall back to which list it came from
            pos = "G" if p in (club_json.get("goalies") or []) else "F"
        if pos == "G":
            goalies.append(_goalie_row(p, team_id, season))
        else:
            skaters.append(_skater_row(p, team_id, season))
    key = ("player_id", "season", "team_id")
    return (
        dedupe([r for r in skaters if r["player_id"] is not None], key),
        dedupe([r for r in goalies if r["player_id"] is not None], key),
    )
