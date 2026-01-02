import requests
import json

url = "https://api.elections.kalshi.com/trade-api/v2/series"

response = requests.get(url)

# save the json
with open("series.json", "w") as f:
    json.dump(response.json(), f)