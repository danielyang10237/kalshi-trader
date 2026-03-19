"""JSON file cache for live NBA game state, keyed by game_id."""

import json
import threading
from pathlib import Path
from typing import Optional

_CACHE_DIR = Path(__file__).parent.parent / "data" / "nba_games"
_lock = threading.Lock()


def _game_file(game_id: str) -> Path:
    return _CACHE_DIR / f"{game_id}.json"


def _default_state(game_id: str) -> dict:
    return {
        "game_id": game_id,
        "home_team": None,
        "away_team": None,
        "home_score": 0,
        "away_score": 0,
        "possession": None,  # "home" or "away"
        "quarter": 1,
        "events": [],  # ordered list of score/possession events
    }


def load_game(game_id: str) -> dict:
    path = _game_file(game_id)
    with _lock:
        if not path.exists():
            return _default_state(game_id)
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return _default_state(game_id)


def save_game(state: dict):
    game_id = state["game_id"]
    with _lock:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_game_file(game_id), "w") as f:
            json.dump(state, f, indent=2)


def list_games() -> list[str]:
    with _lock:
        if not _CACHE_DIR.exists():
            return []
        return [p.stem for p in _CACHE_DIR.glob("*.json")]


def delete_game(game_id: str) -> bool:
    path = _game_file(game_id)
    with _lock:
        if path.exists():
            path.unlink()
            return True
        return False
