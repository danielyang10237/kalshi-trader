"""
Kalshi-compatible WebSocket endpoint.
Serves at /trade-api/ws/v2 to match the real Kalshi WS API.
"""

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect


class WSManager:
    def __init__(self):
        # channel -> ticker -> set of websockets
        self.subscriptions: dict[str, dict[str, set[WebSocket]]] = {}
        self.clients: set[WebSocket] = set()
        self.seq: dict[str, int] = {}

    def _next_seq(self, ticker: str) -> int:
        self.seq[ticker] = self.seq.get(ticker, 0) + 1
        return self.seq[ticker]

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket):
        self.clients.discard(ws)
        for channel_subs in self.subscriptions.values():
            for ticker_subs in channel_subs.values():
                ticker_subs.discard(ws)

    def subscribe(self, ws: WebSocket, channels: list[str], market_tickers: list[str]):
        for channel in channels:
            if channel not in self.subscriptions:
                self.subscriptions[channel] = {}
            for ticker in market_tickers:
                if ticker not in self.subscriptions[channel]:
                    self.subscriptions[channel][ticker] = set()
                self.subscriptions[channel][ticker].add(ws)
            if not market_tickers:
                if "" not in self.subscriptions[channel]:
                    self.subscriptions[channel][""] = set()
                self.subscriptions[channel][""].add(ws)

    async def _send_to_subs(self, channel: str, ticker: str, message: dict):
        subs = self.subscriptions.get(channel, {}).get(ticker, set())
        subs = subs | self.subscriptions.get(channel, {}).get("", set())
        msg_str = json.dumps(message)
        dead = set()
        for ws in subs:
            try:
                await ws.send_text(msg_str)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws)

    async def send_orderbook_snapshot(self, ws: WebSocket, ticker: str, books: dict):
        book = books.get(ticker)
        if not book:
            return
        snap = book.get_snapshot()
        msg = {
            "type": "orderbook_snapshot",
            "seq": self._next_seq(ticker),
            "msg": {"market_ticker": ticker, "yes": snap["yes"], "no": snap["no"]},
        }
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            pass

    async def broadcast_snapshot_for(self, ticker: str, books: dict):
        """Send full snapshot to all subscribers of a specific ticker."""
        book = books.get(ticker)
        if not book:
            return
        subs = self.subscriptions.get("orderbook_delta", {}).get(ticker, set())
        if not subs:
            return
        snap = book.get_snapshot()
        msg = {
            "type": "orderbook_snapshot",
            "seq": self._next_seq(ticker),
            "msg": {"market_ticker": ticker, "yes": snap["yes"], "no": snap["no"]},
        }
        msg_str = json.dumps(msg)
        dead = set()
        for ws in subs:
            try:
                await ws.send_text(msg_str)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_deltas_single(self, deltas: list, ticker: str):
        """Broadcast deltas for a single ticker's book."""
        for delta in deltas:
            msg = {
                "type": "orderbook_delta",
                "seq": self._next_seq(ticker),
                "msg": {
                    "market_ticker": ticker,
                    "price": delta.price,
                    "delta": delta.delta,
                    "side": delta.side,
                },
            }
            await self._send_to_subs("orderbook_delta", ticker, msg)

    async def broadcast_fill(self, fill_dict: dict):
        msg = {"type": "fill", "msg": fill_dict}
        await self._send_to_subs("fill", "", msg)

    async def broadcast_trade(self, trade_dict: dict):
        ticker = trade_dict.get("ticker", "")
        msg = {"type": "trade", "msg": trade_dict}
        await self._send_to_subs("trade", ticker, msg)


ws_manager = WSManager()


async def websocket_endpoint(ws: WebSocket, books: dict):
    await ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                cmd = msg.get("cmd")
                params = msg.get("params", {})
                if cmd == "subscribe":
                    channels = params.get("channels", [])
                    tickers = params.get("market_tickers", [])
                    ws_manager.subscribe(ws, channels, tickers)
                    if "orderbook_delta" in channels:
                        for ticker in tickers:
                            await ws_manager.send_orderbook_snapshot(ws, ticker, books)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(ws)
