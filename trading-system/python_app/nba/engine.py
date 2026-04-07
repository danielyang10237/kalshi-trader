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
from .game_cache import load_game, save_game
from . import live_inference
from .trader import TraderState, TradingParams, cancel_and_requote
from ..settings import settings

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
_NBA_DATA_DIR = Path(__file__).parent.parent.parent.parent / "nba" / "data"
_PREDICTIONS_FILE = _NBA_DATA_DIR / "games_predictions_deploy.csv"
_PLAYER_STATS_DIR = _NBA_DATA_DIR / "player_stats"
_GAME_ROSTERS_DIR = _NBA_DATA_DIR / "game_rosters"

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


def _lookup_historical_prior(espn_game_id: str) -> tuple[float | None, float | None]:
    """Look up stored priors from games_predictions_deploy.csv.

    Returns (prior_home_wp, kalshi_pregame_wp).
    """
    if not _PREDICTIONS_FILE.exists():
        return None, None
    with open(_PREDICTIONS_FILE) as f:
        for row in csv.DictReader(f):
            if row.get("game_id") == espn_game_id:
                try:
                    xgb = float(row["prior_home_wp"])
                except (ValueError, KeyError):
                    xgb = None
                try:
                    kalshi = float(row["kalshi_pregame_wp"])
                except (ValueError, KeyError):
                    kalshi = None
                return xgb, kalshi
    return None, None


def _load_historical_roster(espn_game_id: str, away_team: str, home_team: str) -> dict | None:
    """Load roster from nba/data/game_rosters/ for a historical game.

    Returns dict matching fetch_roster format: {"home": [...], "away": [...]}
    where each player is {"id": str, "name": str, "position": str, "jersey": str}.
    """
    # Search across year subdirectories for the matching file
    for year_dir in sorted(_GAME_ROSTERS_DIR.iterdir(), reverse=True):
        if not year_dir.is_dir():
            continue
        # Filename pattern: {game_id}_{away}_{home}.csv
        for f in year_dir.iterdir():
            if f.name.startswith(espn_game_id) and f.suffix == ".csv":
                result = {"home": [], "away": []}
                with open(f) as fh:
                    for row in csv.DictReader(fh):
                        if row.get("did_not_play", "").upper() == "TRUE":
                            continue
                        side = row.get("home_away", "")
                        if side not in ("home", "away"):
                            continue
                        result[side].append({
                            "id": row.get("athlete_id", ""),
                            "name": row.get("athlete_display_name", ""),
                            "position": row.get("athlete_position_abbreviation", ""),
                            "jersey": row.get("athlete_jersey", ""),
                        })
                logger.info(f"Loaded historical roster from {f.name}: "
                           f"home={len(result['home'])}, away={len(result['away'])}")
                return result
    return None


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
        # Prior model predictions
        self.prior_home_wp: float | None = None      # XGBoost pre-game
        self.kalshi_pregame_wp: float | None = None   # Kalshi pre-game market price
        # Last model features (for GUI display)
        self.last_model_features: dict | None = None
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

        # Fetch roster — historical from game_rosters in sim mode, ESPN live otherwise
        espn_id = lookup_espn_game_id(self.game_id)
        if settings.sim_mode and espn_id:
            self.roster = _load_historical_roster(espn_id, self.away_team, self.home_team)
            if not self.roster:
                logger.warning(f"No historical roster found for {espn_id}, falling back to ESPN")
        if not self.roster and espn_id:
            try:
                self.roster = await fetch_roster(
                    espn_id,
                    home_team=self.home_team,
                    away_team=self.away_team,
                )
            except Exception as e:
                logger.warning(f"Failed to fetch roster from ESPN: {e}")

        self._build_quality_lookup()
        self._restore_persisted_priors()
        self._compute_prior()
        self._compute_pregame_features()
        self._persist_priors()

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
        """Get P(home_win) — from stored predictions in sim mode, XGBoost live otherwise."""
        # In sim mode, use the stored historical priors if available
        if settings.sim_mode:
            espn_id = lookup_espn_game_id(self.game_id)
            if espn_id:
                xgb_prior, kalshi_prior = _lookup_historical_prior(espn_id)
                if xgb_prior is not None:
                    self.prior_home_wp = xgb_prior
                    self.kalshi_pregame_wp = kalshi_prior
                    logger.info(
                        f"Prior (historical): XGBoost={self.prior_home_wp:.4f}"
                        f"{f' Kalshi={self.kalshi_pregame_wp:.4f}' if self.kalshi_pregame_wp else ''}"
                    )
                    return
                logger.warning(f"No stored prior for {espn_id}, falling back to XGBoost")

        # Live mode or fallback: run XGBoost
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

    def _restore_persisted_priors(self):
        """Restore priors from persisted game state if available.

        Called before _compute_prior so that persisted values are used as
        defaults — _compute_prior can still overwrite them.
        """
        state = load_game(self.game_id)
        restored = False
        if state.get("_prior_home_wp") is not None and self.prior_home_wp is None:
            self.prior_home_wp = state["_prior_home_wp"]
            restored = True
        if state.get("_kalshi_pregame_wp") is not None and self.kalshi_pregame_wp is None:
            self.kalshi_pregame_wp = state["_kalshi_pregame_wp"]
            restored = True
        if restored:
            logger.info(
                f"[{self.game_id}] Restored persisted priors: "
                f"xgb={self.prior_home_wp} kalshi_pre={self.kalshi_pregame_wp}"
            )

    def _persist_priors(self):
        """Save priors to the game state JSON so they survive restarts."""
        state = load_game(self.game_id)
        changed = False
        if self.prior_home_wp is not None and state.get("_prior_home_wp") != self.prior_home_wp:
            state["_prior_home_wp"] = self.prior_home_wp
            changed = True
        if self.kalshi_pregame_wp is not None and state.get("_kalshi_pregame_wp") != self.kalshi_pregame_wp:
            state["_kalshi_pregame_wp"] = self.kalshi_pregame_wp
            changed = True
        if changed:
            save_game(state)
            logger.info(f"[{self.game_id}] Persisted priors to disk")

    def on_snapshot(self, snapshot: dict) -> dict:
        """Process a live snapshot: run dual-prior GAM inference, cancel-and-requote.

        Called on every snapshot received from the iOS client.
        Returns the snapshot dict with model and trader metadata attached.
        """
        if not self.is_live or self.prior_home_wp is None:
            return snapshot

        # ── Handle delta reset (after seek/skip in sim replay) ──
        if snapshot.get("delta_reset"):
            self.trader.prev_p_kalshi = None
            self.trader.last_model_delta = None
            self.trader.last_expected_move = None
            self.trader.last_direction = None
            logger.info(f"[{self.game_id}] Delta baseline reset (seek)")

        # ── Computed posterior (XGBoost prior) — always run for display ──
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
        except Exception as e:
            logger.error(f"GAM inference (computed prior) failed: {e}")

        # ── Kalshi posterior (market-price prior) — only if we have market data ──
        p_kalshi = None
        market_mid = None
        bid = self.trader.home_best_bid
        ask = self.trader.home_best_ask
        if bid is not None and ask is not None:
            market_mid = (bid + ask) / 2.0
            market_prob = max(0.01, min(0.99, market_mid / 100.0))
            try:
                p_kalshi = live_inference.predict_home_wp(
                    snapshot=snapshot,
                    prior_home_wp=market_prob,
                    rest_diff=self.rest_diff,
                    roster_quality_diff=self.roster_quality_diff,
                    win_pct_diff=self.win_pct_diff,
                    home_team=self.home_team,
                    away_team=self.away_team,
                )
            except Exception as e:
                logger.error(f"GAM inference (Kalshi prior) failed: {e}")

        if self.snapshot_count % 10 == 1:
            score = f"{snapshot.get('home', {}).get('score', 0)}-{snapshot.get('away', {}).get('score', 0)}"
            logger.info(
                f"[{self.game_id}] snapshot #{self.snapshot_count} | "
                f"score={score} | p_computed={self.home_wp:.4f}"
                f"{f' | p_kalshi={p_kalshi:.4f}' if p_kalshi is not None else ''}"
                f"{f' | mid={market_mid:.0f}' if market_mid is not None else ''}"
            )

        # ── Enrich snapshot ──
        enriched = dict(snapshot)
        enriched["home_wp"] = round(self.home_wp, 4) if self.home_wp is not None else None
        enriched["prior_home_wp"] = round(self.prior_home_wp, 4)

        # ── Attach model features for GUI display ──
        try:
            _feats = live_inference.snapshot_to_features(
                snapshot, self.prior_home_wp,
                rest_diff=self.rest_diff,
                roster_quality_diff=self.roster_quality_diff,
                win_pct_diff=self.win_pct_diff,
                home_team=self.home_team,
                away_team=self.away_team,
            )
            # Round for display, drop internal keys
            self.last_model_features = {
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in _feats.items() if not k.startswith("_")
            }
            enriched["model_features"] = self.last_model_features
        except Exception:
            enriched["model_features"] = None
        enriched["kalshi_pregame_wp"] = round(self.kalshi_pregame_wp, 4) if self.kalshi_pregame_wp is not None else None
        enriched["p_kalshi"] = round(p_kalshi, 4) if p_kalshi is not None else None

        home_score = snapshot.get("home", {}).get("score", 0)
        away_score = snapshot.get("away", {}).get("score", 0)
        enriched["_debug"] = {
            "engine_home_team": self.home_team,
            "engine_away_team": self.away_team,
            "home_score": home_score,
            "away_score": away_score,
            "score_diff": home_score - away_score,
            "prior_home_wp": round(self.prior_home_wp, 4) if self.prior_home_wp else None,
            "home_wp": round(self.home_wp, 4) if self.home_wp else None,
            "p_kalshi": round(p_kalshi, 4) if p_kalshi is not None else None,
            "market_mid": market_mid,
        }

        # ── Always store posteriors on trader for dashboard display ──
        if p_kalshi is not None:
            self.trader.last_p_kalshi = p_kalshi
        if self.home_wp is not None:
            self.trader.last_p_computed = self.home_wp
        if p_kalshi is not None and market_mid is not None:
            self.trader.last_theo = round(p_kalshi * 100)

        # ── Delta-based trading ──
        if (p_kalshi is not None
                and self.home_wp is not None
                and market_mid is not None):

            # Compute model delta (change in Kalshi posterior since last snapshot)
            prev = self.trader.prev_p_kalshi
            if prev is not None:
                model_delta = p_kalshi - prev
                self.trader.last_model_delta = model_delta
                # kalshi_pre = current market mid (market hasn't repriced yet)
                kalshi_pre = market_mid

                if self.trader.params.enabled:
                    trade_result = cancel_and_requote(
                        self.trader, p_kalshi, self.home_wp,
                        model_delta, kalshi_pre,
                    )
                    if trade_result:
                        enriched["last_trade"] = trade_result
                else:
                    # Not trading, but still track delta for display
                    expected_move = model_delta * self.trader.params.delta_scale
                    self.trader.last_expected_move = expected_move
                    self.trader.last_fair = kalshi_pre + expected_move * 100
                    self.trader.last_direction = (
                        "BUY" if model_delta > self.trader.params.min_delta
                        else "SELL" if model_delta < -self.trader.params.min_delta
                        else "SKIP"
                    )

            # Store current p_kalshi as prev for next snapshot
            self.trader.prev_p_kalshi = p_kalshi

        enriched["trader"] = self.trader.to_dict()
        return enriched

    def on_orderbook_update(self, ticker: str, best_bid: Optional[int], best_ask: Optional[int]):
        """Update best bid/ask prices from orderbook snapshot. Called by WS proxy."""
        if ticker == self.trader.home_ticker:
            self.trader.home_best_bid = best_bid
            self.trader.home_best_ask = best_ask
            # Capture first home orderbook mid as Kalshi pre-game prior (live mode)
            if self.kalshi_pregame_wp is None and best_bid is not None and best_ask is not None:
                self.kalshi_pregame_wp = (best_bid + best_ask) / 200.0
                logger.info(f"[{self.game_id}] Kalshi pre-game prior captured: {self.kalshi_pregame_wp:.4f}")
                self._persist_priors()
        elif ticker == self.trader.away_ticker:
            self.trader.away_best_bid = best_bid
            self.trader.away_best_ask = best_ask

    def on_fill(self, fill: dict):
        """Process a fill notification. Position is recomputed from cache on next trade eval."""
        pass  # fills_cache already persists it; position recomputed in cancel_and_requote

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
            "kalshi_pregame_wp": round(self.kalshi_pregame_wp, 4) if self.kalshi_pregame_wp is not None else None,
            "home_wp": round(self.home_wp, 4) if self.home_wp is not None else None,
            "snapshot_count": self.snapshot_count,
            "model_features": self.last_model_features,
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
