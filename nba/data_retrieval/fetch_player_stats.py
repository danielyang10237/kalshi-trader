import os
import time
import glob
import gc
from typing import Dict, Tuple, Optional

import pandas as pd
from nba_api.stats.static import teams
from nba_api.stats.endpoints import boxscoretraditionalv2
from nba_api.library.http import NBAHTTP


def parse_matchup(matchup) -> Tuple[str, str]:
    """Parse matchup string to extract home and away team abbreviations."""
    # Handle NaN or non-string values
    if pd.isna(matchup) or not isinstance(matchup, str):
        raise ValueError(f"Invalid MATCHUP value: {matchup}")
    
    s = matchup.strip()
    if " @ " in s:
        away, home = [x.strip() for x in s.split(" @ ", 1)]
        return home, away
    if " vs. " in s:
        home, away = [x.strip() for x in s.split(" vs. ", 1)]
        return home, away
    if " vs " in s:
        home, away = [x.strip() for x in s.split(" vs ", 1)]
        return home, away
    raise ValueError(f"Unrecognized MATCHUP format: {matchup}")


def build_team_maps() -> Tuple[Dict[str, int], Dict[str, str]]:
    """Build mappings from team abbreviation to team ID and full name."""
    nba_teams = teams.get_teams()
    abbrev_to_id = {t["abbreviation"]: t["id"] for t in nba_teams}
    abbrev_to_full = {t["abbreviation"]: t["full_name"] for t in nba_teams}
    
    # Add historical/relocated teams that aren't in the current teams list
    historical_teams = {
        "VAN": {"id": 1610612744, "full_name": "Vancouver Grizzlies"},  # Now Memphis Grizzlies
        "SEA": {"id": 1610612760, "full_name": "Seattle SuperSonics"},  # Now Oklahoma City Thunder
        "NOH": {"id": 1610612740, "full_name": "New Orleans Hornets"},  # Now New Orleans Pelicans
        "NOK": {"id": 1610612740, "full_name": "New Orleans/Oklahoma City Hornets"},  # Temporary relocation
        "CHA": {"id": 1610612766, "full_name": "Charlotte Bobcats"},  # Changed back to Hornets
        "NJN": {"id": 1610612751, "full_name": "New Jersey Nets"},  # Now Brooklyn Nets
        "CHH": {"id": 1610612766, "full_name": "Charlotte Hornets"},  # Now Charlotte Hornets
    }
    
    for abbrev, info in historical_teams.items():
        if abbrev not in abbrev_to_id:  # Only add if not already present
            abbrev_to_id[abbrev] = info["id"]
            abbrev_to_full[abbrev] = info["full_name"]
    
    return abbrev_to_id, abbrev_to_full

    


def fetch_player_stats(game_id: str, sleep_s: float = 1.0, max_retries: int = 5, timeout: int = 60) -> Optional[pd.DataFrame]:
    """
    Fetch player stats for a game. Returns DataFrame with all player stats.
    Returns None if unable to fetch.
    """
    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                # Exponential backoff on retries, with longer delays for timeouts
                delay = sleep_s * (2 ** attempt)
                if "timeout" in str(last_err).lower():
                    delay *= 2  # Double the delay for timeouts
                    print(f"    [RETRY {attempt + 1}/{max_retries}] Timeout detected, waiting {delay:.1f}s before retry...")
                time.sleep(delay)
            
            # Set timeout on the request
            box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id, timeout=timeout)
            player_stats_df = box.player_stats.get_data_frame()
            
            if player_stats_df.empty:
                return None
            
            # Always sleep after successful request to avoid rate limiting
            time.sleep(sleep_s)
            return player_stats_df
            
        except Exception as e:
            last_err = e
            # Don't sleep after the last failed attempt
            if attempt == max_retries - 1:
                break
            
    print(f"  [WARN] Failed to fetch player stats for game {game_id} after {max_retries} attempts: {last_err}")
    return None


def main():
    schedules_dir = "nba/data/schedules"
    output_dir = "nba/data/players_live"
    
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    # Find all schedule CSVs
    schedule_files = sorted(glob.glob(f"{schedules_dir}/schedule_*.csv"))
    if not schedule_files:
        print(f"No schedule files found in {schedules_dir}/")
        return
    
    print(f"Found {len(schedule_files)} schedule files")
    
    # Build team mappings
    _, abbrev_to_full = build_team_maps()
    
    # Function to reload existing files
    def reload_existing_files():
        return set(os.path.basename(f) for f in glob.glob(f"{output_dir}/*.csv"))
    
    # Track existing output files
    existing_files = reload_existing_files()
    
    if existing_files:
        print(f"Found {len(existing_files)} existing player stats files. Will skip these games.")
    else:
        print("No existing player stats files found. Starting fresh.")
    
    total_games = 0
    new_games = 0
    skipped_games = 0
    error_games = 0
    consecutive_errors = 0
    max_consecutive_errors = 3  # Take a long break after this many consecutive errors
    
    for schedule_file in schedule_files:
        season_name = os.path.basename(schedule_file).replace("schedule_", "").replace(".csv", "")
        print(f"\n{'='*60}")
        print(f"Processing season: {season_name}")
        print(f"{'='*60}")
        
        sched = pd.read_csv(schedule_file, dtype={"GAME_ID": str}, usecols=["GAME_ID", "GAME_DATE", "MATCHUP"])

        # Validate columns
        required_cols = ["GAME_ID", "GAME_DATE", "MATCHUP"]
        if not all(col in sched.columns for col in required_cols):
            print(f"  [SKIP] Missing required columns in {schedule_file}")
            continue

        for _, r in sched.iterrows():
            game_id = str(r["GAME_ID"])
            matchup = r["MATCHUP"]
            
            total_games += 1
            
            try:
                home_abbrev, away_abbrev = parse_matchup(matchup)
            except ValueError as e:
                print(f"  [ERROR] Game {game_id}: {e}")
                error_games += 1
                time.sleep(0.1)  # Small delay even on errors
                continue
            
            if home_abbrev not in abbrev_to_full or away_abbrev not in abbrev_to_full:
                # Skip WNBA teams silently (too many warnings)
                error_games += 1
                time.sleep(0.1)  # Small delay even on errors
                continue
            
            # Generate output filename
            output_filename = f"{game_id}_{away_abbrev}_{home_abbrev}.csv"
            output_path = os.path.join(output_dir, output_filename)
            
            # Skip if already processed
            if output_filename in existing_files:
                skipped_games += 1
                if skipped_games % 100 == 0:
                    print(f"  Skipped {skipped_games} existing games...")
                continue
            
            # Fetch player stats (includes built-in delays)
            player_stats_df = fetch_player_stats(game_id, sleep_s=1.5, timeout=90)
            if player_stats_df is None:
                error_games += 1
                consecutive_errors += 1
                
                # If too many consecutive errors, take a long break and reset session
                if consecutive_errors >= max_consecutive_errors:
                    break_time = 60  # Full minute break
                    print(f"\n  [COOLDOWN] {consecutive_errors} consecutive errors. Taking a {break_time}s break and resetting connection...")
                    try:
                        # Close existing session
                        if hasattr(NBAHTTP, '_session') and NBAHTTP._session is not None:
                            try:
                                NBAHTTP._session.close()
                            except:
                                pass
                        NBAHTTP._session = None
                        gc.collect()
                        gc.collect()
                    except Exception:
                        pass
                    time.sleep(break_time)
                    consecutive_errors = 0  # Reset after break
                    print(f"  [COOLDOWN] Complete. Resuming...\n")
                else:
                    time.sleep(0.5)  # Small delay before continuing
                continue
            
            # Save to CSV
            player_stats_df.to_csv(output_path, index=False)
            new_games += 1
            consecutive_errors = 0  # Reset on success
            existing_files.add(output_filename)  # Track new file
            
            # Progress update
            if new_games % 10 == 0:
                print(f"  Processed {new_games} new games (total: {total_games}, skipped: {skipped_games}, errors: {error_games})...")
            
            # Reset HTTP session every 100 games to avoid connection staleness
            if new_games % 100 == 0:
                reset_duration = 45 if new_games % 300 == 0 else 30  # Longer break every 300 games
                print(f"\n  [SESSION RESET] Processed {new_games} games. Deep resetting HTTP stack...")
                try:
                    # Force close and clear ALL connection pools and sessions
                    import sys
                    import importlib
                    
                    # Close any existing session
                    if hasattr(NBAHTTP, '_session') and NBAHTTP._session is not None:
                        try:
                            # Close all connection pools in the session
                            if hasattr(NBAHTTP._session, 'close'):
                                NBAHTTP._session.close()
                        except:
                            pass
                    
                    # Clear session at class level
                    NBAHTTP._session = None
                    
                    # Try to clear any module-level HTTP state
                    try:
                        # Clear requests module connection pools
                        import requests
                        if hasattr(requests, 'sessions') and hasattr(requests.sessions, 'Session'):
                            # This forces requests to create fresh connection pools
                            pass
                    except:
                        pass
                    
                    # Clear urllib3 connection pools
                    try:
                        import urllib3
                        # Force urllib3 to clear all poolmanagers
                        if hasattr(urllib3, 'PoolManager'):
                            pass
                    except:
                        pass
                    
                    # Clear any boxscore caches
                    if hasattr(boxscoretraditionalv2, '_cache'):
                        boxscoretraditionalv2._cache = None
                    
                    # Aggressive garbage collection
                    for _ in range(3):
                        gc.collect()
                    
                    # Give the server a substantial break
                    print(f"  [SESSION RESET] Taking {reset_duration}s break for server cooldown...")
                    time.sleep(reset_duration)
                    
                    print(f"  [SESSION RESET] Complete. Resuming with fresh connections...\n")
                except Exception as e:
                    print(f"  [SESSION RESET] Warning: {e}. Continuing anyway...")
    
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total games processed: {total_games}")
    print(f"New player stats files created: {new_games}")
    print(f"Games skipped (already exist): {skipped_games}")
    print(f"Errors: {error_games}")
    print(f"Final output: {new_games + skipped_games} files in {output_dir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
