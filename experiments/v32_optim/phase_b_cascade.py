"""Phase B — Cascade allocation Bayesian sweep on v32 algorithm.

Targets cascade allocation on the call side (ultra/top/mid/low) and put side
(put_top/put_mid/put_low). Locks all other modern mechanisms at strategy_config
defaults: F3F, regime asymmetric slopes (UP=0, DOWN=1, PUT_UP=-0.5), DD breaker
(0.60), DD soft band (0.40/0.60/0.50), dead-hold, EARN_SUPP_PUT, CT promote.

Param space rationale:
  - Calls: monotonic gradient enforced (ultra >= top >= mid >= low). Ranges
    centered on Phase 7 winner 0.20/0.15/0.12/0.10. Tests both flatter
    (more 75-79 mass) and steeper (more 95+ concentration) variants.
  - Puts: explicitly tests monotonic shape 0.13/0.10/0.08 since current
    0.10/0.12/0.12 was never re-validated. Range tightened to avoid
    same-bar slot displacement against calls.

Utility:
  log(1 + 5y_compound/100) * 1.5
  + log(1 + 22-now_compound/100) * 1.5
  + sum log(1 + per_year_compound/100) * 1.0  for {2021, 2022, 2023, 2024, 2025, dip}
  - dd_penalty: 200 * max(0, max_dd - 0.70)^2 / 0.01
  - collapse_penalty: 1000 * max_collapse_frac

Soft DD target: 0.70 (penalty kicks in above). Hard cap is implicit via
penalty severity: 0.85 dd -> 4.5 penalty, 0.90 dd -> 8.0 penalty.

Output:
  experiments/v32_optim/phase_b_cascade_results.jsonl  — full eval log
  Final summary printed with top-10 configs by utility, with per-window
  breakdown for the winner.

Usage:
  PYTHONIOENCODING=utf-8 python -u experiments/v32_optim/phase_b_cascade.py 2>&1 \\
    | tee experiments/v32_optim/phase_b_cascade.log
"""
from __future__ import annotations
import json
import math
import os
import sys
import time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc  # noqa: E402
from database.models.core import AlgorithmVersion  # noqa: E402
import strategy_config as _sc  # noqa: E402

# ---------- Windows ----------
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
WINDOW_WEIGHTS = {
    '2021': 1.0, '2022': 1.0, '2023': 1.0, '2024': 1.0, '2025': 1.0,
    'dip': 1.0,
    '22-now': 1.5,
    '5y': 1.5,
}
DD_SOFT_TARGET = 0.70  # penalty kicks in above this

# ---------- Apply config via env vars (so MP workers inherit it) ----------
# CRITICAL: monte_carlo.py reads cascade allocs and TP/SL from env vars at
# module load. MP-spawn workers re-import monte_carlo, so we MUST set env
# vars BEFORE the worker pool spawns (or before each new run_window call,
# since the pool gets recreated each call). Patching mc.TIER_ALLOC in the
# parent does NOT propagate to spawned workers.
def apply_cfg(params: dict):
    """Set env vars for next MC run. Workers re-read these at import time."""
    # Cascade — env names must match monte_carlo.py overrides
    os.environ['TIER_ULTRA_OV']    = str(params['ultra'])
    os.environ['TIER_TOP_OV']      = str(params['top'])
    os.environ['TIER_MID_OV']      = str(params['mid'])
    os.environ['TIER_LOW_OV']      = str(params['low'])
    os.environ['TIER_OVERFLOW_OV'] = '0.0'
    os.environ['PUT_TIER_TOP_OV']  = str(params['put_top'])
    os.environ['PUT_TIER_MID_OV']  = str(params['put_mid'])
    os.environ['PUT_TIER_LOW_OV']  = str(params['put_low'])

    # Also patch parent process so any read in the orchestrator sees the values
    mc.TIER_ALLOC = {
        'ultra':    params['ultra'], 'top': params['top'],
        'mid':      params['mid'],   'low': params['low'],
        'overflow': 0.0,
    }
    mc.PUT_TIER_ALLOC = {
        'put_top': params['put_top'],
        'put_mid': params['put_mid'],
        'put_low': params['put_low'],
    }

    # N_ITER — env override (already supported in monte_carlo.py)
    os.environ['N_ITER_OVERRIDE'] = str(params.get('N_ITER', 200))
    mc.N_ITER = params.get('N_ITER', 200)
    mc.COLLISION_MODES = ['seeded']

    # Everything else stays at strategy_config defaults — no env override needed
    # because we're not varying them in this phase.

# ---------- Run + utility ----------
def run_config(params, version):
    apply_cfg(params)
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
        # log returns can be enormous in good windows; clip to keep utility sane
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

# ---------- Param space + monotonicity filter ----------
PARAM_GRID = {
    'ultra':   [0.18, 0.20, 0.22, 0.25],
    'top':     [0.12, 0.15, 0.17],
    'mid':     [0.10, 0.12, 0.14],
    'low':     [0.08, 0.10, 0.12],
    # puts: test monotonic vs current non-monotonic
    'put_top': [0.08, 0.10, 0.12, 0.13],
    'put_mid': [0.08, 0.10, 0.12],
    'put_low': [0.08, 0.10, 0.12],
}

def is_valid(p):
    # Calls monotonic non-strict
    if not (p['ultra'] >= p['top'] >= p['mid'] >= p['low']):
        return False
    # Total deployed if all 14 slots full at top tiers shouldn't blow up
    # (rough sanity — assume balanced fill)
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
                                if is_valid(p):
                                    out.append(p)
    return out

# ---------- Bayesian acquisition (kernel KNN UCB with diversity) ----------
def normalize(p):
    out = {}
    for k, vals in PARAM_GRID.items():
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

# ---------- Main ----------
def main():
    log_dir = os.path.join(ROOT, 'experiments', 'v32_optim')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'phase_b_cascade_results.jsonl')

    BUDGET = int(os.environ.get('BUDGET', '24'))
    BATCH  = int(os.environ.get('BATCH', '3'))
    N_ITER = int(os.environ.get('N_ITER', '200'))
    print(f"Phase B cascade — budget={BUDGET}, batch={BATCH}, N_ITER={N_ITER}, "
          f"DD soft target={DD_SOFT_TARGET*100:.0f}%, windows={len(SWEEP_WINDOWS)}")

    cands = enumerate_valid()
    print(f"Param space: {len(cands)} valid candidates after monotonicity filter")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"Active version: {version.git_commit if version else 'NONE'}")

    # Seeds: include current production + a few hand-picked variants
    seeds = [
        # current shipped Phase 7 + non-monotonic puts
        {'ultra':0.20,'top':0.15,'mid':0.12,'low':0.10, 'put_top':0.10,'put_mid':0.12,'put_low':0.12, 'N_ITER':N_ITER},
        # monotonic puts (tested but never re-validated)
        {'ultra':0.20,'top':0.15,'mid':0.12,'low':0.10, 'put_top':0.12,'put_mid':0.10,'put_low':0.08, 'N_ITER':N_ITER},
        # put_top tilted (since put≤15 is strongest per-trade WR)
        {'ultra':0.20,'top':0.15,'mid':0.12,'low':0.10, 'put_top':0.13,'put_mid':0.10,'put_low':0.08, 'N_ITER':N_ITER},
        # steeper call cascade
        {'ultra':0.25,'top':0.15,'mid':0.10,'low':0.08, 'put_top':0.10,'put_mid':0.10,'put_low':0.10, 'N_ITER':N_ITER},
        # flatter call cascade
        {'ultra':0.18,'top':0.15,'mid':0.12,'low':0.12, 'put_top':0.10,'put_mid':0.12,'put_low':0.12, 'N_ITER':N_ITER},
    ]

    evaluated = []
    def log(rec):
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')

    # --- Seed phase ---
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

    # --- Bayesian loop ---
    iters = 0
    while len(evaluated) < BUDGET:
        iters += 1
        progress = min(1.0, len(evaluated) / BUDGET)
        kappa = 2.0 + (1.0 - 2.0) * progress  # 2.0 -> 1.0
        scored = []
        for c in cands:
            key = tuple(sorted(c.items()))
            if any(tuple(sorted(e['params'].items())) == key for e in evaluated):
                continue
            n = normalize(c)
            ucb_val, mu, std = ucb(n, evaluated, kappa)
            scored.append((ucb_val, mu, std, c, n))
        scored.sort(key=lambda x: x[0], reverse=True)

        # diversity-aware batch
        batch = []
        for sc in scored:
            if len(batch) >= BATCH: break
            close = any(l1(sc[4], b[4]) < 0.10 for b in batch)
            if not close: batch.append(sc)
        if not batch and scored: batch = scored[:BATCH]
        if not batch:
            print("[no candidates left] stopping")
            break

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

    # --- Summary ---
    print("\n" + "="*120)
    print(f"PHASE B SUMMARY — {len(evaluated)} configs evaluated")
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
