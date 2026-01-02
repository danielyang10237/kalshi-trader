from __future__ import annotations
from . import _get_json, _find_event_id_by_date_and_teams, _get_team_code_from_name, _build_team_id_to_abbr, _team_abbr

from typing import Any, Dict, List, Optional
import re
import pandas as pd
import time
import os

ESPN_CORE_BASE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
ESPN_SITE_WEB_BASE = "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"

# -----------------------------
# Roster (core endpoint)
# -----------------------------

def _get_team_id_for_event(event_id: str, team_abbr: str, *, timeout: int = 30) -> str:
    """
    Convert team abbreviation (e.g., 'LAR') -> ESPN numeric team/competitor id for that event (e.g., '14').
    """
    data = _get_json(ESPN_SUMMARY, params={"event": str(event_id)}, timeout=timeout)
    team_id_to_abbr = _build_team_id_to_abbr(data)

    team_abbr = _team_abbr(team_abbr)
    for tid, abbr in team_id_to_abbr.items():
        if abbr == team_abbr:
            return tid

    raise ValueError(
        f"Could not find team_id for abbr={team_abbr} in event_id={event_id}. "
        f"Available: {sorted(set(team_id_to_abbr.values()))}"
    )

def _extract_roster_entries(roster_json: Any) -> List[dict]:
    """
    ESPN core roster usually looks like {"entries": [...]}, but be defensive.
    """
    if roster_json is None:
        return []
    if isinstance(roster_json, list):
        out: List[dict] = []
        for x in roster_json:
            out.extend(_extract_roster_entries(x))
        return out
    if not isinstance(roster_json, dict):
        return []

    if isinstance(roster_json.get("entries"), list):
        return roster_json["entries"]
    if isinstance(roster_json.get("items"), list):
        return roster_json["items"]
    if isinstance(roster_json.get("athletes"), list):
        return roster_json["athletes"]

    for k in ("roster", "team", "groups", "positions", "categories"):
        if k in roster_json:
            return _extract_roster_entries(roster_json.get(k))

    return []


_ref_id_re = re.compile(r"/(\d+)(?:\?|$)")

def _ref_to_id(ref_obj: Any) -> Optional[str]:
    """
    Extract trailing numeric id from {"$ref": ".../12345?..."}.
    """
    if not (isinstance(ref_obj, dict) and isinstance(ref_obj.get("$ref"), str)):
        return None
    m = _ref_id_re.search(ref_obj["$ref"])
    return m.group(1) if m else None


# -----------------------------
# Module-level cache for athlete info to reduce API calls across games
# Key: athlete_id (str), Value: athlete dict from API
# -----------------------------
_athlete_cache: Dict[str, dict] = {}
_ATHLETE_CACHE_PATH = "nfl/data/players/athlete_cache.json"


def load_athlete_cache(path: Optional[str] = None) -> int:
    """
    Load athlete cache from disk. Returns number of entries loaded.
    
    Args:
        path: Path to cache file. Defaults to _ATHLETE_CACHE_PATH.
    """
    import json
    global _athlete_cache
    
    cache_path = path or _ATHLETE_CACHE_PATH
    if not os.path.exists(cache_path):
        return 0
    
    try:
        with open(cache_path, "r") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            _athlete_cache.update(loaded)
            return len(loaded)
    except Exception as e:
        print(f"Warning: Failed to load athlete cache from {cache_path}: {e}")
    return 0


def save_athlete_cache(path: Optional[str] = None) -> int:
    """
    Save athlete cache to disk. Returns number of entries saved.
    
    Args:
        path: Path to cache file. Defaults to _ATHLETE_CACHE_PATH.
    """
    import json
    
    cache_path = path or _ATHLETE_CACHE_PATH
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    
    try:
        with open(cache_path, "w") as f:
            json.dump(_athlete_cache, f)
        return len(_athlete_cache)
    except Exception as e:
        print(f"Warning: Failed to save athlete cache to {cache_path}: {e}")
        return 0


def _get_cached_athlete(athlete_id: Optional[str], ref_obj: Any, *, timeout: int = 30) -> Optional[dict]:
    """
    Get athlete info from cache if available, otherwise fetch from API and cache it.
    
    Args:
        athlete_id: The athlete's ID (may be None if not yet known)
        ref_obj: The $ref object containing the API URL
        timeout: Request timeout in seconds
        
    Returns:
        Athlete dict from cache or API, or None if unavailable
    """
    # Check cache first
    if athlete_id and athlete_id in _athlete_cache:
        return _athlete_cache[athlete_id]
    
    # Validate ref_obj
    if not (isinstance(ref_obj, dict) and isinstance(ref_obj.get("$ref"), str)):
        return None
    
    # Fetch from API
    try:
        data = _get_json(ref_obj["$ref"], timeout=timeout)
        if data:
            # Cache using provided athlete_id or extracted from response
            cache_id = athlete_id or str(data.get("id", ""))
            if cache_id:
                _athlete_cache[cache_id] = data
        return data
    except Exception:
        return None


def clear_athlete_cache() -> int:
    """
    Clear the athlete cache. Returns the number of entries cleared.
    Useful for testing or if you need to refresh stale data.
    """
    count = len(_athlete_cache)
    _athlete_cache.clear()
    return count


def get_athlete_cache_size() -> int:
    """Return the current number of cached athletes."""
    return len(_athlete_cache)


def _get_roster_for_event_id(
    event_id: str,
    team_id_or_abbr: str,
    *,
    deref_athletes: bool = False,
    deref_positions: bool = False,
    timeout: int = 30,
) -> pd.DataFrame:
    """
    Roster endpoint:
      sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{EVENT_ID}/competitions/{EVENT_ID}/competitors/{TEAM_ID}/roster

    Accepts either numeric team_id (e.g., "14") or team abbreviation (e.g., "LAR") and converts as needed.
    
    If displayName or other key fields are missing from the roster entry, automatically
    fetches athlete details from the athlete.$ref URL to fill in the gaps.
    
    Uses module-level _athlete_cache to avoid redundant API calls for players
    who appear in multiple games.
    """
    event_id = str(event_id)
    team_id_or_abbr = str(team_id_or_abbr).strip()

    if not team_id_or_abbr.isdigit():
        team_id = _get_team_id_for_event(event_id, team_id_or_abbr, timeout=timeout)
    else:
        team_id = team_id_or_abbr

    url = f"{ESPN_CORE_BASE}/events/{event_id}/competitions/{event_id}/competitors/{team_id}/roster"
    roster = _get_json(url, timeout=timeout)
    entries = _extract_roster_entries(roster)

    # Local cache for position refs (these are less likely to repeat across games)
    position_cache: Dict[str, dict] = {}

    def deref_position(ref_obj: Any) -> Optional[dict]:
        if not (isinstance(ref_obj, dict) and isinstance(ref_obj.get("$ref"), str)):
            return None
        u = ref_obj["$ref"]
        if u in position_cache:
            return position_cache[u]
        try:
            position_cache[u] = _get_json(u, timeout=timeout)
            return position_cache[u]
        except Exception:
            return None

    rows: List[Dict[str, Any]] = []

    for e in entries:
        if not isinstance(e, dict):
            continue

        player_id = e.get("playerId") or _ref_to_id(e.get("athlete"))
        display_name = e.get("displayName")
        jersey = e.get("jersey") or e.get("jerseyNumber")

        starter = e.get("starter")
        active = e.get("active")
        did_not_play = e.get("didNotPlay")
        valid = e.get("valid")
        period = e.get("period")
        full_name, position_name = None, None

        pos_id = _ref_to_id(e.get("position"))
        athlete_id = str(player_id) if player_id else None

        # Determine if we need to fetch athlete details
        # Fetch if: deref_athletes is True, OR displayName/jersey is missing
        needs_athlete_deref = deref_athletes or display_name is None or jersey is None
        
        ath_obj = None
        if needs_athlete_deref:
            # Use the cached athlete fetch instead of local deref
            ath_obj = _get_cached_athlete(athlete_id, e.get("athlete"), timeout=timeout)
        
        if ath_obj:
            # Fill in missing fields from athlete object
            if display_name is None:
                display_name = ath_obj.get("displayName")
            full_name = ath_obj.get("fullName")
            if jersey is None:
                jersey = ath_obj.get("jersey")
            if athlete_id is None:
                athlete_id = ath_obj.get("id")
            
            # Also grab position info from athlete if we need it and don't have it
            if position_name is None and not deref_positions:
                ath_position = ath_obj.get("position")
                if isinstance(ath_position, dict):
                    position_name = ath_position.get("name")
                    if pos_id is None:
                        pos_id = ath_position.get("id")

        # Separately deref position if explicitly requested and still missing
        if deref_positions and position_name is None:
            pos_obj = deref_position(e.get("position"))
            if pos_obj:
                position_name = pos_obj.get("name")

        rows.append({
            "event_id": event_id,
            "team_id": team_id,
            "team_input": team_id_or_abbr,

            "athlete_id": athlete_id,
            "display_name": display_name,
            "full_name": full_name,
            "jersey": jersey,

            "position_id": pos_id,
            "position_name": position_name,

            "starter": starter,
            "active": active,
            "did_not_play": did_not_play,
            "valid": valid,
            "period": period,
        })

    return pd.DataFrame(rows)


def _fetch_player_eventlog(year: int, athlete_id: str, *, timeout: int = 30, out_csv: str = "player_stats_each_game.csv") -> pd.DataFrame:
    """
    Player stats for each game (event log):
      /seasons/{YEAR}/athletes/{ATHLETE_ID}/eventlog

    Saves a flattened CSV and prints a small preview.
    """
    url = f"{ESPN_CORE_BASE}/seasons/{int(year)}/athletes/{athlete_id}/eventlog"
    data = _get_json(url, timeout=timeout)

    rows: List[Dict[str, Any]] = []
    if isinstance(data, dict):
        items = data.get("events") or data.get("items") or data.get("entries") or []
        if isinstance(items, dict):
            items = items.get("items") or items.get("events") or []
        if not isinstance(items, list):
            items = []

        for it in items:
            if isinstance(it, dict) and "$ref" in it:
                it = _get_json(it["$ref"], timeout=timeout)

            if not isinstance(it, dict):
                continue

            event = it.get("event") or {}
            if isinstance(event, dict) and "$ref" in event:
                event = {"$ref": event["$ref"]}

            rows.append({
                "athlete_id": str(athlete_id),
                "season": int(year),
                "event_id": (event.get("id") if isinstance(event, dict) else None),
                "event_ref": (event.get("$ref") if isinstance(event, dict) else None),
                "game_date": it.get("date") or it.get("startDate") or it.get("gameDate"),
                "opponent": (it.get("opponent") or {}).get("abbreviation") if isinstance(it.get("opponent"), dict) else None,
                "home_away": it.get("homeAway"),
                "raw": it
            })

    df = pd.DataFrame(rows)
    df.to_csv(out_csv, index=False)
    print(f"Wrote {out_csv} ({len(df)} rows)")
    print(df.head(10).to_string(index=False))
    return df


def _fetch_player_statisticslog(athlete_id: str, *, timeout: int = 30, out_csv: str = "player_statisticslog.csv") -> pd.DataFrame:
    """
    statisticslog:
      /athletes/{ATHLETE_ID}/statisticslog

    Saves a flattened CSV and prints a small preview.
    """
    url = f"{ESPN_CORE_BASE}/athletes/{athlete_id}/statisticslog"
    data = _get_json(url, timeout=timeout)
    
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data["items"]
        deref_items = []
        for it in items:
            if isinstance(it, dict) and "$ref" in it:
                try:
                    deref_items.append(_get_json(it["$ref"], timeout=timeout))
                except Exception:
                    deref_items.append(it)
            else:
                deref_items.append(it)
        roster_df = pd.json_normalize(deref_items, sep=".")
    else:
        roster_df = pd.json_normalize(data, sep=".")

    roster_df.insert(0, "athlete_id", str(athlete_id))
    roster_df.to_csv(out_csv, index=False)
    print(f"Wrote {out_csv} ({len(roster_df)} rows, {len(roster_df.columns)} cols)")
    print(roster_df.head(10).to_string(index=False))
    return roster_df


def _fetch_athlete_overview(athlete_id: str, *, timeout: int = 30, out_csv: str = "athlete_overview.csv") -> pd.DataFrame:
    """
    Athlete Overview:
      site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{ATHLETE_ID}/overview

    Saves a flattened one-row CSV (overview is usually a big nested dict).
    """
    url = f"{ESPN_SITE_WEB_BASE}/athletes/{athlete_id}/overview"
    data = _get_json(url, timeout=timeout)

    roster_df = pd.json_normalize(data, sep=".")
    roster_df.insert(0, "athlete_id", str(athlete_id))

    roster_df.to_csv(out_csv, index=False)
    print(f"Wrote {out_csv} (1 row, {len(roster_df.columns)} cols)")

    preview_cols = roster_df.columns[:min(25, len(roster_df.columns))]
    print(roster_df.loc[:, preview_cols].head(1).to_string(index=False))
    return roster_df

def _fetch_all_rosters(nfl_schedule_path: str, sleep_time: int = 0.2, save_cache_every: int = 10):
    """
    Fetch rosters for all games in schedule.
    
    Args:
        nfl_schedule_path: Path to schedule CSV
        sleep_time: Delay between API calls
        save_cache_every: Save athlete cache to disk every N games (0 to disable)
    """
    # Load existing cache from disk
    loaded = load_athlete_cache()
    print(f"Loaded {loaded} athletes from cache")
    
    schedule_df = pd.read_csv(nfl_schedule_path)
    games_processed = 0
    
    try:
        for _, sched_row in schedule_df.iterrows():
            game_id = sched_row["game_id"]
            home_team = _get_team_code_from_name(sched_row["home_team"])
            away_team = _get_team_code_from_name(sched_row["away_team"])

            output_path = f"nfl/data/rosters/{game_id}"
            if os.path.exists(output_path):
                print(f"Rosters already exist for {game_id}")
                continue
            
            gameday = str(sched_row["gameday"]).strip()
            if len(gameday) >= 10 and gameday[4] == "-" and gameday[7] == "-":
                date_yyyymmdd = gameday[:4] + gameday[5:7] + gameday[8:10]
            else:
                # Fall back to stripping non-digits
                date_yyyymmdd = "".join(ch for ch in gameday if ch.isdigit())
            event_id = _find_event_id_by_date_and_teams(date_yyyymmdd, away_team, home_team)
            time.sleep(sleep_time)
            roster1 = _get_roster_for_event_id(event_id, home_team)
            time.sleep(sleep_time)
            roster2 = _get_roster_for_event_id(event_id, away_team)
            time.sleep(sleep_time)
            os.makedirs(f"nfl/data/rosters/{game_id}", exist_ok=True)
            roster1.to_csv(f"nfl/data/rosters/{game_id}/{home_team}.csv", index=False)
            roster2.to_csv(f"nfl/data/rosters/{game_id}/{away_team}.csv", index=False)

            games_processed += 1
            print(f"Saved rosters for {game_id} between {home_team} and {away_team} (athlete cache size: {get_athlete_cache_size()})")
            
            # Periodically save cache to disk
            if save_cache_every > 0 and games_processed % save_cache_every == 0:
                saved = save_athlete_cache()
                print(f"  -> Saved {saved} athletes to cache")
    
    finally:
        # Always save cache on exit (even on error/interrupt)
        saved = save_athlete_cache()
        print(f"Final cache save: {saved} athletes")

if __name__ == "__main__":
    # fetch_player_eventlog(2025, "12483")
    # fetch_player_statisticslog("12483")
    # fetch_athlete_overview("12483")
    # _fetch_all_rosters("nfl/data/schedules/nfl_schedule_2018_2021.csv")
    # _fetch_all_rosters("nfl/data/schedules/nfl_schedule_2014_2017.csv")
    # _fetch_all_rosters("nfl/data/schedules/nfl_schedule_2010_2013.csv")
    # _fetch_all_rosters("nfl/data/schedules/nfl_schedule_2006_2009.csv")
    _fetch_all_rosters("nfl/data/schedules/nfl_schedule_2002_2005.csv")