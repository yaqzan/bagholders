"""SAW Put U-curve 15 DTE — Phase B Bayesian sweep (DD-primary utility).

Calibrates the SAW Put U-curve mechanism for the 15 DTE strategy. Mirrors
the 30 DTE Phase B harness (`experiments/saw_put_ucurve/sweep_phase_b.py`)
but with three structural differences:

  1. Targets `monte_carlo_15dte` instead of `monte_carlo`.
  2. WIDER param grid: floor expanded to [0.30, 0.75], ceil to [1.00, 1.40],
     midpoint to [65..80] — 30 DTE Region B (mid=72) was found NOT to
     transfer in 22-now smoke (5y DD 83.3% vs baseline 73.3%).
  3. DD-PRIMARY utility: the 30 DTE harness used compound-driven log_sum
     with soft DD penalty above 0.70. For 15 DTE per the new Stage 3
     framework (assessment-backtest.md "T1-T7"), DD is primary; compound is
     theoretical above ~1e10% and serves as a non-regression sanity check.
     Util: reward DD reduction vs baseline, lightly reward compound, HARD
     cliffs on collapse / >5pp annual DD regression / >1pp 5y DD regression.

Param space (6 dims, 4500 valid configs):
  - shape:      {quadratic, sigmoid}            2 vals
  - midpoint:   {65, 70, 72, 75, 77, 80}        6 vals
  - half_width: {15, 18, 22, 25, 28}            5 vals
  - floor:      {0.30, 0.40, 0.50, 0.55, 0.65, 0.75}  6 vals
  - ceil:       {1.00, 1.10, 1.20, 1.30, 1.40}       5 vals
  - power_k:    {1.5, 2.0, 2.5, 3.0, 4.0}             5 vals

Bayesian samples 50 (kernel-KNN UCB, kappa=2.0). Total runtime estimate:
~2-3 hours at N=200 x 4 windows.

Output:
  experiments/saw_put_ucurve_15dte/phase_b_results.jsonl
  experiments/saw_put_ucurve_15dte/phase_b.log

Usage:
  PYTHONIOENCODING=utf-8 python -u experiments/saw_put_ucurve_15dte/sweep_phase_b.py 2>&1 \\
    | tee experiments/saw_put_ucurve_15dte/phase_b.log
"""
from __future__ import annotations
import json, math, os, sys, time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo_15dte as mc  # noqa: E402  -- 15 DTE engine
from database.models.core import AlgorithmVersion  # noqa: E402

# ---------- Windows (4 — screening subset; full 8 in Phase C) ----------
SWEEP_WINDOWS = [
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
    ('5y',     date(2021, 4, 15), date(2026, 4, 15)),
]
# Window weights — 5y is decisive per "Lock decisions on 5y; 22-now is confirmation"
WINDOW_WEIGHTS = {'2022': 1.0, '2023': 1.0, '22-now': 1.5, '5y': 2.0}

# ---------- Param grid (WIDER than 30 DTE Phase B per handoff guidance) ----------
PARAM_GRID = {
    'shape':      ['quadratic', 'sigmoid'],
    'midpoint':   [65, 70, 72, 75, 77, 80],
    'half_width': [15, 18, 22, 25, 28],
    'floor':      [0.30, 0.40, 0.50, 0.55, 0.65, 0.75],
    'ceil':       [1.00, 1.10, 1.20, 1.30, 1.40],
    'power_k':    [1.5, 2.0, 2.5, 3.0, 4.0],
}
SIG_K_MAP = {1.5: 3.0, 2.0: 5.0, 2.5: 7.0, 3.0: 9.0, 4.0: 12.0}


def is_valid(p):
    return p['ceil'] >= p['floor']


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
    """Set env vars for next MC run; workers re-read at module import time."""
    os.environ['SAW_PUT_UCURVE_ENABLED']  = '1'
    os.environ['SAW_PUT_UCURVE_SHAPE']    = params['shape']
    os.environ['SAW_PUT_UCURVE_MIDPOINT'] = str(params['midpoint'])
    os.environ['SAW_PUT_UCURVE_HALFWIDTH']= str(params['half_width'])
    os.environ['SAW_PUT_UCURVE_FLOOR']    = str(params['floor'])
    os.environ['SAW_PUT_UCURVE_CEIL']     = str(params['ceil'])
    if params['shape'] == 'quadratic':
        os.environ['SAW_PUT_UCURVE_POWER'] = str(params['power_k'])
        os.environ['SAW_PUT_UCURVE_K']     = '5.0'  # unused but set for safety
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


# ============================================================================
# DD-PRIMARY UTILITY (Stage 3 framework, T1-T7)
# ============================================================================
# Hard cliffs (return -inf):
#   - any window collapse rate > 0
#   - 5y WorstDD > baseline + 1.0pp (T4)
#   - any annual window DD > baseline + 5.0pp (T5)
#   - 5y compound divergence > 3 OOMs from baseline (T7)
# Soft scoring (when above all cliffs):
#   util = dd_reward + compound_relative + per_window_dd_reward
#   dd_reward          = 200 * (baseline_5y_dd - cand_5y_dd)  [pp -> util]
#                                                              +1pp DD = +2.0 util
#   compound_relative  = sum(W_i * tanh((log(1+ret_i) - log(1+base_i)) / 5))
#                       capped tanh prevents huge compound wins from masking DD damage
#   per_window_dd_reward = 100 * sum_i (baseline_dd_i - cand_dd_i)
#                       rewards spreading DD reduction across windows
# ============================================================================
HARD_CLIFF_5Y_DD_INCREASE   = 0.010   # +1.0pp 5y DD
HARD_CLIFF_ANNUAL_DD_INCR   = 0.050   # +5.0pp any annual DD
HARD_CLIFF_OOM_DIVERGENCE   = 3.0     # 5y compound ±3 OOMs


def utility(window_results, baseline_results):
    if baseline_results is None:
        # Score baseline itself relative to itself = 0 (anchor)
        return dict(utility=0.0, dd_reward=0.0, compound_rel=0.0,
                    per_window_dd=0.0, hard_cliff=None,
                    max_dd=max(r['worst_dd']/100.0 for r in window_results.values()),
                    max_collapse=max(r['p_collapse']/100.0 for r in window_results.values()))

    cand_5y_dd  = window_results['5y']['worst_dd'] / 100.0
    base_5y_dd  = baseline_results['5y']['worst_dd'] / 100.0
    cand_5y_ret = window_results['5y']['mean_ret']
    base_5y_ret = baseline_results['5y']['mean_ret']

    max_collapse = max(r['p_collapse'] / 100.0 for r in window_results.values())

    # ---- HARD CLIFFS (return -inf if any breached) ----
    if max_collapse > 0:
        return dict(utility=-1e9, dd_reward=0, compound_rel=0, per_window_dd=0,
                    hard_cliff=f'collapse rate={max_collapse:.2%}',
                    max_dd=cand_5y_dd, max_collapse=max_collapse)
    if cand_5y_dd - base_5y_dd > HARD_CLIFF_5Y_DD_INCREASE:
        return dict(utility=-1e9, dd_reward=0, compound_rel=0, per_window_dd=0,
                    hard_cliff=f'5y DD +{(cand_5y_dd - base_5y_dd)*100:.1f}pp > +1pp',
                    max_dd=cand_5y_dd, max_collapse=max_collapse)
    for label in ['2022', '2023']:
        c_dd = window_results[label]['worst_dd'] / 100.0
        b_dd = baseline_results[label]['worst_dd'] / 100.0
        if c_dd - b_dd > HARD_CLIFF_ANNUAL_DD_INCR:
            return dict(utility=-1e9, dd_reward=0, compound_rel=0, per_window_dd=0,
                        hard_cliff=f'{label} DD +{(c_dd - b_dd)*100:.1f}pp > +5pp',
                        max_dd=cand_5y_dd, max_collapse=max_collapse)
    # OOM divergence — ONLY fire on compound REGRESSION (downside).
    # Upside (cand >> baseline) is not a strategy break — could be alpha or
    # MC-noise tail. The framework's intent is to catch strategy-breaking
    # changes; we evaluate compound only as a non-regression sanity check.
    if cand_5y_ret > -99.0 and base_5y_ret > -99.0:
        cand_log = math.log10(max(1.001, 1.0 + cand_5y_ret/100.0))
        base_log = math.log10(max(1.001, 1.0 + base_5y_ret/100.0))
        if (base_log - cand_log) > HARD_CLIFF_OOM_DIVERGENCE:    # cand << base
            return dict(utility=-1e9, dd_reward=0, compound_rel=0, per_window_dd=0,
                        hard_cliff=f'5y compound -{base_log-cand_log:.1f} OOMs (regression)',
                        max_dd=cand_5y_dd, max_collapse=max_collapse)

    # ---- SOFT SCORING (DD-DOMINATED — compound is sanity-only above ~1e10%) ----
    # 1. DD reward: 1000 util per pp 5y DD reduction (the PRIMARY signal).
    #    +1pp 5y DD reduction = +10 util. Saving 5pp 5y DD = +50 util.
    dd_reward = 1000.0 * (base_5y_dd - cand_5y_dd)

    # 2. Per-window DD reward: weighted by WINDOW_WEIGHTS (5y=2.0, 22n=1.5,
    #    2022/2023=1.0). 300 util per weighted-pp DD reduction across windows.
    per_window_dd = 0.0
    for label, w in WINDOW_WEIGHTS.items():
        c_dd = window_results[label]['worst_dd'] / 100.0
        b_dd = baseline_results[label]['worst_dd'] / 100.0
        per_window_dd += 300.0 * w * (b_dd - c_dd)

    # 3. Compound relative: capped tanh, weight 1 per window. Acts as a
    #    tiebreaker between DD-equivalent candidates; cannot dominate DD signal.
    #    Capped at ±1 per window (max ±5 total across the 4-window weighted sum).
    compound_rel = 0.0
    for label, w in WINDOW_WEIGHTS.items():
        c_ret = window_results[label]['mean_ret']
        b_ret = baseline_results[label]['mean_ret']
        c_log = math.log1p(max(-0.99, c_ret / 100.0))
        b_log = math.log1p(max(-0.99, b_ret / 100.0))
        rel_log = c_log - b_log     # positive = candidate beats baseline
        compound_rel += w * math.tanh(rel_log / 3.0) * 1.0   # capped at ±1*W

    util = dd_reward + per_window_dd + compound_rel
    return dict(utility=util, dd_reward=dd_reward, compound_rel=compound_rel,
                per_window_dd=per_window_dd, hard_cliff=None,
                max_dd=cand_5y_dd, max_collapse=max_collapse)


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
    log_dir = os.path.join(ROOT, 'experiments', 'saw_put_ucurve_15dte')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'phase_b_results.jsonl')

    BUDGET = int(os.environ.get('BUDGET', '50'))
    N_ITER = int(os.environ.get('N_ITER', '200'))
    print(f"SAW Put U-curve 15 DTE Phase B — budget={BUDGET}, N_ITER={N_ITER}, "
          f"DD-primary util, windows={len(SWEEP_WINDOWS)}")

    cands = enumerate_valid()
    print(f"Param space: {len(cands)} valid configs (post floor<=ceil filter)")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"Active version: {version.git_commit if version else 'NONE'}")

    # ---------- Run baseline FIRST (anchor for all relative scoring) ----------
    print(f"\n[baseline] Running SAW disabled at N={N_ITER}...")
    t_base = time.time()
    baseline_res = run_config(None, version, n_iter=N_ITER, baseline=True)
    base_5y_dd  = baseline_res['5y']['worst_dd']
    base_5y_ret = baseline_res['5y']['mean_ret']
    base_22n_dd = baseline_res['22-now']['worst_dd']
    print(f"  baseline 5y    : ret={base_5y_ret:+.2e}%  dd={base_5y_dd:.1f}%")
    print(f"  baseline 22-now: ret={baseline_res['22-now']['mean_ret']:+.2e}%  dd={base_22n_dd:.1f}%")
    print(f"  baseline 2022  : ret={baseline_res['2022']['mean_ret']:+.2e}%  dd={baseline_res['2022']['worst_dd']:.1f}%")
    print(f"  baseline 2023  : ret={baseline_res['2023']['mean_ret']:+.2e}%  dd={baseline_res['2023']['worst_dd']:.1f}%")
    print(f"  baseline elapsed: {time.time()-t_base:.0f}s")

    base_rec = {'iter': 0, 'phase': 'baseline', 'baseline': True,
                'params': None, 'windows': baseline_res,
                'utility_breakdown': utility(baseline_res, None),
                'elapsed_s': time.time() - t_base}

    def log(rec):
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')

    if os.path.exists(log_path):
        os.remove(log_path)
    log(base_rec)

    evaluated = []

    # ---------- Hand-picked seeds ----------
    seeds = [
        # 30 DTE Region B winner (verbatim) — likely fails 15 DTE smoke at 83% DD,
        # but useful to anchor the gradient
        {'shape': 'quadratic', 'midpoint': 72, 'half_width': 18,
         'floor': 0.55, 'ceil': 1.35, 'power_k': 3.0},
        # 30 DTE Region A (mid=77, tighter floor)
        {'shape': 'quadratic', 'midpoint': 77, 'half_width': 18,
         'floor': 0.45, 'ceil': 1.35, 'power_k': 3.0},
        # MILD contraction (less aggressive than 30 DTE) — preserves more put trades
        {'shape': 'quadratic', 'midpoint': 72, 'half_width': 22,
         'floor': 0.75, 'ceil': 1.20, 'power_k': 2.0},
        # PURE contraction (no amplification, just floor in bad zone)
        {'shape': 'quadratic', 'midpoint': 72, 'half_width': 18,
         'floor': 0.55, 'ceil': 1.00, 'power_k': 3.0},
        # WIDE bad zone (mid=72, hw=28) — contracts across more breadth
        {'shape': 'quadratic', 'midpoint': 72, 'half_width': 28,
         'floor': 0.65, 'ceil': 1.20, 'power_k': 3.0},
        # Sigmoid neutral
        {'shape': 'sigmoid',   'midpoint': 75, 'half_width': 22,
         'floor': 0.50, 'ceil': 1.30, 'power_k': 2.5},
        # CENTERED at 65 (lower midpoint — captures different bad zone)
        {'shape': 'quadratic', 'midpoint': 65, 'half_width': 18,
         'floor': 0.55, 'ceil': 1.30, 'power_k': 3.0},
        # CENTERED at 80 (higher midpoint — bad zone shifts)
        {'shape': 'quadratic', 'midpoint': 80, 'half_width': 15,
         'floor': 0.50, 'ceil': 1.30, 'power_k': 3.0},
    ]

    t_start = time.time()
    print(f"\n[seed phase] {len(seeds)} configs (priors anchored on 30 DTE history + variations)")
    for i, s in enumerate(seeds):
        if len(evaluated) >= BUDGET:
            break
        ti = time.time()
        try:
            res = run_config(s, version, n_iter=N_ITER, baseline=False)
        except Exception as e:
            print(f"  seed {i+1}: EXCEPTION {e}")
            continue
        u = utility(res, baseline_res)
        rec = {'iter': i+1, 'phase': 'seed', 'baseline': False,
               'params': s, 'windows': res, 'utility_breakdown': u,
               'elapsed_s': time.time()-ti}
        evaluated.append({'norm': normalize(s), 'utility': u['utility'],
                          'params': s, 'res': res})
        log(rec)
        param_str = (f"{s['shape'][:4]} mid={s['midpoint']} hw={s['half_width']} "
                     f"f={s['floor']:.2f} c={s['ceil']:.2f} pk={s['power_k']}")
        five_y_ret = res['5y']['mean_ret']
        five_y_dd  = res['5y']['worst_dd']
        twonow_dd  = res['22-now']['worst_dd']
        cliff_str  = f' CLIFF[{u["hard_cliff"]}]' if u['hard_cliff'] else ''
        print(f"  seed {i+1:>2d}/{len(seeds)}  util={u['utility']:>+8.2f}  "
              f"5y_dd={five_y_dd:>5.1f}%(b={base_5y_dd:.1f})  "
              f"22n_dd={twonow_dd:>5.1f}%(b={base_22n_dd:.1f})  "
              f"5y_ret={five_y_ret:>+10.1e}%  t={time.time()-ti:.0f}s  "
              f"[{param_str}]{cliff_str}")

    # ---------- Bayesian phase ----------
    n_to_go = max(0, BUDGET - len(evaluated))
    print(f"\n[Bayesian phase] {n_to_go} additional evals (UCB kappa=2.0, bw=0.30)")
    iter_n = len(seeds)
    while len(evaluated) < BUDGET:
        cand_scored = []
        for c in cands:
            cn = normalize(c)
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
            res = run_config(next_p, version, n_iter=N_ITER, baseline=False)
        except Exception as e:
            print(f"  iter {iter_n}: EXCEPTION {e}")
            evaluated.append({'norm': normalize(next_p), 'utility': -999,
                              'params': next_p, 'res': {}})
            continue
        u = utility(res, baseline_res)
        rec = {'iter': iter_n, 'phase': 'bayesian', 'baseline': False,
               'params': next_p, 'windows': res, 'utility_breakdown': u,
               'elapsed_s': time.time()-ti}
        evaluated.append({'norm': normalize(next_p), 'utility': u['utility'],
                          'params': next_p, 'res': res})
        log(rec)
        param_str = (f"{next_p['shape'][:4]} mid={next_p['midpoint']} "
                     f"hw={next_p['half_width']} f={next_p['floor']:.2f} "
                     f"c={next_p['ceil']:.2f} pk={next_p['power_k']}")
        five_y_dd  = res['5y']['worst_dd']
        twonow_dd  = res['22-now']['worst_dd']
        five_y_ret = res['5y']['mean_ret']
        cliff_str  = f' CLIFF[{u["hard_cliff"]}]' if u['hard_cliff'] else ''
        print(f"  iter {iter_n:>2d}    util={u['utility']:>+8.2f}  "
              f"5y_dd={five_y_dd:>5.1f}%  22n_dd={twonow_dd:>5.1f}%  "
              f"5y_ret={five_y_ret:>+10.1e}%  t={time.time()-ti:.0f}s  "
              f"[{param_str}]{cliff_str}")

    # ---------- Final summary ----------
    total = time.time() - t_start
    print(f"\n[done] {len(evaluated)+1} total evals (incl baseline) in {total:.0f}s ({total/60:.1f}min)")

    print("\n" + "=" * 110)
    print(f"Top 15 configs by DD-primary utility (baseline 5y_dd={base_5y_dd:.1f}%):")
    print("=" * 110)
    sorted_eval = sorted(evaluated, key=lambda e: -e['utility'])
    print(f"{'rank':>4s}  {'util':>8s}  {'5y_dd':>6s}  {'Δdd':>5s}  {'22n_dd':>6s}  "
          f"{'5y_ret':>14s}  {'shape':<10s}  {'mid':>3s}  {'hw':>3s}  "
          f"{'flr':>5s}  {'ceil':>5s}  {'pk':>4s}  {'cliff':>5s}")
    for i, e in enumerate(sorted_eval[:15]):
        p = e['params']
        five_y_dd = e['res'].get('5y', {}).get('worst_dd', 0)
        twonow_dd = e['res'].get('22-now', {}).get('worst_dd', 0)
        five_y    = e['res'].get('5y', {}).get('mean_ret', 0)
        delta_dd  = five_y_dd - base_5y_dd
        cliff = ('Y' if e['utility'] < -1e8 else '')
        print(f"{i+1:>4d}  {e['utility']:>+8.2f}  {five_y_dd:>5.1f}%  "
              f"{delta_dd:>+4.1f}  {twonow_dd:>5.1f}%  "
              f"{five_y:>+14.1e}%  "
              f"{p['shape']:<10s}  {p['midpoint']:>3d}  {p['half_width']:>3d}  "
              f"{p['floor']:>5.2f}  {p['ceil']:>5.2f}  {p['power_k']:>4.1f}  {cliff:>5s}")


if __name__ == '__main__':
    main()
