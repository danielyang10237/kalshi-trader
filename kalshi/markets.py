from .authentification import get_authentification_headers

import json
import requests

url = "https://api.elections.kalshi.com/trade-api/v2/markets"
params = {
    "limit": 100
}

response = requests.get(url, params=params)
response.raise_for_status()  # fail fast on HTTP errors

data = response.json()

# Save to file
output_file = "markets.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)

print(f"Saved response to {output_file}")