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
