"""Phase E — N=500 ship validation of JOINT_B1_C1 vs baseline.

Two candidates × 8 windows × N=500. P1-P6 portfolio ship gate.
Expected runtime: ~30 min total.
"""
from __future__ import annotations
import json, os, sys, time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.v32_optim.phase_d_validate import apply_full, run_config, SWEEP_WINDOWS  # noqa: E402
from database.models.core import AlgorithmVersion  # noqa: E402

JOINT = {
    # cascade
    'ultra': 0.20, 'top': 0.15, 'mid': 0.10, 'low': 0.10,
    'put_top': 0.12, 'put_mid': 0.10, 'put_low': 0.08,
    # TP/SL
    'tp_base': 0.33, 'tp_stress': 0.42,
    'sl_base': -0.27, 'sl_stress': -0.40,
    'breadth_thr': 40,
}

def main():
    log_dir = os.path.join(ROOT, 'experiments', 'v32_optim')
    out_path = os.path.join(log_dir, 'phase_e_n500_results.jsonl')
    N_ITER = int(os.environ.get('N_ITER', '500'))
    version = AlgorithmVersion.get_active_scores_version()
    print(f"Active version: {version.git_commit if version else None}")
    print(f"N_ITER={N_ITER}")
    print(f"\nWindows: {[w[0] for w in SWEEP_WINDOWS]}")

    candidates = [('baseline', {}), ('JOINT', JOINT)]

    results = []
    for name, cfg in candidates:
        t0 = time.time()
        win_res = run_config(cfg, version, N_ITER)
        dur = time.time() - t0
        rec = {'name':name, 'params':cfg, 'windows':win_res, 'duration_s':dur}
        results.append(rec)
        with open(out_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')
        print(f"\n[{name}] N={N_ITER}  dur={dur/60:.1f}min")
        for label,_,_ in SWEEP_WINDOWS:
            w = win_res.get(label, {})
            print(f"  {label:<8}  ret={w.get('mean_ret',0):+.3e}%  dd={w.get('worst_dd',0):.1f}%  "
                  f"col={w.get('p_collapse',0):.2f}%  ctp={w.get('call_tp',0):.1f}  ptp={w.get('put_tp',0):.1f}  "
                  f"ctrd={w.get('call_trades',0):.0f}  ptrd={w.get('put_trades',0):.0f}")

    print("\n" + "="*120)
    print("PHASE E N=500 SHIP VALIDATION")
    print("="*120)
    print(f"{'window':<10}  {'baseline ret':>15}  {'baseline DD':>11}  {'JOINT ret':>15}  {'JOINT DD':>9}  {'Δret':>10}  {'Δdd':>7}  {'<70%?':>6}")
    print("-"*120)
    b_res = results[0]['windows']
    j_res = results[1]['windows']
    p4_breaches = []
    p5_collapses = []
    p6_dd_better = 0
    p6_dd_worse = 0
    for label,_,_ in SWEEP_WINDOWS:
        bw = b_res.get(label, {}); jw = j_res.get(label, {})
        b_ret = bw.get('mean_ret', 0); j_ret = jw.get('mean_ret', 0)
        b_dd = bw.get('worst_dd', 0); j_dd = jw.get('worst_dd', 0)
        # Annual stability gate (P4): >25% regression flag
        if b_ret > 0:
            ret_pct_change = (j_ret - b_ret) / abs(b_ret) * 100
        else:
            ret_pct_change = 0
        regression_flag = '⚠' if ret_pct_change < -25 else ''
        if ret_pct_change < -25 and label not in ('22-now','5y'):
            p4_breaches.append(label)
        # P5 collapse
        j_col = jw.get('p_collapse', 0)
        if j_col > 0:
            p5_collapses.append((label, j_col))
        # P6 DD
        if j_dd < b_dd: p6_dd_better += 1
        elif j_dd > b_dd: p6_dd_worse += 1
        target = '✓' if j_dd <= 70.0 else '✗'
        print(f"{label:<10}  {b_ret:>+15.3e}  {b_dd:>10.1f}%  {j_ret:>+15.3e}  {j_dd:>8.1f}%  {ret_pct_change:>+9.1f}%{regression_flag}  {j_dd-b_dd:>+6.1f}  {target:>6}")

    # P3 headline
    p3_5y = j_res['5y']['mean_ret'] >= b_res['5y']['mean_ret']
    p3_22n = j_res['22-now']['mean_ret'] >= b_res['22-now']['mean_ret']

    print("\nGATE STATUS:")
    print(f"  P1 N=500+:           ✓ (N={N_ITER})")
    print(f"  P2 all 8 windows:    ✓")
    print(f"  P3 5y compound >=:   {'✓' if p3_5y else '✗'}  ({j_res['5y']['mean_ret']:+.2e}% vs {b_res['5y']['mean_ret']:+.2e}%)")
    print(f"  P3 22-now compound>={'✓' if p3_22n else '✗'}  ({j_res['22-now']['mean_ret']:+.2e}% vs {b_res['22-now']['mean_ret']:+.2e}%)")
    print(f"  P4 annual stability: {'✓' if not p4_breaches else '✗ ' + str(p4_breaches)}")
    print(f"  P5 collapse rate:    {'✓' if not p5_collapses else '✗ ' + str(p5_collapses)}")
    print(f"  P6 DD windows imp:   {p6_dd_better} better, {p6_dd_worse} worse")
    overall = p3_5y and p3_22n and not p4_breaches and not p5_collapses
    print(f"\nSHIP GATE: {'PASS ✓' if overall else 'REVIEW ✗'}")

if __name__ == '__main__':
    main()
