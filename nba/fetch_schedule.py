from nba_api.stats.endpoints import leaguegamefinder
from nba_api.stats.library.http import NBAStatsHTTP
import pandas as pd
import os
import time
import traceback
import requests

# Create output directory if it doesn't exist
os.makedirs("nba/data/schedules", exist_ok=True)

for year in range(2002, 1999, -1):

    season = f"{year}-{year+1-2000}"

    output_file = f"nba/data/schedules/schedule_{season}.csv"

    if os.path.exists(output_file):
        print(f"✓ Schedule already exists: {output_file}")
        continue  # Changed from exit() to continue to process all seasons

    print(f"\nFetching season {season}...")
    
    try:
        # DEBUG: Try to manually fetch and inspect response before LeagueGameFinder parses it
        # Make raw request to see what we get
        base_url = "https://stats.nba.com/stats/leaguegamefinder"
        params = {
            'SeasonNullable': season,
            'SeasonTypeNullable': 'Regular Season',
            'LeagueIDNullable': '00',
        }
        headers = NBAStatsHTTP().headers
        
        print("  [DEBUG] Making raw request to inspect response...")
        response = requests.get(base_url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        
        raw_json = response.json()
        print(f"  [DEBUG] Raw response keys: {list(raw_json.keys())}")
        
        # Check what structure we got and decide how to parse
        if 'resultSets' in raw_json:
            print(f"  [DEBUG] ✓ Has 'resultSets' with {len(raw_json['resultSets'])} items")
            # Try using the library first, but be ready to fall back to manual parsing
        elif 'resultSet' in raw_json:
            print("  [DEBUG] ✓ Has 'resultSet' (singular)")
        else:
            print("  [DEBUG] ✗ No 'resultSets' or 'resultSet' found!")
            print("  [DEBUG] Full response structure:")
            for key, val in raw_json.items():
                if isinstance(val, (dict, list)) and len(str(val)) > 200:
                    print(f"    {key}: {type(val).__name__} (length: {len(val) if isinstance(val, list) else 'N/A'})")
                else:
                    print(f"    {key}: {val}")
            continue
        
        # Try to use LeagueGameFinder, but catch KeyError and manually parse if needed
        try:
            lgf = leaguegamefinder.LeagueGameFinder(
                season_nullable=season,
                season_type_nullable="Regular Season",
                league_id_nullable="00",
            )
            df = lgf.get_data_frames()[0]
        except KeyError as ke:
            # Library failed due to resultSet vs resultSets mismatch
            print(f"  [DEBUG] Library failed with KeyError: {ke}, manually parsing...")
            
            # Manually parse the resultSets data
            if 'resultSets' in raw_json and len(raw_json['resultSets']) > 0:
                result_set = raw_json['resultSets'][0]
                headers = result_set['headers']
                rows = result_set['rowSet']
                df = pd.DataFrame(rows, columns=headers)
                print(f"  [DEBUG] ✓ Manually parsed {len(df)} rows")
            else:
                raise

        if df.empty:
            print(f"✗ No data found for season {season}")
            continue

        # Parse dates and keep ONE row per game (since each game appears twice)
        df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"])
        schedule = (
            df.sort_values("GAME_DATE")
            .drop_duplicates(subset=["GAME_ID"])
            [["GAME_ID", "GAME_DATE", "MATCHUP"]]
            .reset_index(drop=True)
        )

        print(f"  Total games: {len(schedule)}")
        print(f"  Date range: {schedule['GAME_DATE'].min().date()} to {schedule['GAME_DATE'].max().date()}")

        schedule.to_csv(output_file, index=False)
        print(f"✓ Saved schedule to {output_file}")
        
        # Add delay to avoid rate limiting
        time.sleep(1.0)
        
    except KeyError as e:
        print(f"✗ Unhandled KeyError for season {season}: {e}")
        print("  (This shouldn't happen if manual parsing works)")
        traceback.print_exc()
        continue
    except Exception as e:
        print(f"✗ Unexpected error for season {season}: {type(e).__name__}: {e}")
        traceback.print_exc()
        continue

print("\n" + "="*50)
print("Done fetching schedules!")