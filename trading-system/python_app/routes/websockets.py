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

    def _convert(msg_str: str) -> str:
        """Translate Kalshi's new fp-dollar fields into the legacy integer-cent
        fields the frontend (OrderbookLadder etc.) reads. Adds yes/no/price/delta
        alongside the *_dollars_fp / price_dollars / delta_fp Kalshi now sends,
        so callers that read either form continue to work."""
        try:
            d = json.loads(msg_str)
            d = json.loads(d) if isinstance(d, str) else d
            t = d.get("type")
            m = d.get("msg")
            if isinstance(m, dict):
                if t == "orderbook_snapshot":
                    if "yes_dollars_fp" in m and "yes" not in m:
                        m["yes"] = [[int(round(float(p)*100)), int(float(s))]
                                     for p, s in m["yes_dollars_fp"]]
                    if "no_dollars_fp" in m and "no" not in m:
                        m["no"]  = [[int(round(float(p)*100)), int(float(s))]
                                     for p, s in m["no_dollars_fp"]]
                elif t == "orderbook_delta":
                    if "price_dollars" in m and "price" not in m:
                        m["price"] = int(round(float(m["price_dollars"]) * 100))
                    if "delta_fp" in m and "delta" not in m:
                        m["delta"] = int(float(m["delta_fp"]))
            return json.dumps(d)
        except Exception:
            return msg_str

    async def forward_messages():
        try:
            while True:
                msg = await stream.out_queue.get()
                msg = _convert(msg)
                # Feed best ask to any engine tracking this ticker
                try:
                    parsed = json.loads(msg)
                    inner = json.loads(parsed) if isinstance(parsed, str) else parsed
                    # Extract best ask from orderbook snapshot/delta
                    asks = inner.get("yes", inner.get("msg", {})).get("yes", []) if isinstance(inner, dict) else []
                    if not asks and isinstance(inner, dict):
                        # Try alternate structure: msg.yes for asks
                        msg_data = inner.get("msg", {})
                        if isinstance(msg_data, dict):
                            asks = msg_data.get("yes", [])
                    # Find best (lowest) ask
                    best_ask = None
                    if asks and isinstance(asks, list):
                        for level in asks:
                            price = level[0] if isinstance(level, list) else level.get("price", 0)
                            if price and (best_ask is None or price < best_ask):
                                best_ask = price
                    if best_ask is not None:
                        for engine in _engines.values():
                            engine.on_orderbook_update(market_ticker, best_ask)
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
