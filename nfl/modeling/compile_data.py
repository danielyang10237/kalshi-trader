from nfl import _get_team_code_from_name

import numpy as np
import pandas as pd

data_files = [
    "nfl/data/schedules/nfl_schedule_2002_2005.csv",
    "nfl/data/schedules/nfl_schedule_2006_2009.csv",
    "nfl/data/schedules/nfl_schedule_2010_2013.csv",
    "nfl/data/schedules/nfl_schedule_2014_2017.csv",
    "nfl/data/schedules/nfl_schedule_2018_2021.csv",
    "nfl/data/schedules/nfl_schedule_2022_2025.csv",
]

data = pd.concat([pd.read_csv(file) for file in data_files])

def _get_average_stats(team_stats: pd.DataFrame, date: str) -> pd.DataFrame:
    team_stats.drop(columns=["game_id", "is_home", "espn_event_id", "team_abbr"], inplace=True)

    team_stats = team_stats[team_stats["gameday"] < date]
    return team_stats.mean(numeric_only=True)

for index, row in data.iterrows():
    game_id = row["game_id"]
    game_date = row["gameday"]

    away_team = _get_team_code_from_name(row["away_team"])
    home_team = _get_team_code_from_name(row["home_team"])

    away_team_stats = pd.read_csv(f"nfl/data/teams/{away_team}.csv")
    home_team_stats = pd.read_csv(f"nfl/data/teams/{home_team}.csv")

    away_team_average_stats = _get_average_stats(away_team_stats, game_date)
    home_team_average_stats = _get_average_stats(home_team_stats, game_date)

    # add away_team_average_stats and home_team_average_stats to the data DataFrame
    data.loc[index, "away_team_average_stats"] = away_team_average_stats
    data.loc[index, "home_team_average_stats"] = home_team_average_stats

    away_team_roster = pd.read_csv(f"nfl/data/rosters/{game_id}/{away_team}.csv")
    home_team_roster = pd.read_csv(f"nfl/data/rosters/{game_id}/{home_team}.csv")

# save the data DataFrame to a CSV file
data.to_csv("nfl/modeling/data/compiled_data.csv", index=False)