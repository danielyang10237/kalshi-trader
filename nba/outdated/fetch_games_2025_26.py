"""
Fetch game stats for 2025-26 season using BoxScoreTraditionalV3 and append to games.csv
"""
import argparse
import os
import time
import pandas as pd
from nba_api.stats.endpoints import boxscoretraditionalv3
from nba_api.stats.library.http import NBAStatsHTTP


def parse_matchup(matchup: str) -> tuple[str, str]:
    """Parse matchup string to get away and home teams."""
    matchup = matchup.strip()

    if " vs. " in matchup:
        parts = matchup.split(" vs. ")
        home_team = parts[0].strip()
        away_team = parts[1].strip()
    elif " @ " in matchup:
        parts = matchup.split(" @ ")
        away_team = parts[0].strip()
        home_team = parts[1].strip()
    else:
        raise ValueError(f"Unknown matchup format: {matchup}")

    return away_team, home_team


def fetch_game_boxscore(game_id: str, debug: bool = False) -> tuple[pd.DataFrame | None, str | None]:
    """
    Fetch traditional box score for a single game using BoxScoreTraditionalV3.
    Returns (DataFrame, error_message) where DataFrame contains formatted game stats.
    """
    try:
        if debug:
            print(f"    [DEBUG] Fetching boxscore for game_id={game_id}")
        
        # Configure timeout
        NBAStatsHTTP.timeout = 30
        
        # Fetch box score
        bs = boxscoretraditionalv3.BoxScoreTraditionalV3(
            game_id=game_id,
            start_period=1,
            end_period=10,  # cover all periods including OT
            start_range=0,
            end_range=0,
            range_type=0,
        )
        
        # Get team stats
        team_stats = bs.team_stats.get_data_frame()
        
        if team_stats.empty or len(team_stats) != 2:
            return None, f"Invalid team stats data (got {len(team_stats)} teams)"
        
        if debug:
            print(f"    [DEBUG] Retrieved stats for {len(team_stats)} teams")
        
        return team_stats, None
        
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        if debug:
            import traceback
            print(f"    [DEBUG] Exception occurred:")
            traceback.print_exc()
        return None, error_msg


def format_game_row(game_id: str, game_date: str, season: str, 
                    away_team: str, home_team: str, 
                    team_stats: pd.DataFrame) -> dict:
    """
    Format team stats into a single row matching the games.csv format.
    """
    # Separate away and home team stats
    # The first team in the response might not always be away, so we need to check
    # In the traditional V3 endpoint, teams are ordered by teamId, not home/away
    # We'll use the fact that the schedule tells us which is home/away
    
    # Get both teams' data
    team1 = team_stats.iloc[0]
    team2 = team_stats.iloc[1]
    
    # Try to match by team abbreviation or name
    # The TEAM_ABBREVIATION field should match our away_team/home_team
    team1_abbr = team1.get('teamTricode', team1.get('TEAM_ABBREVIATION', ''))
    team2_abbr = team2.get('teamTricode', team2.get('TEAM_ABBREVIATION', ''))
    
    # Determine which is away and which is home
    if team1_abbr == away_team or (away_team in str(team1_abbr)):
        away_stats = team1
        home_stats = team2
    elif team2_abbr == away_team or (away_team in str(team2_abbr)):
        away_stats = team2
        home_stats = team1
    else:
        # If we can't match, assume first is away (but this might be wrong)
        away_stats = team1
        home_stats = team2
    
    # Build the row dictionary matching games.csv format
    row = {
        'game_id': game_id,
        'game_date': game_date,
        'season': season,
        'home_team': home_team,
        'away_team': away_team,
        'home_team_full': home_stats.get('teamName', ''),
        'away_team_full': away_stats.get('teamName', ''),
    }
    
    # Add away team stats with away_ prefix
    away_prefix_cols = {
        'TEAM_CITY': 'teamCity',
        'MIN': 'minutes',
        'FGM': 'fieldGoalsMade',
        'FGA': 'fieldGoalsAttempted',
        'FG_PCT': 'fieldGoalsPercentage',
        'FG3M': 'threePointersMade',
        'FG3A': 'threePointersAttempted',
        'FG3_PCT': 'threePointersPercentage',
        'FTM': 'freeThrowsMade',
        'FTA': 'freeThrowsAttempted',
        'FT_PCT': 'freeThrowsPercentage',
        'OREB': 'reboundsOffensive',
        'DREB': 'reboundsDefensive',
        'REB': 'reboundsTotal',
        'AST': 'assists',
        'STL': 'steals',
        'BLK': 'blocks',
        'TO': 'turnovers',
        'PF': 'foulsPersonal',
        'PTS': 'points',
        'PLUS_MINUS': 'plusMinusPoints',
    }
    
    for col, api_col in away_prefix_cols.items():
        # Try new column name first, fallback to old column name
        value = away_stats.get(api_col, away_stats.get(col, None))
        row[f'away_{col}'] = value
    
    # Add home team stats with home_ prefix
    for col, api_col in away_prefix_cols.items():
        value = home_stats.get(api_col, home_stats.get(col, None))
        row[f'home_{col}'] = value
    
    return row


def main():
    parser = argparse.ArgumentParser(
        description="Fetch 2025-26 season game stats using BoxScoreTraditionalV3"
    )
    parser.add_argument(
        "--schedule", 
        default="nba/data/schedules/schedule_2024-25.csv",
        help="Path to 2024-25 schedule CSV"
    )
    parser.add_argument(
        "--output", 
        default="nba/data/games.csv",
        help="Path to games.csv to append to"
    )
    parser.add_argument(
        "--delay", 
        type=float, 
        default=0.6,
        help="Delay between API calls (seconds), recommended 0.6s"
    )
    parser.add_argument(
        "--past-only", 
        action="store_true",
        help="Only fetch games that have already been played (before today)"
    )
    parser.add_argument(
        "--start-date", 
        help="Only fetch games on or after this date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date", 
        help="Only fetch games on or before this date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--debug", 
        action="store_true",
        help="Print debug information"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't append to games.csv, just print what would be done"
    )
    args = parser.parse_args()

    # Load schedule
    print(f"Loading schedule from {args.schedule}...")
    schedule = pd.read_csv(args.schedule, dtype={"GAME_ID": str}, usecols=["GAME_ID", "GAME_DATE", "MATCHUP"])
    schedule["GAME_DATE"] = pd.to_datetime(schedule["GAME_DATE"])
    
    # Apply filters
    if args.start_date:
        start = pd.to_datetime(args.start_date)
        schedule = schedule[schedule["GAME_DATE"] >= start]
        print(f"Filtering to games on or after {start.strftime('%Y-%m-%d')}")
    
    if args.end_date:
        end = pd.to_datetime(args.end_date)
        schedule = schedule[schedule["GAME_DATE"] <= end]
        print(f"Filtering to games on or before {end.strftime('%Y-%m-%d')}")
    
    if args.past_only:
        today = pd.Timestamp.now().normalize()
        schedule = schedule[schedule["GAME_DATE"] < today]
        print(f"Filtering to games before {today.strftime('%Y-%m-%d')}")
    
    # Load existing games.csv to check which games we already have
    if os.path.exists(args.output):
        existing_games = pd.read_csv(args.output, dtype={"game_id": str})
        existing_game_ids = set(existing_games['game_id'].values)
        print(f"Loaded {len(existing_games)} existing games from {args.output}")
    else:
        existing_game_ids = set()
        print(f"No existing games.csv found at {args.output}")
    
    print(f"\nProcessing {len(schedule)} games from schedule...")
    
    success_count = 0
    skip_count = 0
    error_count = 0
    
    for idx, row in schedule.iterrows():
        game_id = str(row["GAME_ID"])
        
        # Skip if already in games.csv
        if game_id in existing_game_ids:
            print(f"  [SKIP] Game {game_id} already in games.csv")
            skip_count += 1
            continue
        
        game_date = row["GAME_DATE"]
        matchup = row["MATCHUP"]
        
        try:
            away_team, home_team = parse_matchup(matchup)
        except ValueError as e:
            print(f"  [ERROR] {e}")
            error_count += 1
            continue
        
        print(
            f"  [{idx+1}/{len(schedule)}] Fetching {game_date.strftime('%Y-%m-%d')} "
            f"{away_team} @ {home_team} (game_id={game_id})...",
            end=" " if not args.debug else "\n",
        )
        
        # Fetch box score
        team_stats, error = fetch_game_boxscore(game_id, debug=args.debug)
        
        if error:
            print(f"SKIP: {error}")
            skip_count += 1
            time.sleep(args.delay)
            continue
        
        # Format the row
        try:
            game_row = format_game_row(
                game_id=game_id,
                game_date=game_date.strftime('%Y-%m-%d'),
                season='2024-25',
                away_team=away_team,
                home_team=home_team,
                team_stats=team_stats
            )
            
            print(f"OK (score: {game_row['away_PTS']}-{game_row['home_PTS']})", end="")
            
            # Save immediately to CSV (unless dry-run)
            if not args.dry_run:
                game_df = pd.DataFrame([game_row])
                
                if os.path.exists(args.output):
                    # Append to existing file
                    game_df.to_csv(args.output, mode='a', header=False, index=False)
                else:
                    # Create new file with header
                    game_df.to_csv(args.output, index=False)
                
                # Add to existing_game_ids so we don't process again
                existing_game_ids.add(game_id)
                print(" ✓ saved")
            else:
                print(" (dry-run)")
            
            success_count += 1
            
        except Exception as e:
            print(f"ERROR formatting: {e}")
            if args.debug:
                import traceback
                traceback.print_exc()
            error_count += 1
        
        # Rate limiting
        time.sleep(args.delay)
    
    print(f"\n{'='*60}")
    print(f"Done! Success: {success_count}, Skipped: {skip_count}, Errors: {error_count}")
    if not args.dry_run and success_count > 0:
        print(f"All successful games saved to {args.output}")
    elif args.dry_run:
        print(f"Dry run complete - no changes made to {args.output}")


if __name__ == "__main__":
    main()
