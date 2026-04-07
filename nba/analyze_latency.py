"""
Analyze the timing relationship between ESPN PBP wallclocks and Kalshi market movements.

Key question: Does the market move BEFORE or AFTER the ESPN wallclock timestamp?
This determines whether the ESPN delay destroys or preserves our latency edge.

Approach: For each state-changing PBP event, measure the Kalshi mid at multiple
lookback AND lookahead windows relative to the ESPN wallclock, then correlate
model_delta with market movements at each offset.
"""

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
from analyze_delta_edge import (
    build_kalshi_mid_series,
    lookup_mid,
    ADMIN_TYPES,
)

warnings.filterwarnings("ignore")

SAMPLES_DIR = DATA_DIR / "samples"

# Offsets relative to ESPN wallclock: negative = before, positive = after
OFFSETS_SEC = [-30, -20, -15, -10, -7, -5, -3, -2, -1, 0, 1, 2, 3, 5, 7, 10, 15, 20, 30]


def analyze_game_latency(
    game_id, gam_reg, gam_ot, iso_post, config, hp,
    gp, season_baselines, prior_features,
):
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

    try:
        game_df = build_game_from_pbp(game_id, prior_home_wp=kalshi_prior)
    except Exception:
        return None
    game_df = attach_baselines_and_priors(game_df, game_id, season_baselines, prior_features)
    game_df = apply_feature_pipeline(game_df, tau)
    p_raw = gam_predict_proba(game_df, gam_reg, gam_ot)
    p_post = postprocess_predictions(p_raw, game_df, prior_alpha, terminal_sec)
    p_cal = iso_post.predict(p_post)

    # Load raw PBP for type_text
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

    game_df["wallclock_dt"] = pd.to_datetime(game_df["wallclock"], utc=True, format="ISO8601")

    kalshi_series = build_kalshi_mid_series(game_id)
    if kalshi_series is None or len(kalshi_series) < 10:
        return None

    rows = []
    for i in range(1, len(game_df)):
        tt = type_texts[i] if i < len(type_texts) else ""
        if any(admin in tt for admin in ADMIN_TYPES):
            continue

        model_delta = p_cal[i] - p_cal[i - 1]
        if abs(model_delta) < 1e-6:
            continue

        wc = game_df["wallclock_dt"].iloc[i]
        if pd.isna(wc):
            continue

        # Measure Kalshi mid at every offset relative to wallclock
        # Use a "far before" anchor: 60s before wallclock
        anchor_mid = lookup_mid(kalshi_series, wc - pd.Timedelta(seconds=60))
        if np.isnan(anchor_mid):
            continue

        row = {
            "game_id": game_id,
            "model_delta": float(model_delta),
            "anchor_mid": float(anchor_mid),
        }
        for offset in OFFSETS_SEC:
            ts = wc + pd.Timedelta(seconds=offset)
            mid = lookup_mid(kalshi_series, ts)
            row[f"kalshi_at_{offset:+d}s"] = float(mid) if not np.isnan(mid) else np.nan
            row[f"market_move_{offset:+d}s"] = (float(mid) - anchor_mid) if not np.isnan(mid) else np.nan

        rows.append(row)

    if not rows:
        return None
    return pd.DataFrame(rows)


def main():
    print("Loading models and data...")
    gam_reg, gam_ot, iso_post, _, config, hp = load_all_artifacts()
    gp = load_game_predictions()
    season_baselines = load_season_baselines()
    prior_features = load_prior_features()

    # Find eligible games
    has_kalshi = gp["kalshi_pregame_wp"].notna()
    eligible = gp[has_kalshi].sort_values("game_date", ascending=False)
    game_ids = []
    for gid in eligible["game_id"]:
        if list((DATA_DIR / "kalshi_live").glob(f"{gid}_*.csv")):
            found = False
            for sdir in (DATA_DIR / "games_live").iterdir():
                if sdir.is_dir() and list(sdir.glob(f"{gid}_*.csv")):
                    found = True
                    break
            if found:
                game_ids.append(gid)
    game_ids = game_ids[:50]

    print(f"Processing {len(game_ids)} games...")
    all_events = []
    for idx, gid in enumerate(game_ids):
        try:
            result = analyze_game_latency(
                gid, gam_reg, gam_ot, iso_post, config, hp,
                gp, season_baselines, prior_features,
            )
            if result is not None:
                all_events.append(result)
        except Exception as e:
            print(f"  {gid} FAILED: {e}")
        if (idx + 1) % 10 == 0:
            print(f"  [{idx+1}/{len(game_ids)}]")

    events = pd.concat(all_events, ignore_index=True)
    print(f"\nTotal: {len(events)} events across {events['game_id'].nunique()} games")

    # ── Analysis 1: Correlation of model_delta with market_move at each offset ──
    # This tells us: at what point relative to the ESPN wallclock has the market
    # already incorporated the event?

    print("\n" + "=" * 80)
    print("  CORRELATION: model_delta vs market_move at each offset from ESPN wallclock")
    print("  (market_move = kalshi_mid_at_offset - kalshi_mid_60s_before)")
    print("=" * 80)

    offsets_results = []

    # All events
    print(f"\n  ALL EVENTS (N={len(events)})")
    print(f"  {'Offset':>8s}  {'Pearson r':>10s}  {'Sign Agree':>10s}  {'Mean |Δk|':>10s}")
    print("  " + "-" * 50)
    for offset in OFFSETS_SEC:
        col = f"market_move_{offset:+d}s"
        mask = events[col].notna()
        sub = events[mask]
        if len(sub) < 10:
            continue
        md = sub["model_delta"].values
        kd = sub[col].values
        r, _ = stats.pearsonr(md, kd)
        sa = np.mean(np.sign(md) == np.sign(kd))
        mean_abs_k = np.mean(np.abs(kd))
        offsets_results.append({"offset": offset, "r": r, "sign_agree": sa,
                                "mean_abs_k": mean_abs_k, "filter": "all"})
        marker = "  <-- ESPN wallclock" if offset == 0 else ""
        print(f"  {offset:>+5d}s    {r:>10.4f}  {sa:>10.3f}  {mean_abs_k:>10.4f}{marker}")

    # Large deltas only
    big = events[events["model_delta"].abs() >= 0.02]
    print(f"\n  LARGE EVENTS |model_delta| >= 0.02 (N={len(big)})")
    print(f"  {'Offset':>8s}  {'Pearson r':>10s}  {'Sign Agree':>10s}  {'Mean |Δk|':>10s}")
    print("  " + "-" * 50)
    for offset in OFFSETS_SEC:
        col = f"market_move_{offset:+d}s"
        mask = big[col].notna()
        sub = big[mask]
        if len(sub) < 10:
            continue
        md = sub["model_delta"].values
        kd = sub[col].values
        r, _ = stats.pearsonr(md, kd)
        sa = np.mean(np.sign(md) == np.sign(kd))
        mean_abs_k = np.mean(np.abs(kd))
        offsets_results.append({"offset": offset, "r": r, "sign_agree": sa,
                                "mean_abs_k": mean_abs_k, "filter": "big"})
        marker = "  <-- ESPN wallclock" if offset == 0 else ""
        print(f"  {offset:>+5d}s    {r:>10.4f}  {sa:>10.3f}  {mean_abs_k:>10.4f}{marker}")

    # ── Plot 1: Correlation curve across offsets ──
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    for label, filt, color, ls in [
        ("All events", "all", "#3b82f6", "-"),
        ("|Δ| >= 0.02", "big", "#ef4444", "--"),
    ]:
        sub = [r for r in offsets_results if r["filter"] == filt]
        if not sub:
            continue
        offsets = [r["offset"] for r in sub]
        rs = [r["r"] for r in sub]
        sas = [r["sign_agree"] for r in sub]
        abs_ks = [r["mean_abs_k"] for r in sub]

        ax1.plot(offsets, rs, f"o{ls}", color=color, linewidth=2, markersize=5, label=f"{label} (Pearson r)")
        ax2.plot(offsets, abs_ks, f"o{ls}", color=color, linewidth=2, markersize=5, label=label)

    ax1.axvline(0, color="black", linewidth=2, linestyle="-", alpha=0.5, label="ESPN wallclock")
    ax1.set_xlabel("Offset from ESPN Wallclock (seconds)", fontsize=12)
    ax1.set_ylabel("Pearson Correlation (model_delta vs market_move)", fontsize=12)
    ax1.set_title("When Does the Market Incorporate the Event?", fontsize=13)
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.3)

    ax2.axvline(0, color="black", linewidth=2, linestyle="-", alpha=0.5, label="ESPN wallclock")
    ax2.set_xlabel("Offset from ESPN Wallclock (seconds)", fontsize=12)
    ax2.set_ylabel("Mean |Market Move| from 60s-before anchor", fontsize=12)
    ax2.set_title("Market Movement Magnitude Over Time", fontsize=13)
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3)

    fig.suptitle("ESPN PBP Latency Analysis: Market Repricing vs ESPN Logging",
                 fontsize=14, fontweight="bold", y=1.02)
    fig.tight_layout()
    fig.savefig(SAMPLES_DIR / "latency_analysis.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\nSaved latency_analysis.png")

    # ── Plot 2: Incremental correlation (does the market keep moving after wallclock?) ──
    # For this, compute correlation of model_delta with the INCREMENTAL market move
    # between adjacent offset windows
    print("\n" + "=" * 80)
    print("  INCREMENTAL MARKET MOVE between adjacent offsets")
    print("  (Does the market keep moving after the ESPN wallclock?)")
    print("=" * 80)

    inc_results = []
    for filt_name, df in [("all", events), ("big", big)]:
        print(f"\n  {filt_name.upper()} (N={len(df)})")
        print(f"  {'Window':>12s}  {'Pearson r':>10s}  {'Sign Agree':>10s}  {'Mean |inc|':>10s}")
        print("  " + "-" * 55)
        for j in range(1, len(OFFSETS_SEC)):
            o1 = OFFSETS_SEC[j - 1]
            o2 = OFFSETS_SEC[j]
            col1 = f"market_move_{o1:+d}s"
            col2 = f"market_move_{o2:+d}s"
            mask = df[col1].notna() & df[col2].notna()
            sub = df[mask]
            if len(sub) < 10:
                continue
            inc = sub[col2].values - sub[col1].values
            md = sub["model_delta"].values
            r, _ = stats.pearsonr(md, inc)
            sa = np.mean(np.sign(md) == np.sign(inc))
            mean_abs = np.mean(np.abs(inc))
            inc_results.append({
                "o1": o1, "o2": o2, "r": r, "sign_agree": sa,
                "mean_abs": mean_abs, "filter": filt_name,
            })
            print(f"  {o1:>+4d}s→{o2:>+4d}s  {r:>10.4f}  {sa:>10.3f}  {mean_abs:>10.5f}")

    # Plot incremental correlation
    fig, ax = plt.subplots(figsize=(12, 5))
    for label, filt, color in [
        ("All events", "all", "#3b82f6"),
        ("|Δ| >= 0.02", "big", "#ef4444"),
    ]:
        sub = [r for r in inc_results if r["filter"] == filt]
        if not sub:
            continue
        midpoints = [(r["o1"] + r["o2"]) / 2 for r in sub]
        rs = [r["r"] for r in sub]
        ax.plot(midpoints, rs, "o-", color=color, linewidth=2, markersize=5, label=label)

    ax.axvline(0, color="black", linewidth=2, linestyle="-", alpha=0.5)
    ax.axhline(0, color="grey", linewidth=1, linestyle="--", alpha=0.5)
    ax.set_xlabel("Midpoint of Time Window (seconds from ESPN wallclock)", fontsize=12)
    ax.set_ylabel("Pearson r (model_delta vs incremental market move)", fontsize=12)
    ax.set_title("Incremental Market Repricing: Where Is New Information Being Absorbed?",
                 fontsize=13, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(SAMPLES_DIR / "latency_incremental.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved latency_incremental.png")

    print("\nDone.")


if __name__ == "__main__":
    main()
