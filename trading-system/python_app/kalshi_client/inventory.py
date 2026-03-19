"""Inventory/Portfolio API methods"""

from typing import Any, Dict


class InventoryAPI:
    """API for portfolio balance and positions"""

    def __init__(self, client):
        self._client = client

    def get_balance(self) -> Dict[str, Any]:
        """
        Get balance and portfolio value.
        
        Returns:
            balance: Available balance in cents
            portfolio_value: Current value of all positions in cents
            updated_ts: Unix timestamp of last update
        """
        return self._client._request("GET", "/portfolio/balance")

