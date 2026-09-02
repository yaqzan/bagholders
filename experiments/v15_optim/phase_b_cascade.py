"""Phase B (15 DTE) — Cascade allocation Bayesian sweep on v35 algorithm.

Mirror of experiments/v32_optim/phase_b_cascade.py adapted for 15 DTE:
  - Uses monte_carlo_15dte (PREMIUM_MULT=1.29, HOLD_DAYS=7, MAX_POSITIONS=8)
  - STRATEGY_15DTE locks (F3F floors=0.40, DD breaker=0.60, DD soft band disabled)
  - HARD_SELL_LOSS=-0.45 (theta-scaled for half-DTE)

Param space: same as 30 DTE (cascade calls + puts) — Bayesian explores around
current shipped non-monotonic 18/12/15/15 calls + 10/12/12 puts to find a
monotonic alternative.

Note: 15 DTE current cascade is NON-MONOTONIC on both sides — never validated.
Strong prior that monotonic shapes win, but bound the search to leave room
for unexpected wins.

Usage:
  PYTHONIOENCODING=utf-8 BUDGET=16 N_ITER=100 python -u \\
    experiments/v15_optim/phase_b_cascade.py 2>&1 \\
    | tee experiments/v15_optim/phase_b_cascade.log
"""
from __future__ import annotations
import json, math, os, sys, time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo_15dte as mc15  # noqa: E402
from database.models.core import AlgorithmVersion  # noqa: E402
import strategy_config as _sc  # noqa: E402

SWEEP_WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('dip',    date(2025, 11, 1), date(2026, 4, 15)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
    ('5y',     date(2021, 4, 15), date(2026, 4, 15)),
]
WINDOW_WEIGHTS = {'2021':1.0,'2022':1.0,'2023':1.0,'2024':1.0,'2025':1.0,
                  'dip':1.0,'22-now':1.5,'5y':1.5}
DD_SOFT_TARGET = 0.70

def apply_cfg(params: dict):
    """Set env vars (so mc15 MP workers inherit) + patch parent module."""
    os.environ['TIER_ULTRA_OV']    = str(params['ultra'])
    os.environ['TIER_TOP_OV']      = str(params['top'])
    os.environ['TIER_MID_OV']      = str(params['mid'])
    os.environ['TIER_LOW_OV']      = str(params['low'])
    os.environ['TIER_OVERFLOW_OV'] = '0.0'
    os.environ['PUT_TIER_TOP_OV']  = str(params['put_top'])
    os.environ['PUT_TIER_MID_OV']  = str(params['put_mid'])
    os.environ['PUT_TIER_LOW_OV']  = str(params['put_low'])

    mc15.TIER_ALLOC = {
        'ultra':    params['ultra'], 'top': params['top'],
        'mid':      params['mid'],   'low': params['low'],
        'overflow': 0.0,
    }
    mc15.PUT_TIER_ALLOC = {
        'put_top': params['put_top'],
        'put_mid': params['put_mid'],
        'put_low': params['put_low'],
    }

    os.environ['N_ITER_OVERRIDE'] = str(params.get('N_ITER', 200))
    mc15.N_ITER = params.get('N_ITER', 200)
    mc15.COLLISION_MODES = ['seeded']

def run_config(params, version):
    apply_cfg(params)
    out = {}
    for label, d_start, d_end in SWEEP_WINDOWS:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wr = mc15.run_window(label, d_start, d_end, version)
        r = wr.get('seeded', {})
        out[label] = {
            'mean_ret':float(r.get('mean_ret',0.0)), 'med_ret':float(r.get('med_ret',0.0)),
            'worst_dd':float(r.get('worst_dd',0.0)), 'mean_dd':float(r.get('mean_dd',0.0)),
            'p_collapse':float(r.get('p_coll',0.0)),
            'call_tp':float(r.get('call_tp',0.0)), 'put_tp':float(r.get('put_tp',0.0)),
            'call_trades':float(r.get('call_trades',0.0)), 'put_trades':float(r.get('put_trades',0.0)),
        }
    return out

def utility(window_results):
    log_sum = 0.0; max_dd = 0.0; max_coll = 0.0; per = {}
    for label, r in window_results.items():
        w = WINDOW_WEIGHTS.get(label, 1.0)
        ret_pct = max(-99.0, r['mean_ret'])
        lr = math.log1p(ret_pct / 100.0)
        log_sum += w * lr; per[label] = lr
        max_dd = max(max_dd, r['worst_dd']/100.0)
        max_coll = max(max_coll, r['p_collapse']/100.0)
    dd_excess = max(0.0, max_dd - DD_SOFT_TARGET)
    dd_pen = 200.0 * (dd_excess**2) / 0.01
    col_pen = 1000.0 * max_coll
    return dict(utility=log_sum-dd_pen-col_pen, log_sum=log_sum,
                dd_penalty=dd_pen, collapse_penalty=col_pen,
                max_dd=max_dd, max_collapse=max_coll, per_window_log=per)

PARAM_GRID = {
    'ultra':   [0.18, 0.20, 0.22, 0.25],
    'top':     [0.12, 0.15, 0.17],
    'mid':     [0.10, 0.12, 0.14],
    'low':     [0.08, 0.10, 0.12],
    'put_top': [0.08, 0.10, 0.12, 0.13],
    'put_mid': [0.08, 0.10, 0.12],
    'put_low': [0.08, 0.10, 0.12],
}

def is_valid(p):
    if not (p['ultra'] >= p['top'] >= p['mid'] >= p['low']):
        return False
    if p['ultra'] + p['top'] + p['mid'] + p['low'] > 0.62:
        return False
    return True

def enumerate_valid():
    out = []
    for u in PARAM_GRID['ultra']:
      for t in PARAM_GRID['top']:
        for m in PARAM_GRID['mid']:
          for l in PARAM_GRID['low']:
            for pt in PARAM_GRID['put_top']:
              for pm in PARAM_GRID['put_mid']:
                for pl in PARAM_GRID['put_low']:
                  p = {'ultra':u,'top':t,'mid':m,'low':l,
                       'put_top':pt,'put_mid':pm,'put_low':pl}
                  if is_valid(p): out.append(p)
    return out

def normalize(p):
    out = {}
    for k, vals in PARAM_GRID.items():
        lo, hi = min(vals), max(vals)
        out[k] = (p[k]-lo) / (hi-lo) if hi>lo else 0.5
    return out

def l1(a, b): return sum(abs(a[k]-b[k]) for k in a)

def surrogate(cn, ev, bw=0.30):
    if not ev: return 0.0, 1.0
    ws, us = [], []
    for e in ev:
        d = l1(cn, e['norm']); w = math.exp(-d/bw)
        ws.append(w); us.append(e['utility'])
    s = sum(ws)
    if s <= 0: return 0.0, 1.0
    mu = sum(w*u for w,u in zip(ws,us))/s
    var = sum(w*(u-mu)**2 for w,u in zip(ws,us))/s
    return mu, math.sqrt(var + 1.0/(1.0+s))

def main():
    log_dir = os.path.join(ROOT, 'experiments', 'v15_optim')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'phase_b_cascade_results.jsonl')

    BUDGET = int(os.environ.get('BUDGET', '16'))
    BATCH  = int(os.environ.get('BATCH', '3'))
    N_ITER = int(os.environ.get('N_ITER', '100'))
    print(f"Phase B (15 DTE) cascade — budget={BUDGET}, batch={BATCH}, N_ITER={N_ITER}, "
          f"DD soft target={DD_SOFT_TARGET*100:.0f}%, windows={len(SWEEP_WINDOWS)}")

    cands = enumerate_valid()
    print(f"Param space: {len(cands)} valid candidates after monotonicity filter")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"Active version: {version.git_commit if version else 'NONE'}")

    seeds = [
        # current shipped 15 DTE (non-monotonic)
        {'ultra':0.18,'top':0.12,'mid':0.15,'low':0.15, 'put_top':0.10,'put_mid':0.12,'put_low':0.12, 'N_ITER':N_ITER},
        # apply 30 DTE v32_optim winner shape — monotonic + monotonic puts
        {'ultra':0.20,'top':0.15,'mid':0.10,'low':0.10, 'put_top':0.12,'put_mid':0.10,'put_low':0.08, 'N_ITER':N_ITER},
        # monotonic with 15 DTE current low scaling
        {'ultra':0.18,'top':0.15,'mid':0.12,'low':0.10, 'put_top':0.12,'put_mid':0.10,'put_low':0.08, 'N_ITER':N_ITER},
        # steeper ultra (15 DTE has tighter slot pool, may favor concentration)
        {'ultra':0.25,'top':0.15,'mid':0.10,'low':0.08, 'put_top':0.10,'put_mid':0.10,'put_low':0.10, 'N_ITER':N_ITER},
        # flatter to maximize trade volume
        {'ultra':0.18,'top':0.15,'mid':0.12,'low':0.12, 'put_top':0.10,'put_mid':0.12,'put_low':0.12, 'N_ITER':N_ITER},
    ]

    evaluated = []
    def log(rec):
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')

    for s in seeds:
        t0 = time.time()
        win_res = run_config(s, version)
        u = utility(win_res)
        rec = {'params':{k:v for k,v in s.items() if k != 'N_ITER'}, 'norm':normalize(s),
               'utility':u['utility'], 'util_info':u, 'windows':win_res,
               'duration_s':time.time()-t0}
        evaluated.append(rec)
        log({'event':'eval','phase':'seed', **rec})
        print(f"[seed] util={u['utility']:+.3f} 5y={win_res['5y']['mean_ret']:+.0e}% "
              f"22n={win_res['22-now']['mean_ret']:+.0e}% maxDD={u['max_dd']*100:.1f}% "
              f"dur={rec['duration_s']:.0f}s  cfg={rec['params']}")

    iters = 0
    while len(evaluated) < BUDGET:
        iters += 1
        progress = min(1.0, len(evaluated)/BUDGET)
        kappa = 2.0 + (1.0-2.0)*progress
        scored = []
        seen = set(tuple(sorted(e['params'].items())) for e in evaluated)
        for c in cands:
            if tuple(sorted(c.items())) in seen: continue
            n = normalize(c)
            mu, std = surrogate(n, evaluated)
            scored.append((mu+kappa*std, mu, std, c, n))
        scored.sort(key=lambda x: x[0], reverse=True)
        batch = []
        for sc in scored:
            if len(batch) >= BATCH: break
            if any(l1(sc[4], b[4]) < 0.10 for b in batch): continue
            batch.append(sc)
        if not batch and scored: batch = scored[:BATCH]
        if not batch:
            print("[no candidates left] stopping"); break

        print(f"\n[iter {iters}  kappa={kappa:.2f}]  picking {len(batch)}:")
        for ucb_v, mu, std, c, _ in batch:
            print(f"  ucb={ucb_v:+.3f} mu={mu:+.3f} std={std:.3f}  {c}")

        for ucb_v, mu, std, c, n in batch:
            t0 = time.time()
            params = {**c, 'N_ITER': N_ITER}
            win_res = run_config(params, version)
            u = utility(win_res)
            rec = {'params':c, 'norm':n, 'utility':u['utility'], 'util_info':u,
                   'windows':win_res, 'duration_s':time.time()-t0,
                   'pred_mu':mu, 'pred_std':std, 'iter':iters}
            evaluated.append(rec)
            log({'event':'eval','phase':'bayes', **rec})
            print(f"  [eval] util={u['utility']:+.3f}  "
                  f"5y={win_res['5y']['mean_ret']:+.0e}%  "
                  f"22n={win_res['22-now']['mean_ret']:+.0e}%  "
                  f"maxDD={u['max_dd']*100:.1f}%  dur={rec['duration_s']:.0f}s  cfg={c}")

    print("\n" + "="*120)
    print(f"PHASE B (15 DTE) SUMMARY — {len(evaluated)} configs evaluated")
    print("="*120)
    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    print(f"{'rk':>2}  {'utility':>8}  {'logsum':>7}  {'maxDD':>6}  {'col%':>5}  params")
    for i, r in enumerate(ranked[:10]):
        u = r['util_info']
        print(f"{i+1:>2}  {r['utility']:>+8.3f}  {u['log_sum']:>+7.2f}  "
              f"{u['max_dd']*100:>6.1f}  {u['max_collapse']*100:>5.2f}  {r['params']}")
    best = ranked[0]
    print(f"\nBest: {best['params']}")
    print(f"\n{'window':<10}  {'MeanRet%':>14}  {'WorstDD':>8}  {'Col%':>5}  {'CallTP':>6}  {'PutTP':>6}  {'CallTr':>7}  {'PutTr':>6}")
    for label,_,_ in SWEEP_WINDOWS:
        w = best['windows'].get(label, {})
        print(f"{label:<10}  {w.get('mean_ret',0):>+14.1f}  {w.get('worst_dd',0):>8.1f}  "
              f"{w.get('p_collapse',0):>5.2f}  {w.get('call_tp',0):>6.1f}  {w.get('put_tp',0):>6.1f}  "
              f"{w.get('call_trades',0):>7.1f}  {w.get('put_trades',0):>6.1f}")

if __name__ == '__main__':
    main()
