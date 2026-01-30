
import argparse
import os
import time
import pandas as pd

from nba_api.live.nba.endpoints import playbyplay as live_pbp


def parse_matchup(matchup: str) -> tuple[str, str]:
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


def fetch_live_play_by_play(game_id: str, debug: bool = False):
    """
    Fetch live/cdn play-by-play for a single game_id.
    Returns (DataFrame or None, error_message or None).
    """
    try:
        pbp = live_pbp.PlayByPlay(game_id=game_id)
        
        # Get the raw dict and extract actions
        pbp_dict = pbp.get_dict()
        
        if debug:
            print(f"[DEBUG] Response keys: {pbp_dict.keys()}")
        
        # The structure is typically: {"game": {"actions": [...]}}
        game_data = pbp_dict.get("game", {})
        actions = game_data.get("actions", [])
        
        if not actions:
            return None, "No actions in response"
        
        df = pd.DataFrame(actions)
        
        if df.empty:
            return None, "Empty actions DataFrame"

        if debug:
            print(f"[DEBUG] Retrieved {len(df)} rows for game_id={game_id}")

        return df, None

    except Exception as e:
        if debug:
            print(f"[DEBUG] Exception for game_id={game_id}: {e}")
        return None, str(e)


def main():
    parser = argparse.ArgumentParser(description="Fetch NBA LIVE play-by-play data")
    parser.add_argument("schedule_csv", help="Path to schedule CSV file")
    parser.add_argument("--output-dir", default="nba/data/games_live",
                        help="Output directory for game files")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Delay between API calls (seconds)")
    parser.add_argument("--start-date", help="Only fetch games on or after this date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="Only fetch games on or before this date (YYYY-MM-DD)")
    parser.add_argument("--past-only", action="store_true",
                        help="Only fetch games that have already been played (before today)")
    parser.add_argument("--debug", action="store_true",
                        help="Print debug info for API responses")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Load schedule (keep GAME_ID as string)
    schedule = pd.read_csv(args.schedule_csv, dtype={"GAME_ID": str})
    schedule["GAME_DATE"] = pd.to_datetime(schedule["GAME_DATE"])

    if args.start_date:
        start = pd.to_datetime(args.start_date)
        schedule = schedule[schedule["GAME_DATE"] >= start]
    if args.end_date:
        end = pd.to_datetime(args.end_date)
        schedule = schedule[schedule["GAME_DATE"] <= end]
    if args.past_only:
        today = pd.Timestamp.now().normalize()
        schedule = schedule[schedule["GAME_DATE"] < today]
        print(f"Filtering to games before {today.strftime('%Y-%m-%d')}")

    print(f"Processing {len(schedule)} games...")

    success_count = 0
    skip_count = 0
    error_count = 0

    for _, row in schedule.iterrows():
        game_id = row["GAME_ID"]
        if len(game_id) < 10:
            game_id = game_id.zfill(10)

        game_date = row["GAME_DATE"]
        matchup = row["MATCHUP"]

        try:
            away_team, home_team = parse_matchup(matchup)
        except ValueError as e:
            print(f"  [ERROR] {e}")
            error_count += 1
            continue

        date_str = game_date.strftime("%Y%m%d")
        output_file = os.path.join(
            args.output_dir, f"{date_str}_{away_team}_{home_team}.csv"
        )

        if os.path.exists(output_file):
            print(f"  [SKIP] {output_file} already exists")
            skip_count += 1
            continue

        print(
            f"  Fetching LIVE {game_date.strftime('%Y-%m-%d')} "
            f"{away_team} @ {home_team} (game_id={game_id})...",
            end=" " if not args.debug else "\n",
        )

        df, error = fetch_live_play_by_play(game_id, debug=args.debug)

        if error:
            print(f"SKIP: {error}")
            skip_count += 1
            time.sleep(args.delay)
            continue

        df.to_csv(output_file, index=False)
        print(f"OK ({len(df)} actions)")
        success_count += 1

        time.sleep(args.delay)

    print(
        f"\nDone! Success: {success_count}, Skipped: {skip_count}, Errors: {error_count}"
    )


if __name__ == "__main__":
    main()
