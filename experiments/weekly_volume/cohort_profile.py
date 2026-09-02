"""Cohort profile of weekly-volume features against 30dte_opt @ w=15 barrier outcomes.

For each feature in {wv_z12, wv_mom4, wv_cmf4, wv_force1, wv_align, wv_obv_slope8}
and each tier {Call 70+, 75+, 80+, 85+, 90+, 95+; Put <30, <25, <20, <15, <10},
split into quintiles and report per-quintile option WR15 + lift vs cohort mean + z.

Surfaces directional cohort signals worth designing a smooth-gradient dampener
around. Look for monotonic ladders (Q1 < Q5 or Q1 > Q5) with z >= 2 and N >= 50.
"""
from __future__ import annotations
import io, sys, math
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl
import numpy as np

import os
_VID = os.environ.get('WVD_VID', '45')
CALLS_PARQ = ROOT / '.cache' / 'weekly_volume' / f'calls_v{_VID}_1825.parquet'
PUTS_PARQ  = ROOT / '.cache' / 'weekly_volume' / f'puts_v{_VID}_1825.parquet'

FEATURES = ['wv_z12', 'wv_mom4', 'wv_cmf4', 'wv_force1', 'wv_align', 'wv_obv_slope8']
CALL_TIERS = [70, 75, 80, 85, 90, 95]
PUT_TIERS  = [30, 25, 20, 15, 10]


def quintile_table(df: pl.DataFrame, feature: str, side: str) -> list[dict]:
    """Split resolved subset into 5 quantile bins by feature, return per-bin WR + N."""
    sub = df.filter(
        pl.col('opt_result_15').is_not_null() & pl.col(feature).is_not_null()
    )
    if sub.height < 50:
        return []
    vals = sub.get_column(feature).to_numpy()
    results = sub.get_column('opt_result_15').to_numpy()
    edges = np.quantile(vals, [0.20, 0.40, 0.60, 0.80])
    bin_idx = np.digitize(vals, edges)  # 0..4
    cohort_wr = float(results.mean())
    cohort_n = len(results)
    rows = []
    for q in range(5):
        mask = (bin_idx == q)
        n = int(mask.sum())
        if n == 0:
            continue
        wr = float(results[mask].mean())
        # Z-test: WR vs cohort baseline
        p = cohort_wr
        var = p * (1 - p) / n
        z = (wr - cohort_wr) / math.sqrt(var) if var > 0 else 0.0
        v_lo = float(vals[mask].min()) if n > 0 else None
        v_hi = float(vals[mask].max()) if n > 0 else None
        rows.append({
            'q': q + 1,
            'n': n,
            'wr': wr * 100,
            'lift_pp': (wr - cohort_wr) * 100,
            'z': z,
            'val_lo': v_lo,
            'val_hi': v_hi,
        })
    return rows, cohort_wr * 100, cohort_n


def print_tier_table(df: pl.DataFrame, side: str, tiers: list[int]):
    print('=' * 110)
    print(f'  SIDE: {side.upper()}')
    print('=' * 110)
    for tier in tiers:
        if side == 'call':
            sub = df.filter(pl.col('overall') >= tier)
        else:
            sub = df.filter(pl.col('overall') <= tier)
        n_resolved = sub.filter(pl.col('opt_result_15').is_not_null()).height
        if n_resolved < 100:
            continue
        cohort_wr = float(sub.filter(pl.col('opt_result_15').is_not_null())
                          .get_column('opt_result_15').to_numpy().mean()) * 100
        sym = '+' if side == 'call' else '<'
        print(f'\n  Tier {sym}{tier}  N(resolved)={n_resolved:>5d}  WR15(baseline)={cohort_wr:.2f}%')
        print('  ' + '-' * 102)
        for feat in FEATURES:
            try:
                rows, _, _ = quintile_table(sub, feat, side)
                if not rows:
                    continue
            except Exception:
                continue
            # Compute Q5-Q1 spread and worst-z
            spread = rows[-1]['wr'] - rows[0]['wr']
            max_abs_z = max(abs(r['z']) for r in rows)
            max_abs_lift = max(abs(r['lift_pp']) for r in rows)
            tag = ''
            if max_abs_z >= 2.0:
                tag = ' **'
            if max_abs_z >= 3.0:
                tag = ' ***'
            print(f'    {feat:<14s} Q1[{rows[0]["val_lo"]:>+6.2f},{rows[0]["val_hi"]:>+6.2f}]={rows[0]["wr"]:>5.1f}% (n={rows[0]["n"]:>4d}, z={rows[0]["z"]:>+4.1f})  '
                  f'Q3={rows[2]["wr"]:>5.1f}%  '
                  f'Q5[{rows[-1]["val_lo"]:>+6.2f},{rows[-1]["val_hi"]:>+6.2f}]={rows[-1]["wr"]:>5.1f}% (n={rows[-1]["n"]:>4d}, z={rows[-1]["z"]:>+4.1f})  '
                  f'Q5-Q1={spread:>+5.1f}pp{tag}')


def main():
    print('Weekly-Volume Cohort Profile  (v44 baseline, 30dte_opt @ w=15)')
    print(f'  Calls parquet: {CALLS_PARQ}')
    print(f'  Puts  parquet: {PUTS_PARQ}\n')

    calls = pl.read_parquet(CALLS_PARQ)
    puts  = pl.read_parquet(PUTS_PARQ)
    print(f'  loaded:  calls={len(calls):,}  puts={len(puts):,}\n')

    print_tier_table(calls, 'call', CALL_TIERS)
    print('\n')
    print_tier_table(puts,  'put',  PUT_TIERS)

    # Cross-tabulation: do the strongest signals interact?
    print('\n')
    print('=' * 110)
    print('  TOP SIGNALS RANKED BY |z| AT 75+ (call) AND <=25 (put) cohorts')
    print('=' * 110)
    candidates = []
    for side, df, tier in [('call', calls.filter(pl.col('overall') >= 75), 75),
                           ('call', calls.filter(pl.col('overall') >= 85), 85),
                           ('call', calls.filter(pl.col('overall') >= 90), 90),
                           ('put',  puts.filter(pl.col('overall') <= 25), 25),
                           ('put',  puts.filter(pl.col('overall') <= 15), 15)]:
        for feat in FEATURES:
            try:
                rows, base_wr, base_n = quintile_table(df, feat, side)
                if not rows:
                    continue
            except Exception:
                continue
            # Best cohort lift (highest |z|)
            best = max(rows, key=lambda r: abs(r['z']))
            spread = rows[-1]['wr'] - rows[0]['wr']
            candidates.append({
                'side': side, 'tier': tier, 'feat': feat,
                'best_q': best['q'], 'best_z': best['z'],
                'best_lift': best['lift_pp'], 'best_n': best['n'],
                'q1_q5_spread': spread,
                'base_wr': base_wr, 'base_n': base_n,
            })

    candidates.sort(key=lambda c: -abs(c['best_z']))
    print(f'\n  {"side":<5s}{"tier":<6s}{"feat":<16s}{"best_Q":<8s}{"best_z":<8s}{"lift_pp":<10s}{"N":<8s}{"Q5-Q1":<10s}{"base_WR":<10s}')
    print('  ' + '-' * 92)
    for c in candidates[:20]:
        sym = '+' if c['side'] == 'call' else '<='
        print(f'  {c["side"]:<5s}{sym}{c["tier"]:<5d}{c["feat"]:<16s}'
              f'Q{c["best_q"]:<7d}{c["best_z"]:<+8.2f}{c["best_lift"]:<+10.2f}'
              f'{c["best_n"]:<8d}{c["q1_q5_spread"]:<+10.2f}{c["base_wr"]:<10.2f}')

    print('\nLEGEND:')
    print('  z = (cohort_WR - tier_baseline_WR) / sqrt(p*(1-p)/n)')
    print('  ** = |z| >= 2.0   *** = |z| >= 3.0')
    print('  Q5-Q1 = WR spread between top-quintile and bottom-quintile bins')
    print('  Monotonic ladders (Q5 > Q3 > Q1 or reverse) are strongest signal — non-monotonic = noise')


if __name__ == '__main__':
    main()
