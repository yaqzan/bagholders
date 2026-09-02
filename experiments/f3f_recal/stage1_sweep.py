"""F3F call-side recalibration — Stage 1 high-blast-radius candidates + Bayesian iteration.

Purpose: post-v45 (ETF de-contamination) + v46 (WVD-Wave) breadth distribution
profile (`profile_v46.py`) revealed that the current F3F call curve floors at
exactly the breadth band where call alpha is highest:

    brd 20-30: N=1,060  WR15=64.2%   F3F scale=0.50  ← floored
    brd 50-80: N=12,971 WR15=58-61%  F3F scale=1.00  ← full

The curve is structurally inverted vs the empirical signal. Stage 1 tests 5
hand-designed candidates spanning the call-side F3F design space; Stage 2
runs Bayesian iteration around the winning direction.

Stage 1 candidates (all hold puts at production):
  C0 baseline: FLOOR=0.50  LOW=30  THRESH=50
  C1 disable:  FLOOR=1.00  LOW=30  THRESH=50  (cut is wrong, just remove it)
  C2 invert:   FLOOR=1.30  LOW=25  THRESH=50  (boost at low brd, taper to 1.0 at THRESH)
  C3 narrower: FLOOR=0.40  LOW=15  THRESH=25  (cut is right but mis-positioned, only at extreme stress)
  C4 softer:   FLOOR=0.75  LOW=30  THRESH=50  (cut is right but too aggressive)

After seeds, ~15-20 Bayesian iterations refine within the most successful
direction. Budget controllable via env BUDGET (default 25 = 5 seeds + 20 bayes).

Utility: same shape as v32_optim phase_b (log compound + DD soft penalty above 0.70).

Output:
  experiments/f3f_recal/stage1_results.jsonl   — full eval log
  experiments/f3f_recal/stage1.log             — stdout

Usage:
  PYTHONIOENCODING=utf-8 python -u experiments/f3f_recal/stage1_sweep.py 2>&1 \
    | tee experiments/f3f_recal/stage1.log
"""
from __future__ import annotations
import io
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

# ---------- Windows (canonical 8) ----------
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
DD_SOFT_TARGET = 0.70


# ---------- Apply F3F call config via env vars ----------
def apply_cfg(params: dict, n_iter: int):
    """Set F3F env vars BEFORE the MC worker pool spawns. MP-spawn workers
    re-import monte_carlo and re-read env, so this propagates correctly."""
    os.environ['F3F_CALL_FLOOR']  = str(params['call_floor'])
    os.environ['F3F_CALL_LOW']    = str(params['call_low'])
    os.environ['F3F_CALL_THRESH'] = str(params['call_thresh'])

    # Patch parent process for any orchestrator reads
    mc.F3F_CALL_FLOOR  = float(params['call_floor'])
    mc.F3F_CALL_LOW    = float(params['call_low'])
    mc.F3F_CALL_THRESH = float(params['call_thresh'])

    # Iteration count
    os.environ['N_ITER_OVERRIDE'] = str(n_iter)
    mc.N_ITER = n_iter
    mc.COLLISION_MODES = ['seeded']


# ---------- Run + utility ----------
def run_config(params, version, n_iter):
    apply_cfg(params, n_iter)
    out = {}
    for label, d_start, d_end in SWEEP_WINDOWS:
        import contextlib
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


# ---------- Param space (Bayesian search) ----------
# F3F_CALL_FLOOR can be either < 1.0 (cut) or > 1.0 (amp). Both modes share
# the same curve definition: floor at low_brd, taper to 1.0 at thresh.
PARAM_GRID = {
    'call_floor':  [0.40, 0.50, 0.65, 0.75, 0.85, 1.00, 1.15, 1.30, 1.45],
    'call_low':    [10, 15, 20, 25, 30, 35, 40],
    'call_thresh': [25, 30, 40, 50, 60, 70],
}


def is_valid(p):
    # call_thresh must be > call_low (otherwise curve is degenerate)
    if p['call_thresh'] <= p['call_low']:
        return False
    # Don't waste compute on extreme combos that won't matter
    # (e.g. floor=0.40 AND low=10 means cut only fires below brd=10, basically a no-op)
    if p['call_floor'] < 0.50 and p['call_low'] < 20:
        return False
    if p['call_floor'] > 1.30 and p['call_thresh'] > 60:
        return False
    return True


def enumerate_valid():
    out = []
    for fl in PARAM_GRID['call_floor']:
        for lo in PARAM_GRID['call_low']:
            for th in PARAM_GRID['call_thresh']:
                p = {'call_floor': fl, 'call_low': lo, 'call_thresh': th}
                if is_valid(p):
                    out.append(p)
    return out


# ---------- Bayesian acquisition (kernel KNN UCB) ----------
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
    mu = sum(w*u for w, u in zip(ws, us)) / ws_sum
    var = sum(w*(u-mu)**2 for w, u in zip(ws, us)) / ws_sum
    std = math.sqrt(var + 1.0/(1.0 + ws_sum))
    return mu, std


def ucb(cand_norm, evaluated, kappa=2.0):
    mu, std = surrogate(cand_norm, evaluated)
    return mu + kappa * std, mu, std


# ---------- Main ----------
def main():
    log_dir = os.path.join(ROOT, 'experiments', 'f3f_recal')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'stage1_results.jsonl')
    # truncate existing log so re-runs are clean
    if os.path.exists(log_path):
        os.remove(log_path)

    BUDGET = int(os.environ.get('BUDGET', '25'))
    BATCH  = int(os.environ.get('BATCH', '3'))
    N_ITER = int(os.environ.get('N_ITER', '300'))

    print(f"F3F Stage 1 — budget={BUDGET}, batch={BATCH}, N_ITER={N_ITER}, "
          f"DD soft target={DD_SOFT_TARGET*100:.0f}%, windows={len(SWEEP_WINDOWS)}")

    cands = enumerate_valid()
    print(f"Param space: {len(cands)} valid candidates after monotonicity filter")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"Active version: {version.git_commit if version else 'NONE'}")

    # Stage 1 hand-designed seeds (5 high-blast-radius candidates)
    seeds = [
        {'call_floor': 0.50, 'call_low': 30, 'call_thresh': 50, 'tag': 'C0_baseline'},
        {'call_floor': 1.00, 'call_low': 30, 'call_thresh': 50, 'tag': 'C1_disable'},
        {'call_floor': 1.30, 'call_low': 25, 'call_thresh': 50, 'tag': 'C2_invert'},
        {'call_floor': 0.40, 'call_low': 15, 'call_thresh': 25, 'tag': 'C3_narrower'},
        {'call_floor': 0.75, 'call_low': 30, 'call_thresh': 50, 'tag': 'C4_softer'},
    ]

    evaluated = []
    def log(rec):
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')

    # --- Seed phase ---
    for s in seeds:
        tag = s.pop('tag')
        t0 = time.time()
        win_res = run_config(s, version, N_ITER)
        u = utility(win_res)
        rec = {
            'tag': tag,
            'params': s,
            'norm': normalize(s),
            'utility': u['utility'],
            'util_info': u,
            'windows': win_res,
            'duration_s': time.time() - t0,
        }
        evaluated.append(rec)
        log({'event': 'eval', 'phase': 'seed', **rec})
        print(f"[seed] {tag:>14s}  util={u['utility']:+.3f}  "
              f"5y={win_res['5y']['mean_ret']:+.0e}%  "
              f"22n={win_res['22-now']['mean_ret']:+.0e}%  "
              f"maxDD={u['max_dd']*100:.1f}%  "
              f"dur={rec['duration_s']:.0f}s")

    # --- Bayesian loop ---
    iters = 0
    while len(evaluated) < BUDGET:
        iters += 1
        progress = min(1.0, len(evaluated) / BUDGET)
        kappa = 2.0 + (1.0 - 2.0) * progress  # 2.0 -> 1.0 over budget
        scored = []
        for c in cands:
            key = tuple(sorted(c.items()))
            if any(tuple(sorted(e['params'].items())) == key for e in evaluated):
                continue
            n = normalize(c)
            ucb_val, mu, std = ucb(n, evaluated, kappa)
            scored.append((ucb_val, mu, std, c, n))
        scored.sort(key=lambda x: x[0], reverse=True)

        # Diversity-aware batch
        batch = []
        for sc in scored:
            if len(batch) >= BATCH:
                break
            close = any(l1(sc[4], b[4]) < 0.10 for b in batch)
            if not close:
                batch.append(sc)
        if not batch and scored:
            batch = scored[:BATCH]
        if not batch:
            print("[no candidates left] stopping")
            break

        print(f"\n[iter {iters}  kappa={kappa:.2f}]  picking {len(batch)}:")
        for ucb_v, mu, std, c, _ in batch:
            print(f"  ucb={ucb_v:+.3f} mu={mu:+.3f} std={std:.3f}  {c}")

        for ucb_v, mu, std, c, n in batch:
            t0 = time.time()
            win_res = run_config(c, version, N_ITER)
            u = utility(win_res)
            rec = {
                'tag': f'bayes_{len(evaluated)+1:03d}',
                'params': c,
                'norm': n,
                'utility': u['utility'],
                'util_info': u,
                'windows': win_res,
                'duration_s': time.time() - t0,
                'pred_mu': mu,
                'pred_std': std,
                'iter': iters,
            }
            evaluated.append(rec)
            log({'event': 'eval', 'phase': 'bayes', **rec})
            print(f"  [eval] util={u['utility']:+.3f}  "
                  f"5y={win_res['5y']['mean_ret']:+.0e}%  "
                  f"22n={win_res['22-now']['mean_ret']:+.0e}%  "
                  f"maxDD={u['max_dd']*100:.1f}%  "
                  f"dur={rec['duration_s']:.0f}s  cfg={c}")

    # --- Summary ---
    print("\n" + "="*120)
    print(f"F3F STAGE 1 SUMMARY — {len(evaluated)} configs evaluated")
    print("="*120)
    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    print(f"{'rk':>2}  {'tag':>14s}  {'utility':>8}  {'logsum':>7}  {'maxDD':>6}  {'col%':>5}  params")
    for i, r in enumerate(ranked[:15]):
        u = r['util_info']
        print(f"{i+1:>2}  {r.get('tag', '?'):>14s}  {r['utility']:>+8.3f}  {u['log_sum']:>+7.2f}  "
              f"{u['max_dd']*100:>6.1f}  {u['max_collapse']*100:>5.2f}  {r['params']}")

    best = ranked[0]
    print(f"\nBest: {best.get('tag', '?')} {best['params']}")
    print(f"\n{'window':<10}  {'MeanRet%':>14}  {'WorstDD':>8}  {'Col%':>5}  "
          f"{'CallTP':>6}  {'PutTP':>6}  {'CallTr':>7}  {'PutTr':>6}")
    for label, _, _ in SWEEP_WINDOWS:
        w = best['windows'].get(label, {})
        print(f"{label:<10}  {w.get('mean_ret', 0):>+14.1f}  {w.get('worst_dd', 0):>8.1f}  "
              f"{w.get('p_collapse', 0):>5.2f}  {w.get('call_tp', 0):>6.1f}  "
              f"{w.get('put_tp', 0):>6.1f}  {w.get('call_trades', 0):>7.1f}  "
              f"{w.get('put_trades', 0):>6.1f}")


if __name__ == '__main__':
    main()
