"""Trades API methods"""

import uuid
from typing import Any, Dict, Literal, Optional


class TradesAPI:
    """API for trade-related endpoints"""

    def __init__(self, client):
        self._client = client

    def get_fills(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get user's fills/trades"""
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if cursor:
            params["cursor"] = cursor
        return self._client._request("GET", "/portfolio/fills", params=params)

    def get_orders_by_market_ticker(
        self,
        ticker: str,
        limit: int = 100,
        cursor: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get user's resting orders for a specific market"""
        params: Dict[str, Any] = {"limit": limit}
        params["ticker"] = ticker
        params["status"] = "resting"
        if cursor:
            params["cursor"] = cursor
        return self._client._request("GET", "/portfolio/orders", params=params)
    
    def get_all_orders_by_market_ticker(
        self,
        ticker: str
    ) -> list:
        """Get ALL resting orders for a market (paginates through all pages)"""
        all_orders = []
        cursor = None
        
        while True:
            response = self.get_orders_by_market_ticker(
                ticker=ticker,
                limit=200,  # max per request
                cursor=cursor
            )
            orders = response.get("orders", [])
            all_orders.extend(orders)
            
            cursor = response.get("cursor")
            if not cursor:
                break
        
        return all_orders

    def get_orders_by_event_ticker(
        self,
        event_ticker: str,
        limit: int = 100,
        cursor: Optional[str] = None
    ) -> list:
        """Get user's resting orders for a specific market"""
        params: Dict[str, Any] = {"limit": limit}
        params["event_ticker"] = event_ticker
        params["status"] = "resting"
        if cursor:
            params["cursor"] = cursor
        return self._client._request("GET", "/portfolio/orders", params=params)
    
    def get_all_orders_by_event_ticker(
        self,
        event_ticker: str
    ) -> list:
        """Get ALL resting orders for an event (paginates through all pages)"""
        all_orders = []
        cursor = None
        
        while True:
            response = self.get_orders_by_event_ticker(
                event_ticker=event_ticker,
                limit=200,  # max per request
                cursor=cursor
            )
            orders = response.get("orders", [])
            all_orders.extend(orders)
            
            cursor = response.get("cursor")
            if not cursor:
                break
        
        return all_orders

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an order by ID. Returns the zeroed order."""
        return self._client._request("DELETE", f"/portfolio/orders/{order_id}")
    
    def cancel_order_batch(self, order_ids: list) -> Dict[str, Any]:
        """Cancel a batch of orders by ID. Returns the zeroed orders."""
        return self._client._request("DELETE", "/portfolio/orders/batch", json_body={"ids": order_ids})

    def get_orders(
        self,
        limit: int = 200,
        cursor: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get user's resting orders (single page)"""
        params: Dict[str, Any] = {"limit": limit, "status": "resting"}
        if cursor:
            params["cursor"] = cursor
        return self._client._request("GET", "/portfolio/orders", params=params)

    def get_all_orders(self) -> list:
        """Get ALL resting orders across all markets (paginates through all pages)"""
        all_orders = []
        cursor = None
        
        while True:
            response = self.get_orders(limit=200, cursor=cursor)
            orders = response.get("orders", [])
            all_orders.extend(orders)
            
            cursor = response.get("cursor")
            if not cursor:
                break
        
        return all_orders

    def cancel_all_orders(self) -> list:
        """Cancel ALL resting orders. Returns list of cancelled orders."""
        all_orders = self.get_all_orders()
        order_ids = [order["order_id"] for order in all_orders]
        
        if not order_ids:
            return []
        
        # Use batch cancel
        return self.cancel_order_batch(order_ids)

    def get_order_groups(self) -> Dict[str, Any]:
        """Get all order groups for the authenticated user"""
        return self._client._request("GET", "/portfolio/order_groups")

    def create_order_group(self, contracts_limit: int) -> Dict[str, Any]:
        """Create a new order group with a contracts limit"""
        return self._client._request(
            "POST",
            "/portfolio/order_groups/create",
            json_body={"contracts_limit": contracts_limit}
        )

    def delete_order_group(self, order_group_id: str) -> Dict[str, Any]:
        """Delete an order group and cancel all orders within it"""
        return self._client._request("DELETE", f"/portfolio/order_groups/{order_group_id}")

    def buy_limit_order(
        self,
        ticker: str,
        count: int,
        price: int,
        client_order_id: Optional[str] = None,
        order_group_id: Optional[str] = None,
        time_in_force: Literal["fill_or_kill", "good_till_canceled", "immediate_or_cancel"] = "good_till_canceled",
        post_only: Optional[bool] = None,
        reduce_only: Optional[bool] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "ticker": ticker,
            "side": "yes",
            "action": "buy",
            "count": count,
            "type": "limit",
        }
        body["yes_price"] = price
        body["client_order_id"] = client_order_id or str(uuid.uuid4())
        if order_group_id:
            body["order_group_id"] = order_group_id
        # reduce_only requires immediate_or_cancel
        if reduce_only:
            body["time_in_force"] = "immediate_or_cancel"
            body["reduce_only"] = True
        else:
            body["time_in_force"] = time_in_force
        if post_only is not None:
            body["post_only"] = post_only
        body["self_trade_prevention_type"] = "maker"
        body["cancel_order_on_pause"] = True
        
        return self._client._request("POST", "/portfolio/orders", json_body=body)

    def sell_limit_order(
        self,
        ticker: str,
        count: int,
        price: int,
        client_order_id: Optional[str] = None,
        order_group_id: Optional[str] = None,
        time_in_force: Literal["fill_or_kill", "good_till_canceled", "immediate_or_cancel"] = "good_till_canceled",
        post_only: Optional[bool] = None,
        reduce_only: Optional[bool] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "ticker": ticker,
            "side": "yes",
            "action": "sell",
            "count": count,
            "type": "limit",
        }
        body["yes_price"] = price
        body["client_order_id"] = client_order_id or str(uuid.uuid4())
        if order_group_id:
            body["order_group_id"] = order_group_id
        # reduce_only requires immediate_or_cancel
        if reduce_only:
            body["time_in_force"] = "immediate_or_cancel"
            body["reduce_only"] = True
        else:
            body["time_in_force"] = time_in_force
        if post_only is not None:
            body["post_only"] = post_only
        body["self_trade_prevention_type"] = "maker"
        body["cancel_order_on_pause"] = True
        
        return self._client._request("POST", "/portfolio/orders", json_body=body)

    def buy_market_order(
        self,
        ticker: str,
        count: int,
        client_order_id: Optional[str] = None,
        order_group_id: Optional[str] = None,
        reduce_only: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Market buy - uses yes_price=99 as max price cap"""
        body: Dict[str, Any] = {
            "ticker": ticker,
            "side": "yes",
            "action": "buy",
            "count": count,
            "type": "market",
            "yes_price": 99,  # Max price cap for protection
        }
        
        body["client_order_id"] = client_order_id or str(uuid.uuid4())
        if order_group_id:
            body["order_group_id"] = order_group_id
        if reduce_only is not None:
            body["reduce_only"] = reduce_only
        
        return self._client._request("POST", "/portfolio/orders", json_body=body)

    def sell_market_order(
        self,
        ticker: str,
        count: int,
        client_order_id: Optional[str] = None,
        order_group_id: Optional[str] = None,
        reduce_only: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Market sell - uses yes_price=1 as min price floor"""
        body: Dict[str, Any] = {
            "ticker": ticker,
            "side": "yes",
            "action": "sell",
            "count": count,
            "type": "market",
            "yes_price": 1,  # Min price floor for protection
        }
        body["client_order_id"] = client_order_id or str(uuid.uuid4())
        if order_group_id:
            body["order_group_id"] = order_group_id
        if reduce_only is not None:
            body["reduce_only"] = reduce_only
        return self._client._request("POST", "/portfolio/orders", json_body=body)