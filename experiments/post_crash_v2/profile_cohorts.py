"""Profile post-crash cohorts in the put-peak universe.

Reads .cache/post_crash_v2/features_v{N}_1825.parquet
Reports WR15/N/exit-return/MFE/MAE by:
  (1) recent-drop bucket (ret_10d) x score tier (<=25 / <=15 / <=5)
  (2) sigma-expansion bucket x score tier
  (3) gap-down count bucket x score tier
  (4) headline: pct_from_252h bucket x score tier (the canonical "post-crash" axis)

Lift = cohort WR15 - tier baseline WR15  (negative = cohort UNDER-performs baseline)
Z = (cohort_wr - baseline_wr) / sqrt(p*(1-p)*(1/n_cohort + 1/n_other))
Where p is the pooled WR.

Lift > 0 = cohort beats baseline. Lift < 0 = cohort worse than baseline (the cohort
we want to suppress at the score stage).
"""
from __future__ import annotations
import io, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / '.cache' / 'post_crash_v2' / 'features_v36_1825.parquet'

PUT_BE = 36.4  # put break-even (option pnl)
CALL_BE = 46.2


def z_two_proportions(wr_a, n_a, wr_b, n_b):
    """Two-sample proportion z. Returns z stat (positive = a > b)."""
    if n_a == 0 or n_b == 0:
        return 0.0
    p_a, p_b = wr_a / 100.0, wr_b / 100.0
    p_pool = (p_a * n_a + p_b * n_b) / (n_a + n_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    return (p_a - p_b) / se if se > 0 else 0.0


def cohort_table(df, axis_col, axis_buckets, score_tiers, w_col='result_15'):
    """For each (score_tier, axis_bucket) cell, compute WR vs tier baseline.
    score_tier is a (label, score_max) tuple."""
    print('\n' + '=' * 116)
    print(f'AXIS: {axis_col}   (target: {w_col})')
    print('-' * 116)
    print(f'{"score":<7} {"bucket":<22} {"N":>6} {"WR":>7} {"avg_xret":>9} {"avg_mae":>8} {"avg_mfe":>8} {"baseline":>9} {"lift_pp":>8} {"z":>6}')
    print('-' * 116)

    for tier_label, tier_max in score_tiers:
        sub = df.filter(pl.col('overall') <= tier_max)
        sub = sub.filter(pl.col(w_col).is_not_null())
        if len(sub) == 0:
            continue
        # tier baseline
        n_base = len(sub)
        wr_base = sub[w_col].sum() / n_base * 100
        print(f'{tier_label:<7} {"BASELINE (all)":<22} {n_base:>6} {wr_base:>6.1f}%')

        for bkt_label, predicate in axis_buckets:
            cell = sub.filter(predicate)
            n_cell = len(cell)
            if n_cell < 10:
                continue
            wr_cell = cell[w_col].sum() / n_cell * 100
            n_other = n_base - n_cell
            wr_other = (sub[w_col].sum() - cell[w_col].sum()) / n_other * 100 if n_other > 0 else 0
            lift = wr_cell - wr_other
            z = z_two_proportions(wr_cell, n_cell, wr_other, n_other)
            avg_xret = cell['exit_return_15'].mean()
            avg_mae = cell['mae_pct_15'].mean()
            avg_mfe = cell['mfe_pct_15'].mean()
            mark = ''
            if abs(z) >= 3.0: mark = ' ***'
            elif abs(z) >= 2.0: mark = ' **'
            elif abs(z) >= 1.5: mark = ' *'
            xret_s = f'{avg_xret:+.3f}' if avg_xret is not None else '   --'
            mae_s = f'{avg_mae:+.3f}' if avg_mae is not None else '   --'
            mfe_s = f'{avg_mfe:+.3f}' if avg_mfe is not None else '   --'
            print(f'{tier_label:<7} {bkt_label:<22} {n_cell:>6} {wr_cell:>6.1f}% {xret_s:>9} {mae_s:>8} {mfe_s:>8} {wr_other:>8.1f}% {lift:>+7.2f} {z:>+6.2f}{mark}')


def main():
    df = pl.read_parquet(PATH)
    print(f'[profile] loaded {len(df):,} put-peak rows from {PATH.name}')

    # Drop rows with no result_15
    n0 = len(df)
    df = df.filter(pl.col('result_15').is_not_null())
    print(f'[profile] result_15 not null: {len(df):,} (-{n0-len(df)} dropped)')

    score_tiers = [
        ('<=25', 25),
        ('<=15', 15),
        ('<=5', 5),
    ]

    # --- AXIS 1: 10-day return (the "recent crash" signal) ---
    ret10d_buckets = [
        ('ret10d >= 0  (rising)',       pl.col('ret_10d') >= 0),
        ('ret10d -5..0',                (pl.col('ret_10d') < 0) & (pl.col('ret_10d') >= -0.05)),
        ('ret10d -10..-5',              (pl.col('ret_10d') < -0.05) & (pl.col('ret_10d') >= -0.10)),
        ('ret10d -15..-10',             (pl.col('ret_10d') < -0.10) & (pl.col('ret_10d') >= -0.15)),
        ('ret10d -25..-15',             (pl.col('ret_10d') < -0.15) & (pl.col('ret_10d') >= -0.25)),
        ('ret10d < -25  (post-crash)',  pl.col('ret_10d') < -0.25),
    ]
    cohort_table(df, 'ret_10d', ret10d_buckets, score_tiers)

    # --- AXIS 2: 5-day return (sharper recent move) ---
    ret5d_buckets = [
        ('ret5d >= 0',                  pl.col('ret_5d') >= 0),
        ('ret5d -5..0',                 (pl.col('ret_5d') < 0) & (pl.col('ret_5d') >= -0.05)),
        ('ret5d -10..-5',               (pl.col('ret_5d') < -0.05) & (pl.col('ret_5d') >= -0.10)),
        ('ret5d -15..-10',              (pl.col('ret_5d') < -0.10) & (pl.col('ret_5d') >= -0.15)),
        ('ret5d < -15 (sharp drop)',    pl.col('ret_5d') < -0.15),
    ]
    cohort_table(df, 'ret_5d', ret5d_buckets, score_tiers)

    # --- AXIS 3: pct_from_252h (broader "fell off cliff") ---
    f252h_buckets = [
        ('within 5% of 252h',           pl.col('pct_from_252h') >= -5.0),
        ('-15..-5  (mild pullback)',    (pl.col('pct_from_252h') < -5.0) & (pl.col('pct_from_252h') >= -15.0)),
        ('-30..-15 (drawdown)',         (pl.col('pct_from_252h') < -15.0) & (pl.col('pct_from_252h') >= -30.0)),
        ('-50..-30 (deep drawdown)',    (pl.col('pct_from_252h') < -30.0) & (pl.col('pct_from_252h') >= -50.0)),
        ('< -50 (post-crash)',          pl.col('pct_from_252h') < -50.0),
    ]
    cohort_table(df, 'pct_from_252h', f252h_buckets, score_tiers)

    # --- AXIS 4: sigma expansion ---
    sigma_buckets = [
        ('sigma_exp <= 1.0 (calm)',      pl.col('sigma_expansion') <= 1.0),
        ('sigma_exp 1.0..1.5',           (pl.col('sigma_expansion') > 1.0) & (pl.col('sigma_expansion') <= 1.5)),
        ('sigma_exp 1.5..2.5',           (pl.col('sigma_expansion') > 1.5) & (pl.col('sigma_expansion') <= 2.5)),
        ('sigma_exp > 2.5 (expanding)',  pl.col('sigma_expansion') > 2.5),
    ]
    cohort_table(df, 'sigma_expansion', sigma_buckets, score_tiers)

    # --- AXIS 5: gap-down count last 5 days ---
    gap_buckets = [
        ('gaps_5d == 0',                 pl.col('gap_down_5d_count') == 0),
        ('gaps_5d == 1',                 pl.col('gap_down_5d_count') == 1),
        ('gaps_5d >= 2',                 pl.col('gap_down_5d_count') >= 2),
    ]
    cohort_table(df, 'gap_down_5d_count', gap_buckets, score_tiers)

    # --- AXIS 6: combined "post-crash" definition (ret_5d < -10% AND sigma_exp > 1.5) ---
    print('\n' + '=' * 116)
    print('COMBINED POST-CRASH DEFINITION: ret_5d < -10% AND sigma_expansion > 1.5')
    print('-' * 116)
    combined = [
        ('NOT post-crash',                (~((pl.col('ret_5d') < -0.10) & (pl.col('sigma_expansion') > 1.5)))),
        ('POST-CRASH (sharp+expansion)',  ((pl.col('ret_5d') < -0.10) & (pl.col('sigma_expansion') > 1.5))),
    ]
    cohort_table(df, 'combined', combined, score_tiers)


if __name__ == '__main__':
    main()
