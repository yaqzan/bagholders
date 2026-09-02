"""Phase H5b — focused validation of HOLD=15 variants from H5 top 10.

H5 winner (HOLD=13) hit 71% DD but truncated 2021 winners (-73% vs V1).
Phase H5b validates HOLD=15 variants which preserve winners but use
HARD_SELL=-0.40 + PUT_TP=0.35 + CTP=0.35 + CSL=-0.30 for DD reduction.
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
from experiments.v27_optimization.phase_h5_validate import (
    apply_cfg, V1_BASE, V0_PROD, V1_BASELINE, H4_WIN, VALIDATION_WINDOWS,
)


# H5 ranks 7 & 8 (HOLD=15) + cand_1 for direct comparison
HOLD15_VARIANTS = [
    # H5 rank 7: HOLD=15, HARD=-0.40, PUT_TP=0.35, CTP=0.35, CSL=-0.30 (DD 71.6%)
    {'label': 'H5_HOLD15_H40',
     'overrides': {'HOLD_DAYS': 15, 'HARD_SELL_LOSS': -0.40, 'PUT_TP': 0.35,
                   'TP_BASE': 0.35, 'SL_BASE': -0.30}},
    # H5 rank 8: HOLD=15, HARD=-0.45, PUT_TP=0.35, CTP=0.35, CSL=-0.30 (DD 71.7%)
    {'label': 'H5_HOLD15_H45',
     'overrides': {'HOLD_DAYS': 15, 'HARD_SELL_LOSS': -0.45, 'PUT_TP': 0.35,
                   'TP_BASE': 0.35, 'SL_BASE': -0.30}},
    # Cand_1 (HOLD=13) — already validated, included for direct comparison
    {'label': 'H5_HOLD13',
     'overrides': {'HOLD_DAYS': 13, 'HARD_SELL_LOSS': -0.40, 'PUT_TP': 0.35,
                   'TP_BASE': 0.35, 'SL_BASE': -0.30}},
    # Hybrid: HOLD=14 (between)
    {'label': 'H5_HOLD14',
     'overrides': {'HOLD_DAYS': 14, 'HARD_SELL_LOSS': -0.40, 'PUT_TP': 0.35,
                   'TP_BASE': 0.35, 'SL_BASE': -0.30}},
]


def build_cfg(overrides):
    cfg = dict(V1_BASE)
    cfg.update(H4_WIN)
    cfg.update(overrides)
    cfg['N_ITER'] = 500
    return cfg


def run_canonical(cfg, version, label):
    apply_cfg(cfg)
    print(f'\n{"=" * 100}\nCONFIG: {label}\n{"=" * 100}')
    out = {}
    for wlabel, d_start, d_end in VALIDATION_WINDOWS:
        wr = mc.run_window(wlabel, d_start, d_end, version)
        out[wlabel] = wr
    return out


def main():
    from database.models.core import AlgorithmVersion
    version = AlgorithmVersion.get_active_scores_version()
    print(f'\nPhase H5b: HOLD={{13,14,15}} variants + V0/V1 baselines at N=500')

    results = {}

    print('\n--- V0_PROD ---')
    results['V0_PROD'] = run_canonical(V0_PROD, version, 'V0 PROD')

    print('\n--- V1_BASELINE ---')
    results['V1_BASELINE'] = run_canonical(V1_BASELINE, version, 'V1 BASELINE')

    for v in HOLD15_VARIANTS:
        cfg = build_cfg(v['overrides'])
        print(f'\n--- {v["label"]} ---')
        results[v['label']] = run_canonical(cfg, version, v['label'])

    # Save
    out_path = os.path.join(os.path.dirname(__file__), 'phase_h5b_results.json')
    with open(out_path, 'w') as f:
        clean = {k: {w: {m: dict(r) for m, r in modes.items()} for w, modes in win_res.items()}
                 for k, win_res in results.items()}
        json.dump(clean, f, indent=2, default=str)

    print('\n' + '=' * 110)
    print('PHASE H5b RESULTS')
    print('=' * 110)
    print(f'\n{"Window":<10}  ' + '  '.join(f'{lbl:>14}' for lbl in ['V0_PROD', 'V1_BASE'] + [v['label'] for v in HOLD15_VARIANTS]))
    print('Realistic Mean Return:')
    for wlabel, _, _ in VALIDATION_WINDOWS:
        row = f'{wlabel:<10}  '
        for label_key in ['V0_PROD', 'V1_BASELINE'] + [v['label'] for v in HOLD15_VARIANTS]:
            r = results[label_key][wlabel].get('realistic', {})
            ret = r.get('mean_ret', 0)
            row += f'{ret:>+13.1f}%  '
        print(row)

    print('\nConservative Worst DD:')
    for wlabel, _, _ in VALIDATION_WINDOWS:
        row = f'{wlabel:<10}  '
        for label_key in ['V0_PROD', 'V1_BASELINE'] + [v['label'] for v in HOLD15_VARIANTS]:
            r = results[label_key][wlabel].get('conservative', {})
            dd = r.get('worst_dd', 0)
            flag = ' *' if dd > 80 else ''
            row += f'{dd:>13.1f}%{flag} '
        print(row)

    print('\nShip Gate Summary (vs V1 baseline):')
    base = results['V1_BASELINE']
    for v in HOLD15_VARIANTS:
        cand_res = results[v['label']]
        worst_cons_dd = max(cand_res[w].get('conservative', {}).get('worst_dd', 0) for w, _, _ in VALIDATION_WINDOWS)
        max_collapse = max(max(cand_res[w].get(m, {}).get('p_coll', 0)
                               for m in ['conservative', 'realistic', 'optimistic'])
                           for w, _, _ in VALIDATION_WINDOWS)
        max_regression = 0; worst_window = None
        for wlabel, _, _ in VALIDATION_WINDOWS:
            b = base[wlabel].get('realistic', {}); c = cand_res[wlabel].get('realistic', {})
            b_ret = b.get('mean_ret', 0); c_ret = c.get('mean_ret', 0)
            if b_ret > 0:
                d = ((c_ret + 100) / (b_ret + 100) - 1) * 100
                if d < max_regression: max_regression = d; worst_window = wlabel
        b22 = base['22-now'].get('realistic', {}).get('mean_ret', 0)
        c22 = cand_res['22-now'].get('realistic', {}).get('mean_ret', 0)
        delta22 = ((c22 + 100) / (b22 + 100) - 1) * 100 if b22 > 0 else 0
        ddflag = 'PASS' if worst_cons_dd <= 80 else 'FAIL'
        regflag = 'PASS' if max_regression >= -25 else 'FAIL'
        ship = ddflag == 'PASS' and regflag == 'PASS' and max_collapse <= 0.5
        print(f'\n  {v["label"]}  overrides={v["overrides"]}')
        print(f'    22-now Δ vs V1: {delta22:+.1f}%')
        print(f'    Worst Cons DD: {worst_cons_dd:.1f}% [{ddflag}]')
        print(f'    Worst Real regression vs V1: {max_regression:+.1f}% on {worst_window} [{regflag}]')
        print(f'    SHIP GATE: {"PASS" if ship else "FAIL"}')


if __name__ == '__main__':
    main()
