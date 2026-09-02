"""Profile put-signal WR by EMA50 extension bucket.

Hypothesis: puts with extreme negative extension (pct_from_ema50 < -30%) are
post-crash oversold setups, not continuing breakdowns. Should underperform
puts at moderate negative extension if mean-reversion dominates.

Reads v_active scores + indicators + price_history + barrier_outcomes cache.
Pure read — no DB writes, no scoring engine invocation.
"""
from __future__ import annotations
import io, sys, sqlite3, json, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'

LOOKBACK_DAYS = 1825  # 5y


def main():
    from database.models.core import AlgorithmVersion
    from database.trader_database import DB

    av = AlgorithmVersion.get_active_scores_version()
    print(f'[profile] Active version: id={av.id}', flush=True)

    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    # ── Load put scores ──
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall, s.weight_info
        FROM scores s
        WHERE s.version_id = {av.id}
          AND s.date >= '{cutoff}'
          AND s.overall IS NOT NULL
          AND s.overall <= 25
    """)
    rows = cur.fetchall()

    syms, dates, overalls, wadjs = [], [], [], []
    for sym, d, ov, winfo in rows:
        wadj = 0.0
        if winfo:
            try:
                wi = json.loads(winfo) if isinstance(winfo, str) else winfo
                wadj = float(wi.get('w_adj', 0.0) or 0.0)
            except Exception:
                pass
        syms.append(sym)
        dates.append(d.isoformat() if hasattr(d, 'isoformat') else d)
        overalls.append(int(ov))
        wadjs.append(wadj)
    scores = pl.DataFrame({'symbol': syms, 'date': dates, 'overall': overalls, 'wadj': wadjs})
    print(f'[profile] Loaded {len(scores):,} put scores in {time.time()-t0:.1f}s', flush=True)

    # ── Load indicators (ema_50) and prices (close) ──
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT i.symbol, i.date, i.ema_50, p.close
        FROM indicators i
        JOIN price_history p ON p.symbol = i.symbol AND p.date = i.date
        WHERE i.date >= '{cutoff}'
          AND i.ema_50 IS NOT NULL AND i.ema_50 > 0
    """)
    rows = cur.fetchall()
    isyms, idates, exts = [], [], []
    for sym, d, ema, close in rows:
        if ema is None or float(ema) == 0:
            continue
        isyms.append(sym)
        idates.append(d.isoformat() if hasattr(d, 'isoformat') else d)
        exts.append(float((float(close) - float(ema)) / float(ema) * 100.0))
    ind = pl.DataFrame({'symbol': isyms, 'date': idates, 'ext': exts})
    print(f'[profile] Loaded {len(ind):,} ind+price rows in {time.time()-t0:.1f}s', flush=True)

    # ── Load barrier outcomes (put side) ──
    t0 = time.time()
    conn = sqlite3.connect(str(CACHE_DB))
    barriers = pl.read_database(
        f"""SELECT symbol, date, w_days, result, exit_return, mae_pct, mfe_pct
            FROM barrier_outcomes
            WHERE side = 'high' AND date >= '{cutoff}' AND barrier_set = '30dte_generic'""",
        connection=conn,
    )
    conn.close()
    print(f'[profile] Loaded {len(barriers):,} barrier rows in {time.time()-t0:.1f}s', flush=True)

    # ── Join scores + ind + barriers ──
    df = scores.join(ind, on=['symbol', 'date'], how='inner')
    print(f'[profile] After ind join: {len(df):,}', flush=True)

    # ── Bucket by extension ──
    df = df.with_columns(
        pl.when(pl.col('ext') >= 0).then(pl.lit('above_ema50'))
          .when(pl.col('ext') >= -10).then(pl.lit('-0_to_-10'))
          .when(pl.col('ext') >= -20).then(pl.lit('-10_to_-20'))
          .when(pl.col('ext') >= -30).then(pl.lit('-20_to_-30'))
          .when(pl.col('ext') >= -40).then(pl.lit('-30_to_-40'))
          .when(pl.col('ext') >= -50).then(pl.lit('-40_to_-50'))
          .otherwise(pl.lit('below_-50'))
          .alias('ext_bucket')
    )

    # Score sub-bucket (overall ≤ 5 / ≤ 15 / ≤ 25)
    df = df.with_columns(
        pl.when(pl.col('overall') <= 5).then(pl.lit('le5'))
          .when(pl.col('overall') <= 15).then(pl.lit('le15'))
          .otherwise(pl.lit('le25'))
          .alias('score_bucket')
    )

    # Join barriers across all w_days, then aggregate WR per (ext_bucket, w_days)
    full = df.join(barriers, on=['symbol', 'date'], how='inner')
    print(f'[profile] After barrier join: {len(full):,}', flush=True)

    # Headline: WR by extension bucket × w_days × score sub-bucket
    print('\n' + '=' * 110)
    print(f'{"score":>7} {"ext bucket":>14} {"N":>7} {"avg_ext":>10} {"WR7":>8} {"WR15":>8} {"WR30":>8} {"avg_ret30":>10} {"avg_mfe30":>10}')
    print('-' * 110)

    score_buckets = ['le25', 'le15', 'le5']
    ext_order = ['above_ema50', '-0_to_-10', '-10_to_-20', '-20_to_-30',
                 '-30_to_-40', '-40_to_-50', 'below_-50']

    for sb in score_buckets:
        for eb in ext_order:
            cell = full.filter((pl.col('score_bucket') == sb) & (pl.col('ext_bucket') == eb))
            if len(cell) == 0:
                continue

            def wr_at(w):
                sub = cell.filter((pl.col('w_days') == w) & pl.col('result').is_not_null())
                if len(sub) == 0:
                    return None, 0
                r = sub['result'].sum()
                n = len(sub)
                return (r / n * 100.0) if n else None, n

            wr7, n7 = wr_at(7)
            wr15, n15 = wr_at(15)
            wr30, n30 = wr_at(30)

            sub30 = cell.filter((pl.col('w_days') == 30))
            avg_ret30 = sub30['exit_return'].mean() if len(sub30) else None
            avg_mfe30 = sub30['mfe_pct'].mean() if len(sub30) else None
            avg_ext = cell['ext'].mean()

            n_disp = n30 if n30 else (n15 if n15 else n7)
            wr7_s = f'{wr7:5.1f}%' if wr7 is not None else '   --  '
            wr15_s = f'{wr15:5.1f}%' if wr15 is not None else '   --  '
            wr30_s = f'{wr30:5.1f}%' if wr30 is not None else '   --  '
            ret_s = f'{avg_ret30:+.3f}' if avg_ret30 is not None else '   --  '
            mfe_s = f'{avg_mfe30:.3f}' if avg_mfe30 is not None else '   --  '

            print(f'{sb:>7} {eb:>14} {n_disp:>7} {avg_ext:>10.1f} {wr7_s:>8} {wr15_s:>8} {wr30_s:>8} {ret_s:>10} {mfe_s:>10}')
        print('-' * 110)

    # Section: side-by-side WR15 across ext (cumulative ≤25)
    print('\nWR15 across extension buckets, cumulative <=25 puts:')
    cell = full.filter(pl.col('w_days') == 15)
    cum = cell.group_by('ext_bucket').agg([
        pl.count().alias('N'),
        pl.col('result').sum().alias('wins'),
        pl.col('ext').mean().alias('avg_ext'),
    ]).sort('avg_ext', descending=True)
    for r in cum.iter_rows(named=True):
        wr = (r['wins'] / r['N'] * 100) if r['N'] else 0
        print(f"  {r['ext_bucket']:>14}: N={r['N']:>6}, avg_ext={r['avg_ext']:+.1f}%, WR15={wr:.1f}%")


if __name__ == '__main__':
    main()
