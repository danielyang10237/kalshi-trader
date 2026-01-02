"""
Fetch player game stats from ESPN API for all starters in the schedule.
"""

import csv
import json
import os
import time
import requests
from pathlib import Path

from . import _get_team_code_from_name, _get_position_name_from_id

SCHEDULE_PATH = "nfl/data/schedules/nfl_schedule_1999_2001.csv"
# SCHEDULE_PATH = "nfl/data/schedules/nfl_schedule_2002_2005.csv"
# SCHEDULE_PATH = "nfl/data/schedules/nfl_schedule_2006_2009.csv"
# SCHEDULE_PATH = "nfl/data/schedules/nfl_schedule_2010_2013.csv"
# SCHEDULE_PATH = "nfl/data/schedules/nfl_schedule_2014_2017.csv"
# SCHEDULE_PATH = "nfl/data/schedules/nfl_schedule_2018_2021.csv"
ROSTERS_DIR = "nfl/data/rosters"
PLAYERS_DIR = "nfl/data/players"
NO_STATS_CACHE_PATH = "nfl/data/players/no_stats_cache.json"

ESPN_GAMELOG_URL = "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{athlete_id}/gamelog?season={season}"

REQUEST_DELAY = 0.1
MAX_RETRIES = 3
RETRY_DELAY = 2.0


def load_no_stats_cache() -> set[str]:
    """Load the set of athlete IDs known to have no stats."""
    cache_path = Path(NO_STATS_CACHE_PATH)
    if cache_path.exists():
        with open(cache_path, "r") as f:
            data = json.load(f)
            return set(data.get("athlete_ids", []))
    return set()


def save_no_stats_cache(athlete_ids: set[str]):
    """Save the set of athlete IDs known to have no stats."""
    cache_path = Path(NO_STATS_CACHE_PATH)
    with open(cache_path, "w") as f:
        json.dump({"athlete_ids": sorted(athlete_ids)}, f, indent=2)


def make_request_with_retry(url: str) -> requests.Response:
    """Make HTTP request with retry logic for transient errors."""
    last_exception = None
    
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as e:
            # Don't retry 404s - those are permanent
            if e.response.status_code == 404:
                raise
            # Retry on 5xx errors (server errors, timeouts)
            if e.response.status_code >= 500:
                last_exception = e
                print(f"        Retry {attempt + 1}/{MAX_RETRIES} after {e.response.status_code} error...")
                time.sleep(RETRY_DELAY * (attempt + 1))  # Exponential backoff
            else:
                raise
        except requests.exceptions.RequestException as e:
            # Retry on connection errors, timeouts, etc.
            last_exception = e
            print(f"        Retry {attempt + 1}/{MAX_RETRIES} after connection error...")
            time.sleep(RETRY_DELAY * (attempt + 1))
    
    # If we exhausted retries, raise the last exception
    raise last_exception


def get_available_seasons(data: dict) -> list[str]:
    """Extract available seasons from the API response."""
    for f in data.get("filters", []):
        if f.get("name") == "season":
            return [opt["value"] for opt in f.get("options", [])]
    return []


def get_prefixed_labels(labels: list[str], categories: list[dict]) -> list[str]:
    """
    Prefix labels based on the categories in the response.
    Categories tell us which stats belong to which group (passing, rushing, receiving, etc.)
    """
    prefixed = []
    label_idx = 0
    
    for cat in categories:
        cat_name = cat.get("name", "unknown").upper()
        count = cat.get("count", 0)
        
        for i in range(count):
            if label_idx < len(labels):
                prefixed.append(f"{cat_name}_{labels[label_idx]}")
                label_idx += 1
    
    # Handle any remaining labels not covered by categories
    while label_idx < len(labels):
        prefixed.append(f"OTHER_{labels[label_idx]}")
        label_idx += 1
    
    return prefixed


def extract_games_from_season(data: dict, athlete_id: str, season: str) -> list[dict]:
    """Extract per-game stats from a season response."""
    games = []
    
    labels = data.get("labels", [])
    categories = data.get("categories", [])
    
    if not labels:
        return games
    
    prefixed_labels = get_prefixed_labels(labels, categories)
    events_info = data.get("events", {})
    
    season_types = data.get("seasonTypes", [])
    for season_type in season_types:
        cats = season_type.get("categories", [])
        for category in cats:
            events = category.get("events", [])
            for event in events:
                event_id = event.get("eventId")
                stats = event.get("stats", [])
                
                if not event_id or not stats:
                    continue
                
                game_data = {
                    "athlete_id": athlete_id,
                    "season": season,
                    "event_id": event_id,
                }
                
                # Add all stats with prefixed labels
                for label, value in zip(prefixed_labels, stats):
                    game_data[label] = value
                
                games.append(game_data)
    
    return games


def fetch_player_stats(athlete_id: str) -> list[dict]:
    """Fetch all available season stats for a player."""
    all_games = []
    
    # First, fetch any season to get the list of available seasons
    url = ESPN_GAMELOG_URL.format(athlete_id=athlete_id, season="2024")
    response = make_request_with_retry(url)
    data = response.json()
    
    available_seasons = get_available_seasons(data)
    
    if not available_seasons:
        return all_games
    
    # Extract games from the first response if 2024 is in available seasons
    if "2024" in available_seasons:
        games = extract_games_from_season(data, athlete_id, "2024")
        all_games.extend(games)
    
    # Fetch remaining seasons
    for season in available_seasons:
        if season == "2024":
            continue  # Already fetched
        
        time.sleep(REQUEST_DELAY)
        
        url = ESPN_GAMELOG_URL.format(athlete_id=athlete_id, season=season)
        response = make_request_with_retry(url)
        data = response.json()
        
        games = extract_games_from_season(data, athlete_id, season)
        all_games.extend(games)
    
    return all_games


def save_player_stats(games: list[dict], filepath: Path):
    """Save player game stats to CSV."""
    if not games:
        return
    
    # Collect ALL fieldnames across all games (different seasons may have different stats)
    all_fieldnames = set()
    for game in games:
        all_fieldnames.update(game.keys())
    
    # Ensure consistent ordering: athlete_id, season, event_id first, then sorted stat columns
    base_fields = ["athlete_id", "season", "event_id"]
    stat_fields = sorted(all_fieldnames - set(base_fields))
    fieldnames = base_fields + stat_fields
    
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for game in games:
            # Fill missing fields with empty string
            row = {field: game.get(field, "") for field in fieldnames}
            writer.writerow(row)


def player_file_exists(athlete_id: str, display_name: str) -> bool:
    """Check if player stats file already exists."""
    filepath = Path(PLAYERS_DIR) / f"{athlete_id}_{display_name}.csv"
    return filepath.exists()


def get_player_filepath(athlete_id: str, display_name: str) -> Path:
    """Get the filepath for a player's stats."""
    return Path(PLAYERS_DIR) / f"{athlete_id}_{display_name}.csv"


def process_roster(
    roster_path: Path,
    processed_players: set,
    positions_without_stats: set,
    no_stats_cache: set
):
    """Process a single roster file and fetch stats for starters."""
    if not roster_path.exists():
        print(f"  Roster not found: {roster_path}")
        return
    
    with open(roster_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Only process starters
            if row.get("starter") != "True":
                continue
            
            athlete_id = row.get("athlete_id")
            display_name = row.get("display_name")
            position_id = row.get("position_id")
            
            if not athlete_id or not display_name:
                continue
            
            # Skip if already processed this run
            player_key = f"{athlete_id}_{display_name}"
            if player_key in processed_players:
                continue
            
            # Skip if file already exists
            if player_file_exists(athlete_id, display_name):
                processed_players.add(player_key)
                continue
            
            # Skip if in no-stats cache
            if athlete_id in no_stats_cache:
                processed_players.add(player_key)
                if position_id:
                    position_name = _get_position_name_from_id(position_id)
                    positions_without_stats.add(position_name)
                continue
            
            print(f"    Fetching stats for {display_name} ({athlete_id})...")
            
            try:
                games = fetch_player_stats(athlete_id)
                
                if games:
                    filepath = get_player_filepath(athlete_id, display_name)
                    save_player_stats(games, filepath)
                    print(f"      Saved {len(games)} games")
                else:
                    # No stats available - track the position and cache
                    no_stats_cache.add(athlete_id)
                    if position_id:
                        position_name = _get_position_name_from_id(position_id)
                        positions_without_stats.add(position_name)
                    print(f"      No stats available (cached)")
                
                processed_players.add(player_key)
                time.sleep(REQUEST_DELAY)
                
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    # Player not found - track position, cache, and continue
                    no_stats_cache.add(athlete_id)
                    if position_id:
                        position_name = _get_position_name_from_id(position_id)
                        positions_without_stats.add(position_name)
                    processed_players.add(player_key)
                    print(f"      No stats available (404, cached)")
                else:
                    raise


def main():
    # Ensure output directory exists
    Path(PLAYERS_DIR).mkdir(parents=True, exist_ok=True)
    
    # Load no-stats cache
    no_stats_cache = load_no_stats_cache()
    print(f"Loaded {len(no_stats_cache)} players from no-stats cache")
    
    # Track processed players and positions without stats
    processed_players: set[str] = set()
    positions_without_stats: set[str] = set()
    
    # Read schedule
    with open(SCHEDULE_PATH, "r") as f:
        reader = csv.DictReader(f)
        schedule_rows = list(reader)
    
    print(f"Processing {len(schedule_rows)} games...")
    
    try:
        for i, row in enumerate(schedule_rows):
            game_id = row.get("game_id")
            if not game_id:
                continue
            
            print(f"[{i+1}/{len(schedule_rows)}] Processing game: {game_id}")
            
            home_team = _get_team_code_from_name(row["home_team"])
            away_team = _get_team_code_from_name(row["away_team"])
            
            roster_dir = Path(ROSTERS_DIR) / game_id
            
            # Process home roster
            home_roster_path = roster_dir / f"{home_team}.csv"
            print(f"  Processing home roster: {home_team}")
            process_roster(home_roster_path, processed_players, positions_without_stats, no_stats_cache)
            
            # Process away roster
            away_roster_path = roster_dir / f"{away_team}.csv"
            print(f"  Processing away roster: {away_team}")
            process_roster(away_roster_path, processed_players, positions_without_stats, no_stats_cache)
    
    finally:
        # Always save the cache, even if we crash
        save_no_stats_cache(no_stats_cache)
        print(f"\nSaved {len(no_stats_cache)} players to no-stats cache")
    
    print("\n" + "=" * 50)
    print("COMPLETE")
    print(f"Total players processed: {len(processed_players)}")
    print(f"\nPositions without stats data:")
    for pos in sorted(positions_without_stats):
        print(f"  - {pos}")


if __name__ == "__main__":
    main()