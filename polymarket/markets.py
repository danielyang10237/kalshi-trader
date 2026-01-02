# market_by_id.py
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

import dotenv
import requests

dotenv.load_dotenv()

BASE_URL = "https://gamma-api.polymarket.com"

# Docs route: GET /polymarket/candlesticks/{condition_id}
# So the full URL is:
#   https://api.domeapi.io/v1/polymarket/candlesticks/{condition_id}
DOME_API_BASE = "https://api.domeapi.io/v1"
CANDLESTICKS_BASE = f"{DOME_API_BASE}/polymarket"

# Defaults requested by you (UTC):
# 2025-12-27 9am UTC
# 2025-12-28 10pm UTC
DEFAULT_START_DT_UTC = datetime(2025, 12, 27, 9, 0, 0, tzinfo=timezone.utc)
DEFAULT_END_DT_UTC = datetime(2025, 12, 28, 22, 0, 0, tzinfo=timezone.utc)
DEFAULT_INTERVAL_MINUTES = 1  # interval=1 -> 1 minute


def _to_unix_seconds(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware (expected UTC).")
    return int(dt.timestamp())


def get_market_by_id(
    market_id: Union[int, str],
    include_tag: bool = False,
    timeout_s: float = 15.0,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    s = session or requests.Session()

    url = f"{BASE_URL}/markets/{market_id}"
    params = {"include_tag": "true"} if include_tag else None

    try:
        resp = s.get(url, params=params, timeout=timeout_s)
    except requests.RequestException as e:
        raise ValueError(f"Request failed: {e}") from e

    if not resp.ok:
        body = (resp.text or "").strip()
        if len(body) > 800:
            body = body[:800] + "…"
        raise ValueError(f"HTTP {resp.status_code} from {url}: {body}")

    try:
        data = resp.json()
    except ValueError as e:
        raise ValueError(f"Non-JSON response from {url}") from e

    if not isinstance(data, dict):
        raise ValueError(f"Unexpected payload type from {url}: {type(data).__name__}")

    return data


def get_candlesticks_from_market(
    output_file: str,
    market: Dict[str, Any],
    *,
    start_dt_utc: datetime = DEFAULT_START_DT_UTC,
    end_dt_utc: datetime = DEFAULT_END_DT_UTC,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
    timeout_s: float = 30.0,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    if "conditionId" not in market:
        raise ValueError("market missing required field 'conditionId'")

    condition_id = market["conditionId"]
    if not isinstance(condition_id, str) or not condition_id:
        raise ValueError(f"Invalid conditionId: {condition_id!r}")

    # Support a few env var names (your current one + more standard options)
    api_key = (
        os.getenv("dome-io-api-key")
        or os.getenv("DOME_IO_API_KEY")
        or os.getenv("DOME_API_KEY")
    )
    if not api_key:
        raise ValueError(
            "Dome API key not found. Set one of: dome-io-api-key, DOME_IO_API_KEY, DOME_API_KEY"
        )

    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be > 0")

    start_time = _to_unix_seconds(start_dt_utc)
    end_time = _to_unix_seconds(end_dt_utc)
    if end_time <= start_time:
        raise ValueError("end_dt_utc must be after start_dt_utc")

    s = session or requests.Session()

    # Correct endpoint from docs:
    # GET https://api.domeapi.io/v1/polymarket/candlesticks/{condition_id}
    url = f"{CANDLESTICKS_BASE}/candlesticks/{condition_id}"
    params = {
        "start_time": start_time,
        "end_time": end_time,
        "interval": interval_minutes,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }

    try:
        resp = s.get(url, params=params, headers=headers, timeout=timeout_s)
    except requests.RequestException as e:
        raise ValueError(f"Request failed: {e}") from e

    if not resp.ok:
        body = (resp.text or "").strip()
        if len(body) > 2000:
            body = body[:2000] + "…"
        raise ValueError(f"HTTP {resp.status_code} from {url}: {body}")

    try:
        data = resp.json()
    except ValueError as e:
        raise ValueError(f"Non-JSON response from {url}") from e

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return data


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python market_by_id.py <market_id> <output_file>")
        raise SystemExit(2)

    mid = sys.argv[1]
    output_file = sys.argv[2]

    market = get_market_by_id(mid, include_tag=False)
    candlesticks = get_candlesticks_from_market(output_file, market)

    print(f"Saved candlesticks to: {output_file}")
    if isinstance(candlesticks, dict):
        keys = ", ".join(list(candlesticks.keys())[:10])
        print(f"Response keys: {keys}")