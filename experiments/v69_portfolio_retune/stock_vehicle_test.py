"""Stock-vehicle test — is the 75+ signal a fundable STOCK strategy (where the ~6% option
spread that killed the options version doesn't apply)?

Builds a calendar-time LONG portfolio: each trading day hold every stock that had a v69
75+ signal in the trailing 15 trading days (equal weight); portfolio daily return = mean of
held names' daily returns. Compares to SPY, and factor-regresses on SPY (market) + a built
momentum factor (top-quintile trailing-126d-return universe portfolio) to isolate ALPHA
beyond beta + momentum. Stock costs ~0.1% round-trip (≈1.7%/yr drag at 15-day holds) are
tiny vs the 6% option spread — the whole point.

Reports: headline cumulative vs SPY, annualized, Sharpe, maxDD; then alpha (annualized) +
t-stat, market beta, momentum beta, R². Pre- and post-cost.

Run: PYTHONIOENCODING=utf-8 python -u experiments/v69_portfolio_retune/stock_vehicle_test.py
"""
from __future__ import annotations
import os, sys, math, bisect
from collections import defaultdict
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from database.models.core import AlgorithmVersion, Score  # noqa: E402

HOLD = 15            # trading-day hold
MOM_LB = 126         # ~6m trailing momentum
COST_RT = 0.001      # 0.1% round-trip stock cost
db = Score._meta.database
av = AlgorithmVersion.get_active_scores_version()


def fetch(sql, *a):
    return db.execute_sql(sql, a).fetchall()


def main():
    # 1. universe price history (scored, sectored stocks + SPY), 2020-2026
    print("loading price history...", flush=True)
    syms = [r[0] for r in fetch(
        "SELECT symbol FROM stocks WHERE sector IS NOT NULL")]
    closes = {}   # sym -> {date: close}
    allsyms = syms + ['SPY']
    for i in range(0, len(allsyms), 150):
        chunk = allsyms[i:i+150]
        ph = ','.join(['%s'] * len(chunk))
        for sym, d, c in db.execute_sql(
            f"SELECT symbol,date,close FROM price_history WHERE symbol IN ({ph}) "
            f"AND date>='2020-06-01' ORDER BY symbol,date", tuple(chunk)):
            closes.setdefault(sym, {})[d] = float(c)
    # trading calendar from SPY
    cal = sorted(closes['SPY'].keys())
    cidx = {d: i for i, d in enumerate(cal)}
    print(f"  {len(syms)} symbols, {len(cal)} trading days {cal[0]}..{cal[-1]}", flush=True)

    # per-symbol aligned close array + daily return array on the calendar
    def arr(sym):
        cl = closes.get(sym, {})
        a = np.array([cl.get(d, np.nan) for d in cal])
        return a
    spy_c = arr('SPY')
    spy_ret = np.concatenate([[np.nan], spy_c[1:]/spy_c[:-1]-1])

    # 2. 75+ signals -> held set per calendar day (entered in trailing HOLD days)
    sigs = fetch("SELECT symbol,date FROM scores WHERE version_id=%s AND overall>=75 "
                 "AND date>='2021-01-01' ORDER BY date", av.id)
    # for each signal day index, the name is held on days [i+1 .. i+HOLD]
    held = defaultdict(set)
    for sym, d in sigs:
        if d not in cidx or sym not in closes: continue
        i = cidx[d]
        for j in range(i+1, min(i+1+HOLD, len(cal))):
            held[j].add(sym)

    # 3. portfolio daily return series (equal weight held names)
    sym_close = {s: arr(s) for s in syms}
    port_ret = np.full(len(cal), np.nan)
    nheld = np.zeros(len(cal))
    entries = np.zeros(len(cal)); exits = np.zeros(len(cal))
    for t in range(1, len(cal)):
        names = held.get(t)
        if not names: continue
        rs = []
        for s in names:
            a = sym_close.get(s)
            if a is None: continue
            if not np.isnan(a[t]) and not np.isnan(a[t-1]):
                rs.append(a[t]/a[t-1]-1)
        if rs:
            port_ret[t] = float(np.mean(rs))
            nheld[t] = len(rs)
        # turnover for cost
        prev = held.get(t-1, set())
        entries[t] = len(names - prev); exits[t] = len(prev - names)

    # 4. momentum factor: each day, top-quintile by trailing MOM_LB return, equal-weight next-day return
    mom_ret = np.full(len(cal), np.nan)
    arrs = sym_close
    for t in range(MOM_LB+1, len(cal)):
        scores = []
        for s in syms:
            a = arrs[s]
            if not np.isnan(a[t-1]) and not np.isnan(a[t-1-MOM_LB]) and a[t-1-MOM_LB] > 0:
                scores.append((s, a[t-1]/a[t-1-MOM_LB]-1))
        if len(scores) < 50: continue
        scores.sort(key=lambda x: x[1], reverse=True)
        top = [s for s, _ in scores[:max(1, len(scores)//5)]]
        rs = [arrs[s][t]/arrs[s][t-1]-1 for s in top if not np.isnan(arrs[s][t]) and not np.isnan(arrs[s][t-1])]
        if rs: mom_ret[t] = float(np.mean(rs))

    # 5. align valid days
    mask = ~np.isnan(port_ret) & ~np.isnan(spy_ret) & ~np.isnan(mom_ret)
    p = port_ret[mask]; m = spy_ret[mask]; mo = mom_ret[mask]
    avg_n = nheld[mask].mean()
    # cost drag: round-trip cost amortized; daily entries+exits as fraction of book
    daily_turn = (entries[mask]+exits[mask]) / np.maximum(nheld[mask], 1)
    cost_daily = daily_turn * (COST_RT/2)
    p_net = p - cost_daily
    days = mask.sum()
    yrs = days/252

    def ann(series): return (np.prod(1+series))**(252/len(series)) - 1
    def sharpe(series): return series.mean()/series.std()*math.sqrt(252) if series.std() else 0
    def maxdd(series):
        eq = np.cumprod(1+series); peak = np.maximum.accumulate(eq)
        return float((1 - eq/peak).max())

    print(f"\n=== 75+ LONG stock portfolio vs SPY ({yrs:.1f}y, avg {avg_n:.0f} names held) ===")
    print(f"{'':16}{'ann.ret':>10}{'Sharpe':>9}{'maxDD':>8}{'totalRet':>12}")
    for name, s in [('75+ gross', p), ('75+ NET cost', p_net), ('SPY', m), ('momentum factor', mo)]:
        tot = (np.prod(1+s)-1)*100
        print(f"{name:16}{ann(s)*100:>+9.1f}%{sharpe(s):>9.2f}{maxdd(s)*100:>7.1f}%{tot:>+11.0f}%")

    # 6. factor regression: p_net ~ a + b1*SPY + b2*MOM
    X = np.column_stack([np.ones(len(p_net)), m, mo])
    beta, *_ = np.linalg.lstsq(X, p_net, rcond=None)
    resid = p_net - X@beta
    n, k = X.shape
    sigma2 = resid@resid/(n-k)
    cov = sigma2*np.linalg.inv(X.T@X)
    se = np.sqrt(np.diag(cov))
    tvals = beta/se
    print(f"\n=== factor regression: 75+net ~ alpha + b_mkt*SPY + b_mom*MOM ===")
    print(f"  alpha (daily):  {beta[0]*100:+.4f}%   ANNUALIZED: {((1+beta[0])**252-1)*100:+.1f}%   t={tvals[0]:.2f}")
    print(f"  market beta:    {beta[1]:.3f}  (t={tvals[1]:.1f})")
    print(f"  momentum beta:  {beta[2]:.3f}  (t={tvals[2]:.1f})")
    ss_res = resid@resid; ss_tot = ((p_net-p_net.mean())**2).sum()
    print(f"  R^2: {1-ss_res/ss_tot:.3f}")
    print(f"\n  est. cost drag: {cost_daily.mean()*252*100:.2f}%/yr  |  pre-tax. Short-term gains tax applies to 15d holds.")
    verdict = "REAL ALPHA beyond SPY+momentum (t>2)" if tvals[0] > 2 else (
        "marginal/insignificant alpha vs SPY+momentum — mostly factor beta" if tvals[0] > 0 else "NEGATIVE alpha")
    print(f"  VERDICT: {verdict}")


if __name__ == '__main__':
    main()
