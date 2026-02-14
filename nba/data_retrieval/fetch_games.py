"""
Fetch NBA play-by-play data for all games in nba/data/schedules/ and save to nba/data/games_live/
"""
import argparse
import glob
import os
import time
import pandas as pd
from nba_api.stats.endpoints import playbyplayv2
from nba_api.stats.library.http import NBAStatsHTTP


# Configure global timeout
NBAStatsHTTP.timeout = 30


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


def fetch_pbp(game_id: str, debug: bool = False) -> tuple[pd.DataFrame | None, str | None]:
    """
    Fetch play-by-play for a single NBA game using playbyplayv2 endpoint.
    
    Returns (DataFrame, error_message) where DataFrame contains play-by-play events.
    """
    try:
        if debug:
            print(f"      [DEBUG] Fetching play-by-play for game_id={game_id}")
        
        pbp = playbyplayv2.PlayByPlayV2(
            game_id=game_id,
            start_period=1,
            end_period=10,   # covers regulation + OT
        )
        
        # Get play-by-play data
        df = pbp.play_by_play.get_data_frame()
        
        if df.empty:
            return None, "No play-by-play data available"
        
        if debug:
            print(f"      [DEBUG] Retrieved {len(df)} plays")
        
        return df, None
    
    except KeyError as e:
        # This happens when the API response doesn't have 'resultSet' or 'resultSets'
        # Common for early 2000s games where data isn't in modern format
        error_msg = "No historical data (missing resultSet in API response)"
        if debug:
            print(f"      [DEBUG] KeyError - likely no data for this game: {e}")
        return None, error_msg
    
    except AttributeError as e:
        # Sometimes the endpoint exists but has no .play_by_play attribute
        error_msg = "API schema mismatch (no play_by_play attribute)"
        if debug:
            print(f"      [DEBUG] AttributeError: {e}")
        return None, error_msg
    
    except Exception as e:
        # Catch-all for other errors (network, timeout, etc.)
        error_msg = f"{type(e).__name__}: {str(e)}"
        if debug:
            import traceback
            print(f"      [DEBUG] Exception occurred:")
            traceback.print_exc()
        return None, error_msg


def main():
    parser = argparse.ArgumentParser(
        description="Fetch NBA play-by-play data for all schedules"
    )
    parser.add_argument(
        "--schedules-dir",
        default="nba/data/schedules",
        help="Directory containing schedule CSV files (ignored if --schedule is specified)"
    )
    parser.add_argument(
        "--schedule",
        help="Process a single schedule CSV file (overrides --schedules-dir)"
    )
    parser.add_argument(
        "--output-dir",
        default="nba/data/games_live",
        help="Output directory for play-by-play files"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.6,
        help="Delay between API calls (seconds), recommended 0.6s"
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
        "--past-only",
        action="store_true",
        help="Only fetch games that have already been played (before today)"
    )
    parser.add_argument(
        "--season",
        help="Only process specific season (e.g., '2024-25') - only applies with --schedules-dir"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print debug information"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't save files, just show what would be processed"
    )
    args = parser.parse_args()

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)

    # Determine which schedule files to process
    if args.schedule:
        # Use single specified schedule file
        if not os.path.exists(args.schedule):
            print(f"Error: Schedule file not found: {args.schedule}")
            return
        schedule_files = [args.schedule]
        print(f"Processing single schedule file: {os.path.basename(args.schedule)}")
    else:
        # Find all schedule files in directory
        schedule_pattern = os.path.join(args.schedules_dir, "schedule_*.csv")
        schedule_files = sorted(glob.glob(schedule_pattern))
        
        if not schedule_files:
            print(f"No schedule files found in {args.schedules_dir}")
            return
        
        # Filter by season if specified
        if args.season:
            schedule_files = [f for f in schedule_files if args.season in f]
            if not schedule_files:
                print(f"No schedule files found for season {args.season}")
                return
        
        print(f"Found {len(schedule_files)} schedule file(s)")
        for sf in schedule_files:
            print(f"  - {os.path.basename(sf)}")
    
    # Get list of already processed games
    existing_files = set()
    if os.path.exists(args.output_dir):
        existing_files = set(os.listdir(args.output_dir))
    
    print(f"\nFound {len(existing_files)} existing play-by-play files in {args.output_dir}")
    
    # Process each schedule
    total_success = 0
    total_already_processed = 0
    total_no_data = 0
    total_error = 0
    
    for schedule_file in schedule_files:
        season_name = os.path.basename(schedule_file).replace('schedule_', '').replace('.csv', '')
        print(f"\n{'='*70}")
        print(f"Processing {season_name} season from {os.path.basename(schedule_file)}")
        print(f"{'='*70}")
        
        # Load schedule
        schedule = pd.read_csv(schedule_file, dtype={"GAME_ID": str}, usecols=["GAME_ID", "GAME_DATE", "MATCHUP"])
        schedule["GAME_DATE"] = pd.to_datetime(schedule["GAME_DATE"])
        
        # Apply filters
        if args.start_date:
            start = pd.to_datetime(args.start_date)
            schedule = schedule[schedule["GAME_DATE"] >= start]
        
        if args.end_date:
            end = pd.to_datetime(args.end_date)
            schedule = schedule[schedule["GAME_DATE"] <= end]
        
        if args.past_only:
            today = pd.Timestamp.now().normalize()
            schedule = schedule[schedule["GAME_DATE"] < today]
        
        if schedule.empty:
            print(f"  No games to process after applying filters")
            continue
        
        print(f"  Processing {len(schedule)} games...")
        
        success_count = 0
        already_processed_count = 0
        no_data_count = 0  # Track games with no historical data
        error_count = 0
        consecutive_errors = 0
        
        for idx, row in schedule.iterrows():
            game_id = str(row["GAME_ID"])
            
            game_date = row["GAME_DATE"]
            matchup = row["MATCHUP"]
            
            try:
                away_team, home_team = parse_matchup(matchup)
            except ValueError as e:
                print(f"    [ERROR] {e}")
                error_count += 1
                continue
            
            # Build output filename
            date_str = game_date.strftime("%Y%m%d")
            output_file = f"{date_str}_{away_team}_{home_team}.csv"
            output_path = os.path.join(args.output_dir, output_file)
            
            # Skip if already exists
            if output_file in existing_files:
                if args.debug:
                    print(f"    [SKIP] {output_file} already exists")
                already_processed_count += 1
                continue
            
            print(
                f"    [{idx+1}/{len(schedule)}] {game_date.strftime('%Y-%m-%d')} "
                f"{away_team} @ {home_team} (id={game_id})...",
                end=" " if not args.debug else "\n",
            )
            
            # Fetch play-by-play
            df, error = fetch_pbp(game_id, debug=args.debug)
            
            if error:
                # Differentiate between missing historical data vs real errors
                if "missing resultSet" in error or "schema mismatch" in error:
                    print(f"SKIP (no historical data)")
                    no_data_count += 1
                    consecutive_errors = 0  # Reset - this is expected for old games
                else:
                    print(f"SKIP: {error}")
                    error_count += 1
                    consecutive_errors += 1
                    
                    # Warn if hitting too many consecutive errors (might be rate limiting)
                    if consecutive_errors >= 5:
                        print(f"      [WARNING] {consecutive_errors} consecutive errors - possible rate limiting")
                        print(f"      Consider increasing --delay (currently {args.delay}s)")
                        if consecutive_errors >= 10:
                            print(f"      [WARNING] Pausing for 30 seconds to avoid rate limits...")
                            time.sleep(30)
                            consecutive_errors = 0
                
                time.sleep(args.delay)
                continue
            
            # Save immediately to CSV (unless dry-run)
            if not args.dry_run:
                df.to_csv(output_path, index=False)
                existing_files.add(output_file)  # Add to set so we don't reprocess
                print(f"OK ({len(df)} plays) ✓ saved")
            else:
                print(f"OK ({len(df)} plays) (dry-run)")
            
            success_count += 1
            consecutive_errors = 0  # Reset on success
            
            # Rate limiting
            time.sleep(args.delay)
        
        print(f"  {season_name} summary:")
        print(f"    ✓ Success: {success_count}")
        print(f"    ⊘ Already processed: {already_processed_count}")
        print(f"    ∅ No historical data: {no_data_count}")
        print(f"    ✗ Errors: {error_count}")
        
        total_success += success_count
        total_already_processed += already_processed_count
        total_no_data += no_data_count
        total_error += error_count
    
    print(f"\n{'='*70}")
    print(f"GRAND TOTAL:")
    print(f"  ✓ Success: {total_success}")
    print(f"  ⊘ Already processed: {total_already_processed}")
    print(f"  ∅ No historical data: {total_no_data}")
    print(f"  ✗ Errors: {total_error}")
    
    if not args.dry_run and total_success > 0:
        print(f"\nAll {total_success} new files saved to {args.output_dir}")
    elif args.dry_run:
        print(f"\nDry run complete - no files saved")


if __name__ == "__main__":
    main()
