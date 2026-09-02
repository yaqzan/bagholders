"""Momentum STOCK strategy reckoning — does the v69 score beat 'just buy MTUM'?

GOAL: decide HONESTLY whether the v69 directional signal supports a fundable momentum
STOCK strategy that beats SPY *and* a pure-momentum benchmark on a risk-adjusted,
cost-AND-tax-aware basis — or whether the honest answer is "just buy momentum".

One calendar-time daily-return framework. Constructions:
  (A) v69 75+ score portfolio, 15-day holds, equal weight.
  (B) PURE MOMENTUM BENCHMARK: top-decile trailing 6-12mo return, equal weight,
      monthly rebalance. This is the bar to beat.
  (C) score-tilt-within-momentum: restrict the v69 75+ names to the high-momentum
      subset (does the score discriminate forward returns AMONG momentum names?).
  (C2) the COMPLEMENT: v69 75+ names that are NOT high-momentum (is there a
       non-momentum pocket the score is picking up?).
  (D) momentum-quality screen baseline: top-decile momentum that ALSO has a v69 score
      (any score) — isolates "momentum names the system covers" from "the v69 signal".

Benchmarks: SPY (market) and a momentum FACTOR (top-quintile trailing-126d, equal weight).
MTUM is not in the DB; the top-decile 6-12mo rule (B) is the real-money proxy a retail
trader would actually run, and MTUM tracks exactly this kind of construction.

Frictions, applied per construction:
  - bid-ask: per-name round-trip cost from market cap (mega ~0.02%, micro ~0.10%+),
    charged on daily turnover.
  - short-term-gains tax: 30% of NET positive gains (15-day holds = ordinary income),
    applied to the net-of-cost annual return. Turnover ~17x/yr so this is a big drag and
    we make it explicit.

For EACH construction: annualized return (gross / net-cost / after-tax), Sharpe, max DD,
and factor-regression alpha (annualized) + t-stat + momentum beta — regressed on
SPY + momentum factor, AND separately on SPY + the pure-momentum BENCHMARK (B) so we can
directly answer "does it beat the bar to beat with significant alpha".

DECISIVE OUTPUT: does ANY construction beat the PURE momentum benchmark (B) with
statistically significant (t>2) alpha after cost+tax?

Run: PYTHONIOENCODING=utf-8 python -u experiments/momentum_stock_strategy/backtest.py
"""
from __future__ import annotations
import os, sys, math, json
from collections import defaultdict
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from database.models.core import AlgorithmVersion, Score  # noqa: E402

HOLD = 15            # trading-day hold for score portfolios
MOM_LB = 126         # ~6m trailing momentum (factor)
MOM_LONG = 252       # ~12m trailing for the 6-12mo benchmark
MOM_SKIP = 21        # skip most-recent month (standard momentum: 12-1)
REBAL = 21           # monthly rebalance for the benchmark
TAX_RATE = 0.30      # short-term-gains tax on net positive gains (ordinary income)
START = '2021-01-01'

db = Score._meta.database
av = AlgorithmVersion.get_active_scores_version()


def fetch(sql, params=()):
    return db.execute_sql(sql, params).fetchall()


# ------- per-name round-trip stock cost from market cap (bid-ask proxy) -------
def cost_for_mcap(mc_bn):
    """Round-trip bid-ask cost as a fraction. Mega-caps ~0.02%, micro ~0.10%+.
    Conservative-but-realistic ladder grounded in liquid US equity spreads."""
    if mc_bn is None:
        return 0.0010
    if mc_bn >= 100: return 0.0002   # mega-cap: 2 bps round trip
    if mc_bn >= 20:  return 0.0004   # large
    if mc_bn >= 10:  return 0.0007   # mid
    if mc_bn >= 2:   return 0.0015   # small
    return 0.0040                     # micro: 40 bps round trip


def main():
    print(f"active version id={av.id}  '{(av.git_message or '')[:50]}'", flush=True)
    print("loading price history...", flush=True)
    rows = fetch("SELECT symbol, market_cap FROM stocks WHERE sector IS NOT NULL")
    syms = [r[0] for r in rows]
    mcap_bn = {r[0]: (float(r[1]) / 1e9 if r[1] is not None else None) for r in rows}
    symset = set(syms)
    rt_cost = {s: cost_for_mcap(mcap_bn.get(s)) for s in syms}

    closes = {}
    allsyms = syms + ['SPY']
    for i in range(0, len(allsyms), 150):
        chunk = allsyms[i:i+150]
        ph = ','.join(['%s'] * len(chunk))
        for sym, d, c in db.execute_sql(
            f"SELECT symbol,date,close FROM price_history WHERE symbol IN ({ph}) "
            f"AND date>='2019-06-01' ORDER BY symbol,date", tuple(chunk)):
            closes.setdefault(sym, {})[d] = float(c)
    cal = sorted(closes['SPY'].keys())
    cidx = {d: i for i, d in enumerate(cal)}
    T = len(cal)
    print(f"  {len(syms)} symbols, {T} trading days {cal[0]}..{cal[-1]}", flush=True)

    def arr(s):
        cl = closes.get(s, {})
        return np.array([cl.get(d, np.nan) for d in cal])
    sym_close = {s: arr(s) for s in syms}
    spy_c = arr('SPY')
    spy_ret = np.concatenate([[np.nan], spy_c[1:] / spy_c[:-1] - 1])

    # trailing return helper (12-1 momentum: return from t-1-MOM_LONG to t-1-MOM_SKIP)
    def trailing_mom(s, t):
        a = sym_close[s]
        i0, i1 = t - 1 - MOM_LONG, t - 1 - MOM_SKIP
        if i0 < 0 or np.isnan(a[i0]) or np.isnan(a[i1]) or a[i0] <= 0:
            return None
        return a[i1] / a[i0] - 1

    # short trailing (126d, for factor) — return from t-1-126 to t-1
    def trailing_126(s, t):
        a = sym_close[s]
        i0 = t - 1 - MOM_LB
        if i0 < 0 or np.isnan(a[i0]) or np.isnan(a[t-1]) or a[i0] <= 0:
            return None
        return a[t-1] / a[i0] - 1

    # =================== signals (v69 75+) ===================
    sigs = fetch("SELECT symbol,date,overall FROM scores WHERE version_id=%s AND overall>=75 "
                 "AND date>=%s ORDER BY date", (av.id, START))
    sigs = [(s, d, ov) for (s, d, ov) in sigs if s in symset and d in cidx]
    print(f"  {len(sigs)} usable 75+ signals", flush=True)

    # all scored names per day (any score) — for the momentum-coverage baseline D
    scored_on = defaultdict(set)
    for s, d, ov in fetch("SELECT symbol,date,overall FROM scores WHERE version_id=%s "
                          "AND date>=%s", (av.id, START)):
        if s in symset and d in cidx:
            scored_on[cidx[d]].add(s)

    # =================== momentum factor (daily, top-quintile 126d) ===================
    print("building momentum factor (top-quintile 126d)...", flush=True)
    mom_factor = np.full(T, np.nan)
    for t in range(MOM_LB + 1, T):
        sc = [(s, trailing_126(s, t)) for s in syms]
        sc = [(s, v) for s, v in sc if v is not None]
        if len(sc) < 50:
            continue
        sc.sort(key=lambda x: x[1], reverse=True)
        top = [s for s, _ in sc[:max(1, len(sc)//5)]]
        rs = [sym_close[s][t]/sym_close[s][t-1]-1 for s in top
              if not np.isnan(sym_close[s][t]) and not np.isnan(sym_close[s][t-1])]
        if rs:
            mom_factor[t] = float(np.mean(rs))

    # =================== (B) PURE MOMENTUM BENCHMARK: top-decile 12-1, monthly rebal ===================
    # Hold the top-decile-by-12-1-momentum basket; rebalance every REBAL days.
    print("building pure-momentum benchmark (top-decile 12-1, monthly rebal)...", flush=True)
    bench_held = defaultdict(set)
    cur = set()
    first_t = MOM_LONG + 1
    for t in range(first_t, T):
        if (t - first_t) % REBAL == 0:
            sc = [(s, trailing_mom(s, t)) for s in syms]
            sc = [(s, v) for s, v in sc if v is not None]
            if len(sc) >= 50:
                sc.sort(key=lambda x: x[1], reverse=True)
                cur = set(s for s, _ in sc[:max(1, len(sc)//10)])  # top decile
        bench_held[t] = set(cur)

    # =================== momentum percentile per (name, day) for tilt constructions ===================
    # For tilt (C): need each day a set of "high-momentum" names = top-decile 12-1.
    # Precompute the top-decile membership each day (continuous, not just rebal days) so we
    # can intersect with the score signals at entry time.
    print("computing daily top-decile momentum membership...", flush=True)
    topdec_on = {}   # t -> set of names in top decile of 12-1 momentum that day
    for t in range(first_t, T):
        sc = [(s, trailing_mom(s, t)) for s in syms]
        sc = [(s, v) for s, v in sc if v is not None]
        if len(sc) < 50:
            topdec_on[t] = set()
            continue
        sc.sort(key=lambda x: x[1], reverse=True)
        topdec_on[t] = set(s for s, _ in sc[:max(1, len(sc)//10)])

    # =================== portfolio builders ===================
    def held_from_signals(sig_list):
        """sig_list = [(sym, date)] entries; hold each HOLD trading days."""
        held = defaultdict(set)
        for s, d in sig_list:
            i = cidx[d]
            for j in range(i+1, min(i+1+HOLD, T)):
                held[j].add(s)
        return held

    def daily_series(held):
        """Return (gross_ret, net_ret, nheld, daily_turn) arrays from a {t:set} holdings map."""
        pr = np.full(T, np.nan)
        nh = np.zeros(T)
        cost = np.zeros(T)
        for t in range(1, T):
            names = held.get(t)
            if not names:
                continue
            rs = []
            for s in names:
                a = sym_close.get(s)
                if a is None or np.isnan(a[t]) or np.isnan(a[t-1]):
                    continue
                rs.append(a[t]/a[t-1]-1)
            if rs:
                pr[t] = float(np.mean(rs))
                nh[t] = len(rs)
            prev = held.get(t-1, set())
            entered = names - prev
            exited = prev - names
            # cost charged this day = (sum of half-spread on entries + half on exits) / book size
            n_book = max(len(names), 1)
            c = 0.0
            for s in entered:
                c += rt_cost.get(s, 0.0010) / 2
            for s in exited:
                c += rt_cost.get(s, 0.0010) / 2
            cost[t] = c / n_book
        net = pr - cost
        return pr, net, nh, cost

    # build holdings maps
    held_A = held_from_signals([(s, d) for s, d, ov in sigs])
    # C: score signals whose name is in the top-decile momentum at entry day
    sig_C = [(s, d) for s, d, ov in sigs if s in topdec_on.get(cidx[d], set())]
    held_C = held_from_signals(sig_C)
    # C2: score signals NOT in top-decile momentum at entry (non-momentum pocket)
    sig_C2 = [(s, d) for s, d, ov in sigs if s not in topdec_on.get(cidx[d], set())]
    held_C2 = held_from_signals(sig_C2)
    print(f"  C (75+ AND top-decile-mom): {len(sig_C)} sigs | C2 (75+ NOT top-decile): {len(sig_C2)} sigs", flush=True)

    # D: momentum-coverage baseline — top-decile momentum names that have ANY v69 score that day,
    # held with the same monthly-rebal logic (isolates 'momentum names the system covers').
    bench_cov = defaultdict(set)
    cur = set()
    for t in range(first_t, T):
        if (t - first_t) % REBAL == 0:
            sc = [(s, trailing_mom(s, t)) for s in syms if s in scored_on.get(t, set())]
            sc = [(s, v) for s, v in sc if v is not None]
            if len(sc) >= 30:
                sc.sort(key=lambda x: x[1], reverse=True)
                cur = set(s for s, _ in sc[:max(1, len(sc)//10)])
        bench_cov[t] = set(cur)

    # =================== series ===================
    gA, nA, nhA, cA = daily_series(held_A)
    gC, nC, nhC, cC = daily_series(held_C)
    gC2, nC2, nhC2, cC2 = daily_series(held_C2)
    gB, nB, nhB, cB = daily_series(bench_held)
    gD, nD, nhD, cD = daily_series(bench_cov)

    # common valid mask (need SPY, momentum factor, AND the benchmark B for regression)
    base_mask = ~np.isnan(spy_ret) & ~np.isnan(mom_factor) & ~np.isnan(nB)
    m = spy_ret[base_mask]
    mo = mom_factor[base_mask]
    bench_b = nB[base_mask]   # net benchmark series as a regressor

    def metrics(gross, net, nh, cost, label):
        mask = base_mask & ~np.isnan(net)
        if mask.sum() < 60:
            return None
        g = gross[mask]; nt = net[mask]
        days = mask.sum(); yrs = days / 252
        def ann(s): return (np.prod(1+s))**(252/len(s)) - 1
        def shp(s): return s.mean()/s.std()*math.sqrt(252) if s.std() else 0.0
        def mdd(s):
            eq = np.cumprod(1+s); pk = np.maximum.accumulate(eq)
            return float((1 - eq/pk).max())
        ann_g = ann(g); ann_n = ann(nt)
        # after-tax: tax 30% of net positive annual return (15d holds = ordinary income).
        # Applied to the COMPOUND net annual return (the realized gain each year is taxed).
        ann_tax = ann_n * (1 - TAX_RATE) if ann_n > 0 else ann_n
        avg_n = nh[mask].mean()
        turn_yr = (cost[mask] > 0).sum()  # not used; compute true turnover below
        # annualized one-way turnover = avg daily (entries+exits)/2 / book * 252 ... approx via cost
        cost_yr = cost[mask].mean() * 252
        return dict(label=label, yrs=yrs, ann_g=ann_g, ann_n=ann_n, ann_tax=ann_tax,
                    sharpe_n=shp(nt), sharpe_g=shp(g), mdd_n=mdd(nt), avg_n=avg_n,
                    cost_yr=cost_yr, net_series=nt, mask=mask)

    def regress(net_series, mask, factors, fnames):
        """Regress net daily series on [1] + factors (each a full-length array, sliced by mask)."""
        y = net_series  # already masked to `mask`
        cols = [np.ones(len(y))]
        for f in factors:
            cols.append(f[mask])
        X = np.column_stack(cols)
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X@beta
        n, k = X.shape
        sigma2 = resid@resid/(n-k)
        cov = sigma2*np.linalg.inv(X.T@X)
        se = np.sqrt(np.diag(cov))
        tv = beta/se
        ss_res = resid@resid; ss_tot = ((y-y.mean())**2).sum()
        out = dict(alpha_d=beta[0], alpha_ann=(1+beta[0])**252-1, alpha_t=tv[0],
                   r2=1-ss_res/ss_tot, betas={}, tvals={})
        for i, nm in enumerate(fnames):
            out['betas'][nm] = beta[i+1]
            out['tvals'][nm] = tv[i+1]
        return out

    constructions = [
        ('A: v69 75+ (15d hold)', gA, nA, nhA, cA),
        ('B: PURE MOM top-decile', gB, nB, nhB, cB),
        ('C: 75+ AND high-mom', gC, nC, nhC, cC),
        ('C2: 75+ NOT high-mom', gC2, nC2, nhC2, cC2),
        ('D: mom-cov top-decile', gD, nD, nhD, cD),
        ('SPY', spy_c[:0], None, None, None),  # handled separately
    ]

    # SPY metrics
    spy_mask = base_mask & ~np.isnan(spy_ret)
    sm = spy_ret[spy_mask]
    def ann_(s): return (np.prod(1+s))**(252/len(s)) - 1
    def shp_(s): return s.mean()/s.std()*math.sqrt(252) if s.std() else 0.0
    def mdd_(s):
        eq = np.cumprod(1+s); pk = np.maximum.accumulate(eq); return float((1-eq/pk).max())

    print("\n" + "="*94)
    print("CONSTRUCTION RETURNS (one calendar-time framework)")
    print("="*94)
    hdr = f"{'construction':<26}{'annG':>8}{'annNet':>8}{'annTax':>8}{'Sharpe':>8}{'maxDD':>8}{'avgN':>6}{'cost/yr':>8}"
    print(hdr)
    results = {}
    for label, gross, net, nh, cost in constructions[:-1]:
        mt = metrics(gross, net, nh, cost, label)
        if mt is None:
            continue
        results[label] = mt
        print(f"{label:<26}{mt['ann_g']*100:>+7.1f}%{mt['ann_n']*100:>+7.1f}%{mt['ann_tax']*100:>+7.1f}%"
              f"{mt['sharpe_n']:>8.2f}{mt['mdd_n']*100:>7.1f}%{mt['avg_n']:>6.0f}{mt['cost_yr']*100:>7.2f}%")
    print(f"{'SPY (benchmark)':<26}{ann_(sm)*100:>+7.1f}%{ann_(sm)*100:>+7.1f}%"
          f"{(ann_(sm)*(1-TAX_RATE) if ann_(sm)>0 else ann_(sm))*100:>+7.1f}%"
          f"{shp_(sm):>8.2f}{mdd_(sm)*100:>7.1f}%{'1':>6}{'0.00':>8}%")

    # =================== factor regressions ===================
    print("\n" + "="*94)
    print("FACTOR REGRESSION 1: net ~ alpha + b_mkt*SPY + b_mom*MOMfactor")
    print("="*94)
    print(f"{'construction':<26}{'alphaAnn':>10}{'alpha_t':>9}{'mkt-b':>8}{'mom-b':>8}{'mom-t':>8}{'R2':>7}")
    reg1 = {}
    for label in ['A: v69 75+ (15d hold)', 'B: PURE MOM top-decile', 'C: 75+ AND high-mom',
                  'C2: 75+ NOT high-mom', 'D: mom-cov top-decile']:
        if label not in results: continue
        mt = results[label]
        r = regress(mt['net_series'], mt['mask'], [spy_ret, mom_factor], ['mkt', 'mom'])
        reg1[label] = r
        print(f"{label:<26}{r['alpha_ann']*100:>+9.1f}%{r['alpha_t']:>9.2f}"
              f"{r['betas']['mkt']:>8.2f}{r['betas']['mom']:>8.2f}{r['tvals']['mom']:>8.1f}{r['r2']:>7.2f}")

    print("\n" + "="*94)
    print("FACTOR REGRESSION 2 (DECISIVE): net ~ alpha + b_mkt*SPY + b_B*BENCHMARK_B")
    print("  -> alpha here = return BEYOND the pure-momentum benchmark you'd actually buy")
    print("="*94)
    print(f"{'construction':<26}{'alphaAnn':>10}{'alpha_t':>9}{'mkt-b':>8}{'B-beta':>8}{'B-t':>8}{'R2':>7}{'  beats B?':>12}")
    reg2 = {}
    for label in ['A: v69 75+ (15d hold)', 'C: 75+ AND high-mom', 'C2: 75+ NOT high-mom',
                  'D: mom-cov top-decile']:
        if label not in results: continue
        mt = results[label]
        r = regress(mt['net_series'], mt['mask'], [spy_ret, nB], ['mkt', 'benchB'])
        reg2[label] = r
        beats = 'YES t>2' if r['alpha_t'] > 2 else ('pos n.s.' if r['alpha_t'] > 0 else 'NO (neg)')
        print(f"{label:<26}{r['alpha_ann']*100:>+9.1f}%{r['alpha_t']:>9.2f}"
              f"{r['betas']['mkt']:>8.2f}{r['betas']['benchB']:>8.2f}{r['tvals']['benchB']:>8.1f}{r['r2']:>7.2f}{beats:>12}")

    # =================== DECISIVE verdict ===================
    print("\n" + "="*94)
    print("DECISIVE QUESTION: does ANY construction beat the PURE momentum benchmark with t>2 alpha")
    print("                  (after cost) — regression 2, alpha vs benchmark B?")
    print("="*94)
    winner = None
    for label, r in reg2.items():
        tag = "SIGNIFICANT (t>2)" if r['alpha_t'] > 2 else "not significant"
        print(f"  {label:<26} alpha vs B = {r['alpha_ann']*100:+.1f}%/yr  t={r['alpha_t']:.2f}  [{tag}]")
        if r['alpha_t'] > 2 and (winner is None or r['alpha_t'] > reg2[winner]['alpha_t']):
            winner = label
    if winner:
        print(f"\n  ANSWER: YES — {winner} beats the pure-momentum benchmark with significant alpha after cost.")
    else:
        print(f"\n  ANSWER: NO — no construction beats the pure-momentum benchmark with t>2 alpha after cost.")
        print(f"          The honest call is 'just buy momentum' (MTUM-style top-decile rule).")

    # also report the after-tax horse race directly (does anything beat B after-tax?)
    print("\n  AFTER-TAX annualized return horse race (the number a taxable trader keeps):")
    rank = sorted(results.items(), key=lambda kv: -kv[1]['ann_tax'])
    for label, mt in rank:
        print(f"    {label:<26} {mt['ann_tax']*100:+.1f}%/yr  (Sharpe {mt['sharpe_n']:.2f}, DD {mt['mdd_n']*100:.0f}%)")
    print(f"    {'SPY':<26} {(ann_(sm)*(1-TAX_RATE) if ann_(sm)>0 else ann_(sm))*100:+.1f}%/yr  (Sharpe {shp_(sm):.2f}, DD {mdd_(sm)*100:.0f}%)")

    # dump structured json for the report
    out = {
        'version_id': av.id,
        'years': float(results['A: v69 75+ (15d hold)']['yrs']) if 'A: v69 75+ (15d hold)' in results else None,
        'spy': {'ann': ann_(sm), 'sharpe': shp_(sm), 'mdd': mdd_(sm)},
        'constructions': {},
    }
    for label, mt in results.items():
        r1 = reg1.get(label, {})
        r2 = reg2.get(label, {})
        out['constructions'][label] = {
            'ann_gross': mt['ann_g'], 'ann_net': mt['ann_n'], 'ann_tax': mt['ann_tax'],
            'sharpe': mt['sharpe_n'], 'mdd': mt['mdd_n'], 'avg_n': mt['avg_n'], 'cost_yr': mt['cost_yr'],
            'reg_factor': {'alpha_ann': r1.get('alpha_ann'), 'alpha_t': r1.get('alpha_t'),
                            'mom_beta': r1.get('betas', {}).get('mom'), 'mkt_beta': r1.get('betas', {}).get('mkt'),
                            'r2': r1.get('r2')} if r1 else None,
            'reg_vs_benchmark': {'alpha_ann': r2.get('alpha_ann'), 'alpha_t': r2.get('alpha_t'),
                                  'benchB_beta': r2.get('betas', {}).get('benchB')} if r2 else None,
        }
    out['decisive_winner_vs_benchmark'] = winner
    with open(os.path.join(os.path.dirname(__file__), 'results.json'), 'w') as f:
        json.dump(out, f, indent=2, default=float)
    print("\n  wrote results.json", flush=True)


if __name__ == '__main__':
    main()
