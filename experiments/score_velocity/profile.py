"""Profile call peaks (70+) by score velocity on v39 5y baseline.

Hypothesis: among signals at score=78, those that came from {65, 72, 78}
carry higher per-trade WR than those from {85, 81, 78}.

If accelerating cohort has +3pp option-WR15 over decelerating at multi-tier,
design CT-style cascade promotion or score-stage micro-boost.
"""
from __future__ import annotations
import io, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / '.cache' / 'score_velocity' / 'calls_v39_1825.parquet'


def z_two_proportions(wr_a, n_a, wr_b, n_b):
    if n_a == 0 or n_b == 0:
        return 0.0
    p_a, p_b = wr_a / 100.0, wr_b / 100.0
    p_pool = (p_a * n_a + p_b * n_b) / (n_a + n_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    return (p_a - p_b) / se if se > 0 else 0.0


def cohort_table(df, axis_col, axis_buckets, score_tiers):
    print('\n' + '=' * 130)
    print(f'AXIS: {axis_col}   (target: opt_result_15 = option-aligned barrier WR15)')
    print('-' * 130)
    print(f'{"score tier":<12} {"bucket":<26} {"N":>6} {"opt_WR15":>9} {"avg_xret":>10} {"baseline":>10} {"lift":>8} {"z":>6}')
    print('-' * 130)

    for tier_label, tier_min in score_tiers:
        sub = df.filter(pl.col('overall') >= tier_min)
        sub = sub.filter(pl.col('opt_result_15').is_not_null())
        if len(sub) == 0:
            continue
        n_base = len(sub)
        wr_base = (sub.filter(pl.col('opt_result_15') == 1).height / n_base) * 100
        ret_base = float(sub['opt_exit_return_15'].mean()) if n_base > 0 else None
        rs = f'{ret_base:+.4f}' if ret_base is not None else '   --'
        print(f'{tier_label:<12} {"BASELINE (all)":<26} {n_base:>6} {wr_base:>8.1f}% {rs:>10}')

        for bkt_label, predicate in axis_buckets:
            cell = sub.filter(predicate)
            n_cell = len(cell)
            if n_cell < 30:
                continue
            wr_cell = (cell.filter(pl.col('opt_result_15') == 1).height / n_cell) * 100
            ret_cell = float(cell['opt_exit_return_15'].mean()) if n_cell > 0 else None
            n_other = n_base - n_cell
            wins_other = sub.filter(pl.col('opt_result_15') == 1).height - cell.filter(pl.col('opt_result_15') == 1).height
            wr_other = (wins_other / n_other * 100) if n_other > 0 else 0
            lift = wr_cell - wr_other
            z = z_two_proportions(wr_cell, n_cell, wr_other, n_other)
            mark = ''
            if abs(z) >= 3.0: mark = ' ***'
            elif abs(z) >= 2.0: mark = ' **'
            elif abs(z) >= 1.5: mark = ' *'
            rs = f'{ret_cell:+.4f}' if ret_cell is not None else '   --'
            print(f'{tier_label:<12} {bkt_label:<26} {n_cell:>6} {wr_cell:>8.1f}% {rs:>10} {wr_other:>8.1f}%  {lift:>+6.2f}pp  {z:>+5.2f}{mark}')


def main():
    df = pl.read_parquet(PATH)
    print(f'[profile] loaded {len(df):,} call peaks (70+)')
    df_v5 = df.filter(pl.col('velocity_5d').is_not_null())
    df_v3 = df.filter(pl.col('velocity_3d').is_not_null())
    print(f'[profile] velocity_5d non-null: {len(df_v5):,}')
    print(f'[profile] velocity_5d range: [{df_v5["velocity_5d"].min()}, {df_v5["velocity_5d"].max()}]')
    print(f'[profile] velocity_5d quintiles:')
    for q in [0.20, 0.40, 0.60, 0.80]:
        print(f'   q{int(q*100):02d}: {df_v5["velocity_5d"].quantile(q)}')

    score_tiers = [
        ('70+',  70),
        ('75+',  75),
        ('80+',  80),
        ('85+',  85),
        ('90+',  90),
    ]

    # AXIS 1: velocity_5d quintiles
    q20 = df_v5['velocity_5d'].quantile(0.20)
    q40 = df_v5['velocity_5d'].quantile(0.40)
    q60 = df_v5['velocity_5d'].quantile(0.60)
    q80 = df_v5['velocity_5d'].quantile(0.80)
    v5_buckets = [
        (f'Q1 vel<={q20} (deceleration)',  pl.col('velocity_5d') <= q20),
        (f'Q2 {q20}<vel<={q40}',           (pl.col('velocity_5d') > q20) & (pl.col('velocity_5d') <= q40)),
        (f'Q3 {q40}<vel<={q60}',           (pl.col('velocity_5d') > q40) & (pl.col('velocity_5d') <= q60)),
        (f'Q4 {q60}<vel<={q80}',           (pl.col('velocity_5d') > q60) & (pl.col('velocity_5d') <= q80)),
        (f'Q5 vel>{q80} (acceleration)',   pl.col('velocity_5d') > q80),
    ]
    cohort_table(df_v5, 'velocity_5d (quintile)', v5_buckets, score_tiers)

    # AXIS 2: discrete velocity_5d buckets (more interpretable)
    discrete_buckets = [
        ('vel ≤ -10 (heavy decel)',  pl.col('velocity_5d') <= -10),
        ('-10 < vel ≤ -5',           (pl.col('velocity_5d') > -10) & (pl.col('velocity_5d') <= -5)),
        ('-5 < vel ≤ 0',             (pl.col('velocity_5d') > -5) & (pl.col('velocity_5d') <= 0)),
        ('0 < vel ≤ +5',             (pl.col('velocity_5d') > 0) & (pl.col('velocity_5d') <= 5)),
        ('+5 < vel ≤ +10',           (pl.col('velocity_5d') > 5) & (pl.col('velocity_5d') <= 10)),
        ('vel > +10 (heavy accel)',  pl.col('velocity_5d') > 10),
    ]
    cohort_table(df_v5, 'velocity_5d (discrete buckets)', discrete_buckets, score_tiers)

    # AXIS 3: velocity_3d (shorter window)
    discrete_buckets_3d = [
        ('vel3d ≤ -8',           pl.col('velocity_3d') <= -8),
        ('-8 < vel3d ≤ -3',      (pl.col('velocity_3d') > -8) & (pl.col('velocity_3d') <= -3)),
        ('-3 < vel3d ≤ +3',      (pl.col('velocity_3d') > -3) & (pl.col('velocity_3d') <= 3)),
        ('+3 < vel3d ≤ +8',      (pl.col('velocity_3d') > 3) & (pl.col('velocity_3d') <= 8)),
        ('vel3d > +8',           pl.col('velocity_3d') > 8),
    ]
    cohort_table(df_v3, 'velocity_3d (discrete)', discrete_buckets_3d, score_tiers)


if __name__ == '__main__':
    main()
