"""
Earnings-Window Option Pricing Spot Check
==========================================

Pulls real ATM option prices from `option_prices` over the last ~2 weeks where
the underlying has an EarningsDate within +/- 10 calendar days of the price date.
Splits into 15 DTE ATM and 30 DTE ATM cohorts and compares each row's actual
premium (% of spot) against our two production formulas:

  premium_pct(15 DTE) = 1.29 * sigma_daily
  premium_pct(30 DTE) = 1.82 * sigma_daily

sigma_daily = 60-day realized daily stdev of returns (%, not decimal).

Outputs:
  - Sample table (~20-30 rows per band) with: symbol, side, dte, spot, strike,
    moneyness, days_to_earn, IV, actual%, formula%, error pp, error %
  - Aggregate stats per band: N, mean actual%, mean formula%, mean error,
    RMSE, MAE, +/- bias.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
import statistics
from datetime import date, timedelta
from collections import defaultdict

import pandas as pd

from database.trader_database import DB

# ── Config ─────────────────────────────────────────────────────────────────
LOOKBACK_DAYS_FROM_LATEST = 14    # span for "last week-ish" of option prices
EARN_WINDOW_DAYS          = 10    # earnings within +/- N calendar days of price date
MAX_MONEYNESS             = 0.03  # |strike-close|/close
MIN_VOLUME                = 5
VOL_LOOKBACK              = 60    # trading days for sigma_daily
PROD_MULT_15              = 1.29
PROD_MULT_30              = 1.82
DTE_BANDS = [
    ('15 DTE', 13, 17, PROD_MULT_15),
    ('30 DTE', 25, 35, PROD_MULT_30),
]


def latest_op_date():
    row = DB.execute_sql("SELECT MAX(date) FROM option_prices").fetchone()
    return row[0]


def load_recent_option_rows(start_date: date, end_date: date) -> list[dict]:
    """ATM options in [start, end], filtered by DTE bands of interest."""
    print(f"Loading option_prices in [{start_date}, {end_date}]…", flush=True)
    sql = """
        SELECT
            op.date,
            o.symbol,
            o.option_type,
            o.strike_price,
            o.expiration_date,
            op.price,
            op.iv,
            op.volume,
            DATEDIFF(o.expiration_date, op.date) AS dte
        FROM option_prices op
        JOIN options o ON o.id = op.option_id
        WHERE op.date BETWEEN %s AND %s
          AND op.volume >= %s
          AND op.iv IS NOT NULL AND op.iv > 0.01
          AND DATEDIFF(o.expiration_date, op.date) BETWEEN 12 AND 36
        ORDER BY op.date DESC, o.symbol
    """
    cur = DB.execute_sql(sql, (start_date, end_date, MIN_VOLUME))
    rows = cur.fetchall()
    print(f"  fetched {len(rows):,} rows (pre-moneyness filter)", flush=True)
    out = []
    for r in rows:
        dt, sym, otype, strike, exp, price, iv, vol, dte = r
        out.append(dict(
            date=dt, symbol=sym, side=otype, strike=float(strike),
            expiration=exp, price=float(price), iv=float(iv),
            volume=int(vol), dte=int(dte),
        ))
    return out


def load_close_lookup(symbols: set, start_date: date) -> dict:
    """Returns {(symbol, date): close} for the given symbols from start_date back
    by VOL_LOOKBACK*2 days."""
    if not symbols:
        return {}
    syms = sorted(symbols)
    print(f"Loading price_history for {len(syms)} symbols since {start_date - timedelta(days=VOL_LOOKBACK*2 + 30)}…", flush=True)
    chunk_size = 60
    lookup = {}
    earliest = start_date - timedelta(days=VOL_LOOKBACK*2 + 30)
    for i in range(0, len(syms), chunk_size):
        chunk = syms[i:i+chunk_size]
        placeholders = ','.join(['%s'] * len(chunk))
        sql = f"""
            SELECT symbol, date, close FROM price_history
            WHERE symbol IN ({placeholders}) AND date >= %s
            ORDER BY symbol, date
        """
        cur = DB.execute_sql(sql, chunk + [earliest])
        for sym, d, close in cur.fetchall():
            lookup[(sym, d)] = float(close)
    print(f"  loaded {len(lookup):,} price_history rows", flush=True)
    return lookup


def compute_sigma_daily(close_lookup: dict) -> dict:
    """Compute 60d realized daily stdev (%) per (symbol, date). Returns
    {(symbol, date): sigma_pct}."""
    print("Computing 60d realized vol…", flush=True)
    rows = [{'symbol': s, 'date': d, 'close': c} for (s, d), c in close_lookup.items()]
    df = pd.DataFrame(rows).sort_values(['symbol', 'date']).reset_index(drop=True)
    df['ret'] = df.groupby('symbol')['close'].pct_change()
    df['sigma'] = (
        df.groupby('symbol')['ret']
          .transform(lambda x: x.rolling(VOL_LOOKBACK, min_periods=VOL_LOOKBACK).std())
        * 100
    )
    df = df.dropna(subset=['sigma'])
    df = df[df['sigma'] > 0]
    return {(r.symbol, r.date): r.sigma for r in df.itertuples(index=False)}


def load_earnings_map(symbols: set, start_date: date, end_date: date) -> dict:
    """Returns {symbol: [earn_date, ...]} for any earnings in
    [start_date - window, end_date + window]."""
    if not symbols:
        return {}
    syms = sorted(symbols)
    lo = start_date - timedelta(days=EARN_WINDOW_DAYS + 5)
    hi = end_date + timedelta(days=EARN_WINDOW_DAYS + 5)
    print(f"Loading earnings_dates for {len(syms)} syms in [{lo}, {hi}]…", flush=True)
    chunk_size = 100
    out = defaultdict(list)
    for i in range(0, len(syms), chunk_size):
        chunk = syms[i:i+chunk_size]
        placeholders = ','.join(['%s'] * len(chunk))
        sql = f"""
            SELECT symbol, date, call_time
            FROM earnings_dates
            WHERE symbol IN ({placeholders}) AND date BETWEEN %s AND %s
        """
        cur = DB.execute_sql(sql, chunk + [lo, hi])
        for sym, d, ct in cur.fetchall():
            out[sym].append((d, ct))
    total = sum(len(v) for v in out.values())
    print(f"  loaded {total:,} earnings rows for {len(out):,} symbols", flush=True)
    return dict(out)


def nearest_earn(earn_list, ref_date):
    """Return (earn_date, call_time, signed_days) for nearest earnings; None if list empty.
    signed_days > 0 means earnings AFTER ref_date (upcoming)."""
    if not earn_list:
        return None
    best = None
    best_abs = 10**9
    for ed, ct in earn_list:
        diff = (ed - ref_date).days
        if abs(diff) < best_abs:
            best_abs = abs(diff)
            best = (ed, ct, diff)
    return best


def fmt_row(r):
    return (
        f"{r['date']!s:>10} {r['symbol']:<6} {r['side'][0].upper()} "
        f"DTE={r['dte']:>2} spot={r['close']:>7.2f} strike={r['strike']:>7.2f} "
        f"mny={r['moneyness']:>+5.2f}% earn{r['days_to_earn']:>+3}d "
        f"IV={r['iv']*100:>5.1f}% sig={r['sigma']:>4.2f}% | "
        f"actual={r['actual_pct']:>5.2f}% pred={r['pred_pct']:>5.2f}% "
        f"err={r['err_pp']:>+5.2f}pp ({r['err_rel']:>+6.1f}%)"
    )


def main():
    DB.connect(reuse_if_open=True)

    end_date = latest_op_date()
    if end_date is None:
        print("No option_prices rows in DB."); return
    start_date = end_date - timedelta(days=LOOKBACK_DAYS_FROM_LATEST)
    print(f"Date window: [{start_date}, {end_date}]\n")

    rows = load_recent_option_rows(start_date, end_date)
    if not rows:
        print("No option rows in window."); return

    # apply DTE band filter early
    keep = []
    for r in rows:
        for label, lo, hi, mult in DTE_BANDS:
            if lo <= r['dte'] <= hi:
                r['band'] = label
                r['band_mult'] = mult
                keep.append(r); break
    rows = keep
    print(f"  {len(rows):,} rows after DTE band filter", flush=True)

    # load close + sigma + earnings
    syms = {r['symbol'] for r in rows}
    close_lookup = load_close_lookup(syms, start_date)
    sigma_lookup = compute_sigma_daily(close_lookup)
    earn_map = load_earnings_map(syms, start_date, end_date)

    # enrich + filter (ATM + earnings-window)
    enriched = []
    skipped_no_close = skipped_no_sigma = skipped_no_earn = skipped_otm = 0
    for r in rows:
        key = (r['symbol'], r['date'])
        close = close_lookup.get(key)
        if close is None or close <= 0:
            skipped_no_close += 1; continue
        mny = (r['strike'] - close) / close
        if abs(mny) > MAX_MONEYNESS:
            skipped_otm += 1; continue
        sigma = sigma_lookup.get(key)
        if sigma is None or sigma <= 0:
            skipped_no_sigma += 1; continue
        ne = nearest_earn(earn_map.get(r['symbol'], []), r['date'])
        if ne is None or abs(ne[2]) > EARN_WINDOW_DAYS:
            skipped_no_earn += 1; continue
        actual_pct = r['price'] / close * 100
        pred_pct   = r['band_mult'] * sigma
        err_pp     = actual_pct - pred_pct
        err_rel    = (actual_pct / pred_pct - 1.0) * 100 if pred_pct > 0 else float('nan')
        enriched.append({
            **r,
            'close': close,
            'moneyness': mny * 100,
            'sigma': sigma,
            'earn_date': ne[0],
            'call_time': ne[1],
            'days_to_earn': ne[2],
            'actual_pct': actual_pct,
            'pred_pct': pred_pct,
            'err_pp': err_pp,
            'err_rel': err_rel,
        })
    print(f"\nFilter funnel: drop {skipped_no_close} no-close, {skipped_otm} OTM, "
          f"{skipped_no_sigma} no-sigma, {skipped_no_earn} no-earn-near")
    print(f"Final cohort: {len(enriched):,} rows\n")

    if not enriched:
        return

    # ── per-band reporting ─────────────────────────────────────────────
    SEP = '─' * 110
    DSEP = '═' * 110
    by_band = defaultdict(list)
    for e in enriched:
        by_band[(e['band'], e['side'])].append(e)

    for band_key in [('15 DTE', 'call'), ('15 DTE', 'put'),
                     ('30 DTE', 'call'), ('30 DTE', 'put')]:
        rows_b = by_band.get(band_key, [])
        if not rows_b: continue
        rows_b.sort(key=lambda r: (abs(r['days_to_earn']), r['symbol']))
        band, side = band_key
        print(DSEP)
        print(f"  {band} ATM {side.upper()}S near earnings (N={len(rows_b)})")
        print(DSEP)
        # show up to 25 sample rows, prefer rows closest to earnings
        for r in rows_b[:25]:
            print(fmt_row(r))
        if len(rows_b) > 25:
            print(f"  … {len(rows_b)-25} more rows")

        # aggregate
        actuals = [r['actual_pct'] for r in rows_b]
        preds   = [r['pred_pct']   for r in rows_b]
        errs_pp = [r['err_pp']     for r in rows_b]
        errs_rel = [r['err_rel'] for r in rows_b if not math.isnan(r['err_rel'])]
        n = len(rows_b)
        rmse = math.sqrt(sum(e*e for e in errs_pp) / n)
        mae  = sum(abs(e) for e in errs_pp) / n
        bias = sum(errs_pp) / n
        print(SEP)
        print(f"  AGG  N={n}  actual={statistics.mean(actuals):.3f}%  "
              f"pred={statistics.mean(preds):.3f}%  "
              f"bias={bias:+.3f}pp  MAE={mae:.3f}pp  RMSE={rmse:.3f}pp  "
              f"mean_err_rel={statistics.mean(errs_rel):+.1f}%")
        print()

    # ── overall side-by-side band comparison ───────────────────────────
    print(DSEP)
    print("  SUMMARY  (formula = mult × sigma_daily, sigma = 60d realized stdev)")
    print(DSEP)
    fmt = '{:<10}  {:<5}  {:>5}  {:>10}  {:>10}  {:>10}  {:>9}  {:>9}'
    print(fmt.format('Band', 'Side', 'N', 'Actual %', 'Pred %', 'Bias pp', 'MAE pp', 'RMSE pp'))
    print(SEP)
    for band, side in [('15 DTE','call'),('15 DTE','put'),('30 DTE','call'),('30 DTE','put')]:
        rs = by_band.get((band, side), [])
        if not rs:
            print(fmt.format(band, side, 0, '—', '—', '—', '—', '—')); continue
        n = len(rs)
        a = statistics.mean(r['actual_pct'] for r in rs)
        p = statistics.mean(r['pred_pct']   for r in rs)
        bias = statistics.mean(r['err_pp']  for r in rs)
        mae  = statistics.mean(abs(r['err_pp']) for r in rs)
        rmse = math.sqrt(statistics.mean(r['err_pp']**2 for r in rs))
        print(fmt.format(band, side, n, f'{a:.3f}', f'{p:.3f}',
                         f'{bias:+.3f}', f'{mae:.3f}', f'{rmse:.3f}'))
    print()

    DB.close()


if __name__ == '__main__':
    main()
