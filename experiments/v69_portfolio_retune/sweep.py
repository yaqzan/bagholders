"""Coarse DD-primary sweep for the v69 honest-substrate portfolio retune.

Runs a curated candidate list (sizing + barrier axes) via subprocess on a subset
of binding windows at coarse N, ranks by a DD-PRIMARY / collapse-penalized utility
(compound is theoretical above ~1e10% per Stage-3 doctrine), and logs everything.

Candidate axes:
  Stage-3 sizing (dominant survival lever): cascade scale, MaxPos, DD soft-band.
  Stage-2 barriers (thin-edge BE management): TP/SL gap, breadth threshold,
                                              PUT_TP/PUT_SL.

Usage:
  PYTHONIOENCODING=utf-8 N_ITER=100 WINDOWS=2022,2025,22-now,5y \
    python -u experiments/v69_portfolio_retune/sweep.py CANDSET
"""
from __future__ import annotations
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402

# DD-primary utility. Compound is theoretical; we want SURVIVAL + low DD first,
# then positive median return as a tiebreak.
DD_HARD = 0.80      # absolute account-survival ceiling
DD_SOFT = 0.72      # penalty kicks in above this (Stage-3 T-gate spirit)


def utility(win_res, weights):
    """Lower DD + 0 collapse dominates; positive median return is the tiebreak."""
    max_dd = 0.0
    max_coll = 0.0
    logret_sum = 0.0
    surv_sum = 0.0
    for label, r in win_res.items():
        w = weights.get(label, 1.0)
        dd = r['worst_dd'] / 100.0
        coll = r['p_collapse'] / 100.0
        max_dd = max(max_dd, dd)
        max_coll = max(max_coll, coll)
        # median return contribution, clipped (compound theoretical at the top)
        mr = max(-99.0, min(r['med_ret'], 1e12))
        lr = math.log1p(mr / 100.0) if mr > -100 else -12.0
        logret_sum += w * max(-12.0, lr)
        # survival reward: 1 - collapse fraction
        surv_sum += w * (1.0 - coll)
    dd_excess_soft = max(0.0, max_dd - DD_SOFT)
    dd_pen = 60.0 * (dd_excess_soft ** 2) / 0.01
    dd_hard_pen = 400.0 if max_dd > DD_HARD else 0.0
    coll_pen = 250.0 * max_coll
    # weight survival and DD as the dominant terms; logret as a smaller tiebreak
    util = (8.0 * surv_sum) - dd_pen - dd_hard_pen - coll_pen + 0.25 * logret_sum
    return dict(utility=util, max_dd=max_dd, max_coll=max_coll,
                logret_sum=logret_sum, surv_sum=surv_sum,
                dd_pen=dd_pen, dd_hard_pen=dd_hard_pen, coll_pen=coll_pen)


# ---- Candidate sets ----------------------------------------------------------
# Baseline current params (empty dict = strategy_config defaults).
CUR = {}

# A cascade-scaled config: multiply all call+put tiers by `s`, keep shape.
def cascade(s, ps=None, **kw):
    """s = call+put cascade scale; ps = optional separate put scale (defaults to s)."""
    if ps is None:
        ps = s
    base = dict(
        TIER_ULTRA=round(0.20 * s, 4), TIER_TOP=round(0.15 * s, 4),
        TIER_MID=round(0.10 * s, 4), TIER_LOW=round(0.10 * s, 4),
        PUT_TIER_TOP=round(0.12 * ps, 4), PUT_TIER_MID=round(0.10 * ps, 4),
        PUT_TIER_LOW=round(0.08 * ps, 4),
    )
    base.update(kw)
    return base


CANDSETS = {
    # Phase S1: pure sizing — does de-leveraging rescue the 5y collapse?
    'sizing': {
        'cur':            CUR,
        'casc_0.85':      cascade(0.85),
        'casc_0.70':      cascade(0.70),
        'casc_0.55':      cascade(0.55),
        'casc_0.45':      cascade(0.45),
        'casc_0.70_mp10': cascade(0.70, MAX_POSITIONS=10, MAX_POSITIONS_CALL=8, MAX_POSITIONS_PUT=6),
        'casc_0.55_mp10': cascade(0.55, MAX_POSITIONS=10, MAX_POSITIONS_CALL=8, MAX_POSITIONS_PUT=6),
        'casc_0.55_mp8':  cascade(0.55, MAX_POSITIONS=8, MAX_POSITIONS_CALL=7, MAX_POSITIONS_PUT=5),
        'casc_0.45_mp8':  cascade(0.45, MAX_POSITIONS=8, MAX_POSITIONS_CALL=7, MAX_POSITIONS_PUT=5),
        # DD soft-band tighter (earlier+deeper call contraction)
        'casc_0.70_ddtight': cascade(0.70, DD_SOFT_BAND_LO=0.25, DD_SOFT_BAND_HI=0.45, DD_SOFT_CALL_FLOOR=0.30),
        'casc_0.55_ddtight': cascade(0.55, DD_SOFT_BAND_LO=0.25, DD_SOFT_BAND_HI=0.45, DD_SOFT_CALL_FLOOR=0.30),
        # Put-exposure cut: tests whether the "never cut puts" doctrine (validated
        # on the INFLATED ~45-50% put-TP substrate) still holds at honest ~25-40% put TP.
        'casc_0.70_putcut': cascade(0.70, ps=0.40),
        'casc_0.55_putcut': cascade(0.55, ps=0.40, MAX_POSITIONS_PUT=6),
        'casc_0.55_mp8_putcut': cascade(0.55, ps=0.40, MAX_POSITIONS=8, MAX_POSITIONS_CALL=7, MAX_POSITIONS_PUT=4),
    },
    # Phase S2: barriers on top of a survivable sizing base (filled after S1 picks base).
    # Placeholder; real entries injected by run after S1.
    'barriers': {},
}

WEIGHTS = {'2021': 1.0, '2022': 1.2, '2023': 1.0, '2024': 1.0, '2025': 1.2,
           'dip': 1.0, '22-now': 1.5, '5y': 1.5}


def main():
    candset = sys.argv[1] if len(sys.argv) > 1 else 'sizing'
    n_iter = int(os.environ.get('N_ITER', '100'))
    windows = os.environ.get('WINDOWS', '2022,2025,22-now,5y').split(',')
    cands = CANDSETS[candset]
    if candset == 'barriers':
        cands = json.loads(os.environ['BARRIER_CANDS_JSON'])
    log_path = os.path.join(os.path.dirname(__file__), f'sweep_{candset}_n{n_iter}.jsonl')
    print(f'[sweep:{candset}] N={n_iter} windows={windows} cands={len(cands)}', flush=True)
    print(f'[sweep:{candset}] log -> {log_path}', flush=True)

    evaluated = []
    for name, params in cands.items():
        t0 = time.time()
        try:
            win_res = run_candidate(params, n_iter=n_iter, windows=windows, tag=name)
        except Exception as e:
            print(f'  [FAIL] {name}: {e}', flush=True)
            continue
        u = utility(win_res, WEIGHTS)
        rec = dict(name=name, params=params, windows=win_res, util=u,
                   duration_s=time.time() - t0)
        evaluated.append(rec)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')
        ddstr = '  '.join(f"{w}:{win_res[w]['worst_dd']:.0f}/{win_res[w]['p_collapse']:.0f}c"
                          for w in windows)
        print(f"  {name:<20} util={u['utility']:+8.2f} maxDD={u['max_dd']*100:.1f}% "
              f"maxColl={u['max_coll']*100:.0f}%  [{ddstr}]  dur={rec['duration_s']:.0f}s",
              flush=True)

    print('\n' + '=' * 100, flush=True)
    print(f'RANKED ({candset}) by DD-primary utility:', flush=True)
    ranked = sorted(evaluated, key=lambda r: r['util']['utility'], reverse=True)
    for i, r in enumerate(ranked):
        u = r['util']
        print(f"{i+1:>2} {r['name']:<20} util={u['utility']:+8.2f} maxDD={u['max_dd']*100:5.1f}% "
              f"coll={u['max_coll']*100:3.0f}% logret={u['logret_sum']:+6.1f}", flush=True)
    if ranked:
        best = ranked[0]
        print(f"\nBEST: {best['name']}  params={best['params']}", flush=True)
        print(f"{'win':<8}{'MedRet%':>16}{'WorstDD':>9}{'Coll':>6}{'CTP':>6}{'PTP':>6}", flush=True)
        for w in windows:
            r = best['windows'][w]
            print(f"{w:<8}{r['med_ret']:>+16,.1f}{r['worst_dd']:>8.1f}%{r['p_collapse']:>5.0f}%"
                  f"{r.get('call_tp',0):>5.1f}%{r.get('put_tp',0):>5.1f}%", flush=True)


if __name__ == '__main__':
    main()
