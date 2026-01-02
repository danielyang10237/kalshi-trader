from __future__ import annotations
from typing import Dict, List, Optional
import csv, json
import requests

position_information_path = "nfl/data/position_map.json"
position_data_prefix_path = "nfl/data/position_prefix.json"

# extra information to include inside our player data
DEFAULT_POSITION_DATA_FIELDS: List[str] = [
    "season",
    "event_id",
    "team_id",
    "date",
    "played",
]


# -----------------------------
# Initialize general data mappings
# -----------------------------
def _initialize_position_information() -> Dict[int, dict[str, str]]:
    """
    Load american football position information from JSON file to fill out our dictionaries
    """
    position_info_dict: Dict[int, dict[str, str]] = {}
    position_info = json.load(open(position_information_path, encoding="utf-8"))
    for pos_id, info in position_info.items():
        position_info_dict[int(pos_id)] = {
            'name': info['name'],
            'abbreviation': info['abbreviation'],
        }
    return position_info_dict

def _initialize_position_data_fields_prefixes() -> Dict[str, List[str]]:
    """
    Load position prefixes from JSON file to map positions to data fields we want to include for that position
    """
    with open(position_data_prefix_path, encoding="utf-8") as f:
        return json.load(f)

def _initialize_team_code_mapping() -> Dict[str, str]:
    """
    Load NFL team name -> team_code mapping from CSV to standardize naming. Ie "LA" -> "LAR"
    """
    team_data = "nfl/data/schedules/nfl_teams.csv"
    mapping: Dict[str, str] = {}
    with open(team_data, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        first_row = next(reader, None)
        if first_row and "team_name" not in first_row[0].lower():
            row = first_row
            if len(row) >= 3:
                mapping[row[0]] = row[2]
        for row in reader:
            if len(row) < 3:
                continue
            team_name_v1 = row[0]
            team_code = row[2]
            team_name_v2 = row[3]
            team_name_v3 = row[8]
            mapping[team_name_v1] = team_code
            mapping[team_name_v2] = team_code
            mapping[team_name_v3] = team_code
    return mapping

def _initialize_team_id_mapping() -> Dict[str, str]:
    """
    Load NFL team_code -> team_id mapping from CSV at import time. ie "LAR" -> "3"
    """
    team_data = "nfl/data/schedules/nfl_teams.csv"
    mapping: Dict[str, str] = {}
    with open(team_data, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        first_row = next(reader, None)
        if first_row and "team_name" not in first_row[0].lower():
            row = first_row
            if len(row) >= 3:
                mapping[row[0]] = row[2]
        for row in reader:
            if len(row) < 3:
                continue
            team_code = row[9]
            team_id = row[2]
            mapping[team_id] = team_code
    return mapping

team_code_mapping: Dict[str, str] = _initialize_team_code_mapping()
POSITION_ID_TO_INFO: Dict[int, dict[str, str]] = _initialize_position_information()
POSITION_DATA_FIELDS_PREFIXES: Dict[str, List[str]] = _initialize_position_data_fields_prefixes()
team_id_mapping: Dict[str, str] = _initialize_team_id_mapping()

# -----------------------------
# Helpers
# -----------------------------

def _get_json(url: str, params: Optional[dict] = None, timeout: int = 30) -> dict:
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

def _team_abbr(s: str) -> str:
    return (s or "").strip().upper()

def _get_data_fields_prefixes_for_position(position_id: str | int) -> List[str]:
    """
    Get the data fields prefixes for a position. _get_data_fields_prefixes_for_position("8")  # Returns ["pass", "rush", "rec"]
    """
    pos_id = int(position_id) if isinstance(position_id, str) else position_id
    return POSITION_DATA_FIELDS_PREFIXES.get(pos_id, [])

def _get_team_id_code(team_name: str) -> str:
    """
    Get the team ID code from a team name. _get_team_id_code("LAR")  # Returns "3"
    """
    team_code = team_code_mapping[team_name]
    team_id = team_id_mapping[team_code]
    return team_id

def _get_team_code_from_name(team_name: str) -> str:
    """
    Get the team code from a team name. _get_team_code_from_name("LA")  # Returns "LAR"
    """
    team_code = team_code_mapping[team_name]
    return team_code

def _get_position_name_from_id(position_id: str | int) -> str:
    """
    Get the full position name from a position ID. position_name("8")  # Returns "Quarterback"
    """
    pos_id = int(position_id) if isinstance(position_id, str) else position_id
    info = POSITION_ID_TO_INFO.get(pos_id) or {}
    return info.get("name") or ""

def _get_position_abbreviation_from_id(position_id: str | int) -> str:
    """
    Get the position abbreviation from a position ID. position_abbreviation("8")  # Returns "QB"
    """
    pos_id = int(position_id)
    info = POSITION_ID_TO_INFO[pos_id]
    return info["abbreviation"]

def _find_event_id_by_date_and_teams(date_yyyymmdd: str, away_abbr: str, home_abbr: str) -> str:
    """
    Find the ESPN event ID for a given date and teams. _find_event_id_by_date_and_teams("20251230", "LAR", "SEA")  # Returns "1234567890"
    """
    score_board_url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

    away_abbr = _team_abbr(away_abbr)
    home_abbr = _team_abbr(home_abbr)

    data = _get_json(score_board_url, params={"dates": date_yyyymmdd})
    events = data.get("events", []) or []
    if not events:
        raise ValueError(f"No events returned for date={date_yyyymmdd}")

    for ev in events:
        comps = ev.get("competitions", []) or []
        if not comps:
            continue
        comp = comps[0]
        competitors = comp.get("competitors", []) or []
        if len(competitors) < 2:
            continue

        found_home = found_away = None
        for c in competitors:
            team = (c.get("team") or {})
            abbr = _team_abbr(team.get("abbreviation"))
            if c.get("homeAway") == "home":
                found_home = abbr
            elif c.get("homeAway") == "away":
                found_away = abbr

        if found_home == home_abbr and found_away == away_abbr:
            return str(ev.get("id"))

    raise ValueError(
        f"Could not find event on {date_yyyymmdd} for away={away_abbr} at home={home_abbr}. "
        f"Check abbreviations and date."
    )

def _build_team_id_to_abbr(data: dict) -> Dict[str, str]:
    """
    Build mapping like {"14": "LAR", "26": "SEA"} from header/competitors.
    """
    header = data.get("header") or {}
    competitions = header.get("competitions") or []
    if not competitions:
        raise ValueError("Unexpected ESPN response: missing header.competitions")

    comp = competitions[0]
    competitors = comp.get("competitors") or []
    team_id_to_abbr: Dict[str, str] = {}

    for c in competitors:
        team = c.get("team") or {}
        tid = str(team.get("id") or "")
        abbr = _team_abbr(team.get("abbreviation"))
        if tid and abbr:
            team_id_to_abbr[tid] = abbr

    return team_id_to_abbr

__all__ = [
    "DEFAULT_POSITION_DATA_FIELDS",
    "_get_json",
    "_team_abbr",
    "_get_data_fields_prefixes_for_position",
    "_get_team_id_code",
    "_get_team_code_from_name",
    "_get_position_name_from_id",
    "_get_position_abbreviation_from_id",
    "_find_event_id_by_date_and_teams",
    "_build_team_id_to_abbr",
]
