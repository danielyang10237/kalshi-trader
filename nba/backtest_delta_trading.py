"""
Fast offline backtest of delta-trading strategy on a single game.

Replays PBP + Kalshi orderbook data, simulates trades on each score change,
and computes P&L without needing the live sim exchange.

Usage:
    python backtest_delta_trading.py
"""

import json
import math
import warnings
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import xgboost as xgb
import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")

BASE_DIR = Path(__file__).resolve().parent
SIM_DATA = BASE_DIR.parent / "sim-exchange" / "data_cache"
ARTIFACTS_DIR = BASE_DIR / "posterior_models" / "deployment"
SAMPLES_DIR = BASE_DIR / "data" / "samples"

# ── Config ────────────────────────────────────────────────────────────────────

PBP_OFFSET_SEC = -2.5       # PBP arrives 2.5s before orderbook wallclock
DELTA_SCALE = 0.6
MIN_DELTA = 0.01
AGGRESSION = 1              # cents past bid/ask for entry
EXIT_OFFSET = 0             # cents inside fair for exit
MAX_POSITION = 200
FEE_RATE = 0.07

HOME_TICKER = "KXNBAGAME-26MAR30PHIMIA-MIA"
NBA_PBP_FILE = SIM_DATA / "401810946_PHI_MIA_nba.csv"
KALSHI_JSONL = SIM_DATA / "26MAR30PHIMIA.jsonl"


# ── Load XGBoost model ───────────────────────────────────────────────────────

def load_model():
    model = xgb.XGBClassifier()
    model.load_model(str(ARTIFACTS_DIR / "xgb_posterior.json"))
    iso = joblib.load(ARTIFACTS_DIR / "iso_xgb_post.pkl")
    with open(ARTIFACTS_DIR / "best_hyperparams.json") as f:
        hp = json.load(f)
    return model, iso, hp

XGBOOST_FEATURES = [
    "score_diff", "t_reg_remaining", "t_reg_norm", "t_reg_log",
    "t_ot_remaining", "t_ot_norm", "t_ot_log", "is_ot", "ot_number",
    "pregame_logit",
    "efg_diff", "ts_diff", "off_rtg_diff", "tov_diff",
    "foul_diff", "ft_pct_diff", "timeout_diff",
    "bonus_diff", "is_home_poss_signed",
    "rest_diff", "roster_quality_diff", "win_pct_diff",
    "stl_diff", "fg3_pct_diff", "fta_rate_diff",
    "pending_ft_signed", "is_dead_ball",
    "poss_x_elapsed", "ft_x_elapsed",
]


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-max(-30, min(30, x))))

def logit(p):
    p = max(1e-7, min(1 - 1e-7, p))
    return math.log(p / (1.0 - p))

def predict(model, iso, hp, feats, prior=0.5):
    X = np.array([[feats[f] for f in XGBOOST_FEATURES]], dtype=np.float32)
    p_raw = float(model.predict_proba(X)[0][1])
    p_raw = max(1e-7, min(1 - 1e-7, p_raw))
    alpha = hp["prior_alpha"]
    tsec = hp.get("terminal_sec", 30.0)
    pw = feats["t_reg_norm"] ** alpha
    bl = pw * logit(prior) + (1 - pw) * logit(p_raw)
    p = sigmoid(bl)
    # Terminal convergence
    t_rem = feats["t_reg_remaining"]
    sd = feats["score_diff"]
    pft = feats["pending_ft_signed"]
    if t_rem < tsec and sd != 0 and pft == 0:
        tf = 1.0 - t_rem / tsec
        sf = 1.0 / (1.0 + math.exp(-1.5 * (abs(sd) - 2.0)))
        tw = tf * sf
        det = 1.0 if sd > 0 else 0.0
        p = (1 - tw) * p + tw * det
    p = max(1e-7, min(1 - 1e-7, p))
    return float(iso.predict([p])[0])


# ── Replay orderbook → time series of best bid/ask ──────────────────────────

def replay_orderbook(jsonl_path, home_ticker):
    """Replay orderbook and return arrays of (wall_ms, best_bid, best_ask)."""
    yes_book = {}
    no_book = {}
    records = []

    with open(jsonl_path) as f:
        for line in f:
            d = json.loads(line)
            if "raw" not in d:
                continue
            raw = json.loads(d["raw"])
            msg_type = raw.get("type", "")
            wall_ms = d["wall_ms"]

            if msg_type == "orderbook_snapshot" and raw["msg"].get("market_ticker") == home_ticker:
                yes_book = {}
                no_book = {}
                for ps, qs in raw["msg"].get("yes_dollars_fp", []):
                    p, q = float(ps), float(qs)
                    if q > 0: yes_book[p] = q
                for ps, qs in raw["msg"].get("no_dollars_fp", []):
                    p, q = float(ps), float(qs)
                    if q > 0: no_book[p] = q

            elif msg_type == "orderbook_delta" and raw["msg"].get("market_ticker") == home_ticker:
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
            else:
                continue

            best_bid = max(yes_book.keys()) * 100 if yes_book else None
            best_ask = (1 - max(no_book.keys())) * 100 if no_book else None
            if best_bid is not None and best_ask is not None:
                records.append((wall_ms, best_bid, best_ask))

    arr = np.array(records, dtype=np.float64)
    return arr[:, 0], arr[:, 1], arr[:, 2]  # wall_ms, bids, asks


def lookup_book(ob_ms, ob_bids, ob_asks, target_ms):
    """Look up best bid/ask at a given timestamp."""
    idx = np.searchsorted(ob_ms, target_ms, side="right") - 1
    if idx < 0:
        return None, None
    return ob_bids[idx], ob_asks[idx]


def find_exit_fill(ob_ms, ob_bids, ob_asks, entry_ms, exit_price, direction, timeout_ms=30000):
    """Scan forward from entry to find when exit would fill. Returns (fill_ms, fill_price) or None."""
    start_idx = np.searchsorted(ob_ms, entry_ms, side="right")
    end_ms = entry_ms + timeout_ms

    for i in range(start_idx, len(ob_ms)):
        if ob_ms[i] > end_ms:
            break
        if direction == "BUY":
            # Exit is a SELL — fills when best_bid >= exit_price
            if ob_bids[i] >= exit_price:
                return ob_ms[i], ob_bids[i]
        else:
            # Exit is a BUY — fills when best_ask <= exit_price
            if ob_asks[i] <= exit_price:
                return ob_ms[i], ob_asks[i]

    return None, None


# ── Load PBP and compute features ───────────────────────────────────────────

def load_pbp_with_features(model, iso, hp):
    """Load PBP, compute model probability at each row."""
    pbp = pd.read_csv(NBA_PBP_FILE)
    pbp["timestamp"] = pd.to_datetime(pbp["wallclock"], utc=True)
    pbp["wall_ms"] = pbp["timestamp"].astype(np.int64) // 10**6

    # Compute basic features for each row
    results = []
    prev_home = 0
    prev_away = 0

    # Cumulative stats
    h_fgm = h_fga = h_fg3m = h_ftm = h_fta = h_oreb = h_tov = h_stl = h_pf = h_to_used = 0
    a_fgm = a_fga = a_fg3m = a_ftm = a_fta = a_oreb = a_tov = a_stl = a_pf = a_to_used = 0

    for _, row in pbp.iterrows():
        home_score = int(row["home_score"])
        away_score = int(row["away_score"])
        is_scoring = str(row["scoring_play"]).upper() == "TRUE"

        # Parse period and clock
        period_str = row.get("period_display_value", "1st Quarter")
        clock_str = row.get("clock_display_value", "12:00.00")

        # Determine quarter number
        if "1st" in period_str: quarter = 1
        elif "2nd" in period_str: quarter = 2
        elif "3rd" in period_str: quarter = 3
        elif "4th" in period_str: quarter = 4
        elif "OT" in period_str:
            quarter = 5
            try:
                ot_num = int(period_str.split("OT")[0].strip()) if period_str[0].isdigit() else 1
                quarter = 4 + ot_num
            except:
                quarter = 5
        else:
            quarter = 1

        # Parse clock to seconds
        try:
            parts = clock_str.split(":")
            clock_sec = int(float(parts[0])) * 60 + float(parts[1]) if len(parts) == 2 else float(parts[0])
        except:
            clock_sec = 720

        is_ot = quarter > 4
        ot_number = max(0, quarter - 4)
        if not is_ot:
            t_reg_remaining = (4 - quarter) * 720 + clock_sec
            t_ot_remaining = 0
        else:
            t_reg_remaining = 0
            t_ot_remaining = clock_sec

        t_reg_norm = t_reg_remaining / 2880.0
        t_reg_log = np.log1p(t_reg_norm * 100) / np.log1p(100)
        t_ot_norm = t_ot_remaining / 300.0 if is_ot else 0.0
        t_ot_log = np.log1p(t_ot_norm * 100) / np.log1p(100) if is_ot else 0.0

        t_log = t_ot_log if is_ot else t_reg_log
        elapsed = 1.0 - t_log

        feats = {
            "score_diff": home_score - away_score,
            "t_reg_remaining": t_reg_remaining,
            "t_reg_norm": t_reg_norm,
            "t_reg_log": t_reg_log,
            "t_ot_remaining": t_ot_remaining,
            "t_ot_norm": t_ot_norm,
            "t_ot_log": t_ot_log,
            "is_ot": is_ot,
            "ot_number": ot_number,
            "pregame_logit": 0.0,  # neutral prior
            "efg_diff": 0.0, "ts_diff": 0.0, "off_rtg_diff": 0.0,
            "tov_diff": 0.0, "foul_diff": 0.0, "ft_pct_diff": 0.0,
            "timeout_diff": 0.0, "bonus_diff": 0.0,
            "is_home_poss_signed": 0.0,
            "rest_diff": 0.0, "roster_quality_diff": 0.0, "win_pct_diff": 0.0,
            "stl_diff": 0.0, "fg3_pct_diff": 0.0, "fta_rate_diff": 0.0,
            "pending_ft_signed": 0, "is_dead_ball": 0,
            "poss_x_elapsed": 0.0, "ft_x_elapsed": 0.0,
        }

        p = predict(model, iso, hp, feats, prior=0.5)

        results.append({
            "wall_ms": row["wall_ms"],
            "home_score": home_score,
            "away_score": away_score,
            "is_scoring": is_scoring,
            "quarter": quarter,
            "clock_sec": clock_sec,
            "p_home": p,
            "text": row.get("text", ""),
        })

        prev_home = home_score
        prev_away = away_score

    return pd.DataFrame(results)


# ── Main backtest ─────────────────────────────────────────────────────────────

def main():
    print("Loading model...")
    model, iso, hp = load_model()

    print("Replaying orderbook (this takes a minute)...")
    ob_ms, ob_bids, ob_asks = replay_orderbook(KALSHI_JSONL, HOME_TICKER)
    print(f"  Orderbook: {len(ob_ms):,} updates over {(ob_ms[-1]-ob_ms[0])/1000/60:.1f} min")

    print("Computing model probabilities from PBP...")
    pbp = load_pbp_with_features(model, iso, hp)
    print(f"  PBP: {len(pbp)} events, {pbp['is_scoring'].sum()} scoring plays")

    # Apply PBP offset (shift timestamps earlier to simulate faster feed)
    offset_ms = PBP_OFFSET_SEC * 1000
    pbp["trade_ms"] = pbp["wall_ms"] + offset_ms

    # Compute deltas
    pbp["p_prev"] = pbp["p_home"].shift(1)
    pbp["model_delta"] = pbp["p_home"] - pbp["p_prev"]

    # Only trade on scoring plays with sufficient delta
    tradeable = pbp[
        pbp["is_scoring"] &
        pbp["model_delta"].notna() &
        (pbp["model_delta"].abs() >= MIN_DELTA)
    ].copy()
    print(f"  Tradeable events: {len(tradeable)}")

    # Simulate trades
    trades = []
    position = 0
    total_pnl = 0
    total_fees = 0

    for _, event in tradeable.iterrows():
        delta = event["model_delta"]
        direction = "BUY" if delta > 0 else "SELL"
        trade_ms = event["trade_ms"]

        # Position limit
        if direction == "BUY" and position >= MAX_POSITION:
            continue
        if direction == "SELL" and position <= -MAX_POSITION:
            continue

        # Look up orderbook at trade time
        bid, ask = lookup_book(ob_ms, ob_bids, ob_asks, trade_ms)
        if bid is None or ask is None:
            continue
        mid = (bid + ask) / 2

        # Size
        abs_delta = abs(delta)
        scale = min(abs_delta / 0.08, 1.0)
        size = max(5, min(50, round(5 + 45 * scale)))

        # Cap by position limit
        if direction == "BUY":
            size = min(size, MAX_POSITION - position)
        else:
            size = min(size, MAX_POSITION + position)
        if size <= 0:
            continue

        # Entry price
        if direction == "BUY":
            entry_price = ask + AGGRESSION
        else:
            entry_price = bid - AGGRESSION

        # Fair value (where market should reprice to)
        expected_move = delta * DELTA_SCALE
        fair = mid + expected_move * 100

        # Exit price
        if direction == "BUY":
            exit_price = round(fair) - EXIT_OFFSET
        else:
            exit_price = round(fair) + EXIT_OFFSET

        # Fee
        p_at_price = entry_price / 100.0
        fee_per = math.ceil(FEE_RATE * p_at_price * (1 - p_at_price) * 100) / 100 * 100  # cents

        # Find exit fill
        fill_ms, fill_price = find_exit_fill(
            ob_ms, ob_bids, ob_asks, trade_ms, exit_price, direction, timeout_ms=60000
        )

        if fill_ms is not None:
            # Trade completed
            if direction == "BUY":
                pnl_cents = (fill_price - entry_price) * size
            else:
                pnl_cents = (entry_price - fill_price) * size
            total_fees_trade = fee_per * size / 100  # rough
            pnl_cents -= total_fees_trade
            exit_delay_s = (fill_ms - trade_ms) / 1000
            status = "FILLED"
        else:
            # Exit didn't fill — mark to market at 30s later
            mtm_bid, mtm_ask = lookup_book(ob_ms, ob_bids, ob_asks, trade_ms + 60000)
            if mtm_bid is None:
                mtm_bid, mtm_ask = bid, ask
            mtm_mid = (mtm_bid + mtm_ask) / 2
            if direction == "BUY":
                pnl_cents = (mtm_mid - entry_price) * size
            else:
                pnl_cents = (entry_price - mtm_mid) * size
            total_fees_trade = fee_per * size / 100
            pnl_cents -= total_fees_trade
            exit_delay_s = 60.0
            status = "MTM"

        total_pnl += pnl_cents
        total_fees += total_fees_trade

        # Update position
        if direction == "BUY":
            position += size
        else:
            position -= size
        # If exit filled, position goes back
        if status == "FILLED":
            if direction == "BUY":
                position -= size
            else:
                position += size

        trades.append({
            "time": event["wall_ms"],
            "quarter": event["quarter"],
            "clock": event["clock_sec"],
            "score": f"{event['away_score']}-{event['home_score']}",
            "direction": direction,
            "size": size,
            "entry": entry_price,
            "exit_target": exit_price,
            "exit_actual": fill_price if status == "FILLED" else None,
            "exit_delay_s": round(exit_delay_s, 1),
            "status": status,
            "pnl_cents": round(pnl_cents, 1),
            "cum_pnl": round(total_pnl, 1),
            "position": position,
            "delta": round(delta, 4),
            "mid": round(mid, 1),
            "fair": round(fair, 1),
            "text": event["text"][:60],
        })

    # ── Results ──
    df = pd.DataFrame(trades)
    print(f"\n{'='*80}")
    print(f"  BACKTEST RESULTS: PHI @ MIA (2026-03-30)")
    print(f"  PBP offset: {PBP_OFFSET_SEC}s | Delta scale: {DELTA_SCALE} | Min delta: {MIN_DELTA}")
    print(f"{'='*80}")
    print(f"\n  Total trades:     {len(df)}")
    print(f"  Exits filled:     {(df['status']=='FILLED').sum()} ({(df['status']=='FILLED').mean()*100:.0f}%)")
    print(f"  Exits MTM:        {(df['status']=='MTM').sum()}")
    print(f"  Total P&L:        ${total_pnl/100:.2f}")
    print(f"  Total fees:       ${total_fees/100:.2f}")
    print(f"  P&L after fees:   ${(total_pnl)/100:.2f}")
    print(f"  Win rate:         {(df['pnl_cents']>0).mean()*100:.0f}%")
    print(f"  Avg win:          ${df[df['pnl_cents']>0]['pnl_cents'].mean()/100:.2f}" if (df['pnl_cents']>0).any() else "  Avg win:          N/A")
    print(f"  Avg loss:         ${df[df['pnl_cents']<=0]['pnl_cents'].mean()/100:.2f}" if (df['pnl_cents']<=0).any() else "  Avg loss:         N/A")
    print(f"  Max position:     {df['position'].abs().max()}")
    print(f"  Avg exit delay:   {df[df['status']=='FILLED']['exit_delay_s'].mean():.1f}s" if (df['status']=='FILLED').any() else "")

    # Show all trades
    print(f"\n  {'Q':>2s} {'Clock':>6s} {'Score':>7s} {'Dir':>4s} {'Sz':>3s} {'Entry':>5s} {'Exit':>5s} "
          f"{'Delay':>6s} {'Status':>6s} {'P&L':>7s} {'Cum':>8s} {'Pos':>4s} {'Delta':>7s} | Event")
    print("  " + "-" * 110)
    for _, t in df.iterrows():
        exit_str = f"{t['exit_actual']:.0f}c" if t['exit_actual'] else f"({t['exit_target']:.0f})"
        print(f"  Q{int(t['quarter']):1d} {t['clock']:>5.0f}s {t['score']:>7s} {t['direction']:>4s} {t['size']:>3d} "
              f"{t['entry']:>4.0f}c {exit_str:>5s} {t['exit_delay_s']:>5.1f}s {t['status']:>6s} "
              f"${t['pnl_cents']/100:>6.2f} ${t['cum_pnl']/100:>7.2f} {t['position']:>4d} {t['delta']:>+7.4f} | {t['text']}")

    # ── P&L curve plot ──
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [2, 1]})

    ax1.plot(range(len(df)), df["cum_pnl"] / 100, "o-", color="#3b82f6", markersize=4, linewidth=1.5)
    ax1.axhline(0, color="grey", linewidth=0.5, linestyle="--")
    ax1.set_ylabel("Cumulative P&L ($)", fontsize=12)
    ax1.set_title(f"Delta Trading Backtest: PHI @ MIA (2026-03-30)\n"
                  f"PBP offset={PBP_OFFSET_SEC}s | {len(df)} trades | "
                  f"P&L=${total_pnl/100:.2f} | Win rate={(df['pnl_cents']>0).mean()*100:.0f}%",
                  fontsize=13, fontweight="bold")
    ax1.grid(True, alpha=0.2)

    colors = ["#10b981" if p > 0 else "#ef4444" for p in df["pnl_cents"]]
    ax2.bar(range(len(df)), df["pnl_cents"] / 100, color=colors, alpha=0.7)
    ax2.axhline(0, color="grey", linewidth=0.5)
    ax2.set_ylabel("Per-Trade P&L ($)", fontsize=12)
    ax2.set_xlabel("Trade #", fontsize=12)
    ax2.grid(True, alpha=0.2)

    fig.tight_layout()
    fig.savefig(SAMPLES_DIR / "backtest_delta_pnl.png", dpi=150, bbox_inches="tight")
    print(f"\nSaved plot to {SAMPLES_DIR / 'backtest_delta_pnl.png'}")


if __name__ == "__main__":
    main()
