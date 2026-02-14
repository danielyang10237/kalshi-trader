"""Series API methods"""

from typing import Any, Dict, Optional, List
import json
from pathlib import Path

DATA_CACHE_DIR = Path(__file__).parent.parent.parent / "data_cache"


class SeriesAPI:
    """API for series-related endpoints"""

    def __init__(self, client):
        self._client = client

    def get_all(self, limit: int = 100, cursor: Optional[str] = None, tags: Optional[List[str]] = None) -> Dict[str, Any]:
        """Get all series"""
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if tags:
            params["tags"] = ",".join(tags)
        return self._client._request("GET", "/series", params=params)

    def get(self, ticker: str) -> Dict[str, Any]:
        """Get a specific series by ticker"""
        return self._client._request("GET", f"/series/{ticker}")

    def get_tags_by_categories(self) -> Dict[str, Any]:
        """Get tags organized by series categories.

        Retrieve tags organized by series categories, which can be used
        for filtering and search functionality.

        Returns:
            A mapping of series categories to their associated tags.
        """
        output = self._client._request("GET", "/search/tags_by_categories")
        with open(DATA_CACHE_DIR / "series_tags.json", "w") as f:
            json.dump(output, f)
        return output

    def get_all_tags(
        self,
        categories_blacklist: List[str] = [
            "Climate and Weather",
            "Companies",
            "Crypto",
            "Economics",
            "Elections",
            "Entertainment",
            "Financials",
            "Mentions",
            "Politics",
            "Science and Technology",
            "Social",
            "Transportation",
        ],
    ) -> Dict[str, Any]:
        """Get all tags"""
        fetched_tags = json.load(open(DATA_CACHE_DIR / "series_tags.json"))
        data = fetched_tags["tags_by_categories"]
        all_tags = []
        for category in data:
            if category not in categories_blacklist:
                all_tags.extend(data[category])
        return all_tags
