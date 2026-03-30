import asyncio
import base64
import json
import requests
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .series import SeriesAPI
from .markets import MarketsAPI
from .events import EventsAPI
from .trades import TradesAPI
from .portfolio import PortfolioAPI
from .inventory import InventoryAPI

MODULE_DIR = Path(__file__).parent
DEFAULT_PRIVATE_KEY_PATH = MODULE_DIR / "kalshi_key.pem"
WS_PATH = "/trade-api/ws/v2"

def load_private_key(pem_path: str = None):
    path = pem_path or str(DEFAULT_PRIVATE_KEY_PATH)
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def sign_pss(private_key, text: str) -> str:
    sig = private_key.sign(
        text.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode("utf-8")


def make_auth_headers(private_key, key_id: str, method: str = "GET", path: str = WS_PATH) -> Dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    normalized_path = path.split("?")[0]
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    msg = timestamp + method.upper() + normalized_path
    signature = sign_pss(private_key, msg)
    return {
        "Content-Type": "application/json",
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }


class KalshiStream:
    """WebSocket client for real-time Kalshi data"""

    def __init__(self, ws_url: str, key_id: str, private_key):
        self.ws_url = ws_url
        self.key_id = key_id
        self._private_key = private_key
        self.out_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=10_000)
        self._stop = asyncio.Event()
        self.channels: List[str] = []
        self.market_tickers: List[str] = []

    async def stop(self):
        self._stop.set()

    async def run_forever(self):
        attempt = 0
        while not self._stop.is_set():
            attempt += 1
            backoff = min(10.0, 0.25 * attempt)
            try:
                await self._connect_once()
                attempt = 0
            except Exception as e:
                print(f"[kalshi] error: {e!r} (reconnect in {backoff:.2f}s)")
                await asyncio.sleep(backoff)

    async def _connect_once(self):
        headers = make_auth_headers(self._private_key, self.key_id, "GET", WS_PATH)
        print(f"[kalshi] Connecting to {self.ws_url} with key_id={self.key_id[:8]}...")

        # Try both kwarg names for websockets version compatibility
        try:
            ws = await websockets.connect(self.ws_url, additional_headers=headers)
        except TypeError:
            ws = await websockets.connect(self.ws_url, extra_headers=headers)
        try:
            print(f"[kalshi] connected: {self.ws_url}")

            sub: Dict[str, Any] = {
                "id": 1,
                "cmd": "subscribe",
                "params": {"channels": self.channels},
            }
            if self.market_tickers:
                sub["params"]["market_tickers"] = self.market_tickers

            await ws.send(json.dumps(sub))
            print(f"[kalshi] subscribed: channels={self.channels} market_tickers={self.market_tickers}")

            async for msg in ws:
                if isinstance(msg, (bytes, bytearray)):
                    msg = msg.decode("utf-8", errors="ignore")
                try:
                    self.out_queue.put_nowait(msg)
                except asyncio.QueueFull:
                    pass
        finally:
            await ws.close()

class StreamFactory:
    """Factory for creating pre-configured streams"""

    def __init__(self, client: "KalshiClient"):
        self._client = client

    def _create(self, channels: List[str], market_tickers: Optional[List[str]] = None) -> KalshiStream:
        stream = KalshiStream(
            ws_url=self._client.api_url,
            key_id=self._client.key_id,
            private_key=self._client._private_key,
        )
        stream.channels = channels
        stream.market_tickers = market_tickers or []
        return stream

    def orderbook(self, market_ticker: str) -> KalshiStream:
        """Create a stream for orderbook updates"""
        return self._create(["orderbook_delta"], [market_ticker])

    def trades(self, market_ticker: str = None) -> KalshiStream:
        """Create a stream for trade updates"""
        tickers = [market_ticker] if market_ticker else None
        return self._create(["trade"], tickers)

    def ticker(self, market_ticker: str = None) -> KalshiStream:
        """Create a stream for ticker updates"""
        tickers = [market_ticker] if market_ticker else None
        return self._create(["ticker"], tickers)

    def fills(self) -> KalshiStream:
        """Create a stream for user fill notifications (requires auth)"""
        return self._create(["fill"], None)

    def custom(self, channels: List[str], market_tickers: Optional[List[str]] = None) -> KalshiStream:
        """Create a stream with custom subscription"""
        return self._create(channels, market_tickers)

class KalshiClient:
    """Unified Kalshi API Client"""

    def __init__(self, api_url: str, key_id: str, private_key_path: str = None):
        self.api_url = api_url
        self.key_id = key_id
        self._private_key = load_private_key(private_key_path)

        # REST config
        self.base_url = api_url.replace("wss://", "https://").replace("ws://", "http://").replace("/trade-api/ws/v2", "")
        self.api_base = f"{self.base_url}/trade-api/v2"

        # REST sub-APIs
        self.series = SeriesAPI(self)
        self.markets = MarketsAPI(self)
        self.events = EventsAPI(self)
        self.trades = TradesAPI(self)
        self.portfolio = PortfolioAPI(self)
        self.inventory = InventoryAPI(self)

        # Stream factory
        self.stream = StreamFactory(self)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        json_body: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        url = f"{self.api_base}{path}"
        full_path = f"/trade-api/v2{path}"
        headers = make_auth_headers(self._private_key, self.key_id, method, full_path)
        
        response = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=30)
        
        if not response.ok:
            # Include actual Kalshi error message in exception
            try:
                error_body = response.json()
                error_msg = error_body.get("message") or error_body.get("error") or str(error_body)
            except Exception:
                error_msg = response.text
            raise Exception(f"{response.status_code}: {error_msg}")
        
        return response.json()


__all__ = [
    "KalshiClient",
    "KalshiStream",
    "StreamFactory",
    "load_private_key",
    "make_auth_headers",
    "WS_PATH",
    "DEFAULT_PRIVATE_KEY_PATH",
]
