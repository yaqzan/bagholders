"""Stage 2 Phase D — Ship gate validation: top PUT_TP candidates at N=500 × 8 windows.

Phase C identified PUT_TP ∈ [0.14, 0.16] as sweet spot (within MC noise of each other
at N=300). Phase D escalates to N=500 × all 8 canonical windows and applies the full
T1-T7 (Stage 3-grade) gate to confirm ship-readiness.

Stage 2 has its own gates (B1-B5), but since this is a portfolio-affecting change
(PUT_TP modifies the option-aligned barrier set used in MC engine), we ALSO check
T1-T7 to ensure no DD floor breach across windows.

Candidates:
  0.14, 0.16, 0.18, 0.20, 0.35 (baseline reference)

Per-candidate compute: 8 windows × ~55s/window = ~7 min @ N=500
Total: 5 × 7 min = ~35 min
"""
from __future__ import annotations
import os, sys, time, io, json, contextlib
from datetime import date

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('dip',    date(2025, 11, 1), date(2026, 4, 15)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
    ('5y',     date(2021, 4, 15), date(2026, 4, 15)),
]


def run_one_window(label, start, end, version, n_iter):
    import monte_carlo as mc
    t0 = time.time()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        wr = mc.run_window(label, start, end, version)
    r = wr.get('seeded', {})
    return dict(
        label=label,
        duration_s=time.time() - t0,
        mean_ret=float(r.get('mean_ret', 0)),
        med_ret=float(r.get('med_ret', 0)),
        worst_dd=float(r.get('worst_dd', 0)),
        mean_dd=float(r.get('mean_dd', 0)),
        p_collapse=float(r.get('p_coll', 0)),
        call_tp=float(r.get('call_tp', 0)),
        put_tp_rate=float(r.get('put_tp', 0)),
        call_trades=float(r.get('call_trades', 0)),
        put_trades=float(r.get('put_trades', 0)),
    )


def run_candidate(put_tp, version, n_iter=500):
    import monte_carlo as mc
    os.environ['PUT_TP_OV'] = str(put_tp)
    os.environ['N_ITER_OVERRIDE'] = str(n_iter)
    mc.PUT_TP = put_tp
    mc.N_ITER = n_iter
    mc.COLLISION_MODES = ['seeded']
    print(f'\n=== PUT_TP={put_tp} (N={n_iter}) ===', flush=True)
    rows = []
    for lbl, st, en in WINDOWS:
        r = run_one_window(lbl, st, en, version, n_iter)
        rows.append(r)
        print(f'  {lbl:>7s}  ret={r["mean_ret"]:+.2e}%  dd={r["worst_dd"]:5.1f}%  '
              f'col={r["p_collapse"]:4.1f}%  putTP={r["put_tp_rate"]:5.2f}%  '
              f'callTP={r["call_tp"]:5.2f}%  ({r["duration_s"]:.0f}s)', flush=True)
    return {row['label']: row for row in rows}


def gate_t1_t7(baseline_w, candidate_w):
    """Apply Stage 3 T1-T7 ship gates."""
    # T2: N=500 ✓ (set externally)
    # T3: all 8 windows present
    t3 = all(w in candidate_w for w in ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y'])

    # T4: 5y WorstDD ≤ baseline +1pp
    t4 = candidate_w['5y']['worst_dd'] - baseline_w['5y']['worst_dd'] <= 1.0
    t4_delta = candidate_w['5y']['worst_dd'] - baseline_w['5y']['worst_dd']

    # T5: no annual window regresses DD by >5pp
    annual = ['2021', '2022', '2023', '2024', '2025', 'dip']
    t5_violations = []
    for w in annual:
        delta = candidate_w[w]['worst_dd'] - baseline_w[w]['worst_dd']
        if delta > 5.0:
            t5_violations.append((w, delta))
    t5 = len(t5_violations) == 0

    # T6: P(collapse) = 0% on every cell
    t6_violations = [(w, candidate_w[w]['p_collapse']) for w in candidate_w
                     if candidate_w[w]['p_collapse'] > 0]
    t6 = len(t6_violations) == 0

    # T7: 5y compound order-of-magnitude within ±3 OOMs of baseline
    import math
    base_ret = baseline_w['5y']['mean_ret']
    cand_ret = candidate_w['5y']['mean_ret']
    if base_ret > 0 and cand_ret > 0:
        oom_delta = math.log10(cand_ret) - math.log10(base_ret)
    else:
        oom_delta = 0
    t7 = abs(oom_delta) <= 3.0

    return dict(
        t3=t3, t4=t4, t4_delta=t4_delta,
        t5=t5, t5_violations=t5_violations,
        t6=t6, t6_violations=t6_violations,
        t7=t7, oom_delta=oom_delta,
        all_pass=t3 and t4 and t5 and t6 and t7,
    )


def main():
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    print(f'Active version: id={av.id} commit={av.git_commit}')

    candidates = [0.35, 0.20, 0.18, 0.16, 0.14]  # baseline first
    N = int(os.environ.get('N_ITER', '500'))

    all_results = {}
    for tp in candidates:
        all_results[tp] = run_candidate(tp, av, n_iter=N)

    out_json = os.path.join(ROOT, 'experiments', 'sl_tax_stage2', 'phase_d_results.json')
    with open(out_json, 'w') as f:
        json.dump({str(k): v for k, v in all_results.items()}, f, indent=2)
    print(f'\n[saved] {out_json}')

    # T1-T7 gate evaluation vs baseline
    baseline = all_results[0.35]
    print('\n' + '=' * 100)
    print('STAGE 2/3 SHIP GATE EVALUATION (N=500 × 8 windows vs baseline PUT_TP=0.35)')
    print('=' * 100)
    print(f'{"PUT_TP":>7s}  {"5y_DD":>7s}  {"Δ_DD":>7s}  {"5y_ret":>10s}  {"OOM_Δ":>7s}  '
          f'{"T4":>3s}  {"T5":>3s}  {"T6":>3s}  {"T7":>3s}  PASS')
    for tp in [0.14, 0.16, 0.18, 0.20]:
        cw = all_results[tp]
        gate = gate_t1_t7(baseline, cw)
        cells = '  '.join(['Y' if g else 'N' for g in [gate['t4'], gate['t5'], gate['t6'], gate['t7']]])
        print(f'{tp:>7.3f}  {cw["5y"]["worst_dd"]:>6.2f}%  {gate["t4_delta"]:>+6.2f}pp  '
              f'{cw["5y"]["mean_ret"]:>+10.2e}%  {gate["oom_delta"]:>+7.2f}  '
              f'{cells}  {"YES" if gate["all_pass"] else "NO":>3s}')
        if gate['t5_violations']:
            print(f'           T5 viol: {gate["t5_violations"]}')

    # Per-window comparison
    print('\n=== Per-window WorstDD ===')
    print(f'{"window":>8s}  ' + '  '.join(f'{tp:>5.2f}' for tp in candidates))
    for w_lbl, _, _ in WINDOWS:
        row = [f'{w_lbl:>8s}']
        for tp in candidates:
            row.append(f'{all_results[tp][w_lbl]["worst_dd"]:>5.1f}')
        print('  '.join(row))


if __name__ == '__main__':
    main()
