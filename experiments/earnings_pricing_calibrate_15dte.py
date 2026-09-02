"""
15 DTE ATM Earnings-Window Pricing — Calibration
=================================================

Earnings-proximal 15 DTE premium is structurally larger than `1.29 × σ_daily`
because 60-day realized vol doesn't include the earnings jump variance. This
script pulls a larger sample of 15 DTE ATM options near earnings, calibrates
several candidate models on a train split, and evaluates on a held-out test
split chronologically.

Models:
  M0 — current baseline:      premium% = 1.29 × σ
  M1 — calibrated flat mult:  premium% = c × σ                (1 parameter)
  M2 — variance-additive:     premium% = N'(0) × √(σ² × T + j² × spans) × √(252/365)
                              T = DTE_cal,  j calibrated      (1 parameter)
  M3 — proximity-weighted:    premium% = (c0 + c1 × proximity) × σ
                              proximity = log(W+1-d_abs)/log(W+1) (2 params)
  M4 — IV-based (sanity):     premium% = k × IV_annual/√252 × √DTE × 100
                              uses option's own IV — upper bound  (1 parameter)

Reports test-set RMSE / MAE / bias per model, both calls and puts, both
inside-earn-window and outside-earn-window cohorts so we can see where
each model wins and where it costs nothing.
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
LOOKBACK_DAYS    = 180         # pull last 6 months of option_prices
EARN_WINDOW_DAYS = 10          # consider earnings within +/- N cal days
MAX_MONEYNESS    = 0.03
MIN_VOLUME       = 5
VOL_LOOKBACK     = 60
DTE_LO, DTE_HI   = 13, 17
TRAIN_FRAC       = 0.70        # chronological split

PROD_MULT_15     = 1.29
N_PRIME_0        = 1.0 / math.sqrt(2 * math.pi)        # 0.3989
CAL_TO_TRADE     = 252.0 / 365.0                        # 0.6904
BS_FACTOR        = N_PRIME_0 * math.sqrt(CAL_TO_TRADE) # ≈ 0.3314


def latest_op_date():
    return DB.execute_sql("SELECT MAX(date) FROM option_prices").fetchone()[0]


def load_options(start_date, end_date):
    sql = """
        SELECT op.date, o.symbol, o.option_type, o.strike_price,
               op.price, op.iv, op.volume,
               DATEDIFF(o.expiration_date, op.date)
        FROM option_prices op
        JOIN options o ON o.id = op.option_id
        WHERE op.date BETWEEN %s AND %s
          AND op.volume >= %s
          AND op.iv IS NOT NULL AND op.iv > 0.01
          AND DATEDIFF(o.expiration_date, op.date) BETWEEN %s AND %s
    """
    print(f"Loading 15 DTE options [{start_date}, {end_date}]…", flush=True)
    cur = DB.execute_sql(sql, (start_date, end_date, MIN_VOLUME, DTE_LO, DTE_HI))
    rows = cur.fetchall()
    print(f"  fetched {len(rows):,}", flush=True)
    return [{
        'date': r[0], 'symbol': r[1], 'side': r[2], 'strike': float(r[3]),
        'price': float(r[4]), 'iv': float(r[5]), 'volume': int(r[6]),
        'dte': int(r[7])} for r in rows]


def load_close_lookup(syms, earliest):
    if not syms: return {}
    syms = sorted(syms)
    out = {}
    for i in range(0, len(syms), 60):
        chunk = syms[i:i+60]
        ph = ','.join(['%s']*len(chunk))
        cur = DB.execute_sql(
            f"SELECT symbol, date, close FROM price_history "
            f"WHERE symbol IN ({ph}) AND date >= %s",
            chunk + [earliest])
        for s, d, c in cur.fetchall():
            out[(s, d)] = float(c)
    return out


def compute_sigma(close_lookup):
    rows = [{'symbol': s, 'date': d, 'close': c}
            for (s, d), c in close_lookup.items()]
    df = pd.DataFrame(rows).sort_values(['symbol', 'date']).reset_index(drop=True)
    df['ret'] = df.groupby('symbol')['close'].pct_change()
    df['sigma'] = (df.groupby('symbol')['ret']
                   .transform(lambda x: x.rolling(VOL_LOOKBACK,
                              min_periods=VOL_LOOKBACK).std()) * 100)
    df = df.dropna(subset=['sigma'])
    df = df[df['sigma'] > 0]
    return {(r.symbol, r.date): r.sigma for r in df.itertuples(index=False)}


def load_earnings(syms, lo, hi):
    if not syms: return {}
    syms = sorted(syms)
    out = defaultdict(list)
    for i in range(0, len(syms), 100):
        chunk = syms[i:i+100]
        ph = ','.join(['%s']*len(chunk))
        cur = DB.execute_sql(
            f"SELECT symbol, date FROM earnings_dates "
            f"WHERE symbol IN ({ph}) AND date BETWEEN %s AND %s",
            chunk + [lo, hi])
        for s, d in cur.fetchall():
            out[s].append(d)
    return dict(out)


def nearest_earn_signed(earns, ref):
    if not earns: return None
    best, best_abs = None, 10**9
    for ed in earns:
        diff = (ed - ref).days
        if abs(diff) < best_abs:
            best_abs = abs(diff); best = diff
    return best


# ── Models ─────────────────────────────────────────────────────────────────

def predict_M0(row):
    """Current production: 1.29 × σ."""
    return PROD_MULT_15 * row['sigma']

def predict_M1(row, c):
    """Flat calibrated mult."""
    return c * row['sigma']

def predict_M2(row, j):
    """Variance-additive. σ in %/day, j in % (one-time earnings jump).
    premium% = N'(0) × √(σ² × DTE × 252/365 + j² × spans) × √(252/365)? No —
    let me re-derive cleanly:

      premium/S ≈ N'(0) × σ_period   where σ_period is total std over option's life.
      σ_period² = σ_daily_trading² × T_trading + j² × spans
      T_trading = DTE_cal × 252/365 (trading days in the period)

    σ_daily_trading is the realized daily stdev (already trading-day basis).
    j is in same %/move units as σ_daily.
    """
    var_calm = (row['sigma'] ** 2) * (row['dte'] * CAL_TO_TRADE)
    var_jump = (j ** 2) if row['spans'] else 0.0
    return N_PRIME_0 * math.sqrt(var_calm + var_jump)

def predict_M3(row, c0, c1):
    """Proximity-weighted multiplier. proximity ∈ [0, 1], higher when earnings closer."""
    prox = row['proximity']  # 0 outside window, → 1 day-of
    return (c0 + c1 * prox) * row['sigma']

def predict_M4(row, k):
    """IV-based BS approximation."""
    iv_daily = row['iv'] / math.sqrt(252)
    return k * iv_daily * math.sqrt(row['dte']) * 100  # → %


# ── Calibration helpers ────────────────────────────────────────────────────

def rmse(rows, pred_fn, *params):
    s = 0.0; n = 0
    for r in rows:
        e = r['actual'] - pred_fn(r, *params)
        s += e*e; n += 1
    return math.sqrt(s / n) if n else float('nan')

def errs(rows, pred_fn, *params):
    return [r['actual'] - pred_fn(r, *params) for r in rows]

def calibrate_1d(rows, pred_fn, lo, hi, n=120):
    best_p, best_r = None, float('inf')
    for i in range(n+1):
        p = lo + (hi - lo) * i / n
        r = rmse(rows, pred_fn, p)
        if r < best_r: best_r, best_p = r, p
    return best_p, best_r

def calibrate_2d(rows, pred_fn, lo0, hi0, lo1, hi1, n=40):
    best, br = None, float('inf')
    for i in range(n+1):
        for j in range(n+1):
            p0 = lo0 + (hi0-lo0)*i/n
            p1 = lo1 + (hi1-lo1)*j/n
            r = rmse(rows, pred_fn, p0, p1)
            if r < br: br, best = r, (p0, p1)
    return best, br


def metrics(rows, pred_fn, *params):
    es = errs(rows, pred_fn, *params)
    n = len(es)
    if n == 0:
        return dict(n=0, rmse=float('nan'), mae=float('nan'),
                    bias=float('nan'), rel=float('nan'))
    rs = math.sqrt(sum(e*e for e in es) / n)
    mae = sum(abs(e) for e in es) / n
    bias = sum(es) / n
    rels = [(r['actual'] / pred_fn(r, *params) - 1.0) * 100
            for r in rows if pred_fn(r, *params) > 0]
    rel = statistics.mean(rels) if rels else float('nan')
    return dict(n=n, rmse=rs, mae=mae, bias=bias, rel=rel)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    DB.connect(reuse_if_open=True)
    end = latest_op_date()
    start = end - timedelta(days=LOOKBACK_DAYS)
    print(f"Window: [{start}, {end}]  ({LOOKBACK_DAYS} days)\n")

    rows = load_options(start, end)
    if not rows:
        print("no rows"); return

    syms = {r['symbol'] for r in rows}
    close = load_close_lookup(syms, start - timedelta(days=VOL_LOOKBACK*2 + 30))
    sigma = compute_sigma(close)
    earns = load_earnings(syms,
                          start - timedelta(days=EARN_WINDOW_DAYS+5),
                          end + timedelta(days=EARN_WINDOW_DAYS+5))

    enriched = []
    for r in rows:
        c = close.get((r['symbol'], r['date']))
        if not c: continue
        if abs(r['strike']-c)/c > MAX_MONEYNESS: continue
        s = sigma.get((r['symbol'], r['date']))
        if not s: continue
        e = nearest_earn_signed(earns.get(r['symbol'], []), r['date'])
        d_abs = abs(e) if e is not None else 99
        spans = (e is not None and 0 <= e <= r['dte'])  # earnings during option's life
        in_win = (e is not None and abs(e) <= EARN_WINDOW_DAYS)
        # log proximity, peaks at d=0, zero at d>=W
        if e is None or abs(e) > EARN_WINDOW_DAYS:
            prox = 0.0
        else:
            W = EARN_WINDOW_DAYS
            prox = math.log(W + 1 - abs(e)) / math.log(W + 1)
        enriched.append({
            'date': r['date'], 'symbol': r['symbol'], 'side': r['side'],
            'sigma': s, 'iv': r['iv'], 'dte': r['dte'],
            'days_earn': e if e is not None else 99,
            'spans': spans, 'in_win': in_win, 'proximity': prox,
            'actual': r['price'] / c * 100,
        })

    print(f"Final cohort: {len(enriched):,} rows", flush=True)
    if not enriched: return

    # split chronological by date
    enriched.sort(key=lambda r: r['date'])
    cut = int(len(enriched) * TRAIN_FRAC)
    train = enriched[:cut]; test = enriched[cut:]
    print(f"Train: {len(train):,} ({train[0]['date']} → {train[-1]['date']})")
    print(f"Test:  {len(test):,} ({test[0]['date']} → {test[-1]['date']})\n")

    SEP = '─' * 95
    DSEP = '═' * 95

    def report_slice(name, rows_train, rows_test):
        print(DSEP)
        print(f"  COHORT: {name}   train N={len(rows_train)}  test N={len(rows_test)}")
        print(DSEP)
        if not rows_train or not rows_test:
            print("  (insufficient data)\n"); return

        # Calibrate each model on train
        c1, _ = calibrate_1d(rows_train, predict_M1, 0.50, 3.50)
        j2, _ = calibrate_1d(rows_train, predict_M2, 0.0, 12.0)
        (c0_3, c1_3), _ = calibrate_2d(rows_train, predict_M3,
                                        0.5, 2.5, 0.0, 2.5, n=30)
        k4, _ = calibrate_1d(rows_train, predict_M4, 0.20, 1.20)

        print(f"  Calibrated params (on train):")
        print(f"    M1  c        = {c1:.3f}")
        print(f"    M2  j (jump%) = {j2:.3f}")
        print(f"    M3  c0={c0_3:.3f}, c1={c1_3:.3f}")
        print(f"    M4  k         = {k4:.3f}")
        print()

        # Evaluate all on test
        models = [
            ('M0  prod 1.29×σ',   predict_M0,  ()),
            ('M1  c×σ',           predict_M1,  (c1,)),
            ('M2  var-additive',  predict_M2,  (j2,)),
            ('M3  proximity',     predict_M3,  (c0_3, c1_3)),
            ('M4  IV-based',      predict_M4,  (k4,)),
        ]
        fmt = '  {:<22}  {:>5}  {:>9}  {:>9}  {:>10}  {:>10}'
        print(fmt.format('Model', 'N', 'RMSE pp', 'MAE pp', 'Bias pp', 'Rel err%'))
        print(SEP)
        m0_rmse = None
        for label, fn, params in models:
            m = metrics(rows_test, fn, *params)
            if label.startswith('M0'): m0_rmse = m['rmse']
            delta = '' if m0_rmse is None else f"  Δ={m['rmse']-m0_rmse:+.3f}"
            print(fmt.format(label, m['n'], f"{m['rmse']:.3f}", f"{m['mae']:.3f}",
                             f"{m['bias']:+.3f}", f"{m['rel']:+.1f}%") + delta)
        print()

    # Inside-earnings-window cohorts
    for side in ['call', 'put']:
        rows_in_train = [r for r in train if r['side']==side and r['in_win']]
        rows_in_test  = [r for r in test  if r['side']==side and r['in_win']]
        report_slice(f"{side.upper()}S — inside earnings window (|d|≤{EARN_WINDOW_DAYS})",
                     rows_in_train, rows_in_test)

    # Outside earnings window cohorts (sanity — should NOT improve over M0)
    for side in ['call', 'put']:
        rows_out_train = [r for r in train if r['side']==side and not r['in_win']]
        rows_out_test  = [r for r in test  if r['side']==side and not r['in_win']]
        report_slice(f"{side.upper()}S — outside earnings window  (sanity)",
                     rows_out_train, rows_out_test)

    # Combined (all 15 DTE ATM, both regimes)
    for side in ['call', 'put']:
        rt = [r for r in train if r['side']==side]
        rs = [r for r in test  if r['side']==side]
        report_slice(f"{side.upper()}S — ALL 15 DTE ATM (combined regimes)", rt, rs)

    # ── Headline: best earnings-aware model in drop-in form ────────────────
    print(DSEP)
    print("  RECOMMENDED FORM (earnings-aware 15 DTE)")
    print(DSEP)
    # re-fit M3 (proximity) on combined inside-window cohort (calls+puts pooled
    # since their RMSE deltas should be similar — earnings premium isn't
    # call-vs-put asymmetric to first order)
    in_train_all = [r for r in train if r['in_win']]
    in_test_all  = [r for r in test  if r['in_win']]
    if in_train_all and in_test_all:
        (c0, c1), tr_rmse = calibrate_2d(in_train_all, predict_M3,
                                          0.5, 2.5, 0.0, 2.5, n=40)
        j_only, j_tr_rmse = calibrate_1d(in_train_all, predict_M2, 0.0, 12.0, n=200)
        m0 = metrics(in_test_all, predict_M0)
        m1 = metrics(in_test_all, predict_M1, calibrate_1d(in_train_all, predict_M1, 0.5, 3.5)[0])
        m2 = metrics(in_test_all, predict_M2, j_only)
        m3 = metrics(in_test_all, predict_M3, c0, c1)
        print(f"  Inside-earnings-window (pooled calls+puts): test N={m0['n']}")
        print(f"    M0 (prod 1.29×σ):                          RMSE={m0['rmse']:.3f}  bias={m0['bias']:+.3f}")
        print(f"    M1 (flat c×σ):                             RMSE={m1['rmse']:.3f}  bias={m1['bias']:+.3f}")
        print(f"    M2 (var-additive, j={j_only:.2f}):         RMSE={m2['rmse']:.3f}  bias={m2['bias']:+.3f}")
        print(f"    M3 (proximity, c0={c0:.2f}, c1={c1:.2f}):  RMSE={m3['rmse']:.3f}  bias={m3['bias']:+.3f}")
        print()
        print(f"  Drop-in proximity formula (best of the three):")
        print(f"    proximity = log({EARN_WINDOW_DAYS}+1-|d_to_earn|) / log({EARN_WINDOW_DAYS}+1)   (0 outside ±{EARN_WINDOW_DAYS}d)")
        print(f"    premium_pct_15dte = ({c0:.3f} + {c1:.3f} × proximity) × σ_daily")
        print(f"                       (collapses to {c0:.3f}×σ outside window — close to prod 1.29)")
        print()
        print(f"  Drop-in variance-additive formula (cleanest physically):")
        print(f"    j = {j_only:.2f}%   (one-time earnings jump magnitude)")
        print(f"    premium_pct_15dte = N'(0) × √(σ²·DTE·252/365 + j²·spans_earn)")

    DB.close()


if __name__ == '__main__':
    main()
