"""NBA automated trader.

Computes edge between model theo and market best ask, applies risk checks,
and places limit buy orders (lifts the ask) when edge exceeds threshold.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from ..fills_cache import get_fills
from ..routes.trading import kalshi, get_or_create_order_group
from ..settings import settings

logger = logging.getLogger(__name__)


@dataclass
class TradingParams:
    min_edge: int = 3           # cents — minimum edge to trade
    max_position: int = 200     # max contracts in one direction
    max_exposure: int = 50000   # cents ($500)
    order_size: int = 10        # contracts per order
    edge_decay: Optional[float] = None  # not used for now
    wp_change_threshold: float = 0.005  # min home_wp change to trigger trade eval (edge mode)
    # delta-momentum strategy params
    strategy: str = "edge"                # "edge" or "delta"
    delta_threshold: float = 0.02         # min |Δp| between snapshots to trigger
    delta_min_interval_sec: float = 0.0   # rate-limit between delta trades (0 = none)
    enabled: bool = False

    def to_dict(self) -> dict:
        return {
            "min_edge": self.min_edge,
            "max_position": self.max_position,
            "max_exposure": self.max_exposure,
            "order_size": self.order_size,
            "edge_decay": self.edge_decay,
            "wp_change_threshold": self.wp_change_threshold,
            "strategy": self.strategy,
            "delta_threshold": self.delta_threshold,
            "delta_min_interval_sec": self.delta_min_interval_sec,
            "enabled": self.enabled,
        }


@dataclass
class TraderState:
    """Tracks live trading state for one game."""
    params: TradingParams = field(default_factory=TradingParams)

    # Best prices from orderbook (cents, 1-99)
    home_best_ask: Optional[int] = None  # best ask on home YES market
    away_best_ask: Optional[int] = None  # best ask on away YES market
    home_best_bid: Optional[int] = None  # best bid on home YES market
    away_best_bid: Optional[int] = None  # best bid on away YES market

    # Market tickers
    home_ticker: Optional[str] = None
    away_ticker: Optional[str] = None

    # Computed from fills
    home_position: int = 0   # net contracts (positive = long YES)
    away_position: int = 0
    home_cost: int = 0       # total cents spent
    away_cost: int = 0

    # Trade log
    trades: list = field(default_factory=list)
    last_trade_time: float = 0.0

    # Last wp that triggered a trade evaluation
    last_evaluated_wp: Optional[float] = None
    # delta-strategy state
    prev_wp: Optional[float] = None
    last_delta_trade_ts: float = 0.0

    def should_evaluate(self, home_wp: float) -> bool:
        """Return True if home_wp has changed enough to warrant a trade evaluation."""
        if self.last_evaluated_wp is None:
            return True
        return abs(home_wp - self.last_evaluated_wp) >= self.params.wp_change_threshold

    def total_exposure(self) -> int:
        """Total cents at risk across both sides."""
        return self.home_cost + self.away_cost

    def to_dict(self) -> dict:
        return {
            "params": self.params.to_dict(),
            "home_best_ask": self.home_best_ask,
            "away_best_ask": self.away_best_ask,
            "home_best_bid": self.home_best_bid,
            "away_best_bid": self.away_best_bid,
            "home_ticker": self.home_ticker,
            "away_ticker": self.away_ticker,
            "home_position": self.home_position,
            "away_position": self.away_position,
            "home_cost": self.home_cost,
            "away_cost": self.away_cost,
            "total_exposure": self.total_exposure(),
            "last_evaluated_wp": self.last_evaluated_wp,
            "recent_trades": self.trades[-10:],
        }


def compute_position_from_fills(ticker: str) -> tuple[int, int]:
    """Compute (net_position, total_cost) from cached fills for a ticker.

    Returns (net_contracts, cost_cents).
    Positive net = long YES, negative = short YES.
    """
    fills = get_fills(ticker=ticker, limit=10000)
    net = 0
    cost = 0
    for f in fills:
        count = f.get("count", 0)
        price = f.get("yes_price", 0)
        action = f.get("action", "")
        side = f.get("side", "yes")

        if action == "buy" and side == "yes":
            net += count
            cost += count * price
        elif action == "sell" and side == "yes":
            net -= count
            cost -= count * price
        elif action == "buy" and side == "no":
            net -= count
            cost += count * (100 - price)
        elif action == "sell" and side == "no":
            net += count
            cost -= count * (100 - price)

    return net, max(cost, 0)


def evaluate_and_trade(state: TraderState, home_wp: float) -> Optional[dict]:
    """Evaluate edge and place a trade if conditions are met.

    Called on every game state update when trading is enabled.

    Parameters
    ----------
    state : TraderState
        Current trading state with orderbook prices and positions.
    home_wp : float
        Model P(home_win) from GAM inference, in [0, 1].

    Returns
    -------
    dict or None
        Trade result if a trade was placed, None otherwise.
    """
    if not state.params.enabled:
        return None
    if home_wp is None:
        return None

    # Model prices in cents
    model_home = home_wp * 100   # theo for home YES
    model_away = (1 - home_wp) * 100  # theo for away YES

    # Evaluate all 4 possible trades and pick the best edge:
    #   1. Buy home YES  at home_best_ask  → edge = model_home - ask  (underpriced)
    #   2. Sell home YES at home_best_bid  → edge = bid - model_home  (overpriced)
    #   3. Buy away YES  at away_best_ask  → edge = model_away - ask
    #   4. Sell away YES at away_best_bid  → edge = bid - model_away
    # Note: "Buy home YES" ≡ "Sell away YES" economically, but different prices/tickers.
    candidates = []

    if state.home_best_ask is not None:
        edge = model_home - state.home_best_ask
        candidates.append(("BUY", "home", state.home_ticker, state.home_best_ask, edge, model_home))

    if state.home_best_bid is not None:
        edge = state.home_best_bid - model_home
        candidates.append(("SELL", "home", state.home_ticker, state.home_best_bid, edge, model_home))

    if state.away_best_ask is not None:
        edge = model_away - state.away_best_ask
        candidates.append(("BUY", "away", state.away_ticker, state.away_best_ask, edge, model_away))

    if state.away_best_bid is not None:
        edge = state.away_best_bid - model_away
        candidates.append(("SELL", "away", state.away_ticker, state.away_best_bid, edge, model_away))

    if not candidates:
        return None

    # Pick the candidate with the largest edge
    candidates.sort(key=lambda c: c[4], reverse=True)
    action, side, ticker, price, best_edge, model_price = candidates[0]

    if best_edge < state.params.min_edge:
        return None

    # Refresh positions for the chosen ticker, then delegate to the shared
    # post-selection pipeline (sizing, risk checks, paper/live order placement).
    if ticker:
        pos, cost = compute_position_from_fills(ticker)
        if side == "home":
            state.home_position = pos
            state.home_cost = cost
        else:
            state.away_position = pos
            state.away_cost = cost

    return _execute_order(state, action, side, ticker, price, model_price,
                          extras={"edge": round(best_edge, 1)})


def _execute_order(state: TraderState, action: str, side: str, ticker: str,
                   price: int, model_price: float,
                   extras: Optional[dict] = None) -> Optional[dict]:
    """Apply risk checks and place a paper/live order. Shared between strategies."""
    cur_pos = state.home_position if side == "home" else state.away_position
    if action == "BUY" and cur_pos >= state.params.max_position:
        return None
    if action == "SELL" and cur_pos <= -state.params.max_position:
        return None
    if state.total_exposure() >= state.params.max_exposure:
        return None

    size = min(state.params.order_size, state.params.max_position - abs(cur_pos))
    if size <= 0:
        return None

    trade_record = {
        "time": time.time(),
        "action": action,
        "side": side,
        "ticker": ticker,
        "price": price,
        "size": size,
        "model_price": round(model_price, 1),
        "paper": settings.paper_trade,
    }
    if extras:
        trade_record.update(extras)
    extras_str = " ".join(f"{k}={v}" for k, v in (extras or {}).items())

    if settings.paper_trade:
        trade_record["result"] = "PAPER"
        state.trades.append(trade_record)
        state.last_trade_time = time.time()
        logger.info(
            f"[trader][PAPER] {action} {side} YES {size}@{price}c | "
            f"model={model_price:.1f}c | ticker={ticker} | {extras_str}"
        )
        return trade_record

    try:
        order_group_id = get_or_create_order_group(ticker)
        if action == "BUY":
            result = kalshi.trades.buy_limit_order(
                ticker=ticker, count=size, price=price,
                order_group_id=order_group_id,
                time_in_force="immediate_or_cancel",
            )
        else:
            result = kalshi.trades.sell_limit_order(
                ticker=ticker, count=size, price=price,
                order_group_id=order_group_id,
                time_in_force="immediate_or_cancel",
            )
        trade_record["result"] = result
        state.trades.append(trade_record)
        state.last_trade_time = time.time()
        logger.info(
            f"[trader] ORDER: {action} {side} YES {size}@{price}c | "
            f"ticker={ticker} | {extras_str}"
        )
        return trade_record
    except Exception as e:
        logger.error(f"[trader] Order failed: {e}")
        return {"error": str(e), "action": action, "side": side, "ticker": ticker}


def evaluate_and_trade_delta(state: TraderState, home_wp: float) -> Optional[dict]:
    """Delta-momentum strategy.

    Tracks home_wp across consecutive snapshots; when |home_wp - prev_wp| exceeds
    delta_threshold, trade in the direction of the move (positive d → long home,
    negative d → long away). Position naturally closes/flips on reverse deltas
    because the new trade is in the opposite direction.
    """
    if not state.params.enabled or home_wp is None:
        return None

    if state.prev_wp is None:
        state.prev_wp = home_wp
        return None  # need two samples to compute a delta

    d = home_wp - state.prev_wp
    state.prev_wp = home_wp

    if abs(d) < state.params.delta_threshold:
        return None

    now = time.time()
    if (state.params.delta_min_interval_sec > 0
            and (now - state.last_delta_trade_ts) < state.params.delta_min_interval_sec):
        return None

    direction = 1 if d > 0 else -1
    model_home = home_wp * 100
    model_away = (1 - home_wp) * 100

    # Score candidates by effective entry cost in cents (lower = better).
    # SELL away_yes at bid Q is economically BUY home_yes at (100 - Q).
    candidates = []
    if direction == 1:
        if state.home_best_ask is not None and state.home_ticker:
            candidates.append(("BUY", "home", state.home_ticker,
                               state.home_best_ask, state.home_best_ask, model_home))
        if state.away_best_bid is not None and state.away_ticker:
            candidates.append(("SELL", "away", state.away_ticker,
                               state.away_best_bid, 100 - state.away_best_bid, model_home))
    else:
        if state.away_best_ask is not None and state.away_ticker:
            candidates.append(("BUY", "away", state.away_ticker,
                               state.away_best_ask, state.away_best_ask, model_away))
        if state.home_best_bid is not None and state.home_ticker:
            candidates.append(("SELL", "home", state.home_ticker,
                               state.home_best_bid, 100 - state.home_best_bid, model_away))

    if not candidates:
        return None

    candidates.sort(key=lambda c: c[4])  # cheapest entry first
    action, side, ticker, price, _eff, model_price = candidates[0]

    # Refresh positions before risk checks
    pos, cost = compute_position_from_fills(ticker)
    if side == "home":
        state.home_position = pos
        state.home_cost = cost
    else:
        state.away_position = pos
        state.away_cost = cost

    result = _execute_order(state, action, side, ticker, price, model_price,
                            extras={"delta": round(d, 4), "direction": direction})
    if result and "error" not in result:
        state.last_delta_trade_ts = now
    return result
