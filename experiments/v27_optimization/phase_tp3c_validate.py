"""Phase TP3C — canonical 3-mode MC validation of earnings boost (W5_B0.50)

Compares shipped V27 production (no boost) vs. earnings boost ON
(W=5, MAX=0.50, calls admit / puts no-admit, calibrated lift table from Phase 3B).

Same 8 validation windows × 3 collision modes × N=200 used by Phase H5b.
N=200 is the screening level — if gates pass, optionally re-validate at N=500.

Ship gates (per CLAUDE.md autonomous-ship protocol):
  - 22-now Realistic compound ≥ shipped baseline
  - Every Realistic-mean window within ±25% of shipped
  - All Conservative DD-C ≤ 80%
  - 0% collapse on every (window × mode) cell

Run: python experiments/v27_optimization/phase_tp3c_validate.py
"""
from __future__ import annotations
import json, os, sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Disable boost initially so import doesn't run with it active
os.environ['EARN_BOOST_ENABLED'] = '0'

import monte_carlo as mc
import experiments.v27_optimization.earn_boost_runtime as ebr
from experiments.v27_optimization.phase_h5_validate import (
    apply_cfg, V1_BASE, V0_PROD, V1_BASELINE, H4_WIN, VALIDATION_WINDOWS,
)


# ── Shipped H5_HOLD15_H40 = current production baseline ─────────────────────
SHIPPED_H5 = {
    **V1_BASE, **H4_WIN,
    'HOLD_DAYS': 15, 'HARD_SELL_LOSS': -0.40, 'PUT_TP': 0.35,
    'TP_BASE': 0.35, 'SL_BASE': -0.30,
    'N_ITER': 200,
}


# ── Boost config (Phase 3C W5_B0.50 winner) ─────────────────────────────────
def enable_boost():
    ebr.EARN_BOOST_ENABLED        = True
    ebr.EARN_BOOST_WINDOW         = 5
    ebr.EARN_BOOST_MAX            = 0.50
    ebr.EARN_BOOST_LIFT_NORM_CALL = 22.3
    ebr.EARN_BOOST_LIFT_NORM_PUT  = 16.3
    ebr.EARN_BOOST_MIN_N          = 10
    ebr.EARN_BOOST_PUT_ADMIT      = False
    ebr._STRENGTH_MAP_CACHE       = None  # force rebuild


def disable_boost():
    ebr.EARN_BOOST_ENABLED  = False
    ebr._STRENGTH_MAP_CACHE = None


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
    print(f'\nPhase TP3C: earnings boost validation @ N=200 vs shipped H5_HOLD15_H40')

    results = {}

    # ── Baseline: shipped production WITH earnings boost OFF ───────────────
    disable_boost()
    print('\n--- SHIPPED (boost OFF) ---')
    results['SHIPPED'] = run_canonical(SHIPPED_H5, version, 'SHIPPED H5_HOLD15_H40 (boost off)')

    # ── Candidate: shipped production WITH boost ON (W=5, MAX=0.50) ─────────
    enable_boost()
    print('\n--- BOOST_W5_B050 (boost ON) ---')
    results['BOOST_W5_B050'] = run_canonical(SHIPPED_H5, version, 'BOOST W=5 MAX=0.50 (calls admit, puts no-admit)')

    # ── Save ──────────────────────────────────────────────────────────────
    out_path = os.path.join(os.path.dirname(__file__), 'phase_tp3c_validate_results.json')
    with open(out_path, 'w') as f:
        clean = {k: {w: {m: dict(r) for m, r in modes.items()} for w, modes in win_res.items()}
                 for k, win_res in results.items()}
        json.dump(clean, f, indent=2, default=str)
    print(f'\n[saved] {out_path}')

    # ── Side-by-side report ───────────────────────────────────────────────
    print('\n' + '=' * 110)
    print('PHASE TP3C VALIDATION — boost vs shipped baseline')
    print('=' * 110)

    print(f"\n{'Window':<10}  {'SHIPPED Real %':>20}  {'BOOST Real %':>20}  {'Δ vs SHIPPED':>14}")
    print('-' * 75)
    for wlabel, _, _ in VALIDATION_WINDOWS:
        s = results['SHIPPED'][wlabel]['realistic']['mean_ret']
        b = results['BOOST_W5_B050'][wlabel]['realistic']['mean_ret']
        delta_pct = ((1 + b/100) / (1 + s/100) - 1) * 100 if (1 + s/100) > 0 else float('inf')
        # For huge numbers (5y compound), use log ratio
        flag = ''
        if abs(delta_pct) > 25:
            flag = ' GATE!'
        print(f'{wlabel:<10}  {s:>+19.1f}%  {b:>+19.1f}%  {delta_pct:>+12.1f}%{flag}')

    print(f"\n{'Window':<10}  {'SHIPPED DD-C':>14}  {'BOOST DD-C':>14}  {'Floor':>6}")
    print('-' * 60)
    floor_breach = False
    for wlabel, _, _ in VALIDATION_WINDOWS:
        s = results['SHIPPED'][wlabel]['conservative']['worst_dd']
        b = results['BOOST_W5_B050'][wlabel]['conservative']['worst_dd']
        flag = ' BREACH!' if b > 80 else ''
        if b > 80: floor_breach = True
        print(f'{wlabel:<10}  {s:>13.1f}%  {b:>13.1f}%  {"80%":>6}{flag}')

    print(f"\n{'Window':<10}  {'BOOST P(coll)':>14}")
    print('-' * 30)
    any_collapse = False
    for wlabel, _, _ in VALIDATION_WINDOWS:
        for m in ['conservative', 'realistic', 'optimistic']:
            p = results['BOOST_W5_B050'][wlabel][m].get('p_coll', 0)
            if p > 0:
                any_collapse = True
                print(f'{wlabel:<10}  {m:<13}  {p:.3f}')

    print(f"\n{'Side':>5}  {'TP rate by window':<60}")
    print('-' * 75)
    for wlabel, _, _ in VALIDATION_WINDOWS:
        s_call = results['SHIPPED'][wlabel]['realistic'].get('call_tp', 0)
        b_call = results['BOOST_W5_B050'][wlabel]['realistic'].get('call_tp', 0)
        s_put  = results['SHIPPED'][wlabel]['realistic'].get('put_tp', 0)
        b_put  = results['BOOST_W5_B050'][wlabel]['realistic'].get('put_tp', 0)
        s_calln = results['SHIPPED'][wlabel]['realistic'].get('call_trades', 0)
        b_calln = results['BOOST_W5_B050'][wlabel]['realistic'].get('call_trades', 0)
        s_putn  = results['SHIPPED'][wlabel]['realistic'].get('put_trades', 0)
        b_putn  = results['BOOST_W5_B050'][wlabel]['realistic'].get('put_trades', 0)
        print(f"{wlabel:<10} CALL ship={s_call:5.1f}% (n={s_calln:.0f}) -> boost={b_call:5.1f}% (n={b_calln:.0f}) | "
              f"PUT ship={s_put:5.1f}% (n={s_putn:.0f}) -> boost={b_put:5.1f}% (n={b_putn:.0f})")

    # ── Ship gate decision ────────────────────────────────────────────────
    print('\n' + '=' * 110)
    print('SHIP GATE DECISION')
    print('=' * 110)
    base_22now = results['SHIPPED']['22-now']['realistic']['mean_ret']
    cand_22now = results['BOOST_W5_B050']['22-now']['realistic']['mean_ret']
    gate_22now = cand_22now >= base_22now
    print(f'  22-now Realistic compound: SHIPPED={base_22now:+.1f}%  BOOST={cand_22now:+.1f}%  '
          f'PASS={gate_22now}')

    max_regression = 0; worst_w = None
    for wlabel, _, _ in VALIDATION_WINDOWS:
        s = results['SHIPPED'][wlabel]['realistic']['mean_ret']
        b = results['BOOST_W5_B050'][wlabel]['realistic']['mean_ret']
        if (1 + s/100) <= 0:
            continue
        d = ((1 + b/100) / (1 + s/100) - 1) * 100
        if d < max_regression:
            max_regression = d; worst_w = wlabel
    gate_25 = max_regression >= -25
    print(f'  Worst window regression: {max_regression:+.1f}% on {worst_w}  PASS={gate_25}')

    print(f'  Conservative DD-C floor (80%): PASS={not floor_breach}')
    print(f'  No collapse on any cell: PASS={not any_collapse}')

    all_pass = gate_22now and gate_25 and not floor_breach and not any_collapse
    print(f'\nALL GATES PASS: {all_pass}')
    if all_pass:
        print('-> SHIPPABLE. Proceed to extend wrapper to backtest_cascade.py + trader.py + api.py + frontend.')
    else:
        print('-> NOT SHIPPABLE. Investigate failed gate(s).')


if __name__ == '__main__':
    main()
