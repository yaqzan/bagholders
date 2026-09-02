"""Re-validate the strongest post-crash cohorts at the option-aligned barrier set
(30dte_opt) to confirm the directional finding translates to the actual TP/SL
threshold the live strategy uses.

Generic barrier (K=1.0sigma) measures pure directional accuracy.
Option barrier (K=1.274sigma at W=15d) measures whether the underlying actually
crosses the TP threshold the strategy fires on.

If cohort lift signs match between barrier sets, the finding is robust.
"""
from __future__ import annotations
import io, sys, sqlite3, math, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / '.cache' / 'post_crash_v2' / 'features_v36_1825.parquet'
CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'


def z_two_proportions(wr_a, n_a, wr_b, n_b):
    if n_a == 0 or n_b == 0:
        return 0.0
    p_a, p_b = wr_a / 100.0, wr_b / 100.0
    p_pool = (p_a * n_a + p_b * n_b) / (n_a + n_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    return (p_a - p_b) / se if se > 0 else 0.0


def main():
    df = pl.read_parquet(PATH)
    print(f'[opt-barrier] {len(df):,} put-peak rows', flush=True)

    # Load 30dte_opt barrier outcomes for w=15d only
    cutoff = (date.today() - timedelta(days=1825)).isoformat()
    t0 = time.time()
    conn = sqlite3.connect(str(CACHE_DB))
    opt_b = pl.read_database(
        f"""SELECT symbol, date, result, exit_return, mae_pct, mfe_pct
            FROM barrier_outcomes
            WHERE side='high' AND barrier_set='30dte_opt'
              AND w_days=15 AND date >= '{cutoff}'""",
        connection=conn,
    )
    conn.close()
    print(f'[opt-barrier] loaded {len(opt_b):,} opt-barrier 15d rows ({time.time()-t0:.1f}s)', flush=True)

    df_o = df.select(['symbol', 'date', 'overall', 'ret_5d', 'ret_10d', 'sigma_expansion']).join(
        opt_b.rename({'result': 'res_opt15', 'exit_return': 'xret_opt15',
                      'mae_pct': 'mae_opt15', 'mfe_pct': 'mfe_opt15'}),
        on=['symbol', 'date'], how='inner')
    print(f'[opt-barrier] joined: {len(df_o):,}', flush=True)

    cohorts = [
        ('ret_10d in (-25,-15]', (pl.col('ret_10d') < -0.15) & (pl.col('ret_10d') >= -0.25)),
        ('ret_10d < -15 (broad)', pl.col('ret_10d') < -0.15),
        ('ret_10d < -25 (extreme)', pl.col('ret_10d') < -0.25),
        ('ret_5d < -10 + sigexp>1.5 (combined)', (pl.col('ret_5d') < -0.10) & (pl.col('sigma_expansion') > 1.5)),
        ('sigma_expansion > 2.5', pl.col('sigma_expansion') > 2.5),
    ]
    tiers = [('<=25', 25), ('<=15', 15)]

    print('\nGENERIC BARRIER (K=1.0sigma) vs OPTION BARRIER (K=1.274sigma) at W=15d')
    print('=' * 124)
    print(f'{"cohort":<40} {"tier":<5} {"N":>6} {"Wgen":>7} {"Wopt":>7} '
          f'{"baseGen":>8} {"baseOpt":>8} {"liftGen":>8} {"liftOpt":>8} {"zGen":>6} {"zOpt":>6}')
    print('-' * 124)

    for name, pred in cohorts:
        for tier_label, tier_max in tiers:
            tier_df = df.filter((pl.col('overall') <= tier_max) & pl.col('result_15').is_not_null())
            tier_df_o = df_o.filter((pl.col('overall') <= tier_max) & pl.col('res_opt15').is_not_null())
            cohort = tier_df.filter(pred)
            cohort_o = tier_df_o.filter(pred)
            if len(cohort) < 30 or len(cohort_o) < 30:
                continue
            other = tier_df.filter(~pred)
            other_o = tier_df_o.filter(~pred)
            n_c, n_o = len(cohort), len(other)
            n_c_o, n_o_o = len(cohort_o), len(other_o)
            wr_gen = cohort['result_15'].sum() / n_c * 100
            wr_opt = cohort_o['res_opt15'].sum() / n_c_o * 100
            base_gen = other['result_15'].sum() / n_o * 100
            base_opt = other_o['res_opt15'].sum() / n_o_o * 100
            lift_gen = wr_gen - base_gen
            lift_opt = wr_opt - base_opt
            z_gen = z_two_proportions(wr_gen, n_c, base_gen, n_o)
            z_opt = z_two_proportions(wr_opt, n_c_o, base_opt, n_o_o)
            print(f'{name:<40} {tier_label:<5} {n_c_o:>6} '
                  f'{wr_gen:>6.1f}% {wr_opt:>6.1f}% {base_gen:>7.1f}% {base_opt:>7.1f}% '
                  f'{lift_gen:>+7.2f} {lift_opt:>+7.2f} {z_gen:>+6.2f} {z_opt:>+6.2f}')


if __name__ == '__main__':
    main()
