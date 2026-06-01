"""
Kalshi-compatible REST API endpoints.
Mounted at /trade-api/v2 to match the real Kalshi API surface.
"""

import json
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request, Query

from .ws_api import ws_manager
from . import admin_api

DATA_CACHE = Path(__file__).parent.parent / "data_cache"


def _load_games() -> list[dict]:
    games_file = DATA_CACHE / "games.json"
    if not games_file.exists():
        return []
    with open(games_file) as f:
        return json.load(f)


router = APIRouter()

# Set by server.py at startup
books = None  # dict[str, Orderbook]
account = None
config = None
get_or_create_book = None


def init(b, acct, cfg, get_book_fn):
    global books, account, config, get_or_create_book
    books = b
    account = acct
    config = cfg
    get_or_create_book = get_book_fn


# =============================================================================
# Portfolio - Orders
# =============================================================================

@router.post("/portfolio/orders")
async def create_order(request: Request):
    body = await request.json()
    ticker = body.get("ticker", "")
    action = body.get("action", "buy")
    count = body.get("count", 1)
    yes_price = body.get("yes_price", 50)
    order_type = body.get("type", "limit")
    time_in_force = body.get("time_in_force", "good_till_canceled")
    client_order_id = body.get("client_order_id", str(uuid.uuid4()))
    order_group_id = body.get("order_group_id")

    if order_type == "market":
        time_in_force = "immediate_or_cancel"

    book = get_or_create_book(ticker)
    if not book:
        return {"error": f"No book for ticker {ticker}"}

    # Suppress delta/fill/trade callbacks — REST path handles these manually
    saved_delta = book._on_delta
    saved_fill = book._on_fill
    saved_trade = book._on_trade
    book._on_delta = None
    book._on_fill = None
    book._on_trade = None
    result = book.submit_order(
        action=action,
        price=yes_price,
        count=count,
        time_in_force=time_in_force,
        order_type=order_type,
        client_order_id=client_order_id,
        order_group_id=order_group_id,
        is_mm=False,
    )
    book._on_delta = saved_delta
    book._on_fill = saved_fill
    book._on_trade = saved_trade

    for fill in result.fills:
        account.record_fill(fill)
        # The REST order path suppresses on_fill callbacks; broadcast manually so
        # the trading-backend's /ws/fills subscription (and any other listeners)
        # see fills generated from REST-placed orders, not just WS-driven ones.
        try:
            await ws_manager.broadcast_fill(fill.to_dict())
        except Exception as _e:
            pass

    if result.resting_order:
        account.add_resting_order(result.resting_order)

    # Broadcast snapshot instead of deltas — idempotent regardless of subscriber count
    await ws_manager.broadcast_snapshot_for(ticker, books)

    order_response = {
        "order_id": result.resting_order.order_id if result.resting_order else str(uuid.uuid4()),
        "client_order_id": client_order_id,
        "ticker": ticker,
        "action": action,
        "side": "yes",
        "type": order_type,
        "yes_price": yes_price,
        "no_price": 100 - yes_price,
        "count": count,
        "remaining_count": result.remaining,
        "status": result.status,
        "time_in_force": time_in_force,
        "order_group_id": order_group_id or "",
        "created_time": result.fills[0].ts if result.fills else __import__("time").time(),
    }

    # Capture market context at order time
    market_context = {
        "best_bid": book.get_best_bid(),
        "best_ask": book.get_best_ask(),
    }
    # Add game state from replay engine if available
    from . import admin_api as _admin
    if _admin.replay_engine:
        score = _admin.replay_engine.get_current_score()
        market_context["home_score"] = score.get("home_score", 0)
        market_context["away_score"] = score.get("away_score", 0)
        market_context["period"] = score.get("period", "")
        market_context["clock"] = score.get("clock", "")
    if _admin.snapshot_feeder and _admin.snapshot_feeder.enabled:
        sf = _admin.snapshot_feeder
        market_context["home_score"] = sf.home_box.score
        market_context["away_score"] = sf.away_box.score
        market_context["period"] = sf.last_period
        market_context["clock"] = sf.last_clock

    # Log every order (not just resting) for the trade log
    from .orderbook import OrderEntry
    log_entry = result.resting_order or OrderEntry(
        order_id=order_response["order_id"],
        client_order_id=client_order_id or "",
        ticker=ticker,
        action=action,
        price=yes_price,
        remaining=result.remaining,
        initial_count=count,
        time_in_force=time_in_force,
        order_type=order_type,
        is_mm=False,
    )
    account.log_order(log_entry, result.status, [f.to_dict() for f in result.fills],
                      market_context=market_context)

    # Notify GUI
    await admin_api.broadcast_gui_event({
        "type": "order",
        "data": order_response,
    })

    return {"order": order_response}


@router.get("/portfolio/orders")
async def get_orders(
    ticker: Optional[str] = Query(None),
    status: str = Query("resting"),
    limit: int = Query(200),
    cursor: Optional[str] = Query(None),
    event_ticker: Optional[str] = Query(None),
):
    return account.get_resting_orders(ticker=ticker, status=status, limit=limit)


@router.delete("/portfolio/orders/{order_id}")
async def cancel_order(order_id: str):
    order = account.remove_resting_order(order_id)
    # Cancel on the correct book
    for book in books.values():
        book.cancel_order(order_id)
    if order:
        return {"order": account._order_to_dict(order, "canceled")}
    return {"order": {"order_id": order_id, "status": "canceled"}}


@router.delete("/portfolio/orders/batch")
async def cancel_orders_batch(request: Request):
    body = await request.json()
    ids = body.get("ids", [])
    cancelled = []
    for oid in ids:
        order = account.remove_resting_order(oid)
        for book in books.values():
            book.cancel_order(oid)
        if order:
            cancelled.append(account._order_to_dict(order, "canceled"))
    return {"orders": cancelled}


# =============================================================================
# Portfolio - Fills, Balance, Positions
# =============================================================================

@router.get("/portfolio/fills")
async def get_fills(
    ticker: Optional[str] = Query(None),
    limit: int = Query(100),
    cursor: Optional[str] = Query(None),
):
    return account.get_fills(ticker=ticker, limit=limit, cursor=cursor)


@router.get("/portfolio/balance")
async def get_balance():
    return account.get_balance()


@router.get("/portfolio/positions")
async def get_positions(
    limit: int = Query(100),
    cursor: Optional[str] = Query(None),
    settlement_status: Optional[str] = Query(None),
):
    return account.get_positions(limit=limit, cursor=cursor)


# =============================================================================
# Portfolio - Order Groups
# =============================================================================

@router.post("/portfolio/order_groups/create")
async def create_order_group(request: Request):
    body = await request.json()
    contracts_limit = body.get("contracts_limit", 100000)
    group_id = account.create_order_group(contracts_limit)
    return {"order_group": {"order_group_id": group_id}}


@router.delete("/portfolio/order_groups/{order_group_id}")
async def delete_order_group(order_group_id: str):
    for book in books.values():
        book.cancel_orders_by_group(order_group_id)
    to_remove = [oid for oid, o in account.resting_orders.items() if o.order_group_id == order_group_id]
    for oid in to_remove:
        account.remove_resting_order(oid)
    account.delete_order_group(order_group_id)
    return {}


@router.get("/portfolio/order_groups")
async def get_order_groups():
    return {"order_groups": list(account.order_groups.values())}


# =============================================================================
# Series & Events
# =============================================================================

@router.get("/series")
async def get_series(limit: int = Query(100), cursor: Optional[str] = Query(None)):
    games = _load_games()
    if not games:
        return {"series": [], "cursor": ""}
    return {"series": [{"ticker": "KXNBAGAME", "title": "NBA Games", "category": "Sports"}], "cursor": ""}


@router.get("/events")
async def get_events(
    series_ticker: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100),
    cursor: Optional[str] = Query(None),
    with_nested_markets: bool = Query(False),
):
    games = _load_games()
    if series_ticker and series_ticker != "KXNBAGAME":
        return {"events": [], "cursor": ""}
    events = []
    for game in games:
        event_ticker = "-".join(game["home_ticker"].split("-")[:-1])
        events.append({
            "event_ticker": event_ticker,
            "title": f"{game['away_team']} @ {game['home_team']} ({game['date']})",
            "sub_title": game.get("note", ""),
            "series_ticker": "KXNBAGAME",
            "category": "Sports",
            "mutually_exclusive": True,
        })
    return {"events": events[:limit], "cursor": ""}


@router.get("/events/{event_ticker}/markets")
async def get_event_markets(
    event_ticker: str,
    status: Optional[str] = Query(None),
    limit: int = Query(100),
    cursor: Optional[str] = Query(None),
):
    games = _load_games()
    for game in games:
        game_event = "-".join(game["home_ticker"].split("-")[:-1])
        if game_event == event_ticker:
            return {
                "markets": [
                    {"ticker": game["home_ticker"], "title": f"{game['home_team']} to win", "status": "open", "event_ticker": event_ticker, "series_ticker": "KXNBAGAME"},
                    {"ticker": game["away_ticker"], "title": f"{game['away_team']} to win", "status": "open", "event_ticker": event_ticker, "series_ticker": "KXNBAGAME"},
                ],
                "cursor": "",
            }
    return {"markets": [], "cursor": ""}


# =============================================================================
# Markets
# =============================================================================

@router.get("/markets/{ticker}")
async def get_market(ticker: str):
    book = books.get(ticker)
    return {
        "market": {
            "ticker": ticker,
            "title": f"Simulated market {ticker}",
            "status": "open",
            "yes_sub_title": "Yes",
            "no_sub_title": "No",
            "result": "",
            "can_close_early": True,
            "expiration_time": "",
            "last_price": book.get_best_ask() if book else 50,
            "volume": len(account.fills) if account else 0,
            "volume_24h": len(account.fills) if account else 0,
            "liquidity": 0,
        }
    }


@router.get("/markets")
async def get_markets(
    series_ticker: Optional[str] = Query(None),
    event_ticker: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100),
    cursor: Optional[str] = Query(None),
):
    games = _load_games()

    if event_ticker:
        for game in games:
            game_event = "-".join(game["home_ticker"].split("-")[:-1])
            if game_event == event_ticker:
                return {
                    "markets": [
                        {"ticker": game["home_ticker"], "title": f"{game['home_team']} to win", "status": "open", "event_ticker": event_ticker, "series_ticker": "KXNBAGAME"},
                        {"ticker": game["away_ticker"], "title": f"{game['away_team']} to win", "status": "open", "event_ticker": event_ticker, "series_ticker": "KXNBAGAME"},
                    ],
                    "cursor": "",
                }
        return {"markets": [], "cursor": ""}

    if series_ticker == "KXNBAGAME":
        markets = []
        for game in games:
            game_event = "-".join(game["home_ticker"].split("-")[:-1])
            markets.append({"ticker": game["home_ticker"], "title": f"{game['home_team']} to win", "status": "open", "event_ticker": game_event, "series_ticker": "KXNBAGAME"})
            markets.append({"ticker": game["away_ticker"], "title": f"{game['away_team']} to win", "status": "open", "event_ticker": game_event, "series_ticker": "KXNBAGAME"})
        return {"markets": markets[:limit], "cursor": ""}

    markets = [{"ticker": t, "title": f"Simulated market {t}", "status": "open"} for t in books]
    return {"markets": markets, "cursor": ""}

# Cache: (ticker, bucket_sec) -> list of candles. Cleared on game select.
_candles_cache: dict = {}


@router.get("/series/{series_ticker}/events/{event_ticker}/candlesticks")
async def get_candlesticks(
    series_ticker: str,
    event_ticker: str,
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
    period_interval: int = Query(1),
    ticker: Optional[str] = Query(None),
):
    """Synthesize candles from the loaded recording's best-bid time series.

    The sim has no trade history, so true OHLC isn't available. Instead we walk
    the orderbook recording's snapshots/deltas for this market_ticker, maintain
    the yes-side book, and at each event record the current best yes-bid. Bucket
    by period_interval minutes and emit:
      high  = max best-bid seen in the bucket
      low   = min best-bid seen in the bucket
      open  = uniform-random int in [low, high]   (visual placeholder)
      close = uniform-random int in [low, high]   (visual placeholder)
      volume = count of yes-side events in the bucket
    Results are deterministic per (ticker, period) and cached until the next
    game selection.
    """
    import random
    re_ = admin_api.replay_engine
    if not ticker or not re_ or not re_.events:
        return {"adjusted_end_ts": end_ts,
                "market_candlesticks": [],
                "market_tickers": [ticker] if ticker else []}

    bucket_sec = max(period_interval, 1) * 60
    cache_key = (ticker, bucket_sec)
    if cache_key not in _candles_cache:
        # Track BOTH sides of the book so we can compute a mid-price.
        # mid_cents = (best_yes_bid + (100 - best_no_bid)) / 2
        # where best_no_bid is the highest NO price someone is willing to pay,
        # so (100 - best_no_bid) is the implied ask on the yes side. Averaging
        # gives a tighter "true price" curve than yes-bid alone.
        yes_levels: dict[int, int] = {}
        no_levels: dict[int, int] = {}
        buckets: dict[int, list[int]] = {}  # bucket_end -> ordered list of mid samples

        for ev in re_.events:
            if ev.ticker != ticker:
                continue
            ts_sec = ev.wall_ms // 1000

            if ev.event_type == "snapshot":
                yes_levels.clear(); no_levels.clear()
                for side, store in (("yes", yes_levels), ("no", no_levels)):
                    for price_str, size_str in ev.data.get(f"{side}_dollars_fp", []):
                        try:
                            p = int(round(float(price_str) * 100))
                            sz = int(float(size_str))
                            if p > 0 and sz > 0:
                                store[p] = sz
                        except (ValueError, TypeError):
                            pass
            elif ev.event_type == "delta":
                side = ev.data.get("side")
                if side not in ("yes", "no"):
                    continue
                store = yes_levels if side == "yes" else no_levels
                try:
                    p = int(round(float(ev.data.get("price_dollars", 0)) * 100))
                    d = int(float(ev.data.get("delta_fp", 0)))
                except (ValueError, TypeError):
                    continue
                ns = store.get(p, 0) + d
                if ns <= 0:
                    store.pop(p, None)
                else:
                    store[p] = ns

            best_yes = max(yes_levels) if yes_levels else 0
            best_no  = max(no_levels)  if no_levels  else 0
            # Mid-price; if only one side has resting liquidity, fall back to it.
            if best_yes > 0 and best_no > 0:
                mid = (best_yes + (100 - best_no)) / 2
            elif best_yes > 0:
                mid = best_yes
            elif best_no > 0:
                mid = 100 - best_no
            else:
                continue
            mid_cents = int(round(mid))
            if not (0 < mid_cents < 100):
                continue

            bucket_end = (ts_sec // bucket_sec) * bucket_sec + bucket_sec
            buckets.setdefault(bucket_end, []).append(mid_cents)

        # Real OHLC from the ordered mid samples in each bucket. Shape matches
        # Kalshi's wire format: price/yes_bid/yes_ask are nested objects, and the
        # frontend reads .price.open / .price.high / .price.low / .price.close.
        all_candles = []
        for end_ts_b in sorted(buckets):
            samples = buckets[end_ts_b]
            o, c, hi, lo = samples[0], samples[-1], max(samples), min(samples)
            all_candles.append({
                "end_period_ts": end_ts_b,
                "price":  {"open": o, "high": hi, "low": lo, "close": c, "mean": (hi+lo)//2},
                "yes_bid": {"open": o, "high": hi, "low": lo, "close": c},
                "yes_ask": {"open": o, "high": hi, "low": lo, "close": c},
                "volume": len(samples),
                "open_interest": 0,
            })
        _candles_cache[cache_key] = all_candles

    candles = _candles_cache[cache_key]
    if start_ts is not None or end_ts is not None:
        lo_ts = start_ts if start_ts is not None else 0
        hi_ts = end_ts if end_ts is not None else 10**12
        candles = [c for c in candles if lo_ts <= c["end_period_ts"] <= hi_ts]

    # Real Kalshi: market_candlesticks is a list parallel to market_tickers;
    # each entry is itself a list of candle objects for that market.
    return {
        "adjusted_end_ts": end_ts,
        "market_candlesticks": [candles],
        "market_tickers": [ticker],
    }
