"""Supplementary pass: add a strict dd>=0.40 'acute stress' threshold on top of
the already-computed panel.parquet (no DB re-pull -- book_dd_pct at 0.10/0.15
turned out to capture 78-86% of all days, since Core's equity curve spends
most of its time well off a rolling peak; a genuinely strict tail cut is
needed to isolate real stress episodes like 2022 bear / COVID trough).
Merges into the existing results.json under by_dd_threshold['0.4'].
"""
import json
from pathlib import Path

import numpy as np
import polars as pl

OUT_DIR = Path(__file__).resolve().parent
N_SKIP_FLOOR = 30
N_BOOTSTRAP = 4000
RNG_SEED = 20260713

df = pl.read_parquet(OUT_DIR / "panel.parquet").sort("date")
sleeve_ret = df["sleeve_return"].to_numpy()
book_pnl = df["book_pnl_pct"].to_numpy()
book_dd = df["book_dd_pct"].to_numpy()
dates_arr = df["date"].to_numpy()
month_blocks = dates_arr.astype("datetime64[M]")
sleeve_loss = sleeve_ret < 0
N_ALL = len(df)


def clustered_bootstrap_corr(x, y, blocks, n_boot=N_BOOTSTRAP, seed=RNG_SEED):
    rng = np.random.default_rng(seed)
    uniq_blocks = np.unique(blocks)
    if len(uniq_blocks) < 3 or len(x) < N_SKIP_FLOOR:
        return None
    idx_by_block = {b: np.where(blocks == b)[0] for b in uniq_blocks}
    boot_r = np.empty(n_boot)
    nB = len(uniq_blocks)
    for i in range(n_boot):
        chosen = rng.choice(uniq_blocks, size=nB, replace=True)
        idx = np.concatenate([idx_by_block[b] for b in chosen])
        if len(idx) < 2 or np.std(x[idx]) == 0 or np.std(y[idx]) == 0:
            boot_r[i] = np.nan
            continue
        boot_r[i] = np.corrcoef(x[idx], y[idx])[0, 1]
    boot_r = boot_r[~np.isnan(boot_r)]
    if len(boot_r) < n_boot * 0.5:
        return None
    se = float(np.std(boot_r, ddof=1))
    ci_lo, ci_hi = np.percentile(boot_r, [2.5, 97.5])
    return {"se_clustered_month_block": se, "ci95_lo": float(ci_lo), "ci95_hi": float(ci_hi),
            "n_boot_used": int(len(boot_r)), "n_clusters": int(nB)}


def two_prop_ztest(x1, n1, x2, n2):
    if n1 == 0 or n2 == 0:
        return None
    p1, p2 = x1 / n1, x2 / n2
    p_pool = (x1 + x2) / (n1 + n2)
    se = (p_pool * (1 - p_pool) * (1 / n1 + 1 / n2)) ** 0.5
    z = (p1 - p2) / se if se > 0 else float("nan")
    return {"p1": p1, "p2": p2, "diff": p1 - p2, "z": z, "n1": n1, "n2": n2}


for thr in (0.40,):
    dd_active = book_dd >= thr
    n_active, n_inactive = int(dd_active.sum()), int((~dd_active).sum())
    entry = {"threshold": thr, "n_dd_active_days": n_active, "n_not_active_days": n_inactive,
              "note": "strict 'acute stress' tail cut, added after seeing 0.10/0.15 capture 78-86% of all days"}
    print(f"[DD>={thr:.2f}] active={n_active} inactive={n_inactive} "
          f"date_range_active={df.filter(pl.col('book_dd_pct')>=thr)['date'].min()}..{df.filter(pl.col('book_dd_pct')>=thr)['date'].max()}")

    if n_active >= N_SKIP_FLOOR and n_inactive >= N_SKIP_FLOOR:
        loss_active = int(sleeve_loss[dd_active].sum())
        loss_inactive = int(sleeve_loss[~dd_active].sum())
        p_loss_active = loss_active / n_active
        p_loss_inactive = loss_inactive / n_inactive
        p_loss_overall = float(np.mean(sleeve_loss))
        p_dd_active_overall = n_active / N_ALL
        p_joint = loss_active / N_ALL
        lift = p_joint / (p_loss_overall * p_dd_active_overall) if (p_loss_overall * p_dd_active_overall) > 0 else float("nan")
        ztest = two_prop_ztest(loss_active, n_active, loss_inactive, n_inactive)
        entry["conditional_probs"] = {
            "p_sleeve_loss_given_dd_active": p_loss_active,
            "p_sleeve_loss_given_not_active": p_loss_inactive,
            "p_sleeve_loss_unconditional": p_loss_overall,
            "p_dd_active_unconditional": p_dd_active_overall,
            "lift_loss_and_ddactive_vs_independence": lift,
            "two_prop_ztest": ztest,
        }
        print(f"  P(sleeve loss | dd_active)     = {p_loss_active:.4f}")
        print(f"  P(sleeve loss | NOT dd_active)  = {p_loss_inactive:.4f}")
        print(f"  lift                            = {lift:.4f}")
        print(f"  two-prop z                      = {ztest['z']:.4f}")
    else:
        entry["conditional_probs"] = None
        print(f"  SKIP conditional-prob (N<{N_SKIP_FLOOR} in a group)")

    if n_active >= N_SKIP_FLOOR:
        r_active = float(np.corrcoef(sleeve_ret[dd_active], book_pnl[dd_active])[0, 1])
        boot_active = clustered_bootstrap_corr(sleeve_ret[dd_active], book_pnl[dd_active], month_blocks[dd_active])
        entry["corr_dd_active_subset"] = {"N": n_active, "pearson_r": r_active, "clustered_bootstrap": boot_active}
        print(f"  r(sleeve, book_pnl | dd_active) = {r_active:.4f}  N={n_active}")
        if boot_active:
            print(f"    clustered SE={boot_active['se_clustered_month_block']:.4f} "
                  f"95% CI=[{boot_active['ci95_lo']:.4f}, {boot_active['ci95_hi']:.4f}]")
    else:
        entry["corr_dd_active_subset"] = {"N": n_active, "skipped": True}

    with open(OUT_DIR / "results.json") as f:
        results = json.load(f)
    results["by_dd_threshold"][str(thr)] = entry
    with open(OUT_DIR / "results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
print("merged into results.json")
