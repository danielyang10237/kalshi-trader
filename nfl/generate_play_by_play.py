from . import _get_json, _team_abbr, _find_event_id_by_date_and_teams, _build_team_id_to_abbr
from typing import Dict, List, Optional, Any
import datetime
import csv
import pandas as pd
import os
import time
import sys

ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"


# -----------------------------
# Play-by-play
# -----------------------------

def _normalize_play_type(type_text: Optional[str]) -> Optional[str]:
    """
    Map ESPN 'type.text' into nflfastR-ish play_type values.
    """
    if not type_text:
        return None
    t = type_text.strip().lower()

    mapping = {
        "rush": "run",
        "pass reception": "pass",
        "pass incompletion": "pass",
        "sack": "pass",
        "kickoff": "kickoff",
        "punt": "punt",
        "field goal good": "field_goal",
        "field goal missed": "field_goal",
        "extra point good": "extra_point",
        "extra point missed": "extra_point",
        "two-point conversion": "two_point_attempt",
        "two-point pass": "two_point_attempt",
        "two-point rush": "two_point_attempt",
        "penalty": "penalty",
        "timeout": "timeout",
        "official timeout": "timeout",
    }

    return mapping.get(t, t.replace(" ", "_"))


def _fetch_espn_pbp(event_id: str) -> pd.DataFrame:
    """
    Fetch ESPN play-by-play from the ESPN_SITE summary endpoint and flatten drives/plays into a DataFrame.
    """
    data = _get_json(ESPN_SUMMARY, params={"event": str(event_id)})
    team_id_to_abbr = _build_team_id_to_abbr(data)

    header = data.get("header") or {}
    comp = (header.get("competitions") or [])[0]
    competitors = comp.get("competitors") or []

    home_team = away_team = None
    home_score = away_score = None
    for c in competitors:
        team = c.get("team") or {}
        abbr = _team_abbr(team.get("abbreviation"))
        if c.get("homeAway") == "home":
            home_team = abbr
            home_score = c.get("score")
        elif c.get("homeAway") == "away":
            away_team = abbr
            away_score = c.get("score")

    drives = (data.get("drives") or {})
    prev_drives = drives.get("previous") or []
    if not prev_drives:
        current = drives.get("current")
        if isinstance(current, dict):
            prev_drives = [current]
        elif isinstance(current, list):
            prev_drives = current
        else:
            prev_drives = []

    rows: List[Dict[str, Any]] = []
    drive_num = 0

    for d in prev_drives:
        drive_num += 1
        plays = d.get("plays") or []
        for p in plays:
            period = (p.get("period") or {}).get("number")
            clock = (p.get("clock") or {}).get("displayValue")
            desc = p.get("text") or ""

            start = p.get("start") or {}
            end = p.get("end") or {}

            down = start.get("down")
            ydstogo = start.get("distance")
            yardline_100 = start.get("yardsToEndzone")

            posteam = None
            team_obj = start.get("team") or {}
            team_id = team_obj.get("id")
            if team_id is not None:
                posteam = team_id_to_abbr.get(str(team_id))

            play_type = _normalize_play_type((p.get("type") or {}).get("text"))

            yards_gained = p.get("statYardage")
            try:
                yards_gained = int(yards_gained) if yards_gained is not None else None
            except Exception:
                yards_gained = None

            wallclock = p.get("wallclock")
            modified = p.get("modified")

            hs = p.get("homeScore")
            a_s = p.get("awayScore")
            try:
                hs = int(hs) if hs is not None else None
            except Exception:
                hs = None
            try:
                a_s = int(a_s) if a_s is not None else None
            except Exception:
                a_s = None

            rows.append({
                "game_id": None,
                "espn_event_id": str(event_id),
                "drive": drive_num,
                "play_id": p.get("id"),
                "sequenceNumber": p.get("sequenceNumber"),
                "qtr": period,
                "time": clock,

                "down": down if down not in (None, 0) else None,
                "ydstogo": ydstogo if ydstogo not in (None, 0) else None,

                "yardline_100": yardline_100,
                "posteam": posteam,
                "play_type": play_type,
                "desc": desc,
                "yards_gained": yards_gained,

                "start_downDistanceText": start.get("downDistanceText"),
                "start_possessionText": start.get("possessionText"),
                "end_downDistanceText": end.get("downDistanceText"),
                "end_possessionText": end.get("possessionText"),

                "wallclock": wallclock,
                "modified": modified,

                "home_team": home_team,
                "away_team": away_team,
                "home_score": hs if hs is not None else home_score,
                "away_score": a_s if a_s is not None else away_score,
            })

    df = pd.DataFrame(rows)

    if df.empty or "drive" not in df.columns:
        return df.reset_index(drop=True)

    if "sequenceNumber" in df.columns:
        df["_seq"] = pd.to_numeric(df["sequenceNumber"], errors="coerce")
        df = df.sort_values(["drive", "_seq"], na_position="last").drop(columns=["_seq"])
    else:
        df = df.sort_values(["drive", "play_id"], na_position="last")

    return df.reset_index(drop=True)

def _get_game_play_by_play_save_to_file(start_date: datetime.date, end_date: datetime.date, spread_spoke: bool = False, season_schedule: str = None):
    destination_directory = 'nfl/data/games'
    team_data = 'nfl/data/schedules/nfl_teams.csv'
    team_mapping = {}

    if spread_spoke:
        schedule_data = 'nfl/data/schedules/spreadspoke_scores.csv'

        with open(team_data, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                team_name = row[0]
                team_id = row[2]
                team_mapping[team_name] = team_id
        
        schedule_df = pd.read_csv(schedule_data)
        schedule_df['schedule_date'] = pd.to_datetime(schedule_df['schedule_date'])
        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        schedule_df = schedule_df[(schedule_df['schedule_date'] >= start_ts) & (schedule_df['schedule_date'] <= end_ts)]

        for index, row in schedule_df.iterrows():
            game_date_ts = row['schedule_date']
            game_date_str = game_date_ts.strftime('%Y%m%d')
            game_date_display = game_date_ts.strftime('%Y-%m-%d')
            home_team = row['team_home']
            away_team = row['team_away']

            output_file = destination_directory + "/" + game_date_str + "_" + home_team + "_" + away_team + ".csv"
            if os.path.exists(output_file):
                print("game already processed, skipping")
                continue

            print("processing game on", game_date_display + " between " + home_team + " and " + away_team)
            home_team_id = team_mapping[home_team]
            away_team_id = team_mapping[away_team]
            
            event_id = _find_event_id_by_date_and_teams(game_date_str, away_team_id, home_team_id)
            print("ESPN event_id:", event_id)
            pbp = _fetch_espn_pbp(event_id)
            pbp.to_csv(output_file, index=False)
            print("Wrote pbp.csv")

            time.sleep(2)
    else:
        schedule_data = season_schedule

        assert season_schedule is not None, "season_schedule is required"

        schedule_df = pd.read_csv(schedule_data)
        schedule_df['gameday'] = pd.to_datetime(schedule_df['gameday'])

        with open(team_data, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                team_code = row[8]
                team_id = row[2]
                team_mapping[team_code] = team_id
        
        for index, row in schedule_df.iterrows():
            game_date_ts = row['gameday']
            game_date_str = game_date_ts.strftime('%Y%m%d')
            away_team = row['away_team']
            home_team = row['home_team']
            away_team = team_mapping[away_team]
            home_team = team_mapping[home_team]
            game_id = row['game_id']
            output_file = destination_directory + "/" + game_id + ".csv"
            if os.path.exists(output_file):
                print("game already processed, skipping")
                continue

            print("processing game on", game_date_str + " between " + home_team + " and " + away_team)

            event_id = _find_event_id_by_date_and_teams(game_date_str, away_team, home_team)
            pbp = _fetch_espn_pbp(event_id)
            pbp.to_csv(output_file, index=False)
            print("Wrote pbp.csv")

            time.sleep(2)

if __name__ == "__main__":
    start_year = int(sys.argv[1])
    end_year = int(sys.argv[2])
    season_schedule = f'nfl/data/schedules/nfl_schedule_{start_year}_{end_year}.csv'
    _get_game_play_by_play_save_to_file(datetime.date(2025, 1, 1), datetime.date(2025, 12, 25), spread_spoke=False, season_schedule=season_schedule)