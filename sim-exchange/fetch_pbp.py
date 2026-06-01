"""Fetch an NBA-format play-by-play JSON for use by the sim's SnapshotFeeder.

The JSON shape the feeder expects is the NBA Live CDN format:
    { "game": { "actions": [ { "timeActual", "clock", "scoreHome", "scoreAway",
                                "actionType", "subType", "shotResult",
                                "period", "teamTricode", "description", ... }, ... ] } }

This script supports two ways to identify the game:
  1. Pass the NBA stats game_id directly (10-digit, e.g. "0042500304" for playoffs).
  2. Pass --date YYYY-MM-DD --home OKC --away SAS and we'll resolve the id
     via nba_api's LeagueGameFinder (requires `pip install nba_api`).

Output is written to sim-exchange/data_cache/<out>.json by default.

Examples:
    python fetch_pbp.py 0042500304
    python fetch_pbp.py 0042500304 --out OKCSAS_pbp.json
    python fetch_pbp.py --date 2026-05-26 --home OKC --away SAS --out OKCSAS_pbp.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

DATA_CACHE = Path(__file__).resolve().parent / "data_cache"
CDN_URL = "https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{game_id}.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
}


def resolve_game_id_from_date(date: str, home: str, away: str) -> str:
    """Use nba_api to find the NBA stats game_id for a given date+matchup."""
    try:
        from nba_api.stats.endpoints import leaguegamefinder
    except ImportError as e:
        raise SystemExit(
            "nba_api is required for --date lookup. Install with:\n"
            "  /opt/anaconda3/envs/kalshi-trader/bin/pip install nba_api"
        ) from e

    # Date format expected by leaguegamefinder is MM/DD/YYYY
    y, m, d = date.split("-")
    date_from = date_to = f"{m}/{d}/{y}"
    finder = leaguegamefinder.LeagueGameFinder(
        date_from_nullable=date_from, date_to_nullable=date_to, timeout=30
    )
    rows = finder.get_data_frames()[0]
    if rows.empty:
        raise SystemExit(f"No games found on {date}")

    home_u, away_u = home.upper(), away.upper()
    # Each game has two rows (one per team). Match by matchup string.
    for _, r in rows.iterrows():
        team = str(r["TEAM_ABBREVIATION"]).upper()
        matchup = str(r["MATCHUP"]).upper()
        # MATCHUP looks like "OKC vs. SAS" (home) or "SAS @ OKC" (away)
        if team == home_u and " VS." in matchup and matchup.endswith(away_u):
            return str(r["GAME_ID"])
        if team == away_u and " @ " in matchup and matchup.endswith(home_u):
            return str(r["GAME_ID"])

    raise SystemExit(
        f"Couldn't find a {away_u}@{home_u} matchup on {date}. Available:\n"
        + rows[["GAME_ID", "TEAM_ABBREVIATION", "MATCHUP"]].to_string(index=False)
    )


def fetch_pbp(game_id: str) -> dict:
    """Download the live-CDN PBP JSON for an NBA stats game_id."""
    url = CDN_URL.format(game_id=game_id)
    r = requests.get(url, headers=HEADERS, timeout=20)
    if r.status_code == 403:
        raise SystemExit(
            f"403 from CDN for {game_id}. Two common causes:\n"
            "  - game_id wrong format (NBA stats ids are 10 digits, e.g. 0042500304)\n"
            "  - game hasn't started yet (live CDN only serves played games)"
        )
    if r.status_code == 404:
        raise SystemExit(f"404 from CDN — game_id {game_id} not found")
    r.raise_for_status()
    return r.json()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("game_id", nargs="?", help="NBA stats game_id (10 digits)")
    p.add_argument("--date", help="YYYY-MM-DD; use with --home and --away for lookup")
    p.add_argument("--home", help="Home team tricode (e.g. OKC)")
    p.add_argument("--away", help="Away team tricode (e.g. SAS)")
    p.add_argument("--out", help="Output filename in data_cache/ (default: <game_id>_pbp.json)")
    args = p.parse_args()

    if not args.game_id and not (args.date and args.home and args.away):
        p.error("provide game_id OR (--date AND --home AND --away)")

    if args.game_id:
        game_id = args.game_id
        if len(game_id) != 10 or not game_id.isdigit():
            print(f"warning: {game_id!r} doesn't look like a 10-digit NBA stats id", file=sys.stderr)
    else:
        print(f"Resolving NBA game_id for {args.away}@{args.home} on {args.date} ...")
        game_id = resolve_game_id_from_date(args.date, args.home, args.away)
        print(f"  -> game_id = {game_id}")

    print(f"Fetching PBP from {CDN_URL.format(game_id=game_id)}")
    data = fetch_pbp(game_id)
    actions = data.get("game", {}).get("actions", [])
    if not actions:
        print("warning: response has no actions[] — file would be empty", file=sys.stderr)
    print(f"  -> {len(actions)} actions")

    out_name = args.out or f"{game_id}_pbp.json"
    DATA_CACHE.mkdir(parents=True, exist_ok=True)
    out_path = DATA_CACHE / out_name
    with open(out_path, "w") as f:
        json.dump(data, f)
    size_kb = out_path.stat().st_size / 1024
    print(f"\nWrote {out_path} ({size_kb:,.1f} KB)")
    print(f"\nNext: in sim-exchange/data_cache/games.json, set\n  \"nba_pbp_json\": \"{out_name}\"")


if __name__ == "__main__":
    main()
