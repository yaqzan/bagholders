"""Cross-version TRUE ALPHA under HOLD — look-ahead-free via the FRIDAY subset.

User pivot: HOLD (ride to TP / day-15 hard-sell) beats CUT (early-stop + recycle) on
honest v69. So re-rank scoring VERSIONS under HOLD to find which carries the most TRUE
alpha -- NOT the levered options compound (leverage x frictionless, dies to spread), but
the factor-adjusted (market + momentum) alpha of the 15-day-hold long STOCK portfolio
(HOLD = 15-day hold, no early stop; the stock vehicle is the fundable one).

CONTAMINATION GATE: the day-of-week test proved ONLY v69 is look-ahead-free; v57/v59/v60
are weekly-inflated. BUT all versions converge to ~56% win on FRIDAY (week complete, no
future left to leak). So each version's FRIDAY 75+/85+ cohort is honest -> a clean
cross-version comparison from existing stored rows, NO recalc. all-day shown beside it as
the inflated reference (how much of each version's apparent alpha was the leak).

Run: PYTHONIOENCODING=utf-8 python -u experiments/v69_portfolio_retune/friday_honest_alpha.py
"""
from __future__ import annotations
import os, sys
from collections import defaultdict
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from database.models.core import Score   # noqa: E402

MOM_LB = 126
HOLD = 15
db = Score._meta.database
VERSIONS = [(57, 'v57 MktWave'), (59, 'v59 VolAuth'), (60, 'v60 SCW'), (69, 'v69 blend')]
THRESHOLDS = [75, 85]


def main():
    print("loading universe + closes...", flush=True)
    syms = [r[0] for r in db.execute_sql(
        "SELECT symbol FROM stocks WHERE sector IS NOT NULL").fetchall()]
    symset = set(syms); allsyms = syms + ['SPY']
    closes = {}
    for i in range(0, len(allsyms), 150):
        chunk = allsyms[i:i+150]; ph = ','.join(['%s'] * len(chunk))
        for sym, d, c in db.execute_sql(
                f"SELECT symbol,date,close FROM price_history WHERE symbol IN ({ph}) "
                f"AND date>='2015-06-01' ORDER BY symbol,date", tuple(chunk)):
            closes.setdefault(sym, {})[d] = float(c)
    cal = sorted(closes['SPY'].keys()); cidx = {d: i for i, d in enumerate(cal)}; T = len(cal)

    def arr(s):
        cl = closes.get(s, {}); return np.array([cl.get(d, np.nan) for d in cal])
    sym_close = {s: arr(s) for s in syms}
    spy_c = arr('SPY'); spy_ret = np.concatenate([[np.nan], spy_c[1:] / spy_c[:-1] - 1])

    print("building honest momentum factor...", flush=True)
    mom_ret = np.full(T, np.nan)
    for t in range(MOM_LB + 1, T):
        sc = []
        for s in syms:
            a = sym_close[s]
            if not np.isnan(a[t-1]) and not np.isnan(a[t-1-MOM_LB]) and a[t-1-MOM_LB] > 0:
                sc.append((s, a[t-1] / a[t-1-MOM_LB] - 1))
        if len(sc) < 50:
            continue
        sc.sort(key=lambda x: x[1], reverse=True)
        top = [s for s, _ in sc[:max(1, len(sc) // 5)]]
        rs = [sym_close[s][t] / sym_close[s][t-1] - 1 for s in top
              if not np.isnan(sym_close[s][t]) and not np.isnan(sym_close[s][t-1])]
        if rs:
            mom_ret[t] = float(np.mean(rs))

    def portfolio(sigset):
        held = defaultdict(set)
        for s, d in sigset:
            i = cidx[d]
            for j in range(i+1, min(i+1+HOLD, T)):
                held[j].add(s)
        pr = np.full(T, np.nan)
        for t in range(1, T):
            names = held.get(t)
            if not names:
                continue
            rs = []
            for s in names:
                a = sym_close.get(s)
                if a is None or np.isnan(a[t]) or np.isnan(a[t-1]):
                    continue
                rs.append(a[t] / a[t-1] - 1)
            if rs:
                pr[t] = float(np.mean(rs))
        return pr

    def regress(pr):
        mask = ~np.isnan(pr) & ~np.isnan(spy_ret) & ~np.isnan(mom_ret)
        p, m, mo = pr[mask], spy_ret[mask], mom_ret[mask]
        if len(p) < 60:
            return None
        X = np.column_stack([np.ones(len(p)), m, mo])
        beta, *_ = np.linalg.lstsq(X, p, rcond=None)
        resid = p - X @ beta; n, k = X.shape
        cov = (resid @ resid / (n - k)) * np.linalg.inv(X.T @ X)
        se = np.sqrt(np.diag(cov)); tv = beta / se
        return dict(alpha=(1+beta[0])**252 - 1, alpha_t=tv[0], mom=beta[2], mkt=beta[1],
                    ann=(np.prod(1+p))**(252/len(p)) - 1, n=len(p))

    print(f"\n{'version':<14}{'thr':>4}{'subset':>9}{'Nsig':>7}{'annRet':>9}{'alpha':>9}"
          f"{'alpha_t':>9}{'mkt_b':>7}{'mom_b':>7}")
    honest = []
    for vid, vlab in VERSIONS:
        sigs = db.execute_sql(
            "SELECT symbol,date,overall FROM scores WHERE version_id=%s AND overall>=75 "
            "AND date>='2016-01-01'", (vid,)).fetchall()
        sigs = [(s, d, ov) for (s, d, ov) in sigs if s in symset and d in cidx]
        for thr in THRESHOLDS:
            base = [(s, d) for (s, d, ov) in sigs if ov >= thr]
            fri = [(s, d) for (s, d) in base if d.weekday() == 4]
            for sub, sset in [('all-day', base), ('FRIDAY', fri)]:
                r = regress(portfolio(sset))
                if r is None:
                    continue
                print(f"{vlab:<14}{thr:>4}{sub:>9}{len(sset):>7}{r['ann']*100:>+8.1f}%"
                      f"{r['alpha']*100:>+8.1f}%{r['alpha_t']:>9.2f}{r['mkt']:>7.2f}{r['mom']:>7.2f}")
                if sub == 'FRIDAY':
                    honest.append((vlab, thr, r))
        print()

    print("=== ranked by HONEST (Friday) alpha t-stat — the true-alpha-under-HOLD ranking ===")
    for vlab, thr, r in sorted(honest, key=lambda x: -x[2]['alpha_t']):
        sig = 'SIGNIFICANT' if abs(r['alpha_t']) > 2 else 'not sig'
        print(f"  {vlab:<14} {thr}+  alpha {r['alpha']*100:+.1f}%/yr  t={r['alpha_t']:.2f}  "
              f"mom_b={r['mom']:.2f}  [{sig}]")
    print("\nFRIDAY rows = honest. all-day = inflated reference. TRUE alpha = market+momentum-adjusted.")
    print("If no version's FRIDAY alpha clears t>2, the honest answer is: no version adds alpha beyond momentum.")


if __name__ == '__main__':
    main()
