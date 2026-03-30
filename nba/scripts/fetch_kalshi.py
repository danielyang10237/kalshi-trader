
from __future__ import annotations

import argparse
import base64
import glob
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv

# Load .env from project root
# __file__ is now in nba/scripts/, so parent.parent = kalshi-bot/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Default output directory: nba/data/kalshi_live
NBA_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = NBA_DIR / "data" / "kalshi_live"

# Hardcoded Kalshi API configuration
KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_API_KEY = os.getenv("KALSHI_API_KEY")
KALSHI_PRIVATE_KEY_PATH = os.getenv(
    "KALSHI_PRIVATE_KEY_PATH",
    str(PROJECT_ROOT / "trading-system" / "python_app" / "kalshi_client" / "kalshi_key.pem")
)

if not KALSHI_API_KEY:
    raise RuntimeError("Missing KALSHI_API_KEY in .env file")


# --- Step 1: Team mapping (full NBA mapping) ---
TEAM_CODES = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}

# All-Star team codes to skip (not real NBA games)
ALL_STAR_CODES = {"STARS", "STRIPES", "WORLD"}

# PBP filename codes that differ from Kalshi ticker codes
PBP_TO_KALSHI_CODE = {
    "GS": "GSW",
    "NY": "NYK",
    "SA": "SAS",
    "NO": "NOP",
    "WSH": "WAS",
    "UTAH": "UTA",
}


def normalize_team_code(code: str) -> str:
    """Convert PBP filename team code to Kalshi ticker code."""
    return PBP_TO_KALSHI_CODE.get(code, code)


def generate_kalshi_tickers(away_code: str, home_code: str, game_date: pd.Timestamp) -> Tuple[str, str]:
    """
    Format: KXNBAGAME-[YY][MMM][DD][AWAY][HOME]-[WINNER]
    Example: KXNBAGAME-26JAN30DETGSW-GSW (Jan 30, 2026, Detroit @ Golden State, GSW wins)

    Note: Date format is YYMMMDD (year first), team order is AWAY then HOME
    """
    # Format: YY + MMM + DD (e.g., "26JAN30" for Jan 30, 2026)
    date_str = pd.to_datetime(game_date).strftime("%y%b%d").upper()

    # Both tickers have same base: AWAY @ HOME
    base = f"KXNBAGAME-{date_str}{away_code}{home_code}"
    home_ticker = f"{base}-{home_code}"  # Home team wins
    away_ticker = f"{base}-{away_code}"  # Away team wins
    return home_ticker, away_ticker


# --- Step 2: Extract game window from PBP ---
def get_game_window(pbp_df: pd.DataFrame, pre_buffer_min: int = 30) -> Tuple[pd.Timestamp, pd.Timestamp]:
    """
    Uses first/last action times in pbp_df['wallclock'].
    Returns UTC timestamps with a pre-game buffer to capture pre-game trades.
    """
    if "wallclock" not in pbp_df.columns:
        raise ValueError("PBP CSV must contain a 'wallclock' column.")

    # Force UTC parsing
    pbp_df = pbp_df.copy()
    pbp_df["wallclock"] = pd.to_datetime(pbp_df["wallclock"], utc=True, errors="coerce")
    pbp_df = pbp_df.dropna(subset=["wallclock"]).sort_values("wallclock")

    if pbp_df.empty:
        raise ValueError("No valid timestamps found in 'wallclock'.")

    game_start_utc = pbp_df["wallclock"].min() - pd.Timedelta(minutes=pre_buffer_min)
    game_end_utc = pbp_df["wallclock"].max()

    return game_start_utc, game_end_utc


# --- Step 3: Kalshi client + trade pulling (paginated) ---

def _load_private_key(pem_path: str):
    """Load RSA private key from PEM file"""
    with open(pem_path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def _sign_pss(private_key, text: str) -> str:
    """Sign text using RSA-PSS"""
    sig = private_key.sign(
        text.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode("utf-8")


def _make_auth_headers(private_key, key_id: str, method: str, path: str) -> Dict[str, str]:
    """Generate Kalshi authentication headers"""
    timestamp = str(int(time.time() * 1000))
    normalized_path = path.split("?")[0]
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    msg = timestamp + method.upper() + normalized_path
    signature = _sign_pss(private_key, msg)
    return {
        "Content-Type": "application/json",
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }


@dataclass
class KalshiClient:
    base_url: str = field(default=KALSHI_BASE_URL)
    api_key: str = field(default_factory=lambda: KALSHI_API_KEY)
    private_key_path: str = field(default_factory=lambda: KALSHI_PRIVATE_KEY_PATH)
    timeout_s: int = 30
    _private_key: Any = field(default=None, init=False, repr=False)

    def __post_init__(self):
        self._private_key = _load_private_key(self.private_key_path)

    def _headers(self, method: str, path: str) -> Dict[str, str]:
        """Generate authenticated headers for a request"""
        full_path = f"/trade-api/v2{path}"
        return _make_auth_headers(self._private_key, self.api_key, method, full_path)

    def get_market(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Check if a market exists and get its details"""
        path = f"/markets/{ticker}"
        url = f"{self.base_url.rstrip('/')}{path}"
        headers = self._headers("GET", path)
        r = requests.get(url, headers=headers, timeout=self.timeout_s)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def search_markets(self, query: str, limit: int = 20) -> Dict[str, Any]:
        """Search for markets by text query"""
        path = "/markets"
        url = f"{self.base_url.rstrip('/')}{path}"
        headers = self._headers("GET", path)
        params = {"limit": limit}
        r = requests.get(url, headers=headers, params=params, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    def get_trades(self, ticker: str, params: Dict[str, Any], debug: bool = False) -> Dict[str, Any]:
        # Kalshi v2 API: GET /trade-api/v2/markets/trades
        path = "/markets/trades"
        params_with_ticker = {**params, "ticker": ticker}
        url = f"{self.base_url.rstrip('/')}{path}"
        headers = self._headers("GET", path)

        if debug:
            print(f"    [DEBUG] GET {url}")
            print(f"    [DEBUG] Params: {params_with_ticker}")

        r = requests.get(url, headers=headers, params=params_with_ticker, timeout=self.timeout_s)

        if debug:
            print(f"    [DEBUG] Status: {r.status_code}")
            print(f"    [DEBUG] Response: {r.text[:500] if r.text else 'empty'}")

        if r.status_code == 404:
            raise FileNotFoundError(f"Endpoint not found (404)")
        r.raise_for_status()
        return r.json()


def _extract_trades_and_cursor(resp: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    trades = resp.get("trades")
    if trades is None:
        trades = resp.get("data", [])
    cursor = resp.get("cursor") or resp.get("next_cursor")
    return trades or [], cursor


def get_game_trades(
    kalshi_client: KalshiClient,
    ticker: str,
    start_ts_utc: pd.Timestamp,
    end_ts_utc: pd.Timestamp,
    page_limit: int = 1000,
    rate_sleep_s: float = 0.15,
    max_pages: int = 500,
    debug: bool = False,
) -> pd.DataFrame:
    """
    Fetch all trades for market during [start_ts, end_ts] window.
    Assumes API supports min_ts/max_ts in unix seconds.
    """
    all_trades: List[Dict[str, Any]] = []
    cursor: Optional[str] = None

    min_ts = int(pd.to_datetime(start_ts_utc, utc=True).timestamp())
    max_ts = int(pd.to_datetime(end_ts_utc, utc=True).timestamp())

    if debug:
        print(f"    [DEBUG] Fetching trades for {ticker}")
        print(f"    [DEBUG] Time range: {start_ts_utc} to {end_ts_utc}")
        print(f"    [DEBUG] Unix timestamps: {min_ts} to {max_ts}")

    for page_num in range(max_pages):
        params = {"limit": page_limit, "min_ts": min_ts, "max_ts": max_ts}
        if cursor:
            params["cursor"] = cursor

        try:
            resp = kalshi_client.get_trades(ticker, params=params, debug=(debug and page_num == 0))
        except FileNotFoundError:
            # Market may not have existed
            return pd.DataFrame()
        except requests.HTTPError as e:
            # Handle rate limit or transient errors more gracefully
            status = getattr(e.response, "status_code", None)
            if status in (429, 500, 502, 503, 504):
                time.sleep(1.0)
                continue
            raise

        trades, cursor = _extract_trades_and_cursor(resp)
        if not trades:
            break

        all_trades.extend(trades)
        if not cursor:
            break

        time.sleep(rate_sleep_s)

    return pd.DataFrame(all_trades)


# --- Step 4: Create 100ms time series with high/low prices ---
def _normalize_trade_timestamp(trades_df: pd.DataFrame) -> pd.Series:
    # Try common field names
    for col in ("timestamp", "ts", "created_time", "time"):
        if col in trades_df.columns:
            # If it's already datetime-like, to_datetime handles it.
            # If numeric unix seconds, unit='s' works.
            return pd.to_datetime(trades_df[col], utc=True, errors="coerce", unit="s" if pd.api.types.is_numeric_dtype(trades_df[col]) else None)
    raise ValueError("Could not find a timestamp column in trades (expected one of: timestamp, ts, created_time, time).")


def _get_yes_price_cents(trades_df: pd.DataFrame) -> pd.Series:
    # Most commonly "price" is YES price in cents.
    if "price" in trades_df.columns:
        return pd.to_numeric(trades_df["price"], errors="coerce")

    # New API format: yes_price_dollars (decimal dollars, e.g. "0.5500")
    if "yes_price_dollars" in trades_df.columns:
        return pd.to_numeric(trades_df["yes_price_dollars"], errors="coerce") * 100

    # Fallbacks if your schema differs.
    for col in ("yes_price", "yesPrice", "p"):
        if col in trades_df.columns:
            return pd.to_numeric(trades_df[col], errors="coerce")

    raise ValueError("Could not find a YES price column in trades (expected 'price', 'yes_price_dollars', or 'yes_price').")


def _get_trade_count(trades_df: pd.DataFrame) -> pd.Series:
    """Get trade size/count from trades"""
    for col in ("count", "count_fp", "size", "volume", "qty"):
        if col in trades_df.columns:
            return pd.to_numeric(trades_df[col], errors="coerce").fillna(0)
    return pd.Series([1] * len(trades_df))  # Default to 1 if no size column


def create_100ms_timeseries(
    trades_df: pd.DataFrame,
    game_start_utc: pd.Timestamp,
    game_end_utc: pd.Timestamp,
    ticker: str,
) -> pd.DataFrame:
    """
    Create a time series with 100ms granularity.
    Each row has: timestamp, high_price, low_price, volume, trade_count
    Empty buckets will have NaN for prices and 0 for volume/count.
    """
    # Create the full 100ms time index from start to end
    time_index = pd.date_range(
        start=game_start_utc,
        end=game_end_utc,
        freq="100ms",  # 0.1 second intervals
        tz="UTC",
    )

    # Initialize result DataFrame with all timestamps
    result = pd.DataFrame({
        "timestamp": time_index,
        "ticker": ticker,
        "high_price_cents": pd.NA,
        "low_price_cents": pd.NA,
        "volume": 0,
        "trade_count": 0,
    })

    if trades_df is None or trades_df.empty:
        return result

    df = trades_df.copy()

    # Parse timestamps
    df["_ts"] = _normalize_trade_timestamp(df)
    df = df.dropna(subset=["_ts"])

    # Filter to game window
    start = pd.to_datetime(game_start_utc, utc=True)
    end = pd.to_datetime(game_end_utc, utc=True)
    df = df[(df["_ts"] >= start) & (df["_ts"] <= end)]

    if df.empty:
        return result

    # Get price and size
    df["_price"] = _get_yes_price_cents(df)
    df["_size"] = _get_trade_count(df)
    df = df.dropna(subset=["_price"])

    if df.empty:
        return result

    # Floor timestamps to 100ms buckets
    df["_bucket"] = df["_ts"].dt.floor("100ms")

    # Aggregate by bucket
    agg = df.groupby("_bucket").agg(
        high_price_cents=("_price", "max"),
        low_price_cents=("_price", "min"),
        volume=("_size", "sum"),
        trade_count=("_price", "count"),
    ).reset_index()
    agg.rename(columns={"_bucket": "timestamp"}, inplace=True)

    # Merge aggregated data into the full time index
    result = result.drop(columns=["high_price_cents", "low_price_cents", "volume", "trade_count"])
    result = result.merge(agg, on="timestamp", how="left")

    # Fill NaN volumes/counts with 0
    result["volume"] = result["volume"].fillna(0).astype(int)
    result["trade_count"] = result["trade_count"].fillna(0).astype(int)

    return result


def compute_game_prices(trades_df: pd.DataFrame, game_start_utc: pd.Timestamp, game_end_utc: pd.Timestamp) -> Dict[str, Any]:
    """Summary stats for the entire game (for backward compatibility)"""
    if trades_df is None or trades_df.empty:
        return {"best_yes_price": None, "worst_yes_price": None, "trade_count": 0, "total_volume": 0}

    df = trades_df.copy()

    df["_ts"] = _normalize_trade_timestamp(df)
    df = df.dropna(subset=["_ts"])

    # Filter to game window
    start = pd.to_datetime(game_start_utc, utc=True)
    end = pd.to_datetime(game_end_utc, utc=True)
    df = df[(df["_ts"] >= start) & (df["_ts"] <= end)]
    if df.empty:
        return {"best_yes_price": None, "worst_yes_price": None, "trade_count": 0, "total_volume": 0}

    yes_price_cents = _get_yes_price_cents(df).dropna()

    if yes_price_cents.empty:
        return {"best_yes_price": None, "worst_yes_price": None, "trade_count": 0, "total_volume": int(pd.to_numeric(df.get("size", 0), errors="coerce").fillna(0).sum())}

    best_yes = float((yes_price_cents.max()) / 100.0)
    worst_yes = float((yes_price_cents.min()) / 100.0)

    volume = int(pd.to_numeric(df.get("size", 0), errors="coerce").fillna(0).sum())

    return {
        "best_yes_price": best_yes,
        "worst_yes_price": worst_yes,
        "trade_count": int(len(yes_price_cents)),
        "total_volume": volume,
    }


# --- Helpers: schedule parsing + PBP file finding ---
def _first_existing_col(df: pd.DataFrame, candidates: List[str]) -> str:
    for c in candidates:
        if c in df.columns:
            return c
    raise ValueError(f"Could not find any of these columns in schedule CSV: {candidates}")


def _parse_matchup_to_teams(matchup: str) -> Tuple[str, str]:
    """
    Parse matchup string to get (away_team_code, home_team_code).

    "LAL vs. GSW" -> home=LAL, away=GSW (team1 vs. team2 means team1 is home)
    "BKN @ CHA" -> home=CHA, away=BKN (team1 @ team2 means team1 is away)
    """
    matchup = matchup.strip()

    if " vs. " in matchup:
        parts = matchup.split(" vs. ")
        home_code = parts[0].strip()
        away_code = parts[1].strip()
    elif " @ " in matchup:
        parts = matchup.split(" @ ")
        away_code = parts[0].strip()
        home_code = parts[1].strip()
    else:
        raise ValueError(f"Unknown matchup format: {matchup}")

    return away_code, home_code


def _code_to_team_name(code: str) -> str:
    """Convert team code (e.g., 'LAL') to full team name (e.g., 'Los Angeles Lakers')"""
    # Reverse lookup in TEAM_CODES
    for name, c in TEAM_CODES.items():
        if c == code:
            return name
    raise ValueError(f"Unknown team code: {code}")


def team_code_to_full_name(team_code: str) -> str:
    """Convert team code (e.g., 'LAL') to full name (e.g., 'Los Angeles Lakers')."""
    for full_name, code in TEAM_CODES.items():
        if code == team_code:
            return full_name
    raise ValueError(f"Unknown team code: {team_code}")


def read_all_games_from_schedule(schedule_csv: str) -> List[Tuple[str, pd.Timestamp, str, str]]:
    """
    Returns list of (espn_id, game_date, away_code, home_code) for completed games only.
    Uses MATCHUP column for team codes.
    """
    sched = pd.read_csv(schedule_csv)
    date_col = _first_existing_col(sched, ["GAME_DATE", "game_date", "DATE", "Date"])

    # Filter to completed games only
    if "status_type_completed" in sched.columns:
        sched = sched[sched["status_type_completed"] == True].copy()

    games = []
    for idx, row in sched.iterrows():
        espn_id = str(row["GAME_ID"])
        game_date = pd.to_datetime(row[date_col])

        # Use MATCHUP column for team codes
        if "MATCHUP" in sched.columns:
            matchup = str(row["MATCHUP"])
            try:
                away_code, home_code = _parse_matchup_to_teams(matchup)
            except ValueError as e:
                print(f"  SKIP row {idx}: {e}")
                continue
        else:
            # Fallback to abbreviation columns
            home_code = str(row.get("home_abbreviation", ""))
            away_code = str(row.get("away_abbreviation", ""))

        # Skip All-Star games
        if away_code in ALL_STAR_CODES or home_code in ALL_STAR_CODES:
            continue

        games.append((espn_id, game_date, away_code, home_code))

    return games


def parse_pbp_filename(pbp_file: str) -> Tuple[str, str, str]:
    """
    Parse game info from PBP filename.
    Expected format: ESPNID_AWAY_HOME.csv (e.g. 401809234_CLE_NYK.csv)
    Returns: (espn_id, away_code, home_code)
    """
    base = os.path.basename(pbp_file).replace(".csv", "")
    parts = base.split("_")
    if len(parts) < 3:
        raise ValueError(f"Invalid PBP filename format: {pbp_file}. Expected: ESPNID_AWAY_HOME.csv")

    espn_id = parts[0]
    away_code = normalize_team_code(parts[1])
    home_code = normalize_team_code(parts[2])

    return espn_id, away_code, home_code


def get_game_date_from_pbp(pbp_file: str) -> pd.Timestamp:
    """Read the game date from PBP CSV, converted to US Eastern time.
    Kalshi tickers use the US Eastern date (e.g. a 10:30 PM ET game on Mar 12
    has a UTC wallclock of Mar 13 02:30Z, but the ticker uses Mar 12)."""
    df = pd.read_csv(pbp_file, nrows=1)
    if "game_date_time" in df.columns and pd.notna(df["game_date_time"].iloc[0]):
        utc_dt = pd.to_datetime(df["game_date_time"].iloc[0], utc=True)
        eastern_dt = utc_dt.tz_convert("US/Eastern")
        return eastern_dt.normalize()
    if "game_date" not in df.columns:
        raise ValueError(f"PBP file {pbp_file} has no 'game_date' column.")
    return pd.to_datetime(df["game_date"].iloc[0])


def get_output_path(pbp_file: str, out_dir: Optional[str] = None) -> str:
    """Get the expected output path for a given PBP file.
    Uses raw ESPN team codes from the PBP filename (e.g. GS, NY, SA)
    to stay consistent with the games_live/ naming convention."""
    if not out_dir:
        out_dir = str(DEFAULT_OUT_DIR)
    base = os.path.basename(pbp_file).replace(".csv", "_kalshi_100ms.csv")
    return os.path.join(out_dir, base)


def find_pbp_file(pbp_dir: str, espn_event_id: str) -> Optional[str]:
    """
    Find PBP file by ESPN event ID.
    Looks for files matching {espn_id}_*.csv in pbp_dir.
    """
    pattern = os.path.join(pbp_dir, f"{espn_event_id}_*.csv")
    hits = sorted(glob.glob(pattern))
    if hits:
        return hits[0]
    return None


# --- Step 5: Main pipeline ---
def process_one_game(
    pbp_file: str,
    kalshi_client: KalshiClient,
    game_date: pd.Timestamp,
    away_code: str,
    home_code: str,
    out_dir: Optional[str] = None,
) -> str:
    pbp_df = pd.read_csv(pbp_file)

    home_ticker, away_ticker = generate_kalshi_tickers(away_code, home_code, game_date)
    start_ts, end_ts = get_game_window(pbp_df)

    # Derive full names for CSV metadata columns
    try:
        home_team_name = team_code_to_full_name(home_code)
    except ValueError:
        home_team_name = home_code
    try:
        away_team_name = team_code_to_full_name(away_code)
    except ValueError:
        away_team_name = away_code

    print(f"  Game window: {start_ts} to {end_ts}")
    print(f"  Home ticker: {home_ticker}")
    print(f"  Away ticker: {away_ticker}")

    # Check if markets exist
    print("  Checking if markets exist...")
    home_market = kalshi_client.get_market(home_ticker)
    away_market = kalshi_client.get_market(away_ticker)

    if home_market:
        m = home_market.get('market', {})
        print(f"    Home market found: {m.get('title', 'N/A')}")
        print(f"      Status: {m.get('status')}, Volume: {m.get('volume')}, Open Interest: {m.get('open_interest')}")
    else:
        print(f"    WARNING: Home market NOT FOUND: {home_ticker}")

    if away_market:
        m = away_market.get('market', {})
        print(f"    Away market found: {m.get('title', 'N/A')}")
        print(f"      Status: {m.get('status')}, Volume: {m.get('volume')}, Open Interest: {m.get('open_interest')}")
    else:
        print(f"    WARNING: Away market NOT FOUND: {away_ticker}")

    # Fetch all trades (with debug on first request)
    print("  Fetching home team trades...")
    home_trades = get_game_trades(kalshi_client, home_ticker, start_ts, end_ts, debug=True)
    print(f"    Got {len(home_trades)} raw trades")

    print("  Fetching away team trades...")
    away_trades = get_game_trades(kalshi_client, away_ticker, start_ts, end_ts, debug=True)
    print(f"    Got {len(away_trades)} raw trades")

    # Create 100ms time series for both markets
    print("  Creating 100ms time series...")
    home_series = create_100ms_timeseries(home_trades, start_ts, end_ts, home_ticker)
    away_series = create_100ms_timeseries(away_trades, start_ts, end_ts, away_ticker)

    # Merge the two series on timestamp
    home_series = home_series.rename(columns={
        "high_price_cents": "home_high_cents",
        "low_price_cents": "home_low_cents",
        "volume": "home_volume",
        "trade_count": "home_trades",
    }).drop(columns=["ticker"])

    away_series = away_series.rename(columns={
        "high_price_cents": "away_high_cents",
        "low_price_cents": "away_low_cents",
        "volume": "away_volume",
        "trade_count": "away_trades",
    }).drop(columns=["ticker"])

    combined = home_series.merge(away_series, on="timestamp", how="outer")

    # Add game metadata columns
    combined.insert(0, "game_date", pd.to_datetime(game_date).strftime("%Y-%m-%d"))
    combined.insert(1, "home_team", home_team_name)
    combined.insert(2, "away_team", away_team_name)
    combined.insert(3, "home_ticker", home_ticker)
    combined.insert(4, "away_ticker", away_ticker)

    # Sort by timestamp
    combined = combined.sort_values("timestamp").reset_index(drop=True)

    # Summary stats before filtering
    total_rows = len(combined)
    home_trades_with_data = combined[combined["home_trades"] > 0]
    away_trades_with_data = combined[combined["away_trades"] > 0]

    # Filter: only keep rows where at least one trade column is non-zero
    combined = combined[
        (combined["home_high_cents"] > 0) |
        (combined["home_low_cents"] > 0) |
        (combined["home_volume"] > 0) |
        (combined["home_trades"] > 0) |
        (combined["away_high_cents"] > 0) |
        (combined["away_low_cents"] > 0) |
        (combined["away_volume"] > 0) |
        (combined["away_trades"] > 0)
    ].reset_index(drop=True)

    print(f"  Time series: {total_rows} total rows -> {len(combined)} rows with trades (100ms buckets)")
    print(f"    Home: {len(home_trades_with_data)} buckets with trades")
    print(f"    Away: {len(away_trades_with_data)} buckets with trades")

    # Save - ensure output directory exists
    if not out_dir:
        out_dir = str(DEFAULT_OUT_DIR)
    os.makedirs(out_dir, exist_ok=True)

    base = os.path.basename(pbp_file).replace(".csv", "_kalshi_100ms.csv")
    out_path = os.path.join(out_dir, base)
    combined.to_csv(out_path, index=False)
    return out_path


def main():
    ap = argparse.ArgumentParser(
        description="Fetch Kalshi trade data for NBA games",
        epilog="""
Usage modes:
  1. Process all PBP files in a directory: --pbp_dir path/to/games_live/2026/
  2. Process from schedule CSV: --schedule_csv path.csv --pbp_dir path/to/games_live/2026/
  3. Process single game: --pbp_file path.csv
        """
    )

    # Batch mode: process all games from schedule
    ap.add_argument("--schedule_csv", help="Schedule CSV; processes ALL completed games.")
    ap.add_argument("--pbp_dir", help="Directory containing per-game PBP CSVs. Can be used alone to process all files, or with --schedule_csv.")

    # Single-game mode (explicit pbp file)
    ap.add_argument("--pbp_file", help="Explicit PBP CSV file to process.")

    ap.add_argument("--out_dir", default=None, help=f"Where to write output CSV (default: {DEFAULT_OUT_DIR}).")
    ap.add_argument("--delay", type=float, default=0.5, help="Delay between processing games (seconds)")

    # Debug mode
    ap.add_argument("--list-nba-markets", action="store_true", help="List available NBA markets and exit")
    ap.add_argument("--test-ticker", help="Test if a specific ticker exists (e.g., KXNBAGAME-26JAN30CLEPHX-PHX)")

    args = ap.parse_args()

    # Client uses hardcoded base URL and loads auth from .env
    client = KalshiClient()

    # Debug: test a specific ticker
    if args.test_ticker:
        print(f"Testing ticker: {args.test_ticker}")
        market = client.get_market(args.test_ticker)
        if market:
            m = market.get("market", {})
            print(f"  FOUND!")
            print(f"  Title: {m.get('title')}")
            print(f"  Status: {m.get('status')}")
            print(f"  Series: {m.get('series_ticker')}")
            print(f"  Event: {m.get('event_ticker')}")
        else:
            print(f"  NOT FOUND")
        return

    # Debug: list NBA markets
    if args.list_nba_markets:
        print("Fetching NBA game markets from Kalshi...")
        try:
            # Search for KXNBAGAME series markets
            path = "/markets"
            url = f"{client.base_url}{path}"
            headers = client._headers("GET", path)

            # Try to find KXNBAGAME markets specifically
            params = {"series_ticker": "KXNBAGAME", "limit": 100}
            r = requests.get(url, headers=headers, params=params, timeout=30)

            if r.ok:
                resp = r.json()
                markets = resp.get("markets", [])
                print(f"\nFound {len(markets)} KXNBAGAME markets:")
                for m in markets[:30]:
                    print(f"  {m.get('ticker')}: {m.get('title')} (status: {m.get('status')})")
            else:
                print(f"Series search failed: {r.status_code}")

            # Also try a general search
            print("\n--- General market search ---")
            params2 = {"limit": 500}
            r2 = requests.get(url, headers=headers, params=params2, timeout=30)
            if r2.ok:
                markets2 = r2.json().get("markets", [])
                nba_game_markets = [m for m in markets2 if m.get("ticker", "").startswith("KXNBAGAME")]
                print(f"Found {len(nba_game_markets)} KXNBAGAME markets in first 500:")
                for m in nba_game_markets[:30]:
                    print(f"  {m.get('ticker')}: {m.get('title')} (status: {m.get('status')})")

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
        return

    # --- BATCH MODE: process all games in schedule CSV ---
    if args.schedule_csv:
        if not args.pbp_dir:
            raise SystemExit("If you pass --schedule_csv, you must also pass --pbp_dir.")

        # Process ALL completed games
        games = read_all_games_from_schedule(args.schedule_csv)
        print(f"Found {len(games)} completed games in schedule CSV")

        success_count = 0
        skip_count = 0
        error_count = 0
        no_pbp_count = 0

        for i, (espn_id, game_date, away_code, home_code) in enumerate(games, 1):
            print(f"\n[{i}/{len(games)}] {game_date.date()}: {away_code} @ {home_code} (ESPN {espn_id})")

            # Find PBP file by ESPN event ID
            pbp_file = find_pbp_file(args.pbp_dir, espn_id)
            if not pbp_file:
                print(f"  SKIP: No PBP file found for ESPN ID {espn_id}")
                no_pbp_count += 1
                continue

            # Check if output already exists
            out_path = get_output_path(pbp_file, args.out_dir)
            if os.path.exists(out_path):
                print(f"  SKIP: Output already exists: {out_path}")
                skip_count += 1
                continue

            # Process the game
            try:
                out = process_one_game(pbp_file, client, game_date, away_code, home_code, out_dir=args.out_dir)
                print(f"  OK: Wrote {out}")
                success_count += 1
            except Exception as e:
                print(f"  ERROR: {e}")
                error_count += 1

            # Rate limit between games
            if i < len(games):
                time.sleep(args.delay)

        print(f"\n{'='*50}")
        print(f"Summary:")
        print(f"  Total games:     {len(games)}")
        print(f"  Success:         {success_count}")
        print(f"  Already exists:  {skip_count}")
        print(f"  No PBP file:     {no_pbp_count}")
        print(f"  Errors:          {error_count}")
        return

    # --- DIRECTORY MODE: process all PBP files in a directory ---
    if args.pbp_dir and not args.schedule_csv:
        print(f"Processing all PBP files in {args.pbp_dir}")

        # Find all CSV files
        pbp_files = sorted(glob.glob(os.path.join(args.pbp_dir, "*.csv")))
        if not pbp_files:
            print(f"No CSV files found in {args.pbp_dir}")
            return

        print(f"Found {len(pbp_files)} PBP files")
        processed = 0
        skipped = 0
        skipped_allstar = 0
        errors = 0

        for pbp_file in pbp_files:
            try:
                # Check if output already exists
                out_path = get_output_path(pbp_file, args.out_dir)
                if os.path.exists(out_path):
                    skipped += 1
                    if skipped % 10 == 0:
                        print(f"  Skipped {skipped} existing files...")
                    continue

                # Parse game info from filename
                espn_id, away_code, home_code = parse_pbp_filename(pbp_file)

                # Skip All-Star games
                if away_code in ALL_STAR_CODES or home_code in ALL_STAR_CODES:
                    print(f"  SKIP All-Star game: {os.path.basename(pbp_file)}")
                    skipped_allstar += 1
                    continue

                # Get game date from PBP file contents
                game_date = get_game_date_from_pbp(pbp_file)

                print(f"\n[{processed + 1}] Processing: {os.path.basename(pbp_file)}")
                print(f"  Game: {away_code} @ {home_code} on {game_date.strftime('%Y-%m-%d')}")

                out = process_one_game(pbp_file, client, game_date, away_code, home_code, out_dir=args.out_dir)
                print(f"  [OK] Wrote: {out}")
                processed += 1

                # Delay between games
                if args.delay > 0:
                    time.sleep(args.delay)

            except Exception as e:
                print(f"  [ERROR] Failed to process {pbp_file}: {e}")
                errors += 1
                continue

        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        print(f"Total files: {len(pbp_files)}")
        print(f"Processed: {processed}")
        print(f"Skipped (already exist): {skipped}")
        print(f"Skipped (All-Star): {skipped_allstar}")
        print(f"Errors: {errors}")
        return

    # --- SINGLE GAME MODE ---
    if args.pbp_file:
        espn_id, away_code, home_code = parse_pbp_filename(args.pbp_file)
        game_date = get_game_date_from_pbp(args.pbp_file)

        print(f"Processing single game: {away_code} @ {home_code} on {game_date.strftime('%Y-%m-%d')}")
        out = process_one_game(args.pbp_file, client, game_date, away_code, home_code, out_dir=args.out_dir)
        print(f"[OK] Wrote: {out}")
        return

    raise SystemExit("Pass either --schedule_csv, --pbp_file, or --pbp_dir.")


if __name__ == "__main__":
    main()
