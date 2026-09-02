"""Phase 15B — 15 DTE DD-reduction levers.

Anchored at the Phase 15A H5-baseline winner (TP=0.35/SL=-0.30/PUT_TP=0.35/PUT_SL=-0.20,
ultra=0.18, put_top=0.10) — which has best per-trade EV at 15 DTE — and adds three
NEW DD-reduction mechanisms:

  1. DD_CIRCUIT_BREAKER : pause new entries when running portfolio DD > X%.
                          Tested {0.0=off, 0.40, 0.50, 0.60, 0.70}.
  2. MAX_POSITIONS      : aggressive contraction {6, 8, 10, 12, 14}.
  3. F3F_PUT_FLOOR      : stronger weakness contraction {0.30, 0.40, 0.50}.
  4. F3F_CALL_FLOOR     : stronger weakness contraction {0.30, 0.40, 0.50}.

Goal: find a config that clears DD-C ≤ 80% on EVERY window while keeping the
22-now compound advantage over 30 DTE H5 (≥4×10^15%) meaningful.

Locks (winners from per-trade EV at 15 DTE):
  - TP/SL : H5 values (TP=0.35, SL=-0.30, PUT_TP=0.35, PUT_SL=-0.20)
  - Cascade alloc: ultra=0.18, top=0.12, mid=0.15, low=0.15
  - Put cascade: put_top=0.10, put_mid=0.12, put_low=0.12
  - HOLD_DAYS=7, PREMIUM_MULT=1.29, HARD=-0.45
"""
from __future__ import annotations
import json, os, sys, math
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo_15dte as mc15
from experiments import bayes_mc

bayes_mc.mc = mc15

DD_TARGET = 0.65
DD_HARD_LIMIT = 0.80


def utility_15b(window_results: dict) -> dict:
    per_window_log = {}
    log_sum = 0.0
    max_dd = 0.0
    max_coll = 0.0
    for label, r in window_results.items():
        w = bayes_mc.WINDOW_WEIGHTS.get(label, 1.0)
        ret_pct = max(-99.0, float(r['mean_ret']))
        lr = math.log1p(ret_pct / 100.0)
        log_sum += w * lr
        per_window_log[label] = lr
        max_dd = max(max_dd, float(r['worst_dd']) / 100.0)
        max_coll = max(max_coll, float(r['p_collapse']) / 100.0)

    soft_excess = max(0.0, max_dd - DD_TARGET)
    soft_penalty = 30.0 * (soft_excess ** 2) / 0.01
    hard_excess = max(0.0, max_dd - DD_HARD_LIMIT)
    hard_penalty = 200.0 * (hard_excess ** 2) / 0.01
    collapse_penalty = 1000.0 * max_coll

    utility = log_sum - soft_penalty - hard_penalty - collapse_penalty

    return {
        'utility': utility, 'log_return': log_sum,
        'log_return_weighted_sum': log_sum,
        'dd_penalty': soft_penalty + hard_penalty,
        'collapse_penalty': collapse_penalty,
        'max_dd': max_dd, 'max_collapse': max_coll,
        'per_window_log': per_window_log,
    }


bayes_mc.utility_from_results = utility_15b


def apply_15b_config(cfg: dict):
    """Apply config to monte_carlo_15dte. Locks per-trade EV winners; sweeps DD levers."""
    m = mc15
    # Lock H5 per-trade EV winners
    m.TP_BASE = 0.35
    m.SL_BASE = -0.30
    m.TP_STRESS = 0.40
    m.SL_STRESS = -0.35
    m.NET_TP_BASE   = m.TP_BASE   + m.SLIP_ENTRY + m.SLIP_TP
    m.NET_TP_STRESS = m.TP_STRESS + m.SLIP_ENTRY + m.SLIP_TP
    m.NET_SL_BASE   = m.SL_BASE   + m.SLIP_ENTRY + m.SLIP_SL
    m.NET_SL_STRESS = m.SL_STRESS + m.SLIP_ENTRY + m.SLIP_SL
    m.TP_SIGMA_BASE   = m.TP_BASE   * m.PREMIUM_MULT / m.DELTA
    m.TP_SIGMA_STRESS = m.TP_STRESS * m.PREMIUM_MULT / m.DELTA
    m.SL_SIGMA_BASE   = abs(m.SL_BASE)   * m.PREMIUM_MULT / m.DELTA
    m.SL_SIGMA_STRESS = abs(m.SL_STRESS) * m.PREMIUM_MULT / m.DELTA

    m.PUT_TP = 0.35
    m.PUT_SL = -0.20
    m.PUT_NET_TP = m.PUT_TP + m.SLIP_ENTRY + m.SLIP_TP
    m.PUT_NET_SL = m.PUT_SL + m.SLIP_ENTRY + m.SLIP_SL
    m.PUT_TP_SIGMA = m.PUT_TP * m.PREMIUM_MULT / m.DELTA
    m.PUT_SL_SIGMA = abs(m.PUT_SL) * m.PREMIUM_MULT / m.DELTA

    # Lock cascade allocations (H5 baseline values)
    m.TIER_ALLOC = {'ultra': 0.18, 'top': 0.12, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}
    m.PUT_TIER_ALLOC = {'put_top': 0.10, 'put_mid': 0.12, 'put_low': 0.12}

    # Other locks
    m.PUT_SL_HOLD_BARS_DEFAULT = 0
    m.PUT_SL_HOLD_BARS_MONDAY = 0
    m.PUT_PRIORITY = 'calls_first'
    m.EARN_SUPP_PUT = True
    m.PUT_BREADTH_MODE = 'none'

    # SWEEP AXES
    m.DD_CIRCUIT_BREAKER = cfg.get('DD_CIRCUIT_BREAKER', 0.0)
    m.MAX_POSITIONS = cfg.get('MAX_POSITIONS', 14)
    m.F3F_PUT_FLOOR = cfg.get('F3F_PUT_FLOOR', 0.50)
    m.F3F_CALL_FLOOR = cfg.get('F3F_CALL_FLOOR', 0.50)

    # Sweep knobs
    m.N_ITER = cfg['N_ITER']
    m.COLLISION_MODES = list(cfg['COLLISION_MODES'])


SCREENING_WINDOWS = [
    ('22-now', date(2022, 1, 1), date(2026, 4, 15)),
]
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}


def run_config_15b(cfg, windows=None, version=None, silent=True):
    if windows is None: windows = SCREENING_WINDOWS
    cfg2 = dict(cfg)
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    apply_15b_config(cfg2)
    if version is None:
        from database.models.core import AlgorithmVersion
        version = AlgorithmVersion.get_active_scores_version()
    out = {}
    for label, d_start, d_end in windows:
        if silent:
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                window_res = mc15.run_window(label, d_start, d_end, version)
        else:
            window_res = mc15.run_window(label, d_start, d_end, version)
        real = window_res.get('realistic', {})
        cons = window_res.get('conservative', {})
        opt  = window_res.get('optimistic', {})
        max_dd = max(float(real.get('worst_dd', 0)),
                     float(cons.get('worst_dd', 0)),
                     float(opt.get('worst_dd', 0)))
        max_coll = max(float(real.get('p_coll', 0)),
                       float(cons.get('p_coll', 0)),
                       float(opt.get('p_coll', 0)))
        out[label] = {
            'mean_ret': float(real.get('mean_ret', 0)),
            'med_ret':  float(real.get('med_ret', 0)),
            'worst_dd': max_dd,
            'mean_dd':  float(real.get('mean_dd', 0)),
            'p_collapse': max_coll,
            'call_tp':  float(real.get('call_tp', 0)),
            'put_tp':   float(real.get('put_tp', 0)),
            'call_trades': float(real.get('call_trades', 0)),
            'put_trades':  float(real.get('put_trades', 0)),
            'cons_dd':  float(cons.get('worst_dd', 0)),
            'real_dd':  float(real.get('worst_dd', 0)),
        }
    return out


bayes_mc.apply_config = apply_15b_config
bayes_mc.run_config = run_config_15b


BASE_CONFIG = {
    'DD_CIRCUIT_BREAKER': 0.0,
    'MAX_POSITIONS':      14,
    'F3F_PUT_FLOOR':      0.50,
    'F3F_CALL_FLOOR':     0.50,
    'N_ITER':             75,
    'COLLISION_MODES':    ['conservative', 'realistic', 'optimistic'],
}


PARAM_SPACE = {
    'DD_CIRCUIT_BREAKER': [0.0, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75],
    'MAX_POSITIONS':      [6, 8, 10, 12, 14],
    'F3F_PUT_FLOOR':      [0.30, 0.40, 0.50],
    'F3F_CALL_FLOOR':     [0.30, 0.40, 0.50],
}

# Strategic seeds — anchor + each lever solo + combos
SEED_CONFIGS = [
    # 1. Production 15 DTE H5 (no breaker, MaxPos=14, F3f=0.50) — anchor (DD=87%)
    {'DD_CIRCUIT_BREAKER': 0.0, 'MAX_POSITIONS': 14, 'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50},
    # 2. DD breaker @ 70% — gentle
    {'DD_CIRCUIT_BREAKER': 0.70, 'MAX_POSITIONS': 14, 'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50},
    # 3. DD breaker @ 60% — moderate
    {'DD_CIRCUIT_BREAKER': 0.60, 'MAX_POSITIONS': 14, 'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50},
    # 4. DD breaker @ 50% — aggressive
    {'DD_CIRCUIT_BREAKER': 0.50, 'MAX_POSITIONS': 14, 'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50},
    # 5. MaxPos=10 only
    {'DD_CIRCUIT_BREAKER': 0.0, 'MAX_POSITIONS': 10, 'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50},
    # 6. MaxPos=8 only
    {'DD_CIRCUIT_BREAKER': 0.0, 'MAX_POSITIONS': 8,  'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50},
    # 7. MaxPos=6 only — strong
    {'DD_CIRCUIT_BREAKER': 0.0, 'MAX_POSITIONS': 6,  'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50},
    # 8. F3F floors 0.30 only — stronger weakness contraction
    {'DD_CIRCUIT_BREAKER': 0.0, 'MAX_POSITIONS': 14, 'F3F_PUT_FLOOR': 0.30, 'F3F_CALL_FLOOR': 0.30},
    # 9. Combo: breaker 60% + MaxPos 10
    {'DD_CIRCUIT_BREAKER': 0.60, 'MAX_POSITIONS': 10, 'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50},
    # 10. Combo: breaker 60% + MaxPos 8 + F3F 0.40
    {'DD_CIRCUIT_BREAKER': 0.60, 'MAX_POSITIONS': 8, 'F3F_PUT_FLOOR': 0.40, 'F3F_CALL_FLOOR': 0.40},
    # 11. Aggressive combo: breaker 50% + MaxPos 8 + F3F 0.30
    {'DD_CIRCUIT_BREAKER': 0.50, 'MAX_POSITIONS': 8, 'F3F_PUT_FLOOR': 0.30, 'F3F_CALL_FLOOR': 0.30},
    # 12. Mid combo: breaker 70% + MaxPos 10 + F3F 0.40
    {'DD_CIRCUIT_BREAKER': 0.70, 'MAX_POSITIONS': 10, 'F3F_PUT_FLOOR': 0.40, 'F3F_CALL_FLOOR': 0.40},
]


def main():
    print('=' * 100)
    print('PHASE 15B — DD-reduction levers (DD_CIRCUIT_BREAKER + MaxPos + F3F floors)')
    print('=' * 100)
    print(f'Anchor: 15 DTE H5 (TP=0.35/SL=-0.30/PUT_TP=0.35/PUT_SL=-0.20, alloc 0.18/0.12/0.15/0.15)')
    print(f'Utility: DD_TARGET={DD_TARGET*100:.0f}%, DD_HARD={DD_HARD_LIMIT*100:.0f}%')
    print(f'Window : 22-now × N=75 × 3 modes (screening)')
    print(f'Param space: {len(PARAM_SPACE)} axes')
    for k, v in PARAM_SPACE.items():
        print(f'  {k}: {v}')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v15dte_phase_15b',
        param_space=PARAM_SPACE,
        base_config=BASE_CONFIG,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '20')),
        batch_size=3, patience=2, epsilon=0.02, bandwidth=0.30,
        kappa_init=2.0, kappa_final=1.0, windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v15dte_phase_15b', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_15b_top_candidates.json')
    with open(out_path, 'w') as f:
        payload = []
        for r in ranked[:10]:
            payload.append({
                'rank':    len(payload) + 1,
                'utility': r['utility'],
                'params':  r['params'],
                'windows': r['windows'],
                'log_return': r['util_info']['log_return'],
                'max_dd':  r['util_info']['max_dd'],
                'max_collapse': r['util_info']['max_collapse'],
            })
        json.dump(payload, f, indent=2, default=str)
    print(f'\nTop 10 saved to {out_path}')


if __name__ == '__main__':
    main()
