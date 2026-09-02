"""Ship-time helper — applies updates given Phase D winner.

Reads `phase_d_results.jsonl` and prints (a) the file changes needed to ship,
(b) verification commands. Does NOT modify files — outputs a check-list that
the operator (or assistant) applies.

Run after Phase D completes:
  PYTHONIOENCODING=utf-8 python -u experiments/saw_put_ucurve_15dte/ship_helper.py
"""
from __future__ import annotations
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PHASE_D = os.path.join(ROOT, 'experiments', 'saw_put_ucurve_15dte', 'phase_d_results.jsonl')


def main():
    if not os.path.exists(PHASE_D):
        print(f"ERROR: {PHASE_D} not found")
        sys.exit(1)
    base = winner = None
    with open(PHASE_D) as f:
        for line in f:
            r = json.loads(line)
            if r['name'] == 'baseline':
                base = r
            else:
                winner = r
    if winner is None:
        print("ERROR: no winner in phase_d_results.jsonl")
        sys.exit(1)
    p = winner['params']
    g = winner['gates']

    print("=" * 70)
    print(f"PHASE D WINNER: {winner['name']}")
    print("=" * 70)
    print(f"Params: shape={p['shape']}  midpoint={p['midpoint']}  "
          f"halfwidth={p['half_width']}  floor={p['floor']}  "
          f"ceil={p['ceil']}  power_k={p['power_k']}")
    print(f"\nT-Gate verdict: {'PASS — SHIP' if g['all_T_pass'] else 'FAIL — DO NOT SHIP'}")
    print(f"  Δ5y_dd  = {g['dd_5y_delta_pp']:+.2f}pp")
    print(f"  Δ22n_dd = {g['dd_22n_delta_pp']:+.2f}pp")
    print(f"  OOM_Δ   = {g['compound_oom_diff']:+.2f}")
    print(f"  T4={g['T4_5y_dd_pass']} T5={g['T5_annual_dd_pass']} "
          f"T6={g['T6_collapse_pass']} T7={g['T7_compound_oom_pass']}")

    if not g['all_T_pass']:
        print(f"\n  STOP — Phase D failed T-gates. Document as NULL RESULT in known-issues.md.")
        return

    print("\n" + "=" * 70)
    print("SHIP CHECK-LIST (apply atomically, then run drift-guard + registry test):")
    print("=" * 70)

    # 1. strategy_config.py
    sig_k = {1.5:3.0, 2.0:5.0, 2.5:7.0, 3.0:9.0, 4.0:12.0}.get(p['power_k'], 5.0)
    print(f"\n[1] strategy_config.py STRATEGY_15DTE — flip _ENABLED + winning params:")
    print(f"    SAW_PUT_UCURVE_ENABLED=True,")
    print(f"    SAW_PUT_UCURVE_SHAPE='{p['shape']}',")
    print(f"    SAW_PUT_UCURVE_MIDPOINT={float(p['midpoint'])},")
    print(f"    SAW_PUT_UCURVE_HALFWIDTH={float(p['half_width'])},")
    print(f"    SAW_PUT_UCURVE_FLOOR={float(p['floor'])},")
    print(f"    SAW_PUT_UCURVE_CEIL={float(p['ceil'])},")
    print(f"    SAW_PUT_UCURVE_POWER={float(p['power_k'])},")
    print(f"    SAW_PUT_UCURVE_K={float(sig_k)},")

    # 2. tests/test_strategy_config_drift.py
    print(f"\n[2] tests/test_strategy_config_drift.py:")
    print(f"    - REMOVE the 'SAW Put U-curve: intentionally NOT wired in mc15' block")
    print(f"      (currently lines ~338-355 in check_15dte()) including the two asserts.")
    print(f"    - ADD 8 SAW pairs to pairs_mc15 (mirror pairs_mc layout, lines 102-109).")
    print(f"    - ADD 8 SAW pairs to pairs_bc15 (mirror pairs_bc, lines 173-180).")

    # 3. mechanism_registry.py
    print(f"\n[3] mechanism_registry.py SAW_PUT_UCURVE entry:")
    print(f"    - dte_15_status='disabled' -> 'enabled'")
    print(f"    - dte_15_wiring_mode='not_wired' -> remove (or set to None)")
    print(f"    - dte_15_reason: replace with ship date + headline metric")
    print(f"    - engine_files_15=() -> ('monte_carlo_15dte.py', 'backtest_cascade_15dte.py')")
    print(f"    - ship_date_15='2026-05-08' (or today)")

    # 4. Verification
    print(f"\n[4] Verify (in this order, all must pass):")
    print(f"    python tests/test_strategy_config_drift.py")
    print(f"    python tests/test_mechanism_registry.py")
    print(f"    PYTHONIOENCODING=utf-8 python trader.py alloc 50000 2>&1 | grep -i saw")
    print(f"    PYTHONIOENCODING=utf-8 python trader.py temporal-refresh   # ~5min")

    # 5. Docs
    print(f"\n[5] Doc updates (bundle into one commit AFTER all the above):")
    print(f"    - .claude/docs/known-issues.md CURRENT SHIP STATE (15 DTE SAW row)")
    print(f"    - .claude/docs/known-issues.md CLOSED — SHIPPED timeline")
    print(f"    - .claude/docs/version-history.md (new SAW 15 DTE section)")
    print(f"    - .claude/docs/trading-strategy.md (15 DTE Variant section)")
    print(f"    - MEMORY.md index pointer")


if __name__ == '__main__':
    main()
