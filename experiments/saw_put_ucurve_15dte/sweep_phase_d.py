"""SAW Put U-curve 15 DTE — Phase D final ship gate (N=500 x 8 windows).

Reads Phase C results, picks the highest-ranked T-gate-passing candidate,
and runs at N=500 vs baseline. Final ship-or-fail decision per Stage 3
T1-T7. Mirrors 30 DTE Phase E pattern.

Output:
  experiments/saw_put_ucurve_15dte/phase_d_results.jsonl
  experiments/saw_put_ucurve_15dte/phase_d.log

Usage:
  PYTHONIOENCODING=utf-8 python -u experiments/saw_put_ucurve_15dte/sweep_phase_d.py 2>&1 \\
    | tee experiments/saw_put_ucurve_15dte/phase_d.log
"""
from __future__ import annotations
import json, math, os, sys, time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo_15dte as mc  # noqa: E402
from database.models.core import AlgorithmVersion  # noqa: E402

# Reuse Phase C apply/run/eval functions
from experiments.saw_put_ucurve_15dte.sweep_phase_c import (  # noqa: E402
    SWEEP_WINDOWS, apply_cfg, run_candidate, evaluate_t_gates,
)

PHASE_C_RESULTS = os.path.join(ROOT, 'experiments', 'saw_put_ucurve_15dte',
                                'phase_c_results.jsonl')


def main():
    log_dir = os.path.join(ROOT, 'experiments', 'saw_put_ucurve_15dte')
    log_path = os.path.join(log_dir, 'phase_d_results.jsonl')

    N_ITER = int(os.environ.get('N_ITER', '500'))

    if not os.path.exists(PHASE_C_RESULTS):
        print(f"ERROR: Phase C results not found at {PHASE_C_RESULTS}")
        sys.exit(1)

    # Load Phase C; pick best T-gate-passing candidate by 5y DD reduction
    candidates = []
    base_res = None
    with open(PHASE_C_RESULTS, 'r') as f:
        for line in f:
            rec = json.loads(line)
            if rec['name'] == 'baseline':
                base_res = rec['res']
            else:
                candidates.append(rec)

    if base_res is None:
        print("ERROR: no baseline in phase_c_results.jsonl")
        sys.exit(1)

    # Filter T-pass candidates, then sort by 5y DD reduction (most negative Δ first).
    passing = [c for c in candidates if c['gates']['all_T_pass']]
    if not passing:
        # Fall back to all candidates ranked by 5y DD reduction; flag for user review
        print("WARNING: no Phase C candidate passed all T-gates. Ranking by Δ5y_dd anyway.")
        passing = candidates
    passing.sort(key=lambda r: r['gates']['dd_5y_delta_pp'])  # most negative first

    print(f"\nPhase D — Final ship gate at N={N_ITER} x {len(SWEEP_WINDOWS)} windows")
    print(f"Phase C candidates passing all T-gates: {len([c for c in candidates if c['gates']['all_T_pass']])}/{len(candidates)}")
    print(f"\nTop 3 candidates (sorted by Δ5y_dd):")
    for i, c in enumerate(passing[:3]):
        g = c['gates']
        print(f"  #{i+1}: {c['name']}  Δ5y_dd={g['dd_5y_delta_pp']:+.2f}pp  "
              f"Δ22n_dd={g['dd_22n_delta_pp']:+.2f}pp  T-all={g['all_T_pass']}")

    winner = passing[0]
    print(f"\nWinner selected: {winner['name']}")
    print(f"  params: {json.dumps(winner['params'])}")

    # Run baseline + winner at N=500
    if os.path.exists(log_path):
        os.remove(log_path)

    def log(rec):
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')

    version = AlgorithmVersion.get_active_scores_version()
    print(f"\nActive version: {version.git_commit if version else 'NONE'}")

    print(f"\n[baseline N=500] running...")
    t0 = time.time()
    base_cand = {'name': 'baseline', 'enabled': False}
    base_n500 = run_candidate(base_cand, version, N_ITER)
    print(f"  baseline elapsed: {time.time()-t0:.0f}s")
    print(f"  baseline 5y    : ret={base_n500['5y']['mean_ret']:+.2e}%  dd={base_n500['5y']['worst_dd']:.1f}%")
    print(f"  baseline 22-now: ret={base_n500['22-now']['mean_ret']:+.2e}%  dd={base_n500['22-now']['worst_dd']:.1f}%")
    log({'name': 'baseline', 'enabled': False, 'params': None,
         'res': base_n500, 'gates': None, 'elapsed_s': time.time()-t0})

    print(f"\n[winner N=500] {winner['name']}...")
    t0 = time.time()
    win_cand = {'name': winner['name'], 'enabled': True, 'params': winner['params']}
    win_n500 = run_candidate(win_cand, version, N_ITER)
    win_gates = evaluate_t_gates(win_n500, base_n500)
    elapsed = time.time() - t0
    log({'name': winner['name'], 'enabled': True, 'params': winner['params'],
         'res': win_n500, 'gates': win_gates, 'elapsed_s': elapsed})

    # Final report
    print("\n" + "=" * 110)
    print(f"PHASE D FINAL SHIP GATE — {winner['name']}")
    print("=" * 110)
    print(f"\n{'window':<10s}  {'baseline_ret':>14s}  {'cand_ret':>14s}  "
          f"{'Δret%':>8s}  {'base_dd':>8s}  {'cand_dd':>8s}  {'Δdd_pp':>7s}")
    for label, _, _ in SWEEP_WINDOWS:
        b = base_n500[label]
        c = win_n500[label]
        try:
            ret_pct = ((1 + c['mean_ret']/100.0) / (1 + b['mean_ret']/100.0) - 1) * 100
        except Exception:
            ret_pct = 0
        print(f"{label:<10s}  {b['mean_ret']:>+13.2e}%  {c['mean_ret']:>+13.2e}%  "
              f"{ret_pct:>+7.1f}%  {b['worst_dd']:>7.1f}%  {c['worst_dd']:>7.1f}%  "
              f"{c['worst_dd']-b['worst_dd']:>+6.1f}")

    print(f"\nT-Gate Results (Stage 3 framework):")
    print(f"  T4 (5y_dd ≤ baseline+1pp): {'PASS' if win_gates['T4_5y_dd_pass'] else 'FAIL'}  "
          f"(Δ5y_dd={win_gates['dd_5y_delta_pp']:+.2f}pp)")
    print(f"  T5 (no annual >5pp regr):  {'PASS' if win_gates['T5_annual_dd_pass'] else 'FAIL'}")
    print(f"  T6 (0% collapse):          {'PASS' if win_gates['T6_collapse_pass'] else 'FAIL'}")
    print(f"  T7 (5y compound ±3 OOM):   {'PASS' if win_gates['T7_compound_oom_pass'] else 'FAIL'}  "
          f"(OOM_Δ={win_gates['compound_oom_diff']:+.2f})")
    print(f"\n  ALL T-GATES:               {'PASS — SHIP CANDIDATE' if win_gates['all_T_pass'] else 'FAIL — investigate'}")
    print(f"\n  Δ5y_dd:    {win_gates['dd_5y_delta_pp']:+.2f}pp")
    print(f"  Δ22-now_dd: {win_gates['dd_22n_delta_pp']:+.2f}pp")


if __name__ == '__main__':
    main()
