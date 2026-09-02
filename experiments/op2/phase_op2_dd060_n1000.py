"""
Phase OP2 — DD060 validation at N=1000 (2026-05-01).

Per-user direction: ship the lowest-DD candidate now. DD060 (DD_CIRCUIT_BREAKER=0.60)
showed 5y=75.9% at N=300 but with same ~3pp noise band as baseline. This run
locks in N=1000 numbers before flipping strategy_config.

Output: experiments/op2/phase_op2_dd060_n1000_results.json
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


def main():
    t0 = time.time()
    cfg = {'DD_CIRCUIT_BREAKER': 0.60}
    print(f"=== Phase OP2 DD060 — N={N_ITER} validation ===", flush=True)
    print(f"cfg: {cfg}", flush=True)

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
    for floor in (80.0, 82.0, 84.0):
        breaches = [f"{label}: {results.get(label, {}).get('worst_dd', 100):.1f}%"
                    for label, _, _ in WINDOWS
                    if results.get(label, {}).get('worst_dd', 100) > floor]
        v = 'PASS ✓' if not breaches else 'FAIL ✗'
        print(f"  {floor:.0f}% gate: {v}", flush=True)
        if breaches:
            print(f"    {breaches}", flush=True)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'phase_op2_dd060_n1000_results.json')
    payload = {
        'n_iter': N_ITER,
        'dead_hold': True,
        'bimodal_fill': True,
        'cfg': cfg,
        'results': results,
        'elapsed_min': round((time.time() - t0) / 60.0, 1),
    }
    with open(out_path, 'w') as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\nResults saved → {out_path}", flush=True)
    print(f"Total elapsed: {payload['elapsed_min']:.1f} min", flush=True)


if __name__ == '__main__':
    main()
