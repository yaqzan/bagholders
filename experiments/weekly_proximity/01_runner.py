"""
Weekly Proximity Day-of-Week wadj Dampener — Fast Runner

Tests variants where wadj's contribution to overall is dampened by a
day-of-week coefficient. The hypothesis: wadj computed early in the week
(Mon/Tue) is noisy because the underlying weekly composite is built on
1-2 days of partial-week data; by Fri it's based on a full week.

Variant: 5 dampening coefficients [d_mon, d_tue, d_wed, d_thu, d_fri] ∈ [0, 1]
applied as: new_wadj = wadj × dampener[day_of_week]

Linear-Jacobian approximation for new_overall:
    new_overall = overall + (new_wadj - wadj) × regime_factor
where regime_factor = regime_mult if pre_regime >= 50 else (2 - regime_mult).

Approximation is exact when vol_mult ≈ 1 (the NEUTRAL majority case) and
when no post-regime dampener fires on the affected score. For variant
ranking this is sufficient; final candidate validates via simulator.
"""
from __future__ import annotations
import io
import sys
import json
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

if __name__ == '__main__' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
PARQ_DIR = ROOT / '.cache' / 'weekly_proximity'
PARQ_DIR.mkdir(parents=True, exist_ok=True)

BUY_THRESHOLDS = [95, 90, 85, 80, 75, 70]
SELL_THRESHOLDS = [30, 25, 20, 15, 10, 5]
THRESHOLD_KEYS = [f'{t}+' for t in BUY_THRESHOLDS] + [f'<{t}' for t in SELL_THRESHOLDS]
PERIODS = [('1d', 1), ('7d', 7), ('15d', 15), ('30d', 30),
           ('60d', 60), ('90d', 90), ('150d', 150)]


class WadjDowRunner:
    def __init__(self, version_id=None, lookback_days=1825):
        if version_id is None:
            from database.models.core import AlgorithmVersion
            version_id = AlgorithmVersion.get_active_scores_version().id
        self.version_id = version_id
        self.lookback_days = lookback_days
        self.df = None
        self.barrier_df = None

    def load(self, verbose=True, refresh=False):
        scores_pq = PARQ_DIR / f'scores_v{self.version_id}_{self.lookback_days}.parquet'
        barriers_pq = PARQ_DIR / f'barriers_{self.lookback_days}.parquet'
        cutoff = (date.today() - timedelta(days=self.lookback_days)).isoformat()

        if scores_pq.exists() and not refresh:
            t0 = time.time()
            self.df = pl.read_parquet(scores_pq)
            if verbose:
                print(f'[runner] loaded scores parquet ({len(self.df):,} rows) in {time.time()-t0:.1f}s', flush=True)
        else:
            from database.trader_database import DB
            if verbose:
                print(f'[runner] building scores parquet for v{self.version_id} (cutoff={cutoff}) ...', flush=True)
            t0 = time.time()
            cur = DB.execute_sql(f"""
                SELECT s.symbol, s.date, s.overall, s.weight_info, s.regime_multiplier
                FROM scores s
                WHERE s.version_id = {self.version_id}
                  AND s.date >= '{cutoff}'
                  AND s.overall IS NOT NULL
            """)
            rows = cur.fetchall()
            if verbose:
                print(f'[runner] fetched {len(rows):,} rows in {time.time()-t0:.1f}s; parsing weight_info...', flush=True)

            symbols, dates, overalls, wadjs = [], [], [], []
            pre_regs, regime_mults, dows = [], [], []
            for sym, d, ov, winfo, regmult in rows:
                if ov is None:
                    continue
                wadj = 0.0
                pre_reg = int(ov)
                if winfo:
                    try:
                        wi = json.loads(winfo) if isinstance(winfo, str) else winfo
                        wadj = float(wi.get('w_adj', 0.0) or 0.0)
                        pre_reg = int(wi.get('pre_regime', ov) or ov)
                    except Exception:
                        pass
                rm = float(regmult) if regmult is not None else 1.0
                d_iso = d.isoformat() if hasattr(d, 'isoformat') else d
                dow = d.weekday() if hasattr(d, 'weekday') else None
                if dow is None:
                    from datetime import datetime as _dt
                    dow = _dt.fromisoformat(d_iso).weekday()
                symbols.append(sym)
                dates.append(d_iso)
                overalls.append(int(ov))
                wadjs.append(wadj)
                pre_regs.append(pre_reg)
                regime_mults.append(rm)
                dows.append(dow)

            self.df = pl.DataFrame({
                'symbol': symbols, 'date': dates,
                'overall': overalls, 'wadj': wadjs,
                'pre_regime': pre_regs, 'regime_mult': regime_mults,
                'dow': dows,
            })
            self.df.write_parquet(scores_pq)
            if verbose:
                print(f'[runner] built+cached {len(self.df):,} rows in {time.time()-t0:.1f}s', flush=True)

        if barriers_pq.exists() and not refresh:
            t0 = time.time()
            self.barrier_df = pl.read_parquet(barriers_pq)
            if verbose:
                print(f'[runner] loaded barriers parquet ({len(self.barrier_df):,} rows) in {time.time()-t0:.1f}s', flush=True)
        else:
            if verbose:
                print(f'[runner] building barriers parquet from sqlite ...', flush=True)
            t0 = time.time()
            conn = sqlite3.connect(str(CACHE_DB))
            self.barrier_df = pl.read_database(
                f"""SELECT symbol, date, side, w_days, result, exit_return, mae_pct, mfe_pct, result_u
                    FROM barrier_outcomes WHERE date >= '{cutoff}'""",
                connection=conn,
            )
            conn.close()
            self.barrier_df.write_parquet(barriers_pq)
            if verbose:
                print(f'[runner] built+cached {len(self.barrier_df):,} rows in {time.time()-t0:.1f}s', flush=True)

    # ------------------------------------------------------------------
    def _apply_dow_dampener(self, dow_coeffs):
        """Returns df with new_overall computed via wadj × dow_dampener."""
        df = self.df
        # Build dampener column via when/then chain
        d = pl.when(pl.col('dow') == 0).then(pl.lit(dow_coeffs[0]))
        d = d.when(pl.col('dow') == 1).then(pl.lit(dow_coeffs[1]))
        d = d.when(pl.col('dow') == 2).then(pl.lit(dow_coeffs[2]))
        d = d.when(pl.col('dow') == 3).then(pl.lit(dow_coeffs[3]))
        d = d.when(pl.col('dow') == 4).then(pl.lit(dow_coeffs[4]))
        d = d.otherwise(pl.lit(1.0))
        df = df.with_columns(d.alias('dow_d'))

        # new_wadj = wadj × dampener
        df = df.with_columns((pl.col('wadj') * pl.col('dow_d')).alias('new_wadj'))

        # regime_factor: regime_mult for >=50, (2 - regime_mult) for <50
        df = df.with_columns(
            pl.when(pl.col('pre_regime') >= 50)
              .then(pl.col('regime_mult'))
              .otherwise(2.0 - pl.col('regime_mult'))
              .alias('regime_factor')
        )

        # new_overall = overall + Δwadj × regime_factor
        df = df.with_columns(
            (pl.col('overall')
             + (pl.col('new_wadj') - pl.col('wadj')) * pl.col('regime_factor'))
              .round().clip(0, 100).cast(pl.Int64)
              .alias('new_overall')
        )
        return df

    # ------------------------------------------------------------------
    def evaluate(self, dow_coeffs, lookback_days=None):
        """Returns bucketed_stats for the given day-of-week dampener coefficients.

        dow_coeffs: [Mon, Tue, Wed, Thu, Fri] ∈ [0, 1]
        lookback_days: filter to last N calendar days (None = full lookback)
        """
        if self.df is None:
            raise RuntimeError('Must call load() first')

        df = self._apply_dow_dampener(dow_coeffs)

        if lookback_days is not None:
            cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
            df = df.filter(pl.col('date') >= cutoff)

        # Filter to peaks
        df_peaks = df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))

        # Side
        df_peaks = df_peaks.with_columns(
            pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high'))
              .alias('side')
        )

        # Filter barrier_df to same window
        bdf = self.barrier_df
        if lookback_days is not None:
            cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
            bdf = bdf.filter(pl.col('date') >= cutoff)

        joined = df_peaks.join(bdf, on=['symbol', 'date', 'side'], how='inner')

        bucket_results = []
        for t in BUY_THRESHOLDS:
            sub = joined.filter(pl.col('new_overall') >= t).with_columns(pl.lit(f'{t}+').alias('bucket'))
            bucket_results.append(sub)
        for t in SELL_THRESHOLDS:
            sub = joined.filter(pl.col('new_overall') <= t).with_columns(pl.lit(f'<{t}').alias('bucket'))
            bucket_results.append(sub)

        bucketed = pl.concat(bucket_results, how='vertical')

        agg = bucketed.group_by(['bucket', 'w_days']).agg([
            pl.count().alias('n'),
            pl.col('result').filter(pl.col('result').is_not_null()).count().alias('n_resolved'),
            pl.col('result').sum().alias('wins'),
            pl.col('result_u').filter(pl.col('result_u').is_not_null()).count().alias('n_u'),
            pl.col('result_u').sum().alias('wins_u'),
            pl.col('exit_return').mean().alias('avg_return'),
            pl.col('mfe_pct').mean().alias('avg_mfe'),
        ])

        bucketed_stats = {k: {'sample_count': 0} for k in THRESHOLD_KEYS}
        for row in agg.iter_rows(named=True):
            bk = row['bucket']
            w = row['w_days']
            label = next((l for l, w_ in PERIODS if w_ == w), None)
            if label is None:
                continue
            n = row['n_resolved'] or 0
            wins = row['wins'] or 0
            stats = bucketed_stats.setdefault(bk, {})
            stats[f'win_rate_{label}'] = (wins / n * 100) if n else None
            stats[f'avg_return_{label}'] = row['avg_return']
            if w == 30:
                stats['sample_count'] = n
        return {'bucketed_stats': bucketed_stats}

    # ------------------------------------------------------------------
    def stability_metric(self, dow_coeffs, sample_symbols=None):
        """Day-over-day score volatility under given dampener.

        For each (symbol, day-pair), |new_overall_today - new_overall_yesterday|.
        Returns mean absolute day-over-day delta across all pairs.

        sample_symbols: list of stocks to use; None = all.
        """
        if self.df is None:
            raise RuntimeError('Must call load() first')
        df = self._apply_dow_dampener(dow_coeffs)
        if sample_symbols:
            df = df.filter(pl.col('symbol').is_in(sample_symbols))
        df = df.sort(['symbol', 'date'])
        df = df.with_columns(
            pl.col('new_overall').shift(1).over('symbol').alias('prev_overall')
        )
        df = df.filter(pl.col('prev_overall').is_not_null())
        df = df.with_columns(
            (pl.col('new_overall') - pl.col('prev_overall')).abs().alias('abs_delta')
        )
        return float(df['abs_delta'].mean())


# ─────────────────────────────────────────────────────────────────────
def main():
    print('Loading runner…')
    r = WadjDowRunner()  # active version
    r.load()
    print(f'\nActive version_id={r.version_id}')
    print(f'Score rows: {len(r.df):,}')
    print(f'Barriers: {len(r.barrier_df):,}')

    # Quick smoke test: baseline (all dampeners=1.0)
    print('\nBaseline (no dampening) — full lookback (5y):')
    base = r.evaluate([1.0, 1.0, 1.0, 1.0, 1.0])
    for k in ['95+', '90+', '85+', '80+', '75+', '70+', '<25', '<15', '<5']:
        s = base['bucketed_stats'].get(k, {})
        n = s.get('sample_count', 0)
        wr15 = s.get('win_rate_15d')
        wr30 = s.get('win_rate_30d')
        print(f'  {k:<6} N={n:>6}  WR15={wr15:.2f}%' if wr15 else f'  {k:<6} N={n:>6}  WR15=N/A',
              end='')
        print(f'  WR30={wr30:.2f}%' if wr30 else '  WR30=N/A')

    stab = r.stability_metric([1.0]*5)
    print(f'\nBaseline stability (mean |Δoverall| day-over-day): {stab:.3f}')


if __name__ == '__main__':
    main()
