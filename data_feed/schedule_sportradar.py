import os
import sys
import json
import argparse
from pathlib import Path
from typing import Any, Dict

import requests
from dotenv import load_dotenv

# Load .env from project root (parent of data_feed/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def fetch_current_week_schedule(
    api_key: str,
    access_level: str = "trial",
    language_code: str = "en",
    timeout_s: int = 30,
) -> Dict[str, Any]:
    """
    Fetch NFL current week schedule from Sportradar.
    
    Args:
        api_key: Sportradar API key
        access_level: "trial" or "production"
        language_code: 2-letter language code (en, de, es, fr, etc.)
        timeout_s: Request timeout in seconds
    
    Returns:
        JSON response with schedule data
    """
    url = (
        f"https://api.sportradar.com/nfl/official/{access_level}/"
        f"v7/{language_code}/games/current_week/schedule.json"
    )
    
    headers = {
        "accept": "application/json",
        "x-api-key": api_key,
    }
    
    r = requests.get(url, headers=headers, timeout=timeout_s)
    print("full url:", r.url)
    r.raise_for_status()
    return r.json()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch NFL current week schedule from Sportradar"
    )
    parser.add_argument(
        "--access-level",
        type=str,
        choices=["trial", "production"],
        default="trial",
        help="API access level (default: trial)",
    )
    parser.add_argument(
        "--language",
        type=str,
        default="en",
        help="Language code (default: en)",
    )
    args = parser.parse_args()

    api_key = os.getenv("SPORT_RADAR_API_KEY")

    if not api_key:
        raise RuntimeError(
            "Missing SPORT_RADAR_API_KEY in your .env or environment."
        )

    # Debug: show masked API key to verify it loaded
    print(f"API key loaded: {api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "API key too short")
    print(f"Access level: {args.access_level}")
    print(f"Language: {args.language}")

    data = fetch_current_week_schedule(
        api_key,
        access_level=args.access_level,
        language_code=args.language,
    )

    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(
            f"HTTP error: {e}\nResponse text:\n{getattr(e.response, 'text', '')}",
            file=sys.stderr,
        )
        raise

