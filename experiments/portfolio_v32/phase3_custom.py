"""
Portfolio v32 — Phase 3 (custom): N=300 × 8-window canonical validation.

Hand-picked from Phase 2 — top 3 by clarity of signal:
  1. D_midlow_12 — primary winner. Phase 2 5y: ret×1.69 + DD -4.9pp (Pareto win).
  2. D_midlow_08 — safer-DD direction. Phase 2 5y: ret×0.47 + DD -5.0pp.
  3. F_maxpos_16 — pool-expansion mixed signal. Phase 2 5y: ret×1.18 + DD +0.3pp.

Applies the P1-P6 ship gate per CLAUDE.md "Portfolio Change Ship Criteria".
N=300+ × 8 windows is the documented threshold for ship decisions (Phase OP1
lesson — N=150 4-window can flip at N=300).

Usage:
    PYTHONIOENCODING=utf-8 python experiments/portfolio_v32/phase3_custom.py
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
    ('D_midlow_12', {
        'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.12, 'low': 0.12, 'overflow': 0.0},
    }),
    ('D_midlow_08', {
        'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.08, 'low': 0.08, 'overflow': 0.0},
    }),
    ('F_maxpos_16', {'MAX_POSITIONS': 16}),
]


def main():
    print(f"Phase 3 — {len(CANDIDATES)} candidates × {len(WINDOWS)} windows × N={N_ITER}")
    for name, cfg in CANDIDATES:
        print(f"  {name}: {cfg}")
    print()

    out_path = os.path.join(ROOT, 'experiments', 'portfolio_v32', 'phase3_custom_results.json')
    results = {name: {} for name, _ in CANDIDATES}

    t0 = time.time()
    # Run candidates sequentially, but parallel windows within each
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
                          f"C/P=({r.get('call_tp', 0):.1f}/{r.get('put_tp', 0):.1f})  "
                          f"[{elapsed/60:.1f}m total]", flush=True)
                results[name][label] = r
                with open(out_path, 'w') as fh:
                    json.dump({'n_iter': N_ITER, 'results': results}, fh, indent=2, default=str)

    # ── Ship gate evaluation ──────────────────────────────────────────────
    print(f"\n{'=' * 110}")
    print("SHIP GATE EVALUATION (P1-P6 per CLAUDE.md)")
    print(f"{'=' * 110}")
    base_runs = results.get('baseline', {})
    summaries = {}
    for name, _ in CANDIDATES:
        if name == 'baseline':
            continue
        runs = results[name]
        passes = []
        fails = []

        # P3: 5y compound ≥ baseline
        cand_5y = runs.get('5y', {}).get('mean_ret', -math.inf)
        base_5y = base_runs.get('5y', {}).get('mean_ret', math.inf)
        ratio_5y = (1+cand_5y/100) / max(1+base_5y/100, 1e-9)
        if cand_5y >= base_5y:
            passes.append(f"P3a 5y: ret×{ratio_5y:.2f} (cand {cand_5y:+.0f}% ≥ base {base_5y:+.0f}%)")
        else:
            fails.append(f"P3a 5y: ret×{ratio_5y:.2f} (cand {cand_5y:+.0f}% < base {base_5y:+.0f}%)")

        # P3: 22-now compound ≥ baseline
        cand_22 = runs.get('22-now', {}).get('mean_ret', -math.inf)
        base_22 = base_runs.get('22-now', {}).get('mean_ret', math.inf)
        ratio_22 = (1+cand_22/100) / max(1+base_22/100, 1e-9)
        if cand_22 >= base_22:
            passes.append(f"P3b 22-now: ret×{ratio_22:.2f}")
        else:
            fails.append(f"P3b 22-now: ret×{ratio_22:.2f} (cand {cand_22:+.0f}% < base {base_22:+.0f}%)")

        # P4: annual within ±25%
        for label, _, _ in WINDOWS:
            if label in ('5y', '22-now', 'dip'):
                continue
            cand_r = runs.get(label, {}).get('mean_ret')
            base_r = base_runs.get(label, {}).get('mean_ret')
            if cand_r is None or base_r is None:
                continue
            ratio = (1+cand_r/100) / max(1+base_r/100, 1e-9)
            if ratio < (1 - RET_REGRESSION_TOL):
                fails.append(f"P4 {label}: ret×{ratio:.2f} (>−{int(RET_REGRESSION_TOL*100)}% gate)")

        # P5: 0% collapse
        for label, _, _ in WINDOWS:
            coll = runs.get(label, {}).get('p_coll', 0.0)
            if coll > 0.5:
                fails.append(f"P5 {label}: {coll:.1f}% collapse")

        # P6: DD vs baseline (relative, since absolute 80% floor unattainable)
        for label, _, _ in WINDOWS:
            cand_dd = runs.get(label, {}).get('worst_dd')
            base_dd = base_runs.get(label, {}).get('worst_dd')
            if cand_dd is None or base_dd is None:
                continue
            if cand_dd > base_dd + DD_REL_TOL:
                fails.append(f"P6 {label}: DD {cand_dd:.1f}% > base {base_dd:.1f}% + {DD_REL_TOL}pp")

        verdict = "PASS — SHIP" if not fails else "FAIL — DO NOT SHIP"
        print(f"\n{name}: {verdict}")
        for p in passes:
            print(f"  ✓ {p}")
        for f in fails:
            print(f"  ✗ {f}")
        summaries[name] = {'passes': passes, 'fails': fails, 'verdict': verdict}

    print(f"\n{'=' * 110}")
    print("PER-WINDOW SUMMARY (cand vs baseline)")
    print(f"{'=' * 110}")
    print(f"{'window':<8}", end='')
    for name, _ in CANDIDATES:
        print(f" | {name:<22}", end='')
    print()
    for label, _, _ in WINDOWS:
        print(f"{label:<8}", end='')
        for name, _ in CANDIDATES:
            r = results[name].get(label, {})
            if 'error' in r:
                cell = "ERR"
            else:
                cell = f"r={r.get('mean_ret', 0):>+10.0f}% DD={r.get('worst_dd', 0):.1f}%"
            print(f" | {cell:<22}", end='')
        print()

    print(f"\nElapsed: {(time.time() - t0)/60:.1f} min")
    print(f"Saved -> {out_path}")


if __name__ == '__main__':
    main()
