import numpy as np
import pandas as pd
import re
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

IN_PATH  = "nfl/modeling/data/condensed_compiled_data.csv"   # change if yours differs
OUT_PATH = "nfl/modeling/data/condensed_groupZ_cluster.csv"

df = pd.read_csv(IN_PATH)

# ---- pick feature columns (diff_*)
diff_cols = [c for c in df.columns if c.startswith("diff_")]

# ---- group by prefix family: diff_<family>.<rest>  -> family
# examples:
#   diff_defensive.QBHits -> defensive
#   diff_defensiveInterceptions.interceptions -> defensiveInterceptions
#   diff_miscellaneous.thirdDownConvPct -> miscellaneous
def family_key(col: str) -> str:
    s = col[len("diff_"):]  # remove diff_
    return s.split(".", 1)[0]  # token before first dot

families = {}
for c in diff_cols:
    fam = family_key(c)
    families.setdefault(fam, []).append(c)

print("Found families:", len(families))
print("Example family sizes:", {k: len(v) for k, v in list(families.items())[:8]})

# ---- build reduced features (no fragmentation: collect in dict -> DataFrame once)
reduced = {}

def choose_k(Xg, k_min=2, k_max=6, random_state=42):
    """
    Stable-ish k selection: try a few ks, choose max silhouette.
    If too small / degenerate, return 2.
    """
    n = Xg.shape[0]
    p = Xg.shape[1]
    if p < 2 or n < 50:
        return 2

    best_k, best_score = 2, -1.0
    for k in range(k_min, min(k_max, n-1) + 1):
        km = KMeans(n_clusters=k, n_init=20, random_state=random_state)
        labels = km.fit_predict(Xg)
        # guard against single cluster (can happen w/ degenerate data)
        if len(np.unique(labels)) < 2:
            continue
        score = silhouette_score(Xg, labels)
        if score > best_score:
            best_score, best_k = score, k
    return best_k

# optional: choose what identifiers you want to keep
keep_cols = [c for c in [
    "game_id","season","gameday","home_team","away_team",
    "home_score","away_score","result","total","overtime"
] if c in df.columns]

out_df = df[keep_cols].copy()

for fam, cols in sorted(families.items(), key=lambda kv: kv[0]):
    Xg = df[cols].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    Xg = Xg.apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(dtype=np.float32)

    # z-score within family
    Zg = StandardScaler().fit_transform(Xg)

    # mean z-score (1 col)
    out_df[f"mean_z_{fam}"] = Zg.mean(axis=1).astype(np.float32)

    # cluster (1 col) — skip if family too small to be meaningful
    if Zg.shape[1] >= 2:
        k = choose_k(Zg, k_min=2, k_max=6, random_state=42)
        km = KMeans(n_clusters=k, n_init=30, random_state=42)
        out_df[f"cluster_{fam}"] = km.fit_predict(Zg).astype(np.int16)
    else:
        out_df[f"cluster_{fam}"] = np.zeros(Zg.shape[0], dtype=np.int16)

print("Original diff columns:", len(diff_cols))
print("Reduced columns added:",
      len([c for c in out_df.columns if c.startswith("mean_z_")]) +
      len([c for c in out_df.columns if c.startswith("cluster_")]))

print("Final shape:", out_df.shape)

out_df.to_csv(OUT_PATH, index=False)
print("Saved:", OUT_PATH)