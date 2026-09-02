"""Phase 5b: Conservative validation of the Bayesian-optimal config.

Goal: isolate the alpha from the phase 3 (cascade allocation) + phase 4
(MaxPos split) winners, while REVERTING phase 1 (PUT_TP/SL) and phase 2
(breadth-adaptive puts) back to current production defaults.

Rationale: phase 1 found PUT_SL=-0.10 as optimal, but raw put TP rate drops
to 28-31% at that SL level (vs 46-50% at SL=-0.20), far below the ~43.5%
break-even. The uplift comes from same-stock re-entry cycling (classic
simulation artifact, see "SL Sweep Findings" in CLAUDE.md) -- not genuine
signal quality. Shipping PUT_SL=-0.10 live will not deliver the paper gains
because real broker fills, bid-ask, and margin constraints will not permit
the sim's per-bar capital recycling.

Phase 3 (allocation cascade <=15: 20%, 16-20: 8%, 21-25: 5%) and Phase 4
(separate 10 call / 10 put caps on 20 total) are genuine structural findings
that should survive in live trading.

Runs at full fidelity (N=500, 3 modes, 6 windows) alongside production baseline.
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


def _assemble_conservative_config():
    """Use phase 3 + 4 winners only; keep phase 1 + 2 at production defaults."""
    cfg = dict(DEFAULT_CONFIG)
    cfg['N_ITER'] = 500
    cfg['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']

    # PHASE 1: REVERTED to production (PUT_TP=0.30, PUT_SL=-0.20 from DEFAULT_CONFIG)
    # PHASE 2: REVERTED to production (PUT_BREADTH_MODE='none')
    # Both kept as DEFAULT_CONFIG values.

    # PHASE 3: cascade allocation -- KEEP winner
    p3 = _load_best('phase_3_put_cascade.jsonl')
    if p3:
        cfg['PUT_TIER_ALLOC'] = {
            'put_top': p3.get('PUT_TIER_ALLOC.put_top', 0.15),
            'put_mid': p3.get('PUT_TIER_ALLOC.put_mid', 0.12),
            'put_low': p3.get('PUT_TIER_ALLOC.put_low', 0.12),
        }

    # PHASE 4: MaxPos split -- KEEP winner
    p4 = _load_best('phase_4_maxpos.jsonl')
    if p4:
        cfg['MAX_POSITIONS']      = p4.get('MAX_POSITIONS', cfg['MAX_POSITIONS'])
        cfg['MAX_POSITIONS_CALL'] = p4.get('MAX_POSITIONS_CALL', None)
        cfg['MAX_POSITIONS_PUT']  = p4.get('MAX_POSITIONS_PUT', None)

    return cfg, {'p3': p3, 'p4': p4}


def _run_full(cfg, label):
    print(f'\n{"="*96}\nPHASE 5b -- running {label} at full fidelity (N={cfg["N_ITER"]} x 3 modes x 6 windows)\n{"="*96}')
    version = AlgorithmVersion.get_active_scores_version()
    apply_config(cfg)

    results = {}
    for wlabel, d_start, d_end in SWEEP_WINDOWS:
        t0 = time.time()
        r = mc.run_window(wlabel, d_start, d_end, version)
        dur = time.time() - t0
        results[wlabel] = r
        print(f'  [{wlabel}] done in {dur:.0f}s')

    return results


def main():
    cons_cfg, winners = _assemble_conservative_config()
    print('\nCONSERVATIVE CONFIG (phase 3+4 winners only; phase 1+2 reverted to prod):')
    for k in ('PUT_TP','PUT_SL','PUT_BREADTH_MODE','PUT_BREADTH_THRESHOLD',
              'PUT_TP_STRESS','PUT_SL_STRESS','PUT_TIER_ALLOC',
              'MAX_POSITIONS','MAX_POSITIONS_CALL','MAX_POSITIONS_PUT'):
        print(f'  {k:<26}= {cons_cfg[k]}')

    base_cfg = dict(DEFAULT_CONFIG)
    base_cfg['N_ITER'] = 500
    base_cfg['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']

    cons_res = _run_full(cons_cfg, 'CONSERVATIVE (p3+p4 only)')
    prod_res = _run_full(base_cfg, 'PRODUCTION baseline')

    # Also load the full-optimization phase 5 results to report the 3-way compare
    p5_path = os.path.join(LOG_DIR, 'phase_5_validation.json')
    full_opt_res = None
    if os.path.exists(p5_path):
        with open(p5_path, 'r', encoding='utf-8') as f:
            p5 = json.load(f)
        full_opt_res = p5.get('optimized_results')

    print('\n' + '=' * 140)
    print('PHASE 5b FINAL -- 3-way side-by-side (Realistic mode)')
    print('=' * 140)
    header = f'{"Window":<10}  {"PROD Real":>14}  {"CONS Real":>14}  {"OPT Real":>16}  {"CONS/PROD":>10}  {"OPT/PROD":>10}  {"CONS DD-R":>9}  {"CONS DD-C":>9}'
    print(header)
    for wlabel, _, _ in SWEEP_WINDOWS:
        pr   = prod_res[wlabel]['realistic']['mean_ret']
        cr   = cons_res[wlabel]['realistic']['mean_ret']
        opr  = full_opt_res[wlabel]['realistic']['mean_ret'] if full_opt_res else float('nan')
        dd_r = cons_res[wlabel]['realistic']['worst_dd']
        dd_c = cons_res[wlabel]['conservative']['worst_dd']
        cr_mult  = (cr / pr) if pr > 0 else float('nan')
        opr_mult = (opr / pr) if pr > 0 else float('nan')
        opr_str  = f'{opr:>+15.1f}%' if full_opt_res else '        n/a'
        print(f'{wlabel:<10}  {pr:>+13.1f}%  {cr:>+13.1f}%  {opr_str}  {cr_mult:>9.2f}x  {opr_mult:>9.2f}x  {dd_r:>8.1f}%  {dd_c:>8.1f}%')

    out_path = os.path.join(LOG_DIR, 'phase_5b_validation.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'winners': winners,
            'conservative_config': cons_cfg,
            'conservative_results': cons_res,
            'production_results': prod_res,
        }, f, default=str, indent=2)
    print(f'\nFull results saved to {out_path}')


if __name__ == '__main__':
    main()
