# kalshi-trader

A research and trading toolkit for prediction markets — primarily **Kalshi**, with
exploratory clients for **Polymarket**. The core focus is **NBA win-probability
modeling**: predicting both pre-game (prior) and live in-game (posterior) win
probabilities, comparing them against market odds, and feeding an automated trader
that places orders when the model finds an edge.

The repo also includes a **simulated Kalshi exchange** for backtesting strategies
against recorded orderbook/play-by-play data, several frontends (a Next.js web
dashboard and a SwiftUI iOS client), and data pipelines for NBA and NFL.

---

## Repository layout

| Path | What it is |
|------|-----------|
| `kalshi/` | Python client for the Kalshi API — auth, series, events, markets, and an orderbook recorder. |
| `polymarket/` | Lightweight Polymarket API client (events, markets). |
| `nba/` | NBA data pipelines and models (prior XGBoost + posterior GAM win probability). See `nba/CLAUDE.md`. |
| `nfl/` | NFL data-fetching scripts (schedules, rosters, play-by-play, team/player stats) and modeling. |
| `trading-system/` | Full-stack live trading app: FastAPI backend + automated NBA trader + Next.js frontends. |
| `sim-exchange/` | A drop-in simulated Kalshi exchange that replays recorded market data for backtesting. |
| `Kalshi-Client/` | Native SwiftUI iOS client (live game dashboard, websocket feed). |
| `test/` | Scratch SwiftUI project. |
| `requirements.txt` | Shared Python dependencies (FastAPI, uvicorn, websockets, cryptography, requests). |

---

## Components

### `kalshi/` — Kalshi API client
Python wrapper around the Kalshi trade API. Handles RSA-PSS request signing
(`authentification.py`) and exposes series, events, and markets. `record_orderbook.py`
captures the full websocket feed (snapshots + deltas) to JSONL with both wall-clock
and monotonic timestamps so an orderbook can be replayed with exact timing later:

```bash
python -m kalshi.record_orderbook KXNBAGAME-26MAR30PHIMIA-PHI KXNBAGAME-26MAR30PHIMIA-MIA
```

### `nba/` — win-probability models
Two models, fed by ESPN data pipelines:

- **Prior (pre-game):** XGBoost classifier for `P(home_win)` using recency-weighted
  team stats and rolling player averages.
- **Posterior (in-game):** `LogisticGAM` (pyGAM) over score differential and game time,
  blended with a season baseline.

The pipeline fetches box scores, rosters, and play-by-play from ESPN, fetches matching
Kalshi trade data at 100ms resolution, builds training sets, trains both models, and
verifies the prior against Kalshi's pre-game odds. See **`nba/CLAUDE.md`** for the full
data flow and run order.

### `trading-system/` — live trading app
A FastAPI backend (`python_app/server.py`) that wraps the Kalshi client, exposes market
data / config / trading / websocket routes, and runs an **automated NBA trader**
(`python_app/nba/trader.py`). The trader computes edge between the model's theo price and
the market best ask, applies risk checks (max position, exposure, order size), and lifts
the ask when the edge clears a threshold. Two frontends (`frontend/` and
`Kalshi-Client-Web/`, both Next.js + TypeScript + Tailwind) provide the UI; the backend
can serve the built client at `/client`. See `trading-system/README.md`.

### `sim-exchange/` — simulated exchange
A self-contained FastAPI service (`backend/server.py`, port 9000) that acts as a drop-in
replacement for the real Kalshi API. It maintains independent orderbooks per ticker,
replays recorded market data and play-by-play, and includes a simple market maker — so
strategies can be backtested without touching real money. Has its own Next.js frontend.

### `Kalshi-Client/` — iOS app
A SwiftUI client with a live game dashboard and websocket manager for streaming
real-time NBA market data.

---

## Setup

### Python
```bash
pip install -r requirements.txt
# some subprojects (e.g. sim-exchange) have their own requirements.txt
pip install -r sim-exchange/backend/requirements.txt
```

The NBA modeling notebooks additionally need `xgboost`, `pygam`, `pandas`,
`numpy`, `scikit-learn`, and `jupyter`.

### Credentials
Create a `.env` in the repo root with your Kalshi API credentials:

```bash
KALSHI_API_KEY=your_api_key_here
KALSHI_PRIVATE_KEY_PATH=kalshi_key.pem
KALSHI_WS_URL=wss://api.elections.kalshi.com/trade-api/ws/v2
FRONTEND_ORIGIN=http://localhost:3000
```

Place your Kalshi RSA private key (`kalshi_key.pem`) where `KALSHI_PRIVATE_KEY_PATH`
points. **Never commit `.env` or your key file.**

---

## Running things

**Live trading backend** (from repo root):
```bash
uvicorn trading-system.python_app.server:app --reload --port 8000
```

**Trading frontend:**
```bash
cd trading-system/frontend && npm install && npm run dev   # http://localhost:3000
```

**Simulated exchange:**
```bash
cd sim-exchange && python run.py                            # http://localhost:9000
cd sim-exchange/frontend && npm install && npm run dev
```

**NBA models:** open the notebooks in `nba/` and follow the run order in `nba/CLAUDE.md`.

---
