"""Live in-game XGBoost inference.

Loads the trained posterior XGBoost model and computes P(home_win)
from a live game snapshot sent by the iOS client.
"""

import json
import logging
import math
import sys
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb

logger = logging.getLogger(__name__)

# --------------- paths ---------------

_NBA_DIR = Path(__file__).parent.parent.parent.parent / "nba"
_ARTIFACTS_DIR = _NBA_DIR / "posterior_models" / "deployment"
_DATA_DIR = _NBA_DIR / "data"

# --------------- constants ---------------

REG_PERIOD_SEC = 720       # 12 minutes
OT_PERIOD_SEC = 300        # 5 minutes
TOTAL_REG_SEC = 4 * REG_PERIOD_SEC  # 2880

# Time-range midpoints (seconds remaining in period)
_TIME_RANGE_SEC = {
    # Q1-Q3
    "12-9": 630, "9-6": 450, "6-3": 270, "3-0": 90,
    # Q4
    "9-7": 480, "7-5": 360,
    # "5-0" uses timer_seconds directly
}

# --------------- feature list (must match training) ---------------

XGBOOST_FEATURES = [
    "score_diff",
    "t_reg_remaining", "t_reg_norm", "t_reg_log",
    "t_ot_remaining", "t_ot_norm", "t_ot_log",
    "is_ot", "ot_number",
    "pregame_logit",
    "efg_diff", "ts_diff", "off_rtg_diff",
    "tov_diff",
    "foul_diff", "ft_pct_diff", "timeout_diff",
    "bonus_diff", "is_home_poss_signed",
    "rest_diff", "roster_quality_diff", "win_pct_diff",
    "stl_diff", "fg3_pct_diff", "fta_rate_diff",
    "pending_ft_signed", "is_dead_ball",
    "poss_x_elapsed", "ft_x_elapsed",
]

# --------------- model singletons ---------------

_model = None
_iso_cal = None
_hp: dict = {}
_season_baselines: dict = {}  # team -> {efg, ts, off_rtg, ft_pct, ...}
_warmed = False


def warmup():
    """Load models and season baselines. Call once at server startup."""
    global _model, _iso_cal, _hp, _season_baselines, _warmed
    if _warmed:
        return

    try:
        _model = xgb.XGBClassifier()
        _model.load_model(str(_ARTIFACTS_DIR / "xgb_posterior.json"))
        _iso_cal = joblib.load(_ARTIFACTS_DIR / "iso_xgb_post.pkl")
        hp_path = _ARTIFACTS_DIR / "best_hyperparams.json"
        if hp_path.exists():
            with open(hp_path) as f:
                _hp = json.load(f)
        logger.info(f"Loaded XGBoost model from {_ARTIFACTS_DIR}")
        logger.info(f"  Hyperparams: alpha={_hp.get('prior_alpha')}, "
                     f"terminal_sec={_hp.get('terminal_sec')}")
    except Exception as e:
        logger.error(f"Failed to load XGBoost model: {e}")
        return

    # Build season baselines from training_games.csv
    _season_baselines = _compute_season_baselines()
    _warmed = True
    logger.info(f"Live inference ready. {len(_season_baselines)} team baselines loaded.")


def _compute_season_baselines() -> dict:
    """Compute per-team season averages from the most recent season in training data."""
    csv_path = _DATA_DIR / "training_games.csv"
    if not csv_path.exists():
        logger.warning(f"training_games.csv not found at {csv_path}")
        return {}

    df = pd.read_csv(csv_path)
    # Use most recent season
    latest_season = df["season"].max()
    df = df[df["season"] == latest_season]

    baselines = {}
    teams = set(df["home_team"].unique()) | set(df["away_team"].unique())

    for team in teams:
        home_games = df[df["home_team"] == team]
        away_games = df[df["away_team"] == team]

        total_fgm = home_games["home_FGM"].sum() + away_games["away_FGM"].sum()
        total_fga = home_games["home_FGA"].sum() + away_games["away_FGA"].sum()
        total_fg3m = home_games["home_FG3M"].sum() + away_games["away_FG3M"].sum()
        total_ftm = home_games["home_FTM"].sum() + away_games["away_FTM"].sum()
        total_fta = home_games["home_FTA"].sum() + away_games["away_FTA"].sum()
        total_pts = home_games["home_PTS"].sum() + away_games["away_PTS"].sum()
        total_oreb = home_games["home_OREB"].sum() + away_games["away_OREB"].sum()
        total_tov = home_games["home_TO"].sum() + away_games["away_TO"].sum()

        total_poss = total_fga - total_oreb + total_tov + 0.44 * total_fta
        total_poss = max(total_poss, 1)

        efg = (total_fgm + 0.5 * total_fg3m) / max(total_fga, 1)
        ts = total_pts / (2 * (total_fga + 0.44 * total_fta)) if total_fga > 0 else 0.5
        off_rtg = 100 * total_pts / total_poss
        ft_pct = total_ftm / max(total_fta, 1)

        n_games = len(home_games) + len(away_games)
        wins = (home_games["home_PTS"] > home_games["away_PTS"]).sum() + \
               (away_games["away_PTS"] > away_games["home_PTS"]).sum()
        win_pct = wins / max(n_games, 1)

        baselines[team] = {
            "efg": efg, "ts": ts, "off_rtg": off_rtg, "ft_pct": ft_pct,
            "win_pct": win_pct,
        }

    return baselines


# --------------- helpers ---------------

def _sigmoid(x: float) -> float:
    x = max(-30.0, min(30.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float) -> float:
    p = max(1e-7, min(1 - 1e-7, p))
    return math.log(p / (1.0 - p))


def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    return num / den if den > 0 else default


def _estimate_possessions(stats: dict) -> float:
    fga = stats.get("fga", 0)
    oreb = stats.get("oreb", 0)
    tov = stats.get("tov", 0)
    fta = stats.get("fta", 0)
    return max(fga - oreb + tov + 0.44 * fta, 1)


# --------------- snapshot → features ---------------

def _time_features(snapshot: dict) -> dict:
    """Convert quarter/time_range/timer_seconds to time features."""
    quarter = snapshot.get("quarter", 1)
    time_range = snapshot.get("time_range", "12-9")
    timer_seconds = snapshot.get("timer_seconds", 300)

    is_ot = quarter >= 5
    ot_number = max(0, quarter - 4)

    if is_ot:
        sec_remaining = timer_seconds
        t_reg_remaining = 0
        t_ot_remaining = sec_remaining
    else:
        if quarter == 4 and time_range == "5-0":
            sec_remaining = timer_seconds
        elif time_range in _TIME_RANGE_SEC:
            sec_remaining = _TIME_RANGE_SEC[time_range]
        else:
            sec_remaining = 360

        periods_after = 4 - quarter
        t_reg_remaining = periods_after * REG_PERIOD_SEC + sec_remaining
        t_ot_remaining = 0

    t_reg_norm = t_reg_remaining / TOTAL_REG_SEC
    t_reg_log = math.log1p(t_reg_norm * 100) / math.log1p(100)
    t_ot_norm = t_ot_remaining / OT_PERIOD_SEC if OT_PERIOD_SEC > 0 else 0
    t_ot_log = math.log1p(t_ot_norm * 100) / math.log1p(100) if is_ot else 0.0

    return {
        "is_ot": is_ot,
        "ot_number": ot_number,
        "t_reg_remaining": t_reg_remaining,
        "t_reg_norm": t_reg_norm,
        "t_reg_log": t_reg_log,
        "t_ot_remaining": t_ot_remaining,
        "t_ot_norm": t_ot_norm,
        "t_ot_log": t_ot_log,
    }


def snapshot_to_features(
    snapshot: dict,
    prior_home_wp: float,
    rest_diff: float = 0.0,
    roster_quality_diff: float = 0.0,
    win_pct_diff: float = 0.0,
    home_team: str = "",
    away_team: str = "",
) -> dict:
    """Convert a live snapshot dict to the full XGBoost feature dict."""
    home = snapshot.get("home", {})
    away = snapshot.get("away", {})

    tau = _hp.get("tau", 200)

    # --- Score diff ---
    score_diff = home.get("score", 0) - away.get("score", 0)

    # --- Time features ---
    tf = _time_features(snapshot)

    # --- Pregame logit ---
    pregame_logit = _logit(prior_home_wp) if prior_home_wp else 0.0

    # --- Possession ---
    poss = snapshot.get("possession")
    if poss == "home":
        is_home_poss_signed = 1.0
    elif poss == "away":
        is_home_poss_signed = -1.0
    else:
        is_home_poss_signed = 0.0

    # --- In-game raw stats ---
    h_fgm, h_fga = home.get("fgm", 0), home.get("fga", 0)
    h_fg3m = home.get("fg3m", 0)
    h_ftm, h_fta = home.get("ftm", 0), home.get("fta", 0)
    h_oreb, h_dreb = home.get("oreb", 0), home.get("dreb", 0)
    h_tov, h_stl, h_pf = home.get("tov", 0), home.get("stl", 0), home.get("pf", 0)
    h_pts = home.get("score", 0)
    h_to_used = home.get("timeouts_used", 0)

    a_fgm, a_fga = away.get("fgm", 0), away.get("fga", 0)
    a_fg3m = away.get("fg3m", 0)
    a_ftm, a_fta = away.get("ftm", 0), away.get("fta", 0)
    a_oreb, a_dreb = away.get("oreb", 0), away.get("dreb", 0)
    a_tov, a_stl, a_pf = away.get("tov", 0), away.get("stl", 0), away.get("pf", 0)
    a_pts = away.get("score", 0)
    a_to_used = away.get("timeouts_used", 0)

    # --- Possessions estimate ---
    h_poss = _estimate_possessions(home)
    a_poss = _estimate_possessions(away)
    total_poss = h_poss + a_poss

    # --- Season baseline blending ---
    h_base = _season_baselines.get(home_team, {})
    a_base = _season_baselines.get(away_team, {})

    w = math.exp(-total_poss / tau) if tau > 0 else 0.0

    h_efg_ig = _safe_div(h_fgm + 0.5 * h_fg3m, h_fga, 0.5)
    a_efg_ig = _safe_div(a_fgm + 0.5 * a_fg3m, a_fga, 0.5)
    h_ts_ig = _safe_div(h_pts, 2 * (h_fga + 0.44 * h_fta), 0.5)
    a_ts_ig = _safe_div(a_pts, 2 * (a_fga + 0.44 * a_fta), 0.5)
    h_offrtg_ig = 100 * h_pts / h_poss if h_poss > 0 else 100.0
    a_offrtg_ig = 100 * a_pts / a_poss if a_poss > 0 else 100.0

    h_efg = w * h_base.get("efg", 0.5) + (1 - w) * h_efg_ig
    a_efg = w * a_base.get("efg", 0.5) + (1 - w) * a_efg_ig
    h_ts = w * h_base.get("ts", 0.5) + (1 - w) * h_ts_ig
    a_ts = w * a_base.get("ts", 0.5) + (1 - w) * a_ts_ig
    h_offrtg = w * h_base.get("off_rtg", 100) + (1 - w) * h_offrtg_ig
    a_offrtg = w * a_base.get("off_rtg", 100) + (1 - w) * a_offrtg_ig

    efg_diff = h_efg - a_efg
    ts_diff = h_ts - a_ts
    off_rtg_diff = h_offrtg - a_offrtg

    tov_diff = a_tov - h_tov
    foul_diff = a_pf - h_pf
    stl_diff = h_stl - a_stl

    h_ft_ig = _safe_div(h_ftm, h_fta, 0.75)
    a_ft_ig = _safe_div(a_ftm, a_fta, 0.75)
    h_ft = w * h_base.get("ft_pct", 0.75) + (1 - w) * h_ft_ig
    a_ft = w * a_base.get("ft_pct", 0.75) + (1 - w) * a_ft_ig
    ft_pct_diff = h_ft - a_ft

    timeout_diff = a_to_used - h_to_used

    fg3_pct_diff = _safe_div(h_fg3m, max(h_fga, 1)) - _safe_div(a_fg3m, max(a_fga, 1))
    fta_rate_diff = _safe_div(h_fta, max(h_fga, 1)) - _safe_div(a_fta, max(a_fga, 1))

    # --- Bonus diff ---
    quarter = snapshot.get("quarter", 1)
    h_period_fouls = home.get("period_fouls", {})
    a_period_fouls = away.get("period_fouls", {})
    qkey = str(quarter)
    h_pf_period = h_period_fouls.get(qkey, 0)
    a_pf_period = a_period_fouls.get(qkey, 0)
    threshold = 4 if quarter > 4 else 5
    home_in_bonus = 1 if a_pf_period >= threshold else 0
    away_in_bonus = 1 if h_pf_period >= threshold else 0
    bonus_diff = home_in_bonus - away_in_bonus

    if win_pct_diff == 0.0 and h_base and a_base:
        win_pct_diff = h_base.get("win_pct", 0.5) - a_base.get("win_pct", 0.5)

    # --- Pending FTs and dead ball ---
    pending_ft_signed = snapshot.get("pending_ft_signed", 0)
    is_dead_ball = 1 if snapshot.get("is_dead_ball", False) else 0

    # --- Time-weighted interaction features ---
    t_log = tf["t_ot_log"] if tf["is_ot"] else tf["t_reg_log"]
    elapsed = 1.0 - t_log
    poss_x_elapsed = is_home_poss_signed * elapsed
    ft_x_elapsed = pending_ft_signed * elapsed

    return {
        "score_diff": score_diff,
        "t_reg_remaining": tf["t_reg_remaining"],
        "t_reg_norm": tf["t_reg_norm"],
        "t_reg_log": tf["t_reg_log"],
        "t_ot_remaining": tf["t_ot_remaining"],
        "t_ot_norm": tf["t_ot_norm"],
        "t_ot_log": tf["t_ot_log"],
        "is_ot": tf["is_ot"],
        "ot_number": tf["ot_number"],
        "pregame_logit": pregame_logit,
        "efg_diff": efg_diff,
        "ts_diff": ts_diff,
        "off_rtg_diff": off_rtg_diff,
        "tov_diff": tov_diff,
        "foul_diff": foul_diff,
        "ft_pct_diff": ft_pct_diff,
        "timeout_diff": timeout_diff,
        "bonus_diff": bonus_diff,
        "is_home_poss_signed": is_home_poss_signed,
        "rest_diff": rest_diff,
        "roster_quality_diff": roster_quality_diff,
        "win_pct_diff": win_pct_diff,
        "stl_diff": stl_diff,
        "fg3_pct_diff": fg3_pct_diff,
        "fta_rate_diff": fta_rate_diff,
        "pending_ft_signed": pending_ft_signed,
        "is_dead_ball": is_dead_ball,
        "poss_x_elapsed": poss_x_elapsed,
        "ft_x_elapsed": ft_x_elapsed,
    }


# --------------- prediction ---------------

def predict_home_wp(
    snapshot: dict,
    prior_home_wp: float,
    rest_diff: float = 0.0,
    roster_quality_diff: float = 0.0,
    win_pct_diff: float = 0.0,
    home_team: str = "",
    away_team: str = "",
) -> Optional[float]:
    """Compute P(home_win) from a live game snapshot.

    Returns None if model is not loaded.
    """
    if _model is None:
        return None

    feats = snapshot_to_features(
        snapshot, prior_home_wp,
        rest_diff=rest_diff,
        roster_quality_diff=roster_quality_diff,
        win_pct_diff=win_pct_diff,
        home_team=home_team,
        away_team=away_team,
    )

    # Single unified model — no reg/OT split
    X = np.array([[feats[f] for f in XGBOOST_FEATURES]], dtype=np.float32)
    p_raw = float(_model.predict_proba(X)[0][1])
    p_raw = max(1e-7, min(1 - 1e-7, p_raw))

    # --- Postprocessing: prior anchoring + terminal convergence ---
    p_post = _postprocess(p_raw, feats, prior_home_wp)

    # --- Isotonic calibration ---
    if _iso_cal is not None:
        try:
            p_cal = float(_iso_cal.predict([p_post])[0])
            p_cal = max(1e-7, min(1 - 1e-7, p_cal))
            return p_cal
        except Exception:
            pass

    return p_post


def _postprocess(
    p_raw: float,
    feats: dict,
    prior_home_wp: float,
) -> float:
    """Apply prior anchoring + data-calibrated terminal convergence."""
    alpha = _hp.get("prior_alpha", 2.0)
    terminal_sec = _hp.get("terminal_sec", 30.0)

    is_ot = feats["is_ot"]
    t_reg_remaining = feats["t_reg_remaining"]
    t_ot_remaining = feats["t_ot_remaining"]

    # Step 1: Prior anchoring (regulation only)
    if not is_ot and prior_home_wp is not None:
        t_reg_norm = feats["t_reg_norm"]
        prior_weight = t_reg_norm ** alpha
        logit_raw = _logit(p_raw)
        logit_prior = _logit(prior_home_wp)
        logit_blended = (1 - prior_weight) * logit_raw + prior_weight * logit_prior
        p = _sigmoid(logit_blended)
    else:
        p = p_raw

    # Step 2: Terminal convergence
    score_diff = feats["score_diff"]
    pending_ft = feats.get("pending_ft_signed", 0)
    time_remaining = t_ot_remaining if is_ot else t_reg_remaining

    if time_remaining < terminal_sec and score_diff != 0 and pending_ft == 0:
        time_factor = 1.0 - time_remaining / terminal_sec
        score_factor = 1.0 / (1.0 + math.exp(-1.5 * (abs(score_diff) - 2.0)))
        terminal_weight = time_factor * score_factor
        deterministic = 1.0 if score_diff > 0 else 0.0
        p = (1 - terminal_weight) * p + terminal_weight * deterministic

    return max(1e-7, min(1 - 1e-7, p))
