"""Markets API methods"""

from typing import Any, Dict, Optional


class MarketsAPI:
    """API for market-related endpoints"""

    def __init__(self, client):
        self._client = client

    def get_all(
        self,
        series_ticker: Optional[str] = None,
        event_ticker: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get markets, optionally filtered"""
        params = {"limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        return self._client._request("GET", "/markets", params=params)

    def get(self, ticker: str) -> Dict[str, Any]:
        """Get a specific market by ticker"""
        return self._client._request("GET", f"/markets/{ticker}")

    def get_by_event(
        self,
        event_ticker: str,
        status: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get all markets for a specific event"""
        params = {"limit": limit, "event_ticker": event_ticker}
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        return self._client._request("GET", "/markets", params=params)

