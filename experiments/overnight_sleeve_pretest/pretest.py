"""P4 gameplan REFINE row: overnight-equity-sleeve pre-test (read-only).

Decisive test (gameplan Sec 4): "do sleeve loss-nights co-cluster with book DD?"

Sleeve  = hypothetical hold-overnight (signal-day close -> next trading day open)
          for every 75+ call signal (v74, active scoring version).
Book DD = Core profile deterministic backtest_cascade equity curve (the documented
          default/live-representative call book), daily drawdown-from-peak.

Holdout: signals and book equity curve both hard-capped to CALIBRATION_CUTOFF_DATE
(2026-06-15) per experiments/_holdout.py. The single-day-forward overnight-return
OUTCOME lookup (next trading day's open) is allowed to resolve up to a few
calendar days past cutoff for signals fired in the last few days of the window --
a fixed, mechanical 1-day-forward resolution of an already-honest pre-cutoff
signal, not a re-optimization -- documented explicitly here and in RESULTS.md.

No writes. No queue. No engine edits.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import date, timedelta

REPO_ROOT = Path(r"C:\Development\Trader")
sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import polars as pl

from experiments._holdout import CUTOFF  # noqa: E402
from algorithm_versions import research_pack as rp  # noqa: E402
from database.models.core import Score  # noqa: E402
from database.models.technical import PriceHistory  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
VERSION_ID = 74
MIN_SCORE = 75
START_DATE = date(2020, 1, 1)  # includes COVID + 2022 bear -> healthy DD-active sample
PRICE_BUFFER_END = CUTOFF + timedelta(days=10)  # slack for next-open lookups near cutoff

DD_THRESHOLD_PRIMARY = 0.10
DD_THRESHOLD_SENSITIVITY = 0.15
N_SKIP_FLOOR = 30
N_BOOTSTRAP = 4000
RNG_SEED = 20260713

rp.ensure_db_open(force_reconnect=True)

print(f"holdout CUTOFF = {CUTOFF.isoformat()}")
print(f"signal window = {START_DATE.isoformat()} .. {CUTOFF.isoformat()} (inclusive)")

# ---------------------------------------------------------------------------
# 1. 75+ call-signal cohort (v74), holdout-capped
# ---------------------------------------------------------------------------
sig_rows = list(
    Score.select(Score.symbol, Score.date, Score.overall)
    .where(
        (Score.version == VERSION_ID)
        & (Score.overall >= MIN_SCORE)
        & (Score.date >= START_DATE)
        & (Score.date <= CUTOFF)
    )
    .dicts()
)
sig_df = pl.DataFrame(sig_rows)
sig_df = sig_df.with_columns(pl.col("date").cast(pl.Date))
# NOTE: experiments._holdout.pre_cutoff_filter compares the date column to
# CUTOFF.isoformat() (a str), which polars 1.40 rejects for a native Date
# column ("cannot compare date to a string value"). Re-asserting the cutoff
# natively here instead (belt-and-suspenders on top of the SQL WHERE clause
# above); the SQL-level filter is the actual holdout guarantee. Flagged as a
# small infra chip -- see RESULTS.md.
assert sig_df.filter(pl.col("date") > CUTOFF).height == 0, "holdout leak in signal query"
n_signals_raw = sig_df.height
symbols = sig_df["symbol"].unique().to_list()
print(f"raw 75+ signals (v74, {START_DATE}..{CUTOFF}): N={n_signals_raw}, distinct symbols={len(symbols)}")

# ---------------------------------------------------------------------------
# 2. PriceHistory for those symbols (buffer past cutoff for next-open lookups)
# ---------------------------------------------------------------------------
CHUNK = 400
ph_rows = []
for i in range(0, len(symbols), CHUNK):
    chunk_syms = symbols[i:i + CHUNK]
    rows = list(
        PriceHistory.select(PriceHistory.symbol, PriceHistory.date, PriceHistory.open, PriceHistory.close)
        .where(
            (PriceHistory.symbol.in_(chunk_syms))
            & (PriceHistory.date >= START_DATE - timedelta(days=5))
            & (PriceHistory.date <= PRICE_BUFFER_END)
        )
        .dicts()
    )
    ph_rows.extend(rows)
ph_df = pl.DataFrame(ph_rows)
ph_df = ph_df.with_columns([
    pl.col("date").cast(pl.Date),
    pl.col("open").cast(pl.Float64),
    pl.col("close").cast(pl.Float64),
])
print(f"PriceHistory rows pulled: {ph_df.height}")

# next trading day's open, per symbol (sorted by date)
ph_df = ph_df.sort(["symbol", "date"])
ph_df = ph_df.with_columns([
    pl.col("date").shift(-1).over("symbol").alias("next_date"),
    pl.col("open").shift(-1).over("symbol").alias("next_open"),
])
ph_df = ph_df.with_columns(
    (pl.col("next_open") / pl.col("close") - 1.0).alias("overnight_return")
)

# ---------------------------------------------------------------------------
# 3. Join signals -> overnight_return outcome
# ---------------------------------------------------------------------------
joined = sig_df.join(
    ph_df.select(["symbol", "date", "next_date", "overnight_return"]),
    on=["symbol", "date"],
    how="left",
)
n_missing_outcome = joined.filter(pl.col("overnight_return").is_null()).height
joined_clean = joined.filter(pl.col("overnight_return").is_not_null())
print(f"signals with resolvable next-open outcome: N={joined_clean.height} "
      f"(dropped {n_missing_outcome} -- delisted/data-hole/last-row-in-series)")

max_next_date = joined_clean["next_date"].max()
print(f"max outcome (next_date) reached: {max_next_date} "
      f"(signal dates hard-capped at {CUTOFF}; outcome may run a few days past cutoff -- documented)")

# ---------------------------------------------------------------------------
# 4. Day-level sleeve panel (equal-weighted across that day's firing signals)
# ---------------------------------------------------------------------------
sleeve_panel = (
    joined_clean.group_by("date")
    .agg([
        pl.col("overnight_return").mean().alias("sleeve_return"),
        pl.col("overnight_return").len().alias("n_signals"),
    ])
    .sort("date")
)
print(f"sleeve day-panel rows (distinct signal-fire dates): {sleeve_panel.height}")

# ---------------------------------------------------------------------------
# 5. Book (Core profile) deterministic equity curve, same holdout-capped window
# ---------------------------------------------------------------------------
from database.models.core import AlgorithmVersion  # noqa: E402
version = AlgorithmVersion.get(AlgorithmVersion.id == VERSION_ID)

import portfolio_profiles  # noqa: E402
import backtest_cascade as cascade_module  # noqa: E402

cfg, profile = portfolio_profiles.apply_profile_to_config({}, "core", root=REPO_ROOT)
print(f"\nrunning Core deterministic backtest_cascade, v{VERSION_ID}, "
      f"{START_DATE} .. {CUTOFF} (this is the book-DD proxy source)...")
result = cascade_module.run_cascade_backtest(
    VERSION_ID,
    min_score=70.0,
    from_date=START_DATE,
    to_date=CUTOFF,
    initial=50_000.0,
    verbose=False,
    cfg=cfg,
)
if not result:
    raise SystemExit("Core backtest returned no result -- aborting pretest")

equity_curve = result.get("equity_curve") or []
print(f"book equity_curve points: {len(equity_curve)}")

book_df = pl.DataFrame(
    {"date": [d for d, _ in equity_curve], "equity": [float(e) for _, e in equity_curve]}
)
book_df = book_df.with_columns(pl.col("date").cast(pl.Date)).sort("date")
book_df = book_df.with_columns([
    pl.col("equity").pct_change().alias("book_pnl_pct"),
    pl.col("equity").cum_max().alias("running_peak"),
])
book_df = book_df.with_columns(
    ((pl.col("running_peak") - pl.col("equity")) / pl.col("running_peak")).alias("book_dd_pct")
)

# ---------------------------------------------------------------------------
# 6. Merge sleeve <-> book on date
# ---------------------------------------------------------------------------
panel = sleeve_panel.join(
    book_df.select(["date", "book_pnl_pct", "book_dd_pct"]),
    on="date",
    how="inner",
).filter(pl.col("book_pnl_pct").is_not_null())
panel = panel.sort("date")
N_ALL = panel.height
print(f"\nmerged sleeve<->book day panel: N={N_ALL}")

panel.write_parquet(OUT_DIR / "panel.parquet")

# ---------------------------------------------------------------------------
# 7. Statistics
# ---------------------------------------------------------------------------
sleeve_ret = panel["sleeve_return"].to_numpy()
book_pnl = panel["book_pnl_pct"].to_numpy()
book_dd = panel["book_dd_pct"].to_numpy()
dates_arr = panel["date"].to_numpy()

sleeve_loss = sleeve_ret < 0


def month_key(d):
    return (d.astype("datetime64[M]"))


month_blocks = month_key(dates_arr)


def pearson(x, y):
    if len(x) < 2:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def clustered_bootstrap_corr(x, y, blocks, n_boot=N_BOOTSTRAP, seed=RNG_SEED):
    """Block bootstrap (cluster = calendar month) SE/CI for Pearson r.
    Resamples whole months with replacement to respect within-month serial
    correlation ('date-clustered' SE) rather than assuming i.i.d. days."""
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
    return {
        "se_clustered_month_block": se,
        "ci95_lo": float(ci_lo),
        "ci95_hi": float(ci_hi),
        "n_boot_used": int(len(boot_r)),
        "n_clusters": int(nB),
    }


def two_prop_ztest(x1, n1, x2, n2):
    """Two-proportion z-test (pooled). Returns (p1, p2, z, diff)."""
    if n1 == 0 or n2 == 0:
        return None
    p1 = x1 / n1
    p2 = x2 / n2
    p_pool = (x1 + x2) / (n1 + n2)
    se = (p_pool * (1 - p_pool) * (1 / n1 + 1 / n2)) ** 0.5
    z = (p1 - p2) / se if se > 0 else float("nan")
    return {"p1": p1, "p2": p2, "diff": p1 - p2, "z": z, "n1": n1, "n2": n2}


results: dict = {
    "meta": {
        "version_id": VERSION_ID,
        "min_score": MIN_SCORE,
        "start_date": START_DATE.isoformat(),
        "cutoff": CUTOFF.isoformat(),
        "book_profile": "core",
        "n_signals_raw": n_signals_raw,
        "n_signals_resolved": int(joined_clean.height),
        "n_signals_dropped_no_outcome": int(n_missing_outcome),
        "sleeve_day_panel_rows": int(sleeve_panel.height),
        "book_equity_curve_points": len(equity_curve),
        "merged_panel_N": int(N_ALL),
        "n_skip_floor": N_SKIP_FLOOR,
        "n_bootstrap": N_BOOTSTRAP,
        "dd_threshold_primary": DD_THRESHOLD_PRIMARY,
        "dd_threshold_sensitivity": DD_THRESHOLD_SENSITIVITY,
    },
    "overall": {},
    "by_dd_threshold": {},
}

# --- overall correlation (all days) ---
r_all = pearson(sleeve_ret, book_pnl)
boot_all = clustered_bootstrap_corr(sleeve_ret, book_pnl, month_blocks)
results["overall"] = {
    "N": int(N_ALL),
    "sleeve_loss_rate": float(np.mean(sleeve_loss)),
    "pearson_r_sleeve_vs_book_pnl": r_all,
    "clustered_bootstrap": boot_all,
}
print(f"\n[ALL DAYS] N={N_ALL}  sleeve_loss_rate={np.mean(sleeve_loss):.4f}  r={r_all:.4f}")
if boot_all:
    print(f"  clustered(month) bootstrap SE={boot_all['se_clustered_month_block']:.4f} "
          f"95% CI=[{boot_all['ci95_lo']:.4f}, {boot_all['ci95_hi']:.4f}] (clusters={boot_all['n_clusters']})")

for thr in (DD_THRESHOLD_PRIMARY, DD_THRESHOLD_SENSITIVITY):
    dd_active = book_dd >= thr
    n_active = int(dd_active.sum())
    n_inactive = int((~dd_active).sum())
    entry: dict = {"threshold": thr, "n_dd_active_days": n_active, "n_not_active_days": n_inactive}

    # conditional loss-rate + lift + two-prop z
    if n_active >= N_SKIP_FLOOR and n_inactive >= N_SKIP_FLOOR:
        loss_active = int(sleeve_loss[dd_active].sum())
        loss_inactive = int(sleeve_loss[~dd_active].sum())
        p_loss_active = loss_active / n_active
        p_loss_inactive = loss_inactive / n_inactive
        p_loss_overall = float(np.mean(sleeve_loss))
        p_dd_active_overall = n_active / N_ALL
        # lift = P(loss & dd_active) / (P(loss) * P(dd_active)) -- 1.0 = independent
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
        print(f"\n[DD>={thr:.2f}] active_days={n_active} inactive_days={n_inactive}")
        print(f"  P(sleeve loss | dd_active)     = {p_loss_active:.4f}")
        print(f"  P(sleeve loss | NOT dd_active)  = {p_loss_inactive:.4f}")
        print(f"  lift (1.0=independent)          = {lift:.4f}")
        print(f"  two-prop z (active vs inactive) = {ztest['z']:.4f}")
    else:
        entry["conditional_probs"] = None
        print(f"\n[DD>={thr:.2f}] SKIP conditional-prob (active={n_active} or inactive={n_inactive} < N={N_SKIP_FLOOR})")

    # correlation within DD-active subset
    if n_active >= N_SKIP_FLOOR:
        r_active = pearson(sleeve_ret[dd_active], book_pnl[dd_active])
        boot_active = clustered_bootstrap_corr(sleeve_ret[dd_active], book_pnl[dd_active], month_blocks[dd_active])
        entry["corr_dd_active_subset"] = {
            "N": n_active,
            "pearson_r": r_active,
            "clustered_bootstrap": boot_active,
        }
        print(f"  r(sleeve, book_pnl | dd_active) = {r_active:.4f}  N={n_active}")
        if boot_active:
            print(f"    clustered SE={boot_active['se_clustered_month_block']:.4f} "
                  f"95% CI=[{boot_active['ci95_lo']:.4f}, {boot_active['ci95_hi']:.4f}]")
    else:
        entry["corr_dd_active_subset"] = {"N": n_active, "skipped": True, "reason": f"N<{N_SKIP_FLOOR}"}
        print(f"  SKIP corr(dd_active subset): N={n_active} < {N_SKIP_FLOOR}")

    results["by_dd_threshold"][str(thr)] = entry

with open(OUT_DIR / "results.json", "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"\nwrote {OUT_DIR / 'results.json'}")
print(f"wrote {OUT_DIR / 'panel.parquet'}")
