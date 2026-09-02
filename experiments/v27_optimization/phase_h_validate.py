"""Generic phase-H validation: full canonical N=500 × 8 windows for top candidates.

Usage: PHASE=h4 PHASE_TOP_N=3 MC_NO_MP=1 python phase_h_validate.py
- Reads phase_{PHASE}_top_candidates.json
- Validates top-N candidates + V1 baseline at N=500 × 8 windows × 3 modes
- Writes phase_{PHASE}_results.json with full breakdown
- Prints ship gate evaluation
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

PHASE = os.environ.get('PHASE', 'h4').lower()
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


def apply_h_config(cfg):
    """Apply config that may have any H-phase keys."""
    # PUT_SL static
    if cfg.get('PUT_SL_BASE') is not None:
        mc.PUT_SL_MODE = 'static'
        mc.PUT_SL = cfg['PUT_SL_BASE']
        mc.PUT_NET_SL = cfg['PUT_SL_BASE'] + mc.SLIP_ENTRY + mc.SLIP_SL
        mc.PUT_SL_SIGMA = abs(cfg['PUT_SL_BASE']) * mc.PREMIUM_MULT / mc.DELTA
    elif cfg.get('PUT_SL') is not None:
        mc.PUT_SL_MODE = 'static'
        mc.PUT_SL = cfg['PUT_SL']
        mc.PUT_NET_SL = cfg['PUT_SL'] + mc.SLIP_ENTRY + mc.SLIP_SL
        mc.PUT_SL_SIGMA = abs(cfg['PUT_SL']) * mc.PREMIUM_MULT / mc.DELTA

    if 'PUT_TP' in cfg:
        mc.PUT_TP = cfg['PUT_TP']
        mc.PUT_NET_TP = cfg['PUT_TP'] + mc.SLIP_ENTRY + mc.SLIP_TP
        mc.PUT_TP_SIGMA = cfg['PUT_TP'] * mc.PREMIUM_MULT / mc.DELTA

    if 'PUT_SL_HOLD_BARS_DEFAULT' in cfg:
        mc.PUT_SL_HOLD_BARS_DEFAULT = cfg['PUT_SL_HOLD_BARS_DEFAULT']
        mc.PUT_SL_HOLD_BARS_MONDAY  = max(0, cfg['PUT_SL_HOLD_BARS_DEFAULT'] + 1) if cfg['PUT_SL_HOLD_BARS_DEFAULT'] > 0 else 0
    if 'PUT_PRIORITY' in cfg: mc.PUT_PRIORITY = cfg['PUT_PRIORITY']
    if 'EARN_SUPP_PUT' in cfg: mc.EARN_SUPP_PUT = cfg['EARN_SUPP_PUT']
    if 'MAX_POSITIONS' in cfg: mc.MAX_POSITIONS = cfg['MAX_POSITIONS']

    if 'F3F_PUT_FLOOR' in cfg:    mc.F3F_PUT_FLOOR = cfg['F3F_PUT_FLOOR']
    if 'F3F_CALL_FLOOR' in cfg:   mc.F3F_CALL_FLOOR = cfg['F3F_CALL_FLOOR']
    if 'F3F_PUT_THRESH' in cfg:   mc.F3F_PUT_THRESH = cfg['F3F_PUT_THRESH']
    if 'F3F_CALL_THRESH' in cfg:  mc.F3F_CALL_THRESH = cfg['F3F_CALL_THRESH']

    # Cascade
    ta = dict(mc.TIER_ALLOC)
    for k in ('ultra', 'top', 'mid', 'low'):
        kk = f'TIER_ALLOC.{k}'
        if kk in cfg:
            ta[k] = cfg[kk]
    if 'TIER_ALLOC' in cfg:
        ta.update(cfg['TIER_ALLOC'])
    mc.TIER_ALLOC = ta

    pta = dict(mc.PUT_TIER_ALLOC)
    for k in ('put_top', 'put_mid', 'put_low'):
        kk = f'PUT_TIER_ALLOC.{k}'
        if kk in cfg:
            pta[k] = cfg[kk]
    if 'PUT_TIER_ALLOC' in cfg:
        pta.update(cfg['PUT_TIER_ALLOC'])
    mc.PUT_TIER_ALLOC = pta

    if 'COLLISION_MODES' in cfg: mc.COLLISION_MODES = list(cfg['COLLISION_MODES'])
    if 'N_ITER' in cfg: mc.N_ITER = cfg['N_ITER']


# V1 baseline (production + hold=0)
V1_BASELINE = {
    'PUT_SL': -0.20, 'PUT_TP': 0.30,
    'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'calls_first',
    'EARN_SUPP_PUT': True, 'MAX_POSITIONS': 14,
    'TIER_ALLOC': {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00},
    'PUT_TIER_ALLOC': {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12},
    'F3F_PUT_FLOOR': 0.75, 'F3F_CALL_FLOOR': 0.70,
    'N_ITER': 500, 'COLLISION_MODES': ['conservative', 'realistic', 'optimistic'],
}

# V0 production (full prod with hold=3) for comparison
V0_PRODUCTION = {**V1_BASELINE, 'PUT_SL_HOLD_BARS_DEFAULT': 3}


def build_phase_config(top_entry, phase_lock_overrides=None):
    """Merge candidate params into V1 baseline + apply phase locks."""
    cfg = dict(V1_BASELINE)
    cfg['N_ITER'] = 500
    if phase_lock_overrides:
        cfg.update(phase_lock_overrides)
    p = top_entry['params']
    for k, v in p.items():
        cfg[k] = v
    return cfg


def run_canonical(cfg, version, label):
    apply_h_config(cfg)
    print(f'\n{"=" * 100}\nCONFIG: {label}\n{"=" * 100}')
    out = {}
    for wlabel, d_start, d_end in VALIDATION_WINDOWS:
        wr = mc.run_window(wlabel, d_start, d_end, version)
        out[wlabel] = wr
    return out


# Phase-specific lock overrides (for H phases that locked specific values)
PHASE_LOCKS = {
    'h1': {},   # H1 sweeps SL/HOLD/PRIORITY directly — no extra lock
    'h2': {},   # H2 same
    'h3': {'PUT_SL': -0.15, 'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'calls_first'},
    'h4': {'PUT_SL': -0.20, 'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'calls_first',
           'EARN_SUPP_PUT': True},
}


def main():
    from database.models.core import AlgorithmVersion

    top_path = os.path.join(os.path.dirname(__file__), f'phase_{PHASE}_top_candidates.json')
    if not os.path.exists(top_path):
        print(f'Missing: {top_path}')
        return
    with open(top_path) as f:
        top_k = json.load(f)
    candidates = top_k[:TOP_N]

    version = AlgorithmVersion.get_active_scores_version()
    print(f'\nPhase {PHASE.upper()} validation: top-{TOP_N} + V1 baseline + V0 production at N=500')
    print(f'Version: {version.git_commit}')

    results = {}

    # V0 production (with hold=3) for direct comparison
    print('\n--- V0_PROD (hold=3 production) ---')
    results['V0_PROD'] = run_canonical(V0_PRODUCTION, version, 'V0 PROD')

    # V1 baseline (production + hold=0)
    print('\n--- V1_BASELINE (prod + hold=0) ---')
    results['V1_BASELINE'] = run_canonical(V1_BASELINE, version, 'V1 BASELINE')

    # Top-N candidates
    locks = PHASE_LOCKS.get(PHASE, {})
    for i, cand in enumerate(candidates):
        cfg = build_phase_config(cand, locks)
        label = f'CAND_{i+1} (util={cand["utility"]:+.3f})'
        print(f'\n--- {label} ---')
        print(f'    Params: {cand["params"]}')
        results[f'cand_{i+1}'] = run_canonical(cfg, version, label)

    # Save
    out_path = os.path.join(os.path.dirname(__file__), f'phase_{PHASE}_results.json')
    with open(out_path, 'w') as f:
        clean = {k: {w: {m: dict(r) for m, r in modes.items()} for w, modes in win_res.items()}
                 for k, win_res in results.items()}
        clean['__candidates__'] = candidates
        json.dump(clean, f, indent=2, default=str)

    # Comparison
    print('\n' + '=' * 110)
    print(f'PHASE {PHASE.upper()} VALIDATION RESULTS')
    print('=' * 110)
    print(f'\n{"Window":<10}  ' + '  '.join(f'{lbl:>14}' for lbl in ['V0_PROD', 'V1_BASE'] + [f'cand_{i+1}' for i in range(len(candidates))]))
    print('Realistic Mean Return:')
    for wlabel, _, _ in VALIDATION_WINDOWS:
        row = f'{wlabel:<10}  '
        for label_key in ['V0_PROD', 'V1_BASELINE'] + [f'cand_{i+1}' for i in range(len(candidates))]:
            r = results[label_key][wlabel].get('realistic', {})
            ret = r.get('mean_ret', 0)
            row += f'{ret:>+13.1f}%  '
        print(row)

    print('\nConservative Worst DD:')
    for wlabel, _, _ in VALIDATION_WINDOWS:
        row = f'{wlabel:<10}  '
        for label_key in ['V0_PROD', 'V1_BASELINE'] + [f'cand_{i+1}' for i in range(len(candidates))]:
            r = results[label_key][wlabel].get('conservative', {})
            dd = r.get('worst_dd', 0)
            flag = ' *' if dd > 80 else ''
            row += f'{dd:>13.1f}%{flag} '
        print(row)

    print('\nShip Gate Summary (vs V1 baseline):')
    base = results['V1_BASELINE']
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
        print(f'    22-now Δ vs V1: {delta22:+.1f}%')
        print(f'    Worst Cons DD: {worst_cons_dd:.1f}% [{ddflag}]')
        print(f'    Worst Real regression vs V1: {max_regression:+.1f}% on {worst_window} [{regflag}]')
        print(f'    Max collapse: {max_collapse:.2f}%')
        print(f'    SHIP GATE: {"PASS" if ship else "FAIL"}')

    print(f'\nFull results: {out_path}')


if __name__ == '__main__':
    main()
