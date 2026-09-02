"""
Phase OP2 — Baseline at N=1000 under dead-hold + bimodal (2026-05-01).

Background: At N=300, baseline 5y DD swung 80.3% ↔ 83.4% across two consecutive
runs (same config, different seeds). 3pp seed-to-seed variance is too noisy to
make ship decisions when the strategy sits right at the 80% floor.

This run: baseline only, N=1000 × 8 windows. Tighter noise band gives a
definitive read on whether dead-hold + bimodal naturally clears the strict 80%
gate, or whether the strategy structurally hovers at 80%.

Expected runtime: ~30 min with 4-window parallelism.

Output: experiments/op2/phase_op2_n1000_results.json
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

N_ITER = 1000
DD_FLOOR_ABS = 80.0


def main():
    t0 = time.time()
    print(f"=== Phase OP2 baseline N=1000 — definitive DD read ===", flush=True)
    print(f"N_ITER={N_ITER}, DD_FLOOR_ABS={DD_FLOOR_ABS}%", flush=True)
    print(f"Dead-hold ON, bimodal fill ON (defaults)", flush=True)

    cfg = {}  # baseline
    print(f"\n=== baseline (current shipped) ===", flush=True)
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

    print("\n\n" + "="*72, flush=True)
    print("VERDICT", flush=True)
    print("="*72, flush=True)

    breaches = []
    near_floor = []
    for label, _, _ in WINDOWS:
        r = results.get(label, {})
        if 'error' in r:
            breaches.append(f"{label}: ERROR")
            continue
        dd = r.get('worst_dd', 100.0)
        if dd > DD_FLOOR_ABS:
            breaches.append(f"{label}: {dd:.1f}%")
        elif dd > DD_FLOOR_ABS - 2.0:
            near_floor.append(f"{label}: {dd:.1f}%")

    print(f"\nStrict 80% absolute gate: {'PASS ✓' if not breaches else 'FAIL ✗'}", flush=True)
    if breaches:
        print(f"  Breaches: {breaches}", flush=True)
    if near_floor:
        print(f"  Near-floor (78-80%): {near_floor}", flush=True)

    # 82% relaxed gate (Option C)
    breach_82 = [f"{label}: {results.get(label, {}).get('worst_dd', 100):.1f}%"
                 for label, _, _ in WINDOWS
                 if results.get(label, {}).get('worst_dd', 100) > 82.0]
    print(f"\nRelaxed 82% gate (Option C): {'PASS ✓' if not breach_82 else 'FAIL ✗'}", flush=True)
    if breach_82:
        print(f"  Breaches: {breach_82}", flush=True)

    # 84% relaxed gate
    breach_84 = [f"{label}: {results.get(label, {}).get('worst_dd', 100):.1f}%"
                 for label, _, _ in WINDOWS
                 if results.get(label, {}).get('worst_dd', 100) > 84.0]
    print(f"Relaxed 84% gate: {'PASS ✓' if not breach_84 else 'FAIL ✗'}", flush=True)
    if breach_84:
        print(f"  Breaches: {breach_84}", flush=True)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'phase_op2_n1000_results.json')
    payload = {
        'n_iter': N_ITER,
        'dead_hold': True,
        'bimodal_fill': True,
        'results': results,
        'breaches_80': breaches,
        'breaches_82': breach_82,
        'breaches_84': breach_84,
        'elapsed_min': round((time.time() - t0) / 60.0, 1),
    }
    with open(out_path, 'w') as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"\nResults saved → {out_path}", flush=True)
    print(f"Total elapsed: {payload['elapsed_min']:.1f} min", flush=True)


if __name__ == '__main__':
    main()
