import csv
import shutil
from pathlib import Path

# Paths
csv_file = "nfl/data/schedules/nfl_schedule_2014_2017.csv"
rosters_dir = Path("nfl/data/rosters")

# Read CSV and process each row
with open(csv_file, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    
    removed_count = 0
    not_found_count = 0
    
    for row in reader:
        game_id = row['game_id']
        folder_path = rosters_dir / game_id
        
        if folder_path.exists() and folder_path.is_dir():
            shutil.rmtree(folder_path)
            print(f"Removed: {game_id}")
            removed_count += 1
        else:
            not_found_count += 1
    
    print("\nSummary:")
    print(f"  Folders removed: {removed_count}")
    print(f"  Folders not found: {not_found_count}")

