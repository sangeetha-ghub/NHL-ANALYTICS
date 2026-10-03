-- =====================================================================
-- NHL Analytics Hub — analysis queries (SQLite dialect)
-- Each block starts with "-- name: <id> | <title>" so the notebook and the
-- Streamlit SQL page can load them by name. Keep one statement per block.
-- =====================================================================

-- name: Q01 | Teams ranked by total goals scored
-- description: Aggregation + JOIN + ORDER BY — which team has scored the most goals this season?
SELECT t.team_name,
       t.conference_name,
       s.games_played,
       s.goals_for,
       ROUND(1.0 * s.goals_for / NULLIF(s.games_played, 0), 2) AS goals_per_game
FROM standings s
JOIN teams t ON t.team_id = s.team_id
ORDER BY s.goals_for DESC;

-- name: Q02 | Top 5 point scorers in the league
-- description: JOIN + GROUP BY + ORDER BY/LIMIT — totals are summed across teams for traded players.
SELECT p.first_name || ' ' || p.last_name AS player,
       p.position,
       GROUP_CONCAT(DISTINCT t.team_abbrev) AS teams,
       SUM(ss.games_played) AS gp,
       SUM(ss.goals)        AS goals,
       SUM(ss.assists)      AS assists,
       SUM(ss.points)       AS points
FROM skater_season_stats ss
JOIN players p ON p.player_id = ss.player_id
JOIN teams   t ON t.team_id   = ss.team_id
GROUP BY ss.player_id
ORDER BY points DESC, goals DESC
LIMIT 5;

-- name: Q03 | Players with 20+ goals and 30+ assists
-- description: WHERE with multiple conditions on a per-player season aggregate (derived table).
SELECT player, position, goals, assists, points
FROM (
    SELECT p.first_name || ' ' || p.last_name AS player,
           p.position,
           SUM(ss.goals)   AS goals,
           SUM(ss.assists) AS assists,
           SUM(ss.points)  AS points
    FROM skater_season_stats ss
    JOIN players p ON p.player_id = ss.player_id
    GROUP BY ss.player_id
) AS totals
WHERE goals > 20
  AND assists > 30
ORDER BY points DESC;

-- name: Q04 | Teams above the league-average points total
-- description: Scalar subquery — compares every team to the league mean.
SELECT t.team_name,
       t.division_name,
       s.points,
       ROUND((SELECT AVG(points) FROM standings), 1) AS league_avg_points,
       s.points - ROUND((SELECT AVG(points) FROM standings), 1) AS points_above_avg
FROM standings s
JOIN teams t ON t.team_id = s.team_id
WHERE s.points > (SELECT AVG(points) FROM standings)
ORDER BY s.points DESC;

-- name: Q05 | Divisions with an average team points total above 90
-- description: GROUP BY + HAVING (may legitimately return no rows early in a season).
SELECT t.conference_name,
       t.division_name,
       COUNT(*)                AS teams,
       ROUND(AVG(s.points), 1) AS avg_team_points,
       MAX(s.points)           AS best_team_points
FROM standings s
JOIN teams t ON t.team_id = s.team_id
GROUP BY t.conference_name, t.division_name
HAVING AVG(s.points) > 90
ORDER BY avg_team_points DESC;

-- name: Q06 | Best save percentage (min. 20 games played)
-- description: JOIN + WHERE threshold + ORDER BY on a rate stat.
SELECT p.first_name || ' ' || p.last_name AS goalie,
       t.team_abbrev AS team,
       g.games_played,
       g.wins,
       ROUND(g.save_pct, 3)          AS save_pct,
       ROUND(g.goals_against_avg, 2) AS gaa,
       g.shutouts
FROM goalie_season_stats g
JOIN players p ON p.player_id = g.player_id
JOIN teams   t ON t.team_id   = g.team_id
WHERE g.games_played >= 20
ORDER BY g.save_pct DESC
LIMIT 10;

-- name: Q07 | Top scorer on every team
-- description: Window function (ROW_NUMBER … PARTITION BY) — best point producer per club.
SELECT team_name, player, position, goals, assists, points
FROM (
    SELECT t.team_name,
           p.first_name || ' ' || p.last_name AS player,
           p.position, ss.goals, ss.assists, ss.points,
           ROW_NUMBER() OVER (PARTITION BY ss.team_id ORDER BY ss.points DESC, ss.goals DESC) AS rn
    FROM skater_season_stats ss
    JOIN players p ON p.player_id = ss.player_id
    JOIN teams   t ON t.team_id   = ss.team_id
) AS ranked
WHERE rn = 1
ORDER BY points DESC;

-- name: Q08 | Home vs. road win split
-- description: Derived columns + ORDER BY — which teams depend most on home ice?
SELECT t.team_name,
       s.wins,
       s.home_wins,
       s.road_wins,
       ROUND(100.0 * s.home_wins / NULLIF(s.wins, 0), 1) AS pct_wins_at_home,
       s.home_wins - s.road_wins                          AS home_minus_road
FROM standings s
JOIN teams t ON t.team_id = s.team_id
ORDER BY home_minus_road DESC;

-- name: Q09 | Biggest blowouts of the season
-- description: Self-join of teams (home + away aliases) on completed games.
SELECT g.game_date,
       h.team_abbrev AS home,
       g.home_score,
       g.away_score,
       a.team_abbrev AS away,
       ABS(g.home_score - g.away_score) AS margin,
       g.venue_name
FROM games g
JOIN teams h ON h.team_id = g.home_team_id
JOIN teams a ON a.team_id = g.away_team_id
WHERE g.game_state IN ('OFF', 'FINAL')
ORDER BY margin DESC, (g.home_score + g.away_score) DESC
LIMIT 10;

-- name: Q10 | Points-per-game leaders from game logs (min. 40 games)
-- description: GROUP BY + HAVING on the per-game fact table.
SELECT p.first_name || ' ' || p.last_name AS player,
       t.team_abbrev AS team,
       COUNT(*)                           AS games,
       SUM(gs.points)                     AS points,
       ROUND(1.0 * SUM(gs.points) / COUNT(*), 2) AS points_per_game,
       SUM(gs.shots_on_goal)              AS shots
FROM game_stats gs
JOIN players p ON p.player_id = gs.player_id
JOIN teams   t ON t.team_id   = p.team_id
GROUP BY gs.player_id
HAVING COUNT(*) >= 40
ORDER BY points_per_game DESC
LIMIT 15;

-- name: Q11 | Players with 2x their team's average points
-- description: Correlated subquery — each skater vs. twice the mean of his own team (min. 20 GP).
SELECT t.team_abbrev AS team,
       p.first_name || ' ' || p.last_name AS player,
       ss.points,
       ROUND((SELECT AVG(x.points) FROM skater_season_stats x
              WHERE x.team_id = ss.team_id AND x.games_played >= 20), 1) AS team_avg_points
FROM skater_season_stats ss
JOIN players p ON p.player_id = ss.player_id
JOIN teams   t ON t.team_id   = ss.team_id
WHERE ss.games_played >= 20
  AND ss.points > 2 * (SELECT AVG(x.points) FROM skater_season_stats x
                       WHERE x.team_id = ss.team_id AND x.games_played >= 20)
ORDER BY ss.points DESC;

-- name: Q12 | Goal differential and points % by conference
-- description: GROUP BY with multiple aggregates.
SELECT t.conference_name,
       SUM(s.goals_for)                     AS goals_for,
       SUM(s.goals_against)                 AS goals_against,
       SUM(s.goals_for - s.goals_against)   AS goal_diff,
       ROUND(100.0 * SUM(s.points) / NULLIF(2 * SUM(s.games_played), 0), 1) AS points_pct
FROM standings s
JOIN teams t ON t.team_id = s.team_id
GROUP BY t.conference_name
ORDER BY points_pct DESC;

-- name: Q13 | League scoring by month
-- description: Date functions (strftime) + GROUP BY on completed games.
SELECT strftime('%Y-%m', g.game_date)                 AS month,
       COUNT(*)                                       AS games,
       SUM(g.home_score + g.away_score)               AS total_goals,
       ROUND(AVG(g.home_score + g.away_score), 2)     AS goals_per_game
FROM games g
WHERE g.game_state IN ('OFF', 'FINAL')
GROUP BY month
ORDER BY month;

-- name: Q14 | Penalty-minute leaders
-- description: JOIN + ORDER BY — the league's most penalised skaters.
SELECT p.first_name || ' ' || p.last_name AS player,
       t.team_abbrev AS team,
       p.position,
       ss.games_played,
       ss.penalty_min
FROM skater_season_stats ss
JOIN players p ON p.player_id = ss.player_id
JOIN teams   t ON t.team_id   = ss.team_id
ORDER BY ss.penalty_min DESC
LIMIT 10;

-- name: Q15 | Roster make-up by birth country
-- description: GROUP BY + HAVING on the players dimension.
SELECT birth_country,
       COUNT(*)                                        AS players,
       SUM(CASE WHEN position = 'G' THEN 1 ELSE 0 END) AS goalies,
       ROUND(AVG(height_cm), 1)                        AS avg_height_cm
FROM players
WHERE birth_country IS NOT NULL
GROUP BY birth_country
HAVING COUNT(*) >= 5
ORDER BY players DESC;
