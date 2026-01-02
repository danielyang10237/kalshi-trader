from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable

import pandas as pd
import requests

from . import (
    _find_event_id_by_date_and_teams,
    _get_team_code_from_name,
    _get_team_id_code,
)


ESPN_STATS_URL = (
    "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/"
    "events/{event_id}/competitions/{comp_id}/competitors/{team_id}/statistics/0"
    "?lang=en&region=us"
)


@dataclass(frozen=True)
class GameRow:
    game_id: str
    gameday: str  # YYYY-MM-DD
    away_team: str
    home_team: str


def _safe_key(category: str, stat_name: str) -> str:
    """Keep it stable and CSV-friendly."""
    cat = (category or "").strip().replace(" ", "_")
    name = (stat_name or "").strip().replace(" ", "_")
    return f"{cat}.{name}"


def _request_json(
    session: requests.Session,
    url: str,
    *,
    max_retries: int = 6,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Basic retry logic for transient ESPN failures / rate limits."""
    backoff = 1.0
    for attempt in range(max_retries):
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue
            raise RuntimeError(f"HTTP {resp.status_code} for {url}: {resp.text[:300]}")
        except (requests.Timeout, requests.ConnectionError):
            if attempt == max_retries - 1:
                raise
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
    raise RuntimeError(f"Failed to fetch after retries: {url}")


def _fetch_team_game_stats(session: requests.Session, *, event_id: str, team_id: str) -> Dict[str, Any]:
    """Returns flattened team boxscore stats for a single team in a single game."""
    url = ESPN_STATS_URL.format(event_id=event_id, comp_id=event_id, team_id=team_id)
    payload = _request_json(session, url)

    out: Dict[str, Any] = {}
    splits = payload.get("splits") or {}
    categories = splits.get("categories") or []
    for cat in categories:
        cat_name = cat.get("name") or cat.get("abbreviation") or cat.get("displayName") or "unknown"
        for stat in (cat.get("stats") or []):
            stat_name = stat.get("name") or stat.get("abbreviation") or stat.get("displayName") or "unknown"
            key = _safe_key(str(cat_name), str(stat_name))
            if "value" in stat and stat["value"] is not None:
                out[key] = stat["value"]
            else:
                out[key] = stat.get("displayValue")
    return out


def _iter_games_csv(games_csv_path: Path) -> Iterable[GameRow]:
    with games_csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"game_id", "gameday", "away_team", "home_team"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Games CSV missing required columns: {sorted(missing)}")

        for row in reader:
            game_id = (row.get("game_id") or "").strip()
            gameday = (row.get("gameday") or "").strip()
            away_team = (row.get("away_team") or "").strip()
            home_team = (row.get("home_team") or "").strip()
            if not (game_id and gameday and away_team and home_team):
                continue
            yield GameRow(game_id=game_id, gameday=gameday, away_team=away_team, home_team=home_team)


def _append_row_to_team_file(team_file: Path, row_dict: Dict[str, Any]) -> None:
    """Appends a row, expanding columns if new stats appear. Dedupes by game_id."""
    team_file.parent.mkdir(parents=True, exist_ok=True)

    if team_file.exists():
        df = pd.read_csv(team_file)
        if "game_id" in df.columns and row_dict.get("game_id") in set(df["game_id"].astype(str)):
            return
        df_out = pd.concat([df, pd.DataFrame([row_dict])], ignore_index=True)
    else:
        df_out = pd.DataFrame([row_dict])

    front_cols = ["game_id", "gameday", "is_home", "espn_event_id", "team_abbr"]
    existing_front = [c for c in front_cols if c in df_out.columns]
    other_cols = [c for c in df_out.columns if c not in existing_front]
    df_out = df_out[existing_front + sorted(other_cols)]

    df_out.to_csv(team_file, index=False)


def _yyyymmdd_from_gameday(gameday: str) -> str:
    # Expect YYYY-MM-DD; fall back to stripping non-digits.
    s = (gameday or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:4] + s[5:7] + s[8:10]
    return "".join(ch for ch in s if ch.isdigit())


def _process_games(
    games_csv_path: Path,
    output_dir: Path = Path("nfl/data/teams"),
    *,
    sleep_s: float = 0.5,
) -> None:
    """Reads the games CSV and writes per-team CSVs with per-game stats."""
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "nfl-team-stats-fetcher/1.1 (+https://www.espn.com)",
        }
    )

    for game in _iter_games_csv(games_csv_path):
        date_yyyymmdd = _yyyymmdd_from_gameday(game.gameday)
        if not date_yyyymmdd:
            print(f"[SKIP] Bad gameday format for game_id={game.game_id}: {game.gameday!r}")
            continue

        try:
            away_team_code = _get_team_code_from_name(game.away_team)
            home_team_code = _get_team_code_from_name(game.home_team)
            event_id = _find_event_id_by_date_and_teams(date_yyyymmdd, away_team_code, home_team_code)
        except Exception as e:
            print(
                f"[ERROR] Could not resolve ESPN event_id for game_id={game.game_id} "
                f"({game.away_team}@{game.home_team} on {game.gameday}): {e}"
            )
            continue

        # away
        try:
            away_team_name = _get_team_code_from_name(game.away_team)
            away_team_id = str(_get_team_id_code(away_team_name))
            away_stats = _fetch_team_game_stats(session, event_id=str(event_id), team_id=away_team_id)
            away_row = {
                "game_id": game.game_id,
                "gameday": game.gameday,
                "is_home": False,
                "espn_event_id": str(event_id),
                "team_abbr": game.away_team,
                **away_stats,
            }
            _append_row_to_team_file(output_dir / f"{away_team_name}.csv", away_row)
        except Exception as e:
            print(f"  [ERROR] Away stats failed for {game.away_team} (game_id={game.game_id}): {e}")

        # home
        try:
            home_team_name = _get_team_code_from_name(game.home_team)
            home_team_id = str(_get_team_id_code(home_team_name))
            home_stats = _fetch_team_game_stats(session, event_id=str(event_id), team_id=home_team_id)
            home_row = {
                "game_id": game.game_id,
                "gameday": game.gameday,
                "is_home": True,
                "espn_event_id": str(event_id),
                "team_abbr": game.home_team,
                **home_stats,
            }
            _append_row_to_team_file(output_dir / f"{home_team_name}.csv", home_row)
        except Exception as e:
            print(f"  [ERROR] Home stats failed for {game.home_team} (game_id={game.game_id}): {e}")

        if sleep_s:
            time.sleep(sleep_s)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch ESPN per-game team stats and append to per-team CSVs (event_id resolved via scoreboard date+teams)."
    )
    parser.add_argument(
        "games_csv",
        type=str,
        help="Path to the games CSV (requires game_id, gameday, away_team, home_team).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="nfl/data/teams",
        help="Directory to write per-team CSV files.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.1,
        help="Sleep seconds between games to be polite to ESPN.",
    )
    args = parser.parse_args()

    _process_games(Path(args.games_csv), Path(args.output_dir), sleep_s=args.sleep)


if __name__ == "__main__":
    main()
