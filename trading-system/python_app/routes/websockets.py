"""WebSocket proxy endpoints"""

import asyncio
import json
from typing import Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..kalshi_client import KalshiClient
from ..settings import settings
from ..fills_cache import save_fill
from ..nba.engine import _engines

router = APIRouter(tags=["websockets"])

# Shared client for creating streams
kalshi = KalshiClient(
    api_url=settings.kalshi_ws_url,
    key_id=settings.kalshi_api_key,
    private_key_path=settings.kalshi_private_key_path,
)

# Connected clients for broadcast
clients: Set[WebSocket] = set()
clients_lock = asyncio.Lock()


@router.websocket("/ws/market")
async def ws_market(ws: WebSocket):
    """General market WebSocket"""
    await ws.accept()
    async with clients_lock:
        clients.add(ws)
    try:
        while True:
            _ = await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with clients_lock:
            clients.discard(ws)


@router.websocket("/ws/orderbook/{market_ticker}")
async def ws_orderbook(ws: WebSocket, market_ticker: str):
    """WebSocket proxy for orderbook data"""
    await ws.accept()
    print(f"[ws_orderbook] Client connected for market: {market_ticker}")

    stream = kalshi.stream.orderbook(market_ticker)
    stream_task = asyncio.create_task(stream.run_forever())

    # Local orderbook state: apply snapshots + deltas incrementally
    # yes_book: {price_cents: size} — YES bids
    # no_book: {price_cents: size} — NO bids (asks in NO-price space)
    yes_book: dict[int, float] = {}
    no_book: dict[int, float] = {}

    def _parse_price(raw) -> int:
        p = float(raw) if isinstance(raw, str) else raw
        if isinstance(raw, str) and p < 1.0:
            p = round(p * 100)
        return int(p) if p else 0

    def _parse_size(raw) -> float:
        return float(raw) if isinstance(raw, str) else raw

    def _update_engines():
        best_bid = max(yes_book.keys()) if yes_book else None
        best_ask = (100 - max(no_book.keys())) if no_book else None
        if best_bid is not None or best_ask is not None:
            for engine in _engines.values():
                engine.on_orderbook_update(market_ticker, best_bid, best_ask)

    async def forward_messages():
        nonlocal yes_book, no_book
        try:
            while True:
                msg = await stream.out_queue.get()
                try:
                    parsed = json.loads(msg)
                    inner = json.loads(parsed) if isinstance(parsed, str) else parsed
                    if not isinstance(inner, dict):
                        pass
                    elif inner.get("type") == "orderbook_snapshot":
                        msg_data = inner.get("msg", {})
                        # Full reset from snapshot
                        yes_book.clear()
                        no_book.clear()
                        for level in (msg_data.get("yes", []) or msg_data.get("yes_dollars_fp", [])):
                            if isinstance(level, list) and len(level) >= 2:
                                p = _parse_price(level[0])
                                s = _parse_size(level[1])
                                if p and s > 0:
                                    yes_book[p] = s
                        for level in (msg_data.get("no", []) or msg_data.get("no_dollars_fp", [])):
                            if isinstance(level, list) and len(level) >= 2:
                                p = _parse_price(level[0])
                                s = _parse_size(level[1])
                                if p and s > 0:
                                    no_book[p] = s
                        _update_engines()

                    elif inner.get("type") == "orderbook_delta":
                        msg_data = inner.get("msg", {})
                        side = msg_data.get("side", "")
                        price_raw = msg_data.get("price_dollars") or msg_data.get("price")
                        delta_raw = msg_data.get("delta_fp") or msg_data.get("delta")
                        if side and price_raw is not None and delta_raw is not None:
                            price = _parse_price(price_raw)
                            delta = _parse_size(delta_raw)
                            book = yes_book if side == "yes" else no_book
                            current = book.get(price, 0)
                            new_size = current + delta
                            if new_size <= 0:
                                book.pop(price, None)
                            else:
                                book[price] = new_size
                            _update_engines()

                except Exception:
                    pass
                await ws.send_text(msg)
        except Exception as e:
            print(f"[ws_orderbook] Error forwarding: {e}")

    forward_task = asyncio.create_task(forward_messages())

    try:
        while True:
            try:
                _ = await ws.receive_text()
            except WebSocketDisconnect:
                print(f"[ws_orderbook] Client disconnected for market: {market_ticker}")
                break
    finally:
        await stream.stop()
        stream_task.cancel()
        forward_task.cancel()
        await asyncio.gather(stream_task, forward_task, return_exceptions=True)


@router.websocket("/ws/trades/{market_ticker}")
async def ws_trades(ws: WebSocket, market_ticker: str):
    """WebSocket proxy for public trades"""
    await ws.accept()
    print(f"[ws_trades] Client connected for market: {market_ticker}")

    stream = kalshi.stream.trades(market_ticker)
    stream_task = asyncio.create_task(stream.run_forever())

    async def forward_messages():
        try:
            while True:
                msg = await stream.out_queue.get()
                await ws.send_text(msg)
        except Exception as e:
            print(f"[ws_trades] Error forwarding: {e}")

    forward_task = asyncio.create_task(forward_messages())

    try:
        while True:
            try:
                _ = await ws.receive_text()
            except WebSocketDisconnect:
                print(f"[ws_trades] Client disconnected for market: {market_ticker}")
                break
    except Exception as e:
        print(f"[ws_trades] Error: {e}")
    finally:
        print(f"[ws_trades] Cleaning up for market: {market_ticker}")
        await stream.stop()
        stream_task.cancel()
        forward_task.cancel()
        await asyncio.gather(stream_task, forward_task, return_exceptions=True)


@router.websocket("/ws/fills")
async def ws_fills(ws: WebSocket):
    """WebSocket proxy for user fill notifications (authenticated)"""
    await ws.accept()
    print(f"[ws_fills] Client connected for fills")

    stream = kalshi.stream.fills()
    stream_task = asyncio.create_task(stream.run_forever())

    async def forward_messages():
        try:
            while True:
                msg = await stream.out_queue.get()
                # Cache fill to disk
                try:
                    parsed = json.loads(msg)
                    inner = json.loads(parsed) if isinstance(parsed, str) else parsed
                    if inner.get("type") == "fill" and inner.get("msg"):
                        save_fill(inner["msg"])
                        for engine in _engines.values():
                            engine.on_fill(inner["msg"])
                except Exception:
                    pass
                await ws.send_text(msg)
        except Exception as e:
            print(f"[ws_fills] Error forwarding: {e}")

    forward_task = asyncio.create_task(forward_messages())

    try:
        while True:
            try:
                _ = await ws.receive_text()
            except WebSocketDisconnect:
                print(f"[ws_fills] Client disconnected")
                break
    except Exception as e:
        print(f"[ws_fills] Error: {e}")
    finally:
        print(f"[ws_fills] Cleaning up")
        await stream.stop()
        stream_task.cancel()
        forward_task.cancel()
        await asyncio.gather(stream_task, forward_task, return_exceptions=True)


async def broadcast_to_clients(msg: str):
    """Broadcast a message to all connected clients"""
    async with clients_lock:
        targets = list(clients)

    if not targets:
        return

    dead = []
    for c in targets:
        try:
            await c.send_text(msg)
        except Exception:
            dead.append(c)

    if dead:
        async with clients_lock:
            for d in dead:
                clients.discard(d)
