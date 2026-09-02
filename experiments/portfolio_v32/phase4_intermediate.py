"""
Portfolio v32 — Phase 4: intermediate variants between baseline (mid=low=0.15)
and D_midlow_12 (0.12). Phase 3 showed D_midlow_12 has universal DD reduction
but fails strict P3b/P4 gates by small margins (22-now ret×0.85, 2022 ret×0.74).

Hypothesis: a milder cut (mid/low = 0.135 or 0.13) may capture most of the DD
improvement while staying within the ±25% annual gate.

Also testing asymmetric variants:
  D_mid_only_12: cut only mid (80-84) tier
  D_low_only_12: cut only low (75-79) tier — this is the highest-N tier so most impactful

8 windows × N=300 each, ship-grade.
"""
import json
import os
import sys
import time
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'experiments', 'op2'))

from phase3_canonical_mc import run_one_window, WINDOWS

N_ITER = 300
RET_REGRESSION_TOL = 0.25
DD_REL_TOL = 1.0

CANDIDATES = [
    ('baseline', {}),
    # Intermediate uniform cuts
    ('D_midlow_135', {
        'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.135, 'low': 0.135, 'overflow': 0.0},
    }),
    ('D_midlow_13', {
        'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.13, 'low': 0.13, 'overflow': 0.0},
    }),
    # Asymmetric: only cut one tier
    ('D_low_only_12', {
        'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.15, 'low': 0.12, 'overflow': 0.0},
    }),
    ('D_mid_only_12', {
        'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.12, 'low': 0.15, 'overflow': 0.0},
    }),
]


def main():
    print(f"Phase 4 — {len(CANDIDATES)} candidates × {len(WINDOWS)} windows × N={N_ITER}")
    for name, cfg in CANDIDATES:
        print(f"  {name}: {cfg}")
    print()

    out_path = os.path.join(ROOT, 'experiments', 'portfolio_v32', 'phase4_intermediate_results.json')
    results = {name: {} for name, _ in CANDIDATES}

    t0 = time.time()
    for cand_idx, (name, cfg) in enumerate(CANDIDATES, 1):
        print(f"\n=== [{cand_idx}/{len(CANDIDATES)}] {name} ===", flush=True)
        with ProcessPoolExecutor(max_workers=4) as ex:
            futs = {}
            for label, d_start, d_end in WINDOWS:
                f = ex.submit(run_one_window, cfg, label, d_start, d_end, N_ITER)
                futs[f] = label
            for f in as_completed(futs):
                label = futs[f]
                try:
                    r = f.result()
                except Exception as e:
                    r = {'error': str(e)}
                elapsed = time.time() - t0
                if 'error' in r:
                    print(f"  {label:<8} ERROR: {r['error'][:60]} [{elapsed/60:.1f}m total]", flush=True)
                else:
                    print(f"  {label:<8} ret={r.get('mean_ret', 0):>+15,.1f}%  "
                          f"DD={r.get('worst_dd', 0):>5.1f}%  coll={r.get('p_coll', 0):.1f}%  "
                          f"[{elapsed/60:.1f}m total]", flush=True)
                results[name][label] = r
                with open(out_path, 'w') as fh:
                    json.dump({'n_iter': N_ITER, 'results': results}, fh, indent=2, default=str)

    # ── Ship gate ────────────────────────────────────────────────────────
    print(f"\n{'=' * 110}")
    print("SHIP GATE EVALUATION (P1-P6)")
    print(f"{'=' * 110}")
    base_runs = results.get('baseline', {})
    for name, _ in CANDIDATES:
        if name == 'baseline':
            continue
        runs = results[name]
        passes = []
        fails = []

        cand_5y = runs.get('5y', {}).get('mean_ret', -math.inf)
        base_5y = base_runs.get('5y', {}).get('mean_ret', math.inf)
        ratio_5y = (1+cand_5y/100) / max(1+base_5y/100, 1e-9)
        if cand_5y >= base_5y:
            passes.append(f"P3a 5y: ret×{ratio_5y:.2f}")
        else:
            fails.append(f"P3a 5y: ret×{ratio_5y:.2f}")

        cand_22 = runs.get('22-now', {}).get('mean_ret', -math.inf)
        base_22 = base_runs.get('22-now', {}).get('mean_ret', math.inf)
        ratio_22 = (1+cand_22/100) / max(1+base_22/100, 1e-9)
        if cand_22 >= base_22:
            passes.append(f"P3b 22-now: ret×{ratio_22:.2f}")
        else:
            fails.append(f"P3b 22-now: ret×{ratio_22:.2f}")

        for label, _, _ in WINDOWS:
            if label in ('5y', '22-now', 'dip'):
                continue
            cr = runs.get(label, {}).get('mean_ret')
            br = base_runs.get(label, {}).get('mean_ret')
            if cr is None or br is None:
                continue
            ratio = (1+cr/100) / max(1+br/100, 1e-9)
            if ratio < (1 - RET_REGRESSION_TOL):
                fails.append(f"P4 {label}: ret×{ratio:.2f}")

        for label, _, _ in WINDOWS:
            coll = runs.get(label, {}).get('p_coll', 0.0)
            if coll > 0.5:
                fails.append(f"P5 {label}: {coll:.1f}% coll")

        for label, _, _ in WINDOWS:
            cd = runs.get(label, {}).get('worst_dd')
            bd = base_runs.get(label, {}).get('worst_dd')
            if cd is None or bd is None:
                continue
            if cd > bd + DD_REL_TOL:
                fails.append(f"P6 {label}: DD {cd:.1f}% > base {bd:.1f}% + {DD_REL_TOL}pp")

        verdict = "PASS — SHIP" if not fails else "FAIL"
        print(f"\n{name}: {verdict}")
        for p in passes:
            print(f"  ✓ {p}")
        for f in fails:
            print(f"  ✗ {f}")

    # Per-window comparison
    print(f"\n{'=' * 110}")
    print("PER-WINDOW DD-Δ vs baseline (negative = SAFER)")
    print(f"{'=' * 110}")
    print(f"{'window':<8} | {'baseline':<14}", end='')
    for name, _ in CANDIDATES:
        if name == 'baseline': continue
        print(f" | {name:<14}", end='')
    print()
    for label, _, _ in WINDOWS:
        b = base_runs.get(label, {}).get('worst_dd', 0)
        print(f"{label:<8} | {b:>5.1f}%        ", end='')
        for name, _ in CANDIDATES:
            if name == 'baseline': continue
            cd = results[name].get(label, {}).get('worst_dd', 0)
            d = cd - b
            print(f" | {cd:>5.1f}% (Δ{d:>+5.1f})", end='')
        print()

    print(f"\nElapsed: {(time.time() - t0)/60:.1f} min")
    print(f"Saved -> {out_path}")


if __name__ == '__main__':
    main()
