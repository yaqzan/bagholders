"""Validate one candidate (and the baseline) at N x 8 windows on v69 honest substrate.

Usage:
  PYTHONIOENCODING=utf-8 N_ITER=500 python -u experiments/v69_portfolio_retune/validate.py PARAMS_JSON_FILE

PARAMS_JSON_FILE: a json file {"name":..., "params":{...}}. If params empty => baseline.
Writes <name>_n<N>.json and prints the T1-T7 comparison vs baseline_n<N>.json if present.
"""
from __future__ import annotations
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402

ALL = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']


def main():
    spec_path = sys.argv[1]
    with open(spec_path, encoding='utf-8') as f:
        spec = json.load(f)
    name = spec['name']
    params = spec.get('params', {})
    n_iter = int(os.environ.get('N_ITER', '500'))
    here = os.path.dirname(__file__)
    out_path = os.path.join(here, f'{name}_n{n_iter}.json')

    print(f'[validate] {name} N={n_iter} windows={ALL}', flush=True)
    t0 = time.time()
    res = run_candidate(params, n_iter=n_iter, windows=ALL, tag=f'val_{name}')
    dur = time.time() - t0
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'name': name, 'params': params, 'n_iter': n_iter,
                   'windows': res, 'duration_s': dur}, f, indent=2)

    hdr = f"{'window':<8}{'MedRet%':>16}{'MeanRet%':>16}{'WorstDD':>9}{'MeanDD':>8}{'Coll':>6}{'CTP':>6}{'PTP':>6}"
    print(hdr, flush=True)
    print('-' * len(hdr), flush=True)
    for w in ALL:
        r = res[w]
        print(f"{w:<8}{r['med_ret']:>+16,.1f}{r['mean_ret']:>+16,.1f}{r['worst_dd']:>8.1f}%"
              f"{r['mean_dd']:>7.1f}%{r['p_collapse']:>5.0f}%{r.get('call_tp',0):>5.1f}%"
              f"{r.get('put_tp',0):>5.1f}%", flush=True)
    print(f'[validate] done in {dur:.0f}s -> {out_path}', flush=True)

    # T1-T7 vs baseline if available
    base_path = os.path.join(here, f'baseline_n{n_iter}.json')
    if os.path.exists(base_path) and params:
        with open(base_path, encoding='utf-8') as f:
            base = json.load(f)['windows']
        print('\n=== T-GATE COMPARISON vs baseline ===', flush=True)
        print(f"{'window':<8}{'baseDD':>9}{'candDD':>9}{'dDD':>8}{'baseColl':>9}{'candColl':>9}", flush=True)
        worst_5y_ok = True
        per_window_ok = True
        coll_ok = True
        for w in ALL:
            b, c = base[w], res[w]
            ddd = c['worst_dd'] - b['worst_dd']
            flag = ''
            if w == '5y' and ddd > 1.0:
                worst_5y_ok = False; flag = ' <-T4 FAIL'
            if ddd > 5.0:
                per_window_ok = False; flag += ' <-T5 FAIL'
            if c['p_collapse'] > 0.0:
                coll_ok = False
            print(f"{w:<8}{b['worst_dd']:>8.1f}%{c['worst_dd']:>8.1f}%{ddd:>+7.1f}{b['p_collapse']:>8.0f}%{c['p_collapse']:>8.0f}%{flag}", flush=True)
        print(f"\nT4 (5y WorstDD <= base+1.0pp): {'PASS' if worst_5y_ok else 'FAIL'}", flush=True)
        print(f"T5 (no window DD > base+5pp):  {'PASS' if per_window_ok else 'FAIL'}", flush=True)
        print(f"T6 (0% collapse all windows):  {'PASS' if coll_ok else 'FAIL'}", flush=True)
        # NOTE: when baseline ITSELF collapses, the right gate is absolute
        # (DD<80%, 0 collapse), not relative-to-broken-baseline. Reported below.
        cand_abs_ok = all(res[w]['worst_dd'] <= 80.0 and res[w]['p_collapse'] == 0.0 for w in ALL)
        print(f"ABS (cand all windows DD<=80% & 0 collapse): {'PASS' if cand_abs_ok else 'FAIL'}", flush=True)


if __name__ == '__main__':
    main()
