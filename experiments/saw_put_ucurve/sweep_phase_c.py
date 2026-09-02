"""SAW Put U-curve Phase C — N=300 × 8-window validation of Phase B top candidates.

Tests: baseline (SAW off) + iter 18 + iter 12 + iter 23.
Gates: P1-P6 portfolio ship criteria (assessment-backtest.md).

Output:
  experiments/saw_put_ucurve/phase_c_results.jsonl
  experiments/saw_put_ucurve/phase_c.log
"""
from __future__ import annotations
import json, math, os, sys, time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc  # noqa: E402
from database.models.core import AlgorithmVersion  # noqa: E402

# ---------- 8-window canonical ----------
SWEEP_WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('dip',    date(2025, 11, 1), date(2026, 4, 15)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
    ('5y',     date(2021, 4, 15), date(2026, 4, 15)),
]

CANDIDATES = [
    {'name': 'baseline', 'enabled': False},
    {'name': 'iter18_RegionB', 'enabled': True, 'shape': 'quadratic',
     'mid': 72, 'hw': 18, 'floor': 0.55, 'ceil': 1.35, 'pk': 3.0},
    {'name': 'iter12_RegionA77', 'enabled': True, 'shape': 'quadratic',
     'mid': 77, 'hw': 18, 'floor': 0.45, 'ceil': 1.35, 'pk': 3.0},
    {'name': 'iter23_RegionA80', 'enabled': True, 'shape': 'quadratic',
     'mid': 80, 'hw': 18, 'floor': 0.45, 'ceil': 1.35, 'pk': 3.0},
]


def apply_cfg(cand, n_iter):
    if not cand['enabled']:
        os.environ['SAW_PUT_UCURVE_ENABLED'] = '0'
        mc.SAW_PUT_UCURVE_ENABLED = 0
    else:
        os.environ['SAW_PUT_UCURVE_ENABLED']  = '1'
        os.environ['SAW_PUT_UCURVE_SHAPE']    = cand['shape']
        os.environ['SAW_PUT_UCURVE_MIDPOINT'] = str(cand['mid'])
        os.environ['SAW_PUT_UCURVE_HALFWIDTH']= str(cand['hw'])
        os.environ['SAW_PUT_UCURVE_FLOOR']    = str(cand['floor'])
        os.environ['SAW_PUT_UCURVE_CEIL']     = str(cand['ceil'])
        os.environ['SAW_PUT_UCURVE_POWER']    = str(cand['pk'])
        os.environ['SAW_PUT_UCURVE_K']        = '5.0'
        mc.SAW_PUT_UCURVE_ENABLED   = 1
        mc.SAW_PUT_UCURVE_SHAPE     = cand['shape']
        mc.SAW_PUT_UCURVE_MIDPOINT  = float(cand['mid'])
        mc.SAW_PUT_UCURVE_HALFWIDTH = float(cand['hw'])
        mc.SAW_PUT_UCURVE_FLOOR     = float(cand['floor'])
        mc.SAW_PUT_UCURVE_CEIL      = float(cand['ceil'])
        mc.SAW_PUT_UCURVE_POWER     = float(cand['pk'])
    os.environ['N_ITER_OVERRIDE'] = str(n_iter)
    mc.N_ITER = n_iter
    mc.COLLISION_MODES = ['seeded']


def run_candidate(cand, version, n_iter):
    apply_cfg(cand, n_iter)
    out = {}
    for label, d_start, d_end in SWEEP_WINDOWS:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wr = mc.run_window(label, d_start, d_end, version)
        r = wr.get('seeded', {})
        out[label] = {
            'mean_ret':    float(r.get('mean_ret', 0.0)),
            'med_ret':     float(r.get('med_ret', 0.0)),
            'worst_dd':    float(r.get('worst_dd', 0.0)),
            'mean_dd':     float(r.get('mean_dd', 0.0)),
            'p_collapse':  float(r.get('p_coll', 0.0)),
            'call_tp':     float(r.get('call_tp', 0.0)),
            'put_tp':      float(r.get('put_tp', 0.0)),
            'call_trades': float(r.get('call_trades', 0.0)),
            'put_trades':  float(r.get('put_trades', 0.0)),
        }
    return out


def main():
    log_dir = os.path.join(ROOT, 'experiments', 'saw_put_ucurve')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'phase_c_results.jsonl')

    N_ITER = int(os.environ.get('N_ITER', '300'))
    print(f"SAW Put U-curve Phase C — N={N_ITER}, candidates={len(CANDIDATES)}, "
          f"windows={len(SWEEP_WINDOWS)}")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"Active version: {version.git_commit if version else 'NONE'}")

    if os.path.exists(log_path):
        os.remove(log_path)

    results = []
    t0 = time.time()
    for cand in CANDIDATES:
        ti = time.time()
        print(f"\n[{cand['name']}] running {len(SWEEP_WINDOWS)} windows...")
        try:
            res = run_candidate(cand, version, N_ITER)
        except Exception as e:
            print(f"  EXCEPTION {e}")
            continue
        rec = {'name': cand['name'], 'cand': cand, 'windows': res,
               'elapsed_s': time.time() - ti}
        results.append(rec)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')

        # Per-candidate summary
        max_dd = max(r['worst_dd'] for r in res.values())
        max_coll = max(r['p_collapse'] for r in res.values())
        five_y = res.get('5y', {}).get('mean_ret', 0)
        twonow = res.get('22-now', {}).get('mean_ret', 0)
        print(f"  done in {time.time()-ti:.0f}s. max_dd={max_dd:.1f}%  "
              f"max_collapse={max_coll:.1f}%  5y={five_y:+.2e}%  22n={twonow:+.2e}%")

    print(f"\n[done] all candidates in {time.time()-t0:.0f}s ({(time.time()-t0)/60:.1f}min)")

    # Final comparison table
    print("\n" + "=" * 110)
    print("Phase C — comparison vs baseline (P1-P6 portfolio gate)")
    print("=" * 110)

    if not results:
        print("No results.")
        return

    base_res = next((r for r in results if r['name'] == 'baseline'), None)
    base_5y = base_res['windows'].get('5y', {}).get('mean_ret', 0) if base_res else 0
    base_22n = base_res['windows'].get('22-now', {}).get('mean_ret', 0) if base_res else 0
    base_dd_5y = base_res['windows'].get('5y', {}).get('worst_dd', 0) if base_res else 0
    base_dd_global = max(r['worst_dd'] for r in base_res['windows'].values()) if base_res else 0

    print(f"\n{'name':<20s}  {'5y_ret':>15s}  {'Δ_5y':>9s}  {'22n_ret':>15s}  "
          f"{'Δ_22n':>9s}  {'5y_dd':>6s}  {'global_dd':>9s}  {'collapse':>8s}")
    print("-" * 110)
    for rec in results:
        name = rec['name']
        res = rec['windows']
        five_y = res.get('5y', {}).get('mean_ret', 0)
        twonow = res.get('22-now', {}).get('mean_ret', 0)
        dd_5y = res.get('5y', {}).get('worst_dd', 0)
        dd_global = max(r['worst_dd'] for r in res.values())
        max_coll = max(r['p_collapse'] for r in res.values())

        if base_5y > 0:
            d5y = ((five_y - base_5y) / base_5y) * 100
            d22n = ((twonow - base_22n) / base_22n) * 100 if base_22n > 0 else 0
            d5y_s = f'{d5y:+8.1f}%'
            d22n_s = f'{d22n:+8.1f}%'
        else:
            d5y_s = '   --   '
            d22n_s = '   --   '

        print(f"{name:<20s}  {five_y:>+15.2e}  {d5y_s:>9s}  {twonow:>+15.2e}  "
              f"{d22n_s:>9s}  {dd_5y:>5.1f}%  {dd_global:>8.1f}%  {max_coll:>7.1f}%")

    print("\nP1-P6 gate (per assessment-backtest.md):")
    print(f"  P1: N={N_ITER} ≥ 300+ {'✓' if N_ITER >= 300 else '✗'}")
    print(f"  P2: 8 windows ✓ ({len(SWEEP_WINDOWS)})")
    print(f"  P3: 5y ≥ baseline AND 22-now ≥ baseline")
    print(f"  P4: No annual window regresses >25% vs baseline")
    print(f"  P5: P(collapse) = 0% on every cell")
    print(f"  P6: DD improvement vs baseline (or within +1pp)")

    # Per-candidate P3-P6 evaluation
    print("\nPer-candidate gate evaluation:")
    for rec in results:
        if rec['name'] == 'baseline':
            continue
        name = rec['name']
        res = rec['windows']
        five_y = res.get('5y', {}).get('mean_ret', 0)
        twonow = res.get('22-now', {}).get('mean_ret', 0)
        dd_global = max(r['worst_dd'] for r in res.values())
        max_coll = max(r['p_collapse'] for r in res.values())

        p3 = '✓' if five_y >= base_5y and twonow >= base_22n else '✗'
        p4_pass = True
        annual_regs = []
        for label in ['2021', '2022', '2023', '2024', '2025', 'dip']:
            base_w = base_res['windows'].get(label, {}).get('mean_ret', 0)
            cand_w = res.get(label, {}).get('mean_ret', 0)
            if base_w > 0:
                d = ((cand_w - base_w) / base_w) * 100
                if d < -25:
                    p4_pass = False
                    annual_regs.append(f'{label}={d:+.0f}%')
        p4 = '✓' if p4_pass else '✗ ' + ','.join(annual_regs)
        p5 = '✓' if max_coll == 0 else f'✗ {max_coll:.1f}%'
        p6 = '✓' if dd_global <= base_dd_global + 1 else f'✗ +{dd_global - base_dd_global:.1f}pp'

        print(f"  {name:<20s}  P3={p3}  P4={p4}  P5={p5}  P6={p6}")

    print("\n[done] writeup")


if __name__ == '__main__':
    main()
