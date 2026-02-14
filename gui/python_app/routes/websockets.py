"""WebSocket proxy endpoints"""

import asyncio
from typing import Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..kalshi_client import KalshiClient
from ..settings import settings

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

    async def forward_messages():
        try:
            while True:
                msg = await stream.out_queue.get()
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
