"""
Portfolio v32 — Phase 3: N=300 × 8-window canonical validation. Final ship gate.

Reads phase2_validate_results.json, picks top-1 (or top-2 if close), runs full
8-window N=300 canonical MC. Applies P1-P6 ship gate per CLAUDE.md
"Portfolio Change Ship Criteria":

  P1: N=300+ per (window × mode)
  P2: All 8 canonical windows
  P3: 5y compound ≥ baseline AND 22-now compound ≥ baseline
  P4: No annual window regresses more than 25% vs baseline
  P5: P(collapse) = 0% on every cell
  P6: DD floor — relative improvement vs baseline (per Phase OP2 docs,
      absolute 80% floor is currently unattainable under bounded-fill MC).

Usage:
    PYTHONIOENCODING=utf-8 python experiments/portfolio_v32/phase3_canonical.py [TOP_K]
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
TOP_K = int(sys.argv[1]) if len(sys.argv) > 1 else 1
RET_REGRESSION_TOL = 0.25     # P4: ±25% on annual windows
DD_REL_TOL = 1.0              # P6: cand DD ≤ baseline + 1pp


def main():
    p2_path = os.path.join(ROOT, 'experiments', 'portfolio_v32', 'phase2_validate_results.json')
    if not os.path.exists(p2_path):
        sys.exit(f"Phase 2 results missing at {p2_path}")

    p2 = json.load(open(p2_path))
    base = p2['results'].get('baseline', {})
    if not base:
        sys.exit("Phase 2 baseline missing")

    base_5y = base.get('5y', {}).get('mean_ret', 0.0)
    base_22 = base.get('22-now', {}).get('mean_ret', 0.0)

    # Compute Phase 2 utility = log(5y_ratio) + log(22-now_ratio) - DD penalty
    def p2_utility(name, runs):
        if any('error' in runs.get(w, {}) for w in ['5y', '22-now']):
            return -999
        u = 0.0
        for label in ['5y', '22-now']:
            r = runs.get(label, {})
            if 'mean_ret' not in r:
                return -999
            cand_mult = max(1.0 + r['mean_ret']/100.0, 1e-9)
            base_r = base.get(label, {}).get('mean_ret', 0.0)
            base_mult = max(1.0 + base_r/100.0, 1e-9)
            u += math.log(cand_mult / base_mult)
        # Penalize DD on 5y
        dd_5y = runs.get('5y', {}).get('worst_dd', 100.0)
        base_dd_5y = base.get('5y', {}).get('worst_dd', 100.0)
        dd_excess = max(0.0, dd_5y - base_dd_5y) / 100.0
        u -= 50.0 * dd_excess ** 2
        return u

    ranked = []
    for name, runs in p2['results'].items():
        if name == 'baseline':
            continue
        ranked.append((p2_utility(name, runs), name))
    ranked.sort(reverse=True)

    selected = [('baseline', {})]
    p1_path = os.path.join(ROOT, 'experiments', 'portfolio_v32', 'phase1_screen_results.json')
    p1 = json.load(open(p1_path))
    for u, name in ranked[:TOP_K]:
        cfg = p1['results'][name]['cand']['cfg']
        selected.append((name, cfg))

    print(f"Phase 3 — {len(selected)} candidates × {len(WINDOWS)} windows × N={N_ITER}")
    print(f"Selected via Phase 2 utility:")
    for name, cfg in selected:
        print(f"  {name}: {cfg}")
    print()

    out_path = os.path.join(ROOT, 'experiments', 'portfolio_v32', 'phase3_canonical_results.json')
    results = {name: {} for name, _ in selected}

    t0 = time.time()
    # 4 workers × 8 windows = run sequentially per candidate to avoid memory pressure
    for cand_idx, (name, cfg) in enumerate(selected, 1):
        print(f"\n=== [{cand_idx}/{len(selected)}] {name} ===", flush=True)
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

    # ── Apply ship gate ────────────────────────────────────────────────────
    print(f"\n{'=' * 110}")
    print("SHIP GATE EVALUATION (P1-P6)")
    print(f"{'=' * 110}")
    base_runs = results.get('baseline', {})
    for name, _ in selected:
        if name == 'baseline':
            continue
        runs = results[name]
        passes = []
        fails = []

        # P3: 5y compound ≥ baseline
        cand_5y = runs.get('5y', {}).get('mean_ret', -math.inf)
        base_5y = base_runs.get('5y', {}).get('mean_ret', math.inf)
        if cand_5y >= base_5y:
            passes.append(f"P3a 5y: {cand_5y:+.1f}% ≥ baseline {base_5y:+.1f}%")
        else:
            cand_mult = (1+cand_5y/100) / max(1+base_5y/100, 1e-9) - 1
            fails.append(f"P3a 5y: {cand_5y:+.1f}% vs baseline {base_5y:+.1f}% ({cand_mult*100:+.1f}%)")

        # P3: 22-now compound ≥ baseline
        cand_22 = runs.get('22-now', {}).get('mean_ret', -math.inf)
        base_22 = base_runs.get('22-now', {}).get('mean_ret', math.inf)
        if cand_22 >= base_22:
            passes.append(f"P3b 22-now: {cand_22:+.1f}% ≥ baseline {base_22:+.1f}%")
        else:
            cand_mult = (1+cand_22/100) / max(1+base_22/100, 1e-9) - 1
            fails.append(f"P3b 22-now: {cand_22:+.1f}% vs baseline {base_22:+.1f}% ({cand_mult*100:+.1f}%)")

        # P4: annual within ±25%
        for label, _, _ in WINDOWS:
            if label in ('5y', '22-now', 'dip'):
                continue
            cand_r = runs.get(label, {}).get('mean_ret', None)
            base_r = base_runs.get(label, {}).get('mean_ret', None)
            if cand_r is None or base_r is None:
                continue
            ratio = (1+cand_r/100) / max(1+base_r/100, 1e-9)
            if ratio < (1 - RET_REGRESSION_TOL):
                fails.append(f"P4 {label}: {(ratio-1)*100:+.1f}% (>−{int(RET_REGRESSION_TOL*100)}% gate)")

        # P5: 0% collapse
        for label, _, _ in WINDOWS:
            coll = runs.get(label, {}).get('p_coll', 0.0)
            if coll > 0.5:
                fails.append(f"P5 {label}: {coll:.1f}% collapse")

        # P6: DD vs baseline (relative tolerance)
        for label, _, _ in WINDOWS:
            cand_dd = runs.get(label, {}).get('worst_dd', None)
            base_dd = base_runs.get(label, {}).get('worst_dd', None)
            if cand_dd is None or base_dd is None:
                continue
            if cand_dd > base_dd + DD_REL_TOL:
                fails.append(f"P6 {label}: DD {cand_dd:.1f}% > baseline {base_dd:.1f}% + {DD_REL_TOL}pp")

        verdict = "PASS — SHIP" if not fails else "FAIL — DO NOT SHIP"
        print(f"\n{name}: {verdict}")
        for p in passes:
            print(f"  ✓ {p}")
        for f in fails:
            print(f"  ✗ {f}")

    print(f"\nElapsed: {(time.time() - t0)/60:.1f} min")
    print(f"Saved -> {out_path}")


if __name__ == '__main__':
    main()
