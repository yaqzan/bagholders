"""Stage 2 Phase C — Fine-grid PUT_TP refinement around 0.20.

Phase B winner: PUT_TP=0.20 strictly Pareto-improves baseline 0.35:
  Worst DD -7.43pp, put_tp +10.18pp, compound x1450, calls untouched.

Phase C tests neighbors {0.14, 0.16, 0.18, 0.20, 0.22, 0.24, 0.26} at
N=300 22-now MC to find the local optimum. Each variant ~55s.
"""
from __future__ import annotations
import os, sys, time, io, json, contextlib
from datetime import date

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def run_one(put_tp_value: float, version, n_iter=300):
    import monte_carlo as mc
    os.environ['PUT_TP_OV'] = str(put_tp_value)
    os.environ['N_ITER_OVERRIDE'] = str(n_iter)
    mc.PUT_TP = put_tp_value
    mc.N_ITER = n_iter
    mc.COLLISION_MODES = ['seeded']
    t0 = time.time()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        wr = mc.run_window('22-now', date(2022, 1, 1), date(2026, 4, 15), version)
    r = wr.get('seeded', {})
    return dict(
        put_tp=put_tp_value,
        duration_s=time.time() - t0,
        mean_ret=float(r.get('mean_ret', 0)),
        worst_dd=float(r.get('worst_dd', 0)),
        p_collapse=float(r.get('p_coll', 0)),
        call_tp=float(r.get('call_tp', 0)),
        put_tp_rate=float(r.get('put_tp', 0)),
        call_trades=float(r.get('call_trades', 0)),
        put_trades=float(r.get('put_trades', 0)),
    )


def main():
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    print(f'Active version: id={av.id} commit={av.git_commit}')

    variants = [0.14, 0.16, 0.18, 0.20, 0.22, 0.24, 0.26, 0.30, 0.35]
    print(f'\n=== Stage 2 Phase C: fine-grid PUT_TP at N=300 22-now ===')
    print(f'{"PUT_TP":>7s}  {"WorstDD":>8s}  {"Coll%":>6s}  {"PutTP%":>7s}  '
          f'{"CallTP%":>7s}  {"22n_ret":>10s}  {"dur":>4s}')
    print('-' * 70)

    results = []
    for tp in variants:
        r = run_one(tp, av)
        results.append(r)
        print(f'{tp:>7.3f}  {r["worst_dd"]:>7.2f}%  {r["p_collapse"]:>5.2f}%  '
              f'{r["put_tp_rate"]:>6.2f}%  {r["call_tp"]:>6.2f}%  '
              f'{r["mean_ret"]:>+10.2e}%  {r["duration_s"]:>3.0f}s', flush=True)

    # Save raw
    out_json = os.path.join(ROOT, 'experiments', 'sl_tax_stage2', 'phase_c_results.json')
    with open(out_json, 'w') as f:
        json.dump(results, f, indent=2)
    print(f'\n[saved] {out_json}')

    # Find DD-best and compound-best
    base = next((r for r in results if r['put_tp'] == 0.35), None)
    if base:
        print('\n=== Vs baseline (PUT_TP=0.35) ===')
        print(f'{"PUT_TP":>7s}  {"Δ_DD":>7s}  {"Δ_PutTP":>9s}  {"compound_ratio":>14s}')
        for r in results:
            if r['put_tp'] == 0.35:
                continue
            dd_delta = r['worst_dd'] - base['worst_dd']
            putp_delta = r['put_tp_rate'] - base['put_tp_rate']
            comp_ratio = (r['mean_ret'] / base['mean_ret']) if base['mean_ret'] > 0 else 0
            print(f'{r["put_tp"]:>7.3f}  {dd_delta:>+7.2f}pp  {putp_delta:>+8.2f}pp  '
                  f'{comp_ratio:>14.2e}')


if __name__ == '__main__':
    main()
