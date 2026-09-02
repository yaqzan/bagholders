"""Fast variant runner for floor-lift score-stage modifications.

Skips the simulator entirely for variants that ONLY modify the final score
(post-regime, post-dampener). Pre-loads v26 production scores + wadj from
the DB once, then for each variant applies the floor-lift formula via
vectorized Polars/NumPy ops, looks up cached barrier outcomes, and aggregates.

Per-variant runtime: ~2-3 seconds vs ~12 minutes via the simulator.

Usage:
    runner = FastVariantRunner(version_id=26, lookback_days=1825)
    runner.load()  # one-time, ~30s

    # Each variant:
    result = runner.evaluate_floor(k=1.0, wadj_cutoff=-13.0,
                                   score_gate=25, lift_target=26,
                                   use_tanh_weakness=False)
    # result['bucketed_stats'] has the same shape as assess_peaks_in_memory
"""
from __future__ import annotations
import io, sys, sqlite3, json, time
if __name__ == '__main__' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'

BUY_THRESHOLDS = [95, 90, 85, 80, 75, 70]
SELL_THRESHOLDS = [30, 25, 20, 15, 10, 5]
THRESHOLD_KEYS = [f'{t}+' for t in BUY_THRESHOLDS] + [f'<{t}' for t in SELL_THRESHOLDS]
PERIODS = [('1d', 1), ('7d', 7), ('15d', 15), ('30d', 30),
           ('60d', 60), ('90d', 90), ('150d', 150)]


class FastVariantRunner:
    def __init__(self, version_id=None, lookback_days=1825):
        """version_id: None = read active production version dynamically."""
        if version_id is None:
            from database.models.core import AlgorithmVersion
            version_id = AlgorithmVersion.get_active_scores_version().id
        self.version_id = version_id
        self.lookback_days = lookback_days
        self.df = None
        self.barrier_df = None

    def load(self, verbose=True, refresh=False):
        """Load v26 scores + barrier cache. Uses Parquet cache for fast reload.

        Args:
            refresh: if True, rebuild Parquet caches from MySQL/SQLite (slow first call).
                     Otherwise reuses existing Parquet files (fast).
        """
        from pathlib import Path
        cache_dir = Path('.cache/runner')
        cache_dir.mkdir(parents=True, exist_ok=True)
        scores_pq = cache_dir / f'scores_v{self.version_id}_{self.lookback_days}.parquet'
        barriers_pq = cache_dir / f'barriers_{self.lookback_days}.parquet'

        cutoff = (date.today() - timedelta(days=self.lookback_days)).isoformat()

        # ── Scores ────────────────────────────────────────────────────
        if scores_pq.exists() and not refresh:
            t0 = time.time()
            self.df = pl.read_parquet(scores_pq)
            if verbose:
                print(f"[fast-runner] loaded scores parquet ({len(self.df):,} rows) in {time.time()-t0:.1f}s", flush=True)
        else:
            from database.trader_database import DB
            if verbose:
                print(f"[fast-runner] building scores parquet from MySQL ...", flush=True)
            t0 = time.time()
            cur = DB.execute_sql(f"""
                SELECT s.symbol, s.date, s.overall, s.weight_info
                FROM scores s
                WHERE s.version_id = {self.version_id}
                  AND s.date >= '{cutoff}'
                  AND s.overall IS NOT NULL
            """)
            rows = cur.fetchall()

            symbols, dates, overalls, wadjs = [], [], [], []
            pre_regs, cap_damps, exh_damps, ext_damps, mis_strs = [], [], [], [], []
            for sym, d, ov, winfo in rows:
                if ov is None: continue
                wadj = 0.0; pre_reg = ov
                cap_d = 0; exh_d = 0.0; ext_d = 0.0; mis_s = 0.0
                if winfo:
                    try:
                        wi = json.loads(winfo) if isinstance(winfo, str) else winfo
                        wadj = float(wi.get('w_adj', 0.0) or 0.0)
                        pre_reg = int(wi.get('pre_regime', ov) or ov)
                        cap_d = 1 if wi.get('cap_dampened') else 0
                        exh_d = float(wi.get('exh_damp', 0.0) or 0.0)
                        ext_d = float(wi.get('ext_damp', 0.0) or 0.0)
                        mis_s = float(wi.get('mis_stress', 0.0) or 0.0)
                    except Exception: pass
                symbols.append(sym)
                dates.append(d.isoformat() if hasattr(d, 'isoformat') else d)
                overalls.append(int(ov))
                wadjs.append(wadj)
                pre_regs.append(pre_reg)
                cap_damps.append(cap_d)
                exh_damps.append(exh_d)
                ext_damps.append(ext_d)
                mis_strs.append(mis_s)
            self.df = pl.DataFrame({
                'symbol': symbols, 'date': dates,
                'overall': overalls, 'wadj': wadjs,
                'pre_regime': pre_regs,
                'cap_dampened': cap_damps,
                'exh_damp': exh_damps,
                'ext_damp': ext_damps,
                'mis_stress': mis_strs,
            })
            self.df.write_parquet(scores_pq)
            if verbose:
                print(f"  built+cached {len(self.df):,} rows in {time.time()-t0:.1f}s", flush=True)

        # ── Barrier outcomes ──────────────────────────────────────────
        if barriers_pq.exists() and not refresh:
            t0 = time.time()
            self.barrier_df = pl.read_parquet(barriers_pq)
            if verbose:
                print(f"[fast-runner] loaded barriers parquet ({len(self.barrier_df):,} rows) in {time.time()-t0:.1f}s", flush=True)
        else:
            if verbose:
                print(f"[fast-runner] building barriers parquet from SQLite cache ...", flush=True)
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
                print(f"  built+cached {len(self.barrier_df):,} rows in {time.time()-t0:.1f}s", flush=True)

    def evaluate_floor(self, k=1.0, wadj_cutoff=-13.0, score_gate=25, lift_target=26,
                       use_tanh_weakness=False, weakness_power=1.0,
                       score_floor=0, verbose=False):
        """Apply floor lift to active production scores and return assessment stats.

        Args:
            k: lift coefficient (0..1)
            wadj_cutoff: wadj boundary defining 'strong' weekly (linear-clip mode)
            score_gate: only lift puts with overall < this
            score_floor: only lift puts with overall >= this (preserves deepest signals if > 0)
            lift_target: lift toward this score
            use_tanh_weakness: use tanh S-curve instead of linear clip
            weakness_power: raise weakness to this power (1.0=linear, 2.0=quadratic, 0.5=sqrt)
        """
        if self.df is None:
            raise RuntimeError("Must call load() first")

        df = self.df

        # Compute base weakness
        if use_tanh_weakness:
            base_w = (1.0 + ((pl.col('wadj') + 7.0) / 4.0).tanh()) / 2.0
        else:
            base_w = ((pl.col('wadj') - wadj_cutoff) / abs(wadj_cutoff)).clip(0.0, 1.0)

        # Apply optional non-linear shaping
        if weakness_power != 1.0:
            weakness_expr = base_w.pow(weakness_power)
        else:
            weakness_expr = base_w

        # Apply floor lift with score_floor option
        df = df.with_columns(weakness_expr.alias('weakness'))
        df = df.with_columns(
            pl.when(
                (pl.col('overall') < score_gate)
                & (pl.col('overall') >= score_floor)
                & (pl.col('weakness') > 0)
            )
              .then(pl.col('overall') + k * pl.col('weakness') * (lift_target - pl.col('overall')))
              .otherwise(pl.col('overall'))
              .round()
              .clip(0, 100)
              .cast(pl.Int64)
              .alias('new_overall')
        )

        # Filter to peaks (>=70 or <=25)
        df_peaks = df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))

        # Per-symbol pruning: keep most extreme score per (symbol, date_cluster) within 1 day
        # This mirrors the simulator's pruning. For speed we use a simpler dedup-by-date.
        # In practice the |score-50| ordering matters when multiple peaks within 1 day,
        # but for variant comparison the bucketing pattern is what counts.
        df_peaks = df_peaks.sort(['symbol', 'date'])
        # Add side
        df_peaks = df_peaks.with_columns(
            pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high'))
              .alias('side')
        )

        # Join with barrier outcomes
        joined = df_peaks.join(self.barrier_df, on=['symbol', 'date', 'side'], how='inner')

        # Build cumulative bucket assignments via expression
        # Expand each peak across its bucket keys
        joined = joined.with_columns([
            pl.when(pl.col('new_overall') >= 95).then(pl.lit('95+'))
              .when(pl.col('new_overall') >= 90).then(pl.lit('90+'))
              .when(pl.col('new_overall') >= 85).then(pl.lit('85+'))
              .when(pl.col('new_overall') >= 80).then(pl.lit('80+'))
              .when(pl.col('new_overall') >= 75).then(pl.lit('75+'))
              .when(pl.col('new_overall') >= 70).then(pl.lit('70+'))
              .when(pl.col('new_overall') <= 5).then(pl.lit('<5'))
              .when(pl.col('new_overall') <= 10).then(pl.lit('<10'))
              .when(pl.col('new_overall') <= 15).then(pl.lit('<15'))
              .when(pl.col('new_overall') <= 20).then(pl.lit('<20'))
              .when(pl.col('new_overall') <= 25).then(pl.lit('<25'))
              .otherwise(None)
              .alias('top_bucket')
        ])

        # Cumulative bucket logic — each call peak goes into all 70+/75+/80+/85+/90+/95+ it qualifies for
        # Each put peak goes into all <30/<25/<20/<15/<10/<5 it qualifies for
        # Simplest: explode for each threshold
        bucket_results = []
        for t in BUY_THRESHOLDS:
            sub = joined.filter(pl.col('new_overall') >= t).with_columns(pl.lit(f'{t}+').alias('bucket'))
            bucket_results.append(sub)
        for t in SELL_THRESHOLDS:
            sub = joined.filter(pl.col('new_overall') <= t).with_columns(pl.lit(f'<{t}').alias('bucket'))
            bucket_results.append(sub)

        if not bucket_results:
            return {'bucketed_stats': {k: {'sample_count': 0} for k in THRESHOLD_KEYS}}

        bucketed = pl.concat(bucket_results, how='vertical')

        # Aggregate per (bucket, w_days)
        agg = bucketed.group_by(['bucket', 'w_days']).agg([
            pl.count().alias('n'),
            pl.col('result').filter(pl.col('result').is_not_null()).count().alias('n_resolved'),
            pl.col('result').sum().alias('wins'),
            pl.col('result_u').filter(pl.col('result_u').is_not_null()).count().alias('n_u'),
            pl.col('result_u').sum().alias('wins_u'),
            pl.col('exit_return').mean().alias('avg_return'),
            pl.col('mae_pct').mean().alias('avg_mae'),
            pl.col('mfe_pct').mean().alias('avg_mfe'),
        ])

        bucketed_stats = {k: {'sample_count': 0} for k in THRESHOLD_KEYS}
        for row in agg.iter_rows(named=True):
            bk = row['bucket']
            w = row['w_days']
            label = next((l for l, w_ in PERIODS if w_ == w), None)
            if label is None: continue
            n = row['n_resolved'] or 0
            wins = row['wins'] or 0
            n_u = row['n_u'] or 0
            wins_u = row['wins_u'] or 0
            stats = bucketed_stats.setdefault(bk, {})
            stats[f'win_rate_{label}']  = (wins / n * 100) if n else None
            stats[f'win_rate_unscaled_{label}'] = (wins_u / n_u * 100) if n_u else None
            stats[f'avg_return_{label}'] = row['avg_return']
            stats[f'avg_mae_{label}']    = (-row['avg_mae']) if row['avg_mae'] is not None else None
            stats[f'avg_mfe_{label}']    = row['avg_mfe']
            mfe = row['avg_mfe']
            stats[f'capture_ratio_{label}'] = ((row['avg_return'] or 0) / mfe) if mfe else None
            if w == 30:
                stats['sample_count'] = n  # use 30d resolved count for sample_count

        return {'bucketed_stats': bucketed_stats}


    def evaluate_general(
        self,
        # Put floor lift (primary)
        k=1.0, wadj_cutoff=-13.0, score_gate=25, lift_target=26,
        use_tanh_weakness=False, weakness_power=1.0, score_floor=0,
        # Multi-stage floor: optional second-stage lift with different params
        # for very-deep puts. Applied AFTER the primary lift.
        deep_k=None, deep_wadj_cutoff=None, deep_score_gate=None, deep_lift_target=None,
        # Call ceiling lift (mirror): when overall >= call_gate AND wadj < call_wadj_cutoff (weak bullish weekly),
        # pull score DOWN toward call_lift_target. Use sparingly (reduces call N).
        call_k=0.0, call_wadj_cutoff=13.0, call_gate=70, call_lift_target=65,
        # Verbosity
        verbose=False,
    ):
        """Generalized variant evaluator supporting multi-stage and call-side lifts."""
        if self.df is None:
            raise RuntimeError("Must call load() first")

        df = self.df

        # ── Primary put floor lift ──
        if use_tanh_weakness:
            base_w = (1.0 + ((pl.col('wadj') + 7.0) / 4.0).tanh()) / 2.0
        else:
            base_w = ((pl.col('wadj') - wadj_cutoff) / abs(wadj_cutoff)).clip(0.0, 1.0)
        w_expr = base_w.pow(weakness_power) if weakness_power != 1.0 else base_w
        df = df.with_columns(w_expr.alias('weakness1'))
        df = df.with_columns(
            pl.when((pl.col('overall') < score_gate)
                    & (pl.col('overall') >= score_floor)
                    & (pl.col('weakness1') > 0))
              .then(pl.col('overall') + k * pl.col('weakness1') * (lift_target - pl.col('overall')))
              .otherwise(pl.col('overall'))
              .round().clip(0, 100).cast(pl.Int64)
              .alias('o_after_p1')
        )

        # ── Optional second-stage put lift (e.g. stronger lift on very-deep) ──
        if deep_k is not None:
            d_cut = deep_wadj_cutoff if deep_wadj_cutoff is not None else wadj_cutoff
            d_gate = deep_score_gate if deep_score_gate is not None else 10
            d_target = deep_lift_target if deep_lift_target is not None else lift_target
            base_w2 = ((pl.col('wadj') - d_cut) / abs(d_cut)).clip(0.0, 1.0)
            df = df.with_columns(base_w2.alias('weakness2'))
            df = df.with_columns(
                pl.when((pl.col('o_after_p1') < d_gate) & (pl.col('weakness2') > 0))
                  .then(pl.col('o_after_p1') + deep_k * pl.col('weakness2') * (d_target - pl.col('o_after_p1')))
                  .otherwise(pl.col('o_after_p1'))
                  .round().clip(0, 100).cast(pl.Int64)
                  .alias('o_after_p2')
            )
        else:
            df = df.with_columns(pl.col('o_after_p1').alias('o_after_p2'))

        # ── Optional call ceiling lift (mirror) ──
        if call_k > 0:
            # Weak bullish weekly: wadj < call_wadj_cutoff (e.g. < +13 means not strongly bullish)
            cw = ((call_wadj_cutoff - pl.col('wadj')) / abs(call_wadj_cutoff)).clip(0.0, 1.0)
            df = df.with_columns(cw.alias('weakness_c'))
            df = df.with_columns(
                pl.when((pl.col('o_after_p2') >= call_gate) & (pl.col('weakness_c') > 0))
                  .then(pl.col('o_after_p2') - call_k * pl.col('weakness_c') * (pl.col('o_after_p2') - call_lift_target))
                  .otherwise(pl.col('o_after_p2'))
                  .round().clip(0, 100).cast(pl.Int64)
                  .alias('new_overall')
            )
        else:
            df = df.with_columns(pl.col('o_after_p2').alias('new_overall'))

        return self._aggregate_from_df(df)

    def _aggregate_from_df(self, df):
        """Shared aggregation: filter peaks, join barriers, bucket, return stats."""
        df_peaks = df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
        df_peaks = df_peaks.sort(['symbol', 'date']).with_columns(
            pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('side')
        )
        joined = df_peaks.join(self.barrier_df, on=['symbol', 'date', 'side'], how='inner')

        bucket_results = []
        for t in BUY_THRESHOLDS:
            sub = joined.filter(pl.col('new_overall') >= t).with_columns(pl.lit(f'{t}+').alias('bucket'))
            bucket_results.append(sub)
        for t in SELL_THRESHOLDS:
            sub = joined.filter(pl.col('new_overall') <= t).with_columns(pl.lit(f'<{t}').alias('bucket'))
            bucket_results.append(sub)

        if not bucket_results:
            return {'bucketed_stats': {k: {'sample_count': 0} for k in THRESHOLD_KEYS}}

        bucketed = pl.concat(bucket_results, how='vertical')
        agg = bucketed.group_by(['bucket', 'w_days']).agg([
            pl.count().alias('n'),
            pl.col('result').filter(pl.col('result').is_not_null()).count().alias('n_resolved'),
            pl.col('result').sum().alias('wins'),
            pl.col('result_u').filter(pl.col('result_u').is_not_null()).count().alias('n_u'),
            pl.col('result_u').sum().alias('wins_u'),
            pl.col('exit_return').mean().alias('avg_return'),
            pl.col('mae_pct').mean().alias('avg_mae'),
            pl.col('mfe_pct').mean().alias('avg_mfe'),
        ])

        bucketed_stats = {k: {'sample_count': 0} for k in THRESHOLD_KEYS}
        for row in agg.iter_rows(named=True):
            bk = row['bucket']
            w = row['w_days']
            label = next((l for l, w_ in PERIODS if w_ == w), None)
            if label is None: continue
            n = row['n_resolved'] or 0; wins = row['wins'] or 0
            n_u = row['n_u'] or 0; wins_u = row['wins_u'] or 0
            stats = bucketed_stats.setdefault(bk, {})
            stats[f'win_rate_{label}'] = (wins / n * 100) if n else None
            stats[f'win_rate_unscaled_{label}'] = (wins_u / n_u * 100) if n_u else None
            stats[f'avg_return_{label}'] = row['avg_return']
            stats[f'avg_mae_{label}'] = (-row['avg_mae']) if row['avg_mae'] is not None else None
            stats[f'avg_mfe_{label}'] = row['avg_mfe']
            mfe = row['avg_mfe']
            stats[f'capture_ratio_{label}'] = ((row['avg_return'] or 0) / mfe) if mfe else None
            if w == 30:
                stats['sample_count'] = n
        return {'bucketed_stats': bucketed_stats}

    def evaluate_batch(self, variants, n_workers=None):
        """Evaluate many variants in parallel via threads.

        Polars releases the GIL during heavy ops, so threading parallelizes
        cleanly without pickle overhead. Default n_workers=cpu_count.
        Returns list of (variant, stats) in input order.
        """
        if self.df is None:
            raise RuntimeError("Must call load() first")
        from concurrent.futures import ThreadPoolExecutor
        import os as _os
        n_workers = n_workers or (_os.cpu_count() or 4)
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futures = [ex.submit(self.evaluate_floor, **v) for v in variants]
            return [(v, f.result()) for v, f in zip(variants, futures)]


# ──────────────────────────────────────────────────────────────────────────────
# CLI: run a sweep of floor variants and print a comparison table
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_SWEEP = [
    # (label, k, wadj_cut, score_gate, lift_target, tanh_w)
    ('baseline',   None, None,  None,  None,  False),  # k=None means no lift (=baseline)
    ('K05',        0.5,  -13.0, 25,    26,    False),
    ('K07',        0.7,  -13.0, 25,    26,    False),
    ('K10',        1.0,  -13.0, 25,    26,    False),
    ('K10_W10',    1.0,  -10.0, 25,    26,    False),
    ('K10_W16',    1.0,  -16.0, 25,    26,    False),
    ('K10_T30',    1.0,  -13.0, 25,    30,    False),
    ('K10_T35',    1.0,  -13.0, 25,    35,    False),
    ('K10_TANH_W', 1.0,  -13.0, 25,    26,    True),
]


def run_sweep(sweep=None, version_id=None, lookback_days=1825):
    runner = FastVariantRunner(version_id=version_id, lookback_days=lookback_days)
    runner.load()
    sweep = sweep or DEFAULT_SWEEP

    print("\n" + "=" * 130)
    print(f"{'Variant':<14}  {'<10 WR':>14}  {'<15 WR':>14}  {'<25 WR':>14}  {'70+ WR':>14}  {'<25 N':>10}  {'time':>7}")
    print("-" * 130)

    baseline_stats = None
    for entry in sweep:
        label, k, wc, sg, lt, ut = entry
        t0 = time.time()
        if k is None:
            # Baseline: no lift — just identity transformation
            stats = runner.evaluate_floor(k=0.0, wadj_cutoff=-13.0, score_gate=0,
                                          lift_target=26, use_tanh_weakness=False)
        else:
            stats = runner.evaluate_floor(k=k, wadj_cutoff=wc, score_gate=sg,
                                          lift_target=lt, use_tanh_weakness=ut)
        t = time.time() - t0
        if label == 'baseline':
            baseline_stats = stats

        def fmt(b):
            v = stats['bucketed_stats'].get(b, {}).get('win_rate_15d')
            if v is None: return '   --   '
            if baseline_stats and b in baseline_stats['bucketed_stats']:
                bv = baseline_stats['bucketed_stats'][b].get('win_rate_15d')
                if bv is not None:
                    d = v - bv
                    return f"{v:5.1f}% ({d:+5.1f})"
            return f"{v:5.1f}%"

        n25 = stats['bucketed_stats'].get('<25', {}).get('sample_count') or 0
        print(f"{label:<14}  {fmt('<10'):>14}  {fmt('<15'):>14}  {fmt('<25'):>14}  {fmt('70+'):>14}  {n25:>10}  {t:>5.2f}s")


if __name__ == '__main__':
    run_sweep()
