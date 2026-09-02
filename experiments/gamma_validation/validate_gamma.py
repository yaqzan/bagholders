"""
Empirical validation of the gamma-aware (BS) option value vs the shipped
constant-delta model, against REAL option_prices.

For each ATM ~30DTE option with forward bars: compare actual (V_t-V0)/V0 to
  - CONST : option_pnl_pct  (shipped, delta=0.5, linear)
  - GAMMA : bs_option_pnl_pct (BS, delta+gamma, vol from premium)
  - LEGACY: delta-only, no theta
Same empirical premium_pct=V0/U0 and same vega_ratio (actual iv_t/pre_iv on
earnings-spanning bars) for CONST and GAMMA, so the diff isolates delta/gamma.

Headline test: does GAMMA cut RMSE/bias vs CONST, and — decisively — does it
win MOST on the big-underlying-move cohort (where linear delta fails)?
"""
import os, sys, math
from collections import defaultdict

ROOT = r"C:\Development\Trader"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments"))
os.chdir(ROOT)

import option_pricing_validation as opv           # reuse the loaders
from option_pricing import option_pnl_pct, bs_option_pnl_pct, DEFAULT_TOTAL_DTE, DEFAULT_DELTA

MAX_SIGNALS = int(os.environ.get("GV_MAX_SIGNALS", "6000"))
BARS = [1, 3, 5, 7, 10, 15]

def load_signals_fast(limit):
    """Date-bounded, indexed, no ORDER BY RAND (avoids the full-scan sort that
    times out). Takes the first `limit` ATM ~30DTE liquid signals it finds."""
    from database.trader_database import DB
    sql = """
    SELECT op.option_id, o.symbol, o.option_type, o.strike_price,
           o.expiration_date, op.date, ph.close, op.iv, op.price
    FROM option_prices op
    JOIN options o ON o.id = op.option_id
    JOIN price_history ph ON ph.symbol = o.symbol AND ph.date = op.date
    WHERE op.volume >= 5 AND op.iv > 0.05 AND op.price > 0.50
      AND DATEDIFF(o.expiration_date, op.date) BETWEEN 25 AND 35
      AND ABS(CAST(o.strike_price AS DECIMAL(10,2)) - CAST(ph.close AS DECIMAL(10,2)))
          / CAST(ph.close AS DECIMAL(10,2)) <= 0.03
      AND op.date <= (CURDATE() - INTERVAL 24 DAY)
    LIMIT %s
    """
    return list(DB.execute_sql(sql, (limit,)).fetchall())

def _stats(res):
    if not res: return dict(n=0, rmse=float('nan'), mae=float('nan'), bias=float('nan'))
    n = len(res)
    return dict(n=n,
                rmse=math.sqrt(sum(r*r for r in res)/n),
                mae=sum(abs(r) for r in res)/n,
                bias=sum(res)/n)

def move_bucket(x):
    a = abs(x)
    if a < 0.02: return "0-2%"
    if a < 0.05: return "2-5%"
    if a < 0.10: return "5-10%"
    return "10%+"

def main():
    rows_in = load_signals_fast(MAX_SIGNALS)
    print(f"signals: {len(rows_in):,}", flush=True)
    syms = list({r[1] for r in rows_in})
    ern = {s: opv.load_earnings_dates(s) for s in syms}

    recs = []   # each: (side, bar, spans, move, actual, const, gamma, legacy)
    for i, (oid, sym, side_str, strike, exp, sig, u0, iv0, v0) in enumerate(rows_in):
        if i % 1000 == 0: print(f"  {i:,}/{len(rows_in):,}", flush=True)
        try:
            side = 'call' if side_str.upper() == 'CALL' else 'put'
            fop = opv.load_forward_option_prices(oid, sig, max_bars=15)
            fph = opv.load_forward_underlying(sym, sig, max_bars=15)
            if len(fop) < 2 or len(fph) < 2: continue
            ph = {d: float(c) for d, c in fph}
            op = {d: (float(p), float(iv) if iv else None) for d, p, iv in fop}
            tdays = sorted(d for d in ph if d >= sig)
            U0 = float(u0); V0 = float(v0)
            if U0 <= 0 or V0 <= 0: continue
            prem = V0 / U0
            pre_iv = float(iv0) if iv0 else None
            for t in BARS:
                if t >= len(tdays): break
                d_t = tdays[t]
                if d_t not in op or d_t not in ph: continue
                V_t, iv_t = op[d_t]; U_t = ph[d_t]
                actual = (V_t - V0) / V0
                spans = any(sig < ed <= d_t for ed in ern.get(sym, []))
                vega = 1.0
                if spans and pre_iv and iv_t and pre_iv > 0:
                    vega = max(0.20, min(2.0, iv_t / pre_iv))
                const = option_pnl_pct(side, U_t, U0, t, prem, total_dte=DEFAULT_TOTAL_DTE, vega_ratio=vega, delta=DEFAULT_DELTA)
                gamma = bs_option_pnl_pct(side, U_t, U0, t, prem, total_dte=DEFAULT_TOTAL_DTE, vega_ratio=vega)
                legacy = ((U_t-U0)/U0 if side=='call' else -(U_t-U0)/U0) * DEFAULT_DELTA / prem
                recs.append((side, t, spans, (U_t-U0)/U0, actual, const, gamma, legacy))
        except Exception:
            continue

    print(f"\nbar-comparisons: {len(recs):,}")
    ns = [r for r in recs if not r[2]]   # non-spanning (clean delta/gamma test)
    print(f"non-spanning: {len(ns):,}   spanning: {len(recs)-len(ns):,}")

    def report(label, rs):
        c = _stats([r[4]-r[5] for r in rs]); g = _stats([r[4]-r[6] for r in rs]); l = _stats([r[4]-r[7] for r in rs])
        if c['n'] == 0: return
        lift = (c['rmse']-g['rmse'])/c['rmse']*100 if c['rmse'] else 0
        print(f"  {label:14s} N={c['n']:>6,} | CONST rmse={c['rmse']:.3f} bias={c['bias']:+.3f} | "
              f"GAMMA rmse={g['rmse']:.3f} bias={g['bias']:+.3f} | LEGACY rmse={l['rmse']:.3f} | "
              f"gamma vs const RMSE {lift:+.1f}%")

    print("\n=== HEADLINE (residual = actual - model; lower RMSE + bias->0 = better) ===")
    report("ALL", recs)
    report("non-spanning", ns)
    print("\n=== by |underlying move| (non-spanning) — gamma should win MOST on big moves ===")
    by = defaultdict(list)
    for r in ns: by[move_bucket(r[3])].append(r)
    for k in ["0-2%", "2-5%", "5-10%", "10%+"]:
        if by[k]: report(k, by[k])
    print("\n=== by side (non-spanning) ===")
    for sd in ('call', 'put'):
        report(sd, [r for r in ns if r[0] == sd])
    print("\n=== by bar (non-spanning) ===")
    for t in BARS:
        report(f"bar={t}", [r for r in ns if r[1] == t])

if __name__ == '__main__':
    main()
