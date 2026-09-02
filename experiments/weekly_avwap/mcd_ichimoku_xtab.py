"""Cross-tab v43 MCD dampener vs Ichimoku state — are they orthogonal?

Two mechanisms targeting (potentially overlapping) "bad-quality call" cohorts:
  - MCD (v43): fires when small-cap (mcd_mcap_b low) on a call signal
  - Ichimoku (Phase E Rank #3 candidate): fires when bearish weekly Ichimoku
    state (kijun_pct < 0) on score ≥ 75

Cross-tab questions:
  Q1: What fraction of MCD-fired peaks are bearish-Ichimoku?
  Q2: What fraction of bearish-Ichimoku 75+ peaks had MCD fire?
  Q3: WR15 by 4-cell cross-tab (MCD × Ichimoku) — confirm independent alpha

If MCD and Ichimoku are orthogonal:
  - Cohort overlap < 30%
  - Each independently lifts WR15 in its respective sub-cohort
  - Combined dampener captures more alpha than either alone

If they're heavily overlapping:
  - Cohort overlap > 60%
  - Combined dampener provides diminishing returns over either alone
"""
from __future__ import annotations

import io
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.cache' / 'weekly_avwap'
BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'


def main():
    # Load v43 parquet (Ichimoku features)
    df = pl.read_parquet(CACHE / 'calls_v43_1825d_min0.parquet')
    df = df.with_columns(
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct')
    )
    df = df.filter(pl.col('price_vs_kijun_pct').is_not_null()
                   & pl.col('price_vs_kijun_pct').is_finite())
    print(f'[load] v43 parquet: {len(df):,} rows', flush=True)

    # Load v43 wadj cache (has mcd_val column)
    wadj_df = pl.read_parquet(CACHE / 'wadj_v43_1825d_min0.parquet')
    df = df.join(wadj_df, on=['symbol', 'date'], how='left')

    # Filter to call cohorts where Ichimoku would be relevant: 75+ peaks (Phase E gate)
    calls_75p = df.filter(pl.col('overall') >= 75)
    print(f'[filter] 75+ calls: {len(calls_75p):,}', flush=True)

    # Define cohorts
    calls_75p = calls_75p.with_columns([
        # MCD fired: mcd_val (which holds mcd_dampen) is non-null and > 0
        (pl.col('mcd_val').is_not_null() & (pl.col('mcd_val') > 0)).alias('mcd_fired'),
        # Bearish Ichimoku: price below kijun-sen
        (pl.col('price_vs_kijun_pct') < 0).alias('bearish_ichimoku'),
    ])

    # Q1: Among 75+ peaks where MCD fired, what fraction is bearish-Ichimoku?
    print('\n' + '=' * 80)
    print('Q1: MCD-fired 75+ peaks — what fraction are bearish-Ichimoku?')
    print('=' * 80)
    mcd_fired_75p = calls_75p.filter(pl.col('mcd_fired'))
    n_mcd_fired = len(mcd_fired_75p)
    if n_mcd_fired > 0:
        n_mcd_bear = mcd_fired_75p.filter(pl.col('bearish_ichimoku')).height
        pct = n_mcd_bear / n_mcd_fired * 100
        print(f'  Total MCD-fired 75+ peaks:          {n_mcd_fired:>8,}')
        print(f'  ...of which bearish-Ichimoku:       {n_mcd_bear:>8,}  ({pct:.1f}%)')
        print(f'  ...of which bullish-Ichimoku:       {n_mcd_fired - n_mcd_bear:>8,}  '
              f'({(n_mcd_fired-n_mcd_bear)/n_mcd_fired*100:.1f}%)')
    else:
        print('  No MCD fires among 75+ peaks (mcd_dampen mostly fires below 75 to push out)')

    # Q2: Among 75+ bearish-Ichimoku peaks, what fraction had MCD fire?
    print('\n' + '=' * 80)
    print('Q2: Bearish-Ichimoku 75+ peaks — what fraction had MCD fire?')
    print('=' * 80)
    bear_75p = calls_75p.filter(pl.col('bearish_ichimoku'))
    n_bear = len(bear_75p)
    n_bear_mcd = bear_75p.filter(pl.col('mcd_fired')).height
    if n_bear > 0:
        pct = n_bear_mcd / n_bear * 100
        print(f'  Total bearish-Ichimoku 75+ peaks:  {n_bear:>8,}')
        print(f'  ...of which MCD also fired:        {n_bear_mcd:>8,}  ({pct:.1f}%)')
        print(f'  ...of which MCD did NOT fire:      {n_bear - n_bear_mcd:>8,}  '
              f'({(n_bear-n_bear_mcd)/n_bear*100:.1f}%)')

    # Q3: 4-cell cross-tab WR15 (MCD × Ichimoku)
    print('\n' + '=' * 80)
    print('Q3: 4-cell cross-tab WR15 (75+ peaks)')
    print('=' * 80)
    print('\n  Each cell: WR15 (N) — option-aligned barriers (30dte_opt @ w=15)\n')
    print(f'  {"":<25s} {"MCD fired":<22s} {"MCD did NOT fire":<22s}')
    print(f'  {"":<25s} {"-" * 18:<22s} {"-" * 18:<22s}')

    cells = {}
    for ich_label, ich_mask in [
        ('bearish-Ichimoku',  pl.col('bearish_ichimoku') == True),
        ('bullish-Ichimoku',  pl.col('bearish_ichimoku') == False),
    ]:
        row_str = f'  {ich_label:<25s}'
        for mcd_label, mcd_mask in [
            ('MCD fired',     pl.col('mcd_fired') == True),
            ('MCD not fired', pl.col('mcd_fired') == False),
        ]:
            sub = calls_75p.filter(ich_mask & mcd_mask & pl.col('opt_result_15').is_not_null())
            n = len(sub)
            wr = float(sub['opt_result_15'].mean()) * 100 if n > 0 else float('nan')
            cells[(ich_label, mcd_label)] = (wr, n)
            row_str += f' {wr:6.2f}% (N={n:>5,})    '
        print(row_str)

    # Marginal effects
    print('\n  Marginal effects:')
    bear_no_mcd = cells[('bearish-Ichimoku',  'MCD not fired')]
    bull_no_mcd = cells[('bullish-Ichimoku',  'MCD not fired')]
    bear_mcd    = cells[('bearish-Ichimoku',  'MCD fired')]
    bull_mcd    = cells[('bullish-Ichimoku',  'MCD fired')]

    if bear_no_mcd[1] >= 30 and bull_no_mcd[1] >= 30:
        ich_alone = bear_no_mcd[0] - bull_no_mcd[0]
        print(f'    Ichimoku marginal (within "MCD did not fire"):  '
              f'bearish ({bear_no_mcd[0]:.2f}%) − bullish ({bull_no_mcd[0]:.2f}%) = '
              f'{ich_alone:+.2f}pp')
    if bear_mcd[1] >= 30 and bear_no_mcd[1] >= 30:
        mcd_alone = bear_mcd[0] - bear_no_mcd[0]
        print(f'    MCD marginal (within "bearish-Ichimoku"):       '
              f'fired ({bear_mcd[0]:.2f}%) − not fired ({bear_no_mcd[0]:.2f}%) = '
              f'{mcd_alone:+.2f}pp')
    if bull_mcd[1] >= 30 and bull_no_mcd[1] >= 30:
        mcd_alone_bull = bull_mcd[0] - bull_no_mcd[0]
        print(f'    MCD marginal (within "bullish-Ichimoku"):       '
              f'fired ({bull_mcd[0]:.2f}%) − not fired ({bull_no_mcd[0]:.2f}%) = '
              f'{mcd_alone_bull:+.2f}pp')

    # Q4: Same analysis but on the ~all 75+ universe (independent of MCD)
    print('\n' + '=' * 80)
    print('Q4: same cross-tab broken down by tier (95+, 85+, 80+)')
    print('=' * 80)
    for tier_label, tier_mask in [
        ('95+', pl.col('overall') >= 95),
        ('85+', pl.col('overall') >= 85),
        ('80+', pl.col('overall') >= 80),
        ('75+', pl.col('overall') >= 75),
    ]:
        sub = calls_75p.filter(tier_mask)
        if len(sub) < 30:
            continue
        bear = sub.filter(pl.col('bearish_ichimoku') & pl.col('opt_result_15').is_not_null())
        bull = sub.filter(~pl.col('bearish_ichimoku') & pl.col('opt_result_15').is_not_null())
        bear_wr = float(bear['opt_result_15'].mean()) * 100 if len(bear) else float('nan')
        bull_wr = float(bull['opt_result_15'].mean()) * 100 if len(bull) else float('nan')
        spread = bull_wr - bear_wr
        print(f'  {tier_label:<5s}  N={len(sub):>5,}  '
              f'bearish-Ich={bear_wr:5.2f}% (N={len(bear):>4,})  '
              f'bullish-Ich={bull_wr:5.2f}% (N={len(bull):>4,})  '
              f'spread={spread:+5.2f}pp')

    # Q5: How much MCD displacement happened "below 75" — i.e., MCD lifted peaks OUT of 75+
    print('\n' + '=' * 80)
    print('Q5: MCD activity in the 70-74 zone (peaks pulled OUT of 75+ by MCD)')
    print('=' * 80)
    near_zone = df.filter((pl.col('overall') >= 70) & (pl.col('overall') < 75))
    near_zone = near_zone.with_columns(
        (pl.col('mcd_val').is_not_null() & (pl.col('mcd_val') > 0)).alias('mcd_fired')
    )
    n_near = len(near_zone)
    n_near_mcd = near_zone.filter(pl.col('mcd_fired')).height
    print(f'  Total 70-74 peaks:                 {n_near:>8,}')
    print(f'  ...with MCD fired (lifted from 75+): {n_near_mcd:>8,}  ({n_near_mcd/n_near*100:.1f}%)')

    # Verdict
    print('\n' + '=' * 80)
    print('VERDICT')
    print('=' * 80)
    if n_mcd_fired > 0 and n_bear > 0:
        overlap_pct = n_bear_mcd / n_bear * 100
        print(f'\n  Cohort overlap: {overlap_pct:.1f}% of bearish-Ichimoku 75+ peaks had MCD fire')
        if overlap_pct < 30:
            print('  ✓ ORTHOGONAL — MCD and Ichimoku target largely different cohorts')
        elif overlap_pct < 60:
            print('  ~ PARTIAL OVERLAP — meaningful but not redundant')
        else:
            print('  ✗ HEAVY OVERLAP — mechanisms target same cohort')


if __name__ == '__main__':
    main()
