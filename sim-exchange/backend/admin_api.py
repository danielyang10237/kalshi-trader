"""
Admin API endpoints for the simulator GUI.
Mounted at /sim/ — NOT part of the Kalshi API surface.
"""

import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, Query

from . import market_maker
from .ws_api import ws_manager

DATA_CACHE = Path(__file__).parent.parent / "data_cache"

router = APIRouter()

# Set by server.py at startup
books = None  # dict[str, Orderbook]
account = None
config = None
replay_engine = None
get_or_create_book = None
snapshot_feeder = None
gui_clients: set[WebSocket] = set()


def init(b, acct, cfg, replay, get_book_fn, feeder=None):
    global books, account, config, replay_engine, get_or_create_book, snapshot_feeder
    books = b
    account = acct
    config = cfg
    replay_engine = replay
    get_or_create_book = get_book_fn
    snapshot_feeder = feeder


async def broadcast_gui_event(event: dict):
    msg = json.dumps(event)
    dead = set()
    for ws in gui_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    for ws in dead:
        gui_clients.discard(ws)


# =============================================================================
# Orderbook Controls
# =============================================================================

@router.get("/book")
async def get_book():
    """Full orderbook state for both tickers."""
    result = {"home_ticker": config.home_ticker, "away_ticker": config.away_ticker}
    for label, ticker in [("home", config.home_ticker), ("away", config.away_ticker)]:
        book = books.get(ticker)
        if book:
            result[label] = book.get_snapshot()
            result[f"{label}_best_ask"] = book.get_best_ask()
            result[f"{label}_best_bid"] = book.get_best_bid()
        else:
            result[label] = {"yes": [], "no": [], "market_ticker": ticker}
    return result


@router.post("/book/seed")
async def seed_book(request: Request):
    """Seed BOTH orderbooks with mirrored liquidity."""
    body = await request.json()
    midpoint = body.get("midpoint", 50)
    spread = body.get("spread", 4)
    depth = body.get("depth", 50)
    levels = body.get("levels", 5)

    home_ticker = config.home_ticker
    away_ticker = config.away_ticker

    # Seed home book
    if home_ticker:
        home_book = get_or_create_book(home_ticker)
        saved_cb = home_book._on_delta
        home_book._on_delta = None
        market_maker.seed_book(home_book, midpoint, spread, depth, levels)
        home_book._on_delta = saved_cb
        await ws_manager.broadcast_snapshot_for(home_ticker, books)

    # Seed away book with mirrored prices
    if away_ticker:
        away_book = get_or_create_book(away_ticker)
        saved_cb = away_book._on_delta
        away_book._on_delta = None
        away_midpoint = 100 - midpoint
        market_maker.seed_book(away_book, away_midpoint, spread, depth, levels)
        away_book._on_delta = saved_cb
        await ws_manager.broadcast_snapshot_for(away_ticker, books)

    await broadcast_gui_event({"type": "book_seeded", "midpoint": midpoint, "spread": spread})
    return {"success": True}


@router.post("/book/clear")
async def clear_book():
    """Remove all MM liquidity from both books."""
    for ticker in [config.home_ticker, config.away_ticker]:
        book = books.get(ticker)
        if book:
            saved_cb = book._on_delta
            book._on_delta = None
            book.clear_mm_orders()
            book._on_delta = saved_cb
            await ws_manager.broadcast_snapshot_for(ticker, books)
    await broadcast_gui_event({"type": "book_cleared"})
    return {"success": True}


# =============================================================================
# Account
# =============================================================================

@router.get("/account")
async def get_account():
    return {
        "balance": account.balance,
        "initial_balance": account.initial_balance,
        "positions": account.positions,
        "fills": account.fills[-50:],
        "resting_orders": len(account.resting_orders),
    }


@router.post("/account/reset")
async def reset_account():
    account.reset()
    for book in books.values():
        book.clear_mm_orders()
    await broadcast_gui_event({"type": "account_reset"})
    return {"success": True}


# =============================================================================
# Configuration
# =============================================================================

@router.get("/config")
async def get_config():
    return {
        "home_ticker": config.home_ticker,
        "away_ticker": config.away_ticker,
        "initial_balance": config.initial_balance,
        "port": config.port,
    }


@router.post("/config")
async def set_config(request: Request):
    body = await request.json()
    if "home_ticker" in body:
        config.home_ticker = body["home_ticker"]
        get_or_create_book(body["home_ticker"])
    if "away_ticker" in body:
        config.away_ticker = body["away_ticker"]
        get_or_create_book(body["away_ticker"])
    if "initial_balance" in body:
        config.initial_balance = body["initial_balance"]
    await broadcast_gui_event({"type": "config_updated"})
    return {"success": True}


# =============================================================================
# Games
# =============================================================================

@router.get("/games")
async def list_games():
    games_file = DATA_CACHE / "games.json"
    if not games_file.exists():
        return {"games": []}
    with open(games_file) as f:
        return {"games": json.load(f)}


@router.post("/games/select")
async def select_game(request: Request):
    body = await request.json()
    game_id = body.get("game_id", "")

    games_file = DATA_CACHE / "games.json"
    if not games_file.exists():
        return {"success": False, "error": "No games catalog found"}

    with open(games_file) as f:
        games = json.load(f)

    game = next((g for g in games if g["game_id"] == game_id), None)
    if not game:
        return {"success": False, "error": f"Game {game_id} not found"}

    config.home_ticker = game["home_ticker"]
    config.away_ticker = game["away_ticker"]
    get_or_create_book(game["home_ticker"])
    get_or_create_book(game["away_ticker"])

    account.reset()

    # Load orderbook recording (JSONL)
    replay_path = DATA_CACHE / game.get("orderbook_file", "")
    replay_loaded = False
    if replay_path.exists():
        game_meta = {
            "home_team": game.get("home_team", ""),
            "away_team": game.get("away_team", ""),
            "home_ticker": game.get("home_ticker", ""),
            "away_ticker": game.get("away_ticker", ""),
            "game_date": game.get("date", ""),
        }
        replay_engine.load(str(replay_path), metadata=game_meta)
        replay_loaded = True

    # Load play-by-play if available
    pbp_path = DATA_CACHE / game.get("pbp_file", "")
    if pbp_path.exists():
        replay_engine.load_pbp(str(pbp_path))

    # Load NBA PBP JSON for snapshot feeder (full box score accumulation)
    nba_pbp_path = DATA_CACHE / game.get("nba_pbp_json", "")
    if snapshot_feeder and nba_pbp_path.exists():
        snapshot_feeder.load_nba_pbp(
            str(nba_pbp_path),
            home_team=game.get("home_team", ""),
            away_team=game.get("away_team", ""),
            game_id=game.get("home_ticker", "").rsplit("-", 1)[0] if game.get("home_ticker") else "",
        )

    await broadcast_gui_event({"type": "game_selected", "game": game})

    return {
        "success": True,
        "game": game,
        "replay_loaded": replay_loaded,
        "total_events": replay_engine.total_events,
    }


# =============================================================================
# Trade Log
# =============================================================================

@router.get("/trades")
async def get_trades(limit: int = Query(100)):
    return {"orders": account.all_orders[-limit:]}


# =============================================================================
# Play-by-Play Feed
# =============================================================================

@router.get("/pbp")
async def get_pbp(limit: int = Query(20)):
    """Get PBP plays up to the current replay timestamp."""
    return {
        "plays": replay_engine.get_plays_up_to_now(limit=limit),
        "score": replay_engine.get_current_score(),
    }


# =============================================================================
# Replay
# =============================================================================

@router.post("/replay/load")
async def replay_load(request: Request):
    body = await request.json()
    filepath = body.get("filepath", "")
    try:
        replay_engine.load(filepath)
        return {"success": True, "metadata": replay_engine.get_metadata(), "total_events": replay_engine.total_events}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/replay/start")
async def replay_start(request: Request):
    body = await request.json()
    speed = body.get("speed", 1.0)
    replay_engine.speed = speed
    if replay_engine.playing and replay_engine.paused:
        replay_engine.resume()
    else:
        asyncio.create_task(replay_engine.play(books, snapshot_feeder=snapshot_feeder))
    return {"success": True}


@router.post("/replay/pause")
async def replay_pause():
    replay_engine.pause()
    return {"success": True}


@router.post("/replay/stop")
async def replay_stop():
    replay_engine.stop()
    if snapshot_feeder:
        snapshot_feeder.reset()
        await snapshot_feeder.disconnect()
    return {"success": True}


@router.get("/replay/status")
async def replay_status():
    return replay_engine.get_status()


@router.post("/replay/seek")
async def replay_seek(request: Request):
    """Seek to a specific event index."""
    body = await request.json()
    target = body.get("index", 0)
    replay_engine.seek(target, books)
    # Broadcast snapshots for all tickers after seek
    for ticker in [config.home_ticker, config.away_ticker]:
        if ticker:
            await ws_manager.broadcast_snapshot_for(ticker, books)
    # Rebuild feeder state to match new position
    if snapshot_feeder and snapshot_feeder.enabled:
        await snapshot_feeder.rebuild_to(replay_engine._current_ts)
    return {"success": True, "index": replay_engine.index}


@router.post("/replay/skip")
async def replay_skip(request: Request):
    """Skip forward or backward by seconds."""
    body = await request.json()
    offset_sec = body.get("offset_sec", 0)
    replay_engine.seek_by_time_offset(offset_sec, books)
    for ticker in [config.home_ticker, config.away_ticker]:
        if ticker:
            await ws_manager.broadcast_snapshot_for(ticker, books)
    # Rebuild feeder state to match new position
    if snapshot_feeder and snapshot_feeder.enabled:
        await snapshot_feeder.rebuild_to(replay_engine._current_ts)
    return {"success": True, "index": replay_engine.index}


@router.post("/replay/speed")
async def replay_speed(request: Request):
    body = await request.json()
    replay_engine.speed = body.get("speed", 1.0)
    return {"success": True, "speed": replay_engine.speed}


# =============================================================================
# Snapshot Feeder (send game state to trading system)
# =============================================================================

@router.get("/feeder/status")
async def feeder_status():
    if not snapshot_feeder:
        return {"enabled": False, "error": "feeder not initialized"}
    return snapshot_feeder.get_status()


@router.post("/replay/pbp_offset")
async def set_pbp_offset(request: Request):
    """Set PBP timestamp offset in seconds. Negative = PBP fires earlier (simulates faster feed)."""
    body = await request.json()
    offset = float(body.get("offset_sec", 0))
    replay_engine.pbp_offset_sec = offset
    return {"pbp_offset_sec": replay_engine.pbp_offset_sec}


@router.post("/feeder/toggle")
async def feeder_toggle(request: Request):
    """Enable/disable the snapshot feeder that sends game state to the trading system."""
    if not snapshot_feeder:
        return {"success": False, "error": "feeder not initialized"}
    body = await request.json()
    enable = body.get("enabled", not snapshot_feeder.enabled)
    if "trading_ws_url" in body:
        snapshot_feeder.trading_ws_url = body["trading_ws_url"]
    if enable and not snapshot_feeder.enabled:
        snapshot_feeder.enabled = True
        await snapshot_feeder.connect()
        await broadcast_gui_event({"type": "feeder_enabled"})
    elif not enable and snapshot_feeder.enabled:
        snapshot_feeder.enabled = False
        await snapshot_feeder.disconnect()
        await broadcast_gui_event({"type": "feeder_disabled"})
    return snapshot_feeder.get_status()


# =============================================================================
# GUI WebSocket
# =============================================================================

@router.websocket("/ws/events")
async def gui_ws(ws: WebSocket):
    await ws.accept()
    gui_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        gui_clients.discard(ws)
