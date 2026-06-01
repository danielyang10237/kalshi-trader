"""
Simulated Kalshi Exchange Server.

Drop-in replacement for the real Kalshi API.
Two independent orderbooks (one per ticker), mirrored only during seeding.
"""

import asyncio

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from .config import SimConfig
from .orderbook import Orderbook
from .account import SimAccount
from .replay import ReplayEngine
from .snapshot_feeder import SnapshotFeeder
from . import rest_api
from . import admin_api
from .ws_api import ws_manager, websocket_endpoint

# Global state
config = SimConfig()
# Two independent orderbooks — one per market
books: dict[str, Orderbook] = {}
account = SimAccount(initial_balance=config.initial_balance)
replay_engine = ReplayEngine()
snapshot_feeder = SnapshotFeeder()

app = FastAPI(title="Simulated Kalshi Exchange", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _handle_fill(fill):
    """Process a fill from the orderbook (including sweep matches)."""
    account.record_fill(fill)
    # Remove from resting orders if fully filled
    for oid, order in list(account.resting_orders.items()):
        if order.ticker == fill.ticker and order.action == fill.action and order.price == fill.yes_price:
            if order.remaining <= 0:
                account.remove_resting_order(oid)
            break
    asyncio.ensure_future(ws_manager.broadcast_fill(fill.to_dict()))
    asyncio.ensure_future(admin_api.broadcast_gui_event({"type": "fill", "data": fill.to_dict()}))


def get_or_create_book(ticker: str) -> Orderbook:
    """Get the orderbook for a ticker, creating it if needed."""
    if ticker and ticker not in books:
        books[ticker] = Orderbook(ticker)
        books[ticker].set_callbacks(
            on_delta=lambda deltas: asyncio.ensure_future(
                ws_manager.broadcast_deltas_single(deltas, ticker)
            ),
            on_fill=lambda fill: _handle_fill(fill),
            on_trade=lambda trade: asyncio.ensure_future(
                ws_manager.broadcast_trade(trade.to_dict())
            ) or asyncio.ensure_future(
                admin_api.broadcast_gui_event({"type": "trade", "data": trade.to_dict()})
            ),
        )
    return books.get(ticker)


# Initialize REST API with shared state
rest_api.init(books, account, config, get_or_create_book)
admin_api.init(books, account, config, replay_engine, get_or_create_book, snapshot_feeder)

# Mount routers
app.include_router(rest_api.router, prefix="/trade-api/v2")
app.include_router(admin_api.router, prefix="/sim")


# Kalshi-compatible WebSocket endpoint
@app.websocket("/trade-api/ws/v2")
async def kalshi_ws(ws: WebSocket):
    await websocket_endpoint(ws, books)


@app.get("/")
async def root():
    return {
        "service": "sim-exchange",
        "status": "running",
        "home_ticker": config.home_ticker,
        "away_ticker": config.away_ticker,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.server:app", host="0.0.0.0", port=config.port, reload=True)
