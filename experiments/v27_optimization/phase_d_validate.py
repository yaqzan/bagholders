"""Phase D-C: Top-K canonical MC validation for Phase D candidates.

Runs each top candidate at full canonical fidelity (N=500 × 8 windows × 3 modes)
plus the production baseline. Critical: validates that puts_first priority +
tight PUT_SL holds across all annual windows (especially 2024 bull-year).
"""
from __future__ import annotations
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc
from experiments import bayes_mc
from experiments.v27_optimization.phase_d_full import (
    extended_apply_config_d,
    PHASE_D_BASE,
    PUT_SL_VARIANTS,
)

bayes_mc.apply_config = extended_apply_config_d


VALIDATION_WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('dip',    date(2025, 11, 1), date(2026, 4, 24)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('5y',     date(2021, 1, 1),  date(2026, 4, 15)),
]


def production_baseline_config():
    """v27 production baseline (matches Phase 0)."""
    return {
        'PUT_TP': 0.30, 'PUT_SL': -0.20, 'PUT_TP_STRESS': 0.30, 'PUT_SL_STRESS': -0.20,
        'TP_BASE': 0.30, 'TP_STRESS': 0.35, 'SL_BASE': -0.35, 'SL_STRESS': -0.40,
        'BREADTH_THRESHOLD': 50, 'PUT_BREADTH_MODE': 'none', 'PUT_BREADTH_THRESHOLD': 50,
        'PUT_TIER_ALLOC': {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12},
        'TIER_ALLOC': {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00},
        'PUT_THRESHOLD': 25, 'MAX_POSITIONS': 14, 'MAX_POSITIONS_CALL': None, 'MAX_POSITIONS_PUT': None,
        'N_ITER': 500, 'COLLISION_MODES': ['conservative', 'realistic', 'optimistic'],
        'BREADTH_ALLOC_ENABLED': True,
        'F3F_CALL_THRESH': 50.0, 'F3F_CALL_FLOOR': 0.70, 'F3F_CALL_LOW': 20.0,
        'F3F_PUT_THRESH': 75.0, 'F3F_PUT_FLOOR': 0.75, 'F3F_PUT_HIGH': 95.0,
        'EARN_SUPP_PUT': True, 'EARN_SUPP_PUT_DAYS': 5,
        'EARN_SUPP_PUT_MIN_OV': 16, 'EARN_SUPP_PUT_MAX_OV': 20,
        'CT_PROMOTE': True, 'CT_PUT_TREND_MIN': 80, 'CT_CALL_TREND_MAX': 20,
        'PUT_SL_VARIANT': 's20', 'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'calls_first',
    }


def run_canonical_3mode(cfg: dict, version, label='?'):
    extended_apply_config_d(cfg)
    print(f'\n{"=" * 100}')
    print(f'CONFIG: {label}')
    print('=' * 100)
    out = {}
    for wlabel, d_start, d_end in VALIDATION_WINDOWS:
        wr = mc.run_window(wlabel, d_start, d_end, version)
        out[wlabel] = wr
    return out


def main():
    from database.models.core import AlgorithmVersion

    top_path = os.path.join(os.path.dirname(__file__), 'phase_d_top_candidates.json')
    with open(top_path) as f:
        top_k = json.load(f)
    top_n = int(os.environ.get('PHASE_D_TOP_N', '5'))
    candidates = top_k[:top_n]

    version = AlgorithmVersion.get_active_scores_version()
    print(f'\nAlgorithm version: {version.git_commit}')
    print(f'Validating {top_n} Phase D candidates + production baseline at N=500 × {len(VALIDATION_WINDOWS)} windows × 3 modes')

    results = {}

    # Production baseline
    print('\n--- Running PRODUCTION BASELINE ---')
    base_cfg = production_baseline_config()
    results['__baseline__'] = run_canonical_3mode(base_cfg, version, 'BASELINE (v27 production)')

    # Phase D candidates
    for i, cand in enumerate(candidates):
        cfg = dict(PHASE_D_BASE)
        cfg['N_ITER'] = 500
        for k, v in cand['params'].items():
            cfg[k] = v
        label = f'CAND_{i+1} (util={cand["utility"]:+.3f})'
        print(f'\n--- Running {label} ---')
        print(f'    Params: {cand["params"]}')
        results[f'cand_{i+1}'] = run_canonical_3mode(cfg, version, label)

    # Save
    out_path = os.path.join(os.path.dirname(__file__), 'phase_d_results.json')
    with open(out_path, 'w') as f:
        clean = {}
        for cand_label, win_res in results.items():
            clean[cand_label] = {}
            for w, modes in win_res.items():
                clean[cand_label][w] = {m: dict(r) for m, r in modes.items()}
        clean['__candidates__'] = candidates
        json.dump(clean, f, indent=2, default=str)

    # Comparison report
    print('\n' + '=' * 110)
    print('PHASE D-C — RESULTS COMPARISON (vs baseline)')
    print('=' * 110)

    base = results['__baseline__']
    print(f'\n{"Window":<10}  {"Baseline":>15}  ' +
          '  '.join(f'{f"cand_{i+1}":>15}' for i in range(len(candidates))))
    print('Realistic Mean Return:')
    for wlabel, _, _ in VALIDATION_WINDOWS:
        b = base[wlabel].get('realistic', {})
        b_ret = b.get('mean_ret', 0)
        row = f'{wlabel:<10}  {b_ret:>+13.1f}%  '
        for i in range(len(candidates)):
            c = results[f'cand_{i+1}'][wlabel].get('realistic', {})
            c_ret = c.get('mean_ret', 0)
            delta_pct = ((c_ret + 100) / (b_ret + 100) - 1) * 100 if b_ret > -100 else 0
            row += f'{c_ret:>+12.1f}% [{delta_pct:+5.1f}%]  '
        print(row)

    print('\nConservative Worst DD (DD-C floor):')
    for wlabel, _, _ in VALIDATION_WINDOWS:
        b = base[wlabel].get('conservative', {})
        b_dd = b.get('worst_dd', 0)
        row = f'{wlabel:<10}  {b_dd:>14.1f}%  '
        for i in range(len(candidates)):
            c = results[f"cand_{i+1}"][wlabel].get('conservative', {})
            c_dd = c.get('worst_dd', 0)
            flag = ' BREACH' if c_dd > 80 else ''
            row += f'{c_dd:>14.1f}%{flag} '
        print(row)

    print('\nPut/Call Trade Counts (Realistic) and TP rates:')
    for wlabel, _, _ in VALIDATION_WINDOWS:
        b = base[wlabel].get('realistic', {})
        row = f'{wlabel:<10}  '
        row += f'C:{b.get("call_trades",0):>5.0f}/P:{b.get("put_trades",0):>5.0f} CTP:{b.get("call_tp",0):>4.1f} PTP:{b.get("put_tp",0):>4.1f} | '
        for i in range(len(candidates)):
            c = results[f'cand_{i+1}'][wlabel].get('realistic', {})
            row += f'C:{c.get("call_trades",0):>4.0f}/P:{c.get("put_trades",0):>4.0f} '
        print(row)

    print('\nShip Gate Summary (per candidate):')
    for i, cand in enumerate(candidates):
        cand_res = results[f'cand_{i+1}']
        worst_cons_dd = max(cand_res[w].get('conservative', {}).get('worst_dd', 0) for w, _, _ in VALIDATION_WINDOWS)
        max_collapse = max(
            max(cand_res[w].get(m, {}).get('p_coll', 0) for m in ['conservative', 'realistic', 'optimistic'])
            for w, _, _ in VALIDATION_WINDOWS
        )
        max_regression = 0
        worst_window = None
        for wlabel, _, _ in VALIDATION_WINDOWS:
            b = base[wlabel].get('realistic', {})
            c = cand_res[wlabel].get('realistic', {})
            b_ret = b.get('mean_ret', 0); c_ret = c.get('mean_ret', 0)
            if b_ret > 0:
                delta_pct = ((c_ret + 100) / (b_ret + 100) - 1) * 100
                if delta_pct < max_regression:
                    max_regression = delta_pct
                    worst_window = wlabel
        b22 = base['22-now'].get('realistic', {}).get('mean_ret', 0)
        c22 = cand_res['22-now'].get('realistic', {}).get('mean_ret', 0)
        delta22 = ((c22 + 100) / (b22 + 100) - 1) * 100 if b22 > 0 else 0
        ddflag = 'PASS' if worst_cons_dd <= 80 else 'FAIL'
        regflag = 'PASS' if max_regression >= -25 else 'FAIL'
        collflag = 'PASS' if max_collapse <= 0.5 else 'FAIL'
        ship = ddflag == 'PASS' and regflag == 'PASS' and collflag == 'PASS'
        print(f'\n  cand_{i+1} (util={cand["utility"]:+.3f})  params={cand["params"]}')
        print(f'    22-now Δ vs baseline: {delta22:+.1f}%')
        print(f'    Worst Cons DD across windows: {worst_cons_dd:.1f}% [{ddflag}]')
        print(f'    Worst Real regression vs baseline: {max_regression:+.1f}% on {worst_window} [{regflag}]')
        print(f'    Max collapse rate: {max_collapse:.2f}% [{collflag}]')
        print(f'    SHIP GATE: {"PASS" if ship else "FAIL"}')

    print(f'\nFull results saved to {out_path}')


if __name__ == '__main__':
    main()
