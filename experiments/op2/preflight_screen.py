"""
Phase OP2 — pre-flight deterministic screen across both 30 and 15 DTE.

Goal: surface obvious losers cheaply before paying canonical MC compute.
Uses backtest_cascade.py / backtest_cascade_15dte.py (both option-aware as of
2026-04-30) on a single window (22-now compound, the headline OP1b gate).

Each candidate runs in-process via run_cascade_backtest(cfg=...) with
monkey-patched module-level constants for things not exposed via cfg
(DD_CIRCUIT_BREAKER, F3F floors, HARD_SELL_LOSS, MAX_POSITIONS).

Outputs:
- JSON: experiments/op2/preflight_results.json
- Stdout: ranked table by 22-now compound

NOT a ship gate. Top-K candidates from this screen advance to canonical MC
validation (N=300+ × 8 windows) which is the ship-grade harness.

Usage:
    PYTHONIOENCODING=utf-8 python experiments/op2/preflight_screen.py
"""
import json
import os
import sys
import time
import importlib
from contextlib import contextmanager
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Window: 22-now compound is the OP1b headline gate.
FROM_DATE = date(2022, 1, 1)
TO_DATE   = date.today()


@contextmanager
def _patched(module, **overrides):
    """Temporarily patch module-level constants and restore after exit."""
    saved = {k: getattr(module, k) for k in overrides if hasattr(module, k)}
    for k, v in overrides.items():
        setattr(module, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(module, k, v)


def _build_cfg(alloc=None, put_alloc=None, f3f=None, **extras):
    """Construct cfg dict for run_cascade_backtest."""
    cfg = {}
    if alloc:
        # Pass tier_alloc as full dict — cfg key 'tier_alloc' is honored
        cfg['tier_alloc'] = alloc
    if put_alloc:
        cfg['put_tier_alloc'] = put_alloc
    if f3f:
        cfg.update(f3f)
    cfg.update(extras)
    return cfg


# ── Candidate library ──────────────────────────────────────────────────
# Each candidate spec: dte ∈ {'30','15'}, optional patches dict (module
# constants), optional cfg dict (override cfg keys honored by run_cascade).
CANDIDATES = {
    # ── 30 DTE ──────────────────────────────────────────────────────────
    '30_baseline':       {'dte': '30'},
    '30_DD55':           {'dte': '30', 'patches': {'DD_CIRCUIT_BREAKER': 0.55}},
    '30_DD50':           {'dte': '30', 'patches': {'DD_CIRCUIT_BREAKER': 0.50}},
    '30_HARD35':         {'dte': '30', 'cfg': {'net_hard': -0.365}},  # -0.35 gross + slippage
    '30_ULTRA13_TOP10':  {'dte': '30', 'cfg_alloc': {
                              '95+': 0.13, '85-94': 0.10, '80-84': 0.12,
                              '75-79': 0.12, '70-74': 0.0,
                          }},
    '30_combo_DD55_U13': {'dte': '30',
                          'patches': {'DD_CIRCUIT_BREAKER': 0.55},
                          'cfg_alloc': {
                              '95+': 0.13, '85-94': 0.10, '80-84': 0.12,
                              '75-79': 0.12, '70-74': 0.0,
                          }},

    # ── 15 DTE ──────────────────────────────────────────────────────────
    '15_baseline':       {'dte': '15'},
    '15_DD50':           {'dte': '15', 'patches': {'DD_CIRCUIT_BREAKER': 0.50}},
    '15_DD45':           {'dte': '15', 'patches': {'DD_CIRCUIT_BREAKER': 0.45}},
    '15_HARD35':         {'dte': '15', 'cfg': {'net_hard': -0.365}},  # -0.35 gross + slippage
    '15_F3F30':          {'dte': '15', 'cfg': {'f3f_call_floor': 0.30, 'f3f_put_floor': 0.30}},
    '15_ULTRA13_TOP10':  {'dte': '15', 'cfg_alloc': {
                              '95+': 0.13, '85-94': 0.10, '80-84': 0.12,
                              '75-79': 0.12, '70-74': 0.0,
                          }},
    '15_combo_DD50_U13': {'dte': '15',
                          'patches': {'DD_CIRCUIT_BREAKER': 0.50},
                          'cfg_alloc': {
                              '95+': 0.13, '85-94': 0.10, '80-84': 0.12,
                              '75-79': 0.12, '70-74': 0.0,
                          }},
}


def get_active_version_id():
    from database.models.core import AlgorithmVersion
    return AlgorithmVersion.get_active_scores_version().id


def run_one(name, spec):
    if spec['dte'] == '30':
        import backtest_cascade as bt
    else:
        import backtest_cascade_15dte as bt

    patches = spec.get('patches') or {}
    cfg = {}
    if 'cfg_alloc' in spec:
        cfg['tier_alloc'] = spec['cfg_alloc']
    if 'cfg_put_alloc' in spec:
        cfg['put_tier_alloc'] = spec['cfg_put_alloc']
    cfg.update(spec.get('cfg') or {})

    t0 = time.time()
    with _patched(bt, **patches):
        try:
            res = bt.run_cascade_backtest(
                version_id=get_active_version_id(),
                from_date=FROM_DATE,
                to_date=TO_DATE,
                initial=50_000,
                verbose=False,
                cfg=cfg,
            )
        except Exception as e:
            return {'name': name, 'dte': spec['dte'], 'error': str(e)}
    elapsed = time.time() - t0

    if not res or 'final_equity' not in res:
        return {'name': name, 'dte': spec['dte'], 'error': 'empty result'}

    # Per-side TP rates from trade log
    log = res.get('trade_log', [])
    closed = [t for t in log if t.get('outcome') in ('tp', 'sl', 'hard')]
    calls  = [t for t in closed if t.get('side') == 'call']
    puts   = [t for t in closed if t.get('side') == 'put']
    c_tp = sum(1 for t in calls if t['outcome'] == 'tp')
    p_tp = sum(1 for t in puts  if t['outcome'] == 'tp')

    return {
        'name':          name,
        'dte':           spec['dte'],
        'patches':       {k: v for k, v in patches.items()},
        'cfg':           cfg,
        'final_eq':      res['final_equity'],
        'max_dd_pct':    res['max_dd'] * 100,
        'closed':        len(closed),
        'calls':         len(calls),
        'puts':          len(puts),
        'call_tp_pct':   100.0 * c_tp / len(calls) if calls else None,
        'put_tp_pct':    100.0 * p_tp / len(puts)  if puts  else None,
        'elapsed_sec':   round(elapsed, 1),
    }


def main():
    print(f"OP2 PRE-FLIGHT  ·  window {FROM_DATE} -> {TO_DATE}  ·  v{get_active_version_id()}")
    print("Each candidate runs the full deterministic backtest (option-aware).")
    print()

    results = []
    for name, spec in CANDIDATES.items():
        r = run_one(name, spec)
        if 'error' in r:
            print(f"  [{name:<22}] ERROR: {r['error']}")
        else:
            mult = r['final_eq'] / 50_000
            print(f"  [{name:<22}] dte={r['dte']}  eq=${r['final_eq']:>16,.0f}  "
                  f"({mult:>9.2e}×)  DD={r['max_dd_pct']:>5.1f}%  "
                  f"C/P TP=({r['call_tp_pct'] or 0:>4.1f}%/{r['put_tp_pct'] or 0:>4.1f}%)  "
                  f"[{r['elapsed_sec']}s]")
        results.append(r)
        sys.stdout.flush()

    out_path = os.path.join(ROOT, 'experiments', 'op2', 'preflight_results.json')
    with open(out_path, 'w') as fh:
        json.dump({
            'window':     f'{FROM_DATE} to {TO_DATE}',
            'candidates': results,
        }, fh, indent=2, default=str)
    print(f"\nSaved -> {out_path}")

    # ── Ranked table ───────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"OP2 PRE-FLIGHT — 22-now compound (deterministic, option-aware)")
    print("=" * 110)
    valid = [r for r in results if 'error' not in r]
    valid.sort(key=lambda r: (-r['final_eq']))

    print(f"{'rank':>4}  {'name':<22} {'dte':>3}  {'final eq':>16}  "
          f"{'mult':>10}  {'maxDD':>6}  {'CTP%':>5}  {'PTP%':>5}  {'closed':>6}")
    print('-' * 110)
    for i, r in enumerate(valid, 1):
        mult = r['final_eq'] / 50_000
        ctp = r['call_tp_pct'] or 0
        ptp = r['put_tp_pct'] or 0
        print(f"{i:>4}  {r['name']:<22} {r['dte']:>3}  ${r['final_eq']:>14,.0f}  "
              f"{mult:>10.2e}  {r['max_dd_pct']:>5.1f}%  {ctp:>4.1f}%  {ptp:>4.1f}%  "
              f"{r['closed']:>6,}")


if __name__ == '__main__':
    main()
