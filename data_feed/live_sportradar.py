import json
import os
import time
import random
from pathlib import Path
from typing import Any, Dict, Optional, Callable

import requests
from dotenv import load_dotenv

# Load .env from project root (parent of data_feed/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class SportradarPushClient:
    """
    Sportradar Push API consumer (HTTP streaming).
    Reads line-delimited JSON from the subscribe endpoint and calls handler(payload_dict).
    """

    def __init__(
        self,
        api_key: str,
        access_level: str = "trial",   # "trial" or "production"
        language_code: str = "en",
        timeout_seconds: int = 60,
        user_agent: str = "SportsData/1.0",
    ):
        self.api_key = api_key
        self.access_level = access_level
        self.language_code = language_code
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

        self.base_url = (
            f"https://api.sportradar.com/nfl/official/{self.access_level}/"
            f"stream/{self.language_code}/events/subscribe"
        )

        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def stream_events(
        self,
        handler: Callable[[Dict[str, Any]], None],
        *,
        status: Optional[str] = "inprogress",
        match: Optional[str] = None,          # e.g. "sd:match:<uuid>"
        team: Optional[str] = None,           # e.g. "sd:team:<uuid>"
        event_category: Optional[str] = None, # redzone, scoring_play, etc.
        event_type: Optional[str] = None,     # timeout, period_end, etc.
        locale: Optional[str] = None,         # "en"
        raw_line_handler: Optional[Callable[[str], None]] = None,  # optional debug
    ) -> None:
        """
        Connects and consumes the push stream. Reconnects on errors until stopped.
        """
        params: Dict[str, str] = {}
        if status:
            params["status"] = status
        if match:
            params["match"] = match
        if team:
            params["team"] = team
        if event_category:
            params["event_category"] = event_category
        if event_type:
            params["event_type"] = event_type
        if locale:
            params["locale"] = locale

        headers = {
            "accept": "application/json",
            "User-Agent": self.user_agent,
            "x-api-key": self.api_key,
        }

        backoff = 1.0
        max_backoff = 30.0

        while not self._stop:
            try:
                with requests.get(
                    self.base_url,
                    headers=headers,
                    params=params,
                    stream=True,
                    timeout=self.timeout_seconds,
                ) as resp:
                    resp.raise_for_status()
                    backoff = 1.0  # reset after successful connect

                    # iter_lines handles chunk boundaries; decode_unicode=True gives str
                    for line in resp.iter_lines(decode_unicode=True):
                        if self._stop:
                            return

                        if line is None:
                            continue

                        line = line.strip()

                        # Heartbeats often arrive as empty lines or whitespace
                        if not line:
                            continue

                        if raw_line_handler:
                            raw_line_handler(line)

                        # Most Sportradar push feeds are JSON per line
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            # Some providers occasionally send non-JSON heartbeat tokens.
                            # If that happens, you can log it and continue.
                            continue

                        handler(payload)

            except requests.HTTPError as e:
                # Auth issues show up here (401/403), or bad access_level path
                print(f"[push] HTTP error: {e}")
            except requests.RequestException as e:
                # network error / timeout / disconnect
                print(f"[push] Connection error: {e}")
            except Exception as e:
                print(f"[push] Unexpected error: {e}")

            if self._stop:
                return

            # Exponential backoff + jitter before reconnect
            sleep_for = min(max_backoff, backoff) * (0.8 + 0.4 * random.random())
            print(f"[push] Reconnecting in {sleep_for:.1f}s...")
            time.sleep(sleep_for)
            backoff *= 2


# --- Example usage: live play-by-play for all in-progress NFL games ---
def handle_event(payload: Dict[str, Any]) -> None:
    # You’ll want to inspect payload structure once you see real samples.
    # A common pattern is payload includes identifiers + an event object.
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    api_key = os.getenv("SPORT_RADAR_API_KEY")
    
    if not api_key:
        raise RuntimeError(
            "Missing SPORT_RADAR_API_KEY in your .env or environment."
        )

    client = SportradarPushClient(
        api_key=api_key,
        access_level="production",
        language_code="en",
        timeout_seconds=90,
    )

    # Stream events for a specific match
    client.stream_events(
        handler=handle_event,
        match="sd:match:9a2084f0-146c-417c-a94d-b26012539d1e",
        # Optional filters:
        # status="inprogress",
        # event_category="scoring_play",
    )