"""
Historical orderbook replay from captured JSONL orderbook recordings.

Replays the exact orderbook snapshots and deltas that were captured from
Kalshi's websocket, giving precise book state at every point in time.

Also supports play-by-play data synced to the replay clock.
"""

import asyncio
import csv
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .ws_api import ws_manager


@dataclass
class ReplayEvent:
    wall_ms: int       # original wall-clock timestamp (unix ms)
    ticker: str        # market ticker
    event_type: str    # "snapshot" or "delta"
    data: dict         # parsed msg content from Kalshi


@dataclass
class PBPPlay:
    timestamp: float   # unix seconds (wallclock)
    text: str
    away_score: int
    home_score: int
    period: str
    clock: str
    scoring: bool


def _parse_ts(ts_str: str) -> float | None:
    ts_str = ts_str.strip()
    if not ts_str:
        return None
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        # Normalize fractional seconds to 6 digits for Python < 3.11 compat
        if "." in ts_str:
            dot_idx = ts_str.index(".")
            end_idx = dot_idx + 1
            while end_idx < len(ts_str) and ts_str[end_idx].isdigit():
                end_idx += 1
            frac = ts_str[dot_idx + 1:end_idx]
            frac = frac.ljust(6, "0")[:6]
            ts_str = ts_str[:dot_idx + 1] + frac + ts_str[end_idx:]
        if "+" not in ts_str and "-" not in ts_str[10:]:
            ts_str = ts_str + "+00:00"
        dt = datetime.fromisoformat(ts_str)
        return dt.timestamp()
    except Exception:
        return None


class ReplayEngine:
    def __init__(self):
        self.events: list[ReplayEvent] = []
        self.pbp_plays: list[PBPPlay] = []
        self.index: int = 0
        self.pbp_index: int = 0
        self.playing: bool = False
        self.paused: bool = False
        self.speed: float = 1.0
        self.pbp_offset_sec: float = 0.0  # negative = PBP fires earlier (simulates faster feed)
        self._stop_event = asyncio.Event()
        self._metadata: dict = {}
        self._base_wall_ms: int = 0
        self._real_start: float = 0
        self._current_wall_ms: int = 0
        # Shadow book state: ticker -> {"yes": {price_cents: size}, "no": {price_cents: size}}
        self._book_state: dict[str, dict[str, dict[int, int]]] = {}

    @property
    def total_events(self) -> int:
        return len(self.events)

    @property
    def _current_ts(self) -> float:
        """Current replay time as unix seconds (for PBP sync)."""
        return self._current_wall_ms / 1000 if self._current_wall_ms else 0

    # ── Loading ──────────────────────────────────────────────────────────────

    def load(self, filepath: str, metadata: dict | None = None):
        """Load and parse a JSONL orderbook recording file."""
        self.events.clear()
        self.index = 0
        self.playing = False
        self.paused = False
        self._current_wall_ms = 0
        self._book_state.clear()
        self._metadata = {}

        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Replay file not found: {filepath}")

        tickers: set[str] = set()

        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Meta line
                if record.get("type") == "meta":
                    self._metadata["started_at"] = record.get("started_at", "")
                    continue

                wall_ms = record.get("wall_ms")
                raw_str = record.get("raw", "")
                if not wall_ms or not raw_str:
                    continue

                try:
                    raw = json.loads(raw_str.strip())
                except json.JSONDecodeError:
                    continue

                msg_type = raw.get("type")
                msg = raw.get("msg", {})
                ticker = msg.get("market_ticker", "")

                if msg_type == "orderbook_snapshot" and ticker:
                    tickers.add(ticker)
                    self.events.append(ReplayEvent(
                        wall_ms=wall_ms,
                        ticker=ticker,
                        event_type="snapshot",
                        data=msg,
                    ))
                elif msg_type == "orderbook_delta" and ticker:
                    tickers.add(ticker)
                    self.events.append(ReplayEvent(
                        wall_ms=wall_ms,
                        ticker=ticker,
                        event_type="delta",
                        data=msg,
                    ))

        self._metadata["total_events"] = len(self.events)
        self._metadata["tickers"] = sorted(tickers)

        # Apply any caller-provided metadata (from games.json)
        if metadata:
            self._metadata.update(metadata)

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

    # ── Metadata & PBP queries ───────────────────────────────────────────────

    def get_metadata(self) -> dict:
        return self._metadata

    def get_plays_up_to_now(self, limit: int = 20) -> list[dict]:
        if not self.pbp_plays or self._current_ts == 0:
            return []
        effective_ts = self._current_ts - self.pbp_offset_sec
        while self.pbp_index < len(self.pbp_plays) and self.pbp_plays[self.pbp_index].timestamp <= effective_ts:
            self.pbp_index += 1
        start = max(0, self.pbp_index - limit)
        return [
            {"text": p.text, "away_score": p.away_score, "home_score": p.home_score,
             "period": p.period, "clock": p.clock, "scoring": p.scoring}
            for p in self.pbp_plays[start:self.pbp_index]
        ]

    def get_current_score(self) -> dict:
        if not self.pbp_plays or self._current_ts == 0:
            return {"away_score": 0, "home_score": 0, "period": "", "clock": ""}
        effective_ts = self._current_ts - self.pbp_offset_sec
        last = None
        for p in self.pbp_plays:
            if p.timestamp > effective_ts:
                break
            last = p
        if last:
            return {"away_score": last.away_score, "home_score": last.home_score,
                    "period": last.period, "clock": last.clock}
        return {"away_score": 0, "home_score": 0, "period": "", "clock": ""}

    # ── Shadow state ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_fp_levels(dollars_fp_list: list) -> dict[int, int]:
        """Convert [["0.55", "100.00"], ...] to {55: 100, ...}"""
        levels: dict[int, int] = {}
        for price_str, size_str in dollars_fp_list:
            price_cents = int(round(float(price_str) * 100))
            size = int(float(size_str))
            if price_cents > 0 and size > 0:
                levels[price_cents] = size
        return levels

    def _apply_event(self, event: ReplayEvent):
        """Apply a single event to the shadow book state."""
        ticker = event.ticker
        if ticker not in self._book_state:
            self._book_state[ticker] = {"yes": {}, "no": {}}
        state = self._book_state[ticker]

        if event.event_type == "snapshot":
            state["yes"] = self._parse_fp_levels(event.data.get("yes_dollars_fp", []))
            state["no"] = self._parse_fp_levels(event.data.get("no_dollars_fp", []))

        elif event.event_type == "delta":
            side = event.data.get("side", "")
            price_str = event.data.get("price_dollars", "")
            delta_str = event.data.get("delta_fp", "")
            if side and price_str and delta_str:
                price_cents = int(round(float(price_str) * 100))
                delta = int(float(delta_str))
                current = state.get(side, {}).get(price_cents, 0)
                new_size = current + delta
                if new_size <= 0:
                    state[side].pop(price_cents, None)
                else:
                    state[side][price_cents] = new_size

    def _sync_book(self, ticker: str, book):
        """Sync an Orderbook from shadow state (suppresses callbacks)."""
        if not book or ticker not in self._book_state:
            return
        state = self._book_state[ticker]
        saved = book._on_delta
        book._on_delta = None
        book.load_mm_state(state["yes"], state["no"])
        book._on_delta = saved

    # ── WS message builders ──────────────────────────────────────────────────

    def _make_snapshot_msg(self, ticker: str) -> dict:
        """Build sim-format snapshot from shadow state."""
        state = self._book_state.get(ticker, {"yes": {}, "no": {}})
        yes = sorted([[p, s] for p, s in state["yes"].items()], key=lambda x: x[0], reverse=True)
        no = sorted([[p, s] for p, s in state["no"].items()], key=lambda x: x[0], reverse=True)
        return {
            "type": "orderbook_snapshot",
            "seq": ws_manager._next_seq(ticker),
            "msg": {"market_ticker": ticker, "yes": yes, "no": no},
        }

    def _make_delta_msg(self, event: ReplayEvent) -> dict:
        """Build sim-format delta from a captured delta event."""
        price_cents = int(round(float(event.data.get("price_dollars", "0")) * 100))
        delta = int(float(event.data.get("delta_fp", "0")))
        return {
            "type": "orderbook_delta",
            "seq": ws_manager._next_seq(event.ticker),
            "msg": {
                "market_ticker": event.ticker,
                "price": price_cents,
                "delta": delta,
                "side": event.data.get("side", ""),
            },
        }

    # ── Playback ─────────────────────────────────────────────────────────────

    async def play(self, books: dict, snapshot_feeder=None):
        """Replay events with proper timing, syncing Orderbooks and broadcasting."""
        if not self.events:
            return

        self.playing = True
        self.paused = False
        self._stop_event.clear()

        first = self.events[self.index]
        self._base_wall_ms = first.wall_ms
        self._real_start = time.time()
        self._current_wall_ms = first.wall_ms

        while self.index < len(self.events) and not self._stop_event.is_set():
            if self.paused:
                await asyncio.sleep(0.05)
                continue

            event = self.events[self.index]

            # Wait for proper timing
            offset_s = (event.wall_ms - self._base_wall_ms) / 1000 / self.speed
            elapsed_s = time.time() - self._real_start
            wait_s = offset_s - elapsed_s
            if wait_s > 0.005:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=wait_s)
                    if self._stop_event.is_set():
                        break
                except asyncio.TimeoutError:
                    pass

            self._current_wall_ms = event.wall_ms

            # Apply to shadow state and sync Orderbook
            self._apply_event(event)
            book = books.get(event.ticker)
            self._sync_book(event.ticker, book)

            # Broadcast to ws subscribers
            if event.event_type == "snapshot":
                msg = self._make_snapshot_msg(event.ticker)
            else:
                msg = self._make_delta_msg(event)
            await ws_manager._send_to_subs("orderbook_delta", event.ticker, msg)

            # Advance PBP snapshot feeder to current time (with offset)
            if snapshot_feeder:
                await snapshot_feeder.advance_to(self._current_ts - self.pbp_offset_sec)

            self.index += 1

        self.playing = False
        # Disconnect feeder when replay ends
        if snapshot_feeder:
            await snapshot_feeder.disconnect()

    def seek(self, target_index: int, books: dict):
        """Seek to a specific event index by replaying shadow state from start."""
        target_index = max(0, min(target_index, len(self.events) - 1))

        # Rebuild shadow state from scratch
        self._book_state.clear()
        for i in range(target_index + 1):
            self._apply_event(self.events[i])

        self.index = target_index
        self._current_wall_ms = self.events[target_index].wall_ms if self.events else 0
        self.pbp_index = 0

        # Sync all orderbooks
        for ticker in list(self._book_state.keys()):
            book = books.get(ticker)
            self._sync_book(ticker, book)

        # Adjust timing for continued playback
        if self.playing:
            self._base_wall_ms = self._current_wall_ms
            self._real_start = time.time()

    def seek_by_time_offset(self, offset_sec: float, books: dict):
        """Seek forward/backward by a time offset in seconds from current position."""
        if not self.events or self._current_wall_ms == 0:
            return
        target_ms = self._current_wall_ms + int(offset_sec * 1000)
        # Binary search for the closest event
        lo, hi = 0, len(self.events) - 1
        best = self.index
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.events[mid].wall_ms <= target_ms:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        self.seek(best, books)

    def pause(self):
        self.paused = True

    def resume(self):
        if self.playing and self.paused:
            self._base_wall_ms = self._current_wall_ms
            self._real_start = time.time()
            self.paused = False

    def stop(self):
        self._stop_event.set()
        self.playing = False
        self.paused = False
        self.index = 0
        self.pbp_index = 0
        self._current_wall_ms = 0
        self._book_state.clear()

    def get_status(self) -> dict:
        total = len(self.events)
        return {
            "playing": self.playing,
            "paused": self.paused,
            "speed": self.speed,
            "pbp_offset_sec": self.pbp_offset_sec,
            "progress_pct": (self.index / total * 100) if total else 0,
            "current_index": self.index,
            "total_events": total,
            "events_played": self.index,
            "metadata": self._metadata,
        }
