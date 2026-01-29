import os
import re
import numpy as np
import pandas as pd


def split_record(series: pd.Series, prefix: str) -> pd.DataFrame:
    """
    Parses 'W-L' or 'W-L-T' into numeric columns:
    {prefix}_wins, {prefix}_losses, {prefix}_ties, {prefix}_win_pct
    """
    s = series.fillna("0-0").astype(str).str.strip()

    def parse_one(x: str):
        nums = re.findall(r"\d+", x)
        if len(nums) == 2:
            w, l = map(int, nums)
            t = 0
        elif len(nums) == 3:
            w, l, t = map(int, nums)
        else:
            w = l = t = 0
        return w, l, t

    parsed = np.array([parse_one(x) for x in s], dtype=np.int32)
    out = pd.DataFrame(parsed, columns=[f"{prefix}_wins", f"{prefix}_losses", f"{prefix}_ties"])

    games = out.sum(axis=1).replace(0, 1)  # avoid divide by zero
    out[f"{prefix}_win_pct"] = (out[f"{prefix}_wins"] + 0.5 * out[f"{prefix}_ties"]) / games
    return out


def make_prefix_diffs_fast(
    df: pd.DataFrame,
    home_prefix: str,
    away_prefix: str,
    exclude_suffixes: set[str] | None = None,
) -> pd.DataFrame:
    """
    For any columns that exist as both {home_prefix}{suffix} and {away_prefix}{suffix},
    creates diff_{suffix} = home - away, drops the original pair, and concatenates once.
    """
    exclude_suffixes = exclude_suffixes or set()

    home_cols = [c for c in df.columns if c.startswith(home_prefix)]
    away_cols = [c for c in df.columns if c.startswith(away_prefix)]

    home_suffix = {c[len(home_prefix):]: c for c in home_cols}
    away_suffix = {c[len(away_prefix):]: c for c in away_cols}

    shared = sorted(set(home_suffix).intersection(away_suffix) - set(exclude_suffixes))
    if not shared:
        return df.copy()

    diff_dict = {}
    drop_cols = []
    for suf in shared:
        hcol = home_suffix[suf]
        acol = away_suffix[suf]

        # numeric coercion
        h = pd.to_numeric(df[hcol], errors="coerce")
        a = pd.to_numeric(df[acol], errors="coerce")
        diff_dict[f"diff_{suf}"] = (h - a).astype("float32")

        drop_cols.extend([hcol, acol])

    diffs = pd.DataFrame(diff_dict, index=df.index)
    out = pd.concat([df.drop(columns=drop_cols, errors="ignore"), diffs], axis=1)
    return out.copy()  # defragment


def main():
    in_path = "nfl/modeling/data/compiled_data.csv"
    out_path = "nfl/modeling/data/condensed_compiled_data.csv"

    df = pd.read_csv(in_path)

    # --- records -> numeric
    df = pd.concat(
        [
            df,
            split_record(df.get("home_record", pd.Series(index=df.index)), "home"),
            split_record(df.get("away_record", pd.Series(index=df.index)), "away"),
        ],
        axis=1,
    )
    df = df.drop(columns=["home_record", "away_record"], errors="ignore")

    # --- 1) collapse ALL home_team_avg_* vs away_team_avg_* into diff_*
    df = make_prefix_diffs_fast(
        df,
        home_prefix="home_team_avg_",
        away_prefix="away_team_avg_",
    )

    # --- 2) also collapse other home_* vs away_* pairs (rest, wins/losses/ties/win_pct, etc.)
    # Exclude things you do NOT want to turn into diffs here (identifiers/outcomes).
    exclude = {
        "team", "score", "record",  # "team" doesn't exist as home_team/away_team suffix anyway, but safe
    }
    df = make_prefix_diffs_fast(
        df,
        home_prefix="home_",
        away_prefix="away_",
        exclude_suffixes=exclude,
    )

    # --- Optional cleanup: ensure numeric for all diff_ columns
    diff_cols = [c for c in df.columns if c.startswith("diff_")]
    df[diff_cols] = df[diff_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    df[diff_cols] = df[diff_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_csv(out_path, index=False)

    print("Wrote:", out_path)
    print("Shape:", df.shape)
    print("Num diff features:", len(diff_cols))


if __name__ == "__main__":
    main()