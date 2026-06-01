"""
PBP snapshot feeder: accumulates box score stats from NBA PBP actions
and sends iOS-format snapshots to the trading system WebSocket.

During sim replay, advances through PBP actions in sync with the orderbook
timeline and pushes game state to the trading engine.
"""

import asyncio
import csv
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets

logger = logging.getLogger(__name__)


def _parse_ts(ts_str: str) -> float:
    """Parse ISO timestamp to unix seconds. Handles NBA's variable fractional seconds."""
    ts_str = ts_str.strip()
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    # Normalize fractional seconds to 6 digits for Python < 3.11 compat
    if "." in ts_str:
        dot_idx = ts_str.index(".")
        # Find where the fraction ends (before + or -)
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


def _parse_clock_seconds(clock_iso: str) -> int:
    """Parse NBA clock 'PT11M45.00S' to seconds remaining."""
    if not clock_iso or not clock_iso.startswith("PT"):
        return 0
    s = clock_iso[2:]  # strip PT
    minutes = 0
    seconds = 0.0
    if "M" in s:
        parts = s.split("M")
        minutes = int(float(parts[0]))
        s = parts[1]
    if s.endswith("S"):
        s = s[:-1]
    if s:
        seconds = float(s)
    return minutes * 60 + int(seconds)


class PBPAction:
    """A single NBA PBP action parsed for box score accumulation."""
    __slots__ = ("ts", "team", "action_type", "sub_type", "made",
                 "period", "clock_seconds", "score_home", "score_away",
                 "description", "possession")

    def __init__(self, raw: dict):
        self.ts = _parse_ts(raw.get("timeActual", "2000-01-01T00:00:00Z"))
        self.team = raw.get("teamTricode", "")
        self.action_type = raw.get("actionType", "")
        self.sub_type = raw.get("subType", "")
        self.made = raw.get("shotResult", "") == "Made"
        self.period = raw.get("period", 1)
        self.clock_seconds = _parse_clock_seconds(raw.get("clock", ""))
        self.score_home = int(raw.get("scoreHome", 0) or 0)
        self.score_away = int(raw.get("scoreAway", 0) or 0)
        self.description = raw.get("description", "")
        # possession teamTricode
        poss_id = raw.get("possession", 0)
        self.possession = ""  # resolved later by tricode matching


class BoxScore:
    """Running box score totals for one team."""
    def __init__(self):
        self.score = 0
        self.fgm = 0
        self.fga = 0
        self.fg3m = 0
        self.fg3a = 0
        self.ftm = 0
        self.fta = 0
        self.oreb = 0
        self.dreb = 0
        self.tov = 0
        self.stl = 0
        self.pf = 0
        self.timeouts_used = 0
        self.period_fouls: dict[int, int] = {}  # {quarter: foul_count}

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "fgm": self.fgm,
            "fga": self.fga,
            "fg3m": self.fg3m,
            "fg3a": self.fg3a,
            "ftm": self.ftm,
            "fta": self.fta,
            "oreb": self.oreb,
            "dreb": self.dreb,
            "tov": self.tov,
            "stl": self.stl,
            "pf": self.pf,
            "timeouts_used": self.timeouts_used,
            "period_fouls": {str(k): v for k, v in self.period_fouls.items()},
        }


class SnapshotFeeder:
    """Accumulates NBA PBP into box score snapshots and sends to trading system."""

    def __init__(self):
        self.actions: list[PBPAction] = []
        self.action_index: int = 0
        self.home_team: str = ""
        self.away_team: str = ""
        self.game_id: str = ""
        self.home_box = BoxScore()
        self.away_box = BoxScore()
        self.last_possession: str = ""  # "home" or "away"
        self.last_period: int = 1
        self.last_clock: int = 720  # 12:00
        self.pending_ft_signed: int = 0  # +N = home shooting N FTs, -N = away
        self.is_dead_ball: bool = False

        # Connection to trading system
        self.enabled: bool = False
        self.trading_ws_url: str = "ws://localhost:8000/nba/ws/input/"
        self._ws = None
        self._connected: bool = False
        self._setup_sent: bool = False

    def load_nba_pbp(self, filepath: str, home_team: str, away_team: str, game_id: str):
        """Load NBA PBP JSON and parse actions."""
        self.actions.clear()
        self.action_index = 0
        self.home_team = home_team
        self.away_team = away_team
        self.game_id = game_id
        self.home_box = BoxScore()
        self.away_box = BoxScore()
        self._setup_sent = False

        path = Path(filepath)
        if not path.exists():
            logger.warning(f"[feeder] PBP file not found: {filepath}")
            return

        with open(path) as f:
            data = json.load(f)

        raw_actions = data.get("game", {}).get("actions", [])
        for raw in raw_actions:
            self.actions.append(PBPAction(raw))

        # Sort by timestamp
        self.actions.sort(key=lambda a: a.ts)
        logger.info(f"[feeder] Loaded {len(self.actions)} PBP actions for {away_team}@{home_team}")

    def load_nba_pbp_csv(self, filepath: str, home_team: str, away_team: str, game_id: str):
        """Load the converted NBA PBP CSV (our format with wallclock, text, scores, etc.).

        Since the CSV doesn't have action types for box score accumulation,
        this only supports score/time tracking. For full box score, use load_nba_pbp() with JSON.
        """
        # For the CSV-only path, we can't accumulate box scores.
        # This is a fallback — prefer load_nba_pbp() with the raw JSON.
        logger.warning("[feeder] CSV PBP loaded — box score stats unavailable, scores only")
        self.actions.clear()
        self.action_index = 0
        self.home_team = home_team
        self.away_team = away_team
        self.game_id = game_id
        self.home_box = BoxScore()
        self.away_box = BoxScore()
        self._setup_sent = False

    async def connect(self):
        """Connect to the trading system WebSocket."""
        if not self.enabled or not self.game_id:
            return
        url = self.trading_ws_url + self.game_id
        try:
            self._ws = await websockets.connect(url)
            self._connected = True
            # Read the initial state the server sends back
            _ = await asyncio.wait_for(self._ws.recv(), timeout=5)
            logger.info(f"[feeder] Connected to trading system at {url}")
        except Exception as e:
            logger.error(f"[feeder] Failed to connect to trading system: {e}")
            self._ws = None
            self._connected = False

    async def disconnect(self):
        """Disconnect from the trading system."""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
        self._connected = False
        self._setup_sent = False

    async def _send(self, msg: dict):
        """Send a message to the trading system WebSocket."""
        if not self._ws or not self._connected:
            logger.info("[feeder] Not connected, attempting reconnect...")
            await self.connect()
        if not self._ws:
            return
        try:
            await self._ws.send(json.dumps(msg))
        except Exception as e:
            logger.warning(f"[feeder] Send failed: {e}")
            self._connected = False
            self._ws = None

    async def _ensure_setup(self):
        """Send the setup message once on first snapshot."""
        if self._setup_sent:
            return
        await self._send({
            "action": "setup",
            "home_team": self.home_team,
            "away_team": self.away_team,
        })
        self._setup_sent = True
        # Small delay for server to process setup
        await asyncio.sleep(0.1)

    def _apply_action(self, action: PBPAction):
        """Apply a single PBP action to the running box score."""
        team = action.team
        if not team:
            # Period start/end, etc. — update time only
            self.last_period = action.period
            self.last_clock = action.clock_seconds
            return

        is_home = team == self.home_team
        box = self.home_box if is_home else self.away_box

        at = action.action_type

        if at == "2pt":
            box.fga += 1
            self.is_dead_ball = False
            if action.made:
                box.fgm += 1
                box.score = action.score_home if is_home else action.score_away
                self.is_dead_ball = True  # dead ball after made basket
        elif at == "3pt":
            box.fga += 1
            box.fg3a += 1
            self.is_dead_ball = False
            if action.made:
                box.fgm += 1
                box.fg3m += 1
                box.score = action.score_home if is_home else action.score_away
                self.is_dead_ball = True
        elif at == "freethrow":
            box.fta += 1
            if action.made:
                box.ftm += 1
                box.score = action.score_home if is_home else action.score_away
            # Decrement pending FTs (toward zero)
            if self.pending_ft_signed > 0 and is_home:
                self.pending_ft_signed -= 1
            elif self.pending_ft_signed < 0 and not is_home:
                self.pending_ft_signed += 1
            # Still dead ball during FT sequence
            self.is_dead_ball = True
        elif at == "rebound":
            if action.sub_type == "offensive":
                box.oreb += 1
            else:
                box.dreb += 1
            self.is_dead_ball = False
        elif at == "turnover":
            box.tov += 1
            self.is_dead_ball = True
        elif at == "steal":
            box.stl += 1
            self.is_dead_ball = False
        elif at == "foul":
            box.pf += 1
            # Track period fouls
            period = action.period
            box.period_fouls[period] = box.period_fouls.get(period, 0) + 1
            self.is_dead_ball = True
            # Detect pending FTs by looking at sub_type
            # Shooting fouls award FTs to the OTHER team
            ft_count = 0
            sub = action.sub_type.lower() if action.sub_type else ""
            desc = action.description.lower() if action.description else ""
            if "shooting" in sub or "shooting" in desc:
                # Look ahead to count FTs (peek at upcoming actions)
                ft_count = self._count_upcoming_fts(is_home)
            elif "flagrant" in sub or "flagrant" in desc:
                ft_count = self._count_upcoming_fts(is_home)
            elif "technical" in sub or "technical" in desc:
                ft_count = 1  # technicals are 1 FT
            # FTs go to the OTHER team (the one that was fouled)
            if ft_count > 0:
                if is_home:
                    # Home fouled → away shoots
                    self.pending_ft_signed = -ft_count
                else:
                    # Away fouled → home shoots
                    self.pending_ft_signed = ft_count
        elif at == "timeout":
            box.timeouts_used += 1
            self.is_dead_ball = True

        # Always sync scores from the PBP data (authoritative)
        self.home_box.score = action.score_home
        self.away_box.score = action.score_away

        # Track possession and time
        self.last_period = action.period
        self.last_clock = action.clock_seconds
        if team == self.home_team:
            self.last_possession = "home"
        elif team == self.away_team:
            self.last_possession = "away"

    def _count_upcoming_fts(self, fouling_team_is_home: bool) -> int:
        """Peek ahead in the action list to count upcoming FT events."""
        count = 0
        for j in range(self.action_index, min(self.action_index + 8, len(self.actions))):
            a = self.actions[j]
            if a.action_type == "freethrow":
                count += 1
            elif a.action_type in ("2pt", "3pt", "rebound", "turnover", "steal"):
                break  # non-FT game action → stop counting
        return count

    @staticmethod
    def _compute_time_range(quarter: int, clock_seconds: int) -> str:
        """Compute the time_range bucket the iOS app uses.

        Q1-Q3: "12-9", "9-6", "6-3", "3-0"
        Q4:    "9-7", "7-5", "5-0"  (finer buckets in crunch time)
        """
        minutes_left = clock_seconds / 60
        if quarter <= 3:
            if minutes_left > 9:
                return "12-9"
            elif minutes_left > 6:
                return "9-6"
            elif minutes_left > 3:
                return "6-3"
            else:
                return "3-0"
        else:  # Q4
            if minutes_left > 9:
                return "12-9"
            elif minutes_left > 7:
                return "9-7"
            elif minutes_left > 5:
                return "7-5"
            else:
                return "5-0"

    def _build_snapshot(self) -> dict:
        """Build an iOS-format snapshot from current box score state."""
        return {
            "action": "snapshot",
            "game_id": self.game_id,
            "timestamp": time.time(),
            "home_team": self.home_team,
            "away_team": self.away_team,
            "possession": self.last_possession or None,
            "quarter": self.last_period,
            "time_range": self._compute_time_range(self.last_period, self.last_clock),
            "timer_seconds": self.last_clock,
            "stopped": False,
            "pending_ft_signed": self.pending_ft_signed,
            "is_dead_ball": self.is_dead_ball,
            "home": self.home_box.to_dict(),
            "away": self.away_box.to_dict(),
        }

    async def advance_to(self, current_ts: float):
        """Advance PBP to the given wall-clock time, sending snapshots for new plays.

        Called by the replay engine on each event to keep PBP in sync.
        """
        if not self.enabled or not self.actions:
            return

        sent_any = False
        while (self.action_index < len(self.actions)
               and self.actions[self.action_index].ts <= current_ts):
            action = self.actions[self.action_index]
            self._apply_action(action)
            self.action_index += 1
            sent_any = True

        if sent_any:
            snapshot = self._build_snapshot()
            score = f"{snapshot['away']['score']}-{snapshot['home']['score']}"
            print(f"[feeder] Q{snapshot['quarter']} {snapshot['timer_seconds']}s | "
                  f"{self.away_team} {score} {self.home_team} | action #{self.action_index}/{len(self.actions)}")
            # _send handles reconnection if needed
            await self._ensure_setup()
            await self._send(snapshot)

    def reset(self):
        """Reset box score and action index."""
        self.action_index = 0
        self.home_box = BoxScore()
        self.away_box = BoxScore()
        self.last_possession = ""
        self.last_period = 1
        self.last_clock = 720
        self.pending_ft_signed = 0
        self.is_dead_ball = False
        self._setup_sent = False

    async def rebuild_to(self, target_ts: float):
        """Reset and replay all PBP actions up to target_ts, then send a snapshot.

        Used after seek/skip to sync the feeder with the new replay position.
        Includes delta_reset flag so the trading engine resets its prev_p_kalshi
        baseline instead of computing a spurious delta from the old position.
        """
        if not self.enabled or not self.actions:
            return
        self.reset()
        # Replay all actions up to target time
        while (self.action_index < len(self.actions)
               and self.actions[self.action_index].ts <= target_ts):
            self._apply_action(self.actions[self.action_index])
            self.action_index += 1
        # Send the rebuilt snapshot with delta_reset flag
        snapshot = self._build_snapshot()
        snapshot["delta_reset"] = True
        score = f"{snapshot['away']['score']}-{snapshot['home']['score']}"
        print(f"[feeder] rebuild to Q{snapshot['quarter']} {snapshot['timer_seconds']}s | "
              f"{self.away_team} {score} {self.home_team} | action #{self.action_index}/{len(self.actions)}")
        await self._ensure_setup()
        await self._send(snapshot)

    def get_status(self) -> dict:
        return {
            "enabled": self.enabled,
            "connected": self._connected,
            "game_id": self.game_id,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "total_actions": len(self.actions),
            "actions_played": self.action_index,
            "trading_ws_url": self.trading_ws_url,
            "home_score": self.home_box.score,
            "away_score": self.away_box.score,
            "period": self.last_period,
            "clock": self.last_clock,
        }
