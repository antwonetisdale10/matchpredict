"""Build data/players/players_2627.csv.

Identity (name, club, position, start price) comes from the 26/27 FPL roster file.
Scoring/assist/minutes history comes ONLY from the completed 2025/26 file, joined on
the stable player `code`. No 26/27 performance column is ever read.
usage: python3 build_players.py fpl_2025-26_dir fpl_2026-27_dir out.csv
"""

import sys
import pandas as pd

old_dir, new_dir, out = sys.argv[1:4]
TEAM = {
    "Man City": "Man City",
    "Man Utd": "Man United",
    "Spurs": "Tottenham",
    "Leeds": "Leeds",
    "Coventry City": "Coventry",
    "Hull City": "Hull",
    "Ipswich Town": "Ipswich",
}
IDENT = [
    "code",
    "first_name",
    "second_name",
    "web_name",
    "team",
    "element_type",
    "now_cost",
    "cost_change_start",
    "status",
]
HIST = [
    "code",
    "goals_scored",
    "assists",
    "minutes",
    "penalties_order",
    "direct_freekicks_order",
    "yellow_cards",
    "red_cards",
]

new = pd.read_csv(f"{new_dir}/players_raw.csv", usecols=IDENT)
teams = pd.read_csv(f"{new_dir}/teams.csv", usecols=["id", "name"]).set_index("id")[
    "name"
]
new["club"] = new["team"].map(teams).replace(TEAM)
new["start_price"] = (new["now_cost"] - new["cost_change_start"]) / 10
old = pd.read_csv(f"{old_dir}/players_raw.csv", usecols=HIST).rename(
    columns={
        "goals_scored": "g_prev",
        "assists": "a_prev",
        "minutes": "min_prev",
        "penalties_order": "pen_order",
        "direct_freekicks_order": "fk_order",
        "yellow_cards": "y_prev",
        "red_cards": "r_prev",
    }
)
df = new.merge(old, on="code", how="left")
df["name"] = df.apply(lambda r: r.web_name, axis=1)
df["pos"] = df["element_type"].map({1: "GK", 2: "DEF", 3: "MID", 4: "FWD"})
df = df[df["status"] != "u"]  # drop players who've left
df = df[
    [
        "code",
        "name",
        "first_name",
        "second_name",
        "club",
        "pos",
        "start_price",
        "g_prev",
        "a_prev",
        "min_prev",
        "pen_order",
        "fk_order",
        "y_prev",
        "r_prev",
    ]
]
df.to_csv(out, index=False)
print(len(df), "players;", df["g_prev"].notna().sum(), "with 25/26 history")
print(df.groupby("club").size().to_string())
