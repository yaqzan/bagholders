"""Sigma-vs-raw verification at the option-aligned barrier (30dte_opt, K=1.274sigma).

Since FastVariantRunner mixes barrier sets, the relative ranking between SIG and
RAW could be artifact-driven. The option barrier (30dte_opt) is the truly
meaningful one — that's what the strategy actually fires on.
"""
from __future__ import annotations
import io, sys, sqlite3, math, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.fast_variant_runner import FastVariantRunner

CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
RET_SIGMA_PATH = ROOT / '.cache' / 'post_crash_v2' / 'ret_sigma_all_1825.parquet'

WINDOWS = [('1y', 365), ('3y', 1095), ('5y', 1825)]


def evaluate_disc(scores_df, opt_b_df, ret_df, feat_col, cutoff):
    df = scores_df.join(ret_df, on=['symbol', 'date'], how='left')
    if cutoff is None:
        df = df.with_columns(pl.col('overall').cast(pl.Int64).alias('new_overall'))
    else:
        df = df.with_columns(
            pl.when((pl.col('overall') <= 25) & (pl.col(feat_col) <= cutoff) & pl.col(feat_col).is_not_null())
              .then(30).otherwise(pl.col('overall'))
              .cast(pl.Int64).alias('new_overall')
        )

    df_peaks = df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
    df_peaks = df_peaks.with_columns(
        pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('side')
    )
    joined = df_peaks.filter(pl.col('side') == 'high').join(opt_b_df, on=['symbol', 'date'], how='inner')

    out = {}
    for thr, label in [(5, '<5'), (15, '<15'), (25, '<25')]:
        cell = joined.filter(pl.col('new_overall') <= thr)
        n = len(cell)
        wins = cell['result'].sum() if n else 0
        out[label] = (wins / n * 100 if n else None, n)
    return out


def main():
    print('[verify-sigma] loading runner ...', flush=True)
    runner = FastVariantRunner()
    runner.load(verbose=False)
    ret_df = pl.read_parquet(RET_SIGMA_PATH)

    print('[verify-sigma] loading 30dte_opt barriers (w=15, side=high) ...', flush=True)
    cutoff = (date.today() - timedelta(days=1825)).isoformat()
    t0 = time.time()
    conn = sqlite3.connect(str(CACHE_DB))
    opt_b = pl.read_database(
        f"""SELECT symbol, date, result FROM barrier_outcomes
            WHERE side='high' AND barrier_set='30dte_opt' AND w_days=15
              AND date >= '{cutoff}'""",
        connection=conn,
    )
    conn.close()
    print(f'  {len(opt_b):,} rows ({time.time()-t0:.1f}s)', flush=True)

    today = date.today()
    subs = {}
    for w_label, days in WINDOWS:
        cd = (today - timedelta(days=days)).isoformat()
        subs[w_label] = (
            runner.df.filter(pl.col('date') >= cd),
            opt_b.filter(pl.col('date') >= cd),
        )

    # Variants to verify
    candidates = [
        ('baseline',      None,        None),
        ('SIG_DISC_100',  'ret_10d_sigma', -1.0),
        ('SIG_DISC_125',  'ret_10d_sigma', -1.25),
        ('SIG_DISC_150',  'ret_10d_sigma', -1.5),
        ('RAW_DISC_10',   'ret_10d',    -0.10),
        ('RAW_DISC_15',   'ret_10d',    -0.15),
    ]

    base_per_w = {}
    for w_label, (sd, bd) in subs.items():
        base_per_w[w_label] = evaluate_disc(sd, bd, ret_df, 'ret_10d', None)

    print('\n' + '=' * 130)
    print('OPTION-ALIGNED BARRIER (30dte_opt, K=1.274sigma at W=15) — sigma vs raw direct comparison')
    print('=' * 130)
    print(f"{'variant':<14} {'tier':<5}  {'1y':<25} {'3y':<25} {'5y':<25}  {'5y N':>7}  H5")
    print('-' * 130)

    for name, feat, c in candidates:
        for tier in ['<5', '<15', '<25']:
            cells = []
            sign = []
            n5y = None
            for w_label, (sd, bd) in subs.items():
                if c is None:
                    cur = base_per_w[w_label]
                else:
                    cur = evaluate_disc(sd, bd, ret_df, feat, c)
                cur_wr, cur_n = cur.get(tier, (None, 0))
                base_wr, base_n = base_per_w[w_label].get(tier, (None, 0))
                if w_label == '5y':
                    n5y = cur_n
                if cur_wr is None or base_wr is None:
                    cells.append('--')
                    continue
                if c is None:
                    cells.append(f'{cur_wr:5.1f}% (N={cur_n})')
                else:
                    d = cur_wr - base_wr
                    cells.append(f'{cur_wr:5.1f}% d{d:+5.2f} (N={cur_n})')
                    sign.append(d)
            if c is None:
                h5 = '-'
            else:
                h5 = '✓' if sign and all(d > -0.10 for d in sign) else '✗'
            print(f'{name:<14} {tier:<5}  {cells[0]:<25} {cells[1]:<25} {cells[2]:<25}  {n5y or 0:>7}  {h5}')
        if c is not None:
            print()


if __name__ == '__main__':
    main()
