"""NBA live game update endpoints.

Architecture: Client (iOS app) is source of truth.
Client sends full state snapshots. Server persists, runs GAM inference,
enriches with model predictions, and broadcasts to consumers.
"""

import asyncio
import json
import time
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

from .game_cache import load_game, save_game, list_games, delete_game
from .espn import lookup_espn_game_id, fetch_roster, parse_ticker
from .engine import start_engine, stop_engine, list_engines, get_engine, _load_historical_roster
from ..settings import settings

router = APIRouter(prefix="/nba", tags=["nba"])


def _roster_has_data(roster: dict) -> bool:
    if roster.get("home") or roster.get("away"):
        return True
    if roster.get("injuries", {}).get("home") or roster.get("injuries", {}).get("away"):
        return True
    if roster.get("leaders", {}).get("home") or roster.get("leaders", {}).get("away"):
        return True
    return False


# ---------- broadcast infrastructure ----------

_subscribers: dict[str, set[WebSocket]] = {}
_sub_lock = asyncio.Lock()


async def _broadcast(game_id: str, state: dict):
    async with _sub_lock:
        subs = list(_subscribers.get(game_id, []))
    dead = []
    for ws in subs:
        try:
            await ws.send_json(state)
        except Exception:
            dead.append(ws)
    if dead:
        async with _sub_lock:
            for ws in dead:
                _subscribers.get(game_id, set()).discard(ws)


def _enrich_with_engine(game_id: str, state: dict) -> dict:
    """If an engine is running for this game, run inference and enrich the state."""
    engine = get_engine(game_id)
    if engine and engine.is_live:
        return engine.on_snapshot(state)
    return state


# ---------- input websocket (mobile client) ----------

@router.websocket("/ws/input/{game_id}")
async def ws_game_input(ws: WebSocket, game_id: str):
    """Mobile client connects here. Sends full state snapshots.

    On connect: server sends last persisted state (enriched with model predictions).
    Messages from client:
        {"action": "setup", "home_team": "LAL", "away_team": "BOS"}
        {"action": "snapshot", ...full snapshot...}
        {"action": "stop"}
    """
    await ws.accept()
    state = load_game(game_id)
    await ws.send_json(state)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"error": "invalid json"})
                continue

            action = msg.get("action")

            if action == "setup":
                state["home_team"] = msg.get("home_team")
                state["away_team"] = msg.get("away_team")
                # Fetch roster if not cached
                if "roster" not in state or not state.get("roster"):
                    try:
                        espn_id = lookup_espn_game_id(game_id)
                        if espn_id:
                            state["espn_game_id"] = espn_id
                            state["roster"] = await fetch_roster(
                                espn_id,
                                home_team=state.get("home_team", ""),
                                away_team=state.get("away_team", ""),
                            )
                    except Exception:
                        pass
                save_game(state)
                await ws.send_json(state)
                await _broadcast(game_id, state)

            elif action == "snapshot":
                # Client sends full state — persist, run inference, broadcast
                msg.pop("action", None)
                # Preserve roster/espn data from server state
                msg["roster"] = state.get("roster")
                msg["espn_game_id"] = state.get("espn_game_id")
                msg["game_id"] = game_id
                state = msg
                save_game(state)

                # Run GAM inference and enrich before broadcasting
                enriched = _enrich_with_engine(game_id, state)
                await _broadcast(game_id, enriched)

            elif action == "stop":
                delete_game(game_id)
                stopped = {"game_id": game_id, "stopped": True}
                await ws.send_json(stopped)
                await _broadcast(game_id, stopped)
                return

            else:
                await ws.send_json({"error": f"unknown action: {action}"})

    except WebSocketDisconnect:
        pass


# ---------- consumer websocket (trading GUI) ----------

@router.websocket("/ws/feed/{game_id}")
async def ws_game_feed(ws: WebSocket, game_id: str):
    """Read-only feed. Sends current state on connect (enriched), then live updates."""
    await ws.accept()
    state = load_game(game_id)
    enriched = _enrich_with_engine(game_id, state)
    await ws.send_json(enriched)

    async with _sub_lock:
        _subscribers.setdefault(game_id, set()).add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with _sub_lock:
            _subscribers.get(game_id, set()).discard(ws)


# ---------- REST endpoints ----------

@router.get("/games")
async def get_games():
    return [load_game(gid) for gid in list_games()]


@router.get("/games/{game_id}")
async def get_game(game_id: str):
    state = load_game(game_id)
    return _enrich_with_engine(game_id, state)


@router.post("/games/{game_id}/stop")
async def stop_game(game_id: str):
    delete_game(game_id)
    stopped = {"game_id": game_id, "stopped": True}
    await _broadcast(game_id, stopped)
    return {"ok": True}


@router.get("/games/{game_id}/roster")
async def get_roster(game_id: str):
    state = load_game(game_id)

    espn_id = lookup_espn_game_id(game_id)
    if not espn_id:
        raise HTTPException(404, "Could not resolve ESPN game_id from ticker")

    home_team = state.get("home_team", "")
    away_team = state.get("away_team", "")
    if not home_team or not away_team:
        parsed = parse_ticker(game_id)
        if parsed:
            home_team = home_team or parsed["home"]
            away_team = away_team or parsed["away"]

    # In sim mode, always use historical roster (skip cache from live ESPN)
    if settings.sim_mode:
        hist = _load_historical_roster(espn_id, away_team, home_team)
        if hist:
            roster = {
                "home": hist["home"],
                "away": hist["away"],
                "injuries": {"home": [], "away": []},
                "leaders": {"home": [], "away": []},
                "predictor": None,
                "odds": None,
            }
            state["espn_game_id"] = espn_id
            state["roster"] = roster
            save_game(state)
            return {"espn_game_id": espn_id, "roster": roster}

    # Live mode: check cache first
    cached = state.get("roster")
    if cached and _roster_has_data(cached):
        return {"espn_game_id": state.get("espn_game_id"), "roster": cached}

    try:
        roster = await fetch_roster(espn_id, home_team=home_team, away_team=away_team)
    except Exception as e:
        raise HTTPException(502, f"ESPN API error: {e}")

    state["espn_game_id"] = espn_id
    state["roster"] = roster
    save_game(state)
    return {"espn_game_id": espn_id, "roster": roster}


@router.delete("/games/{game_id}")
async def remove_game(game_id: str):
    if not delete_game(game_id):
        raise HTTPException(404, "game not found")
    return {"ok": True}


# ---------- trading engine ----------

class EngineStartRequest(BaseModel):
    game_id: str
    kalshi_ticker: str


@router.post("/engine/start")
async def engine_start(req: EngineStartRequest):
    """Start the trading engine for a game. Loads prior + posterior models."""
    engine = await start_engine(req.game_id, req.kalshi_ticker)
    return engine.status()


@router.post("/engine/stop/{game_id}")
async def engine_stop(game_id: str):
    if not stop_engine(game_id):
        raise HTTPException(404, "engine not running for this game")
    return {"ok": True}


@router.get("/engine/status")
async def engine_status():
    return list_engines()


@router.get("/engine/status/{game_id}")
async def engine_status_single(game_id: str):
    engine = get_engine(game_id)
    if not engine:
        raise HTTPException(404, "engine not running for this game")
    return engine.status()


# ---------- trading controls ----------

class TradingParamsRequest(BaseModel):
    min_size: Optional[int] = None
    max_size: Optional[int] = None
    max_position: Optional[int] = None
    max_exposure: Optional[int] = None
    fee_rate: Optional[float] = None
    delta_scale: Optional[float] = None
    min_delta: Optional[float] = None
    delta_full_scale: Optional[float] = None
    aggression: Optional[int] = None
    enabled: Optional[bool] = None


class SetMarketsRequest(BaseModel):
    game_id: str
    home_ticker: str
    away_ticker: str


@router.post("/trading/params/{game_id}")
async def update_trading_params(game_id: str, req: TradingParamsRequest):
    """Update trading parameters for a running engine."""
    engine = get_engine(game_id)
    if not engine:
        raise HTTPException(404, "engine not running for this game")
    p = engine.trader.params
    if req.min_size is not None:
        p.min_size = req.min_size
    if req.max_size is not None:
        p.max_size = req.max_size
    if req.max_position is not None:
        p.max_position = req.max_position
    if req.max_exposure is not None:
        p.max_exposure = req.max_exposure
    if req.fee_rate is not None:
        p.fee_rate = req.fee_rate
    if req.delta_scale is not None:
        p.delta_scale = req.delta_scale
    if req.min_delta is not None:
        p.min_delta = req.min_delta
    if req.delta_full_scale is not None:
        p.delta_full_scale = req.delta_full_scale
    if req.aggression is not None:
        p.aggression = req.aggression
    if req.enabled is not None:
        p.enabled = req.enabled
    return engine.trader.to_dict()


@router.post("/trading/enable/{game_id}")
async def enable_trading(game_id: str):
    engine = get_engine(game_id)
    if not engine:
        raise HTTPException(404, "engine not running for this game")
    engine.trader.params.enabled = True
    engine.trader.last_evaluated_wp = None  # force re-evaluation on next snapshot
    return {"enabled": True}


@router.post("/trading/disable/{game_id}")
async def disable_trading(game_id: str):
    engine = get_engine(game_id)
    if not engine:
        raise HTTPException(404, "engine not running for this game")
    engine.trader.params.enabled = False
    return {"enabled": False}


@router.post("/trading/markets")
async def set_trading_markets(req: SetMarketsRequest):
    engine = get_engine(req.game_id)
    if not engine:
        raise HTTPException(404, "engine not running for this game")
    engine.set_market_tickers(req.home_ticker, req.away_ticker)
    return {"ok": True}


@router.post("/trading/prices/{game_id}")
async def update_trading_prices(game_id: str, prices: dict):
    """Push best bid/ask from frontend orderbook to the engine."""
    engine = get_engine(game_id)
    if not engine:
        raise HTTPException(404, "engine not running for this game")
    engine.trader.home_best_ask = prices.get("homeAsk")
    engine.trader.away_best_ask = prices.get("awayAsk")
    engine.trader.home_best_bid = prices.get("homeBid")
    engine.trader.away_best_bid = prices.get("awayBid")
    return {"ok": True}


@router.get("/trading/state/{game_id}")
async def get_trading_state(game_id: str):
    engine = get_engine(game_id)
    if not engine:
        raise HTTPException(404, "engine not running for this game")
    return engine.trader.to_dict()
