"""Multi-window validation of ext-negative floor lift.

Splits the 5y dataset into 1y, 3y, 5y windows and compares per-window
WR/Ret deltas vs baseline. H5 ship gate requires direction consistency.

Tests both:
  - WIDE variants (catch wider population, larger headline signal)
  - NARROW precision variants (catch only cliff cohort, smaller but cleaner)
"""
from __future__ import annotations
import sys, time

from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.post_crash_floor.sweep_ext_neg_floor import (
    load_data, apply_ext_neg_floor, aggregate, BUY_THRESHOLDS, SELL_THRESHOLDS
)


def main():
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    print(f'[mw] active version: id={av.id}', flush=True)

    scores, barriers = load_data(av.id)
    today = date.today()

    windows = [
        ('1y', (today - timedelta(days=365)).isoformat()),
        ('3y', (today - timedelta(days=1095)).isoformat()),
        ('5y', (today - timedelta(days=1825)).isoformat()),
    ]

    # Candidate variants — span narrow precision to wide aggressive
    variants = [
        ('baseline',          0.0,  -25.0, 25.0, 25, 35),
        # Narrow — pure cliff (cliff cohort = ext < -40%)
        ('NARROW_G40_K1_T50', 1.0,  -40.0, 10.0, 25, 50),
        # Mid — gate at -30%, extends through cliff
        ('MID_G30_K1_T50',    1.0,  -30.0, 20.0, 25, 50),
        # Wide — captures moderate extension too
        ('WIDE_G15_K07_T35',  0.7,  -15.0, 25.0, 25, 35),
        ('COMBO_WIDE_AGGR',   1.0,  -15.0, 35.0, 25, 50),
        # Very narrow - only bottom-tier puts (<=15) at deep ext
        ('TIGHT_DEEP_PUTS',   1.0,  -30.0, 20.0, 15, 50),
    ]

    print('\n' + '=' * 130)
    print(f'{"variant":<22} {"window":>7} {"<25 WR15":>14} {"<25 Ret30":>16} {"<15 WR15":>14} {"<15 Ret30":>16} {"<25 N":>10} {"ΔN%":>7}')
    print('-' * 130)

    for label, k, eg, er, sg, lt in variants:
        for win_label, cutoff in windows:
            sub_scores = scores.filter(pl.col('date') >= cutoff)

            if k == 0.0:
                base = sub_scores.with_columns(pl.col('overall').alias('new_overall'))
                stats = aggregate(base, barriers)
                # Stash as baseline for this window
                if label == 'baseline':
                    if not hasattr(main, '_baselines'):
                        main._baselines = {}
                    main._baselines[win_label] = stats
            else:
                scored = apply_ext_neg_floor(sub_scores, k, eg, er, sg, lt)
                stats = aggregate(scored, barriers)

            base_stats = main._baselines[win_label]

            def delta(stats, base, bucket, key):
                v = stats.get(bucket, {}).get(key)
                b = base.get(bucket, {}).get(key)
                if v is None or b is None:
                    return v, None
                return v, v - b

            wr25, dwr25 = delta(stats, base_stats, '<25', 'wr_15d')
            ret25, dret25 = delta(stats, base_stats, '<25', 'ret_30d')
            wr15, dwr15 = delta(stats, base_stats, '<15', 'wr_15d')
            ret15, dret15 = delta(stats, base_stats, '<15', 'ret_30d')
            n25 = stats.get('<25', {}).get('n', 0) or 0
            base_n25 = base_stats.get('<25', {}).get('n', 0) or 1
            n_pct = (n25 - base_n25) / base_n25 * 100

            def fmt(v, d, suf='%', fmt='5.1f'):
                if v is None:
                    return '   --   '
                ds = f'({d:+.2f})' if d is not None else ''
                return f'{v:{fmt}}{suf} {ds}'

            print(f'{label:<22} {win_label:>7} {fmt(wr25, dwr25):>14} {fmt(ret25, dret25, suf="", fmt="6.3f"):>16} {fmt(wr15, dwr15):>14} {fmt(ret15, dret15, suf="", fmt="6.3f"):>16} {n25:>10} {n_pct:>+5.1f}%')
        print('-' * 130)

    # Concentration check — how many puts fall in the actual EFOR-equivalent
    # zone (overall <=15 AND ext < -30) over each window?
    print('\n=== EFOR-zone concentration (overall <= 15 AND ext < -30) ===')
    for win_label, cutoff in windows:
        sub = scores.filter(
            (pl.col('date') >= cutoff)
            & (pl.col('overall') <= 15)
            & (pl.col('ext') < -30)
        )
        n_puts_zone = len(sub)
        # Total puts in that window
        sub_total = scores.filter(
            (pl.col('date') >= cutoff)
            & (pl.col('overall') <= 25)
        )
        n_total = len(sub_total)
        # And barriers result for the EFOR zone
        z = sub.with_columns(pl.lit('high').alias('side')).join(
            barriers.filter(pl.col('w_days') == 15),
            on=['symbol', 'date', 'side'], how='inner'
        )
        wr15_z = (z['result'].sum() / len(z) * 100) if len(z) else None
        ret30 = sub.with_columns(pl.lit('high').alias('side')).join(
            barriers.filter(pl.col('w_days') == 30),
            on=['symbol', 'date', 'side'], how='inner'
        )['exit_return'].mean()
        wr_str = f'{wr15_z:.1f}%' if wr15_z is not None else '--'
        ret_str = f'{ret30:+.3f}' if ret30 is not None else '--'
        pct = (n_puts_zone / n_total * 100) if n_total else 0
        print(f'  {win_label}: zone N={n_puts_zone} ({pct:.1f}% of all <=25 puts={n_total}); WR15={wr_str}, Ret30={ret_str}')


if __name__ == '__main__':
    main()
