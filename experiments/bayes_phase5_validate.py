"""Phase 5: Final validation of the Bayesian-optimal config.

Runs the combined winning configuration from phases 1-4 at FULL fidelity:
  - N_ITER = 500
  - All 3 collision modes (conservative, realistic, optimistic)
  - All 6 windows (2021 / 2022 / 2023 / 2024 / 2025 / 22-now)

Also re-runs the PRODUCTION baseline (current monte_carlo.py v18 config) at
identical fidelity so we can report a true apples-to-apples delta.
"""

import json, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

from experiments.bayes_mc import (
    DEFAULT_CONFIG, SWEEP_WINDOWS, run_config, apply_config, utility_from_results,
)
import monte_carlo as mc
from database.models.core import AlgorithmVersion

LOG_DIR = os.path.join(ROOT, 'experiments', 'bayes_logs')


def _load_best(phase_file):
    path = os.path.join(LOG_DIR, phase_file)
    if not os.path.exists(path): return None
    best = None
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            rec = json.loads(line)
            if rec.get('event') == 'eval':
                if best is None or rec['utility'] > best['utility']:
                    best = rec
    return best['params'] if best else None


def _assemble_best_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg['N_ITER'] = 500
    cfg['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']

    p1 = _load_best('phase_1_put_tpsl.jsonl')
    p2 = _load_best('phase_2_put_breadth.jsonl')
    p3 = _load_best('phase_3_put_cascade.jsonl')
    p4 = _load_best('phase_4_maxpos.jsonl')

    if p1:
        cfg['PUT_TP'] = p1['PUT_TP']
        cfg['PUT_SL'] = p1['PUT_SL']
    if p2:
        cfg['PUT_BREADTH_MODE'] = p2.get('PUT_BREADTH_MODE', cfg['PUT_BREADTH_MODE'])
        cfg['PUT_BREADTH_THRESHOLD'] = p2.get('PUT_BREADTH_THRESHOLD', cfg['PUT_BREADTH_THRESHOLD'])
        cfg['PUT_TP_STRESS'] = p2.get('PUT_TP_STRESS', cfg['PUT_TP'])
        cfg['PUT_SL_STRESS'] = p2.get('PUT_SL_STRESS', cfg['PUT_SL'])
    if p3:
        cfg['PUT_TIER_ALLOC'] = {
            'put_top': p3.get('PUT_TIER_ALLOC.put_top', 0.15),
            'put_mid': p3.get('PUT_TIER_ALLOC.put_mid', 0.12),
            'put_low': p3.get('PUT_TIER_ALLOC.put_low', 0.12),
        }
    if p4:
        cfg['MAX_POSITIONS'] = p4.get('MAX_POSITIONS', cfg['MAX_POSITIONS'])
        cfg['MAX_POSITIONS_CALL'] = p4.get('MAX_POSITIONS_CALL', None)
        cfg['MAX_POSITIONS_PUT']  = p4.get('MAX_POSITIONS_PUT', None)

    return cfg, {'p1': p1, 'p2': p2, 'p3': p3, 'p4': p4}


def _run_full(cfg, label):
    print(f'\n{"="*96}\nPHASE 5 — running {label} at full fidelity (N={cfg["N_ITER"]} x 3 modes x 6 windows)\n{"="*96}')
    version = AlgorithmVersion.get_active_scores_version()
    apply_config(cfg)

    results = {}
    for wlabel, d_start, d_end in SWEEP_WINDOWS:
        t0 = time.time()
        r = mc.run_window(wlabel, d_start, d_end, version)
        dur = time.time() - t0
        # Each mode's dict is returned — save all
        results[wlabel] = r
        print(f'  [{wlabel}] done in {dur:.0f}s')

    return results


def main():
    best_cfg, winners = _assemble_best_config()
    print('\nOPTIMIZED CONFIG (from Bayesian phases 1-4):')
    for k in ('PUT_TP','PUT_SL','PUT_BREADTH_MODE','PUT_BREADTH_THRESHOLD',
              'PUT_TP_STRESS','PUT_SL_STRESS','PUT_TIER_ALLOC',
              'MAX_POSITIONS','MAX_POSITIONS_CALL','MAX_POSITIONS_PUT'):
        print(f'  {k:<26}= {best_cfg[k]}')

    # Reference baseline (current production)
    base_cfg = dict(DEFAULT_CONFIG)
    base_cfg['N_ITER'] = 500
    base_cfg['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']

    opt_res  = _run_full(best_cfg, 'OPTIMIZED')
    prod_res = _run_full(base_cfg, 'PRODUCTION baseline')

    # Side-by-side summary
    print('\n' + '=' * 120)
    print('PHASE 5 FINAL — side-by-side (Realistic mode headline, Conservative floor)')
    print('=' * 120)
    print(f'{"Window":<10}  {"PROD Real":>14}  {"OPT Real":>14}  {"Δ%":>8}  '
          f'{"PROD Cons":>14}  {"OPT Cons":>14}  {"OPT MaxDD-R":>12}  {"OPT MaxDD-C":>12}')
    for wlabel, _, _ in SWEEP_WINDOWS:
        pr = prod_res[wlabel]['realistic']['mean_ret']
        pc = prod_res[wlabel]['conservative']['mean_ret']
        opr = opt_res[wlabel]['realistic']['mean_ret']
        opc = opt_res[wlabel]['conservative']['mean_ret']
        dd_r = opt_res[wlabel]['realistic']['worst_dd']
        dd_c = opt_res[wlabel]['conservative']['worst_dd']
        delta = (opr - pr)
        print(f'{wlabel:<10}  {pr:>+13.1f}%  {opr:>+13.1f}%  {delta:>+7.1f}  '
              f'{pc:>+13.1f}%  {opc:>+13.1f}%  {dd_r:>11.1f}%  {dd_c:>11.1f}%')

    # Persist full results as JSON for downstream reporting / CLAUDE.md update
    out_path = os.path.join(LOG_DIR, 'phase_5_validation.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'winners': winners,
            'optimized_config': best_cfg,
            'optimized_results': opt_res,
            'production_results': prod_res,
        }, f, default=str, indent=2)
    print(f'\nFull results saved to {out_path}')


if __name__ == '__main__':
    main()
