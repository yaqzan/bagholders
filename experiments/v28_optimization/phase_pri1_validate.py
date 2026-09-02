"""Phase Pri-1 validation — full N=500 × 8 windows × 3 modes.

Validates top-K wr_merged variants vs V0 (current shipped production with
PUT_PRIORITY='calls_first'). Reads phase_pri1_top_candidates.json.

Usage: MC_NO_MP=1 PHASE_TOP_N=3 python phase_pri1_validate.py
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
from experiments.v28_optimization.phase_pri1_priority import (
    extended_apply_config_pri, _TIER_STATS,
)


TOP_N = int(os.environ.get('PHASE_TOP_N', '3'))

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


# V0 = current shipped production (PUT_PRIORITY='calls_first', H5 cascade, etc.)
V0_PRODUCTION = {
    'PUT_PRIORITY': 'calls_first',
    'WR_FORMULA':   'WR15',
    'CALL_BIAS':    1.00,
    'WR_FALLBACK':  65.0,
    'N_ITER':       500,
    'COLLISION_MODES': ['conservative', 'realistic', 'optimistic'],
    # Other params handled by extended_apply_config_pri's production-locks block
    'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00},
    'PUT_TIER_ALLOC': {'put_top': 0.10, 'put_mid': 0.12, 'put_low': 0.12},
    'TP_BASE': 0.35, 'TP_STRESS': 0.40, 'SL_BASE': -0.30, 'SL_STRESS': -0.35,
    'BREADTH_THRESHOLD': 50,
    'PUT_TP': 0.35, 'PUT_SL': -0.20, 'PUT_TP_STRESS': 0.30, 'PUT_SL_STRESS': -0.20,
    'PUT_BREADTH_MODE': 'none', 'PUT_BREADTH_THRESHOLD': 50,
    'PUT_THRESHOLD': 25, 'MAX_POSITIONS': 14,
    'MAX_POSITIONS_CALL': None, 'MAX_POSITIONS_PUT': None,
}


def run_canonical(cfg: dict, version, label: str):
    extended_apply_config_pri(cfg)
    print(f'\n{"=" * 100}')
    print(f'CONFIG: {label}')
    print(f'  PUT_PRIORITY={mc.PUT_PRIORITY}  '
          f'WR_FORMULA={cfg.get("WR_FORMULA","-")}  '
          f'CALL_BIAS={cfg.get("CALL_BIAS","-")}  '
          f'WR_FALLBACK={cfg.get("WR_FALLBACK","-")}')
    if mc.PUT_PRIORITY == 'wr_merged':
        print('  Active priority table:')
        for k, v in sorted(mc.WR_PRIORITY_TABLE.items(), key=lambda x: -x[1]):
            print(f'    {k:>10s}: {v:7.2f}')
    print('=' * 100)
    out = {}
    for wlabel, d_start, d_end in VALIDATION_WINDOWS:
        wr = mc.run_window(wlabel, d_start, d_end, version)
        out[wlabel] = wr
    return out


def build_cand_config(cand: dict) -> dict:
    cfg = dict(V0_PRODUCTION)
    for k, v in cand['params'].items():
        cfg[k] = v
    return cfg


def main():
    from database.models.core import AlgorithmVersion

    top_path = os.path.join(os.path.dirname(__file__), 'phase_pri1_top_candidates.json')
    if not os.path.exists(top_path):
        print(f'Missing: {top_path}')
        sys.exit(1)
    with open(top_path) as f:
        top_k = json.load(f)
    candidates = top_k[:TOP_N]

    version = AlgorithmVersion.get_active_scores_version()
    print(f'\nPhase Pri-1 validation: top-{TOP_N} + V0_PROD baseline at N=500')
    print(f'Active version: {version.git_commit} (id={version.id})')

    results = {}
    print('\n>> V0_PROD (calls_first, current ship)')
    results['V0_PROD'] = run_canonical(V0_PRODUCTION, version, 'V0 PROD')

    for i, cand in enumerate(candidates):
        cfg = build_cand_config(cand)
        label = f'CAND_{i+1} (util={cand["utility"]:+.3f})'
        results[f'cand_{i+1}'] = run_canonical(cfg, version, label)

    out_path = os.path.join(os.path.dirname(__file__), 'phase_pri1_results.json')
    with open(out_path, 'w') as f:
        clean = {k: {w: {m: dict(r) for m, r in modes.items()} for w, modes in win_res.items()}
                 for k, win_res in results.items()}
        clean['__candidates__'] = candidates
        json.dump(clean, f, indent=2, default=str)

    # Comparison tables
    headers = ['V0_PROD'] + [f'cand_{i+1}' for i in range(len(candidates))]
    print('\n' + '=' * 110)
    print('PHASE PRI-1 VALIDATION RESULTS')
    print('=' * 110)
    print(f'\n{"Window":<10}  ' + '  '.join(f'{h:>14}' for h in headers))
    print('Realistic Mean Return:')
    for wlabel, _, _ in VALIDATION_WINDOWS:
        row = f'{wlabel:<10}  '
        for k in headers:
            r = results[k][wlabel].get('realistic', {})
            row += f'{r.get("mean_ret",0):>+13.1f}%  '
        print(row)

    print('\nConservative Worst DD:')
    for wlabel, _, _ in VALIDATION_WINDOWS:
        row = f'{wlabel:<10}  '
        for k in headers:
            r = results[k][wlabel].get('conservative', {})
            dd = r.get('worst_dd', 0)
            flag = ' *' if dd > 80 else ''
            row += f'{dd:>13.1f}%{flag} '
        print(row)

    print('\nCall TP%  /  Put TP%  (realistic):')
    for wlabel, _, _ in VALIDATION_WINDOWS:
        row = f'{wlabel:<10}  '
        for k in headers:
            r = results[k][wlabel].get('realistic', {})
            row += f'  {r.get("call_tp",0):>5.1f}/{r.get("put_tp",0):>5.1f}  '
        print(row)

    print('\nShip Gate Summary (vs V0_PROD):')
    base = results['V0_PROD']
    for i, cand in enumerate(candidates):
        cand_res = results[f'cand_{i+1}']
        worst_cons_dd = max(cand_res[w].get('conservative', {}).get('worst_dd', 0)
                             for w, _, _ in VALIDATION_WINDOWS)
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

        b5 = base['5y'].get('realistic', {}).get('mean_ret', 0)
        c5 = cand_res['5y'].get('realistic', {}).get('mean_ret', 0)
        delta5 = ((c5 + 100) / (b5 + 100) - 1) * 100 if b5 > 0 else 0

        ddflag = 'PASS' if worst_cons_dd <= 80 else 'FAIL'
        regflag = 'PASS' if max_regression >= -25 else 'FAIL'
        ship = ddflag == 'PASS' and regflag == 'PASS' and max_collapse <= 0.5

        print(f'\n  cand_{i+1} (util={cand["utility"]:+.3f})  params={cand["params"]}')
        print(f'    22-now Δ vs V0: {delta22:+.1f}%')
        print(f'       5y Δ vs V0: {delta5:+.1f}%')
        print(f'    Worst Cons DD: {worst_cons_dd:.1f}% [{ddflag}]')
        print(f'    Worst Real regression vs V0: {max_regression:+.1f}% on {worst_window} [{regflag}]')
        print(f'    Max collapse: {max_collapse:.2f}%')
        print(f'    SHIP GATE: {"PASS" if ship else "FAIL"}')

    print(f'\nFull results: {out_path}')


if __name__ == '__main__':
    main()
