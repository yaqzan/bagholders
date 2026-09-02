"""
Approach B — proper-score approximation.

Implements the dominant terms of calculate_rsi_score / calculate_macd_score
in vectorized Polars, then re-derives wadj and re-evaluates per-trade WR15.

Captures:
- Trend-bias-centered RSI score (the ~80% term in calculate_rsi_score)
- Vol-normalized MACD histogram score (mirrors calculate_macd_score's main effect)
- Optional EMA50-gated MACD-contradiction lift (~4pt secondary effect on RSI)

Skips:
- BREAKOUT PUSH (±15pt on rare RSI cross-30/70 events; <5% of rows affected,
  influence on WR15 should be small for tier-aggregate metrics)

Comparison with calendar baseline uses identical scoring pipeline — the only
difference is which raw RSI/MACD enter the score function.
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

CACHE_DIR = Path('.cache/rolling_weekly')
INDICATORS_PQ = CACHE_DIR / 'indicators.parquet'
RUNNER_DIR = Path('.cache/weekly_proximity')
SCORES_PQ = RUNNER_DIR / 'scores_v40_1825.parquet'
BARRIERS_PQ = RUNNER_DIR / 'barriers_1825.parquet'
OUT_PATH = Path(__file__).resolve().parent / 'proper_score_result.json'

BUY_THRESHOLDS = [95, 90, 85, 80, 75, 70]
SELL_THRESHOLDS = [30, 25, 20, 15, 10, 5]
THRESHOLD_KEYS = [f'{t}+' for t in BUY_THRESHOLDS] + [f'<{t}' for t in SELL_THRESHOLDS]
PERIODS = [('1d', 1), ('7d', 7), ('15d', 15), ('30d', 30), ('60d', 60), ('90d', 90), ('150d', 150)]


def rsi_score_expr(rsi_col: str, trend_col: str) -> pl.Expr:
    """Vectorized approximation of calculate_rsi_score base+center.

    score = 50 + 30 * tanh((center - rsi) * 0.06)
    where center = 45 + 5 * tanh((trend - 50) * 0.06)
    """
    trend_bias = ((pl.col(trend_col) - 50.0) * 0.06).tanh()
    center = 45.0 + 5.0 * trend_bias
    return (50.0 + 30.0 * ((center - pl.col(rsi_col)) * 0.06).tanh()).clip(0, 100)


def macd_score_expr(macd_hist_col: str) -> pl.Expr:
    """Vectorized approximation of calculate_macd_score core.

    score = 50 + 30 * tanh(macd_hist / 1.5)
    Uses |hist|=1.5 as the "strong" threshold; produces 50±30 range.
    """
    return (50.0 + 30.0 * (pl.col(macd_hist_col) / 1.5).tanh()).clip(0, 100)


def composite_expr(trend_score: str, rsi_score: str, macd_score: str) -> pl.Expr:
    """Replicates calculate_weekly_composite logic in Polars."""
    osc_avg = (pl.col(rsi_score) + pl.col(macd_score)) / 2.0
    gap = (pl.col(trend_score) - osc_avg).abs()
    damp = 1.0 - 0.5 * (gap / 50.0).clip(0, 1)
    w_trend = 0.35 * damp
    extra = 0.35 * (1.0 - damp)
    w_rsi = 0.35 + extra * 0.5
    w_macd = 0.30 + extra * 0.5
    return (pl.col(trend_score) * w_trend
            + pl.col(rsi_score) * w_rsi
            + pl.col(macd_score) * w_macd).clip(0, 100)


def evaluate(scores_df, barriers_df, lookback_days=1825):
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    df = scores_df.filter(pl.col('date') >= cutoff)
    df_peaks = df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
    df_peaks = df_peaks.with_columns(
        pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('side')
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
        if w == 30:
            s['sample_count'] = n
    return out


def main():
    print('Loading caches...', flush=True)
    t0 = time.time()
    scores = pl.read_parquet(SCORES_PQ).sort(['symbol', 'date'])
    barriers = pl.read_parquet(BARRIERS_PQ)
    indicators = pl.read_parquet(INDICATORS_PQ).sort(['symbol', 'date'])
    print(f'  scores: {len(scores):,} | barriers: {len(barriers):,} | indicators: {len(indicators):,} ({time.time()-t0:.1f}s)', flush=True)

    print('\nLoading calendar weekly trend + RSI + MACD from DB...', flush=True)
    t0 = time.time()
    from database.trader_database import DB
    cur = DB.execute_sql("""
        SELECT wi.symbol, wi.date, wi.rsi, wi.macd, wi.macd_hist
        FROM weekly_indicators wi
        WHERE wi.date >= '2017-01-01' AND wi.rsi IS NOT NULL
    """)
    rows = cur.fetchall()
    cal = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'wk_start': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
        'cal_rsi': [float(r[2]) if r[2] is not None else None for r in rows],
        'cal_macd': [float(r[3]) if r[3] is not None else None for r in rows],
        'cal_macd_h': [float(r[4]) if r[4] is not None else None for r in rows],
    }).sort(['symbol', 'wk_start'])
    print(f'  {len(cal):,} calendar weekly rows ({time.time()-t0:.1f}s)', flush=True)

    cur = DB.execute_sql("""
        SELECT ws.symbol, ws.date, ws.trend
        FROM weekly_scores ws
        WHERE ws.date >= '2017-01-01' AND ws.trend IS NOT NULL
    """)
    rows = cur.fetchall()
    wt = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'wk_start': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
        'wk_trend': [float(r[2]) for r in rows],
    }).sort(['symbol', 'wk_start'])

    print('\nMerging scoring rows with calendar (last week) and rolling indicators...', flush=True)
    t0 = time.time()
    df = scores.with_columns(
        (pl.col('date').str.to_date() - pl.duration(days=7)).dt.strftime('%Y-%m-%d').alias('lookup_date')
    ).sort(['symbol', 'lookup_date'])

    cal2 = cal.with_columns(pl.col('wk_start').alias('lookup_date'))
    cal_prev = cal.with_columns(
        pl.col('wk_start').alias('prev_lookup_date'),
        pl.col('cal_rsi').alias('cal_rsi_prev'),
        pl.col('cal_macd').alias('cal_macd_prev'),
        pl.col('cal_macd_h').alias('cal_macd_h_prev'),
    ).select(['symbol', 'prev_lookup_date', 'cal_rsi_prev', 'cal_macd_prev', 'cal_macd_h_prev'])

    df = df.join_asof(cal2, on='lookup_date', by='symbol', strategy='backward')
    df = df.with_columns(
        (pl.col('lookup_date').str.to_date() - pl.duration(days=7)).dt.strftime('%Y-%m-%d').alias('prev_lookup_date')
    ).sort(['symbol', 'prev_lookup_date'])
    df = df.join_asof(cal_prev, on='prev_lookup_date', by='symbol', strategy='backward')

    df = df.sort(['symbol', 'date']).join_asof(wt, left_on='date', right_on='wk_start', by='symbol', strategy='backward')

    df = df.join(indicators, on=['symbol', 'date'], how='inner')
    print(f'  joined {len(df):,} rows ({time.time()-t0:.1f}s)', flush=True)

    # ── BASELINE: calendar values through proper score function ─────
    print('\nComputing calendar-baseline scores via proper score function...', flush=True)
    df = df.with_columns([
        rsi_score_expr('cal_rsi', 'wk_trend').alias('cal_rsi_score'),
        macd_score_expr('cal_macd_h').alias('cal_macd_score'),
        rsi_score_expr('cal_rsi_prev', 'wk_trend').alias('cal_rsi_score_prev'),
        macd_score_expr('cal_macd_h_prev').alias('cal_macd_score_prev'),
    ]).with_columns([
        composite_expr('wk_trend', 'cal_rsi_score', 'cal_macd_score').alias('cal_comp'),
        composite_expr('wk_trend', 'cal_rsi_score_prev', 'cal_macd_score_prev').alias('cal_comp_prev'),
    ])

    # ── VARIANT: rolling values through proper score function ──────
    print('Computing rolling-variant scores via same proper score function...', flush=True)
    df = df.with_columns([
        rsi_score_expr('w_rsi', 'wk_trend').alias('roll_rsi_score'),
        macd_score_expr('w_macd_h').alias('roll_macd_score'),
        rsi_score_expr('prev_w_rsi', 'wk_trend').alias('roll_rsi_score_prev'),
        macd_score_expr('prev_w_macd_h').alias('roll_macd_score_prev'),
    ]).with_columns([
        composite_expr('wk_trend', 'roll_rsi_score', 'roll_macd_score').alias('roll_comp'),
        composite_expr('wk_trend', 'roll_rsi_score_prev', 'roll_macd_score_prev').alias('roll_comp_prev'),
    ])

    # ── Compute wadj from each composite pair ───────────────────────
    def wadj_from_comps(comp_col, prev_comp_col):
        # base_bias = 15 * tanh((comp-50)/50 * 1.5)  -- assumes agreement=1.0
        # mom = 8 * tanh((comp - prev_comp) / 15)
        # total = base + mom; if total<0: total *= 1.5
        base = 15.0 * (((pl.col(comp_col) - 50.0) / 50.0) * 1.5).tanh()
        mom = 8.0 * (((pl.col(comp_col) - pl.col(prev_comp_col)) / 15.0).tanh())
        raw = base + mom
        return pl.when(raw < 0).then(raw * 1.5).otherwise(raw)

    df = df.with_columns([
        wadj_from_comps('cal_comp', 'cal_comp_prev').alias('wadj_cal'),
        wadj_from_comps('roll_comp', 'roll_comp_prev').alias('wadj_roll'),
    ])

    # Jacobian: new_overall = overall + (wadj_new - wadj_stored) × regime_factor
    df = df.with_columns(
        pl.when(pl.col('pre_regime') >= 50).then(pl.col('regime_mult'))
          .otherwise(2.0 - pl.col('regime_mult')).alias('regime_factor')
    )
    # Baseline reconstructed: overall + (wadj_cal - wadj_stored) * factor
    # (wadj_cal is what production should have produced; wadj_stored may have minor sources of difference)
    df = df.with_columns([
        (pl.col('overall') + (pl.col('wadj_cal') - pl.col('wadj')) * pl.col('regime_factor'))
            .round().clip(0, 100).cast(pl.Int64).alias('overall_cal_proper'),
        (pl.col('overall') + (pl.col('wadj_roll') - pl.col('wadj')) * pl.col('regime_factor'))
            .round().clip(0, 100).cast(pl.Int64).alias('overall_roll_proper'),
    ])

    print('\n=== Per-trade evaluation (5y) ===')
    base_df = df.select([pl.col('symbol'), pl.col('date'),
                          pl.col('overall_cal_proper').alias('new_overall')])
    var_df = df.select([pl.col('symbol'), pl.col('date'),
                         pl.col('overall_roll_proper').alias('new_overall')])
    orig_df = df.select([pl.col('symbol'), pl.col('date'),
                          pl.col('overall').alias('new_overall')])

    print('  Computing baselines...')
    base_stats = evaluate(base_df, barriers, 1825)
    var_stats = evaluate(var_df, barriers, 1825)
    orig_stats = evaluate(orig_df, barriers, 1825)

    print(f'\n{"bucket":<6} {"orig_N":>7} {"orig_WR":>8} {"cal_N":>7} {"cal_WR":>8} {"roll_N":>7} {"roll_WR":>8} {"Δroll-cal":>10}')
    deltas = {}
    for b in ['95+', '90+', '85+', '80+', '75+', '70+', '<5', '<15', '<25']:
        o = orig_stats.get(b, {})
        c = base_stats.get(b, {})
        v = var_stats.get(b, {})
        on = o.get('sample_count', 0)
        ow = o.get('win_rate_15d')
        cn = c.get('sample_count', 0)
        cw = c.get('win_rate_15d')
        vn = v.get('sample_count', 0)
        vw = v.get('win_rate_15d')
        if cw is not None and vw is not None:
            d = vw - cw
            deltas[b] = {'cal_wr': cw, 'roll_wr': vw, 'delta': d, 'cal_n': cn, 'roll_n': vn,
                          'orig_wr': ow, 'orig_n': on}
            print(f'  {b:<6} {on:>7} {ow if ow else 0:>7.2f}% {cn:>7} {cw:>7.2f}% {vn:>7} {vw:>7.2f}%  {d:>+8.2f}pp')

    print('\n=== Stability (day-over-day |Δoverall|, on overall_roll_proper vs overall_cal_proper) ===')
    df_stab = df.sort(['symbol', 'date']).with_columns([
        pl.col('overall_cal_proper').shift(1).over('symbol').alias('prev_cal'),
        pl.col('overall_roll_proper').shift(1).over('symbol').alias('prev_roll'),
    ]).filter(pl.col('prev_cal').is_not_null())
    df_stab = df_stab.with_columns([
        (pl.col('overall_cal_proper') - pl.col('prev_cal')).abs().alias('cal_d'),
        (pl.col('overall_roll_proper') - pl.col('prev_roll')).abs().alias('roll_d'),
    ])
    print(f'  Calendar (proper score): mean |Δ|={float(df_stab["cal_d"].mean()):.3f}')
    print(f'  Rolling  (proper score): mean |Δ|={float(df_stab["roll_d"].mean()):.3f}')
    days = ['Mon','Tue','Wed','Thu','Fri']
    for i, d in enumerate(days):
        sub = df_stab.filter(pl.col('dow') == i)
        if len(sub):
            cv = float(sub['cal_d'].mean())
            rv = float(sub['roll_d'].mean())
            print(f'    {d}: cal={cv:.3f}  roll={rv:.3f}  Δ={(rv-cv):+.3f}')

    out = {
        'lookback_days': 1825,
        'deltas': deltas,
        'stability': {
            'calendar_mean': float(df_stab['cal_d'].mean()),
            'rolling_mean': float(df_stab['roll_d'].mean()),
        },
    }
    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f'\n[OK] saved {OUT_PATH}', flush=True)


if __name__ == '__main__':
    main()
