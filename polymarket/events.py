import requests
import datetime
import dotenv
import json

dotenv.load_dotenv()

POLYMARKET_BASE_URL = "https://gamma-api.polymarket.com"

def get_tags(limit: int, offset: int):
    url = POLYMARKET_BASE_URL + "/tags"
    params = {
        "limit": limit,
        "offset": offset
    }
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e}")
        return []
    return response.json()

def get_all_tags(total_limit: int = 10000):
    tags = []
    limit = 100
    offset = 0
    while True:
        next_tags = get_tags(limit, offset)
        if len(next_tags) == 0 or len(tags) >= total_limit:
            break
        tags.extend(next_tags)
        offset += limit
        print(f"Fetched {len(tags)} tags")
    return tags

def filter_tags(keywords: list[str], tags_path: str = 'polymarket/data/tags.json'):
    with open(tags_path, 'r', encoding='utf-8') as f:
        tags = json.load(f)
    keywords = [keyword.lower() for keyword in keywords]
    filtered_tags = []
    for tag in tags:
        tag_id, label, slug = tag['id'], tag['label'], tag['slug']
        label = label.lower()
        if any(keyword in label for keyword in keywords):
            filtered_tags.append({
                "id": tag_id,
                "label": label,
                "slug": slug
            })
    return filtered_tags

def to_rfc3339(dt: datetime.datetime) -> str:
    # Ensure timezone-aware UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        dt = dt.astimezone(datetime.timezone.utc)

    # Use milliseconds (common API expectation)
    dt = dt.replace(microsecond=(dt.microsecond // 1000) * 1000)
    return dt.isoformat().replace("+00:00", "Z")

def fetch_events_date_range_by_tag_id(limit: int, offset: int, start_date_min: datetime, start_date_max: datetime, tag_id: int | None = None):
    url = POLYMARKET_BASE_URL + "/events"

    params = {
        "limit": limit,
        "offset": offset,
        "start_date_min": start_date_min,
        "start_date_max": start_date_max,
    }

    if tag_id is not None:
        params["tag_id"] = tag_id
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e}")
        return []
    return response.json()

def fetch_events_past_n_days_range_by_tag_id(limit: int, offset: int, num_days: int, tag_id: int | None = None):
    url = POLYMARKET_BASE_URL + "/events"
    now = datetime.datetime.now(datetime.timezone.utc)
    start_date_min = now - datetime.timedelta(days=num_days)

    params = {
        "limit": limit,
        "offset": offset,
        "start_date_min": to_rfc3339(start_date_min),
        "start_date_max": to_rfc3339(now),
    }

    if tag_id:
        params["tag_id"] = tag_id

    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        print("HTTP error:", e)
        print("Response body:", getattr(r, "text", None))
        print("Final URL:", getattr(r, "url", None))
        return []


def fetch_events_date_range_by_tag_slug(limit, offset, start_dt, end_dt, tag_slug=None):
    params = {
        "limit": limit,
        "offset": offset,
    }
    if tag_slug is not None:
        params["tag_slug"] = tag_slug

    r = requests.get(f"{POLYMARKET_BASE_URL}/events", params=params, timeout=30)
    print("Final URL:", r.url)
    print("Status:", r.status_code)
    if r.status_code != 200:
        print("Body:", r.text)
        r.raise_for_status()
    return r.json()

def fetch_events_by_event_slug(limit, offset, event_slug):
    params = {
        "limit": limit,
        "offset": offset,
        "slug": event_slug
    }
    r = requests.get(f"{POLYMARKET_BASE_URL}/events", params=params, timeout=30)
    print("Final URL:", r.url)
    print("Status:", r.status_code)
    
    return r.json()

def fetch_market_id_by_game_and_teams(game_date_str: str, home_team: str, away_team: str):
    away_team = away_team.lower()
    home_team = home_team.lower()
    formatted_date = f"{game_date_str[:4]}-{game_date_str[4:6]}-{game_date_str[6:8]}"
    slug = f"nfl-{home_team}-{away_team}-{formatted_date}"

    events = fetch_events_by_event_slug(5, 0, slug)
    event = events[0]
    markets = event["markets"]

    for market in markets:
        if "vs." in market.get("question", "") and market.get("sportsMarketType") == "moneyline":
            return market["id"]
    return None


if __name__ == "__main__":
    # Pick a *window*, not a single timestamp.
    # day = datetime.datetime(2025, 9, 14, tzinfo=datetime.timezone.utc)
    # start = day - datetime.timedelta(days=1)
    # end = day + datetime.timedelta(days=2)

    # # Try tag_slug first (often easier than tag_id)
    # events = fetch_events_date_range_by_tag_slug(200, 0, start, end, tag_slug="american-football")
    # print("Num events:", len(events))
    # print(events)
    # if events:
    #     print(events[0].get("title"), events[0].get("startDate") or events[0].get("start_date"))

    print(fetch_market_id_by_game_and_teams("20251227", "bal", "gb"))
    # json.dump(fetch_events_by_event_slug(1, 0, "nfl-bal-gb-2025-12-27"), open("event_test.json", "w"))
