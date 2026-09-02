"""Phase I — validate top active-stack ICH put candidate."""
from __future__ import annotations

import io
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
import polars as pl

from experiments.ich_put_recal.phase_g_active_sweep import (
    CACHE_PATH,
    _apply_downstream,
    _apply_hybrid,
    _build_boost_table,
)

RESULTS_PATH = Path(".cache/ich_put_recal/phase_h_active_refine_results.parquet")
YEARS = 1825 / 365.0


def _wr(df, score_col, wr_col, lo=0, hi=25):
    sub = df.filter(pl.col(score_col).is_between(lo, hi) & pl.col(wr_col).is_not_null())
    return (float(sub[wr_col].mean()) * 100, len(sub)) if len(sub) else (None, 0)


def _fmt(v):
    return "--" if v is None else f"{v:.2f}"


def main():
    cache = pl.read_parquet(CACHE_PATH).filter(pl.col("wr7").is_not_null())
    from experiments._holdout import assert_no_holdout_leak

    assert_no_holdout_leak(cache, context="phase_i_active_validate cache")
    res = pl.read_parquet(RESULTS_PATH)
    passed = res.filter(pl.col("w4_pass") & pl.col("w5_pass")).sort("lift_le25", descending=True)
    if len(passed) == 0:
        raise RuntimeError("no W4+W5 passing candidate in phase_h results")
    best = passed.row(0, named=True)
    params = (
        int(best["tail_floor"]),
        int(best["mid_cut"]),
        float(best["mid_scale"]),
        float(best["boundary_k"]),
        float(best["boundary_power"]),
        int(best["boundary_lo"]),
        int(best["boundary_hi"]),
        float(best["sat"]),
        float(best["target"]),
    )
    print("Best active-stack candidate:")
    print(params)

    pre = cache["pre_ich"].to_numpy().astype(np.int16)
    kij = cache["kijun_pct"].fill_null(999.0).to_numpy().astype(float)
    days = cache["days_to_earnings"].fill_null(-1).to_numpy().astype(np.int16)
    final = _apply_downstream(_apply_hybrid(pre, kij, params), days, _build_boost_table())
    work = cache.with_columns(pl.Series("candidate_final", final.astype(np.int16)))

    print()
    print("=" * 100)
    print("W4 DISCRETE BUCKET CHECK (WR7)")
    print("=" * 100)
    print(f"{'bucket':>8} | {'base WR/N':>16} | {'cand WR/N':>16} | {'dWR':>8} | {'dN':>8}")
    print("-" * 100)
    for lo, hi, lbl in [(0, 5, "0-5"), (6, 10, "6-10"), (11, 15, "11-15"), (16, 20, "16-20"), (21, 25, "21-25"), (0, 25, "<=25")]:
        bw, bn = _wr(work, "old_final", "wr7", lo, hi)
        cw, cn = _wr(work, "candidate_final", "wr7", lo, hi)
        dwr = cw - bw if bw is not None and cw is not None else None
        print(
            f"{lbl:>8} | {(_fmt(bw) + '/' + str(bn)):>16} | "
            f"{(_fmt(cw) + '/' + str(cn)):>16} | "
            f"{(f'{dwr:+.2f}' if dwr is not None else '--'):>8} | {cn - bn:+8d}"
        )

    print()
    print("=" * 100)
    print("W2 MULTI-BARRIER-WINDOW CHECK")
    print("=" * 100)
    print(f"{'W':>4} | {'base WR/N':>16} | {'cand WR/N':>16} | {'dWR':>8}")
    print("-" * 100)
    all_positive = True
    for w in (7, 15, 30):
        col = f"wr{w}"
        bw, bn = _wr(work, "old_final", col)
        cw, cn = _wr(work, "candidate_final", col)
        dwr = cw - bw if bw is not None and cw is not None else None
        if dwr is None or dwr <= 0:
            all_positive = False
        print(f"{w:>4} | {(_fmt(bw) + '/' + str(bn)):>16} | {(_fmt(cw) + '/' + str(cn)):>16} | {(f'{dwr:+.2f}' if dwr is not None else '--'):>8}")
    print("W2:", "PASS (WR7/15/30 positive)" if all_positive else "FAIL")

    print()
    print("=" * 100)
    print("W3 MULTI-TIME-WINDOW WR7 CHECK")
    print("=" * 100)
    max_date = cache["date"].max()
    end = np.datetime64(max_date)
    print(f"{'window':>6} | {'base WR/N':>16} | {'cand WR/N':>16} | {'dWR':>8}")
    print("-" * 100)
    for years, label in [(1, "1y"), (3, "3y"), (5, "5y")]:
        # Date strings sort lexicographically as ISO dates.
        cutoff = (np.datetime_as_string(end - np.timedelta64(365 * years, "D"), unit="D"))
        sub = work.filter(pl.col("date") >= cutoff)
        bw, bn = _wr(sub, "old_final", "wr7")
        cw, cn = _wr(sub, "candidate_final", "wr7")
        dwr = cw - bw if bw is not None and cw is not None else None
        print(f"{label:>6} | {(_fmt(bw) + '/' + str(bn)):>16} | {(_fmt(cw) + '/' + str(cn)):>16} | {(f'{dwr:+.2f}' if dwr is not None else '--'):>8}")

    print()
    print("N-flow/year:")
    for lo, hi, name, floor in [(0, 15, "p<=15", 196), (16, 20, "p16-20", 398), (21, 25, "p21-25", 541)]:
        _, n = _wr(work, "candidate_final", "wr7", lo, hi)
        annual = n / YEARS
        print(f"  {name:>7}: {annual:7.1f}/yr floor={floor} {'PASS' if annual >= floor else 'FAIL'}")


if __name__ == "__main__":
    main()
