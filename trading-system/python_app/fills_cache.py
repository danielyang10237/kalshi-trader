"""Simple JSON file cache for trade fills, keyed by market ticker."""

import json
import os
import threading
from pathlib import Path
from typing import List, Optional

_CACHE_DIR = Path(__file__).parent / "data"
_CACHE_FILE = _CACHE_DIR / "fills.json"
_lock = threading.Lock()


def _read_cache() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        with open(_CACHE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _write_cache(data: dict):
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_FILE, "w") as f:
        json.dump(data, f)


def save_fill(fill: dict):
    """Append a fill to the cache, deduplicating by trade_id."""
    trade_id = fill.get("trade_id")
    ticker = fill.get("ticker") or fill.get("market_ticker") or ""
    if not trade_id or not ticker:
        return

    with _lock:
        cache = _read_cache()
        bucket = cache.setdefault(ticker, [])

        # deduplicate
        existing_ids = {f["trade_id"] for f in bucket}
        if trade_id in existing_ids:
            return

        bucket.append(fill)
        _write_cache(cache)


def get_fills(ticker: Optional[str] = None, limit: int = 100) -> List[dict]:
    """Return cached fills, optionally filtered by ticker. Most recent first."""
    with _lock:
        cache = _read_cache()

    if ticker:
        fills = cache.get(ticker, [])
    else:
        fills = [f for bucket in cache.values() for f in bucket]

    # sort newest first by ts or created_time
    def sort_key(f):
        return f.get("ts") or f.get("created_time") or ""

    fills.sort(key=sort_key, reverse=True)
    return fills[:limit]


def clear_fills():
    """Delete all cached fills."""
    with _lock:
        _write_cache({})
