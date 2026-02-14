"""
Discover ESPN event IDs for games on specific dates using ESPN's scoreboard API.
This helps build the mapping from NBA game_id to ESPN event_id.
"""
import argparse
import pandas as pd
import requests
import time


ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"

# Mapping from ESPN abbreviations to NBA API abbreviations
ESPN_TO_NBA_ABBR = {
    "GS": "GSW",
    "NY": "NYK",
    "NO": "NOP",
    "WSH": "WAS",
    "UTAH": "UTA",
    "SA": "SAS",
}


def fetch_espn_scoreboard(date: str) -> list[dict]:
    """
    Fetch ESPN scoreboard for a specific date.
    Date format: YYYYMMDD (e.g., '20251021')
    
    Returns list of games with their event IDs and team info.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    
    params = {"dates": date}
    
    # Convert YYYYMMDD to YYYY-MM-DD for consistency
    query_date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    
    try:
        resp = requests.get(ESPN_SCOREBOARD_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        events = data.get("events", [])
        games = []
        
        for event in events:
            event_id = event.get("id")
            name = event.get("name", "")
            
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            
            comp = competitions[0]
            competitors = comp.get("competitors", [])
            
            if len(competitors) < 2:
                continue
            
            # ESPN lists home team first (index 0), away team second (index 1)
            home_team = competitors[0].get("team", {})
            away_team = competitors[1].get("team", {})
            
            home_abbr = home_team.get("abbreviation", "")
            away_abbr = away_team.get("abbreviation", "")
            
            # Convert ESPN abbreviations to NBA abbreviations
            home_abbr = ESPN_TO_NBA_ABBR.get(home_abbr, home_abbr)
            away_abbr = ESPN_TO_NBA_ABBR.get(away_abbr, away_abbr)
            
            games.append({
                "espn_event_id": event_id,
                "date": query_date,  # Use the query date instead of ESPN's date field (which is in UTC)
                "away_team": away_abbr,
                "home_team": home_abbr,
                "name": name,
            })
        
        return games
        
    except Exception as e:
        print(f"Error fetching scoreboard for {date}: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(
        description="Discover ESPN event IDs from scoreboard API"
    )
    parser.add_argument(
        "--schedule",
        default="nba/data/schedules/schedule_2025-26.csv",
        help="Path to schedule CSV to process"
    )
    parser.add_argument(
        "--output",
        default="nba/data/espn_event_ids_2025_26.csv",
        help="Output CSV file for discovered event IDs"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between API calls (seconds)"
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
    
    # Get unique dates from schedule
    unique_dates = schedule["GAME_DATE"].dt.strftime("%Y%m%d").unique()
    print(f"Found {len(unique_dates)} unique dates to query\n")
    
    all_espn_games = []
    
    for idx, date in enumerate(unique_dates, 1):
        print(f"[{idx}/{len(unique_dates)}] Fetching scoreboard for {date}...", end=" ")
        
        games = fetch_espn_scoreboard(date)
        
        if games:
            print(f"OK ({len(games)} games)")
            all_espn_games.extend(games)
        else:
            print("No games found")
        
        time.sleep(args.delay)
    
    if not all_espn_games:
        print("\nNo ESPN games found!")
        return
    
    # Create DataFrame from ESPN games
    espn_df = pd.DataFrame(all_espn_games)
    
    # Now match with NBA schedule to create mapping
    print(f"\nMatching {len(espn_df)} ESPN games with NBA schedule...")
    
    mappings = []
    
    for _, sched_row in schedule.iterrows():
        game_id = str(sched_row["GAME_ID"])
        
        game_date = sched_row["GAME_DATE"].strftime("%Y-%m-%d")
        matchup = sched_row["MATCHUP"]
        
        # Parse matchup
        if " vs. " in matchup:
            home_team = matchup.split(" vs. ")[0].strip()
            away_team = matchup.split(" vs. ")[1].strip()
        elif " @ " in matchup:
            away_team = matchup.split(" @ ")[0].strip()
            home_team = matchup.split(" @ ")[1].strip()
        else:
            continue
        
        # Find matching ESPN game
        match = espn_df[
            (espn_df["date"] == game_date) &
            (espn_df["away_team"] == away_team) &
            (espn_df["home_team"] == home_team)
        ]
        
        if not match.empty:
            espn_event_id = match.iloc[0]["espn_event_id"]
            mappings.append({
                "GAME_ID": game_id,
                "ESPN_EVENT_ID": espn_event_id,
                "GAME_DATE": game_date,
                "MATCHUP": matchup,
            })
    
    if mappings:
        mapping_df = pd.DataFrame(mappings)
        mapping_df.to_csv(args.output, index=False)
        print(f"\n✓ Saved {len(mappings)} game ID mappings to {args.output}")
        print(f"\nSample mappings:")
        print(mapping_df.head(10))
        
        print(f"\n\nTo use this mapping with fetch_player_stats_2025_26.py:")
        print(f"  python nba/fetch_player_stats_2025_26.py --mapping-file {args.output} --past-only")
    else:
        print("\n✗ No matches found between ESPN and NBA schedules")


if __name__ == "__main__":
    main()
