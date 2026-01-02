import os
import json
import requests
import time
from dotenv import load_dotenv

load_dotenv()

output_dir = "nfl/data/schedules"

def get_sportsdataio_api_key():
    api_key = os.getenv("SPORTS_DATA_IO_KEY")
    if not api_key:
        raise ValueError("SPORTS_DATA_IO_KEY not found in environment variables")
    return api_key

def get_nfl_schedule_save_to_file(output_file: str, season: str) -> dict:
    url = f"https://api.sportsdata.io/v3/nfl/scores/json/Schedules/{season}"
    params = {"key": get_sportsdataio_api_key()}

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(r.json(), f, ensure_ascii=False, indent=2)

    print(f"Saved NFL schedule for {season} to {output_file}")

    return r.json()

def get_nfl_schedule(season: str):
    if not os.path.exists(output_dir + f"/nfl_schedule_{season}.json"):
        nfl_schedule = get_nfl_schedule_save_to_file(output_dir + f"/nfl_schedule_{season}.json", season)
    else:
        nfl_schedule = json.load(open(output_dir + f"/nfl_schedule_{season}.json", "r", encoding="utf-8"))
    return nfl_schedule

if __name__ == "__main__":
    for i in range(2018, 2000, -1):
        get_nfl_schedule(str(i))
        time.sleep(1)