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
    return abbrev_to_id, abbrev_to_full


def fetch_box_score(game_id: str, sleep_s: float = 1.0, max_retries: int = 5, timeout: int = 60) -> Optional[Dict]:
    """
    Fetch box score for a game. Returns dict with 'home' and 'away' team stats.
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
            team_stats_df = box.team_stats.get_data_frame()
            
            if team_stats_df.empty or len(team_stats_df) < 2:
                return None
            
            # First row is away team, second is home team (usually)
            away_stats = team_stats_df.iloc[0].to_dict()
            home_stats = team_stats_df.iloc[1].to_dict()
            
            # Always sleep after successful request to avoid rate limiting
            time.sleep(sleep_s)
            return {"away": away_stats, "home": home_stats}
            
        except Exception as e:
            last_err = e
            # Don't sleep after the last failed attempt
            if attempt == max_retries - 1:
                break
            
    print(f"  [WARN] Failed to fetch box score for game {game_id} after {max_retries} attempts: {last_err}")
    return None


def main():
    schedules_dir = "nba/data/schedules"
    output_csv = "nba/data/games.csv"
    
    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    
    # Find all schedule CSVs
    schedule_files = sorted(glob.glob(f"{schedules_dir}/schedule_*.csv"))
    if not schedule_files:
        print(f"No schedule files found in {schedules_dir}/")
        return
    
    print(f"Found {len(schedule_files)} schedule files")
    
    # Build team mappings
    _, abbrev_to_full = build_team_maps()
    
    # Function to reload existing game IDs from CSV
    def reload_existing_games():
        if os.path.exists(output_csv):
            try:
                df = pd.read_csv(output_csv)
                if not df.empty and "game_id" in df.columns:
                    return set(df["game_id"].astype(str)), df.to_dict('records')
            except (pd.errors.EmptyDataError, pd.errors.ParserError):
                pass
        return set(), []
    
    # Load existing data
    existing_game_ids, rows = reload_existing_games()
    
    if existing_game_ids:
        print(f"Output file exists with {len(existing_game_ids)} games. Will append new games only.")
    else:
        print("No existing output file or empty. Starting fresh.")
    
    total_games = 0
    new_games = 0
    skipped_games = 0
    error_games = 0
    consecutive_errors = 0
    max_consecutive_errors = 5  # Take a long break after this many consecutive errors
    
    for schedule_file in schedule_files:
        season_name = os.path.basename(schedule_file).replace("schedule_", "").replace(".csv", "")
        print(f"\n{'='*60}")
        print(f"Processing season: {season_name}")
        print(f"{'='*60}")
        
        sched = pd.read_csv(schedule_file)
        
        # Validate columns
        required_cols = ["GAME_ID", "GAME_DATE", "MATCHUP"]
        if not all(col in sched.columns for col in required_cols):
            print(f"  [SKIP] Missing required columns in {schedule_file}")
            continue
        
        sched["GAME_DATE"] = pd.to_datetime(sched["GAME_DATE"]).dt.normalize()
        
        for _, r in sched.iterrows():
            game_id = str(r["GAME_ID"]).zfill(10)  # Ensure 10-digit format
            game_date = r["GAME_DATE"]
            matchup = r["MATCHUP"]
            
            total_games += 1
            
            # Reload existing games periodically to catch any new additions
            if (total_games % 100) == 0 and total_games > 0:
                temp_ids, _ = reload_existing_games()
                if temp_ids:
                    existing_game_ids = temp_ids
            
            # Skip if already processed
            if game_id in existing_game_ids:
                skipped_games += 1
                if skipped_games % 100 == 0:
                    print(f"  Skipped {skipped_games} existing games...")
                continue
            
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
            
            home_full = abbrev_to_full[home_abbrev]
            away_full = abbrev_to_full[away_abbrev]
            
            # Fetch box score (includes built-in delays)
            box_score = fetch_box_score(game_id, sleep_s=1.5, timeout=90)
            if not box_score:
                error_games += 1
                consecutive_errors += 1
                
                # If too many consecutive errors, take a long break and reset session
                if consecutive_errors >= max_consecutive_errors:
                    break_time = 45
                    print(f"\n  [COOLDOWN] {consecutive_errors} consecutive errors. Taking a {break_time}s break and resetting connection...")
                    try:
                        NBAHTTP._session = None
                        gc.collect()
                    except Exception:
                        pass
                    time.sleep(break_time)
                    consecutive_errors = 0  # Reset after break
                    print(f"  [COOLDOWN] Complete. Resuming...\n")
                else:
                    time.sleep(0.5)  # Small delay before continuing
                continue
            
            # Build row with game info and stats
            row = {
                "game_id": game_id,
                "game_date": game_date.strftime("%Y-%m-%d"),
                "season": season_name,
                "home_team": home_abbrev,
                "away_team": away_abbrev,
                "home_team_full": home_full,
                "away_team_full": away_full,
            }
            
            # Add home and away team stats
            for stat_key, stat_val in box_score["away"].items():
                if stat_key not in ["TEAM_ID", "TEAM_NAME", "TEAM_ABBREVIATION", "GAME_ID"]:
                    row[f"away_{stat_key}"] = stat_val
            
            for stat_key, stat_val in box_score["home"].items():
                if stat_key not in ["TEAM_ID", "TEAM_NAME", "TEAM_ABBREVIATION", "GAME_ID"]:
                    row[f"home_{stat_key}"] = stat_val
            
            rows.append(row)
            existing_game_ids.add(game_id)
            new_games += 1
            consecutive_errors = 0  # Reset on success
            
            # Progress update
            if new_games % 10 == 0:
                print(f"  Processed {new_games} new games (total: {total_games}, skipped: {skipped_games}, errors: {error_games})...")
            
            # Save periodically (every 25 games to reduce data loss risk)
            if new_games % 25 == 0:
                out_df = pd.DataFrame(rows)
                out_df.to_csv(output_csv, index=False)
                print(f"  [SAVE] Saved {len(out_df)} total games to {output_csv}")
            
            # Reset HTTP session every 300 games to avoid connection staleness
            if new_games % 300 == 0:
                print(f"\n  [SESSION RESET] Processed {new_games} games. Resetting HTTP connection pool...")
                try:
                    # Force nba_api to create a new session next time
                    NBAHTTP._session = None
                    # Give the server a break and let old connections close
                    time.sleep(10)
                    # Force garbage collection to clean up old connections
                    gc.collect()
                    print(f"  [SESSION RESET] Complete. Resuming...\n")
                except Exception as e:
                    print(f"  [SESSION RESET] Warning: {e}. Continuing anyway...")
    
    # Final save
    if rows:
        out_df = pd.DataFrame(rows)
        out_df.to_csv(output_csv, index=False)
        print(f"\n[FINAL SAVE] Saved {len(out_df)} total games to {output_csv}")
    
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total games processed: {total_games}")
    print(f"New games added: {new_games}")
    print(f"Games skipped (already exist): {skipped_games}")
    print(f"Errors: {error_games}")
    print(f"Final output: {len(rows)} games in {output_csv}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
