"""Sanity-check DISC_10 (cutoff at -10%) against the option-aligned barrier (30dte_opt, K=1.274sigma).

The fast_variant_runner mixes all 3 barrier sets in its cache. If DISC_10 is winning
purely on the generic-barrier (K=1.0sigma), but actually HURTS at the option barrier
(K=1.274sigma which is what the strategy actually fires on), it's overfitting.

Conversely, if DISC_10 still wins at the option barrier — the broader cutoff is justified.
"""
from __future__ import annotations
import io, sys, sqlite3, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.fast_variant_runner import FastVariantRunner

CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
RET10D_PATH = ROOT / '.cache' / 'post_crash_v2' / 'ret10d_all_1825.parquet'

WINDOWS = [('1y', 365), ('3y', 1095), ('5y', 1825)]


def evaluate(scores_df, opt_b_df, ret_df, cutoff):
    """Apply discrete cutoff to put scores; aggregate WR15 against 30dte_opt barrier only."""
    df = scores_df.join(ret_df, on=['symbol', 'date'], how='left')
    if cutoff is None:
        df = df.with_columns(pl.col('overall').cast(pl.Int64).alias('new_overall'))
    else:
        df = df.with_columns(
            pl.when((pl.col('overall') <= 25) & (pl.col('ret_10d') <= cutoff) & pl.col('ret_10d').is_not_null())
              .then(30)
              .otherwise(pl.col('overall'))
              .cast(pl.Int64)
              .alias('new_overall')
        )

    # Filter to peaks
    df_peaks = df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
    df_peaks = df_peaks.with_columns(
        pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('side')
    )
    # Inner join to opt barrier (which is high-side w_days=15 only)
    joined = df_peaks.filter(pl.col('side') == 'high').join(opt_b_df, on=['symbol', 'date'], how='inner')

    # Cumulative put buckets
    out = {}
    for thr, label in [(5, '<5'), (15, '<15'), (25, '<25')]:
        cell = joined.filter(pl.col('new_overall') <= thr)
        n = len(cell)
        wins = cell['result'].sum() if n else 0
        out[label] = (wins / n * 100 if n else None, n)
    return out


def main():
    print('[verify] loading runner ...', flush=True)
    runner = FastVariantRunner()
    runner.load(verbose=False)
    ret_df = pl.read_parquet(RET10D_PATH)

    # Load 30dte_opt barriers, w=15d only, side=high (puts) — fresh from SQLite
    print('[verify] loading 30dte_opt barriers (w=15, side=high) ...', flush=True)
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
    print(f'  loaded {len(opt_b):,} rows ({time.time()-t0:.1f}s)', flush=True)

    today = date.today()
    subs = {}
    for w_label, days in WINDOWS:
        cd = (today - timedelta(days=days)).isoformat()
        subs[w_label] = (
            runner.df.filter(pl.col('date') >= cd),
            opt_b.filter(pl.col('date') >= cd),
        )

    cutoffs_to_test = [None, -0.05, -0.08, -0.10, -0.12, -0.13, -0.15, -0.17, -0.20]

    print('\n' + '=' * 130)
    print('OPTION-ALIGNED BARRIER (30dte_opt, K=1.274sigma at W=15d) — verification of fine-grid finding')
    print('=' * 130)
    print(f"{'cutoff':<12} {'tier':<5}  {'1y':<25} {'3y':<25} {'5y':<25}  H5")
    print('-' * 130)

    # Compute baseline first
    base_per_w = {}
    for w_label, (df_w, b_w) in subs.items():
        base_per_w[w_label] = evaluate(df_w, b_w, ret_df, None)

    for c in cutoffs_to_test:
        if c is None: c_label = 'baseline'
        else: c_label = f'<= {c:+.2f}'
        for tier in ['<5', '<15', '<25']:
            cells = []
            sign = []
            for w_label, (df_w, b_w) in subs.items():
                if c is None:
                    cur = base_per_w[w_label]
                else:
                    cur = evaluate(df_w, b_w, ret_df, c)
                cur_wr, cur_n = cur.get(tier, (None, 0))
                base_wr, base_n = base_per_w[w_label].get(tier, (None, 0))
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
                all_pos = all(d >= -0.10 for d in sign)
                h5 = '✓' if all_pos and sum(sign) > 0 else ('✗' if any(d < -0.5 for d in sign) else '~')
            print(f'{c_label:<12} {tier:<5}  {cells[0]:<25} {cells[1]:<25} {cells[2]:<25}  {h5}')
        if c is not None:
            print()


if __name__ == '__main__':
    main()
