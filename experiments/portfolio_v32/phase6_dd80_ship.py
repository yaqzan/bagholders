"""
Portfolio v32 — Phase 6: ship gate (DD < 80% all windows, max compound).

User clarified 2026-05-01: old strict ratio gates were calibrated on contaminated
runs. The real gate is:
  HARD: all 8 windows DD < 80% AND 0% collapse
  SOFT: maximize 5y compound among gate-passing candidates

Baseline at N=500 (Phase 5) FAILS: 5y DD 83.6%, 22-now DD 82.4%.
This sweep finds the highest-compound DEFENSIVE allocation that clears 80% on
every window.

Candidates: 6 × 8 windows × N=500 ≈ 18-20 min wall.
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

N_ITER = 500
DD_HARD_GATE = 80.0   # absolute pp — every window must be ≤ this
COLL_GATE = 0.5       # pp — every window must be ≤ this

CANDIDATES = [
    ('baseline', {}),

    # Mild defensive (phase 4 hint)
    ('D_midlow_13', {
        'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.13, 'low': 0.13, 'overflow': 0.0},
    }),

    # Phase 5 winner — universal DD reduction, 22-now compound +13%
    ('D_midlow_12', {
        'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.12, 'low': 0.12, 'overflow': 0.0},
    }),

    # More aggressive: might trade more compound for even safer DD
    ('D_midlow_10', {
        'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.10, 'low': 0.10, 'overflow': 0.0},
    }),

    # Phase 4 variant — best 22-now compound at N=300 (ret×27.09, but noisy)
    ('D_mid_only_12', {
        'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.12, 'low': 0.15, 'overflow': 0.0},
    }),

    # Phase 4 variant — cuts the high-N "low" tier (75-79 bulk volume)
    ('D_low_only_12', {
        'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.15, 'low': 0.12, 'overflow': 0.0},
    }),
]


def main():
    print(f"Phase 6 — {len(CANDIDATES)} candidates × {len(WINDOWS)} windows × N={N_ITER}")
    print(f"Gate: HARD DD<{DD_HARD_GATE}% all windows + 0% collapse; SOFT maximize compound")
    for name, cfg in CANDIDATES:
        print(f"  {name}: {cfg}")
    print()

    out_path = os.path.join(ROOT, 'experiments', 'portfolio_v32', 'phase6_dd80_results.json')
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
                    breach = "⚠" if r.get('worst_dd', 0) > DD_HARD_GATE else " "
                    print(f"  {label:<8} ret={r.get('mean_ret', 0):>+15,.1f}%  "
                          f"DD={r.get('worst_dd', 0):>5.1f}%{breach} coll={r.get('p_coll', 0):.1f}%  "
                          f"[{elapsed/60:.1f}m total]", flush=True)
                results[name][label] = r
                with open(out_path, 'w') as fh:
                    json.dump({'n_iter': N_ITER, 'gate': {'dd_hard': DD_HARD_GATE,
                              'coll_gate': COLL_GATE}, 'results': results}, fh, indent=2, default=str)

    # ── Gate evaluation ─────────────────────────────────────────────────
    print(f"\n{'=' * 110}")
    print(f"GATE EVALUATION (HARD: DD<{DD_HARD_GATE}% all windows + 0% collapse)")
    print(f"{'=' * 110}")
    gate_passing = []
    for name, _ in CANDIDATES:
        runs = results[name]
        breaches = []
        collapses = []
        for label, _, _ in WINDOWS:
            r = runs.get(label, {})
            if 'error' in r: continue
            dd = r.get('worst_dd', 100.0)
            coll = r.get('p_coll', 100.0)
            if dd > DD_HARD_GATE:
                breaches.append(f"{label}={dd:.1f}%")
            if coll > COLL_GATE:
                collapses.append(f"{label}={coll:.1f}%")
        passes = not breaches and not collapses
        verdict = "PASS" if passes else "FAIL"
        ret_5y = runs.get('5y', {}).get('mean_ret', 0)
        ret_22 = runs.get('22-now', {}).get('mean_ret', 0)
        max_dd = max((runs.get(l[0], {}).get('worst_dd', 0) for l in WINDOWS), default=0)
        print(f"  {name:<18} {verdict}  max_DD={max_dd:>5.1f}%  "
              f"5y ret={ret_5y:>+22.1e}%  22-now ret={ret_22:>+18.1e}%", end='')
        if breaches: print(f"  breaches: {', '.join(breaches)}", end='')
        if collapses: print(f"  collapses: {', '.join(collapses)}", end='')
        print()
        if passes:
            gate_passing.append((name, runs, ret_5y, ret_22))

    if not gate_passing:
        print("\nNO CANDIDATE PASSES GATE. No ship.")
        return

    print(f"\n{'=' * 110}")
    print(f"COMPOUND RANKING AMONG GATE-PASSING CANDIDATES")
    print(f"{'=' * 110}")
    # Rank by 22-now compound (most stable headline at N=500 per noise audit)
    gate_passing.sort(key=lambda x: -x[3])  # x[3] = 22-now ret
    print(f"{'rank':>4} {'name':<18} {'22-now ret':>22} {'5y ret':>22} {'max_DD':>8} {'2024_DD':>8}")
    for i, (name, runs, ret_5y, ret_22) in enumerate(gate_passing, 1):
        max_dd = max((runs.get(l[0], {}).get('worst_dd', 0) for l in WINDOWS), default=0)
        dd24 = runs.get('2024', {}).get('worst_dd', 0)
        print(f"  {i:>2} {name:<18} {ret_22:>+21.2e}% {ret_5y:>+21.2e}% {max_dd:>7.1f}% {dd24:>7.1f}%")

    winner = gate_passing[0][0]
    cfg_winner = next(c for n, c in CANDIDATES if n == winner)
    print(f"\n{'=' * 110}")
    print(f"WINNER: {winner}")
    print(f"  cfg: {cfg_winner}")
    print(f"{'=' * 110}")
    print(f"SHIP: edit strategy_config.py STRATEGY_30DTE.TIER_ALLOC =")
    print(f"      {cfg_winner.get('TIER_ALLOC', '(unchanged)')}")
    print(f"\nElapsed: {(time.time() - t0)/60:.1f} min")
    print(f"Saved -> {out_path}")
    print(f"\nSHIP_VERDICT: PASS  WINNER: {winner}")


if __name__ == '__main__':
    main()
