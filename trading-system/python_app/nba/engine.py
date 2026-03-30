"""NBA trading engine.

Manages lifecycle: start → fetch roster → build quality lookup → load models → live inference.
On each snapshot from the iOS client, runs the posterior GAM to compute P(home_win).
"""

import csv
import logging
import sys
from pathlib import Path
from typing import Optional

from .espn import fetch_roster, lookup_espn_game_id, parse_ticker, _load_static_roster
from . import live_inference
from .trader import TraderState, TradingParams, evaluate_and_trade

# Add nba/ to path so we can import build_prediction_row
_NBA_DIR = Path(__file__).parent.parent.parent.parent / "nba" / "scripts"
if str(_NBA_DIR) not in sys.path:
    sys.path.insert(0, str(_NBA_DIR))

import build_prediction_row  # noqa: E402 — eager import for fast engine starts
build_prediction_row.warmup()
live_inference.warmup()

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"
_ROSTER_DIR = _DATA_DIR / "nba_roster"
# Player stats live in the nba research repo
_PLAYER_STATS_DIR = Path(__file__).parent.parent.parent.parent / "nba" / "data" / "player_stats"

MIN_MINUTES_FOR_RATE = 5.0  # match training pipeline
PRIOR_AVG_WINDOW = 5       # games for weighting players by minutes
ROLLING_WINDOW = 10        # player stat window


def _load_player_stats(player_id: str) -> list[dict]:
    """Load a player's game log from player_stats/{id}.csv, most recent first."""
    path = _PLAYER_STATS_DIR / f"{player_id}.csv"
    if not path.exists():
        return []
    with open(path) as f:
        rows = list(csv.DictReader(f))
    # Sort by game_date descending
    rows.sort(key=lambda r: r.get("game_date", ""), reverse=True)
    return rows


def _parse_minutes(val: str) -> float:
    """Parse minutes string — handles '23.5' and '23:30' formats."""
    if not val:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        pass
    if ":" in val:
        parts = val.split(":")
        try:
            return float(parts[0]) + float(parts[1]) / 60
        except (ValueError, TypeError, IndexError):
            pass
    return 0.0


def _get_prior_avg_minutes(player_id: str) -> float:
    """Average minutes over the player's last 5 games (minutes > 0).

    Matches training: get_prior_avg_minutes() in build_prediction_row.py.
    """
    rows = _load_player_stats(player_id)
    if not rows:
        return 0.0
    mins_list = []
    for row in rows:
        m = _parse_minutes(row.get("minutes", ""))
        if m > 0:
            mins_list.append(m)
        if len(mins_list) >= PRIOR_AVG_WINDOW:
            break
    return sum(mins_list) / len(mins_list) if mins_list else 0.0


def _get_player_pm_rate(player_id: str) -> float:
    """Compute a player's mean per-minute PLUS_MINUS over last 10 qualifying games.

    Matches training: for each game with minutes >= 5, compute pm / minutes,
    then take the simple mean across games.
    """
    rows = _load_player_stats(player_id)
    if not rows:
        return 0.0

    rates = []
    for row in rows:
        if len(rates) >= ROLLING_WINDOW:
            break
        dnp = row.get("did_not_play", "False")
        if dnp == "True":
            continue
        mins = _parse_minutes(row.get("minutes", ""))
        if mins < MIN_MINUTES_FOR_RATE:
            continue
        pm_str = row.get("plus_minus", "")
        if not pm_str:
            continue
        try:
            pm = float(pm_str)
        except (ValueError, TypeError):
            continue
        rates.append(pm / mins)

    return sum(rates) / len(rates) if rates else 0.0


class NbaEngine:
    """One engine instance per game. Holds roster, quality lookup, prior, and live inference."""

    def __init__(self, game_id: str, kalshi_ticker: str):
        self.game_id = game_id
        self.kalshi_ticker = kalshi_ticker
        self.is_live = False
        self.roster: Optional[dict] = None
        self.quality_lookup: dict[str, dict] = {}
        self.home_team: str = ""
        self.away_team: str = ""
        self.home_quality: float = 0.0
        self.away_quality: float = 0.0
        self.roster_quality_diff: float = 0.0
        # Prior model prediction
        self.prior_home_wp: float | None = None
        # Pre-game features (computed once, reused every snapshot)
        self.rest_diff: float = 0.0
        self.win_pct_diff: float = 0.0
        # Live inference state
        self.home_wp: float | None = None  # latest posterior P(home_win)
        self.snapshot_count: int = 0
        # Trading
        self.trader = TraderState()

    async def start(self):
        """Start the engine: resolve teams, fetch roster, build quality lookup, compute prior."""
        parsed = parse_ticker(self.game_id)
        if parsed:
            self.away_team = parsed["away"]
            self.home_team = parsed["home"]

        # Fetch roster from ESPN
        espn_id = lookup_espn_game_id(self.game_id)
        if espn_id:
            try:
                self.roster = await fetch_roster(
                    espn_id,
                    home_team=self.home_team,
                    away_team=self.away_team,
                )
            except Exception as e:
                logger.warning(f"Failed to fetch roster from ESPN: {e}")

        self._build_quality_lookup()
        self._compute_prior()
        self._compute_pregame_features()

        self.is_live = True
        logger.info(
            f"NbaEngine started for {self.game_id} | {self.away_team}@{self.home_team} | "
            f"roster_quality_diff={self.roster_quality_diff:.4f} | "
            f"prior_home_wp={self.prior_home_wp} | "
            f"players={len(self.quality_lookup)}"
        )

    def _build_quality_lookup(self):
        """Build player quality lookup matching the training pipeline.

        Training methodology (build_prediction_row.py / data_preprocess.ipynb):
        1. Each player's weight = prior_avg_minutes(last 5 games) / sum(all weights)
           (weights normalised to sum to 1.0 per team)
        2. Per-player stat = mean of (plus_minus / minutes) across last 10 games
           where minutes >= 5.0
        3. Team roster_quality = sum(weight * per_player_pm_rate)
        4. roster_quality_diff = home - away
        """
        self.quality_lookup = {}
        team_quality = {"home": 0.0, "away": 0.0}

        for side, abbr in [("home", self.home_team), ("away", self.away_team)]:
            if not abbr:
                continue

            if self.roster and self.roster.get(side):
                players = self.roster[side]
            else:
                players = _load_static_roster(abbr)

            # Step 1: get prior avg minutes for each player (for weighting)
            player_data = []
            for p in players:
                pid = str(p.get("id", ""))
                name = p.get("name", "")
                prior_mins = _get_prior_avg_minutes(pid)
                player_data.append((pid, name, prior_mins))

            # Filter to players with prior minutes > 0
            weighted_players = [(pid, name, pm) for pid, name, pm in player_data if pm > 0]
            total_prior_mins = sum(pm for _, _, pm in weighted_players)

            side_quality = 0.0
            if total_prior_mins > 0:
                for pid, name, prior_mins in weighted_players:
                    weight = prior_mins / total_prior_mins  # normalised to sum=1
                    pm_rate = _get_player_pm_rate(pid)
                    self.quality_lookup[name] = {
                        "quality": pm_rate,
                        "weight": round(weight, 4),
                        "prior_mins": round(prior_mins, 1),
                        "side": side,
                        "player_id": pid,
                    }
                    side_quality += weight * pm_rate

            # Also add players with no prior minutes (weight=0, no contribution)
            for pid, name, prior_mins in player_data:
                if prior_mins <= 0 and name not in self.quality_lookup:
                    self.quality_lookup[name] = {
                        "quality": 0.0,
                        "weight": 0.0,
                        "prior_mins": 0.0,
                        "side": side,
                        "player_id": pid,
                    }

            team_quality[side] = side_quality

        self.home_quality = team_quality["home"]
        self.away_quality = team_quality["away"]
        self.roster_quality_diff = self.home_quality - self.away_quality
        logger.info(
            f"roster_quality_diff={self.roster_quality_diff:.4f} "
            f"(home={self.home_quality:.4f}, away={self.away_quality:.4f})"
        )

    def _compute_prior(self):
        """Run the prior XGBoost model to get P(home_win)."""
        try:
            home_roster = self.roster.get("home", []) if self.roster else []
            away_roster = self.roster.get("away", []) if self.roster else []

            if not home_roster and self.home_team:
                home_roster = _load_static_roster(self.home_team)
            if not away_roster and self.away_team:
                away_roster = _load_static_roster(self.away_team)

            self.prior_home_wp = build_prediction_row.predict(
                ticker=self.game_id,
                home_team=self.home_team,
                away_team=self.away_team,
                home_roster=home_roster,
                away_roster=away_roster,
            )
            logger.info(f"Prior model: P(home_win) = {self.prior_home_wp:.4f}")
        except Exception as e:
            logger.error(f"Failed to compute prior prediction: {e}")
            self.prior_home_wp = None

    def _compute_pregame_features(self):
        """Compute pre-game features that stay constant for the whole game."""
        baselines = live_inference._season_baselines
        h = baselines.get(self.home_team, {})
        a = baselines.get(self.away_team, {})
        self.win_pct_diff = h.get("win_pct", 0.5) - a.get("win_pct", 0.5)

        # rest_diff: extracted from build_prediction_row if available
        try:
            row = build_prediction_row.build_feature_row(
                self.game_id, self.home_team, self.away_team, [], []
            )
            self.rest_diff = row.get("home_days_rest", 0) - row.get("away_days_rest", 0)
        except Exception:
            self.rest_diff = 0.0

    def on_snapshot(self, snapshot: dict) -> dict:
        """Process a live snapshot: run GAM inference, return enriched state.

        Called on every snapshot received from the iOS client.
        Returns the snapshot dict with `home_wp` and `model` metadata attached.
        """
        if not self.is_live or self.prior_home_wp is None:
            return snapshot

        try:
            self.home_wp = live_inference.predict_home_wp(
                snapshot=snapshot,
                prior_home_wp=self.prior_home_wp,
                rest_diff=self.rest_diff,
                roster_quality_diff=self.roster_quality_diff,
                win_pct_diff=self.win_pct_diff,
                home_team=self.home_team,
                away_team=self.away_team,
            )
            self.snapshot_count += 1

            if self.snapshot_count % 10 == 1:
                score = f"{snapshot.get('home', {}).get('score', 0)}-{snapshot.get('away', {}).get('score', 0)}"
                logger.info(
                    f"[{self.game_id}] snapshot #{self.snapshot_count} | "
                    f"score={score} | home_wp={self.home_wp:.4f}"
                )
        except Exception as e:
            logger.error(f"GAM inference failed: {e}")

        # Enrich the snapshot
        enriched = dict(snapshot)
        enriched["home_wp"] = round(self.home_wp, 4) if self.home_wp is not None else None
        enriched["prior_home_wp"] = round(self.prior_home_wp, 4)

        # Debug: include raw feature info in broadcast for frontend debugging
        home_score = snapshot.get("home", {}).get("score", 0)
        away_score = snapshot.get("away", {}).get("score", 0)
        enriched["_debug"] = {
            "engine_home_team": self.home_team,
            "engine_away_team": self.away_team,
            "snapshot_home_team": snapshot.get("home_team"),
            "snapshot_away_team": snapshot.get("away_team"),
            "home_score": home_score,
            "away_score": away_score,
            "score_diff": home_score - away_score,
            "prior_home_wp": round(self.prior_home_wp, 4) if self.prior_home_wp else None,
            "home_wp": round(self.home_wp, 4) if self.home_wp else None,
        }

        # Run trading evaluation only when wp changes enough and trading is enabled
        if (self.home_wp is not None
                and self.trader.params.enabled
                and self.trader.should_evaluate(self.home_wp)):
            logger.debug(
                f"[{self.game_id}] wp changed: "
                f"{self.trader.last_evaluated_wp} -> {self.home_wp:.4f}, evaluating trade"
            )
            self.trader.last_evaluated_wp = self.home_wp
            trade_result = evaluate_and_trade(self.trader, self.home_wp)
            if trade_result:
                enriched["last_trade"] = trade_result

        enriched["trader"] = self.trader.to_dict()
        return enriched

    def on_orderbook_update(self, ticker: str, best_ask: Optional[int]):
        """Update best ask price from orderbook delta. Called by WS proxy."""
        if ticker == self.trader.home_ticker:
            self.trader.home_best_ask = best_ask
        elif ticker == self.trader.away_ticker:
            self.trader.away_best_ask = best_ask

    def on_fill(self, fill: dict):
        """Process a fill notification. Position is recomputed from cache on next trade eval."""
        pass  # fills_cache already persists it; position recomputed in evaluate_and_trade

    def set_market_tickers(self, home_ticker: str, away_ticker: str):
        """Set the Kalshi market tickers for home/away YES contracts."""
        self.trader.home_ticker = home_ticker
        self.trader.away_ticker = away_ticker
        logger.info(f"[{self.game_id}] Market tickers: home={home_ticker} away={away_ticker}")

    def stop(self):
        """Stop the engine."""
        self.is_live = False
        self.trader.params.enabled = False
        logger.info(f"NbaEngine stopped for {self.game_id} | {self.snapshot_count} snapshots processed")

    def status(self) -> dict:
        """Return current engine status."""
        return {
            "game_id": self.game_id,
            "kalshi_ticker": self.kalshi_ticker,
            "is_live": self.is_live,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "roster_loaded": self.roster is not None,
            "players_tracked": len(self.quality_lookup),
            "roster_quality_diff": round(self.roster_quality_diff, 4),
            "home_quality": round(self.home_quality, 4),
            "away_quality": round(self.away_quality, 4),
            "prior_home_wp": round(self.prior_home_wp, 4) if self.prior_home_wp is not None else None,
            "home_wp": round(self.home_wp, 4) if self.home_wp is not None else None,
            "snapshot_count": self.snapshot_count,
            "trader": self.trader.to_dict(),
        }


# --------------- global engine registry ---------------

_engines: dict[str, NbaEngine] = {}


def get_engine(game_id: str) -> Optional[NbaEngine]:
    return _engines.get(game_id)


def list_engines() -> list[dict]:
    return [e.status() for e in _engines.values()]


async def start_engine(game_id: str, kalshi_ticker: str) -> NbaEngine:
    if game_id in _engines and _engines[game_id].is_live:
        return _engines[game_id]
    engine = NbaEngine(game_id, kalshi_ticker)
    await engine.start()
    _engines[game_id] = engine
    return engine


def stop_engine(game_id: str) -> bool:
    engine = _engines.pop(game_id, None)
    if engine:
        engine.stop()
        return True
    return False
