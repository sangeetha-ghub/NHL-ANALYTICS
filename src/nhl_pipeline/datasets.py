"""Load the three provided datasets (game_stats, skater_season_stats,
goalie_season_stats) from ``data/raw/datasets``.

The files are shared as downloads, so their exact shape isn't guaranteed:
CSV / JSON / JSONL / XLSX / Parquet are all accepted, and column names are
matched case- and style-insensitively (``playerId``, ``player_id`` and
``PLAYER ID`` all map to ``player_id``). Common API field names (``sog``,
``pim``, ``savePctg`` …) are aliased to the schema's column names.

team_id resolution (the column must reference *our* generated teams.team_id):
  1. if a team abbreviation column exists → map through teams.team_abbrev
  2. elif a numeric team_id exists and agrees with players.team_id for most
     rows → trust it
  3. else → derive team_id from the player's roster team (and log it)
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd

from . import config
from .parsers import normalise_toi, to_float, to_int

log = logging.getLogger(__name__)

EXTENSIONS = (".csv", ".json", ".jsonl", ".xlsx", ".xls", ".parquet")

# compact (lowercase, no separators) source name  ->  schema column
ALIASES: dict[str, str] = {
    # keys
    "gameid": "game_id",
    "playerid": "player_id",
    "teamid": "team_id",
    "teamabbrev": "team_abbrev", "team": "team_abbrev", "abbrev": "team_abbrev", "teamabbreviation": "team_abbrev",
    "season": "season", "seasonid": "season",
    "position": "position", "positioncode": "position", "pos": "position",
    # skater game / season
    "goals": "goals", "assists": "assists", "points": "points",
    "sog": "shots_on_goal", "shotsongoal": "shots_on_goal",
    "shots": "shots",
    "pim": "penalty_min", "penaltymin": "penalty_min", "penaltyminutes": "penalty_min",
    "toi": "toi", "timeonice": "toi",
    "plusminus": "plus_minus",
    "gamesplayed": "games_played", "gp": "games_played",
    "avgtoi": "avg_toi", "avgtimeonice": "avg_toi", "avgtimeonicepergame": "avg_toi", "timeonicepergame": "avg_toi",
    # goalie season
    "wins": "wins", "losses": "losses",
    "otlosses": "ot_losses", "overtimelosses": "ot_losses",
    "savepct": "save_pct", "savepctg": "save_pct", "savepercentage": "save_pct", "svpct": "save_pct",
    "goalsagainstavg": "goals_against_avg", "goalsagainstaverage": "goals_against_avg", "gaa": "goals_against_avg",
    "shutouts": "shutouts", "saves": "saves",
}

GAME_STATS_COLS = ["game_id", "player_id", "team_id", "goals", "assists", "points",
                   "shots_on_goal", "penalty_min", "toi", "plus_minus"]
SKATER_COLS = ["player_id", "season", "team_id", "games_played", "goals", "assists", "points",
               "plus_minus", "penalty_min", "shots", "avg_toi"]
GOALIE_COLS = ["player_id", "season", "team_id", "games_played", "wins", "losses", "ot_losses",
               "save_pct", "goals_against_avg", "shutouts", "saves"]


# ------------------------------------------------------------------ files
def find_dataset(stem: str, directory: Path = config.DATASETS_DIR) -> Path | None:
    directory = Path(directory)
    if not directory.exists():
        return None
    matches = sorted(p for p in directory.iterdir()
                     if p.suffix.lower() in EXTENSIONS and p.stem.lower().startswith(stem.lower()))
    return matches[0] if matches else None


def read_any(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".jsonl":
        return pd.read_json(path, lines=True)
    if suffix == ".json":
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # {"data": [...]} or API-like {"skaters": [...], "goalies": [...]}
            lists = [v for v in data.values() if isinstance(v, list)]
            data = [row for lst in lists for row in lst] if lists else [data]
        return pd.json_normalize(data)
    raise ValueError(f"Unsupported dataset format: {path}")


def _compact(col: str) -> str:
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(col))
    # json_normalize produces "firstName.default" → keep the parent name
    snake = snake.split(".")[0]
    return re.sub(r"[^a-z0-9]", "", snake.lower())


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    renamed: dict[str, str] = {}
    for col in df.columns:
        target = ALIASES.get(_compact(col))
        if target and target not in renamed.values():
            renamed[col] = target
    return df.rename(columns=renamed)[list(renamed.values())]


# ------------------------------------------------------------ team ids
def resolve_team_ids(df: pd.DataFrame, team_ids: dict[str, int], player_team: dict[int, int],
                     label: str) -> pd.DataFrame:
    df = df.copy()
    valid_ids = set(team_ids.values())
    if "team_abbrev" in df.columns:
        df["team_id"] = df["team_abbrev"].map(team_ids)
        log.info("%s: team_id resolved from team abbreviations", label)
    elif "team_id" in df.columns and pd.to_numeric(df["team_id"], errors="coerce").notna().all():
        tid = pd.to_numeric(df["team_id"]).astype(int)
        roster_tid = df["player_id"].map(player_team)
        comparable = roster_tid.notna()
        agree = (tid[comparable] == roster_tid[comparable]).mean() if comparable.any() else 0.0
        if tid.isin(valid_ids).all() and agree >= 0.8:
            df["team_id"] = tid
            log.info("%s: numeric team_id trusted (%.0f%% agree with rosters)", label, agree * 100)
        else:
            df["team_id"] = roster_tid
            log.warning("%s: numeric team_id doesn't match generated ids (%.0f%% agree) — "
                        "derived from players.team_id instead", label, agree * 100)
    elif "team_id" in df.columns:  # strings like "TOR" in a team_id column
        df["team_id"] = df["team_id"].map(team_ids)
    else:
        df["team_id"] = df["player_id"].map(player_team)
        log.warning("%s: no team column — team_id derived from players.team_id", label)
    return df


def _to_rows(df: pd.DataFrame, cols: list[str]) -> list[dict[str, Any]]:
    int_cols = {"game_id", "player_id", "team_id", "goals", "assists", "points", "shots_on_goal",
                "penalty_min", "plus_minus", "games_played", "shots", "wins", "losses",
                "ot_losses", "shutouts", "saves"}
    rows = []
    for rec in df.to_dict(orient="records"):
        row: dict[str, Any] = {}
        for c in cols:
            v = rec.get(c)
            v = None if (isinstance(v, float) and pd.isna(v)) else v
            if c in int_cols:
                v = to_int(v)
            elif c in ("toi", "avg_toi"):
                v = normalise_toi(v)
            elif c == "save_pct":
                v = to_float(v, 4)
                if v is not None and v > 1:  # 91.5 → 0.915
                    v = round(v / 100, 4)
            elif c == "goals_against_avg":
                v = to_float(v, 3)
            elif c == "season":
                v = str(to_int(v) or v) if v is not None else None
            row[c] = v
        rows.append(row)
    return rows


# ------------------------------------------------------------- loaders
def load_game_stats(path: Path, team_ids: dict[str, int], player_team: dict[int, int]) -> list[dict]:
    df = normalise_columns(read_any(path))
    if "shots_on_goal" not in df.columns and "shots" in df.columns:
        df = df.rename(columns={"shots": "shots_on_goal"})
    if "position" in df.columns:  # table covers skaters only
        df = df[df["position"].astype(str).str.upper() != "G"]
    df = resolve_team_ids(df, team_ids, player_team, "game_stats")
    if "points" not in df.columns:
        df["points"] = df.get("goals", 0).fillna(0) + df.get("assists", 0).fillna(0)
    return _to_rows(df.reindex(columns=GAME_STATS_COLS), GAME_STATS_COLS)


def load_season_stats(paths: list[Path], team_ids: dict[str, int], player_team: dict[int, int],
                      player_pos: dict[int, str], season: str) -> tuple[list[dict], list[dict]]:
    """Accepts one combined file or separate skater/goalie files; routes rows by position."""
    frames = []
    for p in paths:
        df = normalise_columns(read_any(p))
        if "position" not in df.columns:
            # infer from file name first, then from the players table
            if "goalie" in p.stem.lower():
                df["position"] = "G"
            elif "skater" in p.stem.lower():
                df["position"] = "F"
            else:
                df["position"] = df["player_id"].map(player_pos)
        frames.append(df)
    if not frames:
        return [], []
    df = pd.concat(frames, ignore_index=True)
    if "season" not in df.columns:
        df["season"] = season
    df["season"] = df["season"].fillna(season)
    df = resolve_team_ids(df, team_ids, player_team, "season_stats")
    is_goalie = df["position"].astype(str).str.upper() == "G"
    skaters, goalies = df[~is_goalie], df[is_goalie]
    if "points" not in skaters.columns:
        skaters = skaters.assign(points=skaters.get("goals", 0).fillna(0) + skaters.get("assists", 0).fillna(0))
    return (_to_rows(skaters.reindex(columns=SKATER_COLS), SKATER_COLS),
            _to_rows(goalies.reindex(columns=GOALIE_COLS), GOALIE_COLS))
