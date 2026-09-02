"""
Phase TP3B — Step 1: empirical WR15 calibration per (earnings cohort × score bucket)

The Phase 3A.2 winner (W5_B15_AN) admits pre-earnings 67-69 calls into the 70+
bucket — but those admissions carry HIGHER quality than the 70-74 baseline,
meaning they're "under-bucketed". The right calibration is to boost each
signal proportional to its empirical WR15 lift over the baseline at its
score, so high-conviction pre1 cohort signals land in the bucket whose
baseline WR matches their actual quality.

This script COMPUTES the lift table — what's the WR15 of (cohort × bucket)
vs the bucket's baseline (cohort='none')? Used directly by Phase 3B to
calibrate the boost magnitude.

Run: python experiments/v27_optimization/phase_tp3b_calibration.py
Output: lift table printed; saved to phase_tp3b_lift_table.json
"""
from __future__ import annotations
import io, sys, os, json, time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

from experiments.fast_variant_runner import FastVariantRunner
from experiments.v27_optimization.phase_tp3a_earnings_boost import build_earnings_lookup, compute_days_to_earnings


def cohort_label(d):
    """Map days_to_ern → cohort label. None / >7 = 'none'."""
    if d is None:
        return 'none'
    if d == 0 or d == 1:
        return 'pre1'
    if 2 <= d <= 3:
        return 'pre3'
    if 4 <= d <= 7:
        return 'pre7'
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


def main():
    t0 = time.time()
    runner = FastVariantRunner(version_id=None, lookback_days=1825)
    runner.load(verbose=True)
    print(f"[runner] loaded in {time.time()-t0:.1f}s", flush=True)

    t1 = time.time()
    symbols = runner.df['symbol'].unique().to_list()
    by_sym = build_earnings_lookup(symbols, runner.lookback_days)
    runner.df = compute_days_to_earnings(runner.df, by_sym, max_window=10)
    print(f"[earnings] step took {time.time()-t1:.1f}s", flush=True)

    # Filter to qualifying scores only
    df = runner.df.filter((pl.col('overall') >= 70) | (pl.col('overall') <= 25))
    print(f"[df] qualifying signals: {len(df):,}", flush=True)

    # Add cohort column (mapped from days_to_ern)
    df_pd = df.to_pandas()
    df_pd['cohort'] = df_pd['days_to_ern'].apply(cohort_label)
    df_pd['side'] = df_pd['overall'].apply(lambda x: 'low' if x >= 50 else 'high')
    df_pd['bucket'] = df_pd.apply(
        lambda r: call_bucket(r['overall']) if r['overall'] >= 50 else put_bucket(r['overall']),
        axis=1
    )

    df_pl = pl.from_pandas(df_pd)
    print(f"[df] tagged with cohort/side/bucket", flush=True)

    # Join with barrier outcomes
    barrier = runner.barrier_df.filter(pl.col('w_days') == 15)
    joined = df_pl.join(barrier, on=['symbol', 'date', 'side'], how='inner')
    print(f"[joined] {len(joined):,} rows with barrier outcomes (W=15)", flush=True)

    # Group by (cohort, bucket) and compute WR
    agg = (
        joined
        .filter(pl.col('result').is_not_null())
        .group_by(['side', 'cohort', 'bucket'])
        .agg([
            pl.count().alias('N'),
            pl.col('result').mean().alias('WR15'),
        ])
    )

    # Pivot: rows=bucket, cols=cohort
    print("\n" + "="*120)
    print("EMPIRICAL WR15 BY (COHORT × BUCKET) — 5y, v27 active version")
    print("="*120)

    agg_pd = agg.to_pandas()
    agg_pd['WR15'] = agg_pd['WR15'] * 100  # percent

    BUCKET_ORDER_CALL = ['95+', '90-94', '85-89', '80-84', '75-79', '70-74']
    BUCKET_ORDER_PUT  = ['0-5', '6-10', '11-15', '16-20', '21-25']
    COHORT_ORDER = ['none', 'pre7', 'pre3', 'pre1']

    def pivot_table(side, bucket_order):
        sub = agg_pd[agg_pd['side'] == side]
        print(f"\n{'CALL' if side=='low' else 'PUT'} side (WR15% / N):")
        # Header
        print(f"  {'Bucket':<8} | " + " | ".join(f"{c:>13}" for c in COHORT_ORDER))
        print(f"  {'-'*8} | " + " | ".join('-'*13 for _ in COHORT_ORDER))
        # Rows
        for b in bucket_order:
            row = sub[sub['bucket'] == b]
            cells = []
            for c in COHORT_ORDER:
                cell_data = row[row['cohort'] == c]
                if len(cell_data) == 0:
                    cells.append(f"{'-':>13}")
                else:
                    wr = cell_data.iloc[0]['WR15']
                    n = cell_data.iloc[0]['N']
                    cells.append(f"{wr:5.1f}% (n={n:>4})")
            print(f"  {b:<8} | " + " | ".join(cells))

        # Lift table: WR15 in cohort minus WR15 in 'none'
        print(f"\n{'CALL' if side=='low' else 'PUT'} side — WR15 LIFT vs 'none' baseline (in pp):")
        print(f"  {'Bucket':<8} | " + " | ".join(f"{c:>10}" for c in ['pre7', 'pre3', 'pre1']))
        print(f"  {'-'*8} | " + " | ".join('-'*10 for _ in range(3)))
        lifts = {}
        for b in bucket_order:
            row = sub[sub['bucket'] == b]
            base = row[row['cohort'] == 'none']
            base_wr = base.iloc[0]['WR15'] if len(base) > 0 else None
            cells = [f"{base_wr:5.1f}% base" if base_wr is not None else f"{'no base':>10}"]
            for c in ['pre7', 'pre3', 'pre1']:
                cell_data = row[row['cohort'] == c]
                if len(cell_data) == 0 or base_wr is None:
                    cells.append(f"{'-':>10}")
                    continue
                wr = cell_data.iloc[0]['WR15']
                n = cell_data.iloc[0]['N']
                lift = wr - base_wr
                lifts[(side, c, b)] = (lift, n)
                cells.append(f"{lift:+5.1f} (n={n:>4})")
            print(f"  {b:<8} | " + " | ".join(cells))
        return lifts

    call_lifts = pivot_table('low', BUCKET_ORDER_CALL)
    put_lifts  = pivot_table('high', BUCKET_ORDER_PUT)

    # Save lift table
    out_path = ROOT / 'experiments' / 'v27_optimization' / 'phase_tp3b_lift_table.json'
    serializable = {f"{side}|{cohort}|{bucket}": [round(lift, 2), int(n)]
                    for (side, cohort, bucket), (lift, n) in {**call_lifts, **put_lifts}.items()}
    with open(out_path, 'w') as f:
        json.dump(serializable, f, indent=2)
    print(f"\n[saved] lift table → {out_path}")
    print(f"\n[total] {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
