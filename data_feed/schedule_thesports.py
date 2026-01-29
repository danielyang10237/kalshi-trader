import os
import sys
import argparse
from datetime import datetime
from typing import Any, Dict
import json
import requests
from dotenv import load_dotenv


BASE_URL = "https://api.thesports.com/v1/american_football/match/diary"


def fetch_schedule(
    user: str,
    secret: str,
    tsp: int,
    timeout_s: int = 30,
) -> Dict[str, Any]:
    params = {"user": user, "secret": secret, "tsp": tsp}
    r = requests.get(BASE_URL, params=params, timeout=timeout_s)

    print("full url:", r.url)
    r.raise_for_status()
    return r.json()


def parse_date_to_timestamp(date_str: str) -> int:
    """Convert MMDDYYYY date string to Unix timestamp."""
    dt = datetime.strptime(date_str, "%m%d%Y")
    return int(dt.timestamp())


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch schedule for a given date")
    parser.add_argument(
        "date",
        type=str,
        help="Date in MMDDYYYY format (e.g., 01162026)",
    )
    args = parser.parse_args()

    load_dotenv()

    user = os.getenv("THE_SPORTS_ACCESS_USER")
    secret = os.getenv("THE_SPORTS_ACCESS_SECRET")

    print("user:", user)
    print("secret:", secret)

    if not user or not secret:
        raise RuntimeError(
            "Missing THE_SPORTS_ACCESS_USER / THE_SPORTS_ACCESS_SECRET in your .env or environment."
        )

    tsp = parse_date_to_timestamp(args.date)
    print(f"Date {args.date} -> timestamp {tsp}")

    data = fetch_schedule(user, secret, tsp)

    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"HTTP error: {e}\nResponse text:\n{getattr(e.response, 'text', '')}", file=sys.stderr)
        raise

