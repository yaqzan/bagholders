"""
Phase OP2 Path B — DD circuit breaker tightening under dead-hold + bimodal (2026-05-01).

Background: Phase OP2 re-baseline (phase_op2_postdh.py) under dead-hold + bimodal
showed baseline cleanly passing 6/8 windows, with 22-now marginal at 80.9% and
5y at 83.4%. WIDE_SL_MP10 INVERTED under dead-hold (baseline now wins compound
2x).

Hypothesis: The 5y residual DD breach is correlated-multi-year drawdown that
the running-DD signal can throttle directly via DD_CIRCUIT_BREAKER. Current
shipped value 0.68. Tightening to 0.65 or 0.60 should pause new entries earlier
during deep drawdowns, capping per-window WorstDD without touching scoring or
SL parameters.

Candidates:
  - baseline (DD=0.68, current shipped) — for comparison
  - DD065 (DD=0.65)
  - DD060 (DD=0.60)

N=300 × 8 windows × 3 candidates. ~9 min total with 4-window parallelism.

Output: experiments/op2/phase_op2_ddtighten_results.json
"""
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from phase3_canonical_mc import run_one_window, WINDOWS

N_ITER = 300
DD_FLOOR_ABS = 80.0


CANDIDATES = {
    'baseline_DD68': {},
    'DD065':         {'DD_CIRCUIT_BREAKER': 0.65},
    'DD060':         {'DD_CIRCUIT_BREAKER': 0.60},
}


def run_candidate(name: str, cfg: dict):
    print(f"\n=== {name}: {cfg} ===", flush=True)
    futs = {}
    with ProcessPoolExecutor(max_workers=4) as ex:
        for label, d_start, d_end in WINDOWS:
            futs[ex.submit(run_one_window, cfg, label, d_start, d_end, N_ITER)] = label
        results = {}
        for f in as_completed(futs):
            label = futs[f]
            try:
                r = f.result()
            except Exception as e:
                r = {'error': str(e)}
            if 'error' in r:
                print(f"  {label:<8} ERROR: {r['error']}", flush=True)
            else:
                print(f"  {label:<8} mean_ret={r['mean_ret']:>+15,.1f}%  "
                      f"DD={r['worst_dd']:>5.1f}%  coll={r['p_coll']:>4.1f}%  "
                      f"C/P TP=({r['call_tp']:.1f}/{r['put_tp']:.1f})  [{r.get('elapsed', 0):.0f}s]",
                      flush=True)
            results[label] = r
    return results


def evaluate_gate(res: dict) -> dict:
    breaches = []
    for label, _, _ in WINDOWS:
        r = res.get(label, {})
        if 'error' in r:
            breaches.append(f"{label}: ERROR")
            continue
        dd = r.get('worst_dd', 100.0)
        if dd > DD_FLOOR_ABS:
            breaches.append(f"{label}: DD={dd:.1f}%")
    return {'pass': len(breaches) == 0, 'breaches': breaches}


def main():
    t0 = time.time()
    print(f"=== Phase OP2 Path B — DD breaker tightening under dead-hold ===", flush=True)
    print(f"N_ITER={N_ITER}, DD_FLOOR_ABS={DD_FLOOR_ABS}%", flush=True)
    print(f"Candidates: {list(CANDIDATES.keys())}", flush=True)

    all_results = {}
    for name, cfg in CANDIDATES.items():
        all_results[name] = run_candidate(name, cfg)

    print("\n\n" + "="*72, flush=True)
    print("VERDICT", flush=True)
    print("="*72, flush=True)

    base = all_results.get('baseline_DD68', {})
    print(f"\n{'window':<10}", end='')
    for name in CANDIDATES.keys():
        print(f"{name:<28}", end='')
    print(flush=True)

    for label, _, _ in WINDOWS:
        print(f"{label:<10}", end='')
        for name in CANDIDATES.keys():
            r = all_results.get(name, {}).get(label, {})
            if 'error' in r:
                cell = f"ERROR"
            else:
                cell = f"DD={r.get('worst_dd', 0):>5.1f}%  ret={r.get('mean_ret', 0):+10,.0f}%"
            print(f"{cell:<28}", end='')
        print(flush=True)

    print("\n\nGATE RESULTS (strict 80% absolute):", flush=True)
    for name in CANDIDATES.keys():
        gate = evaluate_gate(all_results.get(name, {}))
        verdict = 'PASS ✓' if gate['pass'] else 'FAIL ✗'
        print(f"  {name:<20} {verdict}", flush=True)
        if gate['breaches']:
            for b in gate['breaches']:
                print(f"    {b}", flush=True)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'phase_op2_ddtighten_results.json')
    payload = {
        'n_iter': N_ITER,
        'dead_hold': True,
        'bimodal_fill': True,
        'results': all_results,
        'gates': {name: evaluate_gate(all_results.get(name, {})) for name in CANDIDATES.keys()},
        'elapsed_min': round((time.time() - t0) / 60.0, 1),
    }
    with open(out_path, 'w') as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"\nResults saved → {out_path}", flush=True)
    print(f"Total elapsed: {payload['elapsed_min']:.1f} min", flush=True)


if __name__ == '__main__':
    main()
