"""
15 DTE ATM Pricing Spot-Check — production formula vs variance-additive
========================================================================

Pulls real ATM option prices from the last ~14 days, computes both:
  M0  prod        : premium% = 1.29 × σ_daily
  M2  var-additive: premium% = N'(0) × √(σ²·DTE·252/365 + j² × earn_spans)

Slices the cohort by three "environment" axes the user named:
  1. Earnings proximity (days_to_earn): in-window (≤5d), close-window (6-10d), no-earn
  2. IV regime (option's own IV at sample): low <30%, mid 30-60%, high >60%
  3. Market regime (breadth_score on sample date): bear ≤40, neutral 40-60, bull >60

For each cell prints a small sample (≤6 rows) showing per-row error, plus
aggregate RMSE / MAE / bias for prod vs var-additive. Goal: confirm the
variance-additive form is a near-strict win across all environments before
removing the env flag and defaulting it permanently on.
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
LOOKBACK_DAYS    = 14
MAX_MONEYNESS    = 0.03
MIN_VOLUME       = 5
VOL_LOOKBACK     = 60
DTE_LO, DTE_HI   = 13, 17
SAMPLE_PER_CELL  = 6

PROD_MULT_15     = 1.29
DTE_CAL_15       = 15
J_PCT            = 9.3   # universe earnings-jump magnitude (% of spot)
N_PRIME_0        = 1.0 / math.sqrt(2 * math.pi)
CAL_TO_TRADE     = 252.0 / 365.0

# Per-stock j map populated from earnings_jumps cache at module load.
_PER_STOCK_J: dict = {}


# ── Loaders (mirror the earlier scripts) ──────────────────────────────────

def latest_op_date():
    return DB.execute_sql("SELECT MAX(date) FROM option_prices").fetchone()[0]


def load_options(start, end):
    cur = DB.execute_sql("""
        SELECT op.date, o.symbol, o.option_type, o.strike_price,
               op.price, op.iv, op.volume, DATEDIFF(o.expiration_date, op.date)
        FROM option_prices op JOIN options o ON o.id = op.option_id
        WHERE op.date BETWEEN %s AND %s
          AND op.volume >= %s
          AND op.iv IS NOT NULL AND op.iv > 0.01
          AND DATEDIFF(o.expiration_date, op.date) BETWEEN %s AND %s
    """, (start, end, MIN_VOLUME, DTE_LO, DTE_HI))
    return [{'date': r[0], 'symbol': r[1], 'side': r[2], 'strike': float(r[3]),
             'price': float(r[4]), 'iv': float(r[5]), 'volume': int(r[6]),
             'dte': int(r[7])} for r in cur.fetchall()]


def load_close(syms, earliest):
    if not syms: return {}
    out = {}
    syms = sorted(syms)
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


def compute_sigma(close):
    df = pd.DataFrame([{'symbol': s, 'date': d, 'close': c}
                       for (s, d), c in close.items()])
    df = df.sort_values(['symbol', 'date']).reset_index(drop=True)
    df['ret'] = df.groupby('symbol')['close'].pct_change()
    df['sigma'] = (df.groupby('symbol')['ret']
                   .transform(lambda x: x.rolling(VOL_LOOKBACK,
                              min_periods=VOL_LOOKBACK).std()) * 100)
    df = df.dropna(subset=['sigma'])
    df = df[df['sigma'] > 0]
    return {(r.symbol, r.date): r.sigma for r in df.itertuples(index=False)}


def load_earnings(syms, lo, hi):
    if not syms: return {}
    out = defaultdict(list)
    syms = sorted(syms)
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


def load_breadth(start, end):
    """Returns {date: breadth_score} from market_breadth table."""
    cur = DB.execute_sql(
        "SELECT date, breadth_score FROM market_breadth "
        "WHERE date BETWEEN %s AND %s",
        (start - timedelta(days=10), end + timedelta(days=2)))
    return {d: float(b) for d, b in cur.fetchall() if b is not None}


def nearest_earn(earns, ref):
    if not earns: return None
    best, ba = None, 10**9
    for ed in earns:
        diff = (ed - ref).days
        if abs(diff) < ba:
            ba, best = abs(diff), diff
    return best


def breadth_on_or_before(bmap, sorted_dates, d):
    import bisect
    if not sorted_dates: return None
    i = bisect.bisect_right(sorted_dates, d) - 1
    return bmap[sorted_dates[i]] if i >= 0 else None


# ── Predictions ─────────────────────────────────────────────────────────

def predict_prod(sigma_pct):
    return PROD_MULT_15 * sigma_pct


def predict_var_add(sigma_pct, spans):
    """Variance-additive with universe j=9.3."""
    var_calm = (sigma_pct ** 2) * (DTE_CAL_15 * CAL_TO_TRADE)
    var_jump = (J_PCT ** 2) if spans else 0.0
    return N_PRIME_0 * math.sqrt(var_calm + var_jump)


def predict_var_add_per_stock(sigma_pct, spans, symbol):
    """Variance-additive with per-stock j_i (cache lookup); falls back to universe."""
    if not spans:
        return PROD_MULT_15 * sigma_pct
    j = _PER_STOCK_J.get(symbol, J_PCT)
    var_calm = (sigma_pct ** 2) * (DTE_CAL_15 * CAL_TO_TRADE)
    return N_PRIME_0 * math.sqrt(var_calm + j ** 2)


# ── Aggregate metrics ───────────────────────────────────────────────────

def metrics(rows, predfn, *args):
    if not rows:
        return dict(n=0, rmse=float('nan'), mae=float('nan'),
                    bias=float('nan'), rel=float('nan'))
    es = [r['actual'] - predfn(r, *args) for r in rows]
    n = len(es)
    rmse = math.sqrt(sum(e*e for e in es) / n)
    mae  = sum(abs(e) for e in es) / n
    bias = sum(es) / n
    rels = [(r['actual'] / predfn(r, *args) - 1.0) * 100
            for r in rows if predfn(r, *args) > 0]
    return dict(n=n, rmse=rmse, mae=mae, bias=bias,
                rel=statistics.mean(rels) if rels else float('nan'))


def row_pred_prod(r):       return predict_prod(r['sigma'])
def row_pred_var_add(r):    return predict_var_add(r['sigma'], r['spans'])
def row_pred_var_add_ps(r): return predict_var_add_per_stock(r['sigma'], r['spans'], r['symbol'])


# ── Reporting ───────────────────────────────────────────────────────────

def print_cell(label, rows):
    if not rows:
        print(f"  {label}: (no rows)\n"); return
    rows = sorted(rows, key=lambda r: (-r['spans'], abs(r.get('days_to_earn', 99))))
    sample = rows[:SAMPLE_PER_CELL]

    print(f"  {label}  (N={len(rows)})")
    print(f"  {'date':<10}  {'sym':<5} {'sd':<2} {'dte':<3} {'spot':>7} '"
          f"{'σ':>5} {'IV':>5} {'brd':>5} {'d→ern':>6} {'spans':>5} | "
          f"{'actual':>7} {'prod':>7} {'varA':>7} {'errP%':>6} {'errV%':>6}")
    for r in sample:
        eP = (r['actual']/predict_prod(r['sigma'])     - 1) * 100
        eV = (r['actual']/predict_var_add(r['sigma'], r['spans']) - 1) * 100
        print(f"  {r['date']!s:>10}  {r['symbol']:<5} {r['side'][0].upper():<2} "
              f"{r['dte']:<3} {r['close']:>7.2f} "
              f"{r['sigma']:>4.2f}% {r['iv']*100:>4.1f}% "
              f"{('%.0f'%r['breadth']) if r['breadth'] is not None else '  -':>5} "
              f"{r.get('days_to_earn', '∞'):>5}d "
              f"{('Y' if r['spans'] else 'N'):>5} | "
              f"{r['actual']:>5.2f}% {predict_prod(r['sigma']):>5.2f}% "
              f"{predict_var_add(r['sigma'], r['spans']):>5.2f}% "
              f"{eP:>+5.1f}% {eV:>+5.1f}%")
    if len(rows) > SAMPLE_PER_CELL:
        print(f"  …{len(rows)-SAMPLE_PER_CELL} more rows in cell")
    mP  = metrics(rows, row_pred_prod)
    mV  = metrics(rows, row_pred_var_add)
    mVP = metrics(rows, row_pred_var_add_ps)
    print(f"  AGG  prod    : RMSE={mP['rmse']:.3f}pp  MAE={mP['mae']:.3f}pp  bias={mP['bias']:+.3f}pp  rel={mP['rel']:+.1f}%")
    print(f"  AGG  varA-u  : RMSE={mV['rmse']:.3f}pp  MAE={mV['mae']:.3f}pp  bias={mV['bias']:+.3f}pp  rel={mV['rel']:+.1f}%")
    print(f"  AGG  varA-ps : RMSE={mVP['rmse']:.3f}pp  MAE={mVP['mae']:.3f}pp  bias={mVP['bias']:+.3f}pp  rel={mVP['rel']:+.1f}%")
    print(f"  Δ RMSE: prod->varA-u {mP['rmse']-mV['rmse']:+.3f}pp  varA-u->varA-ps {mV['rmse']-mVP['rmse']:+.3f}pp  prod->varA-ps {mP['rmse']-mVP['rmse']:+.3f}pp\n")


# ── Main ───────────────────────────────────────────────────────────────

def main():
    DB.connect(reuse_if_open=True)
    end = latest_op_date()
    start = end - timedelta(days=LOOKBACK_DAYS)
    print(f"Window: [{start}, {end}]\n")

    rows = load_options(start, end)
    syms = {r['symbol'] for r in rows}
    close = load_close(syms, start - timedelta(days=VOL_LOOKBACK*2 + 30))
    sigma = compute_sigma(close)
    earns = load_earnings(syms, start - timedelta(days=20), end + timedelta(days=20))
    bmap  = load_breadth(start, end)
    bdates = sorted(bmap.keys())

    # Per-stock j from earnings_jumps cache (median of last 8 events, N>=3)
    from database.earnings_jump_cache import load_per_stock_jumps
    global _PER_STOCK_J
    _PER_STOCK_J = load_per_stock_jumps(syms)

    print(f"Loaded {len(rows):,} option rows, {len(syms):,} syms, "
          f"{len(close):,} close prices, {len(earns):,} symbols with earnings, "
          f"{len(bmap):,} breadth dates, {len(_PER_STOCK_J):,} per-stock j (universe fallback for {len(syms)-len(_PER_STOCK_J):,})")

    enriched = []
    for r in rows:
        c = close.get((r['symbol'], r['date']))
        if not c: continue
        if abs(r['strike']-c)/c > MAX_MONEYNESS: continue
        s = sigma.get((r['symbol'], r['date']))
        if not s: continue
        e = nearest_earn(earns.get(r['symbol'], []), r['date'])
        b = breadth_on_or_before(bmap, bdates, r['date'])
        # spans: earnings strictly inside (signal_date, signal_date + 15 cal days]
        spans = (e is not None and 0 < e <= DTE_CAL_15)
        enriched.append({
            **r, 'close': c, 'sigma': s, 'breadth': b,
            'days_to_earn': e if e is not None else 99,
            'spans': spans,
            'actual': r['price'] / c * 100,
        })
    print(f"Final ATM cohort: {len(enriched):,}\n")
    if not enriched:
        return

    SEP = '=' * 95

    # ── Slice 1: Earnings proximity ───────────────────────────────────
    print(SEP)
    print("AXIS 1 — EARNINGS PROXIMITY")
    print(SEP)
    cells = {
        'EARN-IN: spans (earn ∈ (0d, 15d])':     [r for r in enriched if r['spans']],
        'NO-EARN: |d_to_earn| > 15 cal days':    [r for r in enriched if not r['spans'] and abs(r['days_to_earn']) > 15],
        'POST-EARN (just past): -10..-1 days':   [r for r in enriched if not r['spans'] and -10 <= r['days_to_earn'] <= -1],
    }
    for label, c in cells.items():
        print_cell(label, c)

    # ── Slice 2: IV regime (option's own IV) ──────────────────────────
    print(SEP)
    print("AXIS 2 — IV REGIME (option's own IV)")
    print(SEP)
    cells = {
        'LOW IV  (<30% annualized)':  [r for r in enriched if r['iv'] < 0.30],
        'MID IV  (30-60%)':           [r for r in enriched if 0.30 <= r['iv'] < 0.60],
        'HIGH IV (≥60%)':             [r for r in enriched if r['iv'] >= 0.60],
    }
    for label, c in cells.items():
        print_cell(label, c)

    # ── Slice 3: Market regime (breadth_score at signal date) ─────────
    print(SEP)
    print("AXIS 3 — MARKET REGIME (breadth_score at sample date)")
    print(SEP)
    cells = {
        'BEAR    (breadth ≤ 40)':    [r for r in enriched if r['breadth'] is not None and r['breadth'] <= 40],
        'NEUTRAL (breadth 40-60)':   [r for r in enriched if r['breadth'] is not None and 40 < r['breadth'] <= 60],
        'BULL    (breadth > 60)':    [r for r in enriched if r['breadth'] is not None and r['breadth'] > 60],
    }
    for label, c in cells.items():
        print_cell(label, c)

    # ── Combined headline ─────────────────────────────────────────────
    print(SEP)
    print("HEADLINE: prod vs var-additive across the full sample")
    print(SEP)
    mP  = metrics(enriched, row_pred_prod)
    mV  = metrics(enriched, row_pred_var_add)
    mVP = metrics(enriched, row_pred_var_add_ps)
    print(f"  N={len(enriched):,}  span={sum(1 for r in enriched if r['spans'])}  "
          f"non-span={sum(1 for r in enriched if not r['spans'])}")
    print(f"  prod    : RMSE={mP['rmse']:.3f}pp  MAE={mP['mae']:.3f}pp  bias={mP['bias']:+.3f}pp  rel={mP['rel']:+.1f}%")
    print(f"  varA-u  : RMSE={mV['rmse']:.3f}pp  MAE={mV['mae']:.3f}pp  bias={mV['bias']:+.3f}pp  rel={mV['rel']:+.1f}%")
    print(f"  varA-ps : RMSE={mVP['rmse']:.3f}pp  MAE={mVP['mae']:.3f}pp  bias={mVP['bias']:+.3f}pp  rel={mVP['rel']:+.1f}%")
    print(f"  Δ RMSE prod -> varA-ps: {mP['rmse']-mVP['rmse']:+.3f}pp ({(1-mVP['rmse']/mP['rmse'])*100:+.1f}%)")
    print(f"  Δ RMSE varA-u -> varA-ps: {mV['rmse']-mVP['rmse']:+.3f}pp ({(1-mVP['rmse']/mV['rmse'])*100:+.1f}%)")

    DB.close()


if __name__ == '__main__':
    main()
