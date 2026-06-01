from typing import Any, Dict, Optional


class EventsAPI:
    def __init__(self, client):
        self._client = client

    def get_all(
        self,
        series_ticker: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        with_nested_markets: bool = False,
    ) -> Dict[str, Any]:
        """Get events, optionally filtered by series and status"""
        params = {"limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        if with_nested_markets:
            params["with_nested_markets"] = "true"
        return self._client._request("GET", "/events", params=params)

    def get(self, event_ticker: str) -> Dict[str, Any]:
        """Get a specific event by ticker"""
        return self._client._request("GET", f"/events/{event_ticker}")

    def get_candlesticks(
        self,
        series_ticker: str,
        event_ticker: str,
        start_ts: int,
        end_ts: int,
        period_interval: int = 1,
        ticker: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get candlesticks for an event. Kalshi requires a `ticker` query param
        identifying which market within the event you want."""
        path = f"/series/{series_ticker}/events/{event_ticker}/candlesticks"
        params = {
            "start_ts": start_ts,
            "end_ts": end_ts,
            "period_interval": period_interval,
        }
        if ticker:
            params["ticker"] = ticker
        return self._client._request("GET", path, params=params)

