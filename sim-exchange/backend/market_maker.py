"""
Market maker: seeds an orderbook with liquidity.

Seeding is done per-book. The admin layer handles calling this
for both home and away books with mirrored midpoints.
"""

from .orderbook import Orderbook


def seed_book(
    book: Orderbook,
    midpoint: int,
    spread: int,
    depth_per_level: int,
    num_levels: int,
):
    """
    Place symmetric MM liquidity around a midpoint on a single book.

    E.g. midpoint=55, spread=4, depth=50, levels=5:
      Asks: 57@50, 58@50, 59@50, 60@50, 61@50
      Bids: 53@50, 52@50, 51@50, 50@50, 49@50
    """
    book.clear_mm_orders()

    half_spread = spread // 2
    best_ask = midpoint + max(half_spread, 1)
    best_bid = midpoint - max(half_spread, 1)

    for i in range(num_levels):
        price = best_ask + i
        if 1 <= price <= 99:
            book.submit_order(action="sell", price=price, count=depth_per_level,
                              time_in_force="good_till_canceled", is_mm=True)

    for i in range(num_levels):
        price = best_bid - i
        if 1 <= price <= 99:
            book.submit_order(action="buy", price=price, count=depth_per_level,
                              time_in_force="good_till_canceled", is_mm=True)


def seed_from_trade(
    book: Orderbook,
    trade_price: int,
    volume: int,
    spread: int = 2,
    num_levels: int = 5,
):
    """
    Seed from a trade event during replay.
    Volume-calibrated depth with exponential taper.
    """
    book.clear_mm_orders()

    if volume <= 0:
        volume = 50

    half_spread = spread / 2
    best_ask = trade_price + max(int(half_spread + 0.5), 1)  # at least 1c above
    best_bid = trade_price - max(int(half_spread + 0.5), 1)  # at least 1c below
    if spread == 1:
        # 1c spread: ask at midpoint+1, bid at midpoint
        best_ask = trade_price + 1
        best_bid = trade_price

    decay = 0.65
    weights = [decay ** i for i in range(num_levels)]
    total_weight = sum(weights)

    for i in range(num_levels):
        level_depth = max(1, int(volume * weights[i] / total_weight))

        ask_price = best_ask + i
        if 1 <= ask_price <= 99:
            book.submit_order(action="sell", price=ask_price, count=level_depth,
                              time_in_force="good_till_canceled", is_mm=True)

        bid_price = best_bid - i
        if 1 <= bid_price <= 99:
            book.submit_order(action="buy", price=bid_price, count=level_depth,
                              time_in_force="good_till_canceled", is_mm=True)
