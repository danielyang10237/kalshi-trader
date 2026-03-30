"""
Core matching engine for a single market's orderbook.

Each market ticker gets its own independent Orderbook instance.

Kalshi convention for snapshots/deltas:
  "yes" field = YES buy orders (bids) at YES prices → shown green
  "no"  field = NO buy orders at NO prices (= YES sells) → shown red
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class OrderEntry:
    order_id: str
    client_order_id: str
    ticker: str
    action: str  # "buy" or "sell"
    price: int  # 1-99 cents (YES price)
    remaining: int
    initial_count: int
    time_in_force: str
    order_group_id: str | None = None
    created_at: float = field(default_factory=time.time)
    is_mm: bool = False
    order_type: str = "limit"


@dataclass
class Fill:
    trade_id: str
    ticker: str
    action: str
    side: str  # always "yes"
    count: int
    yes_price: int
    ts: float

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "ticker": self.ticker,
            "action": self.action,
            "side": self.side,
            "count": self.count,
            "yes_price": self.yes_price,
            "ts": self.ts,
            "created_time": self.ts,
        }


@dataclass
class Trade:
    trade_id: str
    ticker: str
    yes_price: int
    count: int
    taker_side: str
    ts: float

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "ticker": self.ticker,
            "yes_price": self.yes_price,
            "count": self.count,
            "taker_side": self.taker_side,
            "ts": self.ts,
        }


@dataclass
class Delta:
    market_ticker: str
    side: str  # "yes" (bids) or "no" (asks)
    price: int
    delta: int


class MatchResult:
    def __init__(self):
        self.fills: list[Fill] = []
        self.trades: list[Trade] = []
        self.deltas: list[Delta] = []
        self.resting_order: OrderEntry | None = None
        self.status: str = "open"
        self.remaining: int = 0


class Orderbook:
    """
    Limit order book for a single YES market.
    Bids = buy YES, Asks = sell YES.
    """

    def __init__(self, ticker: str):
        self.ticker = ticker
        self.bids: list[OrderEntry] = []  # sorted: price desc, time asc
        self.asks: list[OrderEntry] = []  # sorted: price asc, time asc
        self.seq: int = 0
        self._on_delta: Callable[[list[Delta]], None] | None = None
        self._on_fill: Callable[[Fill], None] | None = None
        self._on_trade: Callable[[Trade], None] | None = None

    def set_callbacks(self, on_delta=None, on_fill=None, on_trade=None):
        self._on_delta = on_delta
        self._on_fill = on_fill
        self._on_trade = on_trade

    def _insert_bid(self, order: OrderEntry):
        i = 0
        while i < len(self.bids):
            if order.price > self.bids[i].price:
                break
            if order.price == self.bids[i].price and order.created_at < self.bids[i].created_at:
                break
            i += 1
        self.bids.insert(i, order)

    def _insert_ask(self, order: OrderEntry):
        i = 0
        while i < len(self.asks):
            if order.price < self.asks[i].price:
                break
            if order.price == self.asks[i].price and order.created_at < self.asks[i].created_at:
                break
            i += 1
        self.asks.insert(i, order)

    def submit_order(
        self,
        action: str,
        price: int,
        count: int,
        time_in_force: str = "good_till_canceled",
        order_type: str = "limit",
        client_order_id: str | None = None,
        order_group_id: str | None = None,
        is_mm: bool = False,
    ) -> MatchResult:
        result = MatchResult()
        result.remaining = count

        order = OrderEntry(
            order_id=str(uuid.uuid4()),
            client_order_id=client_order_id or str(uuid.uuid4()),
            ticker=self.ticker,
            action=action,
            price=price,
            remaining=count,
            initial_count=count,
            time_in_force=time_in_force,
            order_group_id=order_group_id,
            is_mm=is_mm,
            order_type=order_type,
        )

        all_deltas: list[Delta] = []

        if action == "buy":
            all_deltas = self._match_buy(order, result)
        else:
            all_deltas = self._match_sell(order, result)

        # Handle unfilled remainder
        if order.remaining > 0:
            if time_in_force == "good_till_canceled":
                if action == "buy":
                    self._insert_bid(order)
                    # Bids go in "yes" at YES price
                    all_deltas.append(Delta(self.ticker, "yes", price, order.remaining))
                else:
                    self._insert_ask(order)
                    # Asks go in "no" at NO price (100 - ask_price)
                    all_deltas.append(Delta(self.ticker, "no", 100 - price, order.remaining))
                result.resting_order = order
                result.status = "resting"
            else:
                result.status = "canceled" if not result.fills else "filled"

        if result.fills and not result.resting_order:
            result.status = "filled"

        result.remaining = order.remaining
        result.deltas = all_deltas

        if all_deltas and self._on_delta:
            self._on_delta(all_deltas)
        for f in result.fills:
            if self._on_fill:
                self._on_fill(f)
        for t in result.trades:
            if self._on_trade:
                self._on_trade(t)

        return result

    def _match_buy(self, order: OrderEntry, result: MatchResult) -> list[Delta]:
        """Match a buy against resting asks. Consuming asks = remove from 'no' side."""
        deltas: list[Delta] = []
        i = 0
        while i < len(self.asks) and order.remaining > 0:
            ask = self.asks[i]
            if ask.price > order.price:
                break
            fill_price = ask.price
            fill_count = min(order.remaining, ask.remaining)
            ts = time.time()
            trade_id = str(uuid.uuid4())

            result.fills.append(Fill(trade_id, self.ticker, "buy", "yes", fill_count, fill_price, ts))
            result.trades.append(Trade(trade_id, self.ticker, fill_price, fill_count, "yes", ts))
            # Consuming an ask = removing from "no" side
            deltas.append(Delta(self.ticker, "no", 100 - fill_price, -fill_count))

            order.remaining -= fill_count
            ask.remaining -= fill_count
            if ask.remaining == 0:
                self.asks.pop(i)
            else:
                i += 1
        return deltas

    def _match_sell(self, order: OrderEntry, result: MatchResult) -> list[Delta]:
        """Match a sell against resting bids. Consuming bids = remove from 'yes' side."""
        deltas: list[Delta] = []
        i = 0
        while i < len(self.bids) and order.remaining > 0:
            bid = self.bids[i]
            if bid.price < order.price:
                break
            fill_price = bid.price
            fill_count = min(order.remaining, bid.remaining)
            ts = time.time()
            trade_id = str(uuid.uuid4())

            result.fills.append(Fill(trade_id, self.ticker, "sell", "yes", fill_count, fill_price, ts))
            result.trades.append(Trade(trade_id, self.ticker, fill_price, fill_count, "yes", ts))
            # Consuming a bid = removing from "yes" side
            deltas.append(Delta(self.ticker, "yes", fill_price, -fill_count))

            order.remaining -= fill_count
            bid.remaining -= fill_count
            if bid.remaining == 0:
                self.bids.pop(i)
            else:
                i += 1
        return deltas

    def cancel_order(self, order_id: str) -> list[Delta]:
        deltas: list[Delta] = []
        for i, order in enumerate(self.bids):
            if order.order_id == order_id:
                deltas.append(Delta(self.ticker, "yes", order.price, -order.remaining))
                self.bids.pop(i)
                if self._on_delta:
                    self._on_delta(deltas)
                return deltas
        for i, order in enumerate(self.asks):
            if order.order_id == order_id:
                deltas.append(Delta(self.ticker, "no", 100 - order.price, -order.remaining))
                self.asks.pop(i)
                if self._on_delta:
                    self._on_delta(deltas)
                return deltas
        return deltas

    def cancel_orders_by_group(self, order_group_id: str) -> list[Delta]:
        deltas: list[Delta] = []
        self.bids = [o for o in self.bids if o.order_group_id != order_group_id or (
            deltas.append(Delta(self.ticker, "yes", o.price, -o.remaining)) and False
        )]
        self.asks = [o for o in self.asks if o.order_group_id != order_group_id or (
            deltas.append(Delta(self.ticker, "no", 100 - o.price, -o.remaining)) and False
        )]
        if deltas and self._on_delta:
            self._on_delta(deltas)
        return deltas

    def clear_mm_orders(self) -> list[Delta]:
        deltas: list[Delta] = []
        new_bids = []
        for o in self.bids:
            if o.is_mm:
                deltas.append(Delta(self.ticker, "yes", o.price, -o.remaining))
            else:
                new_bids.append(o)
        self.bids = new_bids

        new_asks = []
        for o in self.asks:
            if o.is_mm:
                deltas.append(Delta(self.ticker, "no", 100 - o.price, -o.remaining))
            else:
                new_asks.append(o)
        self.asks = new_asks

        if deltas and self._on_delta:
            self._on_delta(deltas)
        return deltas

    def get_snapshot(self) -> dict:
        """
        Snapshot in Kalshi convention:
          "yes" = YES buy orders (bids) at YES prices → green in UI
          "no"  = NO buy orders (asks) at NO prices (100 - ask_price) → red in UI
        """
        bid_levels: dict[int, int] = {}
        for o in self.bids:
            bid_levels[o.price] = bid_levels.get(o.price, 0) + o.remaining
        ask_levels: dict[int, int] = {}
        for o in self.asks:
            ask_levels[o.price] = ask_levels.get(o.price, 0) + o.remaining

        # yes = bids at YES price
        yes_levels = sorted([[p, s] for p, s in bid_levels.items()], key=lambda x: x[0], reverse=True)
        # no = asks converted to NO price
        no_levels = sorted([[100 - p, s] for p, s in ask_levels.items()], key=lambda x: x[0], reverse=True)
        return {"yes": yes_levels, "no": no_levels, "market_ticker": self.ticker}

    def get_best_ask(self) -> int | None:
        return self.asks[0].price if self.asks else None

    def get_best_bid(self) -> int | None:
        return self.bids[0].price if self.bids else None

    def get_resting_orders(self, is_mm: bool | None = None) -> list[OrderEntry]:
        orders = self.bids + self.asks
        if is_mm is not None:
            orders = [o for o in orders if o.is_mm == is_mm]
        return orders
