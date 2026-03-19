"""
Fetch player stats for 2025-26 season using ESPN API and save to nba/data/players_live/

IMPORTANT: This script requires an ESPN event_id mapping file.
You must first run discover_espn_event_ids.py to build the mapping:

    python nba/discover_espn_event_ids.py --past-only

Then run this script with the generated mapping:

    python nba/fetch_player_stats_2025_26.py --mapping-file nba/espn_event_ids_2025_26.csv --past-only
"""
import argparse
import os
import time
import pandas as pd
import requests


ESPN_SUMMARY_URL = (
    "https://site.web.api.espn.com/apis/site/v2/sports/"
    "basketball/nba/summary"
)


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


def nba_game_id_to_espn_event_id(game_id: str, mapping: dict[str, str]) -> list[str]:
    """
    Get ESPN event_id from NBA game_id using explicit mapping.
    
    Returns list with single event_id if mapping exists, empty list otherwise.
    No guessing - requires actual mapping from ESPN scoreboard API.
    """
    if game_id in mapping:
        return [mapping[game_id]]
    # No mapping: return empty list to signal "no mapping available"
    return []


def fetch_espn_game_player_stats(event_id: str, nba_game_id: str = None, debug: bool = False) -> tuple[pd.DataFrame | None, str | None]:
    """
    Fetch all player boxscore stats for a single ESPN NBA event_id
    using ESPN's summary endpoint.

    Returns (DataFrame, error_message) with one row per player and stat columns expanded.
    """
    try:
        params = {"event": event_id}
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://www.espn.com/nba/game?gameId={event_id}",
        }

        resp = requests.get(ESPN_SUMMARY_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if debug:
            print(f"      [DEBUG] Top-level keys: {list(data.keys())}")

        box = data.get("boxscore")
        if not box:
            return None, "No boxscore object in ESPN summary response"

        players_blocks = box.get("players", [])
        if not players_blocks:
            return None, "No players data in boxscore"
        
        rows = []

        for team_block in players_blocks:
            team_info = team_block.get("team", {})
            team_id = team_info.get("id")
            team_name = team_info.get("displayName")
            team_abbr = team_info.get("abbreviation")

            # In the actual ESPN format, all player box lines are in the FIRST statistics table
            stats_tables = team_block.get("statistics", [])
            if not stats_tables:
                continue
            
            # Get the first table which contains all the player stats
            table = stats_tables[0]
            names = table.get("names", [])  # Human-friendly names
            keys = table.get("keys", []) or names  # Machine-friendly names
            athletes = table.get("athletes", [])
            
            for a in athletes:
                athlete_info = a.get("athlete", {})
                player_id = athlete_info.get("id")
                
                if not player_id:
                    continue
                
                # Get stats values and zip with keys
                stats_values = a.get("stats", [])
                flat_stats = {}
                for col_id, value in zip(keys, stats_values):
                    flat_stats[f"stat_{col_id}"] = value

                row = {
                    "nba_game_id": nba_game_id if nba_game_id else "",
                    "espn_event_id": event_id,
                    "team_id": team_id,
                    "team_name": team_name,
                    "team_abbr": team_abbr,
                    "player_id": player_id,
                    "player_name": athlete_info.get("displayName"),
                    "player_short_name": athlete_info.get("shortName"),
                    "player_position": (athlete_info.get("position") or {}).get("abbreviation"),
                    "starter": a.get("starter"),
                    "did_not_play": a.get("didNotPlay"),
                    "did_not_dress": a.get("didNotDress"),
                    "active": a.get("active"),
                }
                row.update(flat_stats)
                rows.append(row)

        if not rows:
            return None, "No player rows parsed from ESPN boxscore"

        df = pd.DataFrame(rows)
        return df, None
    
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return None, "Game not found (404)"
        return None, f"HTTP {e.response.status_code}: {e.response.reason}"
    
    except requests.exceptions.RequestException as e:
        return None, f"Request error: {str(e)}"
    
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        if debug:
            import traceback
            print(f"      [DEBUG] Exception occurred:")
            traceback.print_exc()
        return None, error_msg


def main():
    parser = argparse.ArgumentParser(
        description="Fetch 2025-26 season player stats using ESPN API"
    )
    parser.add_argument(
        "--schedule",
        default="nba/data/schedules/schedule_2025-26.csv",
        help="Path to 2025-26 schedule CSV"
    )
    parser.add_argument(
        "--output-dir",
        default="nba/data/players_live",
        help="Output directory for player stats files"
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
        "--debug",
        action="store_true",
        help="Print debug information"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't save files, just show what would be processed"
    )
    parser.add_argument(
        "--mapping-file",
        help="CSV mapping file with columns: GAME_ID,ESPN_EVENT_ID (required for 2025-26)"
    )
    args = parser.parse_args()
    
    # Load event ID mapping
    event_id_mapping = {}
    if args.mapping_file and os.path.exists(args.mapping_file):
        print(f"Loading event ID mapping from {args.mapping_file}...")
        mapping_df = pd.read_csv(args.mapping_file, dtype={"GAME_ID": str, "ESPN_EVENT_ID": str})
        event_id_mapping = dict(zip(mapping_df["GAME_ID"], mapping_df["ESPN_EVENT_ID"]))
        print(f"Loaded {len(event_id_mapping)} game ID mappings")
    else:
        print(f"\n⚠️  WARNING: No mapping file provided!")
        print(f"   For 2025-26 season, you must first run:")
        print(f"     python nba/discover_espn_event_ids.py --past-only")
        print(f"   Then use the generated mapping file with:")
        print(f"     python nba/fetch_player_stats_2025_26.py --mapping-file nba/data/espn_event_ids_2025_26.csv --past-only")
        print(f"\n   Continuing without mapping - all games will be skipped...\n")

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)

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
    
    # Get list of already processed games
    existing_files = set()
    if os.path.exists(args.output_dir):
        existing_files = set(os.listdir(args.output_dir))
    
    print(f"Found {len(existing_files)} existing player stats files in {args.output_dir}")
    print(f"\nProcessing {len(schedule)} games from schedule...\n")
    
    success_count = 0
    already_processed_count = 0
    no_data_count = 0
    error_count = 0
    consecutive_errors = 0
    
    for idx, row in schedule.iterrows():
        game_id = str(row["GAME_ID"])
        
        game_date = row["GAME_DATE"]
        matchup = row["MATCHUP"]
        
        try:
            away_team, home_team = parse_matchup(matchup)
        except ValueError as e:
            print(f"  [ERROR] {e}")
            error_count += 1
            continue
        
        # Build output filename using NBA game_id
        output_file = f"{game_id}_{away_team}_{home_team}.csv"
        output_path = os.path.join(args.output_dir, output_file)
        
        # Skip if already exists
        if output_file in existing_files:
            if args.debug:
                print(f"  [SKIP] {output_file} already exists")
            already_processed_count += 1
            continue
        
        print(
            f"  [{idx+1}/{len(schedule)}] {game_date.strftime('%Y-%m-%d')} "
            f"{away_team} @ {home_team} (game_id={game_id})...",
            end=" " if not args.debug else "\n",
        )
        
        # Try to fetch player stats using event_id mapping
        df = None
        error = None
        used_event_id = None
        
        # Get event_id from mapping (no guessing)
        event_id_candidates = nba_game_id_to_espn_event_id(game_id, event_id_mapping)
        
        if not event_id_candidates:
            # No mapping available for this game
            print(f"SKIP (no ESPN event_id mapping)")
            no_data_count += 1
            consecutive_errors = 0  # Not an error, just missing mapping
            continue
        
        # We have a mapping, try to fetch
        event_id = event_id_candidates[0]
        
        if args.debug:
            print(f"      [DEBUG] Using mapped event_id={event_id}")
        
        df, error = fetch_espn_game_player_stats(event_id, nba_game_id=game_id, debug=args.debug)
        
        if df is not None:
            used_event_id = event_id
        
        # Handle result
        if df is None:
            # Differentiate between missing data vs real errors
            if "not found" in error.lower() or "404" in error:
                print(f"SKIP (game not available)")
                no_data_count += 1
                consecutive_errors = 0  # Reset - expected for future/unavailable games
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
            print(f"OK ({len(df)} players) ✓ saved")
        else:
            print(f"OK ({len(df)} players) (dry-run)")
        
        success_count += 1
        consecutive_errors = 0  # Reset on success
        
        # Rate limiting
        time.sleep(args.delay)
    
    print(f"\n{'='*70}")
    print(f"Summary:")
    print(f"  ✓ Success: {success_count}")
    print(f"  ⊘ Already processed: {already_processed_count}")
    print(f"  ∅ No ESPN mapping: {no_data_count}")
    print(f"  ✗ Errors: {error_count}")
    
    if not args.dry_run and success_count > 0:
        print(f"\nAll {success_count} new files saved to {args.output_dir}")
    elif args.dry_run:
        print(f"\nDry run complete - no files saved")
    
    if no_data_count > 0:
        print(f"\n⚠️  {no_data_count} games skipped due to missing ESPN event_id mapping")
        print(f"   Run discover_espn_event_ids.py to build the mapping for these games")


if __name__ == "__main__":
    main()
