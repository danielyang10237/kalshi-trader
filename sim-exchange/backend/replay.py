"""
Historical orderbook replay from kalshi_live CSV files.

Also loads play-by-play data and syncs it to the replay clock.
"""

import asyncio
import csv
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import market_maker
from .orderbook import Orderbook
from .ws_api import ws_manager


@dataclass
class TradeEvent:
    timestamp: float  # unix seconds
    home_price: int | None  # cents (midpoint of high/low)
    home_volume: int
    away_price: int | None
    away_volume: int


@dataclass
class PBPPlay:
    timestamp: float  # unix seconds (wallclock)
    text: str
    away_score: int
    home_score: int
    period: str  # "1st Quarter", "OT1", etc.
    clock: str  # "11:39"
    scoring: bool


def _parse_ts(ts_str: str) -> float | None:
    ts_str = ts_str.strip()
    if not ts_str:
        return None
    try:
        if "+" in ts_str:
            dt = datetime.fromisoformat(ts_str)
        elif ts_str.endswith("Z"):
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(ts_str + "+00:00")
        return dt.timestamp()
    except Exception:
        return None


class ReplayEngine:
    def __init__(self):
        self.events: list[TradeEvent] = []
        self.pbp_plays: list[PBPPlay] = []
        self.index: int = 0
        self.pbp_index: int = 0
        self.playing: bool = False
        self.paused: bool = False
        self.speed: float = 1.0
        self._stop_event = asyncio.Event()
        self._metadata: dict = {}
        self._base_time: float = 0
        self._real_start: float = 0
        self._current_ts: float = 0
        # Configurable replay book params
        self.spread: int = 4  # cents total spread
        self.levels: int = 5
        self.volume_mult: float = 1.0  # multiplier on CSV volume

    @property
    def total_events(self) -> int:
        return len(self.events)

    def load(self, filepath: str):
        """Load and parse a kalshi_live CSV file."""
        self.events.clear()
        self.pbp_plays.clear()
        self.index = 0
        self.pbp_index = 0
        self.playing = False
        self.paused = False
        self._current_ts = 0

        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Replay file not found: {filepath}")

        with open(path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = _parse_ts(row.get("timestamp", ""))
                if ts is None:
                    continue

                home_high = row.get("home_high_cents", "")
                home_low = row.get("home_low_cents", "")
                home_vol = int(row.get("home_volume", "0") or "0")

                home_price = None
                if home_high and home_low:
                    try:
                        h = float(home_high)
                        l = float(home_low)
                        if h < 1.0:
                            h = round(h * 100)
                            l = round(l * 100)
                        home_price = int((h + l) / 2)
                    except ValueError:
                        pass

                away_high = row.get("away_high_cents", "")
                away_low = row.get("away_low_cents", "")
                away_vol = int(row.get("away_volume", "0") or "0")

                away_price = None
                if away_high and away_low:
                    try:
                        h = float(away_high)
                        l = float(away_low)
                        if h < 1.0:
                            h = round(h * 100)
                            l = round(l * 100)
                        away_price = int((h + l) / 2)
                    except ValueError:
                        pass

                if home_price is not None or away_price is not None:
                    self.events.append(TradeEvent(
                        timestamp=ts,
                        home_price=home_price,
                        home_volume=home_vol,
                        away_price=away_price,
                        away_volume=away_vol,
                    ))

        # Read metadata from first row
        if self.events:
            with open(path, "r") as f:
                reader = csv.DictReader(f)
                first_row = next(reader)
                self._metadata = {
                    "home_team": first_row.get("home_team", ""),
                    "away_team": first_row.get("away_team", ""),
                    "home_ticker": first_row.get("home_ticker", ""),
                    "away_ticker": first_row.get("away_ticker", ""),
                    "game_date": first_row.get("game_date", ""),
                    "total_events": len(self.events),
                }

    def load_pbp(self, filepath: str):
        """Load play-by-play CSV and sort by wallclock timestamp."""
        self.pbp_plays.clear()
        self.pbp_index = 0

        path = Path(filepath)
        if not path.exists():
            return

        with open(path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = _parse_ts(row.get("wallclock", ""))
                if ts is None:
                    continue
                text = row.get("text", "")
                if not text:
                    continue
                self.pbp_plays.append(PBPPlay(
                    timestamp=ts,
                    text=text,
                    away_score=int(row.get("away_score", "0") or "0"),
                    home_score=int(row.get("home_score", "0") or "0"),
                    period=row.get("period_display_value", ""),
                    clock=row.get("clock_display_value", ""),
                    scoring=row.get("scoring_play", "").upper() == "TRUE",
                ))

        self.pbp_plays.sort(key=lambda p: p.timestamp)

    def get_metadata(self) -> dict:
        return self._metadata

    def get_plays_up_to_now(self, limit: int = 20) -> list[dict]:
        """Get PBP plays that have occurred up to the current replay timestamp."""
        if not self.pbp_plays or self._current_ts == 0:
            return []

        # Advance pbp_index to current time
        while self.pbp_index < len(self.pbp_plays) and self.pbp_plays[self.pbp_index].timestamp <= self._current_ts:
            self.pbp_index += 1

        # Return the last N plays
        start = max(0, self.pbp_index - limit)
        plays = self.pbp_plays[start:self.pbp_index]
        return [
            {
                "text": p.text,
                "away_score": p.away_score,
                "home_score": p.home_score,
                "period": p.period,
                "clock": p.clock,
                "scoring": p.scoring,
            }
            for p in plays
        ]

    def get_current_score(self) -> dict:
        """Get the latest score at the current replay time."""
        if not self.pbp_plays or self._current_ts == 0:
            return {"away_score": 0, "home_score": 0, "period": "", "clock": ""}

        # Find the last play at or before current time
        last = None
        for p in self.pbp_plays:
            if p.timestamp > self._current_ts:
                break
            last = p

        if last:
            return {
                "away_score": last.away_score,
                "home_score": last.home_score,
                "period": last.period,
                "clock": last.clock,
            }
        return {"away_score": 0, "home_score": 0, "period": "", "clock": ""}

    def _seed_both(self, home_book: Orderbook | None, away_book: Orderbook | None, event: TradeEvent):
        """Seed both books from a trade event. Suppresses delta callbacks."""
        home_price = event.home_price
        if home_price is None and event.away_price is not None:
            home_price = 100 - event.away_price
        if home_price is None:
            return

        vol = max(int((event.home_volume + event.away_volume) * self.volume_mult), 20)
        for book, price in [(home_book, home_price), (away_book, 100 - home_price)]:
            if book:
                saved = book._on_delta
                book._on_delta = None
                market_maker.seed_from_trade(book, price, vol, spread=self.spread, num_levels=self.levels)
                book._on_delta = saved

    async def play(self, home_book: Orderbook | None, away_book: Orderbook | None):
        """Play back events, seeding both books."""
        if not self.events:
            return

        # Build a books dict for snapshot broadcasting
        books = {}
        if home_book:
            books[home_book.ticker] = home_book
        if away_book:
            books[away_book.ticker] = away_book

        self.playing = True
        self.paused = False
        self._stop_event.clear()

        # Seed initial books from first event
        self._seed_both(home_book, away_book, self.events[self.index])
        for ticker in books:
            await ws_manager.broadcast_snapshot_for(ticker, books)

        self._base_time = self.events[self.index].timestamp
        self._real_start = time.time()
        self._current_ts = self._base_time

        while self.index < len(self.events) and not self._stop_event.is_set():
            if self.paused:
                await asyncio.sleep(0.1)
                continue

            event = self.events[self.index]

            # Wait until the right time
            event_offset = (event.timestamp - self._base_time) / self.speed
            elapsed = time.time() - self._real_start
            wait_time = event_offset - elapsed
            if wait_time > 0:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=wait_time)
                    if self._stop_event.is_set():
                        break
                except asyncio.TimeoutError:
                    pass

            self._current_ts = event.timestamp
            self._seed_both(home_book, away_book, event)
            for ticker in books:
                await ws_manager.broadcast_snapshot_for(ticker, books)
            self.index += 1

        self.playing = False

    def seek(self, target_index: int, home_book: Orderbook | None, away_book: Orderbook | None):
        """Jump to a specific event index. Re-seeds both books."""
        target_index = max(0, min(target_index, len(self.events) - 1))
        self.index = target_index
        event = self.events[target_index]
        self._current_ts = event.timestamp
        self._seed_both(home_book, away_book, event)
        self.pbp_index = 0

        if self.playing:
            self._base_time = event.timestamp
            self._real_start = time.time()

        # Reset PBP index to match
        self.pbp_index = 0

        # If currently playing, adjust timing so playback continues from here
        if self.playing:
            self._base_time = event.timestamp
            self._real_start = time.time()

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        self._stop_event.set()
        self.playing = False
        self.paused = False
        self.index = 0
        self.pbp_index = 0
        self._current_ts = 0

    def get_status(self) -> dict:
        total = len(self.events)
        return {
            "playing": self.playing,
            "paused": self.paused,
            "speed": self.speed,
            "progress_pct": (self.index / total * 100) if total else 0,
            "current_index": self.index,
            "total_events": total,
            "events_played": self.index,
            "metadata": self._metadata,
            "spread": self.spread,
            "levels": self.levels,
            "volume_mult": self.volume_mult,
        }
