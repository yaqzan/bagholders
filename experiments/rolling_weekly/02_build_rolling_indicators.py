"""
Compute rolling weekly indicators per (symbol, scoring_date) using Option C
(non-overlapping 5-day ladder, ending date - 1).

For each scoring date T:
  ladder = closes at trading-day indices [T-1, T-6, T-11, ..., T-1-5*(L-1)]
  weekly_rsi    = talib.RSI(ladder, 14)[-1]
  weekly_macd   = talib.MACD(ladder, 12, 26, 9)[0][-1]   # MACD line
  weekly_macd_h = MACD - signal                           # histogram
  weekly_ema50  = talib.EMA(ladder, 50)[-1]
  weekly_ema200 = talib.EMA(ladder, 200)[-1]    # NaN if ladder too short

Ladder length = 200 (gives EMA50 full convergence, EMA200 partial). Stocks with
< 200 days of history have NaN for distant EMAs.

For prev-week values (used by calculate_weekly_adjustment momentum_bias):
  prev_ladder = closes at indices [T-6, T-11, T-16, ...]   # shifted 5 days
  Same indicators on prev_ladder.

Output: .cache/rolling_weekly/indicators.parquet
  columns: symbol, date, w_rsi, w_macd, w_macd_h, prev_w_rsi, prev_w_macd
"""
from __future__ import annotations
import io
import sys
import time
from pathlib import Path

if __name__ == '__main__' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import polars as pl
import talib

CACHE_DIR = Path('.cache/rolling_weekly')
DAILY_PQ  = CACHE_DIR / 'daily.parquet'
OUT_PQ    = CACHE_DIR / 'indicators.parquet'

LADDER_LEN = 200   # 200 trailing 5-day bars = 1000 trading days = ~4 years
RSI_PERIOD = 14
MACD_FAST  = 12
MACD_SLOW  = 26
MACD_SIG   = 9


def compute_for_stock(closes: np.ndarray, dates: list, sym: str):
    """For each index i with sufficient history, compute rolling weekly RSI/MACD.

    closes: 1-D array, sorted by date ascending
    dates:  list of date strings (parallel to closes)

    Returns list of dicts (date, w_rsi, w_macd, w_macd_h, prev_w_rsi, prev_w_macd, prev_w_macd_h)
    """
    N = len(closes)
    # Need ladder ending at i-1 (index i-1 is most recent in ladder)
    # Bar k uses indices [i - 1 - 5*k - 4 .. i - 1 - 5*k] (close at i - 1 - 5*k)
    # Ladder closes (oldest → newest): closes[i - 1 - 5*(L-1)], ..., closes[i - 6], closes[i - 1]
    # For prev: closes[i - 6 - 5*(L-1)], ..., closes[i - 11], closes[i - 6]

    min_idx = 1 + 5 * (LADDER_LEN - 1)   # 1 + 5*199 = 996

    out = []
    for i in range(min_idx, N):
        # Build current ladder (oldest first)
        ladder_idx = [i - 1 - 5 * k for k in range(LADDER_LEN - 1, -1, -1)]
        ladder = closes[ladder_idx]
        if np.any(np.isnan(ladder)):
            continue

        # talib RSI
        rsi_arr = talib.RSI(ladder, timeperiod=RSI_PERIOD)
        macd_arr, macd_sig_arr, macd_hist_arr = talib.MACD(
            ladder, fastperiod=MACD_FAST, slowperiod=MACD_SLOW, signalperiod=MACD_SIG)

        w_rsi = float(rsi_arr[-1]) if not np.isnan(rsi_arr[-1]) else None
        w_macd = float(macd_arr[-1]) if not np.isnan(macd_arr[-1]) else None
        w_macd_h = float(macd_hist_arr[-1]) if not np.isnan(macd_hist_arr[-1]) else None

        # Prev ladder (shifted 5 days back: most recent close at i - 6)
        if i - 1 - 5 * (LADDER_LEN - 1) - 5 < 0:
            prev_w_rsi = None
            prev_w_macd = None
            prev_w_macd_h = None
        else:
            prev_idx = [i - 6 - 5 * k for k in range(LADDER_LEN - 1, -1, -1)]
            prev_ladder = closes[prev_idx]
            if np.any(np.isnan(prev_ladder)):
                prev_w_rsi = None
                prev_w_macd = None
                prev_w_macd_h = None
            else:
                prev_rsi_arr = talib.RSI(prev_ladder, timeperiod=RSI_PERIOD)
                prev_macd_arr, prev_macd_sig_arr, prev_macd_hist_arr = talib.MACD(
                    prev_ladder, fastperiod=MACD_FAST, slowperiod=MACD_SLOW, signalperiod=MACD_SIG)
                prev_w_rsi = float(prev_rsi_arr[-1]) if not np.isnan(prev_rsi_arr[-1]) else None
                prev_w_macd = float(prev_macd_arr[-1]) if not np.isnan(prev_macd_arr[-1]) else None
                prev_w_macd_h = float(prev_macd_hist_arr[-1]) if not np.isnan(prev_macd_hist_arr[-1]) else None

        out.append({
            'symbol': sym,
            'date':   dates[i],
            'w_rsi':  w_rsi,
            'w_macd': w_macd,
            'w_macd_h': w_macd_h,
            'prev_w_rsi':  prev_w_rsi,
            'prev_w_macd': prev_w_macd,
            'prev_w_macd_h': prev_w_macd_h,
        })

    return out


def main():
    print(f'Loading daily cache from {DAILY_PQ}...', flush=True)
    df = pl.read_parquet(DAILY_PQ)
    print(f'  {len(df):,} daily rows, {df["symbol"].n_unique()} symbols', flush=True)

    print(f'\nComputing rolling weekly indicators per stock...', flush=True)
    print(f'  ladder_len={LADDER_LEN} (need ~{1 + 5*(LADDER_LEN-1)} days history per scoring date)', flush=True)

    all_rows = []
    syms = sorted(df['symbol'].unique())
    t0 = time.time()
    for j, sym in enumerate(syms):
        sub = df.filter(pl.col('symbol') == sym).sort('date')
        if len(sub) < 1 + 5 * (LADDER_LEN - 1):
            continue
        closes = sub['close'].to_numpy().astype(np.float64)
        dates  = sub['date'].to_list()
        rows = compute_for_stock(closes, dates, sym)
        all_rows.extend(rows)

        if (j + 1) % 50 == 0 or (j + 1) == len(syms):
            elapsed = time.time() - t0
            rate = (j + 1) / elapsed
            eta = (len(syms) - j - 1) / rate if rate > 0 else 0
            print(f'  [{j+1:>4}/{len(syms)}] {sym}: {len(all_rows):,} rows so far '
                  f'({elapsed:.0f}s elapsed, ~{eta:.0f}s remaining)', flush=True)

    print(f'\n  total rows: {len(all_rows):,} in {time.time()-t0:.1f}s', flush=True)

    out = pl.DataFrame(all_rows)
    out.write_parquet(OUT_PQ)
    print(f'\n[OK] saved {OUT_PQ}', flush=True)
    print(f'  symbols: {out["symbol"].n_unique()}')
    print(f'  date range: {out["date"].min()} -> {out["date"].max()}')


if __name__ == '__main__':
    main()
