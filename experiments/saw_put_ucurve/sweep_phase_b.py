"""SAW Put U-curve Phase B — Bayesian MC sweep over alloc% gradient shape.

Mechanism: at each put signal date, compute U-curve scale based on cross-sector
ETF breadth (% of 11 SPDRs above EMA50) and multiply into put alloc.

Param space (6 dims):
  - shape:      {quadratic, sigmoid}             discrete (2 values)
  - midpoint:   {72, 75, 77, 80}                 4 values
  - half_width: {18, 22, 25, 28}                 4 values
  - floor:      {0.35, 0.45, 0.55, 0.65}         4 values
  - ceil:       {1.15, 1.25, 1.35, 1.50}         4 values
  - power_k:    {1.5, 2.0, 2.5, 3.0}             4 values (interpreted as power
                  for quadratic shape; mapped to sigmoid K via {3, 5, 7, 9})

Total grid: 2 × 4^5 = 2048 valid configs. Bayesian samples 80.

Utility (mirror v32_optim/phase_b_cascade.py shape):
  log(1 + window_compound/100) weighted, soft DD penalty above 0.70, collapse
  penalty 1000× collapse rate.

Output:
  experiments/saw_put_ucurve/phase_b_results.jsonl
  experiments/saw_put_ucurve/phase_b.log

Usage:
  PYTHONIOENCODING=utf-8 python -u experiments/saw_put_ucurve/sweep_phase_b.py 2>&1 \\
    | tee experiments/saw_put_ucurve/phase_b.log
"""
from __future__ import annotations
import json, math, os, sys, time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc  # noqa: E402
from database.models.core import AlgorithmVersion  # noqa: E402

# ---------- Windows (4 — screening subset; full 8 in Stage C) ----------
SWEEP_WINDOWS = [
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
    ('5y',     date(2021, 4, 15), date(2026, 4, 15)),
]
WINDOW_WEIGHTS = {'2022': 1.0, '2023': 1.0, '22-now': 1.5, '5y': 1.5}
DD_SOFT_TARGET = 0.70

# ---------- Param grid ----------
PARAM_GRID = {
    'shape':      ['quadratic', 'sigmoid'],
    'midpoint':   [72, 75, 77, 80],
    'half_width': [18, 22, 25, 28],
    'floor':      [0.35, 0.45, 0.55, 0.65],
    'ceil':       [1.15, 1.25, 1.35, 1.50],
    'power_k':    [1.5, 2.0, 2.5, 3.0],
}
# power_k → sigmoid_K mapping (gentler→sharper)
SIG_K_MAP = {1.5: 3.0, 2.0: 5.0, 2.5: 7.0, 3.0: 9.0}


def is_valid(p):
    return p['ceil'] > p['floor']


def enumerate_valid():
    out = []
    for sh in PARAM_GRID['shape']:
        for mid in PARAM_GRID['midpoint']:
            for hw in PARAM_GRID['half_width']:
                for fl in PARAM_GRID['floor']:
                    for ce in PARAM_GRID['ceil']:
                        for pk in PARAM_GRID['power_k']:
                            p = {'shape': sh, 'midpoint': mid, 'half_width': hw,
                                 'floor': fl, 'ceil': ce, 'power_k': pk}
                            if is_valid(p):
                                out.append(p)
    return out


def apply_cfg(params, n_iter=200):
    """Set env vars for next MC run; workers re-read at import time."""
    os.environ['SAW_PUT_UCURVE_ENABLED']  = '1'
    os.environ['SAW_PUT_UCURVE_SHAPE']    = params['shape']
    os.environ['SAW_PUT_UCURVE_MIDPOINT'] = str(params['midpoint'])
    os.environ['SAW_PUT_UCURVE_HALFWIDTH']= str(params['half_width'])
    os.environ['SAW_PUT_UCURVE_FLOOR']    = str(params['floor'])
    os.environ['SAW_PUT_UCURVE_CEIL']     = str(params['ceil'])
    if params['shape'] == 'quadratic':
        os.environ['SAW_PUT_UCURVE_POWER'] = str(params['power_k'])
        os.environ['SAW_PUT_UCURVE_K']     = '5.0'  # unused but set
    else:  # sigmoid
        os.environ['SAW_PUT_UCURVE_K']     = str(SIG_K_MAP[params['power_k']])
        os.environ['SAW_PUT_UCURVE_POWER'] = '2.0'  # unused

    # Patch parent process for any in-process reads
    mc.SAW_PUT_UCURVE_ENABLED   = 1
    mc.SAW_PUT_UCURVE_SHAPE     = params['shape']
    mc.SAW_PUT_UCURVE_MIDPOINT  = float(params['midpoint'])
    mc.SAW_PUT_UCURVE_HALFWIDTH = float(params['half_width'])
    mc.SAW_PUT_UCURVE_FLOOR     = float(params['floor'])
    mc.SAW_PUT_UCURVE_CEIL      = float(params['ceil'])
    if params['shape'] == 'quadratic':
        mc.SAW_PUT_UCURVE_POWER = float(params['power_k'])
    else:
        mc.SAW_PUT_UCURVE_K     = float(SIG_K_MAP[params['power_k']])

    os.environ['N_ITER_OVERRIDE'] = str(n_iter)
    mc.N_ITER = n_iter
    mc.COLLISION_MODES = ['seeded']


def disable_saw():
    """Disable U-curve mechanism for baseline runs."""
    os.environ['SAW_PUT_UCURVE_ENABLED'] = '0'
    mc.SAW_PUT_UCURVE_ENABLED = 0


def run_config(params, version, n_iter=200, baseline=False):
    if baseline:
        disable_saw()
    else:
        apply_cfg(params, n_iter=n_iter)
    out = {}
    for label, d_start, d_end in SWEEP_WINDOWS:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wr = mc.run_window(label, d_start, d_end, version)
        r = wr.get('seeded', {})
        out[label] = {
            'mean_ret':    float(r.get('mean_ret', 0.0)),
            'med_ret':     float(r.get('med_ret', 0.0)),
            'worst_dd':    float(r.get('worst_dd', 0.0)),
            'mean_dd':     float(r.get('mean_dd', 0.0)),
            'p_collapse':  float(r.get('p_coll', 0.0)),
            'call_tp':     float(r.get('call_tp', 0.0)),
            'put_tp':      float(r.get('put_tp', 0.0)),
            'call_trades': float(r.get('call_trades', 0.0)),
            'put_trades':  float(r.get('put_trades', 0.0)),
        }
    return out


def utility(window_results):
    log_sum = 0.0
    max_dd = 0.0
    max_coll = 0.0
    per_window_log = {}
    for label, r in window_results.items():
        w = WINDOW_WEIGHTS.get(label, 1.0)
        ret_pct = max(-99.0, r['mean_ret'])
        lr = math.log1p(ret_pct / 100.0)
        log_sum += w * lr
        per_window_log[label] = lr
        max_dd   = max(max_dd, r['worst_dd'] / 100.0)
        max_coll = max(max_coll, r['p_collapse'] / 100.0)
    dd_excess = max(0.0, max_dd - DD_SOFT_TARGET)
    dd_penalty = 200.0 * (dd_excess ** 2) / 0.01
    collapse_penalty = 1000.0 * max_coll
    util = log_sum - dd_penalty - collapse_penalty
    return dict(utility=util, log_sum=log_sum, dd_penalty=dd_penalty,
                collapse_penalty=collapse_penalty, max_dd=max_dd,
                max_collapse=max_coll, per_window_log=per_window_log)


# ---------- Bayesian acquisition (kernel KNN UCB) ----------
def normalize(p):
    out = {}
    for k, vals in PARAM_GRID.items():
        if k == 'shape':
            out[k] = 0.0 if p[k] == 'quadratic' else 1.0
        else:
            lo, hi = min(vals), max(vals)
            out[k] = (p[k] - lo) / (hi - lo) if hi > lo else 0.5
    return out


def l1(a, b):
    return sum(abs(a[k] - b[k]) for k in a)


def surrogate(cand_norm, evaluated, bw=0.30):
    if not evaluated:
        return 0.0, 1.0
    ws, us = [], []
    for e in evaluated:
        d = l1(cand_norm, e['norm'])
        w = math.exp(-d / bw)
        ws.append(w); us.append(e['utility'])
    ws_sum = sum(ws)
    if ws_sum <= 0:
        return 0.0, 1.0
    mu = sum(w*u for w,u in zip(ws,us)) / ws_sum
    var = sum(w*(u-mu)**2 for w,u in zip(ws,us)) / ws_sum
    std = math.sqrt(var + 1.0/(1.0 + ws_sum))
    return mu, std


def ucb(cand_norm, evaluated, kappa=2.0):
    mu, std = surrogate(cand_norm, evaluated)
    return mu + kappa * std, mu, std


def main():
    log_dir = os.path.join(ROOT, 'experiments', 'saw_put_ucurve')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'phase_b_results.jsonl')

    BUDGET = int(os.environ.get('BUDGET', '80'))
    N_ITER = int(os.environ.get('N_ITER', '200'))
    print(f"SAW Put U-curve Phase B — budget={BUDGET}, N_ITER={N_ITER}, "
          f"DD soft target={DD_SOFT_TARGET*100:.0f}%, windows={len(SWEEP_WINDOWS)}")

    cands = enumerate_valid()
    print(f"Param space: {len(cands)} valid configs (post floor<ceil filter)")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"Active version: {version.git_commit if version else 'NONE'}")

    # Seeds: include disabled baseline + 5 hand-picked U-curves (per Phase A screen)
    seeds = [
        # baseline (SAW disabled — gives us anchor for delta)
        {'__baseline': True, 'shape': 'quadratic', 'midpoint': 77, 'half_width': 22,
         'floor': 1.0, 'ceil': 1.0, 'power_k': 2.0},
        # Phase A winner (quadratic mid=75, default floor/ceil/power)
        {'shape': 'quadratic', 'midpoint': 75, 'half_width': 25, 'floor': 0.45,
         'ceil':  1.50, 'power_k': 2.0},
        # Same but tighter floor (more aggressive dampening)
        {'shape': 'quadratic', 'midpoint': 75, 'half_width': 25, 'floor': 0.35,
         'ceil':  1.50, 'power_k': 2.0},
        # Sigmoid winner candidate
        {'shape': 'sigmoid',   'midpoint': 77, 'half_width': 22, 'floor': 0.45,
         'ceil':  1.35, 'power_k': 2.0},
        # Sigmoid with sharper transitions
        {'shape': 'sigmoid',   'midpoint': 77, 'half_width': 22, 'floor': 0.35,
         'ceil':  1.50, 'power_k': 3.0},
        # Symmetric narrow
        {'shape': 'quadratic', 'midpoint': 77, 'half_width': 18, 'floor': 0.45,
         'ceil':  1.35, 'power_k': 2.5},
    ]

    evaluated = []

    def log(rec):
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')

    # Clear previous log
    if os.path.exists(log_path):
        os.remove(log_path)

    t_start = time.time()

    # --- Seed phase ---
    print(f"\n[seed phase] {len(seeds)} configs")
    for i, s in enumerate(seeds):
        if i > BUDGET:
            break
        is_baseline = s.pop('__baseline', False)
        ti = time.time()
        try:
            res = run_config(s, version, n_iter=N_ITER, baseline=is_baseline)
        except Exception as e:
            print(f"  seed {i+1}: EXCEPTION {e}")
            continue
        u = utility(res)
        rec = {'iter': i+1, 'phase': 'seed', 'baseline': is_baseline,
               'params': s, 'windows': res, 'utility_breakdown': u,
               'elapsed_s': time.time()-ti}
        if not is_baseline:
            evaluated.append({'norm': normalize(s), 'utility': u['utility'],
                              'params': s, 'res': res})
        log(rec)
        # Print per-iter summary
        param_str = 'BASELINE (off)' if is_baseline else (
            f"{s['shape'][:4]} mid={s['midpoint']} hw={s['half_width']} "
            f"f={s['floor']:.2f} c={s['ceil']:.2f} pk={s['power_k']}")
        five_y_ret = res.get('5y', {}).get('mean_ret', 0)
        twonow = res.get('22-now', {}).get('mean_ret', 0)
        print(f"  seed {i+1:>2d}/{len(seeds)}  util={u['utility']:>+8.2f}  "
              f"max_dd={u['max_dd']*100:>5.1f}%  "
              f"5y={five_y_ret:>+12.1e}%  22n={twonow:>+12.1e}%  "
              f"t={time.time()-ti:.0f}s  [{param_str}]")

    # --- Bayesian phase ---
    n_to_go = max(0, BUDGET - len(evaluated) - 1)  # -1 for baseline
    print(f"\n[Bayesian phase] {n_to_go} additional evals")
    iter_n = len(seeds)
    while len(evaluated) < BUDGET - 1:
        # Score all candidates by UCB
        cand_scored = []
        for c in cands:
            cn = normalize(c)
            # Skip duplicates
            if any(l1(cn, e['norm']) < 1e-9 for e in evaluated):
                continue
            score, mu, std = ucb(cn, evaluated, kappa=2.0)
            cand_scored.append((score, c))
        if not cand_scored:
            break
        cand_scored.sort(key=lambda x: -x[0])
        next_p = cand_scored[0][1]

        iter_n += 1
        ti = time.time()
        try:
            res = run_config(next_p, version, n_iter=N_ITER)
        except Exception as e:
            print(f"  iter {iter_n}: EXCEPTION {e}")
            evaluated.append({'norm': normalize(next_p), 'utility': -999,
                              'params': next_p, 'res': {}})
            continue
        u = utility(res)
        rec = {'iter': iter_n, 'phase': 'bayesian', 'baseline': False,
               'params': next_p, 'windows': res, 'utility_breakdown': u,
               'elapsed_s': time.time()-ti}
        evaluated.append({'norm': normalize(next_p), 'utility': u['utility'],
                          'params': next_p, 'res': res})
        log(rec)
        five_y_ret = res.get('5y', {}).get('mean_ret', 0)
        twonow = res.get('22-now', {}).get('mean_ret', 0)
        param_str = (f"{next_p['shape'][:4]} mid={next_p['midpoint']} "
                     f"hw={next_p['half_width']} f={next_p['floor']:.2f} "
                     f"c={next_p['ceil']:.2f} pk={next_p['power_k']}")
        print(f"  iter {iter_n:>2d}    util={u['utility']:>+8.2f}  "
              f"max_dd={u['max_dd']*100:>5.1f}%  "
              f"5y={five_y_ret:>+12.1e}%  22n={twonow:>+12.1e}%  "
              f"t={time.time()-ti:.0f}s  [{param_str}]")

    # --- Final summary ---
    total = time.time() - t_start
    print(f"\n[done] {len(evaluated)+1} total evals in {total:.0f}s ({total/60:.1f}min)")

    print("\n" + "=" * 100)
    print("Top 10 configs by utility:")
    print("=" * 100)
    sorted_eval = sorted(evaluated, key=lambda e: -e['utility'])
    print(f"{'rank':>4s}  {'util':>8s}  {'max_dd':>7s}  {'5y_ret':>14s}  "
          f"{'22n_ret':>14s}  {'shape':<10s}  {'mid':>3s}  {'hw':>3s}  "
          f"{'flr':>5s}  {'ceil':>5s}  {'pk':>4s}")
    for i, e in enumerate(sorted_eval[:10]):
        p = e['params']
        u_dd = next((v['worst_dd']/100 for k, v in e['res'].items() if v.get('worst_dd', 0) > 0), 0)
        five_y = e['res'].get('5y', {}).get('mean_ret', 0)
        twonow = e['res'].get('22-now', {}).get('mean_ret', 0)
        print(f"{i+1:>4d}  {e['utility']:>+8.2f}  {u_dd*100:>6.1f}%  "
              f"{five_y:>+14.1e}%  {twonow:>+14.1e}%  "
              f"{p['shape']:<10s}  {p['midpoint']:>3d}  {p['half_width']:>3d}  "
              f"{p['floor']:>5.2f}  {p['ceil']:>5.2f}  {p['power_k']:>4.1f}")


if __name__ == '__main__':
    main()
