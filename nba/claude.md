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
│   ├── posterior_training_set3.csv # In-game PBP rows for posterior model (~3.6M rows)
│   ├── team_stats.csv             # Team box scores from hoopR (used by update_data)
│   ├── games_live/{year}/         # ESPN play-by-play CSVs (one file per game, 63 cols)
│   ├── game_rosters/{year}/       # Per-game player rosters from ESPN (one CSV per game)
│   ├── player_stats/              # Per-player career logs (one CSV per player ID)
│   ├── schedules/                 # Season schedule CSVs (from hoopR)
│   ├── kalshi_live/               # Kalshi 100ms trade data per game
│   └── samples/                   # Sample game traces and prediction outputs
├── update_data.ipynb              # Central pipeline: fetch + standardize + training data
├── data_preprocess.ipynb          # Feature engineering: roster weighting, player stats
├── fetch_kalshi.py                # Fetch Kalshi trade data for games
├── prior_modeling.ipynb           # Pre-game model (XGBoost) → prior P(home_win)
├── prior_modeling_deployment.ipynb # Deploy version of prior model (trained on recent data)
├── posterior_modeling2.ipynb       # Builds posterior_training_set3.csv from PBP
├── posterior_modeling3.ipynb       # GAM in-game model; main posterior pipeline
├── verify_prior_executed.ipynb    # Compare XGBoost prior vs Kalshi pregame odds
├── posterior_modeling_executed.ipynb # Executed posterior model with results
├── prior_models/                  # Saved XGBoost models (.pkl)
├── artifacts/                     # Saved GAM models, states, config, plots
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
| **6** | Compute `training_games.csv` (running W/L per team) | `data/training_games.csv` |
| **7** | Compute `training_games2.csv` (recency-weighted team stats) | `data/training_games2.csv` |
| **8** | Update `player_stats/` from game rosters | `data/player_stats/{player_id}.csv` |
| **9** | Compute rolling player averages → `training_games3.csv` | `data/training_games3.csv` |
| **10** | Cleanup → `training_games4.csv` | `data/training_games4.csv` |

**games.csv** is the source of truth for games and box scores; **game_rosters** and **player_stats** feed into the prior training set (rosters and rolling stats).

### 2. **data_preprocess.ipynb** (feature engineering)

Builds training features from raw data:
- Loads game rosters, filters out DNP players
- Weights remaining players by prior average minutes (no leakage)
- Computes rolling player stat averages (5g, 10g, 20g windows)
- Outputs enriched training CSVs

### 3. **fetch_kalshi.py** (Kalshi trade data)

Fetches Kalshi market trade data for NBA games:

```bash
python fetch_kalshi.py --pbp_dir data/games_live/2026/
```

- Reads PBP files to determine game time windows
- Generates Kalshi tickers from date + team codes (internally normalizes ESPN→Kalshi codes for ticker generation only)
- Fetches trades via Kalshi API (requires `KALSHI_API_KEY` in `.env`)
- Outputs 100ms-resolution trade data to `data/kalshi_live/`
- **Output filenames use ESPN team codes** (e.g. `401810650_IND_WSH_kalshi_100ms.csv`), matching the `games_live/` naming convention. Never use Kalshi codes (GSW, NYK, SAS, NOP, WAS, UTA) in output filenames.
- Skips games that already have output files

### 4. **Prior model (pre-game)**

- **prior_modeling.ipynb** (research model)
  - Reads **training_games4.csv**
  - Trains **XGBoost** classifier for P(home_win) with temporal train/val/test split
  - Saves model to `prior_models/xgboost_prior.pkl`

- **prior_modeling_deployment.ipynb** (deploy model)
  - Same architecture, retrained on recent seasons including current
  - Saves to `prior_models/xgboost_prior_deploy.pkl`

### 5. **Posterior model (in-game)**

- **posterior_modeling3.ipynb** (main)
  - Reads **posterior_training_set3.csv**
  - Trains **LogisticGAM** (pyGAM) for in-game win probability
  - Two models: regulation (`te(score_diff, t_reg_norm) + l(pregame_logit) + ...`) and overtime
  - Season baseline blending with configurable tau decay
  - Post-processing with alpha/terminal tuning
  - Saves GAM models and config to `artifacts/`

- **posterior_modeling2.ipynb**
  - Builds **posterior_training_set3.csv** from PBP data
  - Defines feature engineering and row-level schema

### 6. **Verification**

- **verify_prior_executed.ipynb**
  - Compares research XGB, deploy XGB, and Kalshi pregame odds
  - Metrics: log loss, Brier score, accuracy, ECE, ROC AUC
  - Per-game analysis, confidence buckets, biggest disagreements
  - Score-differential-implied "true probability" comparison

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
| **posterior_training_set3.csv** | Per-row game-state dataset for the **posterior** (in-game) GAM model |
| **games_predictions.csv** | Research model prior predictions per game |
| **games_predictions_deploy.csv** | Deploy model prior predictions per game |

---

## Conventions

- **Team abbreviations**: ESPN uses short codes (GS, NY, SA, NO, WSH, UTAH); these differ from standard NBA/Kalshi codes (GSW, NYK, SAS, NOP, WAS, UTA). Internal normalization happens via `HIST_TO_MODERN` in `update_data.ipynb` and `PBP_TO_KALSHI_CODE` in `fetch_kalshi.py`. **All output filenames** in `games_live/` and `kalshi_live/` use **ESPN codes** — never write files with Kalshi/NBA codes.
- **Game IDs**: ESPN event IDs (e.g. 401810587). PBP filenames use ESPN abbreviations (e.g. `401809234_CLE_NY.csv`).
- **Player IDs**: ESPN player IDs used throughout.
- **Outdated code**: `outdated/` holds old fetch scripts (R hoopR notebooks, old Python fetchers). The current pipeline is `update_data.ipynb`.

---

## Quick reference: run order

1. **Refresh raw and training data**
   Run cells in **update_data.ipynb** (set `DATE_FROM`/`DATE_TO` first)

2. **Fetch Kalshi trade data** (for new games)
   `python fetch_kalshi.py --pbp_dir data/games_live/2026/`

3. **Rebuild posterior training set** (if PBP or features changed)
   Run **posterior_modeling2.ipynb**

4. **Train prior model**
   Run **prior_modeling.ipynb** (research) or **prior_modeling_deployment.ipynb** (deploy)

5. **Train posterior model**
   Run **posterior_modeling3.ipynb** (reads `posterior_training_set3.csv`, writes GAM model to `artifacts/`)

6. **Verify prior model**
   Run **verify_prior_executed.ipynb** (compares XGBoost vs Kalshi pregame odds)

7. **Inference**
   Use the saved prior model + GAM model (and same feature pipeline) to get in-game win probability for new games.
