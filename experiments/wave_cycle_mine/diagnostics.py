"""Wave/Cycle Mine -- report-only diagnostics (PREREGISTRATION.md
"Diagnostics" -- never gated, never feeds legs_passed/verdict). Factored out
of mine.py purely to keep that script within its line budget; these
functions have no dependency on the cohort-scan/gate machinery.
"""
from __future__ import annotations

import math
from collections import Counter

import numpy as np
import polars as pl

import calendar_features
import stats


def diagnostic_supply_vs_baseline(df: pl.DataFrame, cal: "calendar_features.WCalendar",
                                   min_s_bars: int) -> str:
    """Entry-supply by OPEX phase / TOM / DOW vs the trading-day baseline
    share over the substrate's own date span -- does the SCORING SYSTEM
    itself have calendar rhythm, independent of any outcome?"""
    dmin, dmax = df["date"].min(), df["date"].max()
    baseline_days = [d for d in cal.trading_days if dmin <= d <= dmax]
    n_base = len(baseline_days)
    lines = [f"entry-supply vs trading-day baseline ({dmin}..{dmax}, {n_base} trading days):"]

    specs = [
        ("w1_days_to_opex_bucket", "b_W1_days_to_opex",
         lambda d: calendar_features.bucket_w1(cal.days_to_opex(d)), ["0-3", "4-8", "9-13", "ge14"]),
        ("w3_turn_of_month", "b_W3_turn_of_month",
         lambda d: "yes" if cal.turn_of_month(d) else "no", ["yes", "no"]),
        ("w4_dow", "b_W4_dow", lambda d: cal.dow(d), calendar_features.DOW_NAMES),
    ]
    for label, entry_col, getter, values in specs:
        base_counts = Counter(getter(d) for d in baseline_days)
        entry_counts = Counter(df[entry_col].drop_nulls().to_list())
        n_entries = sum(entry_counts.values())
        lines.append(f"  {label}:")
        for v in values:
            bshare = base_counts.get(v, 0) / n_base if n_base else float("nan")
            eshare = entry_counts.get(v, 0) / n_entries if n_entries else float("nan")
            lines.append(f"    {v:<8} baseline={bshare*100:5.1f}%  entries={eshare*100:5.1f}%  "
                         f"(n_entries={entry_counts.get(v, 0)})")
    _ = min_s_bars  # unused here, kept for a uniform diagnostics-module call signature
    return "\n".join(lines)


def _pearson_pairwise(a: np.ndarray, b: np.ndarray):
    m = np.isfinite(a) & np.isfinite(b)
    npair = int(m.sum())
    if npair < 30:
        return float("nan"), npair
    aa, bb = a[m], b[m]
    if aa.std() < 1e-12 or bb.std() < 1e-12:
        return float("nan"), npair
    r = float(np.corrcoef(aa, bb)[0, 1])
    return r, npair


def diagnostic_s_correlation(df: pl.DataFrame) -> str:
    """S1-S4 x [vol20, runup20_sig, pit_mcap_b] + S x S -- the expected
    failure mode is 'S ~= vol/trendiness re-label', which the controlled
    fit (not this diagnostic) is what actually exposes/guards against."""
    s_cols = ["s1_vr5", "s2_perm_entropy", "s3_lz76", "s4_kaufman_er20"]
    ctl_cols = ["vol20", "runup20_sig", "pit_mcap_b"]
    arrs = {c: df[c].to_numpy().astype(float) for c in s_cols + ctl_cols}

    lines = ["S x control correlation (pairwise-complete Pearson r; n= pair count):"]
    lines.append(f"{'':<16}" + "".join(f"{c:>20}" for c in ctl_cols))
    for sc in s_cols:
        cells = []
        for cc in ctl_cols:
            r, npair = _pearson_pairwise(arrs[sc], arrs[cc])
            cells.append(f"{r:+.3f}(n={npair})" if not math.isnan(r) else "n/a")
        lines.append(f"{sc:<16}" + "".join(f"{c:>20}" for c in cells))

    lines.append("")
    lines.append("S x S correlation:")
    lines.append(f"{'':<16}" + "".join(f"{c:>16}" for c in s_cols))
    for sc1 in s_cols:
        cells = []
        for sc2 in s_cols:
            if sc1 == sc2:
                cells.append("1.000")
                continue
            r, _npair = _pearson_pairwise(arrs[sc1], arrs[sc2])
            cells.append(f"{r:.3f}" if not math.isnan(r) else "n/a")
        lines.append(f"{sc1:<16}" + "".join(f"{c:>16}" for c in cells))
    return "\n".join(lines)


def diagnostic_young_listing(df: pl.DataFrame, min_s_bars: int) -> str:
    n_total = df.height
    n_young = df.filter(pl.col("_s_young_listing")).height
    lines = [f"S-family young-listing coverage: {n_young}/{n_total} rows (<{min_s_bars} bars strictly "
             f"before entry) -> null S-features"]
    for c in ("s1_vr5", "s2_perm_entropy", "s3_lz76", "s4_kaufman_er20"):
        n_null = df.filter(pl.col(c).is_null()).height
        extra_null = n_null - n_young
        lines.append(f"  {c}: {n_null}/{n_total} null (young_listing={n_young}, "
                     f"other_insufficient_data={extra_null})")
    return "\n".join(lines)


def diagnostic_per_window_base(df: pl.DataFrame) -> str:
    lines = ["per-window base rates (drift check vs peak_fakeout's 70.1%/23.3%):"]
    y_wr = df["apex_win"].to_numpy().astype(float)
    y_ev = df["apex_ev"].to_numpy()
    window_arr = df["window"].to_numpy()
    for lbl in stats.WINDOW_ORDER:
        m = (window_arr == lbl)
        n = int(m.sum())
        wr = float(np.nanmean(y_wr[m])) if n else float("nan")
        plunge = float(np.mean(y_ev[m] == -0.7)) if n else float("nan")
        lines.append(f"  {lbl:<6} n={n:>5} apex_wr={wr:.4f} plunge={plunge:.4f}")
    return "\n".join(lines)
