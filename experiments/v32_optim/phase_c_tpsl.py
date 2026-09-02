"""Phase C — TP/SL by breadth Bayesian sweep on v34 algorithm.

Sweeps the breadth-step TP/SL mechanism for calls only. PUT_TP/PUT_SL locked
(known dead-end: widening PUT_SL beyond -20% breaches DD floor on every test).

Param axes:
  TP_BASE     ∈ {0.30, 0.33, 0.35, 0.37, 0.40}
  TP_STRESS   ∈ {0.35, 0.40, 0.42, 0.45}            (must be > TP_BASE)
  SL_BASE     ∈ {-0.27, -0.30, -0.33}               (tighter than -0.25 = bar-1 noise per docs)
  SL_STRESS   ∈ {-0.30, -0.33, -0.35, -0.40}        (must be more negative than SL_BASE)
  BREADTH_THR ∈ {40, 45, 50, 55, 60}

Constraint logic:
  - Net SL must respect call BE: BE = |SL|/(TP+|SL|). Reject if BE > 50% on either band.
  - Stress must be wider than base on BOTH TP and SL.
  - Cascade and put-side params locked at strategy_config defaults (Phase B
    output will inform a future joint sweep).

Locks (per known-issues.md):
  - Cascade: 0.20/0.15/0.12/0.10 (Phase 7 monotonic)
  - Put cascade: 0.10/0.12/0.12 (current — Phase B is sweeping this)
  - PUT_TP/PUT_SL: 0.35/-0.20 (locked)
  - DD breaker, dead-hold, F3F, regime slopes: shipped values
  - EARN_SUPP_PUT, CT_PROMOTE: ON

Utility identical to Phase B (log compound − DD penalty above 70% − collapse penalty).

Usage:
  PYTHONIOENCODING=utf-8 BUDGET=18 N_ITER=100 python -u \\
    experiments/v32_optim/phase_c_tpsl.py 2>&1 | tee experiments/v32_optim/phase_c_tpsl.log
"""
from __future__ import annotations
import json, math, os, sys, time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc  # noqa: E402
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

PARAM_GRID = {
    'tp_base':   [0.30, 0.33, 0.35, 0.37, 0.40],
    'tp_stress': [0.35, 0.40, 0.42, 0.45],
    'sl_base':   [-0.27, -0.30, -0.33],
    'sl_stress': [-0.30, -0.33, -0.35, -0.40],
    'breadth':   [40, 45, 50, 55, 60],
}

def is_valid(p):
    if not (p['tp_stress'] > p['tp_base']):  # stress must widen TP
        return False
    if not (p['sl_stress'] < p['sl_base']):  # stress must widen SL (more negative)
        return False
    # Break-even sanity (zero slippage)
    be_base   = abs(p['sl_base'])   / (p['tp_base']   + abs(p['sl_base']))
    be_stress = abs(p['sl_stress']) / (p['tp_stress'] + abs(p['sl_stress']))
    if be_base > 0.50 or be_stress > 0.50:
        return False
    return True

def enumerate_valid():
    out = []
    for tb in PARAM_GRID['tp_base']:
      for ts in PARAM_GRID['tp_stress']:
        for sb in PARAM_GRID['sl_base']:
          for ss in PARAM_GRID['sl_stress']:
            for br in PARAM_GRID['breadth']:
              p = {'tp_base':tb,'tp_stress':ts,'sl_base':sb,'sl_stress':ss,'breadth':br}
              if is_valid(p): out.append(p)
    return out

def apply_cfg(params, n_iter):
    """Set env vars (so MP workers inherit) + patch parent module globals."""
    cfg30 = _sc.STRATEGY_30DTE
    opt = cfg30.option

    # Sweep variables — env names match monte_carlo.py overrides (CRITICAL for MP)
    os.environ['TP_BASE_OV']   = str(params['tp_base'])
    os.environ['TP_STRESS_OV'] = str(params['tp_stress'])
    os.environ['SL_BASE_OV']   = str(params['sl_base'])
    os.environ['SL_STRESS_OV'] = str(params['sl_stress'])
    os.environ['BREADTH_THRESHOLD_OV'] = str(params['breadth'])
    os.environ['N_ITER_OVERRIDE'] = str(n_iter)

    # Patch parent module too (for any read in orchestrator code)
    mc.TP_BASE   = params['tp_base']
    mc.TP_STRESS = params['tp_stress']
    mc.SL_BASE   = params['sl_base']
    mc.SL_STRESS = params['sl_stress']
    mc.BREADTH_THRESHOLD = params['breadth']
    mc.NET_TP_BASE   = params['tp_base']
    mc.NET_TP_STRESS = params['tp_stress']
    mc.NET_SL_BASE   = params['sl_base']
    mc.NET_SL_STRESS = params['sl_stress']
    mc.TP_SIGMA_BASE   = params['tp_base']        * cfg30.PREMIUM_MULT / opt.DELTA
    mc.TP_SIGMA_STRESS = params['tp_stress']      * cfg30.PREMIUM_MULT / opt.DELTA
    mc.SL_SIGMA_BASE   = abs(params['sl_base'])   * cfg30.PREMIUM_MULT / opt.DELTA
    mc.SL_SIGMA_STRESS = abs(params['sl_stress']) * cfg30.PREMIUM_MULT / opt.DELTA
    mc.N_ITER = n_iter
    mc.COLLISION_MODES = ['seeded']
    # Cascade and everything else stay at strategy_config defaults — Phase B
    # will sweep cascade independently. Joint validation in Phase D.

def run_config(params, version, n_iter):
    apply_cfg(params, n_iter)
    out = {}
    for label, d_start, d_end in SWEEP_WINDOWS:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wr = mc.run_window(label, d_start, d_end, version)
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
    log_dir = os.path.join(ROOT, 'experiments', 'v32_optim')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'phase_c_tpsl_results.jsonl')

    BUDGET = int(os.environ.get('BUDGET', '18'))
    BATCH  = int(os.environ.get('BATCH', '3'))
    N_ITER = int(os.environ.get('N_ITER', '100'))
    print(f"Phase C TP/SL — budget={BUDGET}, batch={BATCH}, N_ITER={N_ITER}, "
          f"DD soft target={DD_SOFT_TARGET*100:.0f}%, windows={len(SWEEP_WINDOWS)}")

    cands = enumerate_valid()
    print(f"Param space: {len(cands)} valid candidates after BE/monotonicity filter")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"Active version: {version.git_commit if version else 'NONE'}")

    seeds = [
        # current shipped
        {'tp_base':0.35,'tp_stress':0.40,'sl_base':-0.30,'sl_stress':-0.35,'breadth':50},
        # tighter SL (smaller bands)
        {'tp_base':0.35,'tp_stress':0.40,'sl_base':-0.27,'sl_stress':-0.33,'breadth':50},
        # wider TP with same SL
        {'tp_base':0.37,'tp_stress':0.42,'sl_base':-0.30,'sl_stress':-0.35,'breadth':50},
        # narrower TP (faster recycling)
        {'tp_base':0.33,'tp_stress':0.40,'sl_base':-0.30,'sl_stress':-0.35,'breadth':50},
        # higher breadth threshold (more aggressive stress)
        {'tp_base':0.35,'tp_stress':0.40,'sl_base':-0.30,'sl_stress':-0.35,'breadth':55},
    ]

    evaluated = []
    def log(rec):
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')

    # Seed phase
    for s in seeds:
        t0 = time.time()
        win_res = run_config(s, version, N_ITER)
        u = utility(win_res)
        rec = {'params':s, 'norm':normalize(s),
               'utility':u['utility'], 'util_info':u, 'windows':win_res,
               'duration_s':time.time()-t0}
        evaluated.append(rec)
        log({'event':'eval','phase':'seed', **rec})
        print(f"[seed] util={u['utility']:+.3f} 5y={win_res['5y']['mean_ret']:+.2e}% "
              f"22n={win_res['22-now']['mean_ret']:+.2e}% maxDD={u['max_dd']*100:.1f}% "
              f"dur={rec['duration_s']:.0f}s  {s}")

    # Bayesian loop
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
            win_res = run_config(c, version, N_ITER)
            u = utility(win_res)
            rec = {'params':c, 'norm':n, 'utility':u['utility'], 'util_info':u,
                   'windows':win_res, 'duration_s':time.time()-t0,
                   'pred_mu':mu, 'pred_std':std, 'iter':iters}
            evaluated.append(rec)
            log({'event':'eval','phase':'bayes', **rec})
            print(f"  [eval] util={u['utility']:+.3f}  "
                  f"5y={win_res['5y']['mean_ret']:+.2e}%  "
                  f"22n={win_res['22-now']['mean_ret']:+.2e}%  "
                  f"maxDD={u['max_dd']*100:.1f}%  dur={rec['duration_s']:.0f}s  {c}")

    # Summary
    print("\n" + "="*120)
    print(f"PHASE C SUMMARY — {len(evaluated)} configs evaluated")
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
