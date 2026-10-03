"""Offline demo source: a seeded, *synthetic* season served in the exact JSON
shape of the NHL API endpoints.

Why it exists
-------------
* lets the whole pipeline + dashboard run with no network (CI, firewalled
  laptops, the API being down)
* exercises the **same parsers** as live mode, because it returns the same
  JSON structure the real endpoints do

Team names/divisions are the real 2025-26 league alignment; every player,
score and stat is randomly generated. Never present demo output as real data.
"""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from . import config

TEAMS = [  # abbrev, name, conference, division
    ("BOS", "Boston Bruins", "Eastern", "Atlantic"), ("BUF", "Buffalo Sabres", "Eastern", "Atlantic"),
    ("DET", "Detroit Red Wings", "Eastern", "Atlantic"), ("FLA", "Florida Panthers", "Eastern", "Atlantic"),
    ("MTL", "Montréal Canadiens", "Eastern", "Atlantic"), ("OTT", "Ottawa Senators", "Eastern", "Atlantic"),
    ("TBL", "Tampa Bay Lightning", "Eastern", "Atlantic"), ("TOR", "Toronto Maple Leafs", "Eastern", "Atlantic"),
    ("CAR", "Carolina Hurricanes", "Eastern", "Metropolitan"), ("CBJ", "Columbus Blue Jackets", "Eastern", "Metropolitan"),
    ("NJD", "New Jersey Devils", "Eastern", "Metropolitan"), ("NYI", "New York Islanders", "Eastern", "Metropolitan"),
    ("NYR", "New York Rangers", "Eastern", "Metropolitan"), ("PHI", "Philadelphia Flyers", "Eastern", "Metropolitan"),
    ("PIT", "Pittsburgh Penguins", "Eastern", "Metropolitan"), ("WSH", "Washington Capitals", "Eastern", "Metropolitan"),
    ("CHI", "Chicago Blackhawks", "Western", "Central"), ("COL", "Colorado Avalanche", "Western", "Central"),
    ("DAL", "Dallas Stars", "Western", "Central"), ("MIN", "Minnesota Wild", "Western", "Central"),
    ("NSH", "Nashville Predators", "Western", "Central"), ("STL", "St. Louis Blues", "Western", "Central"),
    ("UTA", "Utah Mammoth", "Western", "Central"), ("WPG", "Winnipeg Jets", "Western", "Central"),
    ("ANA", "Anaheim Ducks", "Western", "Pacific"), ("CGY", "Calgary Flames", "Western", "Pacific"),
    ("EDM", "Edmonton Oilers", "Western", "Pacific"), ("LAK", "Los Angeles Kings", "Western", "Pacific"),
    ("SEA", "Seattle Kraken", "Western", "Pacific"), ("SJS", "San Jose Sharks", "Western", "Pacific"),
    ("VAN", "Vancouver Canucks", "Western", "Pacific"), ("VGK", "Vegas Golden Knights", "Western", "Pacific"),
]

FIRST = ["Alex", "Ben", "Carter", "Dylan", "Elias", "Filip", "Gabe", "Henrik", "Isaac", "Jake", "Kasper",
         "Liam", "Mikko", "Noah", "Oskar", "Pavel", "Quinn", "Ryan", "Sami", "Tyler", "Viktor", "Wyatt",
         "Yanni", "Zach", "Anton", "Brady", "Cole", "Dmitri", "Erik", "Felix", "Grant", "Hugo", "Jonas",
         "Kirill", "Lukas", "Matt", "Nils", "Owen", "Petr", "Rasmus"]
LAST = ["Andersson", "Bennett", "Carlsson", "Dubois", "Eriksson", "Fischer", "Gagnon", "Hughes", "Ivanov",
        "Johansson", "Kovalenko", "Lindqvist", "MacLeod", "Novak", "Olsen", "Pelletier", "Quenneville",
        "Rantanen", "Sorensen", "Tremblay", "Uusitalo", "Virtanen", "Walsh", "Yakovlev", "Zelenko",
        "Bergeron", "Chabot", "Dahl", "Ekholm", "Forsberg", "Granlund", "Holm", "Kane", "Laine", "Morin",
        "Nyquist", "Ouellet", "Pastor", "Roy", "Stahl", "Theriault", "Voss"]
COUNTRIES = ["CAN"] * 9 + ["USA"] * 5 + ["SWE"] * 3 + ["FIN"] * 2 + ["RUS"] * 2 + ["CZE", "SVK", "DEU", "CHE", "DNK", "LVA"]


def _toi(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class DemoClient:
    """Drop-in replacement for :class:`NHLClient` (same public methods)."""

    def __init__(self, seed: int = 42, season: str = config.SEASON, played_through: date | None = None):
        self.rng = random.Random(seed)
        self.season = season
        start_year = int(season[:4])
        self.opening = date(start_year, 10, 7)
        self.played_through = played_through or date(start_year + 1, 3, 31)
        self.live_calls = 0
        self.cache_hits = 0
        self._build()

    # ------------------------------------------------------------ world
    def _build(self) -> None:
        rng = self.rng
        self.strength = {t[0]: rng.uniform(0.9, 1.12) for t in TEAMS}
        self.rosters: dict[str, dict[str, list[dict]]] = {}
        next_id = 8_470_001
        for abbrev, *_ in TEAMS:
            groups: dict[str, list[dict]] = {"forwards": [], "defensemen": [], "goalies": []}
            numbers = rng.sample(range(2, 99), 25)
            layout = [("forwards", "C")] * 5 + [("forwards", "LW")] * 5 + [("forwards", "RW")] * 4 \
                + [("defensemen", "D")] * 8 + [("goalies", "G")] * 3
            for i, (group, pos) in enumerate(layout):
                h_in = rng.randint(69, 78) if pos != "G" else rng.randint(72, 79)
                w_lb = rng.randint(175, 230)
                groups[group].append({
                    "id": next_id, "headshot": None,
                    "firstName": {"default": rng.choice(FIRST)}, "lastName": {"default": rng.choice(LAST)},
                    "sweaterNumber": numbers[i], "positionCode": pos,
                    "shootsCatches": rng.choice("LLR"),
                    "heightInInches": h_in, "weightInPounds": w_lb,
                    "heightInCentimeters": round(h_in * 2.54), "weightInKilograms": round(w_lb * 0.4536),
                    "birthDate": (date(1990, 1, 1) + timedelta(days=rng.randint(0, 5200))).isoformat(),
                    "birthCountry": rng.choice(COUNTRIES),
                    # hidden skill used by the simulator, stripped before serving
                    "_skill": rng.lognormvariate(0, 0.55) * (1.6 if pos in ("C", "LW", "RW") else 0.7),
                })
                next_id += 1
            groups["goalies"][0]["_skill"] = 3.0  # clear starter
            self.rosters[abbrev] = groups
        self._simulate_season()

    def _schedule_pairs(self) -> list[tuple[date, str, str]]:
        """82 'rounds' of random perfect matchings → every team plays exactly 82 games."""
        rng, abbrevs = self.rng, [t[0] for t in TEAMS]
        games, day = [], self.opening
        for rnd in range(82):
            rng.shuffle(abbrevs)
            for i in range(0, 32, 2):
                home, away = (abbrevs[i], abbrevs[i + 1]) if rng.random() < 0.5 else (abbrevs[i + 1], abbrevs[i])
                games.append((day + timedelta(days=(i // 2) % 2), home, away))
            day += timedelta(days=2 if rnd % 3 else 3)
        return games

    def _simulate_season(self) -> None:
        self.games: list[dict] = []
        self.boxscores: dict[int, dict] = {}
        for n, (gdate, home, away) in enumerate(sorted(self._schedule_pairs()), start=1):
            gid = int(f"{self.season[:4]}02{n:04d}")
            game = {
                "id": gid, "season": int(self.season), "gameType": 2, "gameDate": gdate.isoformat(),
                "venue": {"default": f"{dict((t[0], t[1]) for t in TEAMS)[home]} Arena"},
                "gameState": "FUT", "homeTeam": {"abbrev": home}, "awayTeam": {"abbrev": away},
            }
            if gdate <= self.played_through:
                box = self._play(gid, home, away)
                game["gameState"] = "OFF"
                game["homeTeam"]["score"] = box["homeTeam"]["score"]
                game["awayTeam"]["score"] = box["awayTeam"]["score"]
                game["gameOutcome"] = {"lastPeriodType": box["_period"]}
                self.boxscores[gid] = box
            self.games.append(game)

    def _pick(self, players: list[dict], k: int, exclude=()) -> list[dict]:
        pool = [p for p in players if p["id"] not in exclude]
        chosen = []
        for _ in range(min(k, len(pool))):
            p = self.rng.choices(pool, weights=[x["_skill"] for x in pool])[0]
            chosen.append(p)
            pool.remove(p)
        return chosen

    def _play(self, gid: int, home: str, away: str) -> dict[str, Any]:
        rng = self.rng
        lineups, lines = {}, {}
        for side, abbrev in (("homeTeam", home), ("awayTeam", away)):
            r = self.rosters[abbrev]
            fwd = sorted(r["forwards"], key=lambda p: -p["_skill"])[:12]
            dfn = sorted(r["defensemen"], key=lambda p: -p["_skill"])[:6]
            goalie = r["goalies"][0] if rng.random() < 0.72 else r["goalies"][1]
            lineups[side] = (fwd, dfn, goalie)
            lines[side] = {p["id"]: {"playerId": p["id"], "sweaterNumber": p["sweaterNumber"],
                                     "name": {"default": f"{p['firstName']['default'][0]}. {p['lastName']['default']}"},
                                     "position": p["positionCode"], "goals": 0, "assists": 0, "points": 0,
                                     "plusMinus": 0, "pim": 0, "sog": 0, "hits": rng.randint(0, 4),
                                     "toi": 0} for p in fwd + dfn}

        def lam(team, opp, home_adv):
            return 2.95 * self.strength[team] / self.strength[opp] * home_adv

        goals = {"homeTeam": self._poisson(lam(home, away, 1.05)), "awayTeam": self._poisson(lam(away, home, 0.96))}
        period = "REG"
        if goals["homeTeam"] == goals["awayTeam"]:
            period = "OT" if rng.random() < 0.65 else "SO"
            winner = "homeTeam" if rng.random() < 0.52 else "awayTeam"
            if period == "OT":
                goals[winner] += 1

        for side, opp in (("homeTeam", "awayTeam"), ("awayTeam", "homeTeam")):
            fwd, dfn, _ = lineups[side]
            skaters = fwd + dfn
            for _ in range(goals[side]):
                scorer = self._pick(skaters, 1)[0]
                lines[side][scorer["id"]]["goals"] += 1
                lines[side][scorer["id"]]["sog"] += 1
                for a in self._pick(skaters, rng.choice([0, 1, 2, 2, 2]), exclude={scorer["id"]}):
                    lines[side][a["id"]]["assists"] += 1
                on_ice = rng.sample(fwd[:9], 3) + rng.sample(dfn, 2)
                for p in on_ice:
                    lines[side][p["id"]]["plusMinus"] += 1
                o_fwd, o_dfn, _ = lineups[opp]
                for p in rng.sample(o_fwd[:9], 3) + rng.sample(o_dfn, 2):
                    lines[opp][p["id"]]["plusMinus"] -= 1
            for i, p in enumerate(skaters):
                line = lines[side][p["id"]]
                line["sog"] += self._poisson(1.4 * p["_skill"] ** 0.5)
                line["pim"] = rng.choice([0] * 32 + [2, 2, 2, 2, 4, 5, 10])
                base = (1250 - i * 55) if p["positionCode"] != "D" else (1450 - (i - 12) * 90)
                line["toi"] = max(360, int(rng.gauss(base, 90)))
        # goalie lines use real goals (a shootout winner isn't a goal against)
        goalie_ga = {"homeTeam": goals["awayTeam"], "awayTeam": goals["homeTeam"]}
        if period == "SO":  # like the NHL, the shootout winner is credited +1 in the final score
            goals[winner] += 1
        box: dict[str, Any] = {"id": gid, "_period": period, "playerByGameStats": {}}
        for side, abbrev in (("homeTeam", home), ("awayTeam", away)):
            fwd, dfn, goalie = lineups[side]
            opp = "awayTeam" if side == "homeTeam" else "homeTeam"
            ga = goalie_ga[side]
            shots_against = max(sum(l["sog"] for l in lines[opp].values()), ga)
            for l in lines[side].values():
                l["points"] = l["goals"] + l["assists"]
                l["toi"] = _toi(l["toi"])
            box[side] = {"abbrev": abbrev, "score": goals[side], "sog": sum(l["sog"] for l in lines[side].values())}
            box["playerByGameStats"][side] = {
                "forwards": [lines[side][p["id"]] for p in fwd],
                "defense": [lines[side][p["id"]] for p in dfn],
                "goalies": [{"playerId": goalie["id"], "position": "G", "goalsAgainst": ga,
                             "shotsAgainst": shots_against, "saves": shots_against - ga,
                             "toi": "60:00", "starter": True}],
            }
        return box

    def _poisson(self, lam: float) -> int:
        l, k, p = pow(2.718281828, -lam), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= l:
                return k
            k += 1

    # ------------------------------------------------------ aggregations
    def _winner(self, gid: int, g: dict) -> str:
        return "homeTeam" if g["homeTeam"]["score"] > g["awayTeam"]["score"] else "awayTeam"

    def _standings(self) -> list[dict]:
        rec = {t[0]: defaultdict(int) for t in TEAMS}
        results = defaultdict(list)
        for g in self.games:
            if g["gameState"] != "OFF":
                continue
            win_side = self._winner(g["id"], g)
            period = g["gameOutcome"]["lastPeriodType"]
            for side, opp in (("homeTeam", "awayTeam"), ("awayTeam", "homeTeam")):
                t = g[side]["abbrev"]
                r = rec[t]
                r["gp"] += 1
                r["gf"] += g[side]["score"]
                r["ga"] += g[opp]["score"]
                if side == win_side:
                    r["w"] += 1
                    r["home_w" if side == "homeTeam" else "road_w"] += 1
                    results[t].append("W")
                elif period == "REG":
                    r["l"] += 1
                    results[t].append("L")
                else:
                    r["otl"] += 1
                    results[t].append("OT")
        out = []
        for abbrev, name, conf, div in TEAMS:
            r, res = rec[abbrev], results[abbrev]
            streak = 0
            for x in reversed(res):
                if x != res[-1]:
                    break
                streak += 1
            out.append({
                "seasonId": int(self.season), "teamAbbrev": {"default": abbrev}, "teamName": {"default": name},
                "conferenceName": conf, "divisionName": div,
                "teamLogo": f"https://assets.nhle.com/logos/nhl/svg/{abbrev}_light.svg",
                "gamesPlayed": r["gp"], "wins": r["w"], "losses": r["l"], "otLosses": r["otl"],
                "points": 2 * r["w"] + r["otl"], "goalFor": r["gf"], "goalAgainst": r["ga"],
                "homeWins": r["home_w"], "roadWins": r["road_w"],
                "streakCode": res[-1] if res else None, "streakCount": streak if res else None,
            })
        return sorted(out, key=lambda x: -x["points"])

    # ------------------------------------------------- public "endpoints"
    def standings(self) -> dict[str, Any]:
        self.live_calls += 1
        return {"standings": self._standings()}

    def roster(self, team_abbrev: str, scope: str = "season") -> dict[str, Any]:
        self.live_calls += 1
        return {g: [{k: v for k, v in p.items() if not k.startswith("_")} for p in players]
                for g, players in self.rosters[team_abbrev].items()}

    def schedule(self, team_abbrev: str, season: str | None = None) -> dict[str, Any]:
        self.live_calls += 1
        games = [{k: v for k, v in g.items()} for g in self.games
                 if team_abbrev in (g["homeTeam"]["abbrev"], g["awayTeam"]["abbrev"])]
        return {"currentSeason": int(self.season), "games": games}

    def boxscore(self, game_id: int) -> dict[str, Any]:
        self.live_calls += 1
        return {k: v for k, v in self.boxscores[game_id].items() if not k.startswith("_")}

    def club_stats(self, team_abbrev: str, season: str | None = None, game_type: int = 2) -> dict[str, Any]:
        self.live_calls += 1
        sk = defaultdict(lambda: defaultdict(float))
        gl = defaultdict(lambda: defaultdict(float))
        for g in self.games:
            if g["gameState"] != "OFF":
                continue
            box = self.boxscores[g["id"]]
            for side in ("homeTeam", "awayTeam"):
                if box[side]["abbrev"] != team_abbrev:
                    continue
                stats = box["playerByGameStats"][side]
                for line in stats["forwards"] + stats["defense"]:
                    s = sk[line["playerId"]]
                    s["gp"] += 1
                    for k in ("goals", "assists", "points", "plusMinus", "pim", "sog"):
                        s[k] += line[k]
                    m, sec = line["toi"].split(":")
                    s["toi"] += int(m) * 60 + int(sec)
                gline = stats["goalies"][0]
                s = gl[gline["playerId"]]
                s["gp"] += 1
                s["ga"] += gline["goalsAgainst"]
                s["sa"] += gline["shotsAgainst"]
                s["sv"] += gline["saves"]
                s["so"] += 1 if gline["goalsAgainst"] == 0 else 0
                won = self._winner(g["id"], g) == side
                period = g["gameOutcome"]["lastPeriodType"]
                s["w" if won else ("l" if period == "REG" else "otl")] += 1
        roster = {p["id"]: p for grp in self.rosters[team_abbrev].values() for p in grp}
        skaters = [{
            "playerId": pid, "positionCode": roster[pid]["positionCode"],
            "firstName": roster[pid]["firstName"], "lastName": roster[pid]["lastName"],
            "gamesPlayed": int(s["gp"]), "goals": int(s["goals"]), "assists": int(s["assists"]),
            "points": int(s["points"]), "plusMinus": int(s["plusMinus"]), "penaltyMinutes": int(s["pim"]),
            "shots": int(s["sog"]), "avgTimeOnIcePerGame": s["toi"] / s["gp"],
        } for pid, s in sk.items()]
        goalies = [{
            "playerId": pid, "positionCode": "G",
            "firstName": roster[pid]["firstName"], "lastName": roster[pid]["lastName"],
            "gamesPlayed": int(s["gp"]), "wins": int(s["w"]), "losses": int(s["l"]),
            "overtimeLosses": int(s["otl"]), "shutouts": int(s["so"]), "saves": int(s["sv"]),
            "shotsAgainst": int(s["sa"]), "goalsAgainst": int(s["ga"]),
            "savePercentage": s["sv"] / s["sa"] if s["sa"] else None,
            "goalsAgainstAverage": s["ga"] / s["gp"] * 1.0,
        } for pid, s in gl.items()]
        return {"season": self.season, "gameType": game_type, "skaters": skaters, "goalies": goalies}
