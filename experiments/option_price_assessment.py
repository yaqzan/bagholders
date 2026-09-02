"""
Option Price Assessment & Bayesian Multiplier Calibration
=========================================================
Goal: Compare actual ATM option prices (from the DB) against our σ-based
approximation formulas, then Bayesian-sweep to find better multipliers.

Current approximations (from CLAUDE.md / monte_carlo.py):
  15 DTE ATM call premium ≈ 1.29 × σ_daily  (as % of stock price)
  30 DTE ATM call premium ≈ 1.82 × σ_daily

where σ_daily = 60-day realized daily stdev of returns (%).

The BS ATM closed-form is:  premium/S ≈ σ_daily × √DTE × N'(0)
  N'(0) = 1/√(2π) ≈ 0.3989

So the theoretical multiplier vs DTE is:  mult(DTE) = 0.3989 × √DTE
  DTE=15 → 0.3989 × 3.873 = 1.545
  DTE=30 → 0.3989 × 5.477 = 2.185

The existing constants (1.29, 1.82) are ~83% of theoretical, suggesting our
realized-vol-based formula underprices relative to the market's IV.

This script:
  1. Pulls ATM option prices from the DB (calls + puts, 10–40 DTE, <3% moneyness)
  2. Computes 60d realized vol in Python for each (symbol, date)
  3. Evaluates: actual_pct, formula_pct, IV-implied_pct, residuals
  4. Bayesian sweep: per-DTE multiplier, per-IV-regime, per-moneyness
  5. Reports the optimal multiplier table and residual stats
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd
import numpy as np

from database.trader_database import DB
from database.models.technical import PriceHistory

# ── Constants ──────────────────────────────────────────────────────────────
VOL_LOOKBACK   = 60          # trading days for realized vol
MIN_VOLUME     = 10          # option trades — filter noise
MAX_MONEYNESS  = 0.03        # |strike − close| / close — ATM window
DTE_BANDS      = [           # (label, lo, hi)
    ('7-10d',  7, 10),
    ('11-14d', 11, 14),
    ('15-17d', 15, 17),
    ('18-22d', 18, 22),
    ('25-35d', 25, 35),
]
# Current production multipliers (from CLAUDE.md)
PROD_MULT_15 = 1.29
PROD_MULT_30 = 1.82
# N'(0) = 1/√(2π).  The production formula uses calendar-day DTE with σ_trading_daily:
#   premium/S = σ_td × √(DTE_cal × 252/365) × N'(0) = σ_td × √DTE_cal × √(252/365) × N'(0)
# So the effective "BS factor" per calendar day is:
#   N'(0) × √(252/365) = 0.3989 × 0.8309 = 0.3314
# Verification: 0.3314 × √15 = 1.283 ≈ 1.29 ✓ ; 0.3314 × √30 = 1.815 ≈ 1.82 ✓
BS_FACTOR     = (1.0 / math.sqrt(2 * math.pi)) * math.sqrt(252.0 / 365.0)   # ≈ 0.3314

# ── Data loading ────────────────────────────────────────────────────────────

def load_price_history_and_vol() -> pd.DataFrame:
    """Load filtered price history (option symbols only, vol window back from earliest option date).
    Then compute 60d realized vol with pandas."""
    print("Loading price history (filtered to option symbols + vol window) …", flush=True)
    # Pre-fetch option symbols to avoid slow IN (SELECT ...) subquery
    sym_rows = DB.execute_sql("SELECT DISTINCT symbol FROM options").fetchall()
    syms = [r[0] for r in sym_rows]
    # Min date from option_prices
    min_date_row = DB.execute_sql("SELECT DATE_SUB(MIN(date), INTERVAL 90 DAY) FROM option_prices").fetchone()
    min_date = min_date_row[0]
    print(f"  {len(syms)} option symbols, loading price history from {min_date}", flush=True)

    # Load in chunks of 50 symbols to avoid huge IN clause
    all_rows = []
    chunk_size = 50
    for i in range(0, len(syms), chunk_size):
        chunk = syms[i:i+chunk_size]
        placeholders = ','.join(['%s'] * len(chunk))
        sql = f"""
            SELECT symbol, date, close FROM price_history
            WHERE symbol IN ({placeholders}) AND date >= %s
            ORDER BY symbol, date
        """
        cursor = DB.execute_sql(sql, chunk + [min_date])
        all_rows.extend(cursor.fetchall())
    rows = all_rows
    print(f"  fetched {len(rows):,} rows", flush=True)
    df = pd.DataFrame(rows, columns=['symbol', 'date', 'close'])
    df['close'] = df['close'].astype(float)
    print(f"  loaded {len(df):,} rows for {df['symbol'].nunique():,} symbols", flush=True)

    print("Computing 60d realized vol (vectorized) …", flush=True)
    df = df.sort_values(['symbol', 'date']).reset_index(drop=True)
    df['ret'] = df.groupby('symbol')['close'].pct_change()
    df['sigma'] = (
        df.groupby('symbol')['ret']
        .transform(lambda x: x.rolling(VOL_LOOKBACK, min_periods=VOL_LOOKBACK).std())
        * 100
    )
    df = df.dropna(subset=['sigma'])
    df = df[df['sigma'] > 0]
    print(f"  vol computed: {len(df):,} date-symbol rows with valid sigma", flush=True)
    return df[['symbol', 'date', 'sigma']].copy()


def load_atm_options(vol_df: pd.DataFrame) -> list[dict]:
    """Fetch near-ATM options. Uses vol_df to get (symbol, date) close prices
    so we can filter moneyness without a 3-table join on 68M rows."""
    print("Loading ATM options from DB …", flush=True)

    # Build a close-price lookup from the vol_df we already have
    # vol_df has: symbol, date, sigma — we need to add close from price_history
    # Actually let's just load option_prices + options (2-table join), then
    # merge with the close lookup we already have in Python.
    print("  step 1: close-price lookup from pre-loaded PH …", flush=True)
    close_sql = """
        SELECT ph.symbol, ph.date, ROUND(ph.close, 4) AS close
        FROM price_history ph
        WHERE ph.symbol IN (SELECT DISTINCT symbol FROM options)
          AND ph.date >= (SELECT MIN(date) - INTERVAL 5 DAY FROM option_prices)
    """
    c = DB.execute_sql(close_sql)
    close_rows = c.fetchall()
    close_lookup = {(r[0], r[1]): float(r[2]) for r in close_rows}
    print(f"  close lookup: {len(close_lookup):,} (sym, date) entries", flush=True)

    print("  step 2: options join (no price_history join) …", flush=True)
    sql = """
        SELECT
            op.date,
            o.symbol,
            o.option_type,
            ROUND(o.strike_price, 4),
            o.expiration_date,
            ROUND(op.price, 4),
            ROUND(op.iv, 6),
            op.volume,
            DATEDIFF(o.expiration_date, op.date)
        FROM option_prices op
        JOIN options o ON o.id = op.option_id
        WHERE op.volume >= %s
          AND op.iv IS NOT NULL AND op.iv > 0.01
          AND DATEDIFF(o.expiration_date, op.date) BETWEEN 7 AND 40
        ORDER BY op.date, o.symbol, o.option_type
    """
    cursor = DB.execute_sql(sql, (MIN_VOLUME,))
    raw = cursor.fetchall()
    print(f"  fetched {len(raw):,} rows (pre-moneyness filter)", flush=True)

    # Apply moneyness filter in Python using close lookup
    result = []
    skipped = 0
    for r in raw:
        dt, sym, opt_type, strike, exp, price, iv, vol, dte = r
        close = close_lookup.get((sym, dt))
        if close is None or close <= 0:
            skipped += 1
            continue
        strike_f = float(strike)
        if abs(strike_f - close) / close >= MAX_MONEYNESS:
            continue
        result.append(dict(
            date=dt, symbol=sym, opt_type=opt_type, strike=strike_f,
            exp=exp, price=float(price), iv=float(iv), volume=int(vol),
            dte=int(dte), close=close
        ))
    print(f"  {len(result):,} ATM rows after moneyness filter  (skipped {skipped:,} no-close)")
    return result


# ── Analysis helpers ─────────────────────────────────────────────────────────

def iv_implied_pct(iv_annual: float, dte: int) -> float:
    """BS ATM approximation using market IV: pct_of_S = IV_daily × √DTE × N'(0)."""
    iv_daily = iv_annual / math.sqrt(252)
    return iv_daily * math.sqrt(dte) * BS_FACTOR * 100   # as %


def formula_pct(sigma_daily: float, mult: float) -> float:
    """Our approximation: premium_pct = mult × σ_daily."""
    return mult * sigma_daily


def bs_theoretical_mult(dte: int) -> float:
    """Theoretical BS multiplier for a given DTE: 0.3989 × √DTE."""
    return BS_FACTOR * math.sqrt(dte)


def dte_band(dte: int) -> str | None:
    for label, lo, hi in DTE_BANDS:
        if lo <= dte <= hi:
            return label
    return None


def bias(errors):
    return statistics.mean(errors)

def rmse(errors):
    return math.sqrt(statistics.mean(e*e for e in errors))

def mae(errors):
    return statistics.mean(abs(e) for e in errors)


# ── Bayesian sweep ────────────────────────────────────────────────────────────

def sweep_multipliers(data: list[dict], grid_n: int = 60) -> dict:
    """
    For each DTE band, sweep multiplier in [0.50, 4.00] and find the value
    that minimises RMSE of (actual_pct − mult × σ_daily).

    Returns {band_label: {'best_mult': float, 'rmse': float, 'n': int,
                          'bias': float, 'mae': float, 'actual_mean': float,
                          'formula_mean': float, 'iv_mean': float}}
    """
    by_band = defaultdict(list)
    for row in data:
        band = dte_band(row['dte'])
        if band and row.get('sigma') is not None:
            by_band[band].append(row)

    results = {}
    for band, rows in sorted(by_band.items(), key=lambda x: x[0]):
        actuals    = [r['actual_pct'] for r in rows]
        sigmas     = [r['sigma'] for r in rows]
        iv_impls   = [r['iv_impl_pct'] for r in rows]

        best_mult  = None
        best_rmse  = float('inf')
        for i in range(grid_n + 1):
            mult = 0.50 + (3.50 * i / grid_n)
            errors = [actuals[j] - mult * sigmas[j] for j in range(len(actuals))]
            r = math.sqrt(statistics.mean(e*e for e in errors))
            if r < best_rmse:
                best_rmse = r
                best_mult = mult

        best_errors = [actuals[j] - best_mult * sigmas[j] for j in range(len(actuals))]
        prod_errors_15 = [actuals[j] - PROD_MULT_15 * sigmas[j] for j in range(len(actuals))]
        prod_errors_30 = [actuals[j] - PROD_MULT_30 * sigmas[j] for j in range(len(actuals))]

        results[band] = {
            'n': len(rows),
            'best_mult': round(best_mult, 3),
            'bs_mult': round(bs_theoretical_mult(round(statistics.mean(r['dte'] for r in rows))), 3),
            'rmse': round(best_rmse, 4),
            'bias': round(bias(best_errors), 4),
            'mae': round(mae(best_errors), 4),
            'actual_mean': round(statistics.mean(actuals), 4),
            'sigma_mean': round(statistics.mean(sigmas), 4),
            'iv_impl_mean': round(statistics.mean(iv_impls), 4),
            'prod_rmse_15': round(rmse(prod_errors_15), 4),
            'prod_rmse_30': round(rmse(prod_errors_30), 4),
            'avg_dte': round(statistics.mean(r['dte'] for r in rows), 1),
        }
    return results


def sweep_by_iv_regime(data: list[dict], grid_n: int = 60) -> dict:
    """
    Bucket by IV regime (low <30%, mid 30-60%, high >60%) and find
    optimal multiplier per regime.
    """
    regimes = {'low_iv': [], 'mid_iv': [], 'high_iv': []}
    for row in data:
        if row.get('sigma') is None:
            continue
        iv_pct = row['iv'] * 100
        if iv_pct < 30:
            regimes['low_iv'].append(row)
        elif iv_pct < 60:
            regimes['mid_iv'].append(row)
        else:
            regimes['high_iv'].append(row)

    results = {}
    for regime, rows in regimes.items():
        if not rows:
            continue
        actuals = [r['actual_pct'] for r in rows]
        sigmas  = [r['sigma'] for r in rows]
        best_mult = None
        best_rmse = float('inf')
        for i in range(grid_n + 1):
            mult = 0.50 + (3.50 * i / grid_n)
            errors = [actuals[j] - mult * sigmas[j] for j in range(len(actuals))]
            r = math.sqrt(statistics.mean(e*e for e in errors))
            if r < best_rmse:
                best_rmse = r
                best_mult = mult
        results[regime] = {
            'n': len(rows),
            'best_mult': round(best_mult, 3),
            'rmse': round(best_rmse, 4),
            'actual_mean': round(statistics.mean(actuals), 4),
            'sigma_mean': round(statistics.mean(sigmas), 4),
            'avg_iv': round(statistics.mean(r['iv'] * 100 for r in rows), 1),
        }
    return results


def sweep_by_moneyness(data: list[dict], grid_n: int = 60) -> dict:
    """Bucket by |strike−close|/close and find optimal mult."""
    buckets = {
        'exact_atm (<0.5%)': [],
        'near_atm (0.5-1.5%)': [],
        'otm_1.5-3%': [],
    }
    for row in data:
        if row.get('sigma') is None:
            continue
        m = abs(row['strike'] - row['close']) / row['close'] * 100
        if m < 0.5:
            buckets['exact_atm (<0.5%)'].append(row)
        elif m < 1.5:
            buckets['near_atm (0.5-1.5%)'].append(row)
        else:
            buckets['otm_1.5-3%'].append(row)

    results = {}
    for bucket, rows in buckets.items():
        if not rows:
            continue
        actuals = [r['actual_pct'] for r in rows]
        sigmas  = [r['sigma'] for r in rows]
        best_mult = None
        best_rmse = float('inf')
        for i in range(grid_n + 1):
            mult = 0.50 + (3.50 * i / grid_n)
            errors = [actuals[j] - mult * sigmas[j] for j in range(len(actuals))]
            r = math.sqrt(statistics.mean(e*e for e in errors))
            if r < best_rmse:
                best_rmse = r
                best_mult = mult
        results[bucket] = {
            'n': len(rows),
            'best_mult': round(best_mult, 3),
            'rmse': round(best_rmse, 4),
            'actual_mean': round(statistics.mean(actuals), 4),
            'sigma_mean': round(statistics.mean(sigmas), 4),
        }
    return results


def sweep_iv_multiplier(data: list[dict], grid_n: int = 80) -> dict:
    """
    Instead of σ_realized, use IV directly: premium_pct = k × IV_daily × √DTE.
    Sweep k around BS theoretical (k=1.0 is exact BS).
    Also test the full formula: premium_pct = k × σ_realized (existing form).
    """
    rows_with_iv = [r for r in data if r.get('sigma') is not None and r['iv'] > 0.01]

    # IV-based: premium_pct ≈ k × (iv/√252) × √dte × 100
    iv_preds = [r['iv'] / math.sqrt(252) * math.sqrt(r['dte']) * 100 for r in rows_with_iv]
    actuals  = [r['actual_pct'] for r in rows_with_iv]
    sigmas   = [r['sigma'] for r in rows_with_iv]

    best_k_iv, best_rmse_iv = None, float('inf')
    best_k_rv, best_rmse_rv = None, float('inf')
    for i in range(grid_n + 1):
        k = 0.50 + (1.00 * i / grid_n)   # [0.5, 1.5] for IV-based
        errors_iv = [actuals[j] - k * iv_preds[j] for j in range(len(actuals))]
        r_iv = math.sqrt(statistics.mean(e*e for e in errors_iv))
        if r_iv < best_rmse_iv:
            best_rmse_iv = r_iv
            best_k_iv = k

    # Also best overall realized-vol multiplier (flat, across all DTEs)
    best_k_rv, best_rmse_rv = None, float('inf')
    for i in range(grid_n + 1):
        mult = 0.50 + (3.50 * i / grid_n)
        errors_rv = [actuals[j] - mult * sigmas[j] for j in range(len(rows_with_iv))]
        r_rv = math.sqrt(statistics.mean(e*e for e in errors_rv))
        if r_rv < best_rmse_rv:
            best_rmse_rv = r_rv
            best_k_rv = mult

    # Baseline: fixed IV-implied (k=1.0, exact BS using market IV)
    baseline_iv_errors = [actuals[j] - iv_preds[j] for j in range(len(actuals))]
    baseline_iv_rmse   = math.sqrt(statistics.mean(e*e for e in baseline_iv_errors))
    # Current production (interpolate between 1.29 @15d and 1.82 @30d)
    prod_errors = [
        actuals[j] - (PROD_MULT_15 if rows_with_iv[j]['dte'] <= 18 else PROD_MULT_30) * sigmas[j]
        for j in range(len(rows_with_iv))
    ]
    prod_rmse = math.sqrt(statistics.mean(e*e for e in prod_errors))

    return {
        'n': len(rows_with_iv),
        'method_iv_best': {
            'k': round(best_k_iv, 3),
            'rmse': round(best_rmse_iv, 4),
            'bias': round(bias([actuals[j] - best_k_iv * iv_preds[j] for j in range(len(actuals))]), 4),
        },
        'method_iv_bs_exact': {
            'k': 1.0,
            'rmse': round(baseline_iv_rmse, 4),
            'bias': round(bias(baseline_iv_errors), 4),
        },
        'method_rv_best': {
            'mult': round(best_k_rv, 3),
            'rmse': round(best_rmse_rv, 4),
            'bias': round(bias([actuals[j] - best_k_rv * sigmas[j] for j in range(len(rows_with_iv))]), 4),
        },
        'method_rv_production': {
            'rmse': round(prod_rmse, 4),
            'bias': round(bias(prod_errors), 4),
        },
        'actual_mean_pct': round(statistics.mean(actuals), 4),
        'avg_iv_pct': round(statistics.mean(r['iv'] * 100 for r in rows_with_iv), 2),
        'avg_rv_pct': round(statistics.mean(sigmas), 2),
        'iv_premium_ratio': round(statistics.mean(r['iv'] * 100 for r in rows_with_iv) /
                                   statistics.mean(sigmas), 3),
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

SEP = '─' * 82

def print_dte_table(band_results: dict):
    print(f'\n{SEP}')
    print('  OPTIMAL MULTIPLIER BY DTE BAND  (premium_pct = mult × σ_daily_realized)')
    print(SEP)
    fmt = '{:<12}  {:>5}  {:>8}  {:>8}  {:>6}  {:>8}  {:>8}  {:>8}  {:>9}'
    hdr = fmt.format('Band', 'N', 'Avg DTE', 'Best Mult', 'BS Mult',
                     'Best RMSE', 'Prod RMSE', 'Actual %', 'σ_daily %')
    print(hdr)
    print('─' * 82)
    for band, r in sorted(band_results.items()):
        # pick which prod_rmse to compare (use 30d for bands > 20)
        prod_r = r['prod_rmse_15'] if r['avg_dte'] <= 18 else r['prod_rmse_30']
        print(fmt.format(
            band, r['n'], r['avg_dte'], r['best_mult'], r['bs_mult'],
            r['rmse'], prod_r, r['actual_mean'], r['sigma_mean']))
    print()


def print_iv_regime_table(regime_results: dict):
    print(SEP)
    print('  OPTIMAL MULTIPLIER BY IV REGIME  (low <30% / mid 30-60% / high >60%)')
    print(SEP)
    fmt = '{:<22}  {:>5}  {:>8}  {:>8}  {:>8}  {:>9}  {:>9}'
    print(fmt.format('Regime', 'N', 'Avg IV%', 'Best Mult', 'Best RMSE',
                     'Actual %', 'σ_daily %'))
    print('─' * 82)
    for regime, r in sorted(regime_results.items()):
        print(fmt.format(regime, r['n'], r['avg_iv'], r['best_mult'],
                         r['rmse'], r['actual_mean'], r['sigma_mean']))
    print()


def print_moneyness_table(m_results: dict):
    print(SEP)
    print('  OPTIMAL MULTIPLIER BY MONEYNESS')
    print(SEP)
    fmt = '{:<24}  {:>5}  {:>8}  {:>8}  {:>9}'
    print(fmt.format('Moneyness Bucket', 'N', 'Best Mult', 'Best RMSE', 'Actual %'))
    print('─' * 82)
    for bucket, r in sorted(m_results.items()):
        print(fmt.format(bucket, r['n'], r['best_mult'], r['rmse'], r['actual_mean']))
    print()


def print_iv_vs_rv(iv_r: dict):
    print(SEP)
    print('  IV-BASED vs REALIZED-VOL-BASED PRICING  (all DTE bands combined)')
    print(SEP)
    n   = iv_r['n']
    avp = iv_r['actual_mean_pct']
    aiv = iv_r['avg_iv_pct']
    arv = iv_r['avg_rv_pct']
    ir  = iv_r['iv_premium_ratio']
    print(f"  N={n:,} | Actual mean={avp:.3f}% of S | Avg IV={aiv:.1f}% | Avg RV={arv:.1f}% | IV/RV ratio={ir:.2f}")
    print()
    print(f"  Production (1.29×σ @15d, 1.82×σ @30d): RMSE={iv_r['method_rv_production']['rmse']:.4f}%"
          f"  bias={iv_r['method_rv_production']['bias']:+.4f}%")
    print(f"  Best flat RV mult ({iv_r['method_rv_best']['mult']:.2f}×σ):        RMSE={iv_r['method_rv_best']['rmse']:.4f}%"
          f"  bias={iv_r['method_rv_best']['bias']:+.4f}%")
    print(f"  BS exact (k=1.0 × IV_daily × √DTE):    RMSE={iv_r['method_iv_bs_exact']['rmse']:.4f}%"
          f"  bias={iv_r['method_iv_bs_exact']['bias']:+.4f}%")
    print(f"  Best IV-scale (k={iv_r['method_iv_best']['k']:.3f}):               RMSE={iv_r['method_iv_best']['rmse']:.4f}%"
          f"  bias={iv_r['method_iv_best']['bias']:+.4f}%")
    print()


def print_recommendations(band_results: dict, iv_r: dict, regime_results: dict):
    print(SEP)
    print('  RECOMMENDATIONS')
    print(SEP)

    # DTE-specific multipliers
    print("  1. Per-DTE multipliers (realized-vol formula, better than production flat constants):")
    for band, r in sorted(band_results.items()):
        avg_dte = r['avg_dte']
        prod_r = r['prod_rmse_15'] if avg_dte <= 18 else r['prod_rmse_30']
        delta = prod_r - r['rmse']
        print(f"     {band:<12}  DTE≈{avg_dte:.0f}d  mult={r['best_mult']:.2f}×σ  "
              f"RMSE={r['rmse']:.4f}%  vs prod {prod_r:.4f}% (Δ={delta:+.4f}%)")

    print()
    print(f"  2. IV-based formula (when IV is available in DB):")
    print(f"     premium_pct = {iv_r['method_iv_best']['k']:.3f} × (IV_annual/√252) × √DTE × 100")
    print(f"     RMSE={iv_r['method_iv_best']['rmse']:.4f}%  vs RV-best {iv_r['method_rv_best']['rmse']:.4f}%"
          f"  (Δ={iv_r['method_rv_best']['rmse'] - iv_r['method_iv_best']['rmse']:+.4f}%)")
    print(f"     → IV-based is {(iv_r['method_rv_best']['rmse'] / iv_r['method_iv_best']['rmse'] - 1)*100:.1f}% better than best RV formula")

    print()
    print("  3. IV-regime-aware multipliers (use different mult based on current IV level):")
    for regime, r in sorted(regime_results.items()):
        print(f"     {regime:<20}  IV≈{r['avg_iv']:.0f}%  mult={r['best_mult']:.2f}×σ")

    print()
    bs_k = iv_r['method_iv_best']['k']
    iv_prem = iv_r['iv_premium_ratio']
    print("  4. Key insight:")
    print(f"     Market IV averages {iv_prem:.2f}× realized vol (IV risk premium).")
    print(f"     Our 1.29/1.82 constants were derived from realized vol and underestimate")
    print(f"     actual market prices by the IV/RV premium. Updating to per-DTE optimal")
    print(f"     multipliers (see #1) closes most of the gap.")
    print(f"     For the most accurate pricing, use IV-based formula (see #2).")
    print(SEP)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    DB.connect(reuse_if_open=True)

    vol_df   = load_price_history_and_vol()
    opt_rows = load_atm_options(vol_df)

    # Merge options with realized vol
    print("Merging options with realized vol …", flush=True)
    opt_df = pd.DataFrame(opt_rows)
    opt_df['date'] = pd.to_datetime(opt_df['date']).dt.date
    merged = opt_df.merge(vol_df, on=['symbol', 'date'], how='inner')
    merged = merged[merged['sigma'] > 0].copy()
    merged['actual_pct']  = merged['price'] / merged['close'] * 100
    merged['iv_impl_pct'] = merged.apply(
        lambda r: iv_implied_pct(r['iv'], r['dte']), axis=1)
    print(f"  {len(merged):,} enriched rows  ({len(opt_df) - len(merged):,} dropped no-vol)")

    data = merged.to_dict('records')

    # Split calls / puts for separate analysis
    calls = [r for r in data if r['opt_type'] == 'call']
    puts  = [r for r in data if r['opt_type'] == 'put']
    print(f"  calls: {len(calls):,}  puts: {len(puts):,}")

    # ── Analysis ──────────────────────────────────────────────────────────
    print("\n\n" + "═" * 82)
    print("  OPTION PRICE ASSESSMENT — σ-BASED APPROXIMATION vs ACTUAL")
    print("  Dataset: ATM options (<3% moneyness), vol≥10, IV>0.01, DTE 7-40d")
    print("═" * 82)

    # ── Calls ──────────────────────────────────────────────────────────────
    print("\n  ▶ CALLS")
    band_c    = sweep_multipliers(calls)
    regime_c  = sweep_by_iv_regime(calls)
    money_c   = sweep_by_moneyness(calls)
    iv_c      = sweep_iv_multiplier(calls)

    print_dte_table(band_c)
    print_iv_regime_table(regime_c)
    print_moneyness_table(money_c)
    print_iv_vs_rv(iv_c)
    print_recommendations(band_c, iv_c, regime_c)

    # ── Puts ───────────────────────────────────────────────────────────────
    print("\n  ▶ PUTS")
    band_p   = sweep_multipliers(puts)
    regime_p = sweep_by_iv_regime(puts)
    money_p  = sweep_by_moneyness(puts)
    iv_p     = sweep_iv_multiplier(puts)

    print_dte_table(band_p)
    print_iv_regime_table(regime_p)
    print_moneyness_table(money_p)
    print_iv_vs_rv(iv_p)
    print_recommendations(band_p, iv_p, regime_p)

    # ── Combined: proposed DTE multiplier lookup table ─────────────────────
    print("\n  ▶ PROPOSED DTE MULTIPLIER TABLE (realized-vol basis)")
    print("     (for use in monte_carlo.py and backtest_cascade.py)\n")
    all_bands = set(list(band_c.keys()) + list(band_p.keys()))
    fmt = '{:<12}  {:>5}  {:>10}  {:>10}  {:>6}  {:>6}'
    print(fmt.format('Band', 'Avg DTE', 'Call mult', 'Put mult', 'BS mult', 'Prod'))
    print('─' * 60)
    for band in sorted(all_bands):
        avg_dte = band_c.get(band, band_p.get(band, {})).get('avg_dte', '?')
        c_mult  = band_c[band]['best_mult'] if band in band_c else '—'
        p_mult  = band_p[band]['best_mult'] if band in band_p else '—'
        bs      = round(bs_theoretical_mult(int(round(float(avg_dte)))), 3) if isinstance(avg_dte, float) else '?'
        prod    = f"{PROD_MULT_15}/@15" if float(avg_dte) <= 18 else f"{PROD_MULT_30}/@30"
        print(fmt.format(band, avg_dte, c_mult, p_mult, bs, prod))

    DB.close()
    print("\nDone.")


if __name__ == '__main__':
    main()
