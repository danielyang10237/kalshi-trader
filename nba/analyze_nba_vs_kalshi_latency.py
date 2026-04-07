"""
Compare timestamps between the live NBA PBP feed and Kalshi orderbook movements
for the PHI @ MIA game (2026-03-30, game_id 401810946).

Question: Does our NBA PBP data arrive before or after the Kalshi market moves?
"""

import json
import warnings
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats

warnings.filterwarnings("ignore")

BASE_DIR = Path(__file__).resolve().parent
SIM_DATA = BASE_DIR.parent / "sim-exchange" / "data_cache"
SAMPLES_DIR = BASE_DIR / "data" / "samples"

NBA_PBP_FILE = SIM_DATA / "401810946_PHI_MIA_nba.csv"
KALSHI_JSONL = SIM_DATA / "26MAR30PHIMIA.jsonl"

HOME_TICKER = "KXNBAGAME-26MAR30PHIMIA-MIA"


# ── 1. Replay Kalshi orderbook → best bid/ask/mid time series ─────────────────

def replay_kalshi_orderbook(jsonl_path: str, home_ticker: str) -> pd.DataFrame:
    """Replay orderbook snapshots + deltas to produce a time series of best bid/ask/mid."""
    yes_book: dict[float, float] = {}  # price → quantity (YES side = bids)
    no_book: dict[float, float] = {}   # price → quantity (NO side → implied asks)

    records = []

    with open(jsonl_path) as f:
        for line in f:
            d = json.loads(line)

            if "raw" not in d:
                continue
            raw = json.loads(d["raw"])
            msg_type = raw.get("type", "")
            wall_ms = d["wall_ms"]

            if msg_type == "orderbook_snapshot":
                if raw["msg"].get("market_ticker") != home_ticker:
                    continue
                # Reset book from snapshot
                yes_book = {}
                no_book = {}
                for price_str, qty_str in raw["msg"].get("yes_dollars_fp", []):
                    p, q = float(price_str), float(qty_str)
                    if q > 0:
                        yes_book[p] = q
                for price_str, qty_str in raw["msg"].get("no_dollars_fp", []):
                    p, q = float(price_str), float(qty_str)
                    if q > 0:
                        no_book[p] = q

                best_bid = max(yes_book.keys()) if yes_book else None
                best_ask = (1 - max(no_book.keys())) if no_book else None
                mid = None
                if best_bid is not None and best_ask is not None:
                    mid = (best_bid + best_ask) / 2
                records.append({"wall_ms": wall_ms, "best_bid": best_bid,
                                "best_ask": best_ask, "mid": mid})

            elif msg_type == "orderbook_delta":
                if raw["msg"].get("market_ticker") != home_ticker:
                    continue
                price = float(raw["msg"]["price_dollars"])
                delta = float(raw["msg"]["delta_fp"])
                side = raw["msg"]["side"]

                book = yes_book if side == "yes" else no_book
                current = book.get(price, 0)
                new_qty = current + delta
                if new_qty > 0:
                    book[price] = new_qty
                else:
                    book.pop(price, None)

                best_bid = max(yes_book.keys()) if yes_book else None
                best_ask = (1 - max(no_book.keys())) if no_book else None
                mid = None
                if best_bid is not None and best_ask is not None:
                    mid = (best_bid + best_ask) / 2
                records.append({"wall_ms": wall_ms, "best_bid": best_bid,
                                "best_ask": best_ask, "mid": mid})

    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["wall_ms"], unit="ms", utc=True)
    return df


# ── 2. Load NBA PBP ───────────────────────────────────────────────────────────

def load_nba_pbp(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["wallclock"], utc=True)
    df["wall_ms"] = df["timestamp"].astype(np.int64) // 10**6
    return df


# ── 3. Analysis ───────────────────────────────────────────────────────────────

def main():
    print("Replaying Kalshi orderbook (this may take a minute)...")
    kalshi = replay_kalshi_orderbook(KALSHI_JSONL, HOME_TICKER)
    kalshi = kalshi.dropna(subset=["mid"]).copy()
    print(f"  Kalshi orderbook: {len(kalshi):,} updates over "
          f"{(kalshi['wall_ms'].max() - kalshi['wall_ms'].min()) / 1000 / 60:.1f} min")

    # Only keep rows where mid actually changed (for efficiency)
    kalshi["mid_changed"] = kalshi["mid"] != kalshi["mid"].shift(1)
    kalshi_changes = kalshi[kalshi["mid_changed"]].copy()
    print(f"  Mid-price changes: {len(kalshi_changes):,}")

    pbp = load_nba_pbp(NBA_PBP_FILE)
    scoring = pbp[pbp["scoring_play"].astype(str).str.upper() == "TRUE"].copy()
    print(f"  NBA PBP: {len(pbp)} events, {len(scoring)} scoring plays")

    # ── For each scoring event, measure Kalshi mid at offsets ──
    OFFSETS_SEC = [-25, -20, -15, -12, -10, -8, -7, -6, -5, -4, -3, -2, -1.5, -1, -0.5, 0,
                   0.5, 1, 1.5, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25]

    kalshi_ms = kalshi["wall_ms"].values
    kalshi_mid = kalshi["mid"].values

    def lookup_mid_fast(target_ms):
        idx = np.searchsorted(kalshi_ms, target_ms, side="right") - 1
        if idx < 0:
            return np.nan
        return kalshi_mid[idx]

    rows = []
    for _, event in scoring.iterrows():
        ev_ms = event["wall_ms"]
        # Get a "far before" anchor (30s before)
        anchor = lookup_mid_fast(ev_ms - 30_000)
        if np.isnan(anchor):
            continue

        row = {
            "text": event["text"],
            "period": event["period_display_value"],
            "clock": event["clock_display_value"],
            "home_score": event["home_score"],
            "away_score": event["away_score"],
            "pbp_wall_ms": ev_ms,
            "anchor_mid": anchor,
        }
        # Score change direction: home point = mid should go up
        prev_idx = pbp.index.get_loc(event.name) - 1
        if prev_idx >= 0:
            prev_home = pbp.iloc[prev_idx]["home_score"]
            prev_away = pbp.iloc[prev_idx]["away_score"]
            home_scored = event["home_score"] > prev_home
            row["home_scored"] = home_scored
            row["points"] = (event["home_score"] - prev_home) + (event["away_score"] - prev_away)
        else:
            row["home_scored"] = None
            row["points"] = 0

        for offset in OFFSETS_SEC:
            target_ms = ev_ms + int(offset * 1000)
            mid = lookup_mid_fast(target_ms)
            row[f"mid_{offset:+.1f}s"] = mid
            row[f"move_{offset:+.1f}s"] = mid - anchor if not np.isnan(mid) else np.nan

        rows.append(row)

    events = pd.DataFrame(rows)
    events["expected_dir"] = events["home_scored"].map({True: 1, False: -1})
    print(f"\n  Analyzed {len(events)} scoring events")

    # ── Compute directional correlation at each offset ──
    print("\n" + "=" * 80)
    print("  KALSHI MID MOVEMENT vs NBA PBP WALLCLOCK (scoring events only)")
    print("  Reference: Kalshi mid 30s before event")
    print("=" * 80)

    print(f"\n  {'Offset':>8s}  {'Mean move (home pts)':>20s}  {'Mean move (away pts)':>20s}  "
          f"{'Dir. Agree':>10s}  {'Mean |move|':>11s}")
    print("  " + "-" * 80)

    offset_stats = []
    for offset in OFFSETS_SEC:
        col = f"move_{offset:+.1f}s"
        mask = events[col].notna() & events["expected_dir"].notna()
        sub = events[mask]
        if len(sub) < 5:
            continue

        move = sub[col].values
        direction = sub["expected_dir"].values
        signed_move = move * direction  # positive = market moved in expected direction

        # Split by who scored
        home_pts = sub[sub["home_scored"] == True]
        away_pts = sub[sub["home_scored"] == False]
        mean_home = home_pts[col].mean() if len(home_pts) > 0 else 0
        mean_away = away_pts[col].mean() if len(away_pts) > 0 else 0

        dir_agree = np.mean(signed_move > 0)
        mean_abs = np.mean(np.abs(move))

        offset_stats.append({
            "offset": offset, "dir_agree": dir_agree,
            "mean_abs": mean_abs, "mean_home": mean_home, "mean_away": mean_away,
        })

        marker = "  <-- NBA PBP wallclock" if offset == 0 else ""
        print(f"  {offset:>+6.1f}s  {mean_home:>+20.5f}  {mean_away:>+20.5f}  "
              f"{dir_agree:>10.3f}  {mean_abs:>11.5f}{marker}")

    # ── Incremental analysis ──
    print(f"\n  INCREMENTAL movement between adjacent offsets:")
    print(f"  {'Window':>14s}  {'Dir. Agree':>10s}  {'Mean |inc|':>11s}")
    print("  " + "-" * 45)

    inc_stats = []
    for j in range(1, len(OFFSETS_SEC)):
        o1, o2 = OFFSETS_SEC[j-1], OFFSETS_SEC[j]
        col1 = f"move_{o1:+.1f}s"
        col2 = f"move_{o2:+.1f}s"
        mask = events[col1].notna() & events[col2].notna() & events["expected_dir"].notna()
        sub = events[mask]
        if len(sub) < 5:
            continue
        inc = sub[col2].values - sub[col1].values
        direction = sub["expected_dir"].values
        signed_inc = inc * direction
        dir_agree = np.mean(signed_inc > 0)
        mean_abs = np.mean(np.abs(inc))
        inc_stats.append({"o1": o1, "o2": o2, "dir_agree": dir_agree, "mean_abs": mean_abs})
        print(f"  {o1:>+5.1f}s→{o2:>+5.1f}s  {dir_agree:>10.3f}  {mean_abs:>11.5f}")

    # ── Plot 1: Cumulative market movement around PBP wallclock ──
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

    # Average mid movement for home-scoring vs away-scoring events
    offsets_arr = [s["offset"] for s in offset_stats]
    home_moves = [s["mean_home"] for s in offset_stats]
    away_moves = [s["mean_away"] for s in offset_stats]
    dir_agrees = [s["dir_agree"] for s in offset_stats]
    mean_abs = [s["mean_abs"] for s in offset_stats]

    ax1.plot(offsets_arr, home_moves, "o-", color="#3b82f6", linewidth=2, markersize=4,
             label="Home scoring plays (mid should rise)")
    ax1.plot(offsets_arr, away_moves, "o-", color="#ef4444", linewidth=2, markersize=4,
             label="Away scoring plays (mid should fall)")
    ax1.axvline(0, color="black", linewidth=2, alpha=0.5, label="NBA PBP wallclock")
    ax1.axhline(0, color="grey", linewidth=0.5, linestyle="--", alpha=0.5)
    ax1.set_xlabel("Offset from NBA PBP Wallclock (seconds)", fontsize=12)
    ax1.set_ylabel("Mean Kalshi Mid Change (from 30s-before anchor)", fontsize=12)
    ax1.set_title("When Does Kalshi Reprice vs NBA PBP Timestamp?", fontsize=13)
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.3)

    ax2.plot(offsets_arr, dir_agrees, "o-", color="#10b981", linewidth=2, markersize=4)
    ax2.axvline(0, color="black", linewidth=2, alpha=0.5, label="NBA PBP wallclock")
    ax2.axhline(0.5, color="grey", linewidth=0.5, linestyle="--", alpha=0.5)
    ax2.set_xlabel("Offset from NBA PBP Wallclock (seconds)", fontsize=12)
    ax2.set_ylabel("Directional Agreement (market moved expected way)", fontsize=12)
    ax2.set_title("Does the Market Already Reflect the Event?", fontsize=13)
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3)
    ax2.set_ylim(0.3, 1.0)

    fig.suptitle("PHI @ MIA (2026-03-30): NBA Live Feed vs Kalshi Orderbook Timing",
                 fontsize=14, fontweight="bold", y=1.02)
    fig.tight_layout()
    fig.savefig(SAMPLES_DIR / "nba_vs_kalshi_latency.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\nSaved nba_vs_kalshi_latency.png")

    # ── Plot 2: Incremental repricing ──
    fig, ax = plt.subplots(figsize=(12, 5))

    midpoints = [(s["o1"] + s["o2"]) / 2 for s in inc_stats]
    inc_dirs = [s["dir_agree"] for s in inc_stats]
    inc_abs = [s["mean_abs"] for s in inc_stats]

    ax.bar(midpoints, inc_abs, width=0.4, color="#3b82f6", alpha=0.6, label="Mean |incremental move|")
    ax_dir = ax.twinx()
    ax_dir.plot(midpoints, inc_dirs, "o-", color="#ef4444", linewidth=2, markersize=5,
                label="Directional agreement")
    ax_dir.axhline(0.5, color="grey", linewidth=0.5, linestyle="--", alpha=0.5)

    ax.axvline(0, color="black", linewidth=2, alpha=0.5)
    ax.set_xlabel("Midpoint of Time Window (seconds from NBA PBP wallclock)", fontsize=12)
    ax.set_ylabel("Mean |Incremental Mid Move|", fontsize=12, color="#3b82f6")
    ax_dir.set_ylabel("Directional Agreement", fontsize=12, color="#ef4444")
    ax_dir.set_ylim(0.3, 1.0)
    ax.set_title("Incremental Kalshi Repricing Around NBA PBP Events\n"
                 "PHI @ MIA (2026-03-30)",
                 fontsize=13, fontweight="bold")
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax_dir.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, fontsize=9, loc="upper left")
    ax.grid(True, alpha=0.2, axis="y")
    fig.tight_layout()
    fig.savefig(SAMPLES_DIR / "nba_vs_kalshi_incremental.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved nba_vs_kalshi_incremental.png")

    # ── Plot 3: Individual event traces (overlay several scoring events) ──
    fig, axes = plt.subplots(2, 3, figsize=(16, 10))
    axes = axes.flatten()

    # Pick 6 representative scoring events (mix of home/away, spread through game)
    sample_events = events.iloc[np.linspace(0, len(events)-1, 6, dtype=int)]

    for idx, (_, ev) in enumerate(sample_events.iterrows()):
        ax = axes[idx]
        offsets_plot = []
        mids_plot = []
        for offset in OFFSETS_SEC:
            col = f"mid_{offset:+.1f}s"
            if col in ev and not np.isnan(ev[col]):
                offsets_plot.append(offset)
                mids_plot.append(ev[col])

        ax.plot(offsets_plot, mids_plot, "o-", color="#3b82f6", linewidth=2, markersize=4)
        ax.axvline(0, color="black", linewidth=2, alpha=0.5)
        direction = "HOME" if ev.get("home_scored") else "AWAY"
        pts = int(ev.get("points", 0))
        ax.set_title(f"{direction} +{pts}pts | {ev['clock']} {ev['period']}\n"
                     f"{str(ev.get('text', ''))[:50]}", fontsize=9)
        ax.set_xlabel("Offset (s)", fontsize=9)
        ax.set_ylabel("Kalshi Mid", fontsize=9)
        ax.grid(True, alpha=0.2)
        ax.tick_params(labelsize=8)

    fig.suptitle("Individual Event Traces: Kalshi Mid Around NBA PBP Timestamp",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(SAMPLES_DIR / "nba_vs_kalshi_traces.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved nba_vs_kalshi_traces.png")

    print("\nDone.")


if __name__ == "__main__":
    main()
