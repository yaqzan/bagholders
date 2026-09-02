"""Phase A: Slow-indicator weekly composite features (known-issues.md Priority #5b).

For each call peak (overall >= 70, active version, pre-cutoff) builds:

  AVWAP-from-last-earnings
    Anchored volume-weighted average price from the most recent earnings
    effective_date before the scoring date. Stable by construction — anchors to
    a discrete event date, not a calendar-week boundary.
    Columns: avwap_pct, avwap_anchor_date, avwap_bars

  Weekly Ichimoku signals
    Tenkan-sen (9w), Kijun-sen (26w), Span A, Span B (52w midpoint).
    Uses last COMPLETED weekly bar (lookup_date = scoring_date - 7 days) to
    avoid partial-week instability — the same problem that caused v42 whiplash.
    Columns: price_vs_kijun_pct, price_vs_tenkan_pct, tenkan_kijun_dir,
             price_vs_span_b_pct, cloud_position

  50-week SMA proximity
    Distance from close to 50-week rolling mean.
    Column: sma50w_pct

  52-week high/low proximity (weekly bars)
    Distance from close to rolling 52-week high and low.
    Columns: w52_high_pct, w52_low_pct

  Option-aligned barrier outcomes (30dte_opt, w=15, side='low')
    Columns: opt_result_15, opt_exit_return_15, opt_mae_15, opt_mfe_15

Output: .cache/weekly_avwap/calls_v{av_id}_{lookback}d.parquet

Usage:
    python experiments/weekly_avwap/build_features.py [lookback_days]
    python experiments/weekly_avwap/build_features.py 1825   # 5y (default)
    python experiments/weekly_avwap/build_features.py 3650   # 10y

Why separate from score_velocity/build_features.py:
  - AVWAP requires per-symbol cumulative VWAP from a variable anchor date.
  - Ichimoku/SMA require weekly price data, not daily scores.
  - These signals are inherently stable across weekday boundaries (no partial-
    bar aggregation) — validating the hypothesis that "slow" indicators avoid
    the COHR-class whiplash problem (Priority #7).
"""
from __future__ import annotations

import bisect
import io
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
OUT_DIR = ROOT / '.cache' / 'weekly_avwap'

LOOKBACK_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 1825
MIN_SCORE = int(sys.argv[2]) if len(sys.argv) > 2 else 70

# Buffer before the lookback start:
#   52 weekly bars  ≈ 375 cal days  (Ichimoku Span B needs 52w of weekly data)
#   252 trading days ≈ 375 cal days  (52w daily HL also needs this)
#   We use 400 to cover both comfortably.
BUFFER_DAYS = 400


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _s(v) -> str:
    """Coerce a date-like value to ISO string."""
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    return str(v)[:10]


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_peaks(av_id: int, start: str, cutoff: str, min_score: int = 70) -> pl.DataFrame:
    from database.trader_database import DB
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall
        FROM scores s
        WHERE s.version_id = {av_id}
          AND s.date >= '{start}'
          AND s.date <= '{cutoff}'
          AND s.overall >= {min_score}
    """)
    rows = cur.fetchall()
    df = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date':   [_s(r[1]) for r in rows],
        'overall':[int(r[2]) for r in rows],
    })
    print(f'[load] call peaks (>={min_score}): {len(df):,}  ({time.time()-t0:.1f}s)', flush=True)
    return df


def load_daily(buf_start: str, cutoff: str) -> pl.DataFrame:
    """Load daily PriceHistory; append cumulative sums for AVWAP."""
    from database.trader_database import DB
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT symbol, date, close, volume, high, low
        FROM price_history
        WHERE date >= '{buf_start}'
          AND date <= '{cutoff}'
        ORDER BY symbol, date
    """)
    rows = cur.fetchall()
    df = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date':   [_s(r[1]) for r in rows],
        'close':  [float(r[2]) for r in rows],
        'volume': [float(r[3]) if r[3] else 0.0 for r in rows],
        'high':   [float(r[4]) for r in rows],
        'low':    [float(r[5]) for r in rows],
    }).sort(['symbol', 'date'])

    # Cumulative price-volume and volume for AVWAP computation.
    # cum_cv[i]        = sum(close*vol from row_0 to i, inclusive)
    # cum_cv_before[i] = sum(close*vol from row_0 to i-1)  ← "just before bar i"
    df = df.with_columns(
        (pl.col('close') * pl.col('volume')).alias('cv')
    )
    df = df.with_columns([
        pl.col('cv').cum_sum().over('symbol').alias('cum_cv'),
        pl.col('volume').cum_sum().over('symbol').alias('cum_v'),
    ])
    df = df.with_columns([
        (pl.col('cum_cv') - pl.col('cv')).alias('cum_cv_before'),
        (pl.col('cum_v') - pl.col('volume')).alias('cum_v_before'),
    ])
    print(f'[load] daily prices: {len(df):,}  ({time.time()-t0:.1f}s)', flush=True)
    return df


def load_earnings(cutoff: str) -> pl.DataFrame:
    """Load EarningsDate.effective_date per symbol (pre-cutoff, no ghosts)."""
    from database.trader_database import DB
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT symbol, effective_date
        FROM earnings_dates
        WHERE effective_date IS NOT NULL
          AND effective_date <= '{cutoff}'
          AND effective_date >= '2015-01-01'
        ORDER BY symbol, effective_date
    """)
    rows = cur.fetchall()
    df = pl.DataFrame({
        'symbol':         [r[0] for r in rows],
        'effective_date': [_s(r[1]) for r in rows],
    })
    print(f'[load] earnings dates: {len(df):,}  ({time.time()-t0:.1f}s)', flush=True)
    return df


def load_weekly(buf_start: str, cutoff: str) -> pl.DataFrame:
    """Load WeeklyPriceHistory; compute Ichimoku, 50W SMA per symbol."""
    from database.trader_database import DB
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT symbol, date, high, low, close
        FROM weekly_price_history
        WHERE date >= '{buf_start}'
          AND date <= '{cutoff}'
        ORDER BY symbol, date
    """)
    rows = cur.fetchall()
    raw = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date':   [_s(r[1]) for r in rows],
        'high':   [float(r[2]) for r in rows],
        'low':    [float(r[3]) for r in rows],
        'close':  [float(r[4]) for r in rows],
    })
    print(f'[load] weekly prices: {len(raw):,}  ({time.time()-t0:.1f}s)', flush=True)

    # Per-symbol rolling computations.
    # NULLs appear naturally for early rows with insufficient lookback history.
    t0 = time.time()
    groups = []
    for sym, grp in raw.group_by('symbol'):
        g = grp.sort('date')
        g = g.with_columns([
            pl.col('high').rolling_max(window_size=9).alias('high_9w'),
            pl.col('low').rolling_min(window_size=9).alias('low_9w'),
            pl.col('high').rolling_max(window_size=26).alias('high_26w'),
            pl.col('low').rolling_min(window_size=26).alias('low_26w'),
            pl.col('high').rolling_max(window_size=52).alias('high_52w'),
            pl.col('low').rolling_min(window_size=52).alias('low_52w'),
            pl.col('close').rolling_mean(window_size=50).alias('sma_50w'),
        ])
        g = g.with_columns([
            # Tenkan-sen: 9w conversion line
            ((pl.col('high_9w') + pl.col('low_9w')) / 2).alias('tenkan'),
            # Kijun-sen: 26w base line
            ((pl.col('high_26w') + pl.col('low_26w')) / 2).alias('kijun'),
            # Senkou Span B: 52w midpoint (used without forward projection here)
            ((pl.col('high_52w') + pl.col('low_52w')) / 2).alias('span_b'),
        ])
        g = g.with_columns([
            # Senkou Span A: (Tenkan + Kijun) / 2
            ((pl.col('tenkan') + pl.col('kijun')) / 2).alias('span_a'),
        ])
        groups.append(g)

    weekly = pl.concat(groups).sort(['symbol', 'date'])
    print(f'[weekly] Ichimoku/SMA computed  ({time.time()-t0:.1f}s)', flush=True)
    return weekly


# ---------------------------------------------------------------------------
# AVWAP computation
# ---------------------------------------------------------------------------

def compute_avwap(
    peaks: pl.DataFrame,
    daily: pl.DataFrame,
    earnings: pl.DataFrame,
) -> pl.DataFrame:
    """
    For each peak (symbol, date): compute AVWAP from last earnings event.

    Algorithm:
      1. Build per-symbol sorted lookup structures from daily (cumulative sums)
         and earnings (effective_dates).
      2. For each peak date, find the last earnings effective_date before it
         (bisect_left - 1).
      3. Find the first trading day >= anchor_date in daily.
      4. AVWAP = (cum_cv[peak] - cum_cv_before[anchor]) /
                 (cum_v[peak]  - cum_v_before[anchor])
         i.e., volume-weighted mean price from anchor bar (inclusive) to
         peak bar (inclusive).
      5. avwap_pct = (close - AVWAP) / AVWAP × 100
         Positive = price is above AVWAP = bullish relative to post-earnings anchor.
    """
    t0 = time.time()

    # Build per-symbol daily lookup dicts.
    # NOTE: polars group_by yields (key_tuple, group_df); unwrap to scalar key.
    sym_daily: dict[str, dict] = {}
    for key, grp in daily.group_by('symbol'):
        sym = key[0] if isinstance(key, tuple) else key
        g = grp.sort('date')
        sym_daily[sym] = {
            'dates':          g['date'].to_list(),
            'close':          g['close'].to_list(),
            'cum_cv':         g['cum_cv'].to_list(),
            'cum_cv_before': g['cum_cv_before'].to_list(),
            'cum_v':          g['cum_v'].to_list(),
            'cum_v_before':  g['cum_v_before'].to_list(),
        }

    # Build per-symbol earnings date arrays
    sym_earnings: dict[str, list[str]] = {}
    for key, grp in earnings.group_by('symbol'):
        sym = key[0] if isinstance(key, tuple) else key
        sym_earnings[sym] = sorted(grp['effective_date'].to_list())

    avwap_pcts:          list[float | None] = []
    avwap_anchor_dates:  list[str | None]   = []
    avwap_bars_list:     list[int | None]   = []

    syms  = peaks['symbol'].to_list()
    dates = peaks['date'].to_list()

    for sym, peak_date in zip(syms, dates):
        sd = sym_daily.get(sym)
        ern = sym_earnings.get(sym, [])

        if sd is None:
            avwap_pcts.append(None)
            avwap_anchor_dates.append(None)
            avwap_bars_list.append(None)
            continue

        # --- locate peak in daily (exact match required) ---
        peak_idx = bisect.bisect_left(sd['dates'], peak_date)
        if peak_idx >= len(sd['dates']) or sd['dates'][peak_idx] != peak_date:
            avwap_pcts.append(None)
            avwap_anchor_dates.append(None)
            avwap_bars_list.append(None)
            continue

        # --- last earnings before peak_date ---
        ern_pos = bisect.bisect_left(ern, peak_date) - 1
        if ern_pos < 0:
            # No prior earnings in the DB for this symbol
            avwap_pcts.append(None)
            avwap_anchor_dates.append(None)
            avwap_bars_list.append(None)
            continue

        anchor_date = ern[ern_pos]

        # --- first trading day on or after anchor_date ---
        anchor_idx = bisect.bisect_left(sd['dates'], anchor_date)
        if anchor_idx >= len(sd['dates']) or anchor_idx > peak_idx:
            # Anchor is beyond data range or after peak (shouldn't happen)
            avwap_pcts.append(None)
            avwap_anchor_dates.append(None)
            avwap_bars_list.append(None)
            continue

        # AVWAP = sum(cv from anchor to peak) / sum(vol from anchor to peak)
        cv_delta = sd['cum_cv'][peak_idx] - sd['cum_cv_before'][anchor_idx]
        v_delta  = sd['cum_v'][peak_idx]  - sd['cum_v_before'][anchor_idx]

        if v_delta <= 0:
            avwap_pcts.append(None)
            avwap_anchor_dates.append(None)
            avwap_bars_list.append(None)
            continue

        avwap = cv_delta / v_delta
        close = sd['close'][peak_idx]
        avwap_pct = round((close - avwap) / avwap * 100, 4) if avwap > 0 else None

        avwap_pcts.append(avwap_pct)
        avwap_anchor_dates.append(anchor_date)
        avwap_bars_list.append(peak_idx - anchor_idx + 1)

    print(f'[avwap] computed for {len(syms):,} peaks  ({time.time()-t0:.1f}s)', flush=True)

    return pl.DataFrame({
        'symbol':             syms,
        'date':               dates,
        'avwap_pct':          avwap_pcts,
        'avwap_anchor_date':  avwap_anchor_dates,
        'avwap_bars':         avwap_bars_list,
    })


# ---------------------------------------------------------------------------
# Weekly feature join
# ---------------------------------------------------------------------------

def join_weekly_features(peaks: pl.DataFrame, weekly: pl.DataFrame) -> pl.DataFrame:
    """
    Asof-join Ichimoku / 50W SMA / 52W HL features to peaks.

    Critical design choice: we look up the weekly bar at (scoring_date - 7 days)
    rather than the current-week bar.  This ensures we only use COMPLETED weekly
    bars, avoiding the partial-week instability that drove COHR's 86→52 whiplash
    (Priority #7).  A scoring date on Wednesday 2026-05-07 uses the weekly bar
    for 2026-04-28 (Monday prior week), not the still-accumulating 2026-05-04 bar.

    % distance columns use the convention: positive = price above the indicator
    (bullish), negative = price below (bearish).  Consistent with pct_from_ema50
    in the scoring algorithm.
    """
    t0 = time.time()

    # Compute the lookup date: 7 calendar days back puts us in the prior week
    peaks_lk = peaks.with_columns(
        (pl.col('date').str.to_date() - pl.duration(days=7))
        .dt.to_string('%Y-%m-%d')
        .alias('lk_date')
    )

    # Weekly feature columns we need
    weekly_sel = weekly.select([
        'symbol', 'date',
        'close', 'tenkan', 'kijun', 'span_a', 'span_b',
        'sma_50w', 'high_52w', 'low_52w',
    ]).rename({'close': 'wk_close', 'date': 'wk_date'})

    # Both sides must be sorted for join_asof
    peaks_sorted  = peaks_lk.sort(['symbol', 'lk_date'])
    weekly_sorted = weekly_sel.sort(['symbol', 'wk_date'])

    joined = peaks_sorted.join_asof(
        weekly_sorted,
        left_on='lk_date',
        right_on='wk_date',
        by='symbol',
        strategy='backward',
    )

    # Derived indicators
    joined = joined.with_columns([
        # Distance from Kijun-sen (26w base line — most-watched Ichimoku level)
        ((pl.col('wk_close') - pl.col('kijun')) / pl.col('kijun') * 100)
            .alias('price_vs_kijun_pct'),
        # Distance from Tenkan-sen (9w conversion — momentum signal)
        ((pl.col('wk_close') - pl.col('tenkan')) / pl.col('tenkan') * 100)
            .alias('price_vs_tenkan_pct'),
        # Tenkan vs Kijun direction: +1 bullish momentum, -1 bearish
        pl.when(pl.col('tenkan') > pl.col('kijun')).then(pl.lit(1, dtype=pl.Int8))
          .when(pl.col('tenkan') < pl.col('kijun')).then(pl.lit(-1, dtype=pl.Int8))
          .otherwise(pl.lit(0, dtype=pl.Int8))
          .alias('tenkan_kijun_dir'),
        # Distance from Span B (52w midpoint — long-term support/resistance)
        ((pl.col('wk_close') - pl.col('span_b')) / pl.col('span_b') * 100)
            .alias('price_vs_span_b_pct'),
        # 50-week SMA distance
        ((pl.col('wk_close') - pl.col('sma_50w')) / pl.col('sma_50w') * 100)
            .alias('sma50w_pct'),
        # 52-week high/low proximity (from weekly bars)
        # w52_high_pct < 0 means price is below the 52w high (negative = discount to high)
        ((pl.col('wk_close') - pl.col('high_52w')) / pl.col('high_52w') * 100)
            .alias('w52_high_pct'),
        # w52_low_pct > 0 means price is above the 52w low (positive = premium to low)
        ((pl.col('wk_close') - pl.col('low_52w')) / pl.col('low_52w') * 100)
            .alias('w52_low_pct'),
    ])

    # Cloud position: +1 = price above cloud, -1 = price below cloud, 0 = in cloud
    joined = joined.with_columns([
        pl.max_horizontal('span_a', 'span_b').alias('cloud_top'),
        pl.min_horizontal('span_a', 'span_b').alias('cloud_bot'),
    ])
    joined = joined.with_columns(
        pl.when(pl.col('cloud_top').is_null())
          .then(None)
          .when(pl.col('wk_close') > pl.col('cloud_top'))
          .then(pl.lit(1, dtype=pl.Int8))
          .when(pl.col('wk_close') < pl.col('cloud_bot'))
          .then(pl.lit(-1, dtype=pl.Int8))
          .otherwise(pl.lit(0, dtype=pl.Int8))
          .alias('cloud_position')
    )

    out = joined.select([
        'symbol', 'date', 'overall',
        'price_vs_kijun_pct', 'price_vs_tenkan_pct', 'tenkan_kijun_dir',
        'price_vs_span_b_pct', 'cloud_position',
        'sma50w_pct',
        'w52_high_pct', 'w52_low_pct',
    ])
    print(f'[weekly] asof-joined to peaks  ({time.time()-t0:.1f}s)', flush=True)
    return out


# ---------------------------------------------------------------------------
# Barrier outcomes
# ---------------------------------------------------------------------------

def load_barriers(start: str, cutoff: str) -> pl.DataFrame:
    """Load option-aligned barrier outcomes (30dte_opt, w=15, side='low' = calls)."""
    t0 = time.time()
    conn = sqlite3.connect(str(CACHE_DB))
    try:
        barriers = pl.read_database(
            f"""SELECT symbol, date, result, exit_return, mae_pct, mfe_pct
                FROM barrier_outcomes
                WHERE side = 'low'
                  AND barrier_set = '30dte_opt'
                  AND w_days = 15
                  AND date >= '{start}'
                  AND date <= '{cutoff}'""",
            connection=conn,
        )
    finally:
        conn.close()
    barriers = barriers.rename({
        'result':      'opt_result_15',
        'exit_return': 'opt_exit_return_15',
        'mae_pct':     'opt_mae_15',
        'mfe_pct':     'opt_mfe_15',
    })
    print(f'[barriers] {len(barriers):,} rows  ({time.time()-t0:.1f}s)', flush=True)
    return barriers


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter, cutoff_iso
    from database.models.core import AlgorithmVersion

    cutoff    = cutoff_iso()
    today     = date.today()
    start     = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    buf_start = (today - timedelta(days=LOOKBACK_DAYS + BUFFER_DAYS)).isoformat()

    av = AlgorithmVersion.get_active_scores_version()
    print(
        f'\n=== weekly_avwap Phase A build ===\n'
        f'  version:   v{av.id} ({av.git_commit[:8]})\n'
        f'  lookback:  {LOOKBACK_DAYS}d  (start={start})\n'
        f'  buffer:    {BUFFER_DAYS}d  (buf_start={buf_start})\n'
        f'  cutoff:    {cutoff}\n'
        f'  min_score: {MIN_SCORE}',
        flush=True,
    )

    # 1. Call peaks
    peaks = load_peaks(av.id, start, cutoff, min_score=MIN_SCORE)
    peaks = pre_cutoff_filter(peaks)
    assert_no_holdout_leak(peaks, 'weekly_avwap/build_features peaks')
    if len(peaks) == 0:
        print('[ERROR] No peaks found — check version / lookback.', flush=True)
        return

    # 2. Daily prices (with cumulative sums for AVWAP)
    daily = load_daily(buf_start, cutoff)

    # 3. Earnings dates
    earnings = load_earnings(cutoff)

    # 4. Weekly prices (with Ichimoku / SMA)
    weekly = load_weekly(buf_start, cutoff)

    # 5. AVWAP per peak
    avwap_df = compute_avwap(peaks, daily, earnings)

    # 6. Weekly features per peak (asof join)
    weekly_feats_df = join_weekly_features(peaks, weekly)

    # 7. Merge all feature groups (keep `overall` for cohort filtering downstream)
    df = peaks.join(avwap_df, on=['symbol', 'date'], how='left')
    df = df.join(weekly_feats_df.drop('overall'), on=['symbol', 'date'], how='left')

    # 8. Barrier outcomes
    barriers = load_barriers(start, cutoff)
    df = df.join(barriers, on=['symbol', 'date'], how='left')

    # 9. Final holdout assertion + write
    assert_no_holdout_leak(df, 'weekly_avwap/build_features final')

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = '' if MIN_SCORE == 70 else f'_min{MIN_SCORE}'
    out_path = OUT_DIR / f'calls_v{av.id}_{LOOKBACK_DAYS}d{suffix}.parquet'
    df.write_parquet(out_path)

    # --- summary ---
    n_avwap  = df['avwap_pct'].drop_nulls().len()
    n_kijun  = df['price_vs_kijun_pct'].drop_nulls().len()
    n_sma50w = df['sma50w_pct'].drop_nulls().len()
    n_bars = len(df)
    if 'opt_result_15' in df.columns:
        resolved = df['opt_result_15'].drop_nulls()
        # barrier_outcomes.result encoding: 1=win, 0=loss/expire
        n_wins = int((resolved == 1).sum()) if len(resolved) else 0
        n_resolved = len(resolved)
    else:
        n_wins = 0
        n_resolved = 0

    print(
        f'\n[done] {out_path} ({out_path.stat().st_size/1e6:.1f} MB)\n'
        f'  total peaks:   {n_bars:,}\n'
        f'  avwap_pct:     {n_avwap:,} non-null ({n_avwap/n_bars*100:.1f}%)\n'
        f'  kijun_pct:     {n_kijun:,} non-null ({n_kijun/n_bars*100:.1f}%)\n'
        f'  sma50w_pct:    {n_sma50w:,} non-null ({n_sma50w/n_bars*100:.1f}%)\n'
        f'  barrier wins:  {n_wins:,} / {n_resolved:,} resolved'
        + (f' ({n_wins/n_resolved*100:.1f}%)' if n_resolved else ''),
        flush=True,
    )
    print(df.describe(), flush=True)


if __name__ == '__main__':
    main()
