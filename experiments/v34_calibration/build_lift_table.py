"""Build v34 EARN_BOOST lift table.

Mirrors phase_tp3b_calibration.py (v27 calibration) but:
  - Uses production v34 scores (active version)
  - Uses _load_effective_earnings_dates + _days_to_earn_from_sorted
    (production semantics: strict-future, AMC-shifted, ghost-filtered).
  - Outputs: experiments/v34_calibration/lift_table_v34.json
"""
from __future__ import annotations
import io, sys, os, json, time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

from experiments.fast_variant_runner import FastVariantRunner
from database.models.core import (
    _load_effective_earnings_dates,
    _days_to_earn_from_sorted,
)


def cohort_label(d):
    if d is None: return 'none'
    if d == 1:    return 'pre1'
    if 2 <= d <= 3: return 'pre3'
    if 4 <= d <= 7: return 'pre7'
    return 'none'


def call_bucket(overall):
    if overall >= 95: return '95+'
    if overall >= 90: return '90-94'
    if overall >= 85: return '85-89'
    if overall >= 80: return '80-84'
    if overall >= 75: return '75-79'
    if overall >= 70: return '70-74'
    return None


def put_bucket(overall):
    if overall <= 5:  return '0-5'
    if overall <= 10: return '6-10'
    if overall <= 15: return '11-15'
    if overall <= 20: return '16-20'
    if overall <= 25: return '21-25'
    return None


def attach_days_to_ern(df, max_window=10):
    syms = df['symbol'].to_list()
    dates = df['date'].to_list()
    eff_by_sym = {}
    days = []
    for sym, d in zip(syms, dates):
        if sym not in eff_by_sym:
            eff_by_sym[sym] = _load_effective_earnings_dates(sym)
        eff = eff_by_sym[sym]
        # Same logic as production but with extended window=max_window so we
        # can later study cohorts like pre7 that exceed the production W=5.
        if not eff:
            days.append(None); continue
        from bisect import bisect_right
        di = d if isinstance(d, date) else date.fromisoformat(d)
        idx = bisect_right(eff, di)
        if idx >= len(eff):
            days.append(None); continue
        delta = (eff[idx] - di).days
        days.append(delta if 1 <= delta <= max_window else None)
    return df.with_columns(pl.Series('days_to_ern', days))


def main():
    t0 = time.time()
    runner = FastVariantRunner(version_id=None, lookback_days=1825)
    runner.load(verbose=True)
    print(f"[runner] loaded in {time.time()-t0:.1f}s | active={runner.version_id}", flush=True)

    t1 = time.time()
    df = attach_days_to_ern(runner.df, max_window=10)
    print(f"[earnings] attached in {time.time()-t1:.1f}s", flush=True)

    df = df.filter((pl.col('overall') >= 70) | (pl.col('overall') <= 25))
    print(f"[df] qualifying signals (≥70 or ≤25): {len(df):,}", flush=True)

    df_pd = df.to_pandas()
    df_pd['cohort'] = df_pd['days_to_ern'].apply(cohort_label)
    df_pd['side'] = df_pd['overall'].apply(lambda x: 'low' if x >= 50 else 'high')
    df_pd['bucket'] = df_pd.apply(
        lambda r: call_bucket(r['overall']) if r['overall'] >= 50 else put_bucket(r['overall']),
        axis=1
    )
    df_pl = pl.from_pandas(df_pd)

    barrier = runner.barrier_df.filter(pl.col('w_days') == 15)
    joined = df_pl.join(barrier, on=['symbol', 'date', 'side'], how='inner')
    print(f"[joined] {len(joined):,} rows × W=15 barrier", flush=True)

    agg = (
        joined
        .filter(pl.col('result').is_not_null())
        .group_by(['side', 'cohort', 'bucket'])
        .agg([
            pl.count().alias('N'),
            pl.col('result').mean().alias('WR15'),
        ])
    )

    agg_pd = agg.to_pandas()
    agg_pd['WR15'] = agg_pd['WR15'] * 100

    BUCKET_ORDER_CALL = ['95+', '90-94', '85-89', '80-84', '75-79', '70-74']
    BUCKET_ORDER_PUT  = ['0-5', '6-10', '11-15', '16-20', '21-25']
    COHORT_ORDER = ['none', 'pre7', 'pre3', 'pre1']

    def pivot_table(side, bucket_order):
        sub = agg_pd[agg_pd['side'] == side]
        title = 'CALL' if side == 'low' else 'PUT'
        print(f"\n{title} side (WR15% / N):")
        print(f"  {'Bucket':<8} | " + " | ".join(f"{c:>13}" for c in COHORT_ORDER))
        print(f"  {'-'*8} | " + " | ".join('-'*13 for _ in COHORT_ORDER))
        for b in bucket_order:
            row = sub[sub['bucket'] == b]
            cells = []
            for c in COHORT_ORDER:
                cell = row[row['cohort'] == c]
                if len(cell) == 0:
                    cells.append(f"{'-':>13}")
                else:
                    cells.append(f"{cell.iloc[0]['WR15']:5.1f}% (n={int(cell.iloc[0]['N']):>4})")
            print(f"  {b:<8} | " + " | ".join(cells))

        print(f"\n{title} side — WR15 LIFT vs 'none' baseline (in pp):")
        print(f"  {'Bucket':<8} | base       | " + " | ".join(f"{c:>10}" for c in ['pre7', 'pre3', 'pre1']))
        lifts = {}
        for b in bucket_order:
            row = sub[sub['bucket'] == b]
            base = row[row['cohort'] == 'none']
            base_wr = float(base.iloc[0]['WR15']) if len(base) > 0 else None
            base_n  = int(base.iloc[0]['N']) if len(base) > 0 else 0
            cells = [f"{base_wr:5.1f}% n={base_n}" if base_wr is not None else "no base"]
            for c in ['pre7', 'pre3', 'pre1']:
                cell = row[row['cohort'] == c]
                if len(cell) == 0 or base_wr is None:
                    cells.append(f"{'-':>10}")
                    continue
                wr = float(cell.iloc[0]['WR15']); n = int(cell.iloc[0]['N'])
                lift = wr - base_wr
                lifts[(side, c, b)] = (lift, n)
                cells.append(f"{lift:+5.1f} (n={n:>4})")
            print(f"  {b:<8} | " + " | ".join(cells))
        return lifts

    print("\n" + "="*120)
    print("EMPIRICAL WR15 BY (COHORT × BUCKET) — 5y, v34 active")
    print("="*120)
    call_lifts = pivot_table('low', BUCKET_ORDER_CALL)
    put_lifts  = pivot_table('high', BUCKET_ORDER_PUT)

    out_path = ROOT / 'experiments' / 'v34_calibration' / 'lift_table_v34.json'
    serializable = {
        f"{side}|{cohort}|{bucket}": [round(lift, 2), int(n)]
        for (side, cohort, bucket), (lift, n) in {**call_lifts, **put_lifts}.items()
    }
    with open(out_path, 'w') as f:
        json.dump(serializable, f, indent=2)
    print(f"\n[saved] {out_path}")
    print(f"[total] {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
