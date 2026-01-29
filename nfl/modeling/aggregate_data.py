import pandas as pd
import sys
import os

from nfl import _get_team_code_from_name

data_files = [
    "nfl/data/schedules/nfl_schedule_2002_2005.csv",
    "nfl/data/schedules/nfl_schedule_2006_2009.csv",
    "nfl/data/schedules/nfl_schedule_2010_2013.csv",
    "nfl/data/schedules/nfl_schedule_2014_2017.csv",
    "nfl/data/schedules/nfl_schedule_2018_2021.csv",
    "nfl/data/schedules/nfl_schedule_2022_2025.csv",
]
data = pd.concat([pd.read_csv(file) for file in data_files], ignore_index=True)

# drop rows where result is NaN
data = data[data["result"].notna()]

# Cache team stats to avoid re-reading CSVs
team_stats_cache = {}

def get_team_stats(team_code: str) -> pd.DataFrame:
    if team_code not in team_stats_cache:
        df = pd.read_csv(f"nfl/data/teams/{team_code}.csv")
        df = df.drop(columns=["game_id", "is_home", "espn_event_id", "team_abbr"], errors='ignore')
        team_stats_cache[team_code] = df
    return team_stats_cache[team_code].copy()

def get_average_stats(team_code: str, date: str) -> dict:
    team_stats = get_team_stats(team_code)
    team_stats = team_stats[team_stats["gameday"] < date]
    if len(team_stats) == 0:
        return {}
    return team_stats.mean(numeric_only=True).to_dict()


def correct_team_record(home_team_score: int, away_team_score: int, home_team_record: str, away_team_record: str) -> tuple[str, str]:
    # Convert to string if it's not already
    home_team_record = str(home_team_record)
    away_team_record = str(away_team_record)
    
    # Handle records that may include ties (W-L-T format)
    home_parts = home_team_record.split("-")
    away_parts = away_team_record.split("-")
    
    home_team_wins = int(home_parts[0])
    home_team_losses = int(home_parts[1])
    home_team_ties = int(home_parts[2]) if len(home_parts) > 2 else 0
    
    away_team_wins = int(away_parts[0])
    away_team_losses = int(away_parts[1])
    away_team_ties = int(away_parts[2]) if len(away_parts) > 2 else 0
    
    if home_team_score > away_team_score:
        home_team_wins -=1
        away_team_losses -= 1
    elif away_team_score > home_team_score:
        home_team_losses -= 1
        away_team_wins -= 1
    else:  # Tie game
        home_team_ties -= 1
        away_team_ties -= 1
    
    # Format output: include ties if they exist, otherwise just W-L
    if home_team_ties > 0 or away_team_ties > 0:
        return f"{home_team_wins}-{home_team_losses}-{home_team_ties}", f"{away_team_wins}-{away_team_losses}-{away_team_ties}"
    else:
        return f"{home_team_wins}-{home_team_losses}", f"{away_team_wins}-{away_team_losses}"

# Collect all new columns in a list of dicts
new_rows = []

for index, row in data.iterrows():
    game_id = row["game_id"]
    game_date = row["gameday"]
    
    try:
        away_team = _get_team_code_from_name(row["away_team"])
        home_team = _get_team_code_from_name(row["home_team"])
        
        away_avg = get_average_stats(away_team, game_date)
        home_avg = get_average_stats(home_team, game_date)
        
        row_data = {}
        for stat_name, stat_value in away_avg.items():
            row_data[f"away_team_avg_{stat_name}"] = stat_value
        for stat_name, stat_value in home_avg.items():
            row_data[f"home_team_avg_{stat_name}"] = stat_value

        home_team_record, away_team_record = correct_team_record(row["home_score"], row["away_score"], row["home_record"], row["away_record"])
        row_data["home_record"] = home_team_record
        row_data["away_record"] = away_team_record
        
        new_rows.append(row_data)
    except Exception as e:
        print(f"Error processing {game_id}: {e}")
        new_rows.append({})

# Create DataFrame from collected data and join once
new_cols_df = pd.DataFrame(new_rows)
data = pd.concat([data.reset_index(drop=True), new_cols_df], axis=1)


columns_to_drop = ['game_type', 'week', 'weekday', 'gametime', 'location', 'old_game_id', 'gsis', 'nfl_detail_id', 'pfr', 'pff', 'espn', 'ftn', 'away_moneyline', 'home_moneyline', 'spread_line', 'away_spread_odds', 'home_spread_odds', 'total_line', 'under_odds', 'over_odds', 'div_game', 'roof', 'surface', 'temp', 'wind', 'away_qb_id', 'home_qb_id', 'away_qb_name', 'home_qb_name', 'away_coach', 'home_coach', 'referee', 'stadium_id', 'stadium']

# Only drop columns that actually exist in the DataFrame
columns_to_drop = [col for col in columns_to_drop if col in data.columns]
data = data.drop(columns=columns_to_drop)

data.to_csv("nfl/modeling/data/compiled_data.csv", index=False)