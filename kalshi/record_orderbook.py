"""
Record Kalshi orderbook updates for one or more market tickers.

Captures every websocket message (snapshots + deltas) to a JSONL file so the
orderbook can be replayed with exact timing later.

Usage:
    python -m kalshi.record_orderbook KXNBAGAME-26MAR30PHIMIA-PHI KXNBAGAME-26MAR30PHIMIA-MIA
    python -m kalshi.record_orderbook --output my_recording.jsonl TICKER1 TICKER2

Each line in the output file is:
    {"wall_ms": <unix_ms>, "mono_ns": <monotonic_ns>, "raw": <original_message>}

wall_ms  – wall-clock time for correlation with external events
mono_ns  – monotonic clock for precise inter-message replay timing
raw      – the message exactly as received from Kalshi (string)
"""

import argparse
import asyncio
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets
from dotenv import load_dotenv

from .authentification import (
    load_private_key_from_file,
    sign_pss_text,
)

# ── Paths & env ──────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_KEY_PATH = PROJECT_ROOT / "trading-system" / "python_app" / "kalshi_client" / "kalshi_key.pem"
WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"

DATA_DIR = Path(__file__).resolve().parent / "data" / "orderbook_recordings"


# ── Auth ─────────────────────────────────────────────────────────────────────

def _make_ws_headers(private_key, key_id: str) -> dict:
    timestamp = str(int(time.time() * 1000))
    msg = timestamp + "GET" + WS_PATH
    signature = sign_pss_text(private_key, msg)
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }


# ── Recorder ─────────────────────────────────────────────────────────────────

async def record(tickers: list[str], output_path: Path, key_id: str, private_key):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    count = 0
    attempt = 0

    with open(output_path, "a") as f:
        # Write a header comment with metadata
        meta = {
            "type": "meta",
            "tickers": tickers,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "wall_ms": int(time.time() * 1000),
            "mono_ns": time.monotonic_ns(),
        }
        f.write(json.dumps(meta) + "\n")
        f.flush()

        while not stop.is_set():
            attempt += 1
            backoff = min(10.0, 0.25 * attempt)
            try:
                headers = _make_ws_headers(private_key, key_id)
                try:
                    ws = await websockets.connect(WS_URL, additional_headers=headers)
                except TypeError:
                    ws = await websockets.connect(WS_URL, extra_headers=headers)

                print(f"[recorder] connected to {WS_URL}")
                attempt = 0

                # Subscribe to orderbook_delta for all tickers
                sub = {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["orderbook_delta"],
                        "market_tickers": tickers,
                    },
                }
                await ws.send(json.dumps(sub))
                print(f"[recorder] subscribed: {tickers}")

                try:
                    async for msg in ws:
                        if stop.is_set():
                            break
                        if isinstance(msg, (bytes, bytearray)):
                            msg = msg.decode("utf-8", errors="ignore")

                        record_line = json.dumps({
                            "wall_ms": int(time.time() * 1000),
                            "mono_ns": time.monotonic_ns(),
                            "raw": msg,
                        })
                        f.write(record_line + "\n")
                        count += 1

                        if count % 100 == 0:
                            f.flush()
                            print(f"[recorder] {count} messages captured")
                finally:
                    await ws.close()

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[recorder] error: {e!r} (reconnect in {backoff:.1f}s)")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass

        # Final flush
        f.flush()

    print(f"[recorder] done — {count} messages written to {output_path}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Record Kalshi orderbook updates for replay",
    )
    parser.add_argument(
        "tickers",
        nargs="+",
        help="Market tickers to record (e.g. KXNBAGAME-26MAR30PHIMIA-PHI)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output JSONL file path (default: auto-generated in kalshi/data/orderbook_recordings/)",
    )
    parser.add_argument(
        "--key-id",
        default=os.getenv("KALSHI_API_KEY"),
        help="Kalshi API key ID (default: $KALSHI_API_KEY)",
    )
    parser.add_argument(
        "--pem",
        default=str(DEFAULT_KEY_PATH),
        help="Path to private key PEM file",
    )
    args = parser.parse_args()

    if not args.key_id:
        parser.error("KALSHI_API_KEY env var not set and --key-id not provided")

    private_key = load_private_key_from_file(args.pem)

    # Build output path
    if args.output:
        output_path = Path(args.output)
    else:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Use first ticker as label, truncate if many
        label = args.tickers[0].split("-")[-1] if len(args.tickers) == 1 else f"{len(args.tickers)}tickers"
        output_path = DATA_DIR / f"ob_{ts}_{label}.jsonl"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[recorder] tickers: {args.tickers}")
    print(f"[recorder] output:  {output_path}")
    print(f"[recorder] Ctrl+C to stop\n")

    asyncio.run(record(args.tickers, output_path, args.key_id, private_key))


if __name__ == "__main__":
    main()
