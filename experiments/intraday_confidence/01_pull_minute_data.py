"""
Phase 1b: Pull yfinance 1-min OHLCV for selected stocks (last 7 days).

Stocks: COHR (case study) + diverse v39 signals (calls, puts).
yfinance limits: interval='1m' yields up to 7 days of history.
Data saved as parquet to .cache/intraday_minute/ — no DB writes.
"""
import os
import sys
import time as time_mod
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

# Pin to v39 — v40 recalc may be running in another session
os.environ.setdefault('ALGORITHM_VERSION_PIN', '200f33a')

CACHE_DIR = Path('.cache/intraday_minute')
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Diverse signal universe: COHR (case) + recent v39 high calls + puts
SYMBOLS = [
    'COHR',                                 # case study — today's THIN_AIR ghost
    'NET', 'TER', 'DINO', 'ADM', 'KRYS',    # recent v39 calls (>=80)
    'IP', 'PDYN', 'CLX', 'XRAY',            # recent v39 puts (<=25)
]

PERIOD = '7d'   # yfinance 1m limit

def pull_one(sym: str) -> pd.DataFrame | None:
    out_path = CACHE_DIR / f'{sym}_1m.parquet'
    if out_path.exists():
        return pd.read_parquet(out_path)
    for attempt in range(3):
        try:
            df = yf.download(sym, interval='1m', period=PERIOD,
                             prepost=False, progress=False, auto_adjust=False)
            if df is None or len(df) == 0:
                print(f'  {sym}: empty')
                return None
            # yf returns multiindex cols when single symbol; flatten
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
            # localize index — yf returns ET already for 1m
            df = df.reset_index().rename(columns={'Datetime': 'ts'})
            df['ts'] = pd.to_datetime(df['ts'], utc=True).dt.tz_convert('America/New_York')
            df = df[['ts', 'Open', 'High', 'Low', 'Close', 'Volume']]
            df.columns = ['ts', 'open', 'high', 'low', 'close', 'volume']
            df.to_parquet(out_path, index=False)
            print(f'  {sym}: {len(df)} rows  {df["ts"].min()} -> {df["ts"].max()}')
            return df
        except Exception as e:
            print(f'  {sym} attempt {attempt+1}: {e}')
            time_mod.sleep(2)
    return None


def main():
    print(f'pulling 1-min for {len(SYMBOLS)} symbols, period={PERIOD}')
    out = {}
    for s in SYMBOLS:
        df = pull_one(s)
        out[s] = df
        time_mod.sleep(0.5)  # polite throttle

    summary = []
    for s, df in out.items():
        if df is None:
            summary.append((s, 0, None, None))
        else:
            days = df['ts'].dt.date.nunique()
            summary.append((s, len(df), df['ts'].min(), df['ts'].max()))

    print('\nSummary:')
    print(f'{"sym":<6} {"rows":>6} {"first":<25} {"last":<25} days')
    for s, n, mn, mx in summary:
        print(f'{s:<6} {n:>6} {str(mn):<25} {str(mx):<25}')


if __name__ == '__main__':
    main()
