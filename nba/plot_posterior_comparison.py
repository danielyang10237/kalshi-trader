"""
Plot posterior model win-probability traces for a single game.

Generates two model curves:
  1. GAM anchored to Kalshi pregame prior
  2. GAM anchored to XGBoost prior (from games_predictions_deploy.csv)
alongside the Kalshi median implied probability.

Usage:
    python plot_posterior_comparison.py <game_id>
    python plot_posterior_comparison.py 401810391
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import joblib

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ARTIFACTS_DIR = BASE_DIR / "posterior_models" / "deployment"
RESEARCH_DIR = BASE_DIR / "posterior_models" / "research"

# ── Constants ─────────────────────────────────────────────────────────────────
REG_PERIOD_SEC = 720
OT_PERIOD_SEC = 300
TOTAL_REG_SEC = 4 * REG_PERIOD_SEC

# ── Load models & config ─────────────────────────────────────────────────────

def load_all_artifacts():
    gam_reg = joblib.load(ARTIFACTS_DIR / "gam_reg.pkl")
    gam_ot = joblib.load(ARTIFACTS_DIR / "gam_ot.pkl")
    iso_post = joblib.load(ARTIFACTS_DIR / "iso_gam_post.pkl")
    iso_raw = joblib.load(ARTIFACTS_DIR / "iso_gam.pkl")
    config = joblib.load(ARTIFACTS_DIR / "gam_config.pkl")

    import json
    with open(RESEARCH_DIR / "best_hyperparams.json") as f:
        hp = json.load(f)

    return gam_reg, gam_ot, iso_post, iso_raw, config, hp


# ── GAM Feature Lists ────────────────────────────────────────────────────────

GAM_REG_FEATURES = [
    "score_diff", "t_reg_norm", "pregame_logit",
    "efg_diff", "ts_diff", "off_rtg_diff",
    "tov_diff",
    "foul_diff", "ft_pct_diff", "timeout_diff",
    "bonus_diff", "is_home_poss_signed",
    "rest_diff", "roster_quality_diff", "win_pct_diff",
    "stl_diff", "fg3_pct_diff", "fta_rate_diff",
]
GAM_OT_FEATURES = [
    "score_diff", "t_ot_norm", "pregame_logit", "ot_number",
    "efg_diff", "ts_diff", "off_rtg_diff",
    "tov_diff",
    "foul_diff", "ft_pct_diff", "timeout_diff",
    "bonus_diff", "is_home_poss_signed",
    "rest_diff", "roster_quality_diff", "win_pct_diff",
    "stl_diff", "fg3_pct_diff", "fta_rate_diff",
]

# ── Data Loaders ──────────────────────────────────────────────────────────────

def load_game_predictions():
    df = pd.read_csv(DATA_DIR / "games_predictions_deploy.csv", dtype={"game_id": str})
    df["game_id"] = df["game_id"].astype(str).str.strip()
    df["prior_home_wp"] = pd.to_numeric(df["prior_home_wp"], errors="coerce")
    if "kalshi_pregame_wp" in df.columns:
        df["kalshi_pregame_wp"] = pd.to_numeric(df["kalshi_pregame_wp"], errors="coerce")
    return df


def load_season_baselines():
    tg2 = pd.read_csv(DATA_DIR / "training_games2.csv", dtype={"game_id": str}, low_memory=False)
    tg2["game_id"] = tg2["game_id"].astype(str).str.strip()
    baselines = pd.DataFrame({"game_id": tg2["game_id"]})
    for side in ["home", "away"]:
        fgm = pd.to_numeric(tg2[f"{side}_FGM"], errors="coerce").fillna(0)
        fga = pd.to_numeric(tg2[f"{side}_FGA"], errors="coerce").fillna(0).clip(lower=1)
        fg3m = pd.to_numeric(tg2[f"{side}_FG3M"], errors="coerce").fillna(0)
        pts = pd.to_numeric(tg2[f"{side}_PTS"], errors="coerce").fillna(0)
        fta = pd.to_numeric(tg2[f"{side}_FTA"], errors="coerce").fillna(0)
        ft_pct = pd.to_numeric(tg2[f"{side}_FT_PCT"], errors="coerce").fillna(0)
        oreb = pd.to_numeric(tg2[f"{side}_OREB"], errors="coerce").fillna(0)
        to = pd.to_numeric(tg2[f"{side}_TO"], errors="coerce").fillna(0)
        baselines[f"season_{side}_efg"] = (fgm + 0.5 * fg3m) / fga
        baselines[f"season_{side}_ts"] = pts / (2 * (fga + 0.44 * fta)).clip(lower=1)
        poss = (fga + 0.44 * fta - oreb + to).clip(lower=1)
        baselines[f"season_{side}_off_rtg"] = 100 * pts / poss
        baselines[f"season_{side}_ft_pct"] = ft_pct
    return baselines.set_index("game_id")


def load_prior_features():
    tg4 = pd.read_csv(
        DATA_DIR / "training_games4.csv", dtype={"game_id": str},
        usecols=["game_id", "home_days_rest", "away_days_rest",
                 "home_10g_player_PLUS_MINUS", "away_10g_player_PLUS_MINUS",
                 "home_win_pct", "away_win_pct"],
        low_memory=False,
    )
    tg4["game_id"] = tg4["game_id"].astype(str).str.strip()
    tg4["rest_diff"] = tg4["home_days_rest"].fillna(0) - tg4["away_days_rest"].fillna(0)
    tg4["roster_quality_diff"] = (
        tg4["home_10g_player_PLUS_MINUS"].fillna(0)
        - tg4["away_10g_player_PLUS_MINUS"].fillna(0)
    )
    tg4["win_pct_diff"] = tg4["home_win_pct"].fillna(0.5) - tg4["away_win_pct"].fillna(0.5)
    return tg4[["game_id", "rest_diff", "roster_quality_diff", "win_pct_diff"]].set_index("game_id")


# ── Build game features from PBP ─────────────────────────────────────────────

def build_game_from_pbp(game_id, prior_home_wp):
    """Build posterior-model features from raw PBP. prior_home_wp is injected externally."""
    # Find PBP file across season directories
    pbp_path = None
    for sdir in sorted((DATA_DIR / "games_live").iterdir()):
        if not sdir.is_dir():
            continue
        matches = list(sdir.glob(f"{game_id}_*.csv"))
        if matches:
            pbp_path = matches[0]
            break
    if pbp_path is None:
        raise FileNotFoundError(f"No PBP file found for game {game_id}")

    pbp = pd.read_csv(pbp_path).sort_values("game_play_number").reset_index(drop=True)

    # Fix "End Period" rows
    end_period_mask = pbp["type_text"].str.contains("End Period", na=False)
    pbp.loc[end_period_mask, "end_quarter_seconds_remaining"] = 0.0

    home_tid = pbp["home_team_id"].iloc[0]
    away_tid = pbp["away_team_id"].iloc[0]
    is_home = pbp["team_id"] == home_tid
    is_away = pbp["team_id"] == away_tid

    # Play-type flags
    fg_att = pbp["shooting_play"].fillna(False) & (pbp["points_attempted"] >= 2)
    fg_made = pbp["scoring_play"].fillna(False) & (pbp["score_value"] >= 2)
    fg3_made = pbp["scoring_play"].fillna(False) & (pbp["score_value"] == 3)
    ft_att = pbp["shooting_play"].fillna(False) & (pbp["points_attempted"] == 1)
    ft_made = pbp["scoring_play"].fillna(False) & (pbp["score_value"] == 1)
    oreb = pbp["type_text"].str.contains("Offensive Rebound", na=False)
    dreb = pbp["type_text"].str.contains("Defensive Rebound", na=False)
    tov = pbp["short_description"].str.contains("Turnover", na=False)
    ast = pbp["text"].str.contains("assists", case=False, na=False) & pbp["scoring_play"].fillna(False)
    stl = pbp["type_text"].str.contains("Steal", na=False)
    blk = pbp["type_text"].str.contains("Block", na=False)
    foul = pbp["short_description"].str.contains("Foul", na=False)
    timeout = pbp["short_description"].str.contains("Timeout", na=False)

    # Cumulative stats
    cols = {}
    for prefix, mask in [("home", is_home), ("away", is_away)]:
        cols[f"{prefix}_fga_to_date"] = (fg_att & mask).cumsum()
        cols[f"{prefix}_fgm_to_date"] = (fg_made & mask).cumsum()
        cols[f"{prefix}_fg3m_to_date"] = (fg3_made & mask).cumsum()
        cols[f"{prefix}_fta_to_date"] = (ft_att & mask).cumsum()
        cols[f"{prefix}_ftm_to_date"] = (ft_made & mask).cumsum()
        cols[f"{prefix}_oreb_to_date"] = (oreb & mask).cumsum()
        cols[f"{prefix}_dreb_to_date"] = (dreb & mask).cumsum()
        cols[f"{prefix}_tov_to_date"] = (tov & mask).cumsum()
        cols[f"{prefix}_ast_to_date"] = (ast & mask).cumsum()
        cols[f"{prefix}_stl_to_date"] = (stl & mask).cumsum()
        cols[f"{prefix}_blk_to_date"] = (blk & mask).cumsum()
        cols[f"{prefix}_pf_to_date"] = (foul & mask).cumsum()
        cols[f"{prefix}_timeouts_called"] = (timeout & mask).cumsum()

    # Derived percentages
    for prefix in ["home", "away"]:
        fga = cols[f"{prefix}_fga_to_date"].clip(lower=1)
        fta = cols[f"{prefix}_fta_to_date"].clip(lower=1)
        fgm = cols[f"{prefix}_fgm_to_date"]
        ftm = cols[f"{prefix}_ftm_to_date"]
        fg3m_c = cols[f"{prefix}_fg3m_to_date"]
        pts = pbp[f"{prefix}_score"]
        cols[f"{prefix}_fg_pct"] = fgm / fga
        cols[f"{prefix}_ft_pct"] = ftm / fta
        cols[f"{prefix}_efg_pct"] = (fgm + 0.5 * fg3m_c) / fga
        cols[f"{prefix}_ts_pct"] = pts / (2 * (fga + 0.44 * cols[f"{prefix}_fta_to_date"])).clip(lower=1)
        poss = (
            cols[f"{prefix}_fga_to_date"]
            + 0.44 * cols[f"{prefix}_fta_to_date"]
            - cols[f"{prefix}_oreb_to_date"]
            + cols[f"{prefix}_tov_to_date"]
        ).clip(lower=1)
        cols[f"{prefix}_poss_to_date"] = poss
        cols[f"{prefix}_off_rtg"] = 100 * pts / poss

    # Possession heuristic
    poss_arr = np.full(len(pbp), np.nan)
    team_ids = pbp["team_id"].values
    short_d = pbp["short_description"].fillna("").values
    type_t = pbp["type_text"].fillna("").values
    shooting = pbp["shooting_play"].fillna(False).values
    for i in range(len(pbp)):
        tid = team_ids[i]
        if pd.isna(tid):
            continue
        if shooting[i] or "Turnover" in short_d[i]:
            poss_arr[i] = 1.0 if tid == home_tid else 0.0
        elif "Rebound" in type_t[i]:
            poss_arr[i] = 1.0 if tid == home_tid else 0.0

    # Net points last 120 game-seconds
    game_sec = np.nan_to_num(pbp["end_game_seconds_remaining"].values.astype(float), nan=0.0)
    home_sc = pbp["home_score"].values.astype(float)
    away_sc = pbp["away_score"].values.astype(float)
    net_pts = np.zeros(len(pbp))
    for i in range(len(pbp)):
        j = i
        while j > 0 and game_sec[j - 1] - game_sec[i] <= 120:
            j -= 1
        net_pts[i] = (home_sc[i] - home_sc[j]) - (away_sc[i] - away_sc[j])

    result = pd.DataFrame({
        "game_id": game_id,
        "season": 2026,
        "play_index": range(len(pbp)),
        "prior_home_wp": prior_home_wp,
        "home_score": home_sc,
        "away_score": away_sc,
        "score_diff": home_sc - away_sc,
        "period_number": pbp["period_number"].values,
        "sec_remaining_game": np.nan_to_num(game_sec, nan=0.0),
        "sec_remaining_period": np.nan_to_num(
            pbp["end_quarter_seconds_remaining"].values.astype(float), nan=0.0
        ),
        "is_overtime": (pbp["period_number"] > 4).astype(int).values,
        "is_home_possession": poss_arr,
        "net_pts_last_120": net_pts,
        "home_win_final": int(home_sc[-1] > away_sc[-1]),
        "sample_weight": 1.0,
    })
    for k, v in cols.items():
        result[k] = v.values if hasattr(v, "values") else v
    result["wallclock"] = pbp["wallclock"].values

    return result


# ── Feature Engineering (mirrors deployment notebook) ─────────────────────────

def compute_prior_logit(df):
    df = df.copy()
    p0 = df["prior_home_wp"].clip(0.001, 0.999)
    df["pregame_logit"] = np.log(p0 / (1.0 - p0))
    return df


def compute_time_features(df):
    df = df.copy()
    period = df["period_number"].astype(int)
    sec_period = pd.to_numeric(df["sec_remaining_period"], errors="coerce").fillna(0).astype(float)
    df["is_ot"] = (period > 4).astype(int)
    df["ot_number"] = np.clip(period - 4, 0, None)
    df["t_reg_remaining"] = np.where(
        period <= 4, (4 - period) * REG_PERIOD_SEC + sec_period, 0
    ).astype(float)
    df["t_ot_remaining"] = np.where(period > 4, sec_period, 0).astype(float)
    df["t_reg_norm"] = df["t_reg_remaining"] / TOTAL_REG_SEC
    df["t_ot_norm"] = np.where(df["is_ot"] == 1, df["t_ot_remaining"] / OT_PERIOD_SEC, 0.0)
    df["elapsed_game_seconds"] = np.where(
        period <= 4,
        (period - 1) * REG_PERIOD_SEC + (REG_PERIOD_SEC - sec_period),
        TOTAL_REG_SEC + (df["ot_number"] - 1) * OT_PERIOD_SEC + (OT_PERIOD_SEC - sec_period),
    ).astype(float)
    return df


def compute_score_diff(df):
    df = df.copy()
    if "score_diff" not in df.columns:
        df["score_diff"] = (
            pd.to_numeric(df["home_score"], errors="coerce").fillna(0).astype(int)
            - pd.to_numeric(df["away_score"], errors="coerce").fillna(0).astype(int)
        )
    return df


def compute_quality_diffs(df, tau):
    df = df.copy()
    if tau and tau > 0 and "season_home_efg" in df.columns:
        total_poss = (
            df.get("home_poss_to_date", pd.Series(0, index=df.index)).fillna(0)
            + df.get("away_poss_to_date", pd.Series(0, index=df.index)).fillna(0)
        )
        w = np.exp(-total_poss / tau)
    else:
        w = 0.0

    for feat, season_col, ingame_col in [
        ("efg", "season_{side}_efg", "{side}_efg_pct"),
        ("ts", "season_{side}_ts", "{side}_ts_pct"),
        ("off_rtg", "season_{side}_off_rtg", "{side}_off_rtg"),
    ]:
        for side in ["home", "away"]:
            s_col = season_col.format(side=side)
            i_col = ingame_col.format(side=side)
            ingame = df[i_col].fillna(0)
            if isinstance(w, float) and w == 0.0:
                df[f"_blended_{side}_{feat}"] = ingame
            else:
                season = df.get(s_col, pd.Series(np.nan, index=df.index))
                has_season = season.notna()
                blended = np.where(has_season, w * season.fillna(0) + (1 - w) * ingame, ingame)
                df[f"_blended_{side}_{feat}"] = blended

    df["efg_diff"] = df["_blended_home_efg"] - df["_blended_away_efg"]
    df["ts_diff"] = df["_blended_home_ts"] - df["_blended_away_ts"]
    df["off_rtg_diff"] = df["_blended_home_off_rtg"] - df["_blended_away_off_rtg"]
    df = df.drop(columns=[c for c in df.columns if c.startswith("_blended_")])

    df["ast_diff"] = df["home_ast_to_date"].fillna(0) - df["away_ast_to_date"].fillna(0)
    df["tov_diff"] = df["away_tov_to_date"].fillna(0) - df["home_tov_to_date"].fillna(0)
    df["reb_diff"] = (
        (df["home_oreb_to_date"].fillna(0) + df["home_dreb_to_date"].fillna(0))
        - (df["away_oreb_to_date"].fillna(0) + df["away_dreb_to_date"].fillna(0))
    )
    if "net_pts_last_120" not in df.columns:
        df["net_pts_last_120"] = 0.0
    return df


def compute_late_diffs(df, tau):
    df = df.copy()
    df["foul_diff"] = df["away_pf_to_date"].fillna(0) - df["home_pf_to_date"].fillna(0)

    if tau and tau > 0 and "season_home_ft_pct" in df.columns:
        total_poss = (
            df.get("home_poss_to_date", pd.Series(0, index=df.index)).fillna(0)
            + df.get("away_poss_to_date", pd.Series(0, index=df.index)).fillna(0)
        )
        w = np.exp(-total_poss / tau)
        home_ft = df["home_ft_pct"].fillna(0)
        away_ft = df["away_ft_pct"].fillna(0)
        s_home = df.get("season_home_ft_pct", pd.Series(np.nan, index=df.index))
        s_away = df.get("season_away_ft_pct", pd.Series(np.nan, index=df.index))
        has_home = s_home.notna()
        has_away = s_away.notna()
        b_home = np.where(has_home, w * s_home.fillna(0) + (1 - w) * home_ft, home_ft)
        b_away = np.where(has_away, w * s_away.fillna(0) + (1 - w) * away_ft, away_ft)
        df["ft_pct_diff"] = b_home - b_away
    else:
        df["ft_pct_diff"] = df["home_ft_pct"].fillna(0) - df["away_ft_pct"].fillna(0)

    df["timeout_diff"] = df["away_timeouts_called"].fillna(0) - df["home_timeouts_called"].fillna(0)
    return df


def compute_bonus_features(df):
    df = df.copy()
    home_pf = df["home_pf_to_date"].fillna(0)
    away_pf = df["away_pf_to_date"].fillna(0)
    period_start_home = df.groupby(["game_id", "period_number"])["home_pf_to_date"].transform("min").fillna(0)
    period_start_away = df.groupby(["game_id", "period_number"])["away_pf_to_date"].transform("min").fillna(0)
    home_pf_period = home_pf - period_start_home
    away_pf_period = away_pf - period_start_away
    threshold = np.where(df["period_number"] > 4, 4, 5)
    home_in_bonus = (away_pf_period >= threshold).astype(int)
    away_in_bonus = (home_pf_period >= threshold).astype(int)
    df["bonus_diff"] = home_in_bonus - away_in_bonus
    return df


def compute_possession(df):
    df = df.copy()
    poss = df["is_home_possession"].values.copy().astype(float)
    df["is_home_poss_signed"] = np.where(
        np.isnan(poss), 0.0, np.where(poss == 0, -1.0, 1.0)
    )
    return df


def compute_ingame_extras(df):
    df = df.copy()
    df["stl_diff"] = (
        df.get("home_stl_to_date", pd.Series(0, index=df.index)).fillna(0)
        - df.get("away_stl_to_date", pd.Series(0, index=df.index)).fillna(0)
    )
    df["blk_diff"] = (
        df.get("home_blk_to_date", pd.Series(0, index=df.index)).fillna(0)
        - df.get("away_blk_to_date", pd.Series(0, index=df.index)).fillna(0)
    )
    df["oreb_diff"] = (
        df.get("home_oreb_to_date", pd.Series(0, index=df.index)).fillna(0)
        - df.get("away_oreb_to_date", pd.Series(0, index=df.index)).fillna(0)
    )
    home_fga = df.get("home_fga_to_date", pd.Series(0, index=df.index)).fillna(0).clip(lower=1)
    away_fga = df.get("away_fga_to_date", pd.Series(0, index=df.index)).fillna(0).clip(lower=1)
    home_fg3m = df.get("home_fg3m_to_date", pd.Series(0, index=df.index)).fillna(0)
    away_fg3m = df.get("away_fg3m_to_date", pd.Series(0, index=df.index)).fillna(0)
    df["fg3_pct_diff"] = (home_fg3m / home_fga) - (away_fg3m / away_fga)
    home_fta = df.get("home_fta_to_date", pd.Series(0, index=df.index)).fillna(0)
    away_fta = df.get("away_fta_to_date", pd.Series(0, index=df.index)).fillna(0)
    df["fta_rate_diff"] = (home_fta / home_fga) - (away_fta / away_fga)
    return df


def attach_baselines_and_priors(df, game_id, season_baselines, prior_features):
    """Attach season baselines and prior features to game DataFrame."""
    if game_id in season_baselines.index:
        for col in season_baselines.columns:
            df[col] = season_baselines.loc[game_id, col]
    if game_id in prior_features.index:
        for col in prior_features.columns:
            df[col] = prior_features.loc[game_id, col]
    else:
        df["rest_diff"] = 0.0
        df["roster_quality_diff"] = 0.0
        df["win_pct_diff"] = 0.0
    return df


def apply_feature_pipeline(df, tau):
    """Apply the full feature pipeline in order."""
    df = compute_prior_logit(df)
    df = compute_time_features(df)
    df = compute_score_diff(df)
    df = compute_quality_diffs(df, tau=tau)
    df = compute_late_diffs(df, tau=tau)
    df = compute_ingame_extras(df)
    df = compute_bonus_features(df)
    df = compute_possession(df)
    return df


# ── Model Inference ───────────────────────────────────────────────────────────

def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


def _logit(p):
    p = np.clip(p, 1e-7, 1 - 1e-7)
    return np.log(p / (1.0 - p))


def gam_predict_proba(states, reg_model, ot_model):
    p = np.full(len(states), 0.5)
    reg_mask = states["is_ot"] == 0
    ot_mask = states["is_ot"] == 1
    if reg_model is not None and reg_mask.any():
        X = states.loc[reg_mask, GAM_REG_FEATURES].values.astype(float)
        p[reg_mask.values] = reg_model.predict_proba(X)
    if ot_model is not None and ot_mask.any():
        X = states.loc[ot_mask, GAM_OT_FEATURES].values.astype(float)
        p[ot_mask.values] = ot_model.predict_proba(X)
    return np.clip(p, 1e-7, 1 - 1e-7)


def postprocess_predictions(p_raw, df, prior_alpha, terminal_sec):
    p_raw = np.asarray(p_raw, dtype=float)
    pregame_logit = df["pregame_logit"].values.astype(float)
    t_reg_norm = df["t_reg_norm"].values.astype(float)
    is_ot = df["is_ot"].values.astype(int)
    t_reg_remaining = df["t_reg_remaining"].values.astype(float)
    t_ot_remaining = df["t_ot_remaining"].values.astype(float)
    score_diff = df["score_diff"].values.astype(float)

    # Step 1: Prior anchoring
    prior_weight = np.where(is_ot == 0, np.power(t_reg_norm, prior_alpha), 0.0)
    raw_logit = _logit(p_raw)
    blended_logit = prior_weight * pregame_logit + (1.0 - prior_weight) * raw_logit
    p = _sigmoid(blended_logit)

    # Step 2: Terminal convergence
    time_remaining = np.where(is_ot == 0, t_reg_remaining, t_ot_remaining)
    terminal_mask = (time_remaining < terminal_sec) & (score_diff != 0)
    if terminal_mask.any():
        terminal_weight = 1.0 - time_remaining[terminal_mask] / terminal_sec
        deterministic = np.where(score_diff[terminal_mask] > 0, 1.0, 0.0)
        p[terminal_mask] = (1.0 - terminal_weight) * p[terminal_mask] + terminal_weight * deterministic

    return np.clip(p, 1e-7, 1 - 1e-7)


# ── Kalshi Data ───────────────────────────────────────────────────────────────

def load_kalshi_implied(game_id):
    matches = list((DATA_DIR / "kalshi_live").glob(f"{game_id}_*.csv"))
    if not matches:
        return None
    kalshi = pd.read_csv(matches[0])
    kalshi["timestamp"] = pd.to_datetime(kalshi["timestamp"], format="ISO8601")
    # Auto-detect scale: cents (0-100) vs probability (0-1)
    max_val = max(kalshi["home_high_cents"].max(), kalshi["away_high_cents"].max())
    scale = 100.0 if max_val > 1.0 else 1.0
    home_mid = (kalshi["home_high_cents"] + kalshi["home_low_cents"]) / 2 / scale
    away_mid = (kalshi["away_high_cents"] + kalshi["away_low_cents"]) / 2 / scale
    kalshi["home_implied"] = np.where(
        kalshi["home_volume"] > 0, home_mid,
        np.where(kalshi["away_volume"] > 0, 1 - away_mid, np.nan),
    )
    return kalshi.dropna(subset=["home_implied"])


# ── Main ──────────────────────────────────────────────────────────────────────

def main(game_id: str):
    print(f"Loading models and data...")
    gam_reg, gam_ot, iso_post, iso_raw, config, hp = load_all_artifacts()
    tau = config.get("tau", 200)
    prior_alpha = hp["prior_alpha"]
    terminal_sec = hp["terminal_sec"]

    gp = load_game_predictions()
    season_baselines = load_season_baselines()
    prior_features = load_prior_features()

    # Get game info
    ginfo = gp[gp["game_id"] == game_id]
    if ginfo.empty:
        print(f"ERROR: game_id {game_id} not found in games_predictions_deploy.csv")
        sys.exit(1)
    ginfo = ginfo.iloc[0]
    home_team = ginfo["home_team"]
    away_team = ginfo["away_team"]
    xgb_prior = ginfo["prior_home_wp"]
    kalshi_prior = ginfo.get("kalshi_pregame_wp", np.nan)

    print(f"Game:    {game_id}")
    print(f"Matchup: {home_team} (home) vs {away_team} (away)")
    print(f"XGB prior:    {xgb_prior:.4f}")
    print(f"Kalshi prior: {kalshi_prior:.4f}" if pd.notna(kalshi_prior) else "Kalshi prior: N/A")

    # --- Build features with Kalshi prior ---
    if pd.notna(kalshi_prior):
        game_kalshi = build_game_from_pbp(game_id, prior_home_wp=kalshi_prior)
        game_kalshi = attach_baselines_and_priors(game_kalshi, game_id, season_baselines, prior_features)
        game_kalshi = apply_feature_pipeline(game_kalshi, tau)
        p_raw_kalshi = gam_predict_proba(game_kalshi, gam_reg, gam_ot)
        p_post_kalshi = postprocess_predictions(p_raw_kalshi, game_kalshi, prior_alpha, terminal_sec)
        p_cal_kalshi = iso_post.predict(p_post_kalshi)
        has_kalshi_model = True
    else:
        has_kalshi_model = False
        print("  (Kalshi prior not available — skipping Kalshi-anchored model)")

    # --- Build features with XGBoost prior ---
    game_xgb = build_game_from_pbp(game_id, prior_home_wp=xgb_prior)
    game_xgb = attach_baselines_and_priors(game_xgb, game_id, season_baselines, prior_features)
    game_xgb = apply_feature_pipeline(game_xgb, tau)
    p_raw_xgb = gam_predict_proba(game_xgb, gam_reg, gam_ot)
    p_post_xgb = postprocess_predictions(p_raw_xgb, game_xgb, prior_alpha, terminal_sec)
    p_cal_xgb = iso_post.predict(p_post_xgb)

    # --- Elapsed time in minutes ---
    elapsed_min = game_xgb["elapsed_game_seconds"].values / 60.0

    # --- Load Kalshi trades ---
    kalshi = load_kalshi_implied(game_id)
    kalshi_binned = None
    if kalshi is not None and len(kalshi) > 0:
        # Map Kalshi timestamps to game time via wallclock interpolation
        game_xgb["wallclock_dt"] = pd.to_datetime(game_xgb["wallclock"], utc=True, format="ISO8601")
        wc_sec = game_xgb["wallclock_dt"].astype(np.int64).values / 1e9
        el_min = elapsed_min

        kalshi_sec = kalshi["timestamp"].astype(np.int64).values / 1e9

        # Clamp Kalshi to PBP wallclock range
        wc_min, wc_max = wc_sec.min(), wc_sec.max()
        kalshi_mask = (kalshi_sec >= wc_min) & (kalshi_sec <= wc_max)
        kalshi = kalshi[kalshi_mask].copy()
        kalshi_sec = kalshi_sec[kalshi_mask]

        if len(kalshi) > 0:
            kalshi["elapsed_min"] = np.interp(kalshi_sec, wc_sec, el_min)
            max_min = elapsed_min.max()
            bin_edges = np.arange(0, max_min + 0.5, 0.5)
            kalshi_sorted = kalshi.sort_values("elapsed_min")
            kalshi_sorted["bin"] = pd.cut(kalshi_sorted["elapsed_min"], bins=bin_edges, labels=bin_edges[:-1])
            kalshi_binned = kalshi_sorted.groupby("bin", observed=True)["home_implied"].median().reset_index()
            kalshi_binned.columns = ["elapsed_min", "home_implied"]
            kalshi_binned["elapsed_min"] = kalshi_binned["elapsed_min"].astype(float)
            kalshi_binned = kalshi_binned.dropna()
            print(f"Kalshi trades: {len(kalshi)} (binned to {len(kalshi_binned)} points)")
        else:
            print("No Kalshi trades within PBP time range")
    else:
        print("No Kalshi trade data found for this game")

    # --- Plot ---
    outcome = "Home Win" if game_xgb["home_win_final"].iloc[0] == 1 else "Away Win"
    date = ginfo["game_date"]
    total_periods = game_xgb["period_number"].max()
    max_min = elapsed_min.max()

    fig, ax1 = plt.subplots(figsize=(14, 5))

    # Win probability lines
    if has_kalshi_model:
        ax1.plot(elapsed_min, p_cal_kalshi, color="#f97316", linewidth=1.5, alpha=0.9,
                 label=f"GAM (Kalshi prior = {kalshi_prior:.3f})")
    ax1.plot(elapsed_min, p_cal_xgb, color="#3b82f6", linewidth=1.5, alpha=0.9,
             label=f"GAM (XGB prior = {xgb_prior:.3f})")
    if kalshi_binned is not None and len(kalshi_binned) > 0:
        ax1.plot(kalshi_binned["elapsed_min"], kalshi_binned["home_implied"],
                 color="#10b981", linewidth=1.8, alpha=0.85, label="Kalshi Implied (median)")

    ax1.axhline(0.5, color="grey", linewidth=0.5, linestyle="--", alpha=0.5)
    ax1.set_ylabel("P(Home Win)", fontsize=12)
    ax1.set_ylim(-0.02, 1.02)
    ax1.set_xlabel("Game Time (minutes)", fontsize=12)

    # Score differential (right y-axis)
    score_diff = game_xgb["score_diff"].values
    ax2 = ax1.twinx()
    ax2.fill_between(elapsed_min, score_diff, 0,
                     where=(score_diff >= 0), color="#2563eb", alpha=0.08)
    ax2.fill_between(elapsed_min, score_diff, 0,
                     where=(score_diff < 0), color="#ef4444", alpha=0.08)
    ax2.plot(elapsed_min, score_diff, color="#6b7280", linewidth=0.8, alpha=0.5, label="Score Diff")
    ax2.set_ylabel("Score Diff (Home - Away)", fontsize=12, color="#6b7280")
    ax2.tick_params(axis="y", labelcolor="#6b7280")

    # Quarter / OT markers
    for q in range(1, 5):
        qx = q * REG_PERIOD_SEC / 60.0
        if qx <= max_min:
            ax1.axvline(qx, color="grey", linewidth=0.5, linestyle=":", alpha=0.4)
            ax1.text(qx, 1.01, f"Q{q}", ha="right", va="bottom", fontsize=8, color="grey")
    for ot in range(1, max(int(total_periods) - 3, 0)):
        otx = (TOTAL_REG_SEC + ot * OT_PERIOD_SEC) / 60.0
        if otx <= max_min:
            ax1.axvline(otx, color="grey", linewidth=0.5, linestyle=":", alpha=0.4)
            ax1.text(otx, 1.01, f"OT{ot}", ha="right", va="bottom", fontsize=8, color="grey")

    ax1.set_title(
        f"{away_team} @ {home_team}  ({date})  ---  {outcome}\n"
        f"Posterior Model Comparison: Kalshi Prior vs XGB Prior vs Kalshi Market",
        fontsize=13, fontweight="bold",
    )
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="lower right", fontsize=9)
    fig.tight_layout()

    # Save
    out_dir = DATA_DIR / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    fig_path = out_dir / f"posterior_comparison_{game_id}.png"
    fig.savefig(fig_path, dpi=150, bbox_inches="tight")
    print(f"\nSaved plot to {fig_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python plot_posterior_comparison.py <game_id>")
        print("Example: python plot_posterior_comparison.py 401810391")
        sys.exit(1)
    main(sys.argv[1])
