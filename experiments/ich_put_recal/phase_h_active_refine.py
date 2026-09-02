"""Phase H — narrow active-stack refinement around the Phase G passing basin."""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
import polars as pl

from experiments.ich_put_recal.phase_g_active_sweep import (
    CACHE_PATH,
    RESULTS_PATH,
    _apply_downstream,
    _apply_hybrid,
    _build_boost_table,
    _lhs,
    _metrics,
)

OUT_PATH = Path(".cache/ich_put_recal/phase_h_active_refine_results.parquet")


def main():
    df = pl.read_parquet(CACHE_PATH)
    from experiments._holdout import assert_no_holdout_leak

    assert_no_holdout_leak(df, context="phase_h_active_refine cache")
    df = df.filter(pl.col("wr7").is_not_null())
    pre = df["pre_ich"].to_numpy().astype(np.int16)
    kij = df["kijun_pct"].fill_null(999.0).to_numpy().astype(float)
    days = df["days_to_earnings"].fill_null(-1).to_numpy().astype(np.int16)
    old = df["old_final"].to_numpy().astype(np.int16)
    wr7 = df["wr7"].to_numpy().astype(float)
    boost_table = _build_boost_table()

    baseline = _metrics(old, wr7, {
        "wr_le25": 0, "wr_le5": 0, "wr_le10": 0,
        "wr_le15": 0, "wr_p16_20": 0, "wr_p21_25": 0,
    })
    print(f"[cache] loaded {len(df):,} rows")
    print(f"[baseline] <=25 WR7={baseline['wr_le25']:.2f}% N={baseline['n_le25']:,}")

    # Around Phase G sole passing basin:
    # tail=5, mid=18, mid_scale=.93, bK=.15, bP=.73, bLo=18, bHi=26, sat=13.9, tgt=35.1
    ranges = [
        (4, 8),        # tail_floor
        (16, 20),      # mid_cut
        (0.75, 1.15),  # mid_scale
        (0.05, 0.30),  # boundary_k
        (0.35, 1.35),  # boundary_power
        (16, 20),      # boundary_lo
        (24, 28),      # boundary_hi
        (10.0, 18.0),  # sat
        (32.0, 40.0),  # target
    ]
    raw = _lhs(1200, ranges, seed=33)
    variants = []
    for row in raw:
        tail_floor = int(round(row[0]))
        mid_cut = int(round(row[1]))
        boundary_lo = int(round(row[5]))
        boundary_hi = int(round(row[6]))
        if boundary_hi <= boundary_lo + 1 or mid_cut >= boundary_hi:
            continue
        variants.append((
            tail_floor, mid_cut, float(row[2]), float(row[3]), float(row[4]),
            boundary_lo, boundary_hi, float(row[7]), float(row[8])
        ))

    rows = []
    t0 = time.time()
    for i, params in enumerate(variants, 1):
        final = _apply_downstream(_apply_hybrid(pre, kij, params), days, boost_table)
        m = _metrics(final, wr7, baseline)
        if m is None:
            continue
        m.update({
            "tail_floor": params[0], "mid_cut": params[1],
            "mid_scale": params[2], "boundary_k": params[3],
            "boundary_power": params[4], "boundary_lo": params[5],
            "boundary_hi": params[6], "sat": params[7], "target": params[8],
        })
        rows.append(m)
        if i % 200 == 0:
            print(f"  variant {i}/{len(variants)} ({time.time() - t0:.1f}s)", flush=True)

    res = pl.DataFrame(rows)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    res.write_parquet(OUT_PATH)
    passed = res.filter(pl.col("w4_pass") & pl.col("w5_pass"))
    print(f"\n[cache] wrote {OUT_PATH}: {len(res):,} variants")
    print(f"W4+W5 pass: {len(passed)} / {len(res)}")

    top = (passed if len(passed) else res).sort("lift_le25", descending=True).head(20)
    print("\nTop refined candidates:")
    print(
        f"{'lift':>7} {'wr25':>7} {'n25':>6} {'wr15':>7} {'wr16-20':>8} "
        f"{'wr21-25':>8} {'tail':>4} {'mid':>4} {'mS':>5} {'bK':>5} {'bP':>5} {'bLo':>4} {'bHi':>4} {'sat':>5} {'tgt':>5}"
    )
    for r in top.iter_rows(named=True):
        print(
            f"{r['lift_le25']:+7.2f} {r['wr_le25']:7.2f} {r['n_le25']:6d} "
            f"{r['wr_le15']:7.2f} {r['wr_p16_20']:8.2f} {r['wr_p21_25']:8.2f} "
            f"{r['tail_floor']:4d} {r['mid_cut']:4d} {r['mid_scale']:5.2f} "
            f"{r['boundary_k']:5.2f} {r['boundary_power']:5.2f} "
            f"{r['boundary_lo']:4d} {r['boundary_hi']:4d} {r['sat']:5.1f} {r['target']:5.1f}"
        )


if __name__ == "__main__":
    main()
