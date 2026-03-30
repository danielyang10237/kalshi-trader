"""
Simulated account state: balance, positions, fills, order groups.
"""

import uuid
from typing import Any

from .orderbook import Fill, OrderEntry


class SimAccount:
    def __init__(self, initial_balance: int = 100_000):
        self.initial_balance = initial_balance
        self.balance: int = initial_balance  # cents
        self.positions: dict[str, int] = {}  # ticker -> net contracts (+ = long YES)
        self.fills: list[dict] = []
        self.resting_orders: dict[str, OrderEntry] = {}  # order_id -> OrderEntry
        self.order_groups: dict[str, dict] = {}  # group_id -> metadata
        self.all_orders: list[dict] = []  # log of all orders submitted

    def record_fill(self, fill: Fill):
        """Process a fill: update balance, position, and record it."""
        self.fills.append(fill.to_dict())

        ticker = fill.ticker
        if ticker not in self.positions:
            self.positions[ticker] = 0

        if fill.action == "buy":
            self.balance -= fill.yes_price * fill.count
            self.positions[ticker] += fill.count
        else:  # sell
            self.balance += fill.yes_price * fill.count
            self.positions[ticker] -= fill.count

    def add_resting_order(self, order: OrderEntry):
        self.resting_orders[order.order_id] = order

    def remove_resting_order(self, order_id: str) -> OrderEntry | None:
        return self.resting_orders.pop(order_id, None)

    def create_order_group(self, contracts_limit: int = 100000) -> str:
        group_id = str(uuid.uuid4())
        self.order_groups[group_id] = {
            "order_group_id": group_id,
            "contracts_limit": contracts_limit,
        }
        return group_id

    def delete_order_group(self, group_id: str) -> bool:
        return self.order_groups.pop(group_id, None) is not None

    def get_fills(self, ticker: str | None = None, limit: int = 100, cursor: str | None = None) -> dict:
        fills = self.fills
        if ticker:
            fills = [f for f in fills if f["ticker"] == ticker]
        fills = list(reversed(fills))[:limit]  # newest first
        return {"fills": fills, "cursor": ""}

    def get_balance(self) -> dict:
        portfolio_value = self.balance
        # Add unrealized value from positions
        # (simplified: we'd need current prices for real PnL)
        return {"balance": self.balance, "portfolio_value": portfolio_value}

    def get_positions(self, limit: int = 100, cursor: str | None = None) -> dict:
        positions = []
        for ticker, net in self.positions.items():
            if net != 0:
                positions.append({
                    "ticker": ticker,
                    "market_ticker": ticker,
                    "position": net,
                    "market_exposure": abs(net) * 50,  # rough estimate
                    "realized_pnl": 0,
                    "resting_orders_count": sum(
                        1 for o in self.resting_orders.values() if o.ticker == ticker
                    ),
                })
        return {"market_positions": positions[:limit], "cursor": ""}

    def get_resting_orders(
        self, ticker: str | None = None, status: str = "resting", limit: int = 200
    ) -> dict:
        orders = list(self.resting_orders.values())
        if ticker:
            orders = [o for o in orders if o.ticker == ticker]
        order_dicts = []
        for o in orders[:limit]:
            order_dicts.append(self._order_to_dict(o, "resting"))
        return {"orders": order_dicts, "cursor": ""}

    def log_order(self, order: OrderEntry, status: str, fills: list[dict] | None = None):
        """Log an order submission for the trade log."""
        self.all_orders.append({
            "order_id": order.order_id,
            "client_order_id": order.client_order_id,
            "ticker": order.ticker,
            "action": order.action,
            "price": order.price,
            "count": order.initial_count,
            "remaining": order.remaining,
            "status": status,
            "time_in_force": order.time_in_force,
            "order_type": order.order_type,
            "is_mm": order.is_mm,
            "created_at": order.created_at,
            "fills": fills or [],
        })

    def reset(self):
        """Reset account to initial state."""
        self.balance = self.initial_balance
        self.positions.clear()
        self.fills.clear()
        self.resting_orders.clear()
        self.order_groups.clear()
        self.all_orders.clear()

    @staticmethod
    def _order_to_dict(order: OrderEntry, status: str) -> dict:
        return {
            "order_id": order.order_id,
            "client_order_id": order.client_order_id,
            "ticker": order.ticker,
            "action": order.action,
            "side": "yes",
            "type": order.order_type,
            "yes_price": order.price,
            "no_price": 100 - order.price,
            "count": order.initial_count,
            "remaining_count": order.remaining,
            "status": status,
            "time_in_force": order.time_in_force,
            "order_group_id": order.order_group_id,
            "created_time": order.created_at,
        }
