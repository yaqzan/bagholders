"""
Rolling-weekly variant runner.

For each (symbol, scoring_date) row in the v40 score table:
  1. Look up rolling indicators (w_rsi, w_macd, prev_w_rsi, prev_w_macd) from indicators.parquet
  2. Re-derive wadj using calculate_weekly_adjustment with these new values + existing trend
  3. Apply Jacobian shortcut: new_overall = overall + (new_wadj - old_wadj) × regime_factor

Important note on approximation:
  calculate_weekly_adjustment expects 0-100 SCORED values for rsi/macd. The existing
  system feeds it WeeklyScore.rsi (which is the OUTPUT of calculate_rsi_score, not raw).
  We approximate by passing raw RSI directly (also 0-100, naturally) and converting
  raw MACD to 0-100 via sigmoid. This captures the TIMING change of rolling weekly
  but isn't exact production behavior. Full ship would need to run rolling RSI through
  calculate_rsi_score. This is a first-pass directional test.
"""
from __future__ import annotations
import io
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

if __name__ == '__main__' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from database.utils.scoring import calculate_weekly_adjustment, calculate_weekly_composite

CACHE_DIR = Path('.cache/rolling_weekly')
INDICATORS_PQ = CACHE_DIR / 'indicators.parquet'
RUNNER_DIR = Path('.cache/weekly_proximity')   # reuse weekly_proximity caches (v40)
SCORES_PQ = RUNNER_DIR / 'scores_v40_1825.parquet'
BARRIERS_PQ = RUNNER_DIR / 'barriers_1825.parquet'

OUT_PATH = Path(__file__).resolve().parent / 'variant_result.json'


BUY_THRESHOLDS = [95, 90, 85, 80, 75, 70]
SELL_THRESHOLDS = [30, 25, 20, 15, 10, 5]
THRESHOLD_KEYS = [f'{t}+' for t in BUY_THRESHOLDS] + [f'<{t}' for t in SELL_THRESHOLDS]
PERIODS = [('1d', 1), ('7d', 7), ('15d', 15), ('30d', 30),
           ('60d', 60), ('90d', 90), ('150d', 150)]


def macd_to_score(macd_hist: float | None) -> float | None:
    """Map MACD histogram to 0-100 score via sigmoid centered at 0.

    Approximation of what calculate_macd_score does (which is breakout-aware).
    For first-pass directional test; full ship needs proper score function.
    """
    if macd_hist is None or np.isnan(macd_hist):
        return None
    return 50.0 + 50.0 * np.tanh(macd_hist / 3.0)


def derive_new_wadj(rsi_new: float, macd_new: float, trend: float,
                    prev_rsi_new: float, prev_macd_new: float, prev_trend: float):
    """Run calculate_weekly_adjustment with new values."""
    if any(v is None or (isinstance(v, float) and np.isnan(v))
           for v in [rsi_new, macd_new, trend]):
        return 0.0
    total, detail = calculate_weekly_adjustment(
        trend=trend, rsi=rsi_new, macd=macd_new,
        prev_trend=prev_trend, prev_rsi=prev_rsi_new, prev_macd=prev_macd_new,
    )
    return total


def evaluate_variant(scores_df: pl.DataFrame, barriers_df: pl.DataFrame,
                     lookback_days: int = 1825) -> dict:
    """Bucket + barrier join, return WR15/30 stats per bucket."""
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    df = scores_df.filter(pl.col('date') >= cutoff)

    df_peaks = df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
    df_peaks = df_peaks.with_columns(
        pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high'))
          .alias('side')
    )

    bdf = barriers_df.filter(pl.col('date') >= cutoff)
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
        pl.len().alias('n'),
        pl.col('result').filter(pl.col('result').is_not_null()).count().alias('n_resolved'),
        pl.col('result').sum().alias('wins'),
        pl.col('exit_return').mean().alias('avg_return'),
    ])

    out = {k: {'sample_count': 0} for k in THRESHOLD_KEYS}
    for row in agg.iter_rows(named=True):
        bk = row['bucket']
        w = row['w_days']
        label = next((l for l, w_ in PERIODS if w_ == w), None)
        if label is None:
            continue
        n = row['n_resolved'] or 0
        wins = row['wins'] or 0
        s = out.setdefault(bk, {})
        s[f'win_rate_{label}'] = (wins / n * 100) if n else None
        s[f'avg_return_{label}'] = row['avg_return']
        if w == 30:
            s['sample_count'] = n
    return out


def main():
    print('Loading caches...', flush=True)
    t0 = time.time()
    scores = pl.read_parquet(SCORES_PQ)  # has: symbol, date, overall, wadj, pre_regime, regime_mult, dow
    barriers = pl.read_parquet(BARRIERS_PQ)
    indicators = pl.read_parquet(INDICATORS_PQ)
    print(f'  scores: {len(scores):,} | barriers: {len(barriers):,} | indicators: {len(indicators):,}'
          f' ({time.time()-t0:.1f}s)', flush=True)

    # Need raw weekly trend from somewhere. The fast_variant_runner parquet doesn't have it.
    # Pull weekly trend from WeeklyScore via DB (one-time).
    print('\nLoading weekly trend scores from DB...', flush=True)
    t0 = time.time()
    from database.trader_database import DB
    cur = DB.execute_sql("""
        SELECT ws.symbol, ws.date, ws.trend
        FROM weekly_scores ws
        WHERE ws.date >= '2017-01-01' AND ws.trend IS NOT NULL
    """)
    rows = cur.fetchall()
    weekly_trend = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'wk_start': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
        'wk_trend': [int(r[2]) for r in rows],
    })
    print(f'  {len(weekly_trend):,} weekly trend rows ({time.time()-t0:.1f}s)', flush=True)

    # Join: for each scoring date, find the latest weekly_trend with wk_start <= date - 7
    # (last week's Monday). Use pl.join_asof.
    print('\nMerging rolling indicators + last-week trend with score table...', flush=True)
    t0 = time.time()

    # Sort prerequisites for asof joins
    scores = scores.sort(['symbol', 'date'])
    weekly_trend = weekly_trend.sort(['symbol', 'wk_start'])

    # Previous-week (W-1) trend: we want wk_start < date and wk_start >= date - 14 days
    # Easiest: join_asof on date_lt
    scores2 = scores.join_asof(
        weekly_trend, left_on='date', right_on='wk_start', by='symbol', strategy='backward')
    # Note: this gives the most recent wk_start <= scoring date, which can be THIS week's Monday or earlier.
    # For "last week's trend" we need wk_start <= date - 7 days. Subtract via filter.
    # Actually for simplicity, use this-week's Monday's WeeklyScore.trend (from production lookup behavior)

    # Now join with rolling indicators
    indicators = indicators.sort(['symbol', 'date'])
    scores3 = scores2.join(indicators, on=['symbol', 'date'], how='inner')
    print(f'  joined {len(scores3):,} rows ({time.time()-t0:.1f}s)', flush=True)

    # Compute new wadj
    print('\nComputing new wadj per row...', flush=True)
    t0 = time.time()
    # Use Polars expressions where possible; fall back to map for the non-vectorizable adj
    # Actually calculate_weekly_adjustment is Python code with branching — use map_elements

    # Convert raw MACD histogram to 0-100 score
    df = scores3.with_columns([
        (50.0 + 50.0 * (pl.col('w_macd_h').fill_null(0.0) / 3.0).tanh()).alias('w_macd_score'),
        (50.0 + 50.0 * (pl.col('prev_w_macd_h').fill_null(0.0) / 3.0).tanh()).alias('prev_w_macd_score'),
    ])

    # Vectorized weekly_adjustment computation in Polars
    # base_bias = 15 * tanh(((composite-50)/50) * 1.5) * agreement
    # agreement = 0.8 + 0.6 * consistency where consistency = sum(max(0, s_i*sign(avg))) / 3
    # For simplicity, treat trend as the trend value passed in.
    # composite = calculate_weekly_composite(rsi, macd, trend)
    # We re-implement the composite formula in Polars:
    def composite_expr(trend_col, rsi_col, macd_col):
        osc_avg = (rsi_col + macd_col) / 2.0
        gap = (trend_col - osc_avg).abs()
        damp = (1.0 - 0.5 * (gap / 50.0).clip(0, 1))
        w_trend = 0.35 * damp
        extra = 0.35 * (1.0 - damp)
        w_rsi = 0.35 + extra * 0.5
        w_macd = 0.30 + extra * 0.5
        return (trend_col * w_trend + rsi_col * w_rsi + macd_col * w_macd).clip(0, 100)

    df = df.with_columns([
        composite_expr(pl.col('wk_trend').cast(pl.Float64),
                       pl.col('w_rsi'),
                       pl.col('w_macd_score')).alias('comp_new'),
        composite_expr(pl.col('wk_trend').cast(pl.Float64),
                       pl.col('prev_w_rsi'),
                       pl.col('prev_w_macd_score')).alias('prev_comp_new'),
    ])

    # Now compute wadj from composites
    # base_bias = 15 * tanh(((comp-50)/50) * 1.5) * agreement
    # agreement: simplified — we assume agreement≈1.0 for the variant test (since we don't have
    # the exact same components-derived agreement). Set agreement = 1.0 across the board.
    # base_bias_simple = 15 * tanh((comp-50)/50 * 1.5)  -- this matches when agreement=1.0
    # momentum_bias = 8 * tanh(delta/15) where delta = comp - prev_comp
    # total = base_bias_simple + momentum_bias
    # asymmetric scaling: if total < 0: total *= 1.5

    df = df.with_columns([
        (15.0 * (((pl.col('comp_new') - 50.0) / 50.0) * 1.5).tanh()).alias('base_bias_simple'),
        (8.0 * (((pl.col('comp_new') - pl.col('prev_comp_new')) / 15.0).tanh())).alias('mom_bias_new'),
    ])
    df = df.with_columns(
        (pl.col('base_bias_simple') + pl.col('mom_bias_new')).alias('wadj_raw_new')
    )
    df = df.with_columns(
        pl.when(pl.col('wadj_raw_new') < 0)
          .then(pl.col('wadj_raw_new') * 1.5)
          .otherwise(pl.col('wadj_raw_new'))
          .alias('wadj_new')
    )

    # Apply Jacobian shortcut
    df = df.with_columns(
        pl.when(pl.col('pre_regime') >= 50)
          .then(pl.col('regime_mult'))
          .otherwise(2.0 - pl.col('regime_mult'))
          .alias('regime_factor')
    )
    df = df.with_columns(
        (pl.col('overall') + (pl.col('wadj_new') - pl.col('wadj')) * pl.col('regime_factor'))
          .round().clip(0, 100).cast(pl.Int64)
          .alias('new_overall')
    )

    print(f'  wadj computation done in {time.time()-t0:.1f}s', flush=True)

    # Save the prepared dataframe for downstream
    df_out = df.select(['symbol', 'date', 'overall', 'wadj', 'wadj_new', 'pre_regime',
                         'regime_mult', 'regime_factor', 'new_overall', 'dow',
                         'w_rsi', 'prev_w_rsi', 'w_macd_score', 'prev_w_macd_score'])
    out_pq = Path(__file__).resolve().parent / 'variant_overall.parquet'
    df_out.write_parquet(out_pq)
    print(f'  saved {out_pq} ({len(df_out):,} rows)', flush=True)

    # Per-trade evaluation
    print('\nPer-trade evaluation (5y baseline + variant)...', flush=True)

    # Baseline: use overall as-is
    baseline_df = df.select([
        pl.col('symbol'), pl.col('date'), pl.col('overall').alias('new_overall')
    ])
    base_stats = evaluate_variant(baseline_df, barriers, lookback_days=1825)

    # Variant
    var_df = df.select([
        pl.col('symbol'), pl.col('date'), pl.col('new_overall')
    ])
    var_stats = evaluate_variant(var_df, barriers, lookback_days=1825)

    print(f'\n{"bucket":<6} {"N_var":>7} {"N_base":>7} {"WR15_v":>7} {"WR15_b":>7} {"ΔWR15":>7}')
    delta_summary = {}
    for b in ['95+', '90+', '85+', '80+', '75+', '70+', '<5', '<15', '<25']:
        v = var_stats.get(b, {})
        bs = base_stats.get(b, {})
        n_v = v.get('sample_count', 0)
        n_b = bs.get('sample_count', 0)
        wr_v = v.get('win_rate_15d')
        wr_b = bs.get('win_rate_15d')
        if wr_v is not None and wr_b is not None:
            delta_summary[b] = {'wr15_v': wr_v, 'wr15_b': wr_b, 'delta': wr_v - wr_b,
                                 'n_v': n_v, 'n_b': n_b}
            print(f'  {b:<6} {n_v:>7} {n_b:>7}  {wr_v:>5.2f}%  {wr_b:>5.2f}%  {wr_v-wr_b:>+5.2f}pp')

    # Stability metric
    print('\nStability metric (day-over-day |Δoverall|):')
    df_stab = df.sort(['symbol', 'date'])
    df_stab = df_stab.with_columns([
        pl.col('overall').shift(1).over('symbol').alias('prev_baseline_overall'),
        pl.col('new_overall').shift(1).over('symbol').alias('prev_variant_overall'),
    ])
    df_stab = df_stab.filter(pl.col('prev_baseline_overall').is_not_null())
    df_stab = df_stab.with_columns([
        (pl.col('overall') - pl.col('prev_baseline_overall')).abs().alias('base_d'),
        (pl.col('new_overall') - pl.col('prev_variant_overall')).abs().alias('var_d'),
    ])
    print(f'  baseline: |Δ|={float(df_stab["base_d"].mean()):.3f}')
    print(f'  variant:  |Δ|={float(df_stab["var_d"].mean()):.3f}')

    # Per-day-of-week
    print('\n  Per-day-of-week stability:')
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    for i, dn in enumerate(days):
        sub = df_stab.filter(pl.col('dow') == i)
        bv = float(sub['base_d'].mean()) if len(sub) else None
        vv = float(sub['var_d'].mean()) if len(sub) else None
        if bv is not None and vv is not None:
            print(f'    {dn}: baseline={bv:.3f}  variant={vv:.3f}  Δ={(vv-bv):+.3f}')

    # Save full result
    result = {
        'lookback_days': 1825,
        'baseline_stats': {b: {k: v for k, v in s.items() if not callable(v)}
                            for b, s in base_stats.items()},
        'variant_stats': {b: {k: v for k, v in s.items() if not callable(v)}
                           for b, s in var_stats.items()},
        'delta_summary': delta_summary,
        'stability': {
            'baseline_overall': float(df_stab['base_d'].mean()),
            'variant_overall':  float(df_stab['var_d'].mean()),
        },
    }
    OUT_PATH.write_text(json.dumps(result, indent=2, default=str))
    print(f'\n[OK] saved {OUT_PATH}', flush=True)


if __name__ == '__main__':
    main()
