"""
Portfolio v32 — Phase 7: monotonic cascade vs current asymmetric.

Current shipped TIER_ALLOC has top=0.12, mid=0.15 — non-monotonic by score.
Phase H4 (2026-04-28, original Bayesian sweep that found this) flagged the
asymmetry with "(asymmetric!)" in v27-optimization-log.md, and that was at
N=100 single-window — well within the noise floor we documented this session
(1.6-1.8× compound variance, ±5pp DD).

Per-trade WR IS roughly monotonic by score (v31, 5y discrete buckets):
  95+: 69.2% | 90-94: 66.4% | 85-89: 67.2% | 80-84: 60.9% | 75-79: 58.3%

This sweep tests whether the asymmetric H4 outcome was a noise artifact at N=100.
At N=500 × 8 windows, does a monotonic gradient match per-trade quality?

Gate: DD<80% all windows + 0% collapse (HARD). Maximize compound (SOFT).
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
DD_HARD_GATE = 80.0

CANDIDATES = [
    # Current shipped (H4 asymmetric + v32 low-cut)
    ('current_asym',  {}),

    # Top↔mid swap: simplest monotonic variant
    ('mono_A_swap',   {'TIER_ALLOC': {
        'ultra': 0.18, 'top': 0.15, 'mid': 0.12, 'low': 0.12, 'overflow': 0.0}}),

    # Steeper gradient — bigger ultra, monotonic descent
    ('mono_B_steep',  {'TIER_ALLOC': {
        'ultra': 0.20, 'top': 0.15, 'mid': 0.12, 'low': 0.10, 'overflow': 0.0}}),

    # Smooth gradient — small step between adjacent tiers
    ('mono_C_smooth', {'TIER_ALLOC': {
        'ultra': 0.18, 'top': 0.14, 'mid': 0.13, 'low': 0.12, 'overflow': 0.0}}),

    # Concentrated — heavier ultra, lighter mid+low
    ('concentrated',  {'TIER_ALLOC': {
        'ultra': 0.22, 'top': 0.13, 'mid': 0.10, 'low': 0.10, 'overflow': 0.0}}),
]


def main():
    print(f"Phase 7 — {len(CANDIDATES)} candidates × {len(WINDOWS)} windows × N={N_ITER}")
    print(f"Gate: HARD DD<{DD_HARD_GATE}% all windows + 0% collapse; SOFT maximize compound")
    for name, cfg in CANDIDATES:
        ta = cfg.get('TIER_ALLOC', {'ultra': 0.18, 'top': 0.12, 'mid': 0.15, 'low': 0.12, 'overflow': 0.0})
        print(f"  {name:<14} ultra={ta['ultra']:.2f} top={ta['top']:.2f} "
              f"mid={ta['mid']:.2f} low={ta['low']:.2f}")
    print()

    out_path = os.path.join(ROOT, 'experiments', 'portfolio_v32', 'phase7_monotonic_results.json')
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
                    json.dump({'n_iter': N_ITER, 'gate': {'dd_hard': DD_HARD_GATE},
                              'results': results}, fh, indent=2, default=str)

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
            if coll > 0.5:
                collapses.append(f"{label}={coll:.1f}%")
        passes = not breaches and not collapses
        verdict = "PASS" if passes else "FAIL"
        ret_5y = runs.get('5y', {}).get('mean_ret', 0)
        ret_22 = runs.get('22-now', {}).get('mean_ret', 0)
        max_dd = max((runs.get(l[0], {}).get('worst_dd', 0) for l in WINDOWS), default=0)
        print(f"  {name:<14} {verdict}  max_DD={max_dd:>5.1f}%  "
              f"5y ret={ret_5y:>+22.1e}%  22-now ret={ret_22:>+18.1e}%", end='')
        if breaches: print(f"  breaches: {', '.join(breaches)}", end='')
        if collapses: print(f"  collapses: {', '.join(collapses)}", end='')
        print()
        if passes:
            gate_passing.append((name, runs, ret_5y, ret_22))

    if not gate_passing:
        print("\nNO CANDIDATE PASSES GATE.")
        return

    # Rank gate-passing by composite score: avg(log_ret_22, log_ret_5y) - 50*max(0, dd-0.65)²
    def composite(name, runs, ret_5y, ret_22):
        logs = []
        for r in [ret_5y, ret_22]:
            logs.append(math.log(max(1.0 + r/100, 1e-9)))
        max_dd = max((runs.get(l[0], {}).get('worst_dd', 0) for l in WINDOWS), default=0)
        dd_excess = max(0.0, max_dd/100 - 0.65)
        return sum(logs)/len(logs) - 50.0 * dd_excess**2

    ranked = sorted(gate_passing, key=lambda x: -composite(*x))
    print(f"\n{'=' * 110}")
    print("RANKED AMONG GATE-PASSING (composite = avg(log 5y, log 22-now) − DD penalty)")
    print(f"{'=' * 110}")
    print(f"{'rank':>4} {'name':<14} {'composite':>10} {'22-now ret':>22} {'5y ret':>22} {'max_DD':>8}")
    for i, entry in enumerate(ranked, 1):
        name, runs, ret_5y, ret_22 = entry
        c = composite(*entry)
        max_dd = max((runs.get(l[0], {}).get('worst_dd', 0) for l in WINDOWS), default=0)
        print(f"  {i:>2} {name:<14} {c:>10.2f} {ret_22:>+21.2e}% {ret_5y:>+21.2e}% {max_dd:>7.1f}%")

    winner = ranked[0][0]
    print(f"\n{'=' * 110}")
    if winner == 'current_asym':
        print(f"WINNER: current_asym (no change to ship — H4 asymmetric is empirically optimal at N=500)")
    else:
        cfg_winner = next(c for n, c in CANDIDATES if n == winner)
        print(f"WINNER: {winner} (RESHIP — monotonic beats current asym)")
        print(f"  cfg: {cfg_winner.get('TIER_ALLOC', {})}")
    print(f"{'=' * 110}")

    # Per-window comparison
    print()
    print(f"{'window':<8} | " + " | ".join(f"{n:<22}" for n, _ in CANDIDATES))
    for label, _, _ in WINDOWS:
        cells = []
        for name, _ in CANDIDATES:
            r = results[name].get(label, {})
            if 'error' in r:
                cells.append("ERR".ljust(22))
            else:
                cells.append(f"r={r.get('mean_ret', 0):>+10.0f}% DD={r.get('worst_dd', 0):.1f}%".ljust(22))
        print(f"{label:<8} | " + " | ".join(cells))

    print(f"\nElapsed: {(time.time() - t0)/60:.1f} min")
    print(f"Saved -> {out_path}")
    print(f"\nSHIP_VERDICT: {'PASS' if winner != 'current_asym' else 'KEEP_CURRENT'}  WINNER: {winner}")


if __name__ == '__main__':
    main()
