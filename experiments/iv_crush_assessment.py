"""
IV Crush Assessment — Empirical Investigation
==============================================

Goal: Quantify post-earnings IV crush on ATM options across the option_prices DB,
build a "best guess" equation for predicting crush magnitude, and apply it to puts
specifically (where the user is interested in EARN_SUPP_PUT / weak-weekly interaction).

Methodology:
  1. For each (symbol, earnings_date), find ATM options (|K-S|/S < 3%) with DTE in
     {15-21, 22-35, 36-50} at the pre-earnings close.
  2. Match the SAME contract on D-1 (last close before earnings) and D+1 (first close
     after) using call_time to determine which side is "before" and "after".
  3. Compute crush = (iv_pre - iv_post) / iv_pre  (relative) and (iv_pre - iv_post) abs.
  4. Stratify by:
       - DTE band at earnings
       - Pre-earnings IV level (low <40%, mid 40-70%, high 70-120%, extreme >120%)
       - Side (call vs put)
       - Realized-vol regime (60d RV)
  5. Fit a regression of post_iv on:
       - pre_iv (the dominant input)
       - DTE_at_earnings (longer DTE → less proportional crush)
       - side (small dummy)
     to produce a "best guess" equation.
  6. Apply to puts: report distribution of put-specific crush, useful for option-P&L
     modeling in MC.

Assumptions:
  - call_time field is one of: 'before-market-open' / 'after-market-close' /
    'time-not-supplied' / 'time-during-trading' / NULL.
  - For BMO: pre_date = earnings_date - 1 trading day, post_date = earnings_date.
  - For AMC: pre_date = earnings_date, post_date = earnings_date + 1 trading day.
  - For UNKNOWN/intraday: pre_date = D-1, post_date = D+1 (skip the day-of for safety).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta

from database.trader_database import DB

# ────────────────────────────────────────────────────────────────────────────
# Phase 1: Load earnings events with call_time, find pre/post trading dates
# ────────────────────────────────────────────────────────────────────────────

def load_trading_calendar():
    """Distinct dates in option_prices = trading calendar (option market days)."""
    rows = DB.execute_sql("SELECT DISTINCT date FROM option_prices ORDER BY date").fetchall()
    return [r[0] for r in rows]

def neighboring_trading_days(d, calendar, n=1):
    """Return (prev, next) trading day from calendar around date d."""
    # binary-search
    lo, hi = 0, len(calendar)
    while lo < hi:
        mid = (lo + hi) // 2
        if calendar[mid] < d:
            lo = mid + 1
        else:
            hi = mid
    # lo is index of first day >= d
    prev_idx = lo - 1
    if calendar[lo] != d if lo < len(calendar) else True:
        # d isn't a trading day; lo points past it
        next_idx = lo
    else:
        next_idx = lo + 1
    prev = calendar[prev_idx] if prev_idx >= 0 else None
    nxt  = calendar[next_idx] if next_idx < len(calendar) else None
    return prev, nxt

def classify_call_time(ct):
    """Return 'BMO' / 'AMC' / 'INTRADAY' / 'UNKNOWN'."""
    if ct is None or ct == '' or 'not-supplied' in (ct or ''):
        return 'UNKNOWN'
    ct = (ct or '').lower()
    if 'before' in ct or 'pre' in ct or 'bmo' in ct:
        return 'BMO'
    if 'after' in ct or 'post' in ct or 'amc' in ct:
        return 'AMC'
    if 'during' in ct or 'intraday' in ct:
        return 'INTRADAY'
    return 'UNKNOWN'

def determine_pre_post(ern_date, call_time, calendar):
    """Return (pre_date, post_date) per earnings call_time conventions.
    Pre = last trading close BEFORE the announcement.
    Post = first trading close AFTER the announcement."""
    cls = classify_call_time(call_time)
    prev, nxt = neighboring_trading_days(ern_date, calendar)
    if cls == 'BMO':
        # Announcement before market open ON ern_date.
        # Pre = trading day before ern_date. Post = ern_date itself.
        # Find ern_date as a trading day; if not, use prev/next.
        # Use simple lookup: pre_date = day before ern_date in calendar; post_date = ern_date if it's a trading day.
        pre = prev
        # Is ern_date itself a trading day?
        post = ern_date if ern_date in _calendar_set else nxt
        return pre, post
    elif cls == 'AMC':
        # Announcement after market close ON ern_date.
        # Pre = ern_date (its close is the last unaffected). Post = next trading day.
        pre = ern_date if ern_date in _calendar_set else prev
        post = nxt
        return pre, post
    else:
        # UNKNOWN/INTRADAY: skip day-of, use D-1 vs D+1
        pre = prev
        post = nxt
        return pre, post

# Globals populated lazily
_calendar = None
_calendar_set = None

def _ensure_calendar():
    global _calendar, _calendar_set
    if _calendar is None:
        _calendar = load_trading_calendar()
        _calendar_set = set(_calendar)


# ────────────────────────────────────────────────────────────────────────────
# Phase 2: For each event, find ATM contracts with both pre/post quotes
# ────────────────────────────────────────────────────────────────────────────

def find_atm_contracts(symbol, pre_date, post_date, dte_min=15, dte_max=50):
    """Return list of (option_id, option_type, strike, expiration, pre_iv, post_iv,
                       pre_price, post_price, pre_close_S, dte_at_earnings, moneyness)
    for ATM options with both pre and post IV coverage on this earnings event.

    Strategy: for the symbol, find options whose expiration is dte_min..dte_max
    calendar days past pre_date, with strikes within ~5% of pre_close.
    Then JOIN to option_prices on pre_date and post_date.
    """
    # Get pre-close stock price
    row = DB.execute_sql(
        "SELECT close FROM price_history WHERE symbol=%s AND date=%s",
        (symbol, pre_date)
    ).fetchone()
    if row is None or row[0] is None:
        return []
    pre_close = float(row[0])

    # Find option contracts with the right DTE band at earnings
    sql = """
        SELECT o.id, o.option_type, o.strike_price, o.expiration_date,
               op_pre.iv  AS pre_iv,  op_post.iv  AS post_iv,
               op_pre.price AS pre_price, op_post.price AS post_price,
               op_pre.volume + op_post.volume AS vol_total
        FROM options o
        JOIN option_prices op_pre  ON op_pre.option_id  = o.id AND op_pre.date  = %s
        JOIN option_prices op_post ON op_post.option_id = o.id AND op_post.date = %s
        WHERE o.symbol = %s
          AND DATEDIFF(o.expiration_date, %s) BETWEEN %s AND %s
          AND ABS(o.strike_price - %s) / %s < 0.05
          AND op_pre.iv IS NOT NULL AND op_post.iv IS NOT NULL
          AND op_pre.iv > 0.05 AND op_post.iv > 0.05
          AND op_pre.iv < 5.0 AND op_post.iv < 5.0
    """
    rows = DB.execute_sql(sql, (
        pre_date, post_date, symbol, pre_date, dte_min, dte_max,
        pre_close, pre_close
    )).fetchall()
    out = []
    for r in rows:
        oid, otype, strike, exp, pre_iv, post_iv, pre_price, post_price, vol = r
        moneyness = abs(float(strike) - pre_close) / pre_close
        if moneyness > 0.03:  # tighten to true ATM
            continue
        dte = (exp - pre_date).days
        out.append({
            'option_id': oid,
            'option_type': otype,
            'strike': float(strike),
            'expiration': exp,
            'pre_iv': float(pre_iv),
            'post_iv': float(post_iv),
            'pre_price': float(pre_price),
            'post_price': float(post_price),
            'pre_close': pre_close,
            'dte': dte,
            'moneyness': moneyness,
            'vol_total': int(vol or 0),
        })
    return out


# ────────────────────────────────────────────────────────────────────────────
# Phase 3: Realized vol context (for stratification)
# ────────────────────────────────────────────────────────────────────────────

def realized_vol_60d(symbol, ref_date):
    """60-day daily-stdev realized vol % at ref_date. Returns None if insufficient history."""
    rows = DB.execute_sql(
        """
        SELECT close FROM price_history
        WHERE symbol = %s AND date <= %s
        ORDER BY date DESC LIMIT 61
        """,
        (symbol, ref_date)
    ).fetchall()
    if len(rows) < 30:
        return None
    closes = [float(r[0]) for r in rows][::-1]  # ascending
    rets = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
    if len(rets) < 20:
        return None
    return statistics.pstdev(rets) * 100  # in %


# ────────────────────────────────────────────────────────────────────────────
# Phase 4: Main scan
# ────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 78)
    print("IV CRUSH ASSESSMENT — EMPIRICAL")
    print("=" * 78, flush=True)

    _ensure_calendar()
    print(f"Trading calendar: {len(_calendar)} days, {_calendar[0]} to {_calendar[-1]}", flush=True)

    # Load earnings events in option_prices coverage range
    cal_min, cal_max = _calendar[0], _calendar[-1]
    rows = DB.execute_sql(
        """
        SELECT symbol, date, call_time
        FROM earnings_dates
        WHERE date >= %s AND date <= %s
        ORDER BY date, symbol
        """,
        (cal_min, cal_max)
    ).fetchall()
    print(f"Earnings events in scope: {len(rows)}", flush=True)

    # Cache realized vol per (symbol, date) — only need it once per event
    rv_cache = {}

    samples = []  # one record per (event, contract)
    seen_events = 0
    matched_events = 0

    for sym, ern_date, ct in rows:
        seen_events += 1
        if seen_events % 200 == 0:
            print(f"  scanned {seen_events}/{len(rows)} events, matched {matched_events}, samples {len(samples)}", flush=True)

        pre_date, post_date = determine_pre_post(ern_date, ct, _calendar)
        if pre_date is None or post_date is None:
            continue
        if pre_date < cal_min or post_date > cal_max:
            continue

        contracts = find_atm_contracts(sym, pre_date, post_date)
        if not contracts:
            continue
        matched_events += 1

        rv = rv_cache.get((sym, pre_date))
        if rv is None:
            rv = realized_vol_60d(sym, pre_date)
            rv_cache[(sym, pre_date)] = rv

        ct_class = classify_call_time(ct)

        for c in contracts:
            samples.append({
                'symbol': sym,
                'ern_date': ern_date,
                'ct_class': ct_class,
                'pre_date': pre_date,
                'post_date': post_date,
                'rv60': rv,
                **c,
            })

    print(f"\n>>> Matched {matched_events}/{seen_events} events, {len(samples)} option samples", flush=True)

    if not samples:
        print("No samples — abort.")
        return

    # ── Aggregate stats ──────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("CRUSH DISTRIBUTION (overall)")
    print("=" * 78)
    crushes_rel = [(s['pre_iv'] - s['post_iv']) / s['pre_iv'] for s in samples]
    crushes_abs = [s['pre_iv'] - s['post_iv'] for s in samples]
    print(f"  Samples N = {len(samples)}")
    print(f"  Relative crush: mean={statistics.mean(crushes_rel):+.3f}  median={statistics.median(crushes_rel):+.3f}  stdev={statistics.pstdev(crushes_rel):.3f}")
    print(f"  Absolute crush: mean={statistics.mean(crushes_abs):+.3f}  median={statistics.median(crushes_abs):+.3f}  stdev={statistics.pstdev(crushes_abs):.3f}")
    print(f"  Pre-IV  mean={statistics.mean([s['pre_iv'] for s in samples]):.3f}")
    print(f"  Post-IV mean={statistics.mean([s['post_iv'] for s in samples]):.3f}")

    # ── Stratify by DTE band ────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("CRUSH BY DTE-AT-EARNINGS BAND")
    print("=" * 78)
    bands = [('15-21', 15, 21), ('22-35', 22, 35), ('36-50', 36, 50)]
    print(f"  {'Band':<8} {'N':>5}  {'pre_iv':>7}  {'post_iv':>8}  {'rel_crush':>10}  {'abs_crush':>10}")
    for label, lo, hi in bands:
        sub = [s for s in samples if lo <= s['dte'] <= hi]
        if not sub: continue
        rel = [(s['pre_iv'] - s['post_iv']) / s['pre_iv'] for s in sub]
        abs_ = [s['pre_iv'] - s['post_iv'] for s in sub]
        pre = [s['pre_iv'] for s in sub]
        post = [s['post_iv'] for s in sub]
        print(f"  {label:<8} {len(sub):>5}  {statistics.mean(pre):>7.3f}  {statistics.mean(post):>8.3f}  {statistics.mean(rel):>+9.3f}  {statistics.mean(abs_):>+9.3f}")

    # ── Stratify by side (CALL vs PUT) ──────────────────────────────────
    print("\n" + "=" * 78)
    print("CRUSH BY SIDE")
    print("=" * 78)
    print(f"  {'Side':<6} {'N':>5}  {'pre_iv':>7}  {'post_iv':>8}  {'rel_crush':>10}  {'abs_crush':>10}")
    for side in ('CALL', 'PUT'):
        sub = [s for s in samples if s['option_type'].upper() == side]
        if not sub: continue
        rel = [(s['pre_iv'] - s['post_iv']) / s['pre_iv'] for s in sub]
        abs_ = [s['pre_iv'] - s['post_iv'] for s in sub]
        pre = [s['pre_iv'] for s in sub]
        post = [s['post_iv'] for s in sub]
        print(f"  {side:<6} {len(sub):>5}  {statistics.mean(pre):>7.3f}  {statistics.mean(post):>8.3f}  {statistics.mean(rel):>+9.3f}  {statistics.mean(abs_):>+9.3f}")

    # ── Stratify by pre-IV level ────────────────────────────────────────
    print("\n" + "=" * 78)
    print("CRUSH BY PRE-IV LEVEL  (pre_iv buckets, both sides)")
    print("=" * 78)
    iv_bands = [
        ('<0.40', 0.0, 0.40),
        ('0.40-0.60', 0.40, 0.60),
        ('0.60-0.80', 0.60, 0.80),
        ('0.80-1.20', 0.80, 1.20),
        ('1.20-2.00', 1.20, 2.00),
        ('>2.00', 2.00, 99.0),
    ]
    print(f"  {'PreIV':<12} {'N':>5}  {'pre_iv':>7}  {'post_iv':>8}  {'rel_crush':>10}  {'abs_crush':>10}")
    for label, lo, hi in iv_bands:
        sub = [s for s in samples if lo <= s['pre_iv'] < hi]
        if not sub: continue
        rel = [(s['pre_iv'] - s['post_iv']) / s['pre_iv'] for s in sub]
        abs_ = [s['pre_iv'] - s['post_iv'] for s in sub]
        pre = [s['pre_iv'] for s in sub]
        post = [s['post_iv'] for s in sub]
        print(f"  {label:<12} {len(sub):>5}  {statistics.mean(pre):>7.3f}  {statistics.mean(post):>8.3f}  {statistics.mean(rel):>+9.3f}  {statistics.mean(abs_):>+9.3f}")

    # ── Stratify by RV60 band (vol regime) ──────────────────────────────
    print("\n" + "=" * 78)
    print("CRUSH BY RV60 BAND  (60d realized daily vol %, pre-earnings)")
    print("=" * 78)
    rv_bands = [
        ('<2%',  0.0, 2.0),
        ('2-3%', 2.0, 3.0),
        ('3-5%', 3.0, 5.0),
        ('5-8%', 5.0, 8.0),
        ('>8%',  8.0, 99.0),
    ]
    print(f"  {'RV60':<8} {'N':>5}  {'pre_iv':>7}  {'post_iv':>8}  {'rel_crush':>10}  {'abs_crush':>10}")
    for label, lo, hi in rv_bands:
        sub = [s for s in samples if s['rv60'] is not None and lo <= s['rv60'] < hi]
        if not sub: continue
        rel = [(s['pre_iv'] - s['post_iv']) / s['pre_iv'] for s in sub]
        abs_ = [s['pre_iv'] - s['post_iv'] for s in sub]
        pre = [s['pre_iv'] for s in sub]
        post = [s['post_iv'] for s in sub]
        print(f"  {label:<8} {len(sub):>5}  {statistics.mean(pre):>7.3f}  {statistics.mean(post):>8.3f}  {statistics.mean(rel):>+9.3f}  {statistics.mean(abs_):>+9.3f}")

    # ── 2D table: PRE-IV × DTE for puts ─────────────────────────────────
    print("\n" + "=" * 78)
    print("PUT IV CRUSH 2D TABLE  (rel_crush mean, N in parens)")
    print("=" * 78)
    puts = [s for s in samples if s['option_type'].upper() == 'PUT']
    print(f"  Total puts: {len(puts)}")
    print(f"  {'DTE / PreIV':<14}", end='')
    for label, _, _ in iv_bands:
        print(f"  {label:>12}", end='')
    print()
    for d_label, d_lo, d_hi in bands:
        print(f"  {d_label:<14}", end='')
        for iv_label, iv_lo, iv_hi in iv_bands:
            sub = [s for s in puts if d_lo <= s['dte'] <= d_hi and iv_lo <= s['pre_iv'] < iv_hi]
            if len(sub) < 5:
                print(f"  {'-':>12}", end='')
                continue
            rel = [(s['pre_iv'] - s['post_iv']) / s['pre_iv'] for s in sub]
            print(f"  {statistics.mean(rel):>+7.3f}({len(sub):>3d})", end='')
        print()

    # ── Build a regression: post_iv ~ a + b*pre_iv + c*log(DTE) + d*side ────
    print("\n" + "=" * 78)
    print("REGRESSION: post_iv = a + b*pre_iv + c*log(DTE) + d*is_put")
    print("=" * 78)
    X, Y = [], []
    for s in samples:
        X.append([1.0, s['pre_iv'], math.log(max(s['dte'], 1)), 1.0 if s['option_type'].upper() == 'PUT' else 0.0])
        Y.append(s['post_iv'])
    coefs = ols(X, Y)
    if coefs is not None:
        a, b, c, d = coefs
        # R²
        y_mean = sum(Y) / len(Y)
        ss_tot = sum((y - y_mean) ** 2 for y in Y)
        ss_res = 0.0
        for i, x in enumerate(X):
            yhat = sum(coef * xi for coef, xi in zip(coefs, x))
            ss_res += (Y[i] - yhat) ** 2
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        print(f"  Coefficients:")
        print(f"    intercept (a) = {a:+.4f}")
        print(f"    pre_iv    (b) = {b:+.4f}")
        print(f"    log(DTE)  (c) = {c:+.4f}")
        print(f"    is_put    (d) = {d:+.4f}")
        print(f"  R² = {r2:.3f}")
        print()
        print(f"  Best-guess equation:")
        print(f"    post_iv = {a:+.4f} + {b:+.4f} * pre_iv + {c:+.4f} * log(DTE) + {d:+.4f} * is_put")
        print()
        print(f"  Examples:")
        for pre, dte, side in [(0.40, 30, 'PUT'), (0.60, 30, 'PUT'), (0.80, 30, 'PUT'),
                                (1.00, 30, 'PUT'), (1.50, 30, 'PUT'),
                                (0.60, 15, 'PUT'), (0.60, 45, 'PUT')]:
            isp = 1.0 if side == 'PUT' else 0.0
            pred = a + b * pre + c * math.log(dte) + d * isp
            crush_rel = (pre - pred) / pre
            print(f"    pre_iv={pre:.2f}, DTE={dte}, {side}:  post_iv ≈ {pred:.3f}  (crush ≈ {crush_rel:+.1%})")

    # ── Simpler form: post_iv ≈ k * pre_iv (ratio model) ────────────────
    print("\n" + "=" * 78)
    print("RATIO MODEL: post_iv = k * pre_iv  (per side, per DTE band)")
    print("=" * 78)
    print(f"  {'Side':<6} {'DTE Band':<10} {'N':>5}  {'k (mean post/pre)':>18}  {'std':>6}")
    for side in ('CALL', 'PUT'):
        for d_label, d_lo, d_hi in bands:
            sub = [s for s in samples if s['option_type'].upper() == side and d_lo <= s['dte'] <= d_hi]
            if len(sub) < 10: continue
            ratios = [s['post_iv'] / s['pre_iv'] for s in sub]
            print(f"  {side:<6} {d_label:<10} {len(sub):>5}  {statistics.mean(ratios):>18.3f}  {statistics.pstdev(ratios):>6.3f}")

    # ── Save sample dump for downstream use ─────────────────────────────
    out_path = os.path.join(os.path.dirname(__file__), 'iv_crush_samples.csv')
    with open(out_path, 'w') as f:
        f.write("symbol,ern_date,ct_class,option_type,strike,expiration,dte,moneyness,pre_iv,post_iv,pre_price,post_price,rv60,vol_total\n")
        for s in samples:
            f.write(f"{s['symbol']},{s['ern_date']},{s['ct_class']},{s['option_type']},"
                    f"{s['strike']},{s['expiration']},{s['dte']},{s['moneyness']:.4f},"
                    f"{s['pre_iv']:.4f},{s['post_iv']:.4f},{s['pre_price']:.2f},{s['post_price']:.2f},"
                    f"{s['rv60'] if s['rv60'] is not None else ''},{s['vol_total']}\n")
    print(f"\nSaved {len(samples)} samples to {out_path}")


def ols(X, Y):
    """Minimal OLS via normal equations: solve X^T X beta = X^T Y. Returns coefs or None on singular."""
    n = len(X)
    if n == 0:
        return None
    p = len(X[0])
    # Build XtX (p×p) and XtY (p)
    XtX = [[0.0] * p for _ in range(p)]
    XtY = [0.0] * p
    for i in range(n):
        for j in range(p):
            XtY[j] += X[i][j] * Y[i]
            for k in range(p):
                XtX[j][k] += X[i][j] * X[i][k]
    # Gauss-Jordan
    M = [row[:] + [XtY[i]] for i, row in enumerate(XtX)]
    for i in range(p):
        # Pivot
        max_row = max(range(i, p), key=lambda r: abs(M[r][i]))
        if abs(M[max_row][i]) < 1e-12:
            return None
        M[i], M[max_row] = M[max_row], M[i]
        # Eliminate
        for j in range(p):
            if j == i: continue
            factor = M[j][i] / M[i][i]
            for k in range(i, p + 1):
                M[j][k] -= factor * M[i][k]
    return [M[i][p] / M[i][i] for i in range(p)]


if __name__ == '__main__':
    main()
