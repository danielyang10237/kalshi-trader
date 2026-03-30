"""
Build a prior-model feature row for a FUTURE NBA game from roster data.

Constructs the same feature vector that the training pipeline produces
so the prior XGBoost model can score it.

Can be called standalone or imported by the trading engine.
"""

import json
import pickle
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ── Paths (relative to nba/) ──────────────────────────────────────────────

# __file__ is in nba/scripts/, so parent.parent = nba/
BASE_DIR = Path(__file__).parent.parent
TRAINING_CSV = BASE_DIR / "data" / "training_games.csv"
PLAYER_STATS_DIR = BASE_DIR / "data" / "player_stats"
TEAMS_CSV = BASE_DIR / "data" / "nba_teams.csv"
MODEL_PKL = BASE_DIR / "prior_models" / "xgboost_prior_deploy.pkl"
CONFIG_JSON = BASE_DIR / "prior_models" / "config_deploy.json"

# ── Module-level caches (loaded once, reused across calls) ────────────────

_cached_model = None
_cached_feature_cols: list[str] | None = None
_cached_training: pd.DataFrame | None = None
_cached_player_stats: dict[str, pd.DataFrame | None] = {}


def _get_model():
    global _cached_model, _cached_feature_cols
    if _cached_model is None:
        with open(CONFIG_JSON) as f:
            _cached_feature_cols = json.load(f)["feature_cols"]
        with open(MODEL_PKL, "rb") as f:
            _cached_model = pickle.load(f)
    return _cached_model, _cached_feature_cols


def _get_training() -> pd.DataFrame:
    global _cached_training
    if _cached_training is None:
        _cached_training = pd.read_csv(TRAINING_CSV, dtype={"game_id": str})
        _cached_training["game_date"] = pd.to_datetime(_cached_training["game_date"])
    return _cached_training


def _get_player_stats(pid: str) -> pd.DataFrame | None:
    if pid in _cached_player_stats:
        return _cached_player_stats[pid]
    pfile = PLAYER_STATS_DIR / f"{pid}.csv"
    if not pfile.exists():
        _cached_player_stats[pid] = None
        return None
    try:
        df = pd.read_csv(pfile, dtype={"game_id": str, "athlete_id": str, "team_id": str})
        _cached_player_stats[pid] = df
        return df
    except Exception:
        _cached_player_stats[pid] = None
        return None


def warmup():
    """Pre-load model, config, and training CSV. Call at server boot."""
    _get_model()
    _get_training()

# ── Team name mapping ─────────────────────────────────────────────────────

HIST_TO_MODERN = {
    "SEA": "OKC", "VAN": "MEM", "NJN": "BKN", "NJ": "BKN", "BRK": "BKN",
    "NOH": "NOP", "NOK": "NOP", "NO": "NOP",
    "CHH": "CHA", "CHO": "CHA", "CHA": "CHA",
    "WSB": "WAS", "WSH": "WAS", "BAL": "WAS", "WAS": "WAS",
    "PHO": "PHX", "GS": "GSW", "SA": "SAS", "NY": "NYK", "BK": "BKN",
    "UTAH": "UTA",
    "ATL": "ATL", "BOS": "BOS", "BKN": "BKN", "CHI": "CHI", "CLE": "CLE",
    "DAL": "DAL", "DEN": "DEN", "DET": "DET", "GSW": "GSW", "HOU": "HOU",
    "IND": "IND", "LAC": "LAC", "LAL": "LAL", "MEM": "MEM", "MIA": "MIA",
    "MIL": "MIL", "MIN": "MIN", "NOP": "NOP", "NYK": "NYK", "OKC": "OKC",
    "ORL": "ORL", "PHI": "PHI", "PHX": "PHX", "POR": "POR", "SAC": "SAC",
    "SAS": "SAS", "TOR": "TOR", "UTA": "UTA",
}

TEAM_TO_ID = {a: i for i, a in enumerate(sorted([
    "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW",
    "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK",
    "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
]))}

CITY_TZ = {
    "Atlanta": -5, "Boston": -5, "Brooklyn": -5, "Charlotte": -5,
    "Cleveland": -5, "Detroit": -5, "Indiana": -5, "Miami": -5,
    "New York": -5, "Orlando": -5, "Philadelphia": -5,
    "Toronto": -5, "Washington": -5,
    "Chicago": -6, "Dallas": -6, "Houston": -6, "Memphis": -6,
    "Milwaukee": -6, "Minnesota": -6, "New Orleans": -6,
    "Oklahoma City": -6, "San Antonio": -6,
    "Denver": -7, "Utah": -7, "Phoenix": -7,
    "Golden State": -8, "LA": -8, "Los Angeles": -8,
    "Portland": -8, "Sacramento": -8,
}

ABBR_TO_CITY = {
    "ATL": "Atlanta", "BOS": "Boston", "BKN": "Brooklyn", "CHA": "Charlotte",
    "CHI": "Chicago", "CLE": "Cleveland", "DAL": "Dallas", "DEN": "Denver",
    "DET": "Detroit", "GSW": "Golden State", "HOU": "Houston", "IND": "Indiana",
    "LAC": "LA", "LAL": "Los Angeles", "MEM": "Memphis", "MIA": "Miami",
    "MIL": "Milwaukee", "MIN": "Minnesota", "NOP": "New Orleans", "NYK": "New York",
    "OKC": "Oklahoma City", "ORL": "Orlando", "PHI": "Philadelphia", "PHX": "Phoenix",
    "POR": "Portland", "SAC": "Sacramento", "SAS": "San Antonio", "TOR": "Toronto",
    "UTA": "Utah", "WAS": "Washington",
}


def canonical(abbrev):
    if abbrev is None or (isinstance(abbrev, float) and np.isnan(abbrev)):
        return abbrev
    a = str(abbrev).strip().upper()
    return HIST_TO_MODERN.get(a, a)


# ── Stat helpers ──────────────────────────────────────────────────────────

TEAM_STAT_NAMES = [
    "FGM", "FGA", "FG_PCT", "FG3M", "FG3A", "FG3_PCT",
    "FTM", "FTA", "FT_PCT", "OREB", "DREB", "REB",
    "AST", "STL", "BLK", "TO", "PF", "PTS", "PLUS_MINUS",
]
DECAY = 0.9
TEAM_ROLLING_WINDOW = 10

PLAYER_COUNT_COLS = [
    "FGA", "FG3M", "FG3A", "FTM", "FTA",
    "OREB", "DREB", "REB", "AST", "STL", "BLK", "TO", "PF", "PTS",
    "PLUS_MINUS",
]
PLAYER_PCT_COLS = ["FG_PCT", "FG3_PCT", "FT_PCT"]
PLAYER_STAT_COLS = PLAYER_COUNT_COLS + PLAYER_PCT_COLS
PLAYER_WINDOWS = [10, 20]
MIN_MINUTES_FOR_RATE = 5.0

ESPN_COL = {
    "FGA": "field_goals_attempted",
    "FG3M": "three_point_field_goals_made",
    "FG3A": "three_point_field_goals_attempted",
    "FTM": "free_throws_made", "FTA": "free_throws_attempted",
    "OREB": "offensive_rebounds", "DREB": "defensive_rebounds",
    "REB": "rebounds", "AST": "assists", "STL": "steals",
    "BLK": "blocks", "TO": "turnovers", "PF": "fouls",
    "PTS": "points", "PLUS_MINUS": "plus_minus",
}


def _compute_weighted_stats(history, stats, decay):
    n = len(history)
    games_rev = list(reversed(history))
    raw_w = [decay ** (t + 1) for t in range(n)]
    total = sum(raw_w)
    nw = [w / total for w in raw_w]
    result = {}
    for stat in stats:
        ws, wu = 0.0, 0.0
        for i, g in enumerate(games_rev):
            v = g.get(stat)
            if v is not None and not np.isnan(v):
                ws += nw[i] * v
                wu += nw[i]
        result[stat] = ws / wu if wu > 0 else np.nan
    return result


def _compute_rolling_stats(history, stats, window):
    recent = history[-window:]
    result = {}
    for stat in stats:
        vals = [g.get(stat) for g in recent
                if g.get(stat) is not None and not np.isnan(g.get(stat))]
        result[stat] = np.mean(vals) if vals else np.nan
    return result


def _convert_min(val):
    if pd.isna(val) or val == "":
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        pass
    s = str(val).strip()
    if ":" in s:
        parts = s.split(":")
        try:
            return float(parts[0]) + float(parts[1]) / 60.0
        except ValueError:
            return 0.0
    return 0.0


# ── Ticker helpers ────────────────────────────────────────────────────────

def parse_teams_from_ticker(ticker: str):
    m = re.search(r"([A-Z]{3})([A-Z]{3,4})$", ticker)
    if not m:
        raise ValueError(f"Cannot parse teams from ticker: {ticker}")
    return canonical(m.group(1)), canonical(m.group(2))


def parse_game_date_from_ticker(ticker: str):
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{1,2})", ticker)
    if not m:
        return None
    yy, mon, dd = m.group(1), m.group(2), m.group(3)
    year = 2000 + int(yy)
    months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
              "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
    month = months.get(mon, 1)
    return datetime(year, month, int(dd)).strftime("%Y-%m-%d")


# ── Build feature row ─────────────────────────────────────────────────────

def build_feature_row(
    ticker: str,
    home_team: str,
    away_team: str,
    home_roster: list[dict],
    away_roster: list[dict],
    game_date_str: str | None = None,
) -> dict:
    """Build the 159-feature row for the prior XGBoost model.

    Parameters
    ----------
    ticker : Kalshi event ticker (e.g. KXNBAGAME-26MAR10CHIGSW)
    home_team, away_team : canonical 3-letter abbreviations
    home_roster, away_roster : lists of {"id": str, "name": str, ...}
        Active players only (injuries already filtered out).
    game_date_str : "YYYY-MM-DD" or None (parsed from ticker)
    """
    if game_date_str is None:
        game_date_str = parse_game_date_from_ticker(ticker)
    game_date = pd.to_datetime(game_date_str)

    # Current season: Oct+ = that year, else year-1
    season_year = game_date.year if game_date.month >= 10 else game_date.year - 1
    season_label = f"{season_year}-{str(season_year + 1)[-2:]}"

    # ── 1. Team recency-weighted stats + rolling r10 + records + days_rest ──

    training = _get_training()

    season_games = training[training["season"] == season_label].sort_values("game_date")

    team_history: dict[str, list] = {}
    team_records: dict[str, dict] = {}
    team_last_date: dict[str, pd.Timestamp] = {}

    # Track last game date across all seasons for days_rest
    all_sorted = training.sort_values("game_date")
    for _, r in all_sorted.iterrows():
        for t in [r["home_team"], r["away_team"]]:
            team_last_date[t] = r["game_date"]

    # Replay current season to build history
    for _, r in season_games.iterrows():
        ht, at = r["home_team"], r["away_team"]
        for t in [ht, at]:
            if t not in team_records:
                team_records[t] = {"wins": 0, "losses": 0}
            if t not in team_history:
                team_history[t] = []

        home_pts = r.get("home_PTS")
        away_pts = r.get("away_PTS")
        if pd.notna(home_pts) and pd.notna(away_pts):
            if float(home_pts) > float(away_pts):
                team_records[ht]["wins"] += 1
                team_records[at]["losses"] += 1
            elif float(away_pts) > float(home_pts):
                team_records[at]["wins"] += 1
                team_records[ht]["losses"] += 1

        for prefix, team in [("away", at), ("home", ht)]:
            gs = {}
            for stat in TEAM_STAT_NAMES:
                col = f"{prefix}_{stat}"
                val = r.get(col)
                try:
                    gs[stat] = float(val) if pd.notna(val) else np.nan
                except (ValueError, TypeError):
                    gs[stat] = np.nan
            team_history[team].append(gs)

        team_last_date[ht] = r["game_date"]
        team_last_date[at] = r["game_date"]

    row: dict = {}

    # Recency-weighted team averages
    for prefix, team in [("away", away_team), ("home", home_team)]:
        hist = team_history.get(team, [])
        if hist:
            weighted = _compute_weighted_stats(hist, TEAM_STAT_NAMES, DECAY)
            for stat in TEAM_STAT_NAMES:
                row[f"{prefix}_{stat}"] = weighted[stat]
        else:
            for stat in TEAM_STAT_NAMES:
                row[f"{prefix}_{stat}"] = np.nan

    # Records
    for prefix, team in [("home", home_team), ("away", away_team)]:
        rec = team_records.get(team, {"wins": 0, "losses": 0})
        row[f"{prefix}_wins"] = rec["wins"]
        row[f"{prefix}_losses"] = rec["losses"]

    # Days rest
    MAX_REST = 7
    for prefix, team in [("home", home_team), ("away", away_team)]:
        last = team_last_date.get(team)
        if last is not None:
            rest = (game_date - last).days
            row[f"{prefix}_days_rest"] = min(rest, MAX_REST)
        else:
            row[f"{prefix}_days_rest"] = MAX_REST

    # 10-game rolling team stats (r10)
    for prefix, team in [("away", away_team), ("home", home_team)]:
        hist = team_history.get(team, [])
        if len(hist) >= TEAM_ROLLING_WINDOW:
            rolling = _compute_rolling_stats(hist, TEAM_STAT_NAMES, TEAM_ROLLING_WINDOW)
            for stat in TEAM_STAT_NAMES:
                row[f"{prefix}_r10_{stat}"] = rolling[stat]
        else:
            for stat in TEAM_STAT_NAMES:
                row[f"{prefix}_r10_{stat}"] = np.nan

    # ── 2. Player rolling stats (10g, 20g) ───────────────────────────────

    def get_prior_avg_minutes(pid, cur_date, n=5):
        df = _get_player_stats(pid)
        if df is None or df.empty:
            return 0.0
        date_col = "game_date" if "game_date" in df.columns else (
            "GAME_DATE" if "GAME_DATE" in df.columns else None)
        min_col = "minutes" if "minutes" in df.columns else (
            "MIN" if "MIN" in df.columns else None)
        if not date_col or not min_col:
            return 0.0
        d = df.copy()
        d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
        d["_min"] = d[min_col].apply(_convert_min)
        cur_dt = pd.to_datetime(cur_date, errors="coerce")
        prior = d[(d[date_col] < cur_dt) & (d["_min"] > 0)]
        if prior.empty:
            return 0.0
        return float(prior.sort_values(date_col, ascending=False).head(n)["_min"].mean())

    def get_player_rolling(pid, cur_date, max_window=20):
        df = _get_player_stats(pid)
        if df is None or df.empty:
            return None
        date_col = "game_date" if "game_date" in df.columns else (
            "GAME_DATE" if "GAME_DATE" in df.columns else None)
        min_col = "minutes" if "minutes" in df.columns else (
            "MIN" if "MIN" in df.columns else None)
        if not date_col or not min_col:
            return None
        d = df.copy()
        d["_min"] = d[min_col].apply(_convert_min)
        d = d[d["_min"] >= MIN_MINUTES_FOR_RATE]
        d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
        d = d.dropna(subset=[date_col])
        cur_dt = pd.to_datetime(cur_date, errors="coerce")
        if cur_dt is not None and not pd.isna(cur_dt):
            d = d[d[date_col] < cur_dt]
        d = d.sort_values(date_col, ascending=False).head(max_window)
        if d.empty:
            return None

        for short, espn in ESPN_COL.items():
            if espn in d.columns:
                d[short] = pd.to_numeric(d[espn], errors="coerce")

        fgm_col = "field_goals_made" if "field_goals_made" in d.columns else None
        fga_col = "field_goals_attempted" if "field_goals_attempted" in d.columns else None
        if fgm_col and fga_col:
            fgm = pd.to_numeric(d[fgm_col], errors="coerce")
            fga = pd.to_numeric(d[fga_col], errors="coerce")
            d["FG_PCT"] = (fgm / fga).where(fga > 0, 0.0)
        else:
            d["FG_PCT"] = 0.0
        fg3m = pd.to_numeric(d.get("three_point_field_goals_made",
                                    pd.Series(dtype=float)), errors="coerce")
        fg3a = pd.to_numeric(d.get("three_point_field_goals_attempted",
                                    pd.Series(dtype=float)), errors="coerce")
        d["FG3_PCT"] = (fg3m / fg3a).where(fg3a > 0, 0.0)
        ftm = pd.to_numeric(d.get("free_throws_made",
                                   pd.Series(dtype=float)), errors="coerce")
        fta = pd.to_numeric(d.get("free_throws_attempted",
                                   pd.Series(dtype=float)), errors="coerce")
        d["FT_PCT"] = (ftm / fta).where(fta > 0, 0.0)

        mins = d["_min"]
        result = {}
        for w in PLAYER_WINDOWS:
            wd = d.head(w)
            wd_mins = mins.head(w)
            stats = {}
            for stat in PLAYER_COUNT_COLS:
                if stat in wd.columns:
                    vals = pd.to_numeric(wd[stat], errors="coerce").fillna(0)
                    per_min = vals / wd_mins
                    stats[stat] = per_min.mean() if len(per_min) > 0 else 0.0
                else:
                    stats[stat] = 0.0
            for stat in PLAYER_PCT_COLS:
                if stat in wd.columns:
                    vals = pd.to_numeric(wd[stat], errors="coerce").dropna()
                    stats[stat] = vals.mean() if len(vals) > 0 else 0.0
                else:
                    stats[stat] = 0.0
            result[w] = stats
        return result

    for prefix, players in [("away", away_roster), ("home", home_roster)]:
        # Get prior avg minutes for weighting
        prior_mins = []
        for p in players:
            pm = get_prior_avg_minutes(p["id"], game_date_str)
            prior_mins.append(pm)

        weighted_players = [(p, pm) for p, pm in zip(players, prior_mins) if pm > 0]
        total_pm = sum(pm for _, pm in weighted_players)

        agg = {w: {s: 0.0 for s in PLAYER_STAT_COLS} for w in PLAYER_WINDOWS}
        has_data = False

        if total_pm > 0:
            for p, pm in weighted_players:
                weight = pm / total_pm
                history = get_player_rolling(p["id"], game_date_str)
                if history is None:
                    continue
                has_data = True
                for w in PLAYER_WINDOWS:
                    for stat in PLAYER_STAT_COLS:
                        agg[w][stat] += weight * history[w][stat]

        for w in PLAYER_WINDOWS:
            for stat in PLAYER_STAT_COLS:
                row[f"{prefix}_{w}g_player_{stat}"] = agg[w][stat] if has_data else 0.0

    # ── 3. Derived features ───────────────────────────────────────────────

    # Drop PLUS_MINUS from team stats (not in final feature set)
    row.pop("away_PLUS_MINUS", None)
    row.pop("home_PLUS_MINUS", None)
    row.pop("away_r10_PLUS_MINUS", None)
    row.pop("home_r10_PLUS_MINUS", None)

    # eFG from recency-weighted stats
    away_fga = float(row.get("away_FGA", 0) or 0)
    home_fga = float(row.get("home_FGA", 0) or 0)
    row["away_eFG"] = ((row.get("away_FGM", 0) + 0.5 * row.get("away_FG3M", 0)) / away_fga * 100) if away_fga > 0 else np.nan
    row["home_eFG"] = ((row.get("home_FGM", 0) + 0.5 * row.get("home_FG3M", 0)) / home_fga * 100) if home_fga > 0 else np.nan

    # r10 eFG
    r10_away_fga = float(row.get("away_r10_FGA", 0) or 0)
    r10_home_fga = float(row.get("home_r10_FGA", 0) or 0)
    row["away_r10_eFG"] = ((row.get("away_r10_FGM", 0) + 0.5 * row.get("away_r10_FG3M", 0)) / max(r10_away_fga, 1) * 100) if not np.isnan(r10_away_fga) else np.nan
    row["home_r10_eFG"] = ((row.get("home_r10_FGM", 0) + 0.5 * row.get("home_r10_FG3M", 0)) / max(r10_home_fga, 1) * 100) if not np.isnan(r10_home_fga) else np.nan

    # Win percentages
    hw, hl = row.get("home_wins", 0), row.get("home_losses", 0)
    aw, al = row.get("away_wins", 0), row.get("away_losses", 0)
    row["home_win_pct"] = hw / (hw + hl + 0.01)
    row["away_win_pct"] = aw / (aw + al + 0.01)

    # Team IDs
    row["home_team_id"] = TEAM_TO_ID.get(home_team, -1)
    row["away_team_id"] = TEAM_TO_ID.get(away_team, -1)

    # Timezone diff
    home_tz = CITY_TZ.get(ABBR_TO_CITY.get(home_team, ""), 0)
    away_tz = CITY_TZ.get(ABBR_TO_CITY.get(away_team, ""), 0)
    row["tz_diff"] = home_tz - away_tz

    return row


def predict(
    ticker: str,
    home_team: str,
    away_team: str,
    home_roster: list[dict],
    away_roster: list[dict],
    game_date_str: str | None = None,
) -> float:
    """Build feature row and return P(home_win) from the prior XGBoost model."""
    row = build_feature_row(ticker, home_team, away_team,
                            home_roster, away_roster, game_date_str)

    model, feature_cols = _get_model()
    features = pd.DataFrame([row]).reindex(columns=feature_cols)
    proba = model.predict_proba(features)[:, 1][0]
    return float(proba)
