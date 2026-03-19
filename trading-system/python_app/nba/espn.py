"""Resolve Kalshi event ticker → ESPN game_id → roster via ESPN API."""

import csv
import json
import re
from datetime import datetime
from pathlib import Path

import httpx

_DATA_DIR = Path(__file__).parent.parent / "data"
_ROSTER_DIR = _DATA_DIR / "nba_roster"
_NAME_MAP: dict[str, str] | None = None
_SCHEDULE: list[dict] | None = None

# Cache espn_game_id per kalshi ticker so we don't re-lookup each time
_espn_id_cache: dict[str, str] = {}


def _load_name_map() -> dict[str, str]:
    global _NAME_MAP
    if _NAME_MAP is None:
        with open(_DATA_DIR / "nba_name_mapping.json") as f:
            _NAME_MAP = json.load(f)
    return _NAME_MAP


def _load_schedule() -> list[dict]:
    global _SCHEDULE
    if _SCHEDULE is None:
        with open(_DATA_DIR / "nba_schedule.csv") as f:
            _SCHEDULE = list(csv.DictReader(f))
    return _SCHEDULE


def _normalize(abbr: str) -> str:
    """Map any team abbreviation to canonical form using the mapping."""
    m = _load_name_map()
    return m.get(abbr.upper(), abbr.upper())


def _load_static_roster(team_abbr: str) -> list[dict]:
    """Load static roster from data/nba_roster/{TEAM}.json."""
    canon = _normalize(team_abbr)
    path = _ROSTER_DIR / f"{canon}.json"
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def parse_ticker(ticker: str) -> dict | None:
    """Parse KXNBAGAME-26MAR10CHIGSW → {year, month, day, away, home, date}."""
    # Remove series prefix
    suffix = ticker.replace("KXNBAGAME-", "")
    # Pattern: YY + MON + DD + AWAY(3) + HOME(3)
    match = re.match(r"(\d{2})([A-Z]{3})(\d{1,2})([A-Z]{3})([A-Z]{3,4})$", suffix)
    if not match:
        return None
    year = 2000 + int(match.group(1))
    month_str = match.group(2)
    day = int(match.group(3))
    away = match.group(4)
    home = match.group(5)
    months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
              "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
    month = months.get(month_str)
    if not month:
        return None
    date_str = f"{year}-{month:02d}-{day:02d}"
    return {"year": year, "month": month, "day": day,
            "away": away, "home": home, "date": date_str}


def lookup_espn_game_id(ticker: str) -> str | None:
    """Given a Kalshi ticker, find the ESPN game_id from the schedule CSV."""
    if ticker in _espn_id_cache:
        return _espn_id_cache[ticker]

    parsed = parse_ticker(ticker)
    if not parsed:
        return None

    away_canon = _normalize(parsed["away"])
    home_canon = _normalize(parsed["home"])
    game_date = parsed["date"]

    schedule = _load_schedule()
    for row in schedule:
        row_date = row.get("GAME_DATE", "")
        if row_date != game_date:
            continue
        row_home = _normalize(row.get("home_abbreviation", ""))
        row_away = _normalize(row.get("away_abbreviation", ""))
        if row_home == home_canon and row_away == away_canon:
            gid = row["GAME_ID"]
            _espn_id_cache[ticker] = gid
            return gid
    return None


async def fetch_roster(espn_game_id: str, home_team: str = "", away_team: str = "") -> dict:
    """Fetch game info from ESPN summary API + combine with static rosters.

    Loads full team rosters from data/nba_roster/, fetches injuries from ESPN,
    then removes players listed as "Out" to produce the expected active roster.
    """
    url = f"https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={espn_game_id}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    result = {
        "home": [], "away": [],
        "injuries": {"home": [], "away": []},
        "leaders": {"home": [], "away": []},
        "predictor": None,
        "odds": None,
    }

    # --- injuries (available pre-game and in-game) ---
    for inj_section in data.get("injuries", []):
        team_info = inj_section.get("team", {})
        homeaway = ""
        # Match by team id from header competitors
        for comp in data.get("header", {}).get("competitions", [{}])[0].get("competitors", []):
            if comp.get("id") == team_info.get("id") or comp.get("team", {}).get("id") == team_info.get("id"):
                homeaway = comp.get("homeAway", "")
                break
        if homeaway not in ("home", "away"):
            continue
        for inj in inj_section.get("injuries", []):
            athlete = inj.get("athlete", {})
            name = athlete.get("displayName", "")
            pos = athlete.get("position", {})
            position = pos.get("abbreviation", "") if isinstance(pos, dict) else ""
            jersey = athlete.get("jersey", "")
            status = inj.get("status", "")
            details = inj.get("details", {})
            injury_type = details.get("type", "")
            injury_detail = details.get("detail", "")
            return_date = details.get("returnDate", "")
            if name:
                result["injuries"][homeaway].append({
                    "name": name, "position": position, "jersey": jersey,
                    "status": status,
                    "injury": f"{injury_type} - {injury_detail}" if injury_detail else injury_type,
                    "return_date": return_date,
                })

    # --- build active roster: static roster minus "Out" players ---
    out_names = {
        "home": {inj["name"] for inj in result["injuries"]["home"] if inj["status"] == "Out"},
        "away": {inj["name"] for inj in result["injuries"]["away"] if inj["status"] == "Out"},
    }
    # Also exclude suspended players
    for side in ("home", "away"):
        for inj in result["injuries"][side]:
            if inj["status"] == "Suspension":
                out_names[side].add(inj["name"])

    teams = {"home": home_team, "away": away_team}
    for side in ("home", "away"):
        abbr = teams[side]
        if not abbr:
            continue
        static = _load_static_roster(abbr)
        result[side] = [
            {"id": p["id"], "name": p["name"], "position": p["position"], "jersey": p["jersey"]}
            for p in static
            if p["name"] not in out_names[side]
        ]

    # --- leaders (pre-game top performers) ---
    for leader_section in data.get("leaders", []):
        team_info = leader_section.get("team", {})
        homeaway = ""
        for comp in data.get("header", {}).get("competitions", [{}])[0].get("competitors", []):
            if comp.get("id") == team_info.get("id") or comp.get("team", {}).get("id") == team_info.get("id"):
                homeaway = comp.get("homeAway", "")
                break
        if homeaway not in ("home", "away"):
            continue
        seen = set()
        for cat in leader_section.get("leaders", []):
            for leader in cat.get("leaders", []):
                athlete = leader.get("athlete", {})
                name = athlete.get("displayName", "")
                if name and name not in seen:
                    seen.add(name)
                    pos = athlete.get("position", {})
                    position = pos.get("abbreviation", "") if isinstance(pos, dict) else ""
                    jersey = athlete.get("jersey", "")
                    # Check if injured
                    inj_status = ""
                    if isinstance(athlete.get("status"), str):
                        inj_status = athlete["status"]
                    elif isinstance(athlete.get("injuries"), dict):
                        inj_status = athlete["injuries"].get("status", "")
                    result["leaders"][homeaway].append({
                        "name": name, "position": position, "jersey": jersey,
                        "stat": leader.get("displayValue", ""),
                        "stat_name": cat.get("displayName", ""),
                        "status": inj_status,
                    })

    # --- predictor ---
    pred = data.get("predictor", {})
    if pred:
        result["predictor"] = {
            "home_win_pct": pred.get("homeTeam", {}).get("gameProjection"),
            "away_win_pct": pred.get("awayTeam", {}).get("gameProjection"),
        }

    # --- odds ---
    pickcenter = data.get("pickcenter", [])
    if pickcenter:
        pc = pickcenter[0]
        result["odds"] = {
            "spread": pc.get("spread"),
            "over_under": pc.get("overUnder"),
            "home_ml": pc.get("homeTeamOdds", {}).get("moneyLine"),
            "away_ml": pc.get("awayTeamOdds", {}).get("moneyLine"),
        }

    return result
