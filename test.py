#!/usr/bin/env python3
"""
Sportradar NCAAFB push-feed ingester (events/subscribe).

Usage examples:
  export SPORTRADAR_API_KEY="YOUR_KEY"
  python sportradar_ncaafb_stream.py --access-level trial --language en --match "sd:match:9a2084f0-146c-417c-a94d-b26012539d1e"

  # With additional query params (example explained in Sportradar docs / sample):
  python sportradar_ncaafb_stream.py --status inprogress --match "sd:match:673b459c-7506-4c11-9273-1b9502537f1d"

  # Add arbitrary params:
  python sportradar_ncaafb_stream.py --param foo=bar --param baz=qux
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterator, Optional
from urllib.parse import urljoin

import requests

import dotenv
dotenv.load_dotenv()

API_KEY = os.getenv("SPORT_RADAR_API_KEY")

@dataclass(frozen=True)
class StreamConfig:
    api_key: str
    access_level: str = "trial"     # trial | production
    language_code: str = "en"       # en
    base_url: str = "https://api.sportradar.com/ncaafb"
    connect_timeout_s: int = 10
    read_timeout_s: int = 60        # for the initial HTTP request; stream read is continuous
    max_backoff_s: int = 60


class SportradarStreamError(RuntimeError):
    pass


def build_subscribe_url(cfg: StreamConfig) -> str:
    return f"{cfg.base_url}/{cfg.access_level}/stream/{cfg.language_code}/events/subscribe"


def subscribe_and_get_redirect(
    cfg: StreamConfig,
    params: Dict[str, str],
) -> str:
    """
    Calls the subscribe endpoint and returns the redirect Location (stream URL).
    """
    headers = {"x-api-key": cfg.api_key}

    url = build_subscribe_url(cfg)
    resp = requests.get(
        url,
        headers=headers,
        params=params,
        allow_redirects=False,
        timeout=(cfg.connect_timeout_s, cfg.read_timeout_s),
    )

    # Sportradar typically returns a redirect for the stream URL.
    if resp.status_code not in (301, 302, 303, 307, 308):
        raise SportradarStreamError(
            f"Expected redirect from subscribe endpoint, got {resp.status_code}: {resp.text[:500]}"
        )

    location = resp.headers.get("Location")
    if not location:
        raise SportradarStreamError("Redirect response missing Location header.")

    # Location may be absolute or relative.
    return urljoin(url, location)


def open_stream(cfg: StreamConfig, stream_url: str) -> requests.Response:
    """
    Opens the streaming connection (newline-delimited JSON).
    """
    headers = {'x-api-key': cfg.api_key}
    resp = requests.get(
        stream_url,
        headers=headers,
        stream=True,
        timeout=(cfg.connect_timeout_s, None),  # no read timeout for a continuous stream
    )
    resp.raise_for_status()
    return resp


def iter_events(resp: requests.Response) -> Iterator[dict]:
    """
    Yields decoded JSON events from a streaming response.
    """
    for line in resp.iter_lines(decode_unicode=True):
        # Keep-alives are often empty lines
        if not line:
            continue

        # Some streams may include stray whitespace
        line = line.strip()
        if not line:
            continue

        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            # If the provider ever sends non-JSON lines, log and skip
            print(f"[warn] Non-JSON line: {line[:200]}", file=sys.stderr)


def run_forever(cfg: StreamConfig, params: Dict[str, str]) -> None:
    """
    Connects, consumes events, and reconnects on failure.
    """
    backoff = 1

    while True:
        try:
            redirect_url = subscribe_and_get_redirect(cfg, params)
            print(f"[info] Subscribed. Streaming from: {redirect_url}", file=sys.stderr)

            with open_stream(cfg, redirect_url) as stream_resp:
                backoff = 1  # reset after successful connection
                for event in iter_events(stream_resp):
                    # Do your ingestion here: write to DB, queue, file, etc.
                    # For now, print as compact JSON:
                    print(json.dumps(event, separators=(",", ":"), ensure_ascii=False))

        except (requests.RequestException, SportradarStreamError) as e:
            print(f"[error] Stream error: {e}", file=sys.stderr)
            print(f"[info] Reconnecting in {backoff}s...", file=sys.stderr)
            time.sleep(backoff)
            backoff = min(cfg.max_backoff_s, backoff * 2)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sportradar NCAAFB events stream ingester")
    p.add_argument("--api-key", default=API_KEY, help="Sportradar API key (or set SPORTRADAR_API_KEY)")
    p.add_argument("--access-level", default="trial", choices=["trial", "production"])
    p.add_argument("--language", default="en")
    p.add_argument("--match", default=None, help='Optional: match id like "sd:match:...".')
    p.add_argument("--status", default=None, help='Optional: status filter (example: "inprogress").')
    p.add_argument(
        "--param",
        action="append",
        default=[],
        help="Extra query param in key=value form. Can be repeated.",
    )
    return p.parse_args()


def build_params(args: argparse.Namespace) -> Dict[str, str]:
    params: Dict[str, str] = {}

    if args.match:
        params["match"] = args.match
    if args.status:
        params["status"] = args.status

    for item in args.param:
        if "=" not in item:
            raise ValueError(f"--param must be key=value, got: {item}")
        k, v = item.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k:
            raise ValueError(f"Empty key in --param: {item}")
        params[k] = v

    return params


def main() -> None:
    args = parse_args()
    if not API_KEY:
        raise SystemExit("Missing API key. Pass --api-key or set SPORTRADAR_API_KEY.")

    cfg = StreamConfig(
        api_key=API_KEY,
        access_level=args.access_level,
        language_code=args.language,
    )

    params = build_params(args)
    run_forever(cfg, params)


if __name__ == "__main__":
    main()