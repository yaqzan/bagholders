"""Phase EVS — canonical 3-mode MC validation: v29 (V6 gradient) vs v28 (binary suppression).

Ship gate after recalculate. v29 = volume amplifier earnings volume suppression
replaced binary ±1d null with pre-only log-gradient (W=2, M=1.0).

Per-trade A/B at 5y already showed:
  - 95+ N 38 -> 46 (+21%) at WR15 +1.4pp
  - 90+ +1.1pp on +13% N
  - All other tiers neutral / put side indifferent

The v24 reversion lesson: per-trade improvements are necessary but not
sufficient. This script runs canonical 3-mode MC against the actual DB
score rows for both versions and compares.

Decision gates (must clear ALL):
  1. 5y Realistic compound >= V28 baseline
  2. Every annual Realistic window within +/-25% of V28
  3. All Conservative DD-C <= 80%
  4. 0% collapse on every (window x mode) cell

If gate fails: revert via `trader revert v28`.

Usage:
    MC_NO_MP=1 N_ITER_OVERRIDE=200 python -m experiments.v28_optimization.phase_evs_canonical_mc
"""
from __future__ import annotations
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# monte_carlo.py handles stdout encoding on import — do not double-wrap
import monte_carlo as mc
from database.models.core import AlgorithmVersion


WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('dip',    date(2025, 11, 1), date(2026, 4, 24)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
    ('5y',     date(2021, 1, 1),  date(2026, 4, 15)),
]


def run_one_version(version, label):
    """Run canonical MC across all WINDOWS for a single algorithm version."""
    print('\n' + '=' * 110)
    print(f"  VERSION: {label}  (id={version.id}  commit={version.git_commit})")
    print('=' * 110, flush=True)
    out = {}
    for w_label, d_start, d_end in WINDOWS:
        out[w_label] = mc.run_window(w_label, d_start, d_end, version)
    return out


def fmt_pct(x):
    return f"{x:+,.1f}%"


def compare(v28_results, v29_results):
    print('\n' + '=' * 110)
    print("  COMPARISON: v29 (V6 gradient) vs v28 (binary suppression)")
    print('=' * 110)
    modes = mc.COLLISION_MODES

    # Realistic mean returns
    print(f"\n{'Window':<8}  {'v28 Real':>15}  {'v29 Real':>15}  {'delta %':>10}")
    print('-' * 60)
    fail_25pct = []
    for label, _, _ in WINDOWS:
        v28 = v28_results[label]['realistic']
        v29 = v29_results[label]['realistic']
        v28r = v28['mean_ret']
        v29r = v29['mean_ret']
        # Compute relative change in (1+ret)
        if abs(1 + v28r/100) < 1e-9:
            delta = float('nan')
        else:
            delta = ((1 + v29r/100) / (1 + v28r/100) - 1) * 100
        marker = ''
        if abs(delta) > 25:
            marker = '  <-- 25% gate breach'
            fail_25pct.append((label, delta))
        print(f"{label:<8}  {fmt_pct(v28r):>15}  {fmt_pct(v29r):>15}  {delta:>+9.1f}%{marker}")

    # Conservative DD
    print(f"\n{'Window':<8}  {'v28 DD-C':>10}  {'v29 DD-C':>10}  {'breach 80%':>12}")
    print('-' * 50)
    fail_dd = []
    for label, _, _ in WINDOWS:
        v28dd = v28_results[label]['conservative']['worst_dd']
        v29dd = v29_results[label]['conservative']['worst_dd']
        breach = 'BREACH' if v29dd > 80 else ''
        if v29dd > 80:
            fail_dd.append((label, v29dd))
        print(f"{label:<8}  {v28dd:>9.1f}%  {v29dd:>9.1f}%  {breach:>12}")

    # Collapse rate
    print(f"\n{'Window':<8}  {'mode':<14}  {'v28 P(coll)':>11}  {'v29 P(coll)':>11}")
    print('-' * 55)
    fail_coll = []
    for label, _, _ in WINDOWS:
        for mode in modes:
            v28c = v28_results[label][mode]['p_coll']
            v29c = v29_results[label][mode]['p_coll']
            if v29c > 0:
                fail_coll.append((label, mode, v29c))
                print(f"{label:<8}  {mode:<14}  {v28c:>10.1f}%  {v29c:>10.1f}%  <-- collapse")

    # 5y compound headline
    v28_5y = v28_results['5y']['realistic']['mean_ret']
    v29_5y = v29_results['5y']['realistic']['mean_ret']
    if abs(1 + v28_5y/100) < 1e-9:
        compound_delta = float('nan')
    else:
        compound_delta = ((1 + v29_5y/100) / (1 + v28_5y/100) - 1) * 100

    print('\n' + '=' * 110)
    print(f"  HEADLINE: 5y Realistic compound  v28={v28_5y:+,.1f}%  v29={v29_5y:+,.1f}%  delta={compound_delta:+.1f}%")
    print('=' * 110)

    print("\n  GATE RESULTS:")
    print(f"    [{'PASS' if compound_delta >= 0 else 'FAIL'}] Gate 1: 5y compound >= v28 baseline")
    print(f"    [{'PASS' if not fail_25pct else 'FAIL'}] Gate 2: every annual within +/-25%")
    if fail_25pct:
        for w, d in fail_25pct:
            print(f"            BREACH at {w}: {d:+.1f}%")
    print(f"    [{'PASS' if not fail_dd else 'FAIL'}] Gate 3: all Conservative DD-C <= 80%")
    if fail_dd:
        for w, d in fail_dd:
            print(f"            BREACH at {w}: {d:.1f}%")
    print(f"    [{'PASS' if not fail_coll else 'FAIL'}] Gate 4: 0% collapse on every cell")
    if fail_coll:
        for w, m, c in fail_coll:
            print(f"            BREACH at {w}/{m}: {c:.1f}%")

    overall_pass = (compound_delta >= 0 and not fail_25pct and not fail_dd and not fail_coll)
    print(f"\n  OVERALL: {'SHIP' if overall_pass else 'REVERT'}")
    return overall_pass, compound_delta, fail_25pct, fail_dd, fail_coll


def main():
    print('=' * 110)
    print(f"  Phase EVS canonical MC: v29 (V6) vs v28 baseline")
    print(f"  N_ITER={mc.N_ITER}  modes={mc.COLLISION_MODES}")
    print('=' * 110)

    v29 = AlgorithmVersion.get(AlgorithmVersion.id == 29)
    v28 = AlgorithmVersion.get(AlgorithmVersion.id == 28)

    # Sanity: v29 commit should be 8473cba (V6 ship)
    assert v29.git_commit.startswith('8473cba'), f"Expected v29=8473cba, got {v29.git_commit}"
    assert v28.git_commit.startswith('e3c8678'), f"Expected v28=e3c8678, got {v28.git_commit}"

    # Run v28 first (cleaner cache behavior)
    v28_results = run_one_version(v28, 'v28 (binary suppression — baseline)')
    v29_results = run_one_version(v29, 'v29 (V6 gradient — ship candidate)')

    # Save raw results for forensic comparison
    out_path = os.path.join(os.path.dirname(__file__), 'phase_evs_canonical_mc_results.json')
    serializable = {
        'v28': {label: {m: {k: r[k] for k in ('mean_ret', 'worst_dd', 'p_coll')} for m, r in win.items()}
                for label, win in v28_results.items()},
        'v29': {label: {m: {k: r[k] for k in ('mean_ret', 'worst_dd', 'p_coll')} for m, r in win.items()}
                for label, win in v29_results.items()},
        'n_iter': mc.N_ITER,
    }
    with open(out_path, 'w') as f:
        json.dump(serializable, f, indent=2)
    print(f"\n[results] saved to {out_path}")

    overall_pass, compound_delta, _, _, _ = compare(v28_results, v29_results)

    sys.exit(0 if overall_pass else 1)


if __name__ == '__main__':
    main()
