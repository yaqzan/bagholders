"""Phase H5 validation: top-3 H5 candidates + V1 baseline + V0 production at N=500.

H5 locks H4 winner allocs/F3f/MaxPos AND V1's SL/hold/priority. Candidate
params override loss-limiting axes (HOLD_DAYS, HARD_SELL_LOSS, PUT_TP, etc).
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

# H4 winner locks
H4_WIN = {
    'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00},
    'PUT_TIER_ALLOC': {'put_top': 0.10, 'put_mid': 0.12, 'put_low': 0.12},
    'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50,
    'MAX_POSITIONS': 14,
}

# V1 + H5 base
V1_BASE = {
    'PUT_SL': -0.20, 'PUT_TP': 0.30,
    'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'calls_first',
    'EARN_SUPP_PUT': True,
    'HOLD_DAYS': 15, 'HARD_SELL_LOSS': -0.50,
    'TP_BASE': 0.30, 'TP_STRESS': 0.35,
    'SL_BASE': -0.35, 'SL_STRESS': -0.40,
    'BREADTH_THRESHOLD': 50,
    'N_ITER': 500,
    'COLLISION_MODES': ['conservative', 'realistic', 'optimistic'],
}

V0_PROD = {**V1_BASE, 'PUT_SL_HOLD_BARS_DEFAULT': 3,
           'TIER_ALLOC': {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00},
           'PUT_TIER_ALLOC': {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12},
           'F3F_PUT_FLOOR': 0.75, 'F3F_CALL_FLOOR': 0.70, 'MAX_POSITIONS': 14}

V1_BASELINE = {**V1_BASE,
               'TIER_ALLOC': {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00},
               'PUT_TIER_ALLOC': {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12},
               'F3F_PUT_FLOOR': 0.75, 'F3F_CALL_FLOOR': 0.70, 'MAX_POSITIONS': 14}


def apply_cfg(cfg):
    # PUT side
    mc.PUT_SL_MODE = 'static'
    if 'PUT_SL' in cfg:
        mc.PUT_SL = cfg['PUT_SL']
        mc.PUT_NET_SL = cfg['PUT_SL'] + mc.SLIP_ENTRY + mc.SLIP_SL
        mc.PUT_SL_SIGMA = abs(cfg['PUT_SL']) * mc.PREMIUM_MULT / mc.DELTA
    if 'PUT_TP' in cfg:
        mc.PUT_TP = cfg['PUT_TP']
        mc.PUT_NET_TP = cfg['PUT_TP'] + mc.SLIP_ENTRY + mc.SLIP_TP
        mc.PUT_TP_SIGMA = cfg['PUT_TP'] * mc.PREMIUM_MULT / mc.DELTA
    mc.PUT_SL_HOLD_BARS_DEFAULT = cfg.get('PUT_SL_HOLD_BARS_DEFAULT', 0)
    mc.PUT_SL_HOLD_BARS_MONDAY  = max(0, mc.PUT_SL_HOLD_BARS_DEFAULT + 1) if mc.PUT_SL_HOLD_BARS_DEFAULT > 0 else 0
    mc.PUT_PRIORITY = cfg.get('PUT_PRIORITY', 'calls_first')
    mc.EARN_SUPP_PUT = cfg.get('EARN_SUPP_PUT', True)
    mc.HOLD_DAYS = cfg.get('HOLD_DAYS', 15)
    mc.HARD_SELL_LOSS = cfg.get('HARD_SELL_LOSS', -0.50)
    mc.NET_HARD_SELL = mc.HARD_SELL_LOSS + mc.SLIP_ENTRY + mc.SLIP_HARD

    # CALL side
    if 'TP_BASE' in cfg or 'CALL_TP_BASE' in cfg:
        v = cfg.get('TP_BASE', cfg.get('CALL_TP_BASE'))
        mc.TP_BASE = v
        mc.NET_TP_BASE = v + mc.SLIP_ENTRY + mc.SLIP_TP
        mc.TP_SIGMA_BASE = v * mc.PREMIUM_MULT / mc.DELTA
    if 'TP_STRESS' in cfg:
        mc.TP_STRESS = cfg['TP_STRESS']
        mc.NET_TP_STRESS = cfg['TP_STRESS'] + mc.SLIP_ENTRY + mc.SLIP_TP
        mc.TP_SIGMA_STRESS = cfg['TP_STRESS'] * mc.PREMIUM_MULT / mc.DELTA
    if 'SL_BASE' in cfg or 'CALL_SL_BASE' in cfg:
        v = cfg.get('SL_BASE', cfg.get('CALL_SL_BASE'))
        mc.SL_BASE = v
        mc.NET_SL_BASE = v + mc.SLIP_ENTRY + mc.SLIP_SL
        mc.SL_SIGMA_BASE = abs(v) * mc.PREMIUM_MULT / mc.DELTA
    if 'SL_STRESS' in cfg:
        mc.SL_STRESS = cfg['SL_STRESS']
        mc.NET_SL_STRESS = cfg['SL_STRESS'] + mc.SLIP_ENTRY + mc.SLIP_SL
        mc.SL_SIGMA_STRESS = abs(cfg['SL_STRESS']) * mc.PREMIUM_MULT / mc.DELTA
    if 'BREADTH_THRESHOLD' in cfg: mc.BREADTH_THRESHOLD = cfg['BREADTH_THRESHOLD']
    if 'TIER_ALLOC' in cfg: mc.TIER_ALLOC = dict(cfg['TIER_ALLOC'])
    if 'PUT_TIER_ALLOC' in cfg: mc.PUT_TIER_ALLOC = dict(cfg['PUT_TIER_ALLOC'])
    if 'F3F_PUT_FLOOR' in cfg:  mc.F3F_PUT_FLOOR = cfg['F3F_PUT_FLOOR']
    if 'F3F_CALL_FLOOR' in cfg: mc.F3F_CALL_FLOOR = cfg['F3F_CALL_FLOOR']
    if 'MAX_POSITIONS' in cfg: mc.MAX_POSITIONS = cfg['MAX_POSITIONS']
    if 'N_ITER' in cfg: mc.N_ITER = cfg['N_ITER']
    if 'COLLISION_MODES' in cfg: mc.COLLISION_MODES = list(cfg['COLLISION_MODES'])


def build_h5_candidate(cand):
    """Build full config: V1_BASE + H4 winner + H5 candidate overrides."""
    cfg = dict(V1_BASE)
    cfg.update(H4_WIN)
    cfg['N_ITER'] = 500
    p = cand['params']
    cfg['HOLD_DAYS'] = p['HOLD_DAYS']
    cfg['HARD_SELL_LOSS'] = p['HARD_SELL_LOSS']
    cfg['PUT_TP'] = p['PUT_TP']
    cfg['TP_BASE'] = p['CALL_TP_BASE']
    cfg['SL_BASE'] = p['CALL_SL_BASE']
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

    top_path = os.path.join(os.path.dirname(__file__), 'phase_h5_top_candidates.json')
    with open(top_path) as f:
        top_k = json.load(f)
    top_n = int(os.environ.get('PHASE_TOP_N', '3'))
    candidates = top_k[:top_n]

    version = AlgorithmVersion.get_active_scores_version()
    print(f'\nPhase H5 validation: top-{top_n} + V1 + V0 at N=500 × {len(VALIDATION_WINDOWS)} windows')

    results = {}

    print('\n--- V0_PROD ---')
    results['V0_PROD'] = run_canonical(V0_PROD, version, 'V0 PROD')

    print('\n--- V1_BASELINE ---')
    results['V1_BASELINE'] = run_canonical(V1_BASELINE, version, 'V1 BASELINE')

    for i, cand in enumerate(candidates):
        cfg = build_h5_candidate(cand)
        label = f'H5_CAND_{i+1} (util={cand["utility"]:+.3f})'
        print(f'\n--- {label} ---')
        print(f'    Params: {cand["params"]}')
        results[f'cand_{i+1}'] = run_canonical(cfg, version, label)

    out_path = os.path.join(os.path.dirname(__file__), 'phase_h5_results.json')
    with open(out_path, 'w') as f:
        clean = {k: {w: {m: dict(r) for m, r in modes.items()} for w, modes in win_res.items()}
                 for k, win_res in results.items()}
        clean['__candidates__'] = candidates
        json.dump(clean, f, indent=2, default=str)

    print('\n' + '=' * 110)
    print('PHASE H5 VALIDATION RESULTS')
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
