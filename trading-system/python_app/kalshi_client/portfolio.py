"""Portfolio/Inventory API methods"""

from typing import Any, Dict, Optional


class PortfolioAPI:
    """API for portfolio/inventory-related endpoints"""

    def __init__(self, client):
        self._client = client

    def get_balance(self) -> Dict[str, Any]:
        """Get account balance"""
        return self._client._request("GET", "/portfolio/balance")

    def get_positions(
        self,
        limit: int = 100,
        cursor: Optional[str] = None,
        settlement_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get user's positions"""
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if settlement_status:
            params["settlement_status"] = settlement_status
        return self._client._request("GET", "/portfolio/positions", params=params)

