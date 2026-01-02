"""
Fetch team records at the time of each game and add to schedule CSV.
"""

import csv
import time
import requests
from pathlib import Path

from . import (
    _get_team_code_from_name,
    _get_team_id_code,
)

RECORDS_URL = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{event_id}/competitions/{event_id}/competitors/{team_id}/records"

REQUEST_DELAY = 0.1


def get_team_record(event_id: str, team_id: str) -> str | None:
    """Fetch team record at the time of a specific event."""
    url = RECORDS_URL.format(event_id=event_id, team_id=team_id)
    
    response = requests.get(url)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    
    data = response.json()
    
    # Find the "overall" record
    for item in data.get("items", []):
        if item.get("name") == "overall":
            return item.get("summary")
    
    return None


def process_schedule(csv_path: str):
    """Process schedule CSV and add team records."""
    csv_path = Path(csv_path)
    
    if not csv_path.exists():
        print(f"Error: File not found: {csv_path}")
        return
    
    # Read existing data
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)
    
    # Add columns if they don't exist
    if "home_record" not in fieldnames:
        fieldnames.append("home_record")
    if "away_record" not in fieldnames:
        fieldnames.append("away_record")
    
    # Track games without records
    games_without_records = []
    
    # Process each row
    for i, row in enumerate(rows):
        game_id = row.get("game_id", "")
        
        # Skip if records already exist
        if row.get("home_record") and row.get("away_record"):
            continue
        
        home_team_name = row.get("home_team")
        away_team_name = row.get("away_team")
        game_date = row.get("gameday")
        
        if not home_team_name or not away_team_name or not game_date:
            print(f"[{i+1}/{len(rows)}] Skipping {game_id}: missing team or date info")
            continue
        
        print(f"[{i+1}/{len(rows)}] Processing {game_id}...")
        
        try:
            # Get event_id from espn column
            event_id = row.get("espn")
            
            if not event_id:
                print(f"  No ESPN event_id for {game_id}")
                games_without_records.append(game_id)
                continue
            
            # Convert team names to codes and IDs
            home_team_code = _get_team_code_from_name(home_team_name)
            away_team_code = _get_team_code_from_name(away_team_name)
            
            home_team_id = _get_team_id_code(home_team_code)
            away_team_id = _get_team_id_code(away_team_code)
            
            # Fetch records if not already present
            if not row.get("home_record"):
                time.sleep(REQUEST_DELAY)
                home_record = get_team_record(event_id, home_team_id)
                if home_record:
                    row["home_record"] = home_record
                    print(f"  Home ({home_team_name}): {home_record}")
                else:
                    print(f"  Could not get home record for {home_team_name}")
                    games_without_records.append(f"{game_id} (home)")
            
            if not row.get("away_record"):
                time.sleep(REQUEST_DELAY)
                away_record = get_team_record(event_id, away_team_id)
                if away_record:
                    row["away_record"] = away_record
                    print(f"  Away ({away_team_name}): {away_record}")
                else:
                    print(f"  Could not get away record for {away_team_name}")
                    games_without_records.append(f"{game_id} (away)")
        
        except Exception as e:
            print(f"  Error processing {game_id}: {e}")
            games_without_records.append(game_id)
            continue
    
    # Write updated CSV
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"\nUpdated {csv_path}")
    
    if games_without_records:
        print(f"\nGames without records ({len(games_without_records)}):")
        for game in games_without_records:
            print(f"  - {game}")


def main():
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m nfl.fetch_team_records <csv_path>")
        print("Example: python -m nfl.fetch_team_records nfl/data/schedules/nfl_schedule_2002_2005.csv")
        sys.exit(1)
    
    csv_path = sys.argv[1]
    process_schedule(csv_path)


if __name__ == "__main__":
    main()