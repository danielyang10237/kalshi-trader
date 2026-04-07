"""NBA delta-based directional trader.

Captures latency edge by trading on model probability DELTAS rather than levels.
On each game event: compute how much P(home_win) changed, predict the market's
repricing direction, and place a one-sided marketable limit order before the
Kalshi book catches up.
"""

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

from ..fills_cache import get_fills
from ..settings import settings
from ..settings import settings

logger = logging.getLogger(__name__)


# ── Parameters ───────────────────────────────────────────────────────────────

@dataclass
class TradingParams:
    min_size: int = 5             # minimum contracts per order
    max_size: int = 50            # absolute cap per order
    max_position: int = 200       # max net contracts in one direction
    max_exposure: int = 50_000    # cents ($500)
    fee_rate: float = 0.07        # Kalshi fee rate
    # Delta-specific
    delta_scale: float = 0.6      # model_delta → expected_move multiplier
    min_delta: float = 0.03       # minimum |model_delta| to act on
    delta_full_scale: float = 0.08  # |model_delta| at which to trade max size
    aggression: int = 0           # cents past bid/ask for ENTRY (0=at, 1=through)
    exit_offset: int = 0          # cents inside fair for EXIT (0=at fair, 1=1c inside, -1=1c outside)
    enabled: bool = False

    def to_dict(self) -> dict:
        return {
            "min_size": self.min_size,
            "max_size": self.max_size,
            "max_position": self.max_position,
            "max_exposure": self.max_exposure,
            "fee_rate": self.fee_rate,
            "delta_scale": self.delta_scale,
            "min_delta": self.min_delta,
            "delta_full_scale": self.delta_full_scale,
            "aggression": self.aggression,
            "exit_offset": self.exit_offset,
            "enabled": self.enabled,
        }


# ── State ────────────────────────────────────────────────────────────────────

@dataclass
class TraderState:
    """Tracks live trading state for one game."""
    params: TradingParams = field(default_factory=TradingParams)

    # Best prices from orderbook (cents, 1-99)
    home_best_bid: Optional[int] = None
    home_best_ask: Optional[int] = None
    away_best_bid: Optional[int] = None
    away_best_ask: Optional[int] = None

    # Market tickers
    home_ticker: Optional[str] = None
    away_ticker: Optional[str] = None

    # Position (computed from fills)
    home_position: int = 0
    home_cost: int = 0

    # Posterior tracking (for dashboard + delta computation)
    last_theo: Optional[int] = None
    last_p_kalshi: Optional[float] = None
    last_p_computed: Optional[float] = None
    prev_p_kalshi: Optional[float] = None   # previous snapshot's Kalshi posterior

    # Delta tracking
    last_model_delta: Optional[float] = None
    last_expected_move: Optional[float] = None
    last_direction: Optional[str] = None     # "BUY", "SELL", or "SKIP"
    last_order_price: Optional[int] = None
    last_size: Optional[int] = None
    last_fair: Optional[float] = None        # kalshi_pre + expected_move*100

    # Exit order tracking
    last_exit_price: Optional[int] = None

    # Trade log
    trades: list = field(default_factory=list)
    last_trade_time: float = 0.0

    def total_exposure(self) -> int:
        return self.home_cost

    def to_dict(self) -> dict:
        return {
            "params": self.params.to_dict(),
            "home_best_bid": self.home_best_bid,
            "home_best_ask": self.home_best_ask,
            "away_best_bid": self.away_best_bid,
            "away_best_ask": self.away_best_ask,
            "home_ticker": self.home_ticker,
            "away_ticker": self.away_ticker,
            "home_position": self.home_position,
            "home_cost": self.home_cost,
            "total_exposure": self.total_exposure(),
            "last_theo": self.last_theo,
            "last_p_kalshi": self.last_p_kalshi,
            "last_p_computed": self.last_p_computed,
            "prev_p_kalshi": self.prev_p_kalshi,
            "last_model_delta": self.last_model_delta,
            "last_expected_move": self.last_expected_move,
            "last_direction": self.last_direction,
            "last_order_price": self.last_order_price,
            "last_size": self.last_size,
            "last_fair": self.last_fair,
            "last_exit_price": self.last_exit_price,
            "recent_trades": self.trades[-10:],
        }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_or_create_group(kalshi_client, group_key: str, market_order_groups: dict) -> str:
    """Get or create an order group by arbitrary key."""
    if group_key in market_order_groups:
        return market_order_groups[group_key]
    result = kalshi_client.trades.create_order_group(contracts_limit=100000)
    gid = result.get("order_group_id") or result.get("order_group", {}).get("order_group_id")
    if not gid:
        raise Exception(f"Failed to create order group: {result}")
    market_order_groups[group_key] = gid
    return gid


def compute_position_from_fills(ticker: str) -> tuple[int, int]:
    """Compute (net_position, total_cost) from fills.

    In sim mode, queries the sim exchange REST API directly (fills WS
    may not be connected). Falls back to local fills cache.
    """
    if settings.sim_mode:
        try:
            import requests
            resp = requests.get(
                f"{settings.kalshi_ws_url.replace('ws://', 'http://').replace('/trade-api/ws/v2', '')}/sim/account",
                timeout=1,
            )
            if resp.ok:
                data = resp.json()
                pos = data.get("positions", {}).get(ticker, 0)
                # Compute cost from fills
                cost = 0
                for f in data.get("fills", []):
                    if f.get("ticker") != ticker:
                        continue
                    c = f.get("count", 0)
                    p = f.get("yes_price", 0)
                    if f.get("action") == "buy":
                        cost += c * p
                    else:
                        cost -= c * p
                return pos, max(cost, 0)
        except Exception as e:
            logger.warning(f"[sim] Failed to fetch position from sim: {e}")

    # Fallback: local fills cache
    fills = get_fills(ticker=ticker, limit=10_000)
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


def compute_fee_per_contract_cents(p: float, fee_rate: float = 0.07) -> float:
    """Per-contract Kalshi fee in cents at probability p."""
    fee_dollars = math.ceil(fee_rate * p * (1 - p) * 100) / 100
    return fee_dollars * 100


def compute_delta_size(model_delta: float, params: TradingParams) -> int:
    """Size based on delta magnitude.

    Larger |model_delta| = more contracts. Linear scale from min_size to max_size.
    """
    abs_delta = abs(model_delta)
    scale = min(abs_delta / params.delta_full_scale, 1.0)
    size = round(params.min_size + (params.max_size - params.min_size) * scale)
    return max(params.min_size, min(params.max_size, size))


# ── Core: cancel and trade on delta ──────────────────────────────────────────

def cancel_and_requote(
    state: TraderState,
    p_kalshi: float,
    p_computed: float,
    model_delta: float,
    kalshi_pre: float,
) -> Optional[dict]:
    """Cancel all resting orders and place a directional order based on model delta.

    Parameters
    ----------
    state : TraderState
    p_kalshi : float        Current Kalshi-anchored posterior P(home_win).
    p_computed : float      Current XGBoost-anchored posterior P(home_win).
    model_delta : float     Change in Kalshi posterior since last snapshot.
    kalshi_pre : float      Market mid in cents BEFORE this snapshot (pre-reprice).

    Returns
    -------
    dict or None    Trade record if an order was placed.
    """
    from ..routes.trading import kalshi, get_or_create_order_group, market_order_groups

    if not state.params.enabled or not state.home_ticker:
        return None

    ticker = state.home_ticker
    params = state.params

    # ── Step 1: cancel entry orders only (preserve exit orders) ──
    entry_group_key = f"{ticker}__entry"
    if entry_group_key in market_order_groups:
        try:
            kalshi.trades.delete_order_group(market_order_groups[entry_group_key])
        except Exception as e:
            logger.warning(f"[delta] Failed to delete entry group: {e}")
        market_order_groups.pop(entry_group_key, None)

    # ── Step 2: check delta threshold ──
    abs_delta = abs(model_delta)
    if abs_delta < params.min_delta:
        state.last_direction = "SKIP"
        state.last_model_delta = model_delta
        state.last_expected_move = None
        state.last_order_price = None
        state.last_size = 0
        return None

    # ── Step 3: compute expected move and fair value ──
    expected_move = model_delta * params.delta_scale
    fair = kalshi_pre + expected_move * 100
    direction = "BUY" if model_delta > 0 else "SELL"

    # ── Step 4: refresh position ──
    pos, cost = compute_position_from_fills(ticker)
    state.home_position = pos
    state.home_cost = cost

    # ── Step 5: exposure check ──
    if cost >= params.max_exposure:
        state.last_direction = "SKIP"
        return None

    # ── Step 6: compute size with inventory skew ──
    raw_size = compute_delta_size(model_delta, params)

    if direction == "BUY":
        # Buying increases long position
        if pos >= params.max_position:
            state.last_direction = "SKIP"
            return None
        # Inventory skew: reduce size when adding to existing direction
        if pos > 0:
            remaining_capacity = params.max_position - pos
            raw_size = min(raw_size, remaining_capacity)
        # If already short and buying, that's reducing risk — no reduction needed
    else:
        # Selling decreases position (or goes short)
        if pos <= -params.max_position:
            state.last_direction = "SKIP"
            return None
        if pos < 0:
            remaining_capacity = params.max_position + pos
            raw_size = min(raw_size, remaining_capacity)

    size = max(1, raw_size)

    # ── Step 7: determine order price ──
    if direction == "BUY":
        if state.home_best_ask is None:
            state.last_direction = "SKIP"
            return None
        order_price = min(99, state.home_best_ask + params.aggression)
    else:
        if state.home_best_bid is None:
            state.last_direction = "SKIP"
            return None
        order_price = max(1, state.home_best_bid - params.aggression)

    # ── Step 8: compute fee (factored into exit price, not used as gate) ──
    p_at_price = order_price / 100.0
    fee_cents = compute_fee_per_contract_cents(p_at_price, params.fee_rate)

    # ── Step 9: build trade record ──
    trade_record = {
        "time": time.time(),
        "direction": direction,
        "order_price": order_price,
        "size": size,
        "model_delta": round(model_delta, 5),
        "expected_move": round(expected_move, 5),
        "fair": round(fair, 1),
        "kalshi_pre": round(kalshi_pre, 1),
        "p_kalshi": round(p_kalshi, 4),
        "p_computed": round(p_computed, 4),
        "position": pos,
        "fee_cents": round(fee_cents, 2),
        "paper": settings.paper_trade,
    }

    # ── Step 10: compute exit price ──
    # Exit at where we expect the market to settle after repricing.
    # exit_offset: 0 = at fair, positive = closer to entry (more likely to fill),
    # negative = further from entry (more profit per trade, less likely to fill).
    if direction == "BUY":
        # Bought → sell at fair (where market should reprice to)
        exit_price = max(1, min(99, round(fair) - params.exit_offset))
    else:
        # Sold → buy back at fair
        exit_price = max(1, min(99, round(fair) + params.exit_offset))

    # ── Step 11: place orders ──
    if settings.paper_trade:
        trade_record["result"] = "PAPER"
        trade_record["exit_price"] = exit_price
        logger.info(
            f"[delta][PAPER] {direction} {size}@{order_price}c → exit@{exit_price}c | "
            f"delta={model_delta:+.4f} move={expected_move:+.4f} fair={fair:.1f} "
            f"pos={pos} mid={kalshi_pre:.1f}"
        )
    else:
        try:
            # Entry: IOC to fill immediately before market reprices
            entry_gid = _get_or_create_group(kalshi, entry_group_key, market_order_groups)
            if direction == "BUY":
                r = kalshi.trades.buy_limit_order(
                    ticker=ticker,
                    count=size,
                    price=order_price,
                    order_group_id=entry_gid,
                    time_in_force="immediate_or_cancel",
                )
            else:
                r = kalshi.trades.sell_limit_order(
                    ticker=ticker,
                    count=size,
                    price=order_price,
                    order_group_id=entry_gid,
                    time_in_force="immediate_or_cancel",
                )
            trade_record["result"] = r

            # Exit: GTC resting order at fair value (opposite side)
            exit_group_key = f"{ticker}__exit"
            exit_gid = _get_or_create_group(kalshi, exit_group_key, market_order_groups)
            try:
                if direction == "BUY":
                    # Bought → resting sell at fair to take profit
                    kalshi.trades.sell_limit_order(
                        ticker=ticker,
                        count=size,
                        price=exit_price,
                        order_group_id=exit_gid,
                        time_in_force="good_till_canceled",
                    )
                else:
                    # Sold → resting buy at fair to take profit
                    kalshi.trades.buy_limit_order(
                        ticker=ticker,
                        count=size,
                        price=exit_price,
                        order_group_id=exit_gid,
                        time_in_force="good_till_canceled",
                    )
                trade_record["exit_price"] = exit_price
                logger.info(
                    f"[delta] {direction} {size}@{order_price}c → exit@{exit_price}c | "
                    f"delta={model_delta:+.4f} move={expected_move:+.4f} fair={fair:.1f} "
                    f"pos={pos} mid={kalshi_pre:.1f}"
                )
            except Exception as e:
                logger.warning(f"[delta] Exit order failed: {e}")
                trade_record["exit_error"] = str(e)

        except Exception as e:
            logger.error(f"[delta] Entry order failed: {e}")
            trade_record["error"] = str(e)

    # ── Step 12: update state ──
    state.last_theo = round(p_kalshi * 100)
    state.last_p_kalshi = p_kalshi
    state.last_p_computed = p_computed
    state.last_model_delta = model_delta
    state.last_expected_move = expected_move
    state.last_direction = direction
    state.last_order_price = order_price
    state.last_size = size
    state.last_fair = fair
    state.last_exit_price = exit_price
    state.last_trade_time = time.time()
    state.trades.append(trade_record)

    return trade_record
