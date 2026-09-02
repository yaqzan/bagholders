"""
Approach A — distribution check.

Compare rolling weekly raw indicators (from indicators.parquet) vs the existing
calendar WeeklyIndicator raw values (from DB) for the SAME (symbol, scoring_date)
pairs. Tells us if rolling and calendar are near-substitutes or fundamentally
different signals.

Outputs:
- Mean / median / stddev of each indicator under both
- Per-row correlation
- Distribution overlap
- Sample diff distribution: which way does rolling deviate from calendar?
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
OUT_PATH = Path(__file__).resolve().parent / 'distribution_check.json'


def main():
    print('Loading rolling indicators...', flush=True)
    rolling = pl.read_parquet(INDICATORS_PQ)
    print(f'  {len(rolling):,} rows', flush=True)

    print('\nFetching calendar WeeklyIndicator from DB...', flush=True)
    t0 = time.time()
    from database.trader_database import DB
    cur = DB.execute_sql("""
        SELECT symbol, date, rsi, macd, macd_hist
        FROM weekly_indicators
        WHERE date >= '2017-01-01' AND rsi IS NOT NULL
    """)
    rows = cur.fetchall()
    cal = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'wk_start': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
        'cal_rsi': [float(r[2]) if r[2] is not None else None for r in rows],
        'cal_macd': [float(r[3]) if r[3] is not None else None for r in rows],
        'cal_macd_h': [float(r[4]) if r[4] is not None else None for r in rows],
    })
    cal = cal.sort(['symbol', 'wk_start'])
    print(f'  {len(cal):,} calendar weekly rows ({time.time()-t0:.1f}s)', flush=True)

    # Join: for each rolling (symbol, date), find the most recent calendar weekly
    # with wk_start <= date - 7 days (last week's Monday).
    # Easiest: subtract 7 days from rolling.date, then asof-join to cal.wk_start.
    print('\nMatching rolling vs calendar (last week Monday)...', flush=True)
    t0 = time.time()
    rolling = rolling.sort(['symbol', 'date'])
    rolling2 = rolling.with_columns(
        (pl.col('date').str.to_date() - pl.duration(days=7))
            .dt.strftime('%Y-%m-%d').alias('lookup_date')
    )
    rolling2 = rolling2.sort(['symbol', 'lookup_date'])
    cal = cal.with_columns(pl.col('wk_start').alias('lookup_date'))
    joined = rolling2.join_asof(cal, on='lookup_date', by='symbol', strategy='backward')
    joined = joined.filter(
        pl.col('cal_rsi').is_not_null() & pl.col('w_rsi').is_not_null()
    )
    print(f'  matched {len(joined):,} rows ({time.time()-t0:.1f}s)', flush=True)

    # ── Stats ────────────────────────────────────────────────────────
    print('\n=== Raw RSI distribution ===')
    for col, name in [('w_rsi', 'rolling'), ('cal_rsi', 'calendar')]:
        s = joined[col]
        print(f'  {name:<10} mean={float(s.mean()):.2f}  median={float(s.median()):.2f}  '
              f'std={float(s.std()):.2f}  min={float(s.min()):.2f}  max={float(s.max()):.2f}')

    print('\n=== Raw MACD distribution ===')
    for col, name in [('w_macd', 'rolling'), ('cal_macd', 'calendar')]:
        s = joined[col]
        if s.null_count() == len(s):
            continue
        s = s.drop_nulls()
        print(f'  {name:<10} mean={float(s.mean()):.3f}  median={float(s.median()):.3f}  '
              f'std={float(s.std()):.3f}  min={float(s.min()):.3f}  max={float(s.max()):.3f}')

    print('\n=== Raw MACD-histogram distribution ===')
    for col, name in [('w_macd_h', 'rolling'), ('cal_macd_h', 'calendar')]:
        s = joined[col]
        if s.null_count() == len(s):
            continue
        s = s.drop_nulls()
        print(f'  {name:<10} mean={float(s.mean()):.3f}  median={float(s.median()):.3f}  '
              f'std={float(s.std()):.3f}  min={float(s.min()):.3f}  max={float(s.max()):.3f}')

    # ── Correlation ──────────────────────────────────────────────────
    print('\n=== Pearson correlation (per-row, same scoring date) ===')
    corr_rsi = float(joined.select(pl.corr('w_rsi', 'cal_rsi')).to_numpy()[0, 0])
    print(f'  RSI:        {corr_rsi:+.4f}')
    has_macd = joined['w_macd'].null_count() < len(joined) and joined['cal_macd'].null_count() < len(joined)
    if has_macd:
        # Filter to non-null on both sides
        sub = joined.filter(pl.col('w_macd').is_not_null() & pl.col('cal_macd').is_not_null())
        corr_macd = float(sub.select(pl.corr('w_macd', 'cal_macd')).to_numpy()[0, 0])
        corr_macd_h = float(sub.select(pl.corr('w_macd_h', 'cal_macd_h')).to_numpy()[0, 0])
        print(f'  MACD line:  {corr_macd:+.4f}')
        print(f'  MACD hist:  {corr_macd_h:+.4f}')

    # ── Diff distribution (rolling - calendar) ───────────────────────
    print('\n=== Diff (rolling - calendar) ===')
    diff_rsi = (joined['w_rsi'] - joined['cal_rsi'])
    print(f'  RSI diff:   mean={float(diff_rsi.mean()):+.2f}  median={float(diff_rsi.median()):+.2f}  '
          f'std={float(diff_rsi.std()):.2f}')
    print(f'    |diff|≤5:  {(diff_rsi.abs() <= 5).sum() / len(diff_rsi) * 100:.1f}% of rows')
    print(f'    |diff|≤10: {(diff_rsi.abs() <= 10).sum() / len(diff_rsi) * 100:.1f}% of rows')
    print(f'    |diff|≤20: {(diff_rsi.abs() <= 20).sum() / len(diff_rsi) * 100:.1f}% of rows')

    # ── Per-bucket comparison: is the deviation systematic by RSI level? ───
    print('\n=== RSI diff broken out by calendar RSI band (does rolling shift systematically?) ===')
    bands = [(0, 30), (30, 40), (40, 50), (50, 60), (60, 70), (70, 100)]
    for lo, hi in bands:
        sub = joined.filter((pl.col('cal_rsi') >= lo) & (pl.col('cal_rsi') < hi))
        if len(sub) == 0:
            continue
        d = (sub['w_rsi'] - sub['cal_rsi'])
        print(f'  cal_rsi ∈ [{lo:>3}, {hi:>3}): N={len(sub):>6,}  '
              f'mean(rolling - calendar)={float(d.mean()):+.2f}  '
              f'std={float(d.std()):.2f}')

    # ── Verdict heuristic ────────────────────────────────────────────
    print('\n=== Verdict heuristic ===')
    if abs(float(diff_rsi.mean())) < 2 and float(diff_rsi.std()) < 8 and corr_rsi > 0.7:
        verdict = 'NEAR-SUBSTITUTE — rolling and calendar are tightly aligned; expect proper-scoring WR within ±1pp'
    elif corr_rsi > 0.5:
        verdict = 'CORRELATED BUT DIFFERENT — same general signal but different magnitudes; proper scoring may still differ noticeably'
    else:
        verdict = 'DECOUPLED — rolling and calendar are fundamentally different signals; ship H3 fallback'
    print(f'  → {verdict}')

    out = {
        'n_matched': len(joined),
        'rsi': {
            'rolling_mean': float(joined['w_rsi'].mean()),
            'rolling_median': float(joined['w_rsi'].median()),
            'calendar_mean': float(joined['cal_rsi'].mean()),
            'calendar_median': float(joined['cal_rsi'].median()),
            'correlation': corr_rsi,
            'diff_mean': float(diff_rsi.mean()),
            'diff_std': float(diff_rsi.std()),
            'pct_within_5': float((diff_rsi.abs() <= 5).sum() / len(diff_rsi)),
            'pct_within_10': float((diff_rsi.abs() <= 10).sum() / len(diff_rsi)),
        },
        'verdict': verdict,
    }
    if has_macd:
        out['macd'] = {'correlation_line': corr_macd, 'correlation_hist': corr_macd_h}
    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f'\n[OK] saved {OUT_PATH}', flush=True)


if __name__ == '__main__':
    main()
