# NBA Repo: Structure and How It Works

This directory contains data pipelines and models for NBA game prediction: **prior** (pre-game) win probability and **posterior** (in-game) win probability. Data is fetched from ESPN, standardized, then used to train and run the models.

---

## Directory layout

```
nba/
├── data/                          # All data (CSVs, per-game/per-player files)
│   ├── games.csv                  # Master game list + box scores (append-only)
│   ├── games_predictions.csv      # Prior model predictions (research model)
│   ├── games_predictions_deploy.csv # Prior model predictions (deploy model)
│   ├── training_games.csv         # Games + running W/L
│   ├── training_games2.csv        # + recency-weighted team stats + roster weights
│   ├── training_games3.csv        # + rolling player averages (5g, 10g, 20g)
│   ├── training_games4.csv        # Final prior-model training set (cleaned)
│   ├── posterior_training.csv # In-game PBP rows for posterior model (~3.6M rows)
│   ├── team_stats.csv             # Team box scores from hoopR (used by update_data)
│   ├── games_live/{year}/         # ESPN play-by-play CSVs (one file per game, 63 cols)
│   ├── game_rosters/{year}/       # Per-game player rosters from ESPN (one CSV per game)
│   ├── player_stats/              # Per-player career logs (one CSV per player ID)
│   ├── schedules/                 # Season schedule CSVs (from hoopR)
│   ├── kalshi_live/               # Kalshi 100ms trade data per game
│   └── samples/                   # Sample game traces and prediction outputs
├── update_data.ipynb              # Central pipeline: fetch + standardize + training data
├── data_preprocess.ipynb          # Feature engineering: roster weighting, player stats
├── scripts/
│   ├── fetch_kalshi.py            # Fetch Kalshi trade data for games
│   └── build_prediction_row.py    # Build prediction rows for inference
├── prior_modeling.ipynb           # Pre-game model (XGBoost) → prior P(home_win)
├── prior_modeling_research.ipynb  # Research prior model (with saved outputs)
├── prior_modeling_deployment.ipynb # Deploy version of prior model (trained on recent data)
├── posterior_modeling2.ipynb       # Builds posterior_training.csv from PBP
├── posterior_modeling3.ipynb       # GAM in-game model; main posterior pipeline
├── posterior_modeling_research.ipynb # Research posterior model (train 2018-23, val 2024, test 2025)
├── posterior_modeling_deployment.ipynb # Deployment posterior model (train 2021-26, Kalshi anchor)
├── verify_prior_research.ipynb    # Compare XGBoost prior vs Kalshi pregame odds
├── verify_posterior_research.ipynb # Posterior model evaluation
├── prior_models/                  # Saved XGBoost models (.pkl)
├── posterior_models/
│   ├── research/                  # GAM models from posterior_modeling_research
│   └── deployment/                # GAM models from posterior_modeling_deployment
├── outdated/                      # Old fetch/standardization scripts and notebooks
└── claude.md                      # This file
```

---

## Data flow

### 1. **update_data.ipynb** (central pipeline)

Run cells sequentially in Jupyter. Key variables set in cell 2:
- `SCHEDULE` — path to schedule CSV (e.g. `data/schedules/schedule_2025-26.csv`)
- `DATE_FROM` / `DATE_TO` — date range to process

**Cells (in order):**

| Cell | What it does | Outputs |
|------|-------------|---------|
| **0** | Imports and setup | — |
| **1** | Standardize team names in PBP files | Renames files + content columns |
| **2** | Fetch box scores from ESPN for new games | Appends to `data/games.csv`, `data/team_stats.csv` |
| **3** | (hoopR/R) Team stats builder | `data/team_stats.csv` |
| **4** | Fetch game rosters from ESPN | `data/game_rosters/{year}/*.csv` |
| **5** | **Fetch missing ESPN play-by-play** (Python, ESPN summary API) | `data/games_live/{year}/*.csv` |
| **6** | Compute `training_games.csv` (running W/L per team, days rest) | `data/training_games.csv` |
| **7** | Compute `training_games2.csv` (recency-weighted team stats) | `data/training_games2.csv` |
| **8** | Update `player_stats/` from game rosters | `data/player_stats/{player_id}.csv` |
| **9** | Compute rolling player averages → `training_games3.csv` | `data/training_games3.csv` |
| **10** | Cleanup → `training_games4.csv` | `data/training_games4.csv` |
| **11** | Fetch missing Kalshi trade data | `data/kalshi_live/*.csv` |
| **12** | Add Kalshi pregame implied probabilities | `kalshi_pregame_wp` column in predictions CSVs |
| **13** | Verification: check for missing data | Summary report |

**games.csv** is the source of truth for games and box scores; **game_rosters** and **player_stats** feed into the prior training set (rosters and rolling stats).

### 2. **data_preprocess.ipynb** (feature engineering)

Builds training features from raw data:
- Loads game rosters, filters out DNP players
- Weights remaining players by prior average minutes (no leakage)
- Computes rolling player stat averages (5g, 10g, 20g windows)
- Outputs enriched training CSVs

### 3. **scripts/fetch_kalshi.py** (Kalshi trade data)

Fetches Kalshi market trade data for NBA games. Also imported as a module by `update_data.ipynb`.

```bash
python scripts/fetch_kalshi.py --pbp_dir data/games_live/2026/
```

- Reads PBP files to determine game time windows
- Generates Kalshi tickers from date + team codes (internally normalizes ESPN→Kalshi codes for ticker generation only)
- Fetches trades via Kalshi API (requires `KALSHI_API_KEY` in `.env`)
- Outputs 100ms-resolution trade data to `data/kalshi_live/`
- **Output filenames use ESPN team codes** (e.g. `401810650_IND_WSH_kalshi_100ms.csv`), matching the `games_live/` naming convention. Never use Kalshi codes (GSW, NYK, SAS, NOP, WAS, UTA) in output filenames.
- Skips games that already have output files

### 4. **Prior model (pre-game)**

- **prior_modeling.ipynb** / **prior_modeling_research.ipynb** (research)
  - Reads **training_games4.csv**
  - Trains **XGBoost** classifier for P(home_win) with temporal train/val/test split
  - Saves model to `prior_models/xgboost_prior.pkl`
  - Outputs `games_predictions.csv`

- **prior_modeling_deployment.ipynb** (deploy)
  - Same architecture, retrained on recent seasons including current
  - Saves to `prior_models/xgboost_prior_deploy.pkl`
  - Outputs `games_predictions_deploy.csv`

### 5. **Posterior model (in-game)**

- **posterior_modeling_research.ipynb** (research)
  - Reads **posterior_training.csv**
  - Trains **LogisticGAM** (pyGAM) for in-game win probability
  - Uses Kalshi pregame odds where available, XGBoost prior as fallback
  - Train: 2018-2023, Val: 2024, Test: 2025
  - Tunes hyperparameters (tau, prior_alpha, terminal_sec) on validation set
  - Saves GAM models, config, and `best_hyperparams.json` to `posterior_models/research/`

- **posterior_modeling_deployment.ipynb** (deployment)
  - Same GAM architecture, imports hyperparameters from research (`best_hyperparams.json`)
  - Trains on all data from 2020-21 through 2025-26 (no val/test split)
  - Uses Kalshi pregame odds where available, XGBoost prior as fallback
  - Designed for live trading: starts close to market odds, latency edge exploitable from tipoff
  - Saves GAM models and config to `posterior_models/deployment/`

- **posterior_data_pipeline.ipynb**
  - Builds **posterior_training.csv** from PBP data (`games_live/`) + prior predictions (`games_predictions.csv`)
  - Processes each game's PBP into ~160 adaptive-sampled state snapshots with cumulative and rolling features
  - **Incremental**: detects existing game_ids and only processes new games, appends to CSV
  - ~3.6M rows, 64 columns across all seasons

### 6. **Verification**

- **verify_prior_research.ipynb**
  - Compares research XGB, deploy XGB, and Kalshi pregame odds
  - Metrics: log loss, Brier score, accuracy, ECE, ROC AUC
  - Per-game analysis, confidence buckets, biggest disagreements
  - Score-differential-implied "true probability" comparison

- **verify_posterior_research.ipynb**
  - Posterior model evaluation

---

## Key file roles

| File / dir | Role |
|------------|------|
| **games.csv** | Canonical game list + box scores (team-level) |
| **game_rosters/{year}/** | One CSV per game: player rosters + box scores from ESPN |
| **player_stats/** | One CSV per player: career game logs |
| **games_live/{year}/** | ESPN play-by-play CSVs (63 cols, one per game). Fetched via hoopR (bulk) or ESPN summary API (recent games) |
| **kalshi_live/** | Kalshi 100ms trade data per game (home/away high/low/volume) |
| **training_games4.csv** | Final feature matrix for the **prior** (pre-game) model |
| **posterior_training.csv** | Per-row game-state dataset for the **posterior** (in-game) GAM model |
| **games_predictions.csv** | Research model prior predictions per game (+ `kalshi_pregame_wp`) |
| **games_predictions_deploy.csv** | Deploy model prior predictions per game (+ `kalshi_pregame_wp`) |

---

## Conventions

- **Team abbreviations**: ESPN uses short codes (GS, NY, SA, NO, WSH, UTAH); these differ from standard NBA/Kalshi codes (GSW, NYK, SAS, NOP, WAS, UTA). Internal normalization happens via `HIST_TO_MODERN` in `update_data.ipynb` and `PBP_TO_KALSHI_CODE` in `scripts/fetch_kalshi.py`. **All output filenames** in `games_live/` and `kalshi_live/` use **ESPN codes** — never write files with Kalshi/NBA codes.
- **Game IDs**: ESPN event IDs (e.g. 401810587). PBP filenames use ESPN abbreviations (e.g. `401809234_CLE_NY.csv`).
- **Player IDs**: ESPN player IDs used throughout.
- **Outdated code**: `outdated/` holds old fetch scripts (R hoopR notebooks, old Python fetchers). The current pipeline is `update_data.ipynb`.

---

## Quick reference: run order

1. **Refresh raw and training data**
   Run cells in **update_data.ipynb** (set `DATE_FROM`/`DATE_TO` first)

2. **Train prior model**
   Run **prior_modeling.ipynb** (research) or **prior_modeling_deployment.ipynb** (deploy)

3. **Train posterior model (research)**
   Run **posterior_modeling_research.ipynb** — tunes hyperparams, saves `best_hyperparams.json`

4. **Train posterior model (deployment)**
   Run **posterior_modeling_deployment.ipynb** — imports research hyperparams, trains on 2021-26

5. **Verify models**
   Run **verify_prior_research.ipynb** and **verify_posterior_research.ipynb**

6. **Inference**
   Use the saved deployment GAM model (and same feature pipeline) to get in-game win probability for new games.
