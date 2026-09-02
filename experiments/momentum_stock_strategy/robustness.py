"""Robustness checks for the momentum-stock verdict:
 1. Is the pure-momentum benchmark cherry-picked? Sweep (top-decile/quintile) x (monthly/quarterly
    rebal) x (12-1 / 6m lookback) and report each as a 'bar to beat'.
 2. Tax/turnover honesty: report true annualized one-way turnover per construction so the tax drag
    is auditable (15d score holds ~17x/yr vs monthly-rebal momentum ~lower).
 3. Holdout-ish split: in-sample (<=2024) vs recent (2025+) factor-alpha for construction A,
    to see if the (insignificant) residual alpha is stable or drifting.
 4. Regress construction A on the BEST benchmark variant (not just the default B) — does A's
    'beats B' alpha survive against a stronger momentum bar?

Run: PYTHONIOENCODING=utf-8 python -u experiments/momentum_stock_strategy/robustness.py
"""
from __future__ import annotations
import os, sys, math
from collections import defaultdict
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from database.models.core import AlgorithmVersion, Score  # noqa: E402

HOLD = 15
TAX_RATE = 0.30
START = '2021-01-01'
db = Score._meta.database
av = AlgorithmVersion.get_active_scores_version()


def cost_for_mcap(mc_bn):
    if mc_bn is None: return 0.0010
    if mc_bn >= 100: return 0.0002
    if mc_bn >= 20:  return 0.0004
    if mc_bn >= 10:  return 0.0007
    if mc_bn >= 2:   return 0.0015
    return 0.0040


def main():
    rows = db.execute_sql("SELECT symbol, market_cap FROM stocks WHERE sector IS NOT NULL").fetchall()
    syms = [r[0] for r in rows]
    mcap_bn = {r[0]: (float(r[1])/1e9 if r[1] is not None else None) for r in rows}
    symset = set(syms)
    rt_cost = {s: cost_for_mcap(mcap_bn.get(s)) for s in syms}
    closes = {}
    allsyms = syms + ['SPY']
    for i in range(0, len(allsyms), 150):
        chunk = allsyms[i:i+150]; ph = ','.join(['%s']*len(chunk))
        for sym, d, c in db.execute_sql(
            f"SELECT symbol,date,close FROM price_history WHERE symbol IN ({ph}) AND date>='2019-06-01' ORDER BY symbol,date",
            tuple(chunk)):
            closes.setdefault(sym, {})[d] = float(c)
    cal = sorted(closes['SPY'].keys()); cidx = {d: i for i, d in enumerate(cal)}; T = len(cal)
    def arr(s):
        cl = closes.get(s, {}); return np.array([cl.get(d, np.nan) for d in cal])
    sym_close = {s: arr(s) for s in syms}
    spy_c = arr('SPY'); spy_ret = np.concatenate([[np.nan], spy_c[1:]/spy_c[:-1]-1])

    def trail(s, t, lb, skip):
        a = sym_close[s]; i0, i1 = t-1-lb, t-1-skip
        if i0 < 0 or np.isnan(a[i0]) or np.isnan(a[i1]) or a[i0] <= 0: return None
        return a[i1]/a[i0]-1

    def momentum_bench(lb, skip, frac_denom, rebal):
        """Hold top (1/frac_denom) by trailing momentum; rebalance every `rebal` days. Return net series."""
        held = defaultdict(set); cur = set(); first_t = lb+1
        for t in range(first_t, T):
            if (t-first_t) % rebal == 0:
                sc = [(s, trail(s, t, lb, skip)) for s in syms]
                sc = [(s, v) for s, v in sc if v is not None]
                if len(sc) >= 50:
                    sc.sort(key=lambda x: x[1], reverse=True)
                    cur = set(s for s, _ in sc[:max(1, len(sc)//frac_denom)])
            held[t] = set(cur)
        return daily_series(held, sym_close, rt_cost, T)

    def daily_series(held, sym_close, rt_cost, T):
        pr = np.full(T, np.nan); cost = np.zeros(T)
        for t in range(1, T):
            names = held.get(t)
            if not names: continue
            rs = []
            for s in names:
                a = sym_close.get(s)
                if a is None or np.isnan(a[t]) or np.isnan(a[t-1]): continue
                rs.append(a[t]/a[t-1]-1)
            if rs: pr[t] = float(np.mean(rs))
            prev = held.get(t-1, set())
            n_book = max(len(names), 1); c = 0.0
            for s in (names - prev): c += rt_cost.get(s, 0.001)/2
            for s in (prev - names): c += rt_cost.get(s, 0.001)/2
            cost[t] = c/n_book
        return pr, pr - cost, cost, held

    def turnover_yr(held):
        """annualized one-way turnover = avg daily one-way name turnover / book * 252."""
        tot = 0.0; cnt = 0
        for t in range(1, T):
            names = held.get(t)
            if not names: continue
            prev = held.get(t-1, set())
            oneway = (len(names - prev) + len(prev - names)) / 2
            tot += oneway / max(len(names), 1); cnt += 1
        return tot/cnt*252 if cnt else 0.0

    def ann(s): return (np.prod(1+s))**(252/len(s))-1
    def shp(s): return s.mean()/s.std()*math.sqrt(252) if s.std() else 0.0
    def mdd(s):
        eq = np.cumprod(1+s); pk = np.maximum.accumulate(eq); return float((1-eq/pk).max())

    # ---- 1. benchmark spec sweep ----
    print("="*88)
    print("1. Is the momentum benchmark cherry-picked? Sweep of momentum 'bars to beat'")
    print("="*88)
    print(f"{'spec':<34}{'annNet':>9}{'annTax':>9}{'Sharpe':>8}{'maxDD':>8}{'turn/yr':>9}")
    bench_variants = {}
    for lb, skip, lbl in [(252, 21, '12-1mo'), (126, 0, '6mo'), (252, 0, '12mo')]:
        for fd, fl in [(10, 'decile'), (5, 'quintile')]:
            for rb, rl in [(21, 'monthly'), (63, 'quarterly')]:
                pr, net, cost, held = momentum_bench(lb, skip, fd, rb)
                mask = ~np.isnan(net) & ~np.isnan(spy_ret)
                nt = net[mask]
                at = ann(nt)*(1-TAX_RATE) if ann(nt) > 0 else ann(nt)
                spec = f"{lbl} {fl} {rl}"
                bench_variants[spec] = net
                print(f"{spec:<34}{ann(nt)*100:>+8.1f}%{at*100:>+8.1f}%{shp(nt):>8.2f}{mdd(nt)*100:>7.1f}%{turnover_yr(held):>8.1f}x")

    # ---- 2. construction A turnover ----
    sigs = db.execute_sql("SELECT symbol,date FROM scores WHERE version_id=%s AND overall>=75 AND date>=%s ORDER BY date",
                          (av.id, START)).fetchall()
    sigs = [(s, d) for s, d in sigs if s in symset and d in cidx]
    heldA = defaultdict(set)
    for s, d in sigs:
        i = cidx[d]
        for j in range(i+1, min(i+1+HOLD, T)): heldA[j].add(s)
    prA, netA, costA, _ = daily_series(heldA, sym_close, rt_cost, T)
    print(f"\n2. Construction A (v69 75+, 15d hold) annualized one-way turnover: {turnover_yr(heldA):.1f}x/yr")
    print(f"   (high turnover => the 30% short-term tax bites hard; momentum benchmark turns ~{turnover_yr(momentum_bench(252,21,10,21)[3]):.0f}x)")

    # ---- 3. in-sample vs recent split for A's factor alpha ----
    print("\n" + "="*88)
    print("3. Construction A factor-alpha stability: <=2024-12-31 vs 2025+ (is the residual stable?)")
    print("="*88)
    # momentum factor (top-quintile 126d) for regression
    momf = np.full(T, np.nan)
    for t in range(127, T):
        sc = [(s, trail(s, t, 126, 0)) for s in syms]; sc = [(s, v) for s, v in sc if v is not None]
        if len(sc) < 50: continue
        sc.sort(key=lambda x: x[1], reverse=True); top = [s for s, _ in sc[:max(1, len(sc)//5)]]
        rs = [sym_close[s][t]/sym_close[s][t-1]-1 for s in top if not np.isnan(sym_close[s][t]) and not np.isnan(sym_close[s][t-1])]
        if rs: momf[t] = float(np.mean(rs))
    import datetime as _dt
    _cut_date = _dt.date(2025, 1, 1)
    def _as_date(d):
        return d if isinstance(d, _dt.date) else _dt.date.fromisoformat(str(d)[:10])
    cut = next((i for i, d in enumerate(cal) if _as_date(d) >= _cut_date), T)
    def reg_window(lo, hi, label):
        idx = np.zeros(T, bool); idx[lo:hi] = True
        mask = idx & ~np.isnan(netA) & ~np.isnan(spy_ret) & ~np.isnan(momf)
        y = netA[mask]
        if len(y) < 60:
            print(f"   {label}: too few obs"); return
        X = np.column_stack([np.ones(len(y)), spy_ret[mask], momf[mask]])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None); resid = y - X@beta; n, k = X.shape
        cov = (resid@resid/(n-k))*np.linalg.inv(X.T@X); se = np.sqrt(np.diag(cov)); tv = beta/se
        print(f"   {label:<18} alpha {((1+beta[0])**252-1)*100:+.1f}%/yr  t={tv[0]:.2f}  mom-b {beta[2]:.2f}  N={len(y)}")
    reg_window(0, cut, 'in-sample <=2024')
    reg_window(cut, T, '2025+ recent')

    # ---- 4. A vs the STRONGEST benchmark variant ----
    print("\n" + "="*88)
    print("4. Does A's alpha survive vs the STRONGEST momentum bar (not the default)?")
    print("="*88)
    best_spec = max(bench_variants.items(), key=lambda kv: (lambda s: ann(s[~np.isnan(s)&~np.isnan(spy_ret)]))(kv[1]))
    bname, bser = best_spec
    print(f"   strongest momentum benchmark = '{bname}'")
    mask = ~np.isnan(netA) & ~np.isnan(spy_ret) & ~np.isnan(bser)
    y = netA[mask]
    X = np.column_stack([np.ones(len(y)), spy_ret[mask], bser[mask]])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None); resid = y - X@beta; n, k = X.shape
    cov = (resid@resid/(n-k))*np.linalg.inv(X.T@X); se = np.sqrt(np.diag(cov)); tv = beta/se
    print(f"   A ~ alpha + mkt + bestBench:  alpha {((1+beta[0])**252-1)*100:+.1f}%/yr  t={tv[0]:.2f}  "
          f"bench-beta {beta[2]:.2f} (t={tv[2]:.1f})")
    verdict = "SIGNIFICANT" if tv[0] > 2 else "NOT significant"
    print(f"   -> A's alpha beyond the strongest momentum bar is {verdict} (t={tv[0]:.2f}).")


if __name__ == '__main__':
    main()
