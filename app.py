"""NHL Analytics Hub — Streamlit dashboard.

    streamlit run app.py

Every page reads from the SQLite database built by ``run_pipeline.py``.
Filters are pushed down into SQL: the WHERE clause is assembled from the
filters the user actually set (see ``WhereBuilder``) and all values are bound
parameters. The database is opened read-only.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from nhl_pipeline import config  # noqa: E402
from nhl_pipeline.db import connect, load_named_queries  # noqa: E402
from nhl_pipeline.query_builder import WhereBuilder  # noqa: E402

try:
    from streamlit_option_menu import option_menu
except ImportError:  # keeps the app usable if the component isn't installed
    option_menu = None

st.set_page_config(page_title="NHL Analytics Hub", page_icon="🏒", layout="wide")

# Rink palette: ice, boards, blue line, goal-line red
ICE, BOARDS, BLUE, RED, MUTED = "#F4F7F9", "#1B2430", "#1F4E9C", "#C8102E", "#5B6B7C"

st.markdown(
    f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Barlow:wght@400;500;600&display=swap');
    html, body, [class*="css"], .stMarkdown, .stDataFrame {{ font-family: 'Barlow', system-ui, sans-serif; }}
    h1, h2, h3 {{ font-family: 'Barlow Condensed', 'Barlow', sans-serif !important; letter-spacing: .01em; color: {BOARDS}; }}
    h1 {{ border-bottom: 3px solid {RED}; padding-bottom: .2rem; margin-bottom: .9rem; display: inline-block; }}
    [data-testid="stMetricValue"] {{ font-family: 'Barlow Condensed', sans-serif; color: {BLUE}; }}
    [data-testid="stMetricLabel"] p {{ color: {MUTED}; font-weight: 500; }}
    .hub-sub {{ color: {MUTED}; margin-top: .25rem; margin-bottom: 1rem; }}
    .hub-highlight-name {{ font-family: 'Barlow Condensed', sans-serif; font-size: 1.45rem; font-weight: 700; color: {BOARDS}; line-height: 1.1; }}
    .hub-highlight-stat {{ color: {MUTED}; }}
    </style>
    """,
    unsafe_allow_html=True,
)

DB_PATH = Path(os.getenv("NHL_DB_PATH", str(config.DB_PATH)))  # read at run time
FORWARD_CODES = ["C", "L", "R", "LW", "RW", "F"]  # live API uses L/R for wingers
PAGES = ["Home", "Standings", "Team Info", "Player Search", "Game Results", "Leaderboards", "SQL Query"]
ICONS = ["house", "list-ol", "shield", "person-badge", "calendar-event", "trophy", "terminal"]


# =============================================================== data access
@st.cache_data(ttl=300, show_spinner=False)
def run_query(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = connect(DB_PATH, read_only=True)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def safe_query(sql: str, params: tuple | list = ()) -> pd.DataFrame | None:
    """Run SQL and turn failures into readable messages instead of stack traces."""
    try:
        return run_query(sql, tuple(params))
    except FileNotFoundError:
        st.error(f"No database at `{DB_PATH}`. Build it first: `python run_pipeline.py` "
                 "(or `python run_pipeline.py --source demo` offline).")
    except (sqlite3.Error, pd.errors.DatabaseError) as err:
        st.error(f"The query failed: {err}")
    return None


def scalar(sql: str, params: tuple = (), default=None):
    df = safe_query(sql, params)
    if df is None or df.empty or pd.isna(df.iat[0, 0]):
        return default
    return df.iat[0, 0]


def run_info() -> dict:
    f = DB_PATH.with_suffix(".run.json")
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def title(text: str, sub: str | None = None) -> None:
    st.title(text)
    if sub:
        st.markdown(f"<div class='hub-sub'>{sub}</div>", unsafe_allow_html=True)


def empty(msg: str) -> None:
    st.info(msg)


def team_options() -> dict[str, int]:
    df = safe_query("SELECT team_name, team_id FROM teams ORDER BY team_name")
    return {} if df is None else dict(zip(df.team_name, df.team_id))


def state_label(code: str) -> str:
    return config.GAME_STATE_LABELS.get(code, code)


def bar(df: pd.DataFrame, x: str, y: str, color: str = BLUE, height: int = 320, sort: str = "-x"):
    return (alt.Chart(df).mark_bar(color=color, cornerRadiusEnd=2)
            .encode(x=alt.X(x, title=None),
                    y=alt.Y(y, sort=sort, title=None, axis=alt.Axis(labelOverlap=False, labelLimit=220)),
                    tooltip=list(df.columns)).properties(height=height))


# ===================================================================== pages
def page_home() -> None:
    title("NHL Analytics Hub", f"Season {config.SEASON[:4]}–{config.SEASON[4:]} at a glance")

    teams = scalar("SELECT COUNT(*) FROM teams", default=0)
    players = scalar("SELECT COUNT(*) FROM players", default=0)
    played = scalar("SELECT COUNT(*) FROM games WHERE game_state IN ('OFF','FINAL') AND game_type = 2", default=0)
    scheduled = scalar("SELECT COUNT(*) FROM games WHERE game_type = 2", default=0)
    goals = scalar("SELECT SUM(home_score + away_score) FROM games WHERE game_state IN ('OFF','FINAL') "
                   "AND game_type = 2", default=0)

    cols = st.columns(4)
    for col, (label, value, help_) in zip(cols, [
        ("Teams", f"{teams}", None),
        ("Players on rosters", f"{players:,}", None),
        ("Regular-season games played", f"{played:,}", f"{scheduled:,} scheduled"),
        ("Goals scored", f"{int(goals):,}", f"{goals / played:.2f} per game" if played else None),
    ]):
        with col, st.container(border=True):
            st.metric(label, value)
            st.caption(help_ or "\u00a0")

    st.subheader("Leading the way")
    top = safe_query("""
        SELECT p.first_name || ' ' || p.last_name AS name, t.team_abbrev AS team,
               SUM(ss.goals) g, SUM(ss.assists) a, SUM(ss.points) pts
        FROM skater_season_stats ss JOIN players p USING (player_id) JOIN teams t ON t.team_id = p.team_id
        GROUP BY ss.player_id ORDER BY pts DESC, g DESC LIMIT 1""")
    goalie = safe_query("""
        SELECT p.first_name || ' ' || p.last_name AS name, t.team_abbrev AS team,
               g.save_pct, g.goals_against_avg, g.games_played
        FROM goalie_season_stats g JOIN players p USING (player_id) JOIN teams t ON t.team_id = g.team_id
        WHERE g.games_played >= 20 ORDER BY g.save_pct DESC LIMIT 1""")
    leader = safe_query("""
        SELECT t.team_name AS name, t.logo_url, s.points, s.wins, s.losses, s.ot_losses
        FROM standings s JOIN teams t USING (team_id) ORDER BY s.points DESC, s.wins DESC LIMIT 1""")

    c1, c2, c3 = st.columns(3)
    with c1, st.container(border=True):
        st.caption("Top point scorer")
        if top is not None and not top.empty:
            r = top.iloc[0]
            st.markdown(f"<div class='hub-highlight-name'>{r['name']} ({r.team})</div>"
                        f"<div class='hub-highlight-stat'>{r.pts} points: {r.g} goals, {r.a} assists</div>",
                        unsafe_allow_html=True)
        else:
            st.write("No skater stats loaded.")
    with c2, st.container(border=True):
        st.caption("Best save percentage (20+ games)")
        if goalie is not None and not goalie.empty:
            r = goalie.iloc[0]
            st.markdown(f"<div class='hub-highlight-name'>{r['name']} ({r.team})</div>"
                        f"<div class='hub-highlight-stat'>{r.save_pct:.3f} save %, {r.goals_against_avg:.2f} GAA "
                        f"in {r.games_played} games</div>", unsafe_allow_html=True)
        else:
            st.write("No goalie has 20 games yet.")
    with c3, st.container(border=True):
        st.caption("League leader")
        if leader is not None and not leader.empty:
            r = leader.iloc[0]
            st.markdown(f"<div class='hub-highlight-name'>{r['name']}</div>"
                        f"<div class='hub-highlight-stat'>{r.points} points ({r.wins}-{r.losses}-{r.ot_losses})</div>",
                        unsafe_allow_html=True)

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Goals for, by team")
        gf = safe_query("SELECT t.team_abbrev AS team, s.goals_for AS goals FROM standings s "
                        "JOIN teams t USING (team_id) ORDER BY s.goals_for DESC")
        if gf is not None and not gf.empty:
            st.altair_chart(bar(gf, "goals:Q", "team:N", height=560), width="stretch")
    with right:
        st.subheader("Goals per game, by month")
        monthly = safe_query("""
            SELECT strftime('%Y-%m', game_date) AS month, ROUND(AVG(home_score + away_score), 2) AS goals_per_game
            FROM games WHERE game_state IN ('OFF','FINAL') AND game_type = 2 GROUP BY month ORDER BY month""")
        if monthly is not None and not monthly.empty:
            line = (alt.Chart(monthly).mark_line(point=True, color=RED)
                    .encode(x=alt.X("month:N", title=None, axis=alt.Axis(labelAngle=0)),
                            y=alt.Y("goals_per_game:Q", title=None, scale=alt.Scale(zero=False)),
                            tooltip=["month", "goals_per_game"]).properties(height=250))
            st.altair_chart(line, width="stretch")
        st.subheader("Conference race")
        conf = safe_query("""
            SELECT t.conference_name AS conference, SUM(s.points) AS points,
                   SUM(s.goals_for - s.goals_against) AS goal_diff
            FROM standings s JOIN teams t USING (team_id) GROUP BY conference""")
        if conf is not None and not conf.empty:
            cc = st.columns(len(conf))
            for col, (_, r) in zip(cc, conf.iterrows()):
                with col, st.container(border=True):
                    st.metric(r.conference, f"{r.points} pts", f"{r.goal_diff:+d} goal diff",
                              delta_color="normal" if r.goal_diff >= 0 else "inverse")


def page_standings() -> None:
    title("Standings", "Sorted by points. Ties break on wins, then goal differential.")
    c1, c2, c3 = st.columns([2, 2, 3])
    conference = c1.radio("Conference", ["All", "Eastern", "Western"], horizontal=True)
    div_where = WhereBuilder().add("conference_name = ?", conference)
    divs = safe_query(f"SELECT DISTINCT division_name FROM teams{div_where.sql()} ORDER BY 1", div_where.params)
    division = c2.selectbox("Division", ["All"] + ([] if divs is None else divs.division_name.tolist()))
    search = c3.text_input("Team name contains", placeholder="e.g. Rangers")

    where = (WhereBuilder()
             .add("t.conference_name = ?", conference)
             .add("t.division_name = ?", division)
             .add_like("t.team_name", search))
    df = safe_query(f"""
        SELECT t.logo_url AS logo, t.team_name AS team, t.conference_name AS conference,
               t.division_name AS division, s.games_played AS gp, s.wins AS w, s.losses AS l,
               s.ot_losses AS otl, s.points AS pts,
               ROUND(1.0 * s.points / NULLIF(2 * s.games_played, 0), 3) AS pts_pct,
               s.goals_for AS gf, s.goals_against AS ga, s.goals_for - s.goals_against AS diff,
               s.home_wins || '-' || s.road_wins AS "home-road W",
               COALESCE(s.streak_type || s.streak_count, '') AS streak
        FROM standings s JOIN teams t USING (team_id)
        {where.sql()}
        ORDER BY s.points DESC, s.wins DESC, diff DESC""", where.params)
    if df is None:
        return
    if df.empty:
        empty("No teams match these filters. Clear the search box or pick another division.")
        return
    df.insert(0, "rank", range(1, len(df) + 1))
    st.dataframe(df, hide_index=True, height=min(38 * (len(df) + 1), 1250), column_config={
        "logo": st.column_config.ImageColumn("", width="small"),
        "pts_pct": st.column_config.ProgressColumn("pts %", min_value=0, max_value=1, format="%.3f"),
        "diff": st.column_config.NumberColumn("diff", format="%+d"),
    })


def page_team() -> None:
    teams = team_options()
    if not teams:
        empty("No teams loaded yet. Run the pipeline first.")
        return
    default = st.query_params.get("team")
    names = list(teams)
    idx = next((i for i, n in enumerate(names) if default and default.lower() in n.lower()), 0)
    name = st.selectbox("Team", names, index=idx)
    team_id = int(teams[name])

    info = safe_query("""
        SELECT t.*, s.games_played, s.wins, s.losses, s.ot_losses, s.points, s.goals_for,
               s.goals_against, s.streak_type, s.streak_count,
               (SELECT COUNT(*) + 1 FROM standings x WHERE x.points > s.points) AS league_rank
        FROM teams t LEFT JOIN standings s USING (team_id) WHERE t.team_id = ?""", (team_id,))
    if info is None or info.empty:
        return
    r = info.iloc[0]
    logo_col, head_col = st.columns([1, 6])
    if isinstance(r.logo_url, str) and r.logo_url:
        logo_col.image(r.logo_url, width=110)
    with head_col:
        title(r.team_name, f"{r.conference_name} Conference, {r.division_name} Division")

    if pd.notna(r.games_played):
        m = st.columns(5)
        for col, (label, value) in zip(m, [
            ("Record (W-L-OTL)", f"{int(r.wins)}-{int(r.losses)}-{int(r.ot_losses)}"),
            ("Points", f"{int(r.points)}"),
            ("League rank", f"#{int(r.league_rank)}"),
            ("Goals for / against", f"{int(r.goals_for)} / {int(r.goals_against)}"),
            ("Streak", f"{r.streak_type or ''}{'' if pd.isna(r.streak_count) else int(r.streak_count)}" or "–"),
        ]):
            with col, st.container(border=True):
                st.metric(label, value)

    st.subheader("Roster")
    skaters = safe_query("""
        SELECT p.headshot_url AS photo, p.jersey_number AS "#", p.first_name || ' ' || p.last_name AS player,
               p.position AS pos, p.shoots_catches AS shoots, p.birth_country AS born,
               CAST((julianday('now') - julianday(p.birth_date)) / 365.25 AS INT) AS age,
               p.height_cm, p.weight_kg,
               ss.games_played AS gp, ss.goals AS g, ss.assists AS a, ss.points AS pts,
               ss.plus_minus AS "+/-", ss.avg_toi AS toi
        FROM players p LEFT JOIN skater_season_stats ss ON ss.player_id = p.player_id AND ss.team_id = p.team_id
        WHERE p.team_id = ? AND p.position <> 'G'
        ORDER BY pts DESC NULLS LAST, p.last_name""", (team_id,))
    goalies = safe_query("""
        SELECT p.headshot_url AS photo, p.jersey_number AS "#", p.first_name || ' ' || p.last_name AS player,
               p.shoots_catches AS catches, p.birth_country AS born,
               CAST((julianday('now') - julianday(p.birth_date)) / 365.25 AS INT) AS age,
               g.games_played AS gp, g.wins AS w, g.losses AS l, g.ot_losses AS otl,
               ROUND(g.save_pct, 3) AS "sv%", ROUND(g.goals_against_avg, 2) AS gaa, g.shutouts AS so
        FROM players p LEFT JOIN goalie_season_stats g ON g.player_id = p.player_id AND g.team_id = p.team_id
        WHERE p.team_id = ? AND p.position = 'G'
        ORDER BY gp DESC NULLS LAST""", (team_id,))
    photo = {"photo": st.column_config.ImageColumn("", width="small")}
    # hide the photo column when the source has no headshots (e.g. demo data)
    skaters = skaters.drop(columns="photo") if skaters is not None and skaters.photo.isna().all() else skaters
    goalies = goalies.drop(columns="photo") if goalies is not None and goalies.photo.isna().all() else goalies
    if skaters is not None:
        fwd = skaters[skaters.pos.isin(FORWARD_CODES)]
        dfn = skaters[skaters.pos == "D"]
        t1, t2, t3 = st.tabs([f"Forwards ({len(fwd)})", f"Defense ({len(dfn)})",
                              f"Goalies ({0 if goalies is None else len(goalies)})"])
        t1.dataframe(fwd, hide_index=True, column_config=photo)
        t2.dataframe(dfn, hide_index=True, column_config=photo)
        if goalies is not None:
            t3.dataframe(goalies, hide_index=True, column_config=photo)

    st.subheader("Last 10 results")
    games = safe_query("""
        SELECT g.game_date AS date,
               CASE WHEN g.home_team_id = ? THEN 'vs ' || a.team_abbrev ELSE '@ ' || h.team_abbrev END AS opponent,
               CASE WHEN g.home_team_id = ? THEN g.home_score ELSE g.away_score END AS gf,
               CASE WHEN g.home_team_id = ? THEN g.away_score ELSE g.home_score END AS ga
        FROM games g JOIN teams h ON h.team_id = g.home_team_id JOIN teams a ON a.team_id = g.away_team_id
        WHERE (g.home_team_id = ? OR g.away_team_id = ?) AND g.game_state IN ('OFF','FINAL')
        ORDER BY g.game_date DESC LIMIT 10""", (team_id,) * 5)
    if games is not None and not games.empty:
        games["result"] = games.apply(lambda x: "W" if x.gf > x.ga else "L", axis=1)
        st.dataframe(games, hide_index=True)
    else:
        empty("No completed games for this team yet.")


def page_players() -> None:
    title("Player search", "Find a player, then pick one from the results for the full profile.")
    teams = team_options()
    c1, c2, c3 = st.columns([3, 2, 2])
    name = c1.text_input("Name contains", placeholder="first or last name")
    group = c2.radio("Position", ["All", "Forwards", "Defense", "Goalies"], horizontal=True)
    team = c3.selectbox("Team", ["All"] + list(teams))

    where = WhereBuilder().add_like("p.first_name || ' ' || p.last_name", name)
    where.add_in("p.position", {"Forwards": FORWARD_CODES, "Defense": ["D"], "Goalies": ["G"]}.get(group))
    where.add("p.team_id = ?", teams.get(team) if team != "All" else None)

    results = safe_query(f"""
        SELECT p.player_id, p.first_name || ' ' || p.last_name AS player, p.position AS pos,
               t.team_abbrev AS team, p.jersey_number AS "#",
               COALESCE(SUM(ss.points), 0) AS pts, COALESCE(SUM(gs.wins), 0) AS "goalie wins"
        FROM players p JOIN teams t USING (team_id)
        LEFT JOIN skater_season_stats ss ON ss.player_id = p.player_id
        LEFT JOIN goalie_season_stats gs ON gs.player_id = p.player_id
        {where.sql()}
        GROUP BY p.player_id ORDER BY pts DESC, "goalie wins" DESC, player LIMIT 500""", where.params)
    if results is None:
        return
    if results.empty:
        empty("No players match. Try part of a name, or set Position and Team back to All.")
        return
    total = scalar(f"SELECT COUNT(*) FROM players p{where.sql()}", tuple(where.params), default=len(results))
    st.caption(f"{total:,} player(s) found" + (f", showing the top {len(results)}" if total > len(results) else ""))
    st.dataframe(results.drop(columns=["player_id"]), hide_index=True, height=260)

    labels = {f"{r.player} ({r.team}, {r.pos})": int(r.player_id) for r in results.itertuples()}
    choice = st.selectbox("Open profile", list(labels))
    show_profile(labels[choice])


def show_profile(player_id: int) -> None:
    p = safe_query("""SELECT p.*, t.team_name, t.logo_url FROM players p JOIN teams t USING (team_id)
                      WHERE p.player_id = ?""", (player_id,))
    if p is None or p.empty:
        return
    r = p.iloc[0]
    st.divider()
    pic, bio = st.columns([1, 5])
    if isinstance(r.headshot_url, str) and r.headshot_url:
        pic.image(r.headshot_url, width=140)
    elif isinstance(r.logo_url, str):
        pic.image(r.logo_url, width=110)
    with bio:
        st.header(f"{r.first_name} {r.last_name}")
        details = [f"#{int(r.jersey_number)}" if pd.notna(r.jersey_number) else None, r.position, r.team_name,
                   f"born {r.birth_date} ({r.birth_country})" if r.birth_date else None,
                   f"{r.height_cm:.0f} cm, {r.weight_kg:.0f} kg" if pd.notna(r.height_cm) and pd.notna(r.weight_kg) else None,
                   f"{'catches' if r.position == 'G' else 'shoots'} {r.shoots_catches}" if r.shoots_catches else None]
        st.markdown(f"<div class='hub-sub'>{', '.join(d for d in details if d)}</div>", unsafe_allow_html=True)

    if r.position == "G":
        s = safe_query("""SELECT SUM(games_played) gp, SUM(wins) w, SUM(losses) l, SUM(ot_losses) otl,
                                 SUM(saves) sv, SUM(shutouts) so,
                                 SUM(save_pct * games_played) / NULLIF(SUM(games_played), 0) svp,
                                 SUM(goals_against_avg * games_played) / NULLIF(SUM(games_played), 0) gaa
                          FROM goalie_season_stats WHERE player_id = ?""", (player_id,))
        if s is None or pd.isna(s.gp.iat[0]):
            empty("No season stats for this goalie.")
            return
        s = s.iloc[0]
        cards = [("Games", int(s.gp)), ("Record", f"{int(s.w)}-{int(s.l)}-{int(s.otl)}"),
                 ("Save %", f"{s.svp:.3f}" if pd.notna(s.svp) else "–"),
                 ("GAA", f"{s.gaa:.2f}" if pd.notna(s.gaa) else "–"), ("Saves", f"{int(s.sv):,}"),
                 ("Shutouts", int(s.so))]
        for col, (label, value) in zip(st.columns(6), cards):
            with col, st.container(border=True):
                st.metric(label, value)
        league = safe_query("""SELECT AVG(save_pct) svp, AVG(goals_against_avg) gaa
                               FROM goalie_season_stats WHERE games_played >= 10""")
        if league is not None and pd.notna(s.svp):
            comp = pd.DataFrame({"who": ["This goalie", "League avg (10+ GP)"],
                                 "save %": [s.svp, league.svp.iat[0]]})
            st.altair_chart(alt.Chart(comp).mark_bar().encode(
                x=alt.X("save %:Q", scale=alt.Scale(domain=[0.85, 0.95])), y=alt.Y("who:N", title=None),
                color=alt.Color("who:N", scale=alt.Scale(range=[BLUE, "#AEB9C4"]), legend=None),
                tooltip=["who", alt.Tooltip("save %:Q", format=".3f")]).properties(height=120), width="stretch")
        return

    s = safe_query("""SELECT SUM(games_played) gp, SUM(goals) g, SUM(assists) a, SUM(points) pts,
                             SUM(plus_minus) pm, SUM(penalty_min) pim, SUM(shots) shots
                      FROM skater_season_stats WHERE player_id = ?""", (player_id,))
    if s is None or pd.isna(s.gp.iat[0]):
        empty("No season stats for this player.")
        return
    s = s.iloc[0]
    cards = [("Games", int(s.gp)), ("Goals", int(s.g)), ("Assists", int(s.a)), ("Points", int(s.pts)),
             ("+/-", f"{int(s.pm):+d}"), ("Shooting %", f"{100 * s.g / s.shots:.1f}" if s.shots else "–")]
    for col, (label, value) in zip(st.columns(6), cards):
        with col, st.container(border=True):
            st.metric(label, value)

    left, right = st.columns([3, 2])
    log = safe_query("""SELECT g.game_date, gs.goals, gs.assists, gs.points, gs.shots_on_goal, gs.toi
                        FROM game_stats gs JOIN games g USING (game_id)
                        WHERE gs.player_id = ? ORDER BY g.game_date""", (player_id,))
    with left:
        st.subheader("Points over the season")
        if log is not None and not log.empty:
            log["cumulative points"] = log.points.cumsum()
            log["game_date"] = pd.to_datetime(log.game_date)
            st.altair_chart(alt.Chart(log).mark_area(color=BLUE, opacity=.25, line={"color": BLUE})
                            .encode(x=alt.X("game_date:T", title=None), y=alt.Y("cumulative points:Q", title=None),
                                    tooltip=["game_date:T", "goals", "assists", "cumulative points"])
                            .properties(height=260), width="stretch")
        else:
            empty("No game-by-game lines for this player.")
    with right:
        st.subheader("Per game vs. position avg")
        pos_filter = "('D')" if r.position == "D" else "(" + ", ".join(f"'{c}'" for c in FORWARD_CODES) + ")"
        avg = safe_query(f"""
            SELECT AVG(1.0 * ss.goals / ss.games_played) g, AVG(1.0 * ss.assists / ss.games_played) a,
                   AVG(1.0 * ss.shots / ss.games_played) sog
            FROM skater_season_stats ss JOIN players p USING (player_id)
            WHERE ss.games_played >= 20 AND p.position IN {pos_filter}""")
        if avg is not None and s.gp:
            comp = pd.DataFrame({
                "stat": ["Goals", "Assists", "Shots"] * 2,
                "who": ["Player"] * 3 + ["Position avg"] * 3,
                "per game": [s.g / s.gp, s.a / s.gp, s.shots / s.gp,
                             avg.g.iat[0], avg.a.iat[0], avg.sog.iat[0]],
            })
            st.altair_chart(alt.Chart(comp).mark_bar().encode(
                x=alt.X("per game:Q", title=None),
                y=alt.Y("stat:N", title=None, sort=["Goals", "Assists", "Shots"]),
                yOffset=alt.YOffset("who:N"),
                color=alt.Color("who:N", scale=alt.Scale(range=[BLUE, "#AEB9C4"]),
                                legend=alt.Legend(title=None, orient="bottom")),
                tooltip=["stat", "who", alt.Tooltip("per game:Q", format=".2f")]).properties(height=240),
                width="stretch")
    if log is not None and not log.empty:
        with st.expander("Game log"):
            st.dataframe(log.sort_values("game_date", ascending=False), hide_index=True)


def page_games() -> None:
    title("Game results", "Filter by date, team, state and game type. Filtering happens in SQL.")
    bounds = safe_query("SELECT MIN(game_date) lo, MAX(game_date) hi FROM games")
    if bounds is None or pd.isna(bounds.lo.iat[0]):
        empty("No games loaded yet. Run the pipeline first.")
        return
    lo, hi = date.fromisoformat(bounds.lo.iat[0]), date.fromisoformat(bounds.hi.iat[0])
    teams = team_options()

    c1, c2, c3, c4 = st.columns([3, 2, 3, 2])
    picked = c1.date_input("Date range", value=(lo, hi), min_value=lo, max_value=hi)
    team = c2.selectbox("Team", ["All"] + list(teams))
    state = c3.radio("Game state", ["All", "Final", "Upcoming", "Live"], horizontal=True)
    gtype = c4.selectbox("Game type", ["All"] + list(config.GAME_TYPE_LABELS.values()), index=2)

    if isinstance(picked, (list, tuple)):  # range picker: 0, 1 (mid-selection) or 2 dates
        start = picked[0] if picked else None
        end = picked[1] if len(picked) > 1 else start
    else:
        start = end = picked
    states = {"Final": ["OFF", "FINAL"], "Upcoming": ["FUT", "PRE"], "Live": ["LIVE", "CRIT"]}.get(state)
    type_code = {v: k for k, v in config.GAME_TYPE_LABELS.items()}.get(gtype)
    team_id = teams.get(team)

    where = (WhereBuilder()
             .add("g.game_date >= ?", start.isoformat() if start else None)
             .add("g.game_date <= ?", end.isoformat() if end else None)
             .add("(g.home_team_id = ? OR g.away_team_id = ?)", team_id, team_id)
             .add("g.game_type = ?", type_code)
             .add_in("g.game_state", states))
    df = safe_query(f"""
        SELECT g.game_id, g.game_date AS date, a.team_name AS away, g.away_score AS "away g",
               g.home_score AS "home g", h.team_name AS home, g.game_state AS state,
               g.game_type AS type, g.venue_name AS venue
        FROM games g JOIN teams h ON h.team_id = g.home_team_id JOIN teams a ON a.team_id = g.away_team_id
        {where.sql()}
        ORDER BY g.game_date DESC, g.game_id DESC""", where.params)
    if df is None:
        return
    if df.empty:
        empty("No games match these filters. Widen the date range or set Game state to All.")
        return
    # friendly labels are applied in the display layer; the DB keeps raw API codes
    df["state"] = df.state.map(state_label)
    df["type"] = df.type.map(config.GAME_TYPE_LABELS).fillna(df.type.astype(str))
    finals = df[df.state == "Final"]
    m1, m2, m3 = st.columns(3)
    for col, (label, value) in zip((m1, m2, m3), [
        ("Games found", f"{len(df):,}"),
        ("Completed", f"{len(finals):,}"),
        ("Goals per completed game", f"{(finals['away g'] + finals['home g']).mean():.2f}" if len(finals) else "–"),
    ]):
        with col, st.container(border=True):
            st.metric(label, value)
    st.dataframe(df, hide_index=True, column_config={"game_id": st.column_config.NumberColumn(format="%d")})
    with st.expander("SQL used for this view"):
        st.code(f"WHERE {' AND '.join(where.conditions) or '1=1'}\n-- params: {where.params}", language="sql")


def page_leaderboards() -> None:
    title("Leaderboards")
    c1, c2 = st.columns([2, 5])
    min_gp = c1.slider("Minimum games played", 0, 60, 20, step=5)
    top_n = c2.select_slider("Show top", [5, 10, 15, 25], value=10)

    def skater_board(metric: str, label: str, ascending: bool = False):
        order = "ASC" if ascending else "DESC"
        return safe_query(f"""
            SELECT p.first_name || ' ' || p.last_name AS player, t.team_abbrev AS team, p.position AS pos,
                   SUM(ss.games_played) AS gp, SUM(ss.{metric}) AS "{label}"
            FROM skater_season_stats ss JOIN players p USING (player_id) JOIN teams t ON t.team_id = p.team_id
            GROUP BY ss.player_id HAVING SUM(ss.games_played) >= ?
            ORDER BY "{label}" {order}, gp ASC LIMIT ?""", (min_gp, top_n))

    def goalie_board(expr: str, label: str, ascending: bool = False, fmt: str | None = None):
        order = "ASC" if ascending else "DESC"
        return safe_query(f"""
            SELECT p.first_name || ' ' || p.last_name AS player, t.team_abbrev AS team,
                   g.games_played AS gp, {expr} AS "{label}"
            FROM goalie_season_stats g JOIN players p USING (player_id) JOIN teams t ON t.team_id = g.team_id
            WHERE g.games_played >= ? ORDER BY "{label}" {order} LIMIT ?""", (min_gp, top_n))

    boards = {
        "Points": lambda: skater_board("points", "points"),
        "Goals": lambda: skater_board("goals", "goals"),
        "Assists": lambda: skater_board("assists", "assists"),
        "Plus/minus": lambda: skater_board("plus_minus", "+/-"),
        "Penalty minutes": lambda: skater_board("penalty_min", "PIM"),
        "Save %": lambda: goalie_board("ROUND(g.save_pct, 3)", "sv%"),
        "GAA": lambda: goalie_board("ROUND(g.goals_against_avg, 2)", "GAA", ascending=True),
        "Goalie wins": lambda: goalie_board("g.wins", "wins"),
        "Shutouts": lambda: goalie_board("g.shutouts", "shutouts"),
    }
    tabs = st.tabs(list(boards))
    for tab, (name, fn) in zip(tabs, boards.items()):
        with tab:
            df = fn()
            if df is None:
                continue
            if df.empty:
                empty(f"Nobody has {min_gp}+ games yet. Lower the minimum games played.")
                continue
            df.insert(0, "rank", range(1, len(df) + 1))
            value_col = df.columns[-1]
            left, right = st.columns([3, 2])
            left.dataframe(df, hide_index=True)
            chart_df = df.assign(label=df.player + " (" + df.team + ")")
            ascending = name == "GAA"
            right.altair_chart(
                bar(chart_df[["label", value_col]], f"{value_col}:Q", "label:N",
                    color=RED if name in ("Penalty minutes", "GAA") else BLUE,
                    height=max(160, 26 * len(df)), sort="x" if ascending else "-x"), width="stretch")


READ_ONLY_START = re.compile(r"^\s*(SELECT|WITH|EXPLAIN|PRAGMA\s+table_info)\b", re.I)


def strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines()).strip()


def page_sql() -> None:
    title("SQL query", "Run one of the prepared analysis queries, or write your own. The database is read-only.")
    try:
        queries = load_named_queries()
    except OSError as err:
        st.error(f"Could not read {config.QUERIES_PATH}: {err}")
        queries = []
    options = ["Write my own query"] + [f"{q['id']}: {q['title']}" for q in queries]
    picked = st.selectbox("Prepared queries", options, index=1 if queries else 0)
    q = next((q for q in queries if picked.startswith(q["id"] + ":")), None)
    if q:
        st.caption(q["description"])
    sql = st.text_area("SQL", value=q["sql"] if q else "SELECT * FROM teams LIMIT 10", height=260,
                       key=f"sql_{picked}")

    with st.expander("Tables and columns"):
        schema = safe_query("""SELECT m.name AS "table", group_concat(p.name, ', ') AS columns
                               FROM sqlite_master m JOIN pragma_table_info(m.name) p
                               WHERE m.type = 'table' AND m.name NOT LIKE 'sqlite_%'
                               GROUP BY m.name ORDER BY m.name""")
        if schema is not None:
            st.dataframe(schema, hide_index=True)

    if st.button("Run query", type="primary"):
        body = strip_comments(sql).rstrip(";").strip()
        if not body:
            st.error("The query is empty. Type a SELECT statement.")
            return
        if ";" in body:
            st.error("Run one statement at a time: remove everything after the first semicolon.")
            return
        if not READ_ONLY_START.match(body):
            st.error("Only read queries are allowed here: start with SELECT or WITH.")
            return
        df = safe_query(body)
        if df is None:
            return
        if df.empty:
            st.info("The query ran and returned no rows.")
            return
        st.success(f"{len(df):,} row(s)")
        st.dataframe(df.head(5000), hide_index=True)
        if len(df) > 5000:
            st.caption("Showing the first 5,000 rows.")
        st.download_button("Download CSV", df.to_csv(index=False).encode(), "query_result.csv", "text/csv")


# ============================================================== navigation
def sidebar() -> str:
    requested = st.query_params.get("page", "Home")
    default_index = PAGES.index(requested) if requested in PAGES else 0
    with st.sidebar:
        if option_menu is not None:
            choice = option_menu(
                "NHL Analytics Hub", PAGES, icons=ICONS, menu_icon="snow", default_index=default_index,
                styles={
                    "container": {"padding": "0", "background-color": "transparent"},
                    "menu-title": {"font-family": "Barlow Condensed, sans-serif", "font-weight": "700",
                                   "color": BOARDS},
                    "nav-link": {"font-size": "0.95rem", "--hover-color": "#E4EBF0"},
                    "nav-link-selected": {"background-color": BLUE},
                },
            )
        else:
            choice = st.radio("Navigate", PAGES, index=default_index)
        info = run_info()
        st.divider()
        if info:
            source = {"api": "NHL public API", "demo": "synthetic demo season"}.get(info.get("source"), info.get("source"))
            st.caption(f"Data: {source}\n\nBuilt {info.get('built_at', '?')}")
        st.caption(f"Database: `{DB_PATH.name}`")
    return choice or PAGES[default_index]


def main() -> None:
    page = sidebar()
    if not DB_PATH.exists():
        title("NHL Analytics Hub")
        st.error(f"No database found at `{DB_PATH}`.")
        st.markdown("Build it from the project folder, then refresh this page:")
        st.code("python run_pipeline.py              # live NHL API\n"
                "python run_pipeline.py --source demo  # offline, synthetic data", language="bash")
        return
    if run_info().get("source") == "demo":
        st.warning("Demo mode: teams are real, but every player, score and stat is randomly generated. "
                   "Run `python run_pipeline.py` for live NHL data.", icon="🧪")
    {
        "Home": page_home, "Standings": page_standings, "Team Info": page_team,
        "Player Search": page_players, "Game Results": page_games,
        "Leaderboards": page_leaderboards, "SQL Query": page_sql,
    }[page]()


main()
