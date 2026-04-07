"""
Analyze per-event model deltas vs Kalshi market movements.

For each state-changing PBP event:
  - Computes GAM model probability before/after → model_delta
  - Measures Kalshi mid before and at 5s/10s/15s/30s after → market_delta
  - Aggregates across many games to assess latency edge

Usage:
    python analyze_delta_edge.py                   # all available games
    python analyze_delta_edge.py --n_games 20      # most recent 20
    python analyze_delta_edge.py --game_id 401810391
"""

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats

from plot_posterior_comparison import (
    load_all_artifacts,
    load_game_predictions,
    load_season_baselines,
    load_prior_features,
    build_game_from_pbp,
    attach_baselines_and_priors,
    apply_feature_pipeline,
    gam_predict_proba,
    postprocess_predictions,
    DATA_DIR,
)

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# ── Config ────────────────────────────────────────────────────────────────────
WINDOWS_SEC = [5, 10, 15, 30]
PRE_EVENT_SEC = 2  # look up Kalshi mid this many seconds before event wallclock

# PBP type_texts that are NOT state-changing
ADMIN_TYPES = {
    "Substitution", "End Period", "End Game", "Jumpball",
    "Challenge", "Coach's Challenge", "Delay of Game",
    "Official's Timeout", "TV Timeout", "Instant Replay",
}

SAMPLES_DIR = DATA_DIR / "samples"


# ── Kalshi Forward-Filled Mid Series ──────────────────────────────────────────

def build_kalshi_mid_series(game_id: str) -> pd.DataFrame | None:
    """Load Kalshi trades, compute unified home_implied mid, forward-fill."""
    matches = list((DATA_DIR / "kalshi_live").glob(f"{game_id}_*.csv"))
    if not matches:
        return None
    df = pd.read_csv(matches[0])
    if len(df) == 0:
        return None

    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Auto-detect scale
    max_val = max(
        df["home_high_cents"].max() if df["home_high_cents"].notna().any() else 0,
        df["away_high_cents"].max() if df["away_high_cents"].notna().any() else 0,
    )
    scale = 100.0 if max_val > 1.0 else 1.0

    # Compute home implied from each side
    home_mid_h = np.where(
        df["home_volume"] > 0,
        (df["home_high_cents"] + df["home_low_cents"]) / 2 / scale,
        np.nan,
    )
    home_mid_a = np.where(
        df["away_volume"] > 0,
        1.0 - (df["away_high_cents"] + df["away_low_cents"]) / 2 / scale,
        np.nan,
    )

    # Prefer home when both exist, otherwise use whichever is available
    both_mask = ~np.isnan(home_mid_h) & ~np.isnan(home_mid_a)
    h_vol = df["home_volume"].fillna(0).values.astype(float)
    a_vol = df["away_volume"].fillna(0).values.astype(float)
    total_vol = h_vol + a_vol
    vol_avg = np.where(
        total_vol > 0,
        (h_vol * home_mid_h + a_vol * home_mid_a) / total_vol,
        np.nan,
    )

    home_mid = np.where(
        both_mask, vol_avg,
        np.where(~np.isnan(home_mid_h), home_mid_h, home_mid_a),
    )

    result = pd.DataFrame({
        "timestamp": df["timestamp"],
        "home_mid": home_mid,
    })
    result["home_mid"] = result["home_mid"].ffill().bfill()
    result = result.dropna(subset=["home_mid"])
    if result.empty:
        return None
    return result


def lookup_mid(series: pd.DataFrame, ts: pd.Timestamp) -> float:
    """Return forward-filled home_mid at or just before ts."""
    if series is None or series.empty:
        return np.nan
    idx = series["timestamp"].searchsorted(ts, side="right") - 1
    if idx < 0:
        return np.nan
    return series["home_mid"].iloc[idx]


# ── Single Game Analysis ──────────────────────────────────────────────────────

def analyze_single_game(
    game_id, gam_reg, gam_ot, iso_post, config, hp,
    gp, season_baselines, prior_features, windows_sec,
):
    """Process one game. Returns event-level DataFrame or None on failure."""
    ginfo = gp[gp["game_id"] == game_id]
    if ginfo.empty:
        return None
    ginfo = ginfo.iloc[0]
    kalshi_prior = ginfo.get("kalshi_pregame_wp", np.nan)
    if pd.isna(kalshi_prior):
        return None

    tau = config.get("tau", 200)
    prior_alpha = hp["prior_alpha"]
    terminal_sec = hp["terminal_sec"]

    # Build model predictions (Kalshi-anchored)
    try:
        game_df = build_game_from_pbp(game_id, prior_home_wp=kalshi_prior)
    except Exception:
        return None
    game_df = attach_baselines_and_priors(game_df, game_id, season_baselines, prior_features)
    game_df = apply_feature_pipeline(game_df, tau)
    p_raw = gam_predict_proba(game_df, gam_reg, gam_ot)
    p_post = postprocess_predictions(p_raw, game_df, prior_alpha, terminal_sec)
    p_cal = iso_post.predict(p_post)
    game_df["p_cal"] = p_cal

    # Load raw PBP for type_text filtering
    pbp_path = None
    for sdir in sorted((DATA_DIR / "games_live").iterdir()):
        if not sdir.is_dir():
            continue
        matches = list(sdir.glob(f"{game_id}_*.csv"))
        if matches:
            pbp_path = matches[0]
            break
    if pbp_path is None:
        return None
    pbp_raw = pd.read_csv(pbp_path).sort_values("game_play_number").reset_index(drop=True)
    type_texts = pbp_raw["type_text"].fillna("").values

    # Parse wallclocks
    game_df["wallclock_dt"] = pd.to_datetime(game_df["wallclock"], utc=True, format="ISO8601")

    # Load Kalshi mid series
    kalshi_series = build_kalshi_mid_series(game_id)
    if kalshi_series is None or len(kalshi_series) < 10:
        return None

    # Compute model deltas and market deltas for each event
    rows = []
    for i in range(1, len(game_df)):
        # Filter admin events
        tt = type_texts[i] if i < len(type_texts) else ""
        if any(admin in tt for admin in ADMIN_TYPES):
            continue

        model_delta = p_cal[i] - p_cal[i - 1]
        # Skip zero-change events
        if abs(model_delta) < 1e-6:
            continue

        wc = game_df["wallclock_dt"].iloc[i]
        if pd.isna(wc):
            continue

        # Kalshi mid before event
        kalshi_pre = lookup_mid(kalshi_series, wc - pd.Timedelta(seconds=PRE_EVENT_SEC))
        if np.isnan(kalshi_pre):
            continue

        row = {
            "game_id": game_id,
            "play_index": i,
            "type_text": tt,
            "period": int(game_df["period_number"].iloc[i]),
            "score_diff": float(game_df["score_diff"].iloc[i]),
            "elapsed_min": float(game_df["elapsed_game_seconds"].iloc[i]) / 60.0,
            "model_prob_before": float(p_cal[i - 1]),
            "model_prob_after": float(p_cal[i]),
            "model_delta": float(model_delta),
            "kalshi_pre": float(kalshi_pre),
        }

        for w in windows_sec:
            kalshi_post = lookup_mid(kalshi_series, wc + pd.Timedelta(seconds=w))
            market_delta = kalshi_post - kalshi_pre if not np.isnan(kalshi_post) else np.nan
            row[f"kalshi_post_{w}s"] = kalshi_post if not np.isnan(kalshi_post) else np.nan
            row[f"market_delta_{w}s"] = market_delta

        rows.append(row)

    if not rows:
        return None
    return pd.DataFrame(rows)


# ── Multi-Game Runner ─────────────────────────────────────────────────────────

def find_eligible_games(gp, n_games=None):
    """Find games with both Kalshi prior and Kalshi trade data."""
    has_kalshi = gp["kalshi_pregame_wp"].notna()
    eligible = gp[has_kalshi].copy()
    eligible = eligible.sort_values("game_date", ascending=False)

    # Check which have Kalshi trade files
    valid_ids = []
    for gid in eligible["game_id"]:
        if list((DATA_DIR / "kalshi_live").glob(f"{gid}_*.csv")):
            # Also check PBP exists
            found = False
            for sdir in (DATA_DIR / "games_live").iterdir():
                if sdir.is_dir() and list(sdir.glob(f"{gid}_*.csv")):
                    found = True
                    break
            if found:
                valid_ids.append(gid)
    if n_games:
        valid_ids = valid_ids[:n_games]
    return valid_ids


def run_analysis(n_games=None, game_id=None, windows_sec=WINDOWS_SEC):
    """Main entry: load models, process games, aggregate."""
    print("Loading models and data...")
    gam_reg, gam_ot, iso_post, _, config, hp = load_all_artifacts()
    gp = load_game_predictions()
    season_baselines = load_season_baselines()
    prior_features = load_prior_features()

    if game_id:
        game_ids = [game_id]
    else:
        game_ids = find_eligible_games(gp, n_games)

    print(f"Processing {len(game_ids)} games...")
    all_events = []
    for idx, gid in enumerate(game_ids):
        try:
            result = analyze_single_game(
                gid, gam_reg, gam_ot, iso_post, config, hp,
                gp, season_baselines, prior_features, windows_sec,
            )
            if result is not None:
                all_events.append(result)
                n_ev = len(result)
            else:
                n_ev = 0
        except Exception as e:
            n_ev = 0
            print(f"  [{idx+1}/{len(game_ids)}] {gid} — FAILED: {e}")
            continue
        if (idx + 1) % 10 == 0 or idx == len(game_ids) - 1:
            print(f"  [{idx+1}/{len(game_ids)}] {gid} — {n_ev} events")

    if not all_events:
        print("No events collected. Exiting.")
        sys.exit(1)

    events = pd.concat(all_events, ignore_index=True)
    print(f"\nTotal: {len(events)} events across {events['game_id'].nunique()} games")
    return events


# ── Analysis & Plots ──────────────────────────────────────────────────────────

def print_summary(events, windows_sec):
    """Print correlation tables."""
    print("\n" + "=" * 70)
    print("  MODEL DELTA vs MARKET DELTA — CORRELATION ANALYSIS")
    print("=" * 70)

    print(f"\n{'Window':>8s}  {'N':>6s}  {'Pearson r':>10s}  {'p-value':>10s}  "
          f"{'Spearman r':>10s}  {'Sign Agree':>10s}  {'Mean |Δm|':>10s}  {'Mean |Δk|':>10s}")
    print("-" * 90)

    for w in windows_sec:
        col = f"market_delta_{w}s"
        mask = events[col].notna() & events["model_delta"].notna()
        sub = events[mask]
        if len(sub) < 10:
            continue
        md = sub["model_delta"].values
        kd = sub[col].values
        r_p, p_p = stats.pearsonr(md, kd)
        r_s, _ = stats.spearmanr(md, kd)
        sign_agree = np.mean(np.sign(md) == np.sign(kd))
        print(f"{w:>5d}s    {len(sub):>6d}  {r_p:>10.4f}  {p_p:>10.2e}  "
              f"{r_s:>10.4f}  {sign_agree:>10.3f}  {np.mean(np.abs(md)):>10.4f}  "
              f"{np.mean(np.abs(kd)):>10.4f}")

    # Filtered to large deltas
    for thresh in [0.01, 0.02, 0.05]:
        big = events[events["model_delta"].abs() >= thresh]
        print(f"\n  Filtered to |model_delta| >= {thresh} ({len(big)} events):")
        print(f"  {'Window':>8s}  {'N':>6s}  {'Pearson r':>10s}  {'Sign Agree':>10s}  "
              f"{'Mean |Δm|':>10s}  {'Mean |Δk|':>10s}")
        for w in windows_sec:
            col = f"market_delta_{w}s"
            mask = big[col].notna()
            sub = big[mask]
            if len(sub) < 5:
                continue
            md = sub["model_delta"].values
            kd = sub[col].values
            r_p, _ = stats.pearsonr(md, kd)
            sign_agree = np.mean(np.sign(md) == np.sign(kd))
            print(f"  {w:>5d}s    {len(sub):>6d}  {r_p:>10.4f}  {sign_agree:>10.3f}  "
                  f"{np.mean(np.abs(md)):>10.4f}  {np.mean(np.abs(kd)):>10.4f}")


def generate_plots(events, output_dir, windows_sec):
    """Generate all analysis plots."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find best window
    best_w, best_r = windows_sec[0], 0
    for w in windows_sec:
        col = f"market_delta_{w}s"
        mask = events[col].notna()
        if mask.sum() < 10:
            continue
        r, _ = stats.pearsonr(events.loc[mask, "model_delta"], events.loc[mask, col])
        if r > best_r:
            best_r, best_w = r, w

    # ── 1. Scatter: model_delta vs market_delta (best window) ──
    col = f"market_delta_{best_w}s"
    mask = events[col].notna()
    md = events.loc[mask, "model_delta"].values
    kd = events.loc[mask, col].values

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.scatter(md, kd, alpha=0.08, s=8, c="#3b82f6", rasterized=True)
    # Regression line
    slope, intercept, r_val, _, _ = stats.linregress(md, kd)
    x_line = np.linspace(md.min(), md.max(), 100)
    ax.plot(x_line, slope * x_line + intercept, color="#ef4444", linewidth=2,
            label=f"OLS: y={slope:.3f}x + {intercept:.4f}")
    ax.plot([-0.5, 0.5], [-0.5, 0.5], "k--", alpha=0.3, label="y=x (perfect)")
    ax.set_xlabel("Model Delta (GAM prob change)", fontsize=12)
    ax.set_ylabel(f"Market Delta (Kalshi change, {best_w}s)", fontsize=12)
    ax.set_title(f"Per-Event: Model Delta vs Market Delta ({best_w}s window)\n"
                 f"r={r_val:.4f}, slope={slope:.3f}, N={len(md):,}", fontsize=13)
    lim = max(np.percentile(np.abs(md), 99), np.percentile(np.abs(kd), 99)) * 1.1
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_aspect("equal")
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.2)
    fig.savefig(output_dir / "delta_scatter.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved delta_scatter.png")

    # ── 2. Correlation by window ──
    rs_pearson = []
    rs_spearman = []
    sign_agrees = []
    ns = []
    for w in windows_sec:
        c = f"market_delta_{w}s"
        m = events[c].notna()
        sub_md = events.loc[m, "model_delta"].values
        sub_kd = events.loc[m, c].values
        rp, _ = stats.pearsonr(sub_md, sub_kd)
        rs2, _ = stats.spearmanr(sub_md, sub_kd)
        sa = np.mean(np.sign(sub_md) == np.sign(sub_kd))
        rs_pearson.append(rp)
        rs_spearman.append(rs2)
        sign_agrees.append(sa)
        ns.append(len(sub_md))

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(windows_sec, rs_pearson, "o-", color="#3b82f6", linewidth=2, label="Pearson r")
    ax.plot(windows_sec, rs_spearman, "s--", color="#f97316", linewidth=2, label="Spearman r")
    ax.plot(windows_sec, sign_agrees, "^:", color="#10b981", linewidth=2, label="Sign Agreement")
    ax.set_xlabel("Time Window After Event (seconds)", fontsize=12)
    ax.set_ylabel("Correlation / Agreement", fontsize=12)
    ax.set_title("Model Delta vs Market Delta: Correlation by Window", fontsize=13)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_xticks(windows_sec)
    fig.savefig(output_dir / "correlation_by_window.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved correlation_by_window.png")

    # ── 3. Bias distribution ──
    col = f"market_delta_{best_w}s"
    mask = events[col].notna()
    diff = events.loc[mask, "model_delta"].values - events.loc[mask, col].values

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(diff, bins=100, color="#3b82f6", alpha=0.7, edgecolor="white", linewidth=0.3)
    ax.axvline(0, color="black", linewidth=1, linestyle="--")
    ax.axvline(np.mean(diff), color="#ef4444", linewidth=2, label=f"Mean = {np.mean(diff):.5f}")
    ax.axvline(np.median(diff), color="#f97316", linewidth=2, linestyle="--",
               label=f"Median = {np.median(diff):.5f}")
    ax.set_xlabel(f"Model Delta - Market Delta ({best_w}s)", fontsize=12)
    ax.set_ylabel("Count", fontsize=12)
    ax.set_title(f"Bias Distribution: Model Overshoot vs Undershoot\n"
                 f"Std = {np.std(diff):.5f}, N = {len(diff):,}", fontsize=13)
    ax.legend(fontsize=10)
    ax.set_xlim(np.percentile(diff, 1), np.percentile(diff, 99))
    fig.savefig(output_dir / "delta_bias_distribution.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved delta_bias_distribution.png")

    # ── 4. Binned analysis: model delta magnitude vs market response ──
    col = f"market_delta_{best_w}s"
    mask = events[col].notna() & (events["model_delta"].abs() > 1e-4)
    sub = events[mask].copy()
    sub["abs_model_delta"] = sub["model_delta"].abs()
    sub["abs_market_delta"] = sub[col].abs()
    sub["sign_match"] = (np.sign(sub["model_delta"]) == np.sign(sub[col])).astype(int)

    try:
        sub["delta_bin"] = pd.qcut(sub["abs_model_delta"], q=6, duplicates="drop")
    except ValueError:
        sub["delta_bin"] = pd.cut(sub["abs_model_delta"], bins=6)

    binned = sub.groupby("delta_bin", observed=True).agg(
        n=("model_delta", "size"),
        mean_abs_model=("abs_model_delta", "mean"),
        mean_abs_market=("abs_market_delta", "mean"),
        sign_agree=("sign_match", "mean"),
        mean_model=("model_delta", "mean"),
        mean_market=(col, "mean"),
    ).reset_index()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    x = range(len(binned))
    labels = [f"{r.mean_abs_model:.3f}" for _, r in binned.iterrows()]

    ax1.bar([i - 0.15 for i in x], binned["mean_abs_model"], width=0.3,
            color="#3b82f6", alpha=0.8, label="Mean |Model Δ|")
    ax1.bar([i + 0.15 for i in x], binned["mean_abs_market"], width=0.3,
            color="#10b981", alpha=0.8, label=f"Mean |Market Δ| ({best_w}s)")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(labels, fontsize=9)
    ax1.set_xlabel("Mean |Model Delta| per Bin", fontsize=11)
    ax1.set_ylabel("Mean Absolute Delta", fontsize=11)
    ax1.set_title("Market Response by Model Delta Magnitude", fontsize=12)
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.2, axis="y")
    # Add count labels
    for i, row in enumerate(binned.itertuples()):
        ax1.text(i, max(row.mean_abs_model, row.mean_abs_market) + 0.001,
                 f"n={row.n}", ha="center", fontsize=8, color="grey")

    ax2.bar(x, binned["sign_agree"], color="#f97316", alpha=0.8)
    ax2.axhline(0.5, color="grey", linewidth=1, linestyle="--", alpha=0.5)
    ax2.set_xticks(list(x))
    ax2.set_xticklabels(labels, fontsize=9)
    ax2.set_xlabel("Mean |Model Delta| per Bin", fontsize=11)
    ax2.set_ylabel("Fraction Signs Match", fontsize=11)
    ax2.set_title("Directional Accuracy by Event Size", fontsize=12)
    ax2.set_ylim(0, 1)
    ax2.grid(True, alpha=0.2, axis="y")

    fig.suptitle(f"Binned Analysis: Does the Market Move Proportionally? ({best_w}s window)",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    fig.savefig(output_dir / "delta_binned_analysis.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved delta_binned_analysis.png")

    # ── 5. Scatter colored by game period ──
    col = f"market_delta_{best_w}s"
    mask = events[col].notna()
    sub = events[mask].copy()

    fig, ax = plt.subplots(figsize=(8, 8))
    colors = {1: "#93c5fd", 2: "#3b82f6", 3: "#f97316", 4: "#ef4444"}
    for p in [1, 2, 3, 4]:
        pm = sub["period"] == p
        if pm.any():
            ax.scatter(sub.loc[pm, "model_delta"], sub.loc[pm, col],
                       alpha=0.12, s=8, c=colors.get(p, "#6b7280"), label=f"Q{p}",
                       rasterized=True)
    ot_mask = sub["period"] > 4
    if ot_mask.any():
        ax.scatter(sub.loc[ot_mask, "model_delta"], sub.loc[ot_mask, col],
                   alpha=0.2, s=12, c="#a855f7", label="OT", rasterized=True)
    ax.plot([-0.5, 0.5], [-0.5, 0.5], "k--", alpha=0.3)
    ax.set_xlabel("Model Delta", fontsize=12)
    ax.set_ylabel(f"Market Delta ({best_w}s)", fontsize=12)
    ax.set_title(f"Model vs Market Delta by Quarter ({best_w}s)", fontsize=13)
    lim = max(np.percentile(np.abs(sub["model_delta"]), 99),
              np.percentile(np.abs(sub[col]), 99)) * 1.1
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_aspect("equal")
    ax.legend(fontsize=9, markerscale=3)
    ax.grid(True, alpha=0.2)
    fig.savefig(output_dir / "delta_scatter_by_quarter.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved delta_scatter_by_quarter.png")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Analyze model delta vs market delta")
    parser.add_argument("--n_games", type=int, default=None,
                        help="Number of most recent games to analyze (default: all)")
    parser.add_argument("--game_id", type=str, default=None,
                        help="Analyze a single game")
    parser.add_argument("--windows", type=str, default="5,10,15,30",
                        help="Comma-separated time windows in seconds")
    args = parser.parse_args()

    windows = [int(w) for w in args.windows.split(",")]

    events = run_analysis(
        n_games=args.n_games,
        game_id=args.game_id,
        windows_sec=windows,
    )

    # Save raw events
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    events.to_csv(SAMPLES_DIR / "delta_events.csv", index=False)
    print(f"Saved delta_events.csv ({len(events):,} events)")

    print_summary(events, windows)
    generate_plots(events, SAMPLES_DIR, windows)

    print("\nDone.")


if __name__ == "__main__":
    main()
