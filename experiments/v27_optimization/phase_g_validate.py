"""Phase G-C: validate top-3 from Phase G at full N=500 × 8 windows."""
from __future__ import annotations
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc
from experiments import bayes_mc
from experiments.v27_optimization.phase_g_composite import (
    extended_apply_config_g,
    PHASE_G_BASE,
    F3F_PRESETS,
)
from experiments.v27_optimization.phase_d_validate import (
    VALIDATION_WINDOWS,
    production_baseline_config,
)

bayes_mc.apply_config = extended_apply_config_g


def run_canonical(cfg, version, label):
    extended_apply_config_g(cfg)
    print(f'\n{"=" * 100}\nCONFIG: {label}\n{"=" * 100}')
    out = {}
    for wlabel, d_start, d_end in VALIDATION_WINDOWS:
        wr = mc.run_window(wlabel, d_start, d_end, version)
        out[wlabel] = wr
    return out


def main():
    from database.models.core import AlgorithmVersion

    top_path = os.path.join(os.path.dirname(__file__), 'phase_g_top_candidates.json')
    with open(top_path) as f:
        top_k = json.load(f)
    top_n = int(os.environ.get('PHASE_G_TOP_N', '3'))
    candidates = top_k[:top_n]

    version = AlgorithmVersion.get_active_scores_version()
    print(f'\nv27 algorithm: {version.git_commit}')
    print(f'Validating Phase G top-{top_n} + production baseline at N=500 × {len(VALIDATION_WINDOWS)} windows × 3 modes')

    results = {}

    print('\n--- BASELINE ---')
    base_cfg = production_baseline_config()
    base_cfg.update({'PUT_TP': 0.30, 'PUT_THRESHOLD': 25,
                     'PUT_TIER_ALLOC': {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12},
                     'F3F_PRESET': 'phase_e', 'TIER_ALLOC.top': 0.15})
    results['__baseline__'] = run_canonical(base_cfg, version, 'BASELINE (v27 production)')

    for i, cand in enumerate(candidates):
        cfg = dict(PHASE_G_BASE)
        cfg['N_ITER'] = 500
        for k, v in cand['params'].items():
            cfg[k] = v
        label = f'CAND_{i+1} (util={cand["utility"]:+.3f})'
        print(f'\n--- {label} ---  Params: {cand["params"]}')
        results[f'cand_{i+1}'] = run_canonical(cfg, version, label)

    out_path = os.path.join(os.path.dirname(__file__), 'phase_g_results.json')
    with open(out_path, 'w') as f:
        clean = {k: {w: {m: dict(r) for m, r in modes.items()} for w, modes in win_res.items()}
                 for k, win_res in results.items()}
        clean['__candidates__'] = candidates
        json.dump(clean, f, indent=2, default=str)

    print('\n' + '=' * 110)
    print('PHASE G-C — RESULTS COMPARISON (vs baseline)')
    print('=' * 110)
    base = results['__baseline__']
    print(f'\n{"Window":<10}  {"Baseline":>15}  ' + '  '.join(f'{f"cand_{i+1}":>15}' for i in range(len(candidates))))
    print('Realistic Mean Return:')
    for wlabel, _, _ in VALIDATION_WINDOWS:
        b = base[wlabel].get('realistic', {}); b_ret = b.get('mean_ret', 0)
        row = f'{wlabel:<10}  {b_ret:>+13.1f}%  '
        for i in range(len(candidates)):
            c = results[f'cand_{i+1}'][wlabel].get('realistic', {})
            c_ret = c.get('mean_ret', 0)
            delta = ((c_ret + 100) / (b_ret + 100) - 1) * 100 if b_ret > -100 else 0
            row += f'{c_ret:>+12.1f}% [{delta:+5.1f}%]  '
        print(row)

    print('\nConservative Worst DD:')
    for wlabel, _, _ in VALIDATION_WINDOWS:
        b = base[wlabel].get('conservative', {}); b_dd = b.get('worst_dd', 0)
        row = f'{wlabel:<10}  {b_dd:>14.1f}%  '
        for i in range(len(candidates)):
            c = results[f'cand_{i+1}'][wlabel].get('conservative', {})
            c_dd = c.get('worst_dd', 0)
            flag = ' BREACH' if c_dd > 80 else ''
            row += f'{c_dd:>14.1f}%{flag} '
        print(row)

    print('\nPut/Call TP rates and trade counts:')
    for wlabel, _, _ in VALIDATION_WINDOWS:
        row = f'{wlabel:<10}  '
        for i in range(len(candidates)):
            c = results[f'cand_{i+1}'][wlabel].get('realistic', {})
            row += f'C:{c.get("call_trades",0):>4.0f}/P:{c.get("put_trades",0):>4.0f} CTP:{c.get("call_tp",0):>4.1f} PTP:{c.get("put_tp",0):>4.1f}  '
        print(row)

    print('\nShip Gate Summary:')
    for i, cand in enumerate(candidates):
        cand_res = results[f'cand_{i+1}']
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
        print(f'\n  cand_{i+1} (util={cand["utility"]:+.3f})  params={cand["params"]}')
        print(f'    22-now Δ vs baseline: {delta22:+.1f}%')
        print(f'    Worst Cons DD: {worst_cons_dd:.1f}% [{ddflag}]')
        print(f'    Worst Real regression: {max_regression:+.1f}% on {worst_window} [{regflag}]')
        print(f'    Max collapse: {max_collapse:.2f}%')
        print(f'    SHIP GATE: {"PASS" if ship else "FAIL"}')

    print(f'\nFull results: {out_path}')


if __name__ == '__main__':
    main()
