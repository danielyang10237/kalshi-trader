import os
os.environ["PYTENSOR_FLAGS"] = ",".join([
    "cxx=",                      # no C++ compilation
    "mode=FAST_COMPILE",
    "optimizer=fast_compile",
    "exception_verbosity=high",  # <-- show real node + shapes + storage map
    "traceback__limit=50",       # <-- longer backtrace
])

# prevent BLAS thread weirdness / oversubscription
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"

import time
import numpy as np
import pandas as pd
import pymc as pm
import pytensor.tensor as pt
from sklearn.preprocessing import StandardScaler


def tprint(msg, t0):
    dt = time.time() - t0
    print(f"[{dt:8.1f}s] {msg}", flush=True)

t0 = time.time()

data_path = "nfl/modeling/data/condensed_groupZ_cluster.csv"
df = pd.read_csv(data_path)
tprint(f"Loaded data: df.shape={df.shape}", t0)

# ---- Target
y = (df["home_score"] - df["away_score"]).astype("float32").to_numpy()
tprint(f"Built target y: shape={y.shape}, mean={y.mean():.3f}, std={y.std():.3f}", t0)

# ---- Team + season ids
home_team = df["home_team"].astype(str).to_numpy()
away_team = df["away_team"].astype(str).to_numpy()
season = df["season"].astype(int).to_numpy()

teams = np.sort(np.unique(np.concatenate([home_team, away_team])))
team_to_id = {t: i for i, t in enumerate(teams)}
home_id = np.array([team_to_id[t] for t in home_team], dtype="int32")
away_id = np.array([team_to_id[t] for t in away_team], dtype="int32")
n_teams = len(teams)

seasons = np.sort(np.unique(season))
season_to_id = {s: i for i, s in enumerate(seasons)}
season_id = np.array([season_to_id[s] for s in season], dtype="int32")
n_seasons = len(seasons)

tprint(f"Teams: {n_teams} | Seasons: {n_seasons}", t0)

# ---- Separate continuous vs categorical features
mean_cols = [c for c in df.columns if c.startswith("mean_z_")]
cluster_cols = [c for c in df.columns if c.startswith("cluster_")]
tprint(f"Found mean_z_*: {len(mean_cols)} | cluster_*: {len(cluster_cols)}", t0)

# Continuous features
X_mean = df[mean_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0)
X_mean = X_mean.apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(dtype=np.float32)

scaler = StandardScaler()
X_mean_scaled = scaler.fit_transform(X_mean).astype("float32")
n_games, n_mean_features = X_mean_scaled.shape
tprint(f"X_mean_scaled: shape={X_mean_scaled.shape}", t0)

# Cluster ids as int arrays (no scaling!)
cluster_ids = {}
cluster_nlevels = {}
for c in cluster_cols:
    arr = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int).to_numpy(dtype=np.int32)

    uniq = np.unique(arr)
    remap = {v: i for i, v in enumerate(uniq)}
    arr = np.array([remap[v] for v in arr], dtype=np.int32)

    cluster_ids[c] = arr
    cluster_nlevels[c] = len(uniq)

# Print a quick summary of cluster cardinalities (super useful sanity check)
summary = sorted([(c, cluster_nlevels[c]) for c in cluster_cols], key=lambda x: -x[1])
tprint("Cluster levels (top 10 by K): " + ", ".join([f"{c}:{k}" for c, k in summary[:10]]), t0)

# ---- Model
tprint("Building PyMC model graph...", t0)

with pm.Model() as model:
    # data containers
    X_data = pm.Data("X_mean", X_mean_scaled)
    home_data = pm.Data("home_id", home_id)
    away_data = pm.Data("away_id", away_id)
    season_data = pm.Data("season_id", season_id)

    cluster_data = {c: pm.Data(c, cluster_ids[c]) for c in cluster_cols}

    # home field
    home_field = pm.Normal("home_field", mu=2.0, sigma=2.0)

    # team strength by season (random walk)
    sigma_season = pm.HalfNormal("sigma_season", 1.5)
    sigma_init = pm.HalfNormal("sigma_init", 3.0)

    s0 = pm.Normal("s0", 0.0, sigma_init, shape=(n_teams,))
    inc = pm.Normal("inc", 0.0, sigma_season, shape=(n_teams, n_seasons - 1))
    s = pt.concatenate([s0[:, None], s0[:, None] + pt.cumsum(inc, axis=1)], axis=1)

    strength_diff = s[home_data, season_data] - s[away_data, season_data]

    # continuous mean_z_* coefficients
    beta = pm.Normal("beta", 0.0, 1.0, shape=n_mean_features)
    stat_effect = pt.dot(X_data, beta)

    # categorical cluster_* effects: random intercept per cluster label
    cluster_effect_terms = []
    for c in cluster_cols:
        K = cluster_nlevels[c]
        if K <= 1:
            continue
        sigma_c = pm.HalfNormal(f"sigma_{c}", 1.0)
        eff_c = pm.Normal(f"eff_{c}", 0.0, sigma_c, shape=K)
        cluster_effect_terms.append(eff_c[cluster_data[c]])

    cluster_effect = 0.0 if len(cluster_effect_terms) == 0 else pt.add(*cluster_effect_terms)

    # robust margin likelihood
    sigma_y = pm.HalfNormal("sigma_y", 7.0)
    nu = pm.Exponential("nu", 0.1) + 2.0

    mu = home_field + strength_diff + stat_effect + cluster_effect
    pm.StudentT("margin", nu=nu, mu=mu, sigma=sigma_y, observed=y)

tprint("Model built. Starting sampling...", t0)

with model:
    idata = pm.sample(
        draws=300,
        tune=300,
        chains=1,
        cores=1,
        target_accept=0.9,
        init="jitter+adapt_diag",
        progressbar=True,
        random_seed=42,
    )

tprint("Sampling complete.", t0)

# Save for later
out_path = "nfl/modeling/data/condensed_groupZ_cluster_model.nc"
idata.to_netcdf(out_path)
tprint(f"Saved InferenceData to: {out_path}", t0)