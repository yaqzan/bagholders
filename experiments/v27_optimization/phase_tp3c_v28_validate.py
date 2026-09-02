"""Phase TP3C — post-ship canonical MC validation of v28 earnings boost.

Compares v27 (ad02704, baseline pre-boost) vs v28 (e3c8678, score-stage
earnings boost shipped). Both versions live in the DB; we explicitly
target each via the `version` argument to monte_carlo.run_window.

Same 8 windows × 3 modes × N=200 used by Phase H5b. Required gates:
  - 22-now Realistic compound v28 ≥ v27
  - Every Realistic-mean window within ±25%
  - All Conservative DD-C ≤ 80%
  - 0% collapse on every (window × mode) cell

Compared against the runtime-wrapper validation snapshot to confirm the
score-stage migration produces equivalent portfolio-level results.
"""
from __future__ import annotations
import json, os, sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc
from database.models.core import AlgorithmVersion
from experiments.v27_optimization.phase_h5_validate import (
    apply_cfg, V1_BASE, H4_WIN, VALIDATION_WINDOWS,
)


SHIPPED_H5 = {
    **V1_BASE, **H4_WIN,
    'HOLD_DAYS': 15, 'HARD_SELL_LOSS': -0.40, 'PUT_TP': 0.35,
    'TP_BASE': 0.35, 'SL_BASE': -0.30,
    'N_ITER': 200,
}


def run_canonical(cfg, version, label):
    apply_cfg(cfg)
    print(f'\n{"=" * 100}\nCONFIG: {label}  (version: id={version.id})\n{"=" * 100}')
    out = {}
    for wlabel, d_start, d_end in VALIDATION_WINDOWS:
        wr = mc.run_window(wlabel, d_start, d_end, version)
        out[wlabel] = wr
    return out


def main():
    # Resolve both versions explicitly. v27 = ad02704; v28 = e3c8678.
    v27 = AlgorithmVersion.get_or_none(AlgorithmVersion.commit_hash.startswith('ad02704'))
    v28 = AlgorithmVersion.get_or_none(AlgorithmVersion.commit_hash.startswith('e3c8678'))
    if v27 is None:
        # Fall back to id lookup
        v27 = AlgorithmVersion.get_or_none(AlgorithmVersion.id == 27)
    if v28 is None:
        v28 = AlgorithmVersion.get_active_scores_version()

    print(f'\nPhase TP3C v28 validation @ N=200')
    print(f'  v27 (baseline): id={v27.id if v27 else "?"} hash={getattr(v27, "commit_hash", "?")[:8]}')
    print(f'  v28 (boost):    id={v28.id if v28 else "?"} hash={getattr(v28, "commit_hash", "?")[:8]}')

    results = {}
    print('\n--- v27 (baseline, no boost in DB) ---')
    results['v27'] = run_canonical(SHIPPED_H5, v27, 'v27 baseline (ad02704)')
    print('\n--- v28 (boost baked into scores) ---')
    results['v28'] = run_canonical(SHIPPED_H5, v28, 'v28 boost shipped (e3c8678)')

    out_path = os.path.join(os.path.dirname(__file__), 'phase_tp3c_v28_validate_results.json')
    with open(out_path, 'w') as f:
        clean = {k: {w: {m: dict(r) for m, r in modes.items()} for w, modes in win_res.items()}
                 for k, win_res in results.items()}
        json.dump(clean, f, indent=2, default=str)
    print(f'\n[saved] {out_path}')

    print('\n' + '=' * 110)
    print('v28 vs v27 — RETURN COMPARISON')
    print('=' * 110)
    print(f"\n{'Window':<10}  {'v27 Real %':>20}  {'v28 Real %':>20}  {'Δ vs v27':>14}")
    print('-' * 75)
    floor_breach = False; any_collapse = False; max_regression = 0; worst_w = None
    for wlabel, _, _ in VALIDATION_WINDOWS:
        s = results['v27'][wlabel]['realistic']['mean_ret']
        b = results['v28'][wlabel]['realistic']['mean_ret']
        if (1 + s/100) > 0:
            d = ((1 + b/100) / (1 + s/100) - 1) * 100
            if d < max_regression:
                max_regression = d; worst_w = wlabel
        else:
            d = float('inf')
        flag = ' GATE!' if abs(d) > 25 else ''
        print(f'{wlabel:<10}  {s:>+19.1f}%  {b:>+19.1f}%  {d:>+12.1f}%{flag}')

    print(f"\n{'Window':<10}  {'v27 DD-C':>12}  {'v28 DD-C':>12}")
    for wlabel, _, _ in VALIDATION_WINDOWS:
        s = results['v27'][wlabel]['conservative']['worst_dd']
        b = results['v28'][wlabel]['conservative']['worst_dd']
        if b > 80: floor_breach = True
        flag = ' BREACH!' if b > 80 else ''
        print(f'{wlabel:<10}  {s:>11.1f}%  {b:>11.1f}%{flag}')

    for wlabel, _, _ in VALIDATION_WINDOWS:
        for m in ['conservative', 'realistic', 'optimistic']:
            if results['v28'][wlabel][m].get('p_coll', 0) > 0:
                any_collapse = True

    base_22now = results['v27']['22-now']['realistic']['mean_ret']
    cand_22now = results['v28']['22-now']['realistic']['mean_ret']
    gate_22now = cand_22now >= base_22now
    gate_25 = max_regression >= -25

    print('\n' + '=' * 110)
    print('SHIP GATE DECISION (v28 vs v27)')
    print('=' * 110)
    print(f'  22-now Realistic compound: v27={base_22now:+.1f}%  v28={cand_22now:+.1f}%  PASS={gate_22now}')
    print(f'  Worst window regression: {max_regression:+.1f}% on {worst_w}  PASS={gate_25}')
    print(f'  Conservative DD-C floor (80%): PASS={not floor_breach}')
    print(f'  No collapse on any cell: PASS={not any_collapse}')
    all_pass = gate_22now and gate_25 and not floor_breach and not any_collapse
    print(f'\nALL GATES PASS: {all_pass}')


if __name__ == '__main__':
    main()
