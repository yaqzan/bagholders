"""Exploration sweep — WHERE does the score's alpha-beyond-momentum live (if anywhere)?

The stock-vehicle test showed the 75+ long portfolio beats SPY but it's ~mostly momentum
(mom-beta 0.49 t=19); the alpha beyond market+momentum was +11.4%/yr at t=1.35 (not sig).
This sweep hunts for a configuration where that alpha IS significant (t>2):
  A. score-tier sweep (75/80/85/90/95) — is alpha concentrated in the top tiers?
  B. hold-period sweep (5/10/15/20/30d) — is the edge faster/slower than 15d?
  C. trend-component split — LOW-trend 75+ signals are scored high by NON-momentum info
     (RSI/BB/weekly/volume); if THEY have alpha, the score adds something momentum doesn't.
  D. RSI-component split.
  E. long/short (long 75+ minus short <=25) — market-neutral, isolates pure selection alpha.

For each config: calendar-time portfolio -> regress daily ret on SPY + built momentum factor
-> report alpha (annualized) + t-stat + betas. Ranked by |alpha t|. If nothing clears t>2,
the honest answer is the score does NOT reliably add alpha beyond momentum.

Run: PYTHONIOENCODING=utf-8 python -u experiments/v69_portfolio_retune/stock_alpha_sweep.py
"""
from __future__ import annotations
import os, sys, math
from collections import defaultdict
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from database.models.core import AlgorithmVersion, Score  # noqa: E402

MOM_LB = 126
COST_RT = 0.001
db = Score._meta.database
av = AlgorithmVersion.get_active_scores_version()


def main():
    print("loading prices + signals...", flush=True)
    syms = [r[0] for r in db.execute_sql("SELECT symbol FROM stocks WHERE sector IS NOT NULL").fetchall()]
    symset = set(syms)
    closes = {}
    allsyms = syms + ['SPY']
    for i in range(0, len(allsyms), 150):
        chunk = allsyms[i:i+150]; ph = ','.join(['%s']*len(chunk))
        for sym, d, c in db.execute_sql(
            f"SELECT symbol,date,close FROM price_history WHERE symbol IN ({ph}) AND date>='2020-06-01' ORDER BY symbol,date",
            tuple(chunk)):
            closes.setdefault(sym, {})[d] = float(c)
    cal = sorted(closes['SPY'].keys()); cidx = {d: i for i, d in enumerate(cal)}; T = len(cal)
    def arr(s):
        cl = closes.get(s, {}); return np.array([cl.get(d, np.nan) for d in cal])
    sym_close = {s: arr(s) for s in syms}
    spy_c = arr('SPY'); spy_ret = np.concatenate([[np.nan], spy_c[1:]/spy_c[:-1]-1])

    # signals with components: overall, trend, rsi (for conditioning)
    sigs = db.execute_sql(
        "SELECT symbol,date,overall,trend,rsi FROM scores WHERE version_id=%s "
        "AND (overall>=70 OR overall<=25) AND date>='2021-01-01' ORDER BY date", (av.id,)).fetchall()
    sigs = [(s, d, ov, tr, rs) for (s, d, ov, tr, rs) in sigs if s in symset and d in cidx]

    # momentum factor (top-quintile trailing-126d return, equal weight, next-day)
    print("building momentum factor...", flush=True)
    mom_ret = np.full(T, np.nan)
    for t in range(MOM_LB+1, T):
        sc = []
        for s in syms:
            a = sym_close[s]
            if not np.isnan(a[t-1]) and not np.isnan(a[t-1-MOM_LB]) and a[t-1-MOM_LB] > 0:
                sc.append((s, a[t-1]/a[t-1-MOM_LB]-1))
        if len(sc) < 50: continue
        sc.sort(key=lambda x: x[1], reverse=True)
        top = [s for s, _ in sc[:max(1, len(sc)//5)]]
        rs = [sym_close[s][t]/sym_close[s][t-1]-1 for s in top
              if not np.isnan(sym_close[s][t]) and not np.isnan(sym_close[s][t-1])]
        if rs: mom_ret[t] = float(np.mean(rs))

    def portfolio(sigset, hold, short=False):
        held = defaultdict(set)
        for s, d in sigset:
            i = cidx[d]
            for j in range(i+1, min(i+1+hold, T)): held[j].add(s)
        pr = np.full(T, np.nan); nh = np.zeros(T)
        for t in range(1, T):
            names = held.get(t)
            if not names: continue
            rs = []
            for s in names:
                a = sym_close.get(s)
                if a is None or np.isnan(a[t]) or np.isnan(a[t-1]): continue
                rs.append(a[t]/a[t-1]-1)
            if rs: pr[t] = float(np.mean(rs)); nh[t] = len(rs)
        if short: pr = -pr
        return pr, nh

    def regress(pr):
        mask = ~np.isnan(pr) & ~np.isnan(spy_ret) & ~np.isnan(mom_ret)
        p, m, mo = pr[mask], spy_ret[mask], mom_ret[mask]
        if len(p) < 60: return None
        X = np.column_stack([np.ones(len(p)), m, mo])
        beta, *_ = np.linalg.lstsq(X, p, rcond=None)
        resid = p - X@beta; n, k = X.shape
        cov = (resid@resid/(n-k))*np.linalg.inv(X.T@X); se = np.sqrt(np.diag(cov))
        tv = beta/se
        ann_alpha = (1+beta[0])**252 - 1
        ann_ret = (np.prod(1+p))**(252/len(p))-1
        return dict(alpha=ann_alpha, alpha_t=tv[0], mkt=beta[1], mom=beta[2], mom_t=tv[2],
                    ann_ret=ann_ret, n=len(p))

    longs = [(s, d) for (s, d, ov, tr, rs) in sigs if ov >= 70]
    def tier(th): return [(s, d) for (s, d, ov, tr, rs) in sigs if ov >= th]
    shorts = [(s, d) for (s, d, ov, tr, rs) in sigs if ov <= 25]

    configs = []
    # A. tier sweep @ hold 15
    for th in (75, 80, 85, 90, 95):
        configs.append((f"A tier {th}+ h15", tier(th), 15, False))
    # B. hold sweep @ 75+
    for h in (5, 10, 20, 30):
        configs.append((f"B 75+ h{h}", tier(75), h, False))
    # C. trend-component split @ 75+ h15
    tr75 = [(s, d, tr) for (s, d, ov, tr, rs) in sigs if ov >= 75 and tr is not None]
    if tr75:
        trs = sorted(x[2] for x in tr75); lo, hi = trs[len(trs)//3], trs[2*len(trs)//3]
        configs.append(("C 75+ LOW-trend h15", [(s, d) for s, d, tr in tr75 if tr <= lo], 15, False))
        configs.append(("C 75+ HIGH-trend h15", [(s, d) for s, d, tr in tr75 if tr >= hi], 15, False))
    # D. RSI-component split @ 75+ h15
    rs75 = [(s, d, rs) for (s, d, ov, tr, rs) in sigs if ov >= 75 and rs is not None]
    if rs75:
        rss = sorted(x[2] for x in rs75); lo, hi = rss[len(rss)//3], rss[2*len(rss)//3]
        configs.append(("D 75+ LOW-rsi h15", [(s, d) for s, d, rs in rs75 if rs <= lo], 15, False))
        configs.append(("D 75+ HIGH-rsi h15", [(s, d) for s, d, rs in rs75 if rs >= hi], 15, False))

    print(f"\n{'config':<22}{'N sig':>7}{'ann.ret':>9}{'alpha':>9}{'alpha_t':>9}{'mkt-b':>7}{'mom-b':>7}")
    results = []
    for name, sset, hold, short in configs:
        pr, nh = portfolio(sset, hold, short)
        r = regress(pr)
        if r is None: continue
        results.append((name, len(sset), r))
        print(f"{name:<22}{len(sset):>7}{r['ann_ret']*100:>+8.1f}%{r['alpha']*100:>+8.1f}%"
              f"{r['alpha_t']:>9.2f}{r['mkt']:>7.2f}{r['mom']:>7.2f}")
    # E. long/short market-neutral (long 75+ minus short <=25)
    lp, _ = portfolio(longs, 15, False); sp, _ = portfolio(shorts, 15, True)
    ls = np.where(~np.isnan(lp) & ~np.isnan(sp), 0.5*lp + 0.5*sp, np.nan)
    r = regress(ls)
    if r:
        print(f"{'E L/S 75+ vs <=25 h15':<22}{len(longs)+len(shorts):>7}{r['ann_ret']*100:>+8.1f}%"
              f"{r['alpha']*100:>+8.1f}%{r['alpha_t']:>9.2f}{r['mkt']:>7.2f}{r['mom']:>7.2f}")
        results.append(("E L/S", 0, r))

    print("\n--- ranked by |alpha t| ---")
    for name, n, r in sorted(results, key=lambda x: -abs(x[2]['alpha_t']))[:6]:
        sig = "SIGNIFICANT" if abs(r['alpha_t']) > 2 else "not sig"
        print(f"  {name:<22} alpha {r['alpha']*100:+.1f}%/yr  t={r['alpha_t']:.2f}  [{sig}]")


if __name__ == '__main__':
    main()
