"""Fetch all 30 NBA team rosters from ESPN and save to data/nba_roster/."""

import json
import httpx
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "nba_roster"
DATA_DIR.mkdir(exist_ok=True)

# ESPN uses different abbreviations than standard
# Map: canonical abbr -> ESPN API abbr
ESPN_TEAMS = [
    "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET",
    "GS", "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN",
    "NO", "NY", "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SA",
    "TOR", "UTAH", "WSH",
]

# ESPN abbr -> canonical file name
ESPN_TO_CANONICAL = {
    "GS": "GSW", "SA": "SAS", "NY": "NYK", "UTAH": "UTA",
    "WSH": "WAS", "NO": "NOP", "PHX": "PHX",
}


def main():
    with httpx.Client(timeout=15) as client:
        for espn_abbr in ESPN_TEAMS:
            canonical = ESPN_TO_CANONICAL.get(espn_abbr, espn_abbr)
            url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{espn_abbr}/roster"
            print(f"Fetching {espn_abbr} -> {canonical}...")
            try:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"  ERROR: {e}")
                continue

            players = []
            for athlete in data.get("athletes", []):
                players.append({
                    "id": athlete.get("id"),
                    "name": athlete.get("displayName", athlete.get("fullName", "")),
                    "position": athlete.get("position", {}).get("abbreviation", ""),
                    "jersey": athlete.get("jersey", ""),
                })

            out = DATA_DIR / f"{canonical}.json"
            out.write_text(json.dumps(players, indent=2))
            print(f"  Saved {len(players)} players to {out.name}")

    print("Done.")


if __name__ == "__main__":
    main()
