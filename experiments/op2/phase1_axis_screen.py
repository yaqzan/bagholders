"""
Phase OP2 Phase 1 — independent axis screen (30 DTE only).

Each axis is varied while all other params hold at baseline. Goal: identify
the 2-4 most impactful levers before combinatorial Bayesian search.

Utility = log(final_eq / 50000) − DD_PENALTY × max(0, max_dd − 0.65)²
where DD_PENALTY scales drawdown >65% quadratically (above 80% becomes
disqualifying).

Outputs:
- experiments/op2/phase1_results.json  (full per-candidate dict)
- stdout: per-axis ranked sub-table + global ranking

Usage:
    PYTHONIOENCODING=utf-8 python experiments/op2/phase1_axis_screen.py
"""
import json
import math
import os
import sys
import time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FROM_DATE = date(2022, 1, 1)
TO_DATE   = date.today()


# Each candidate: dte, optional patches (module-level monkey-patches), optional cfg
# Axis label is the 'axis' key — used to group sub-tables.
CANDIDATES = [
    # ── Axis: BASELINE (control) ───────────────────────────────────────
    {'name':'baseline',                'axis':'control', 'dte':'30'},

    # ── Axis: DD_CIRCUIT_BREAKER ───────────────────────────────────────
    {'name':'DD_45',  'axis':'DD',     'dte':'30', 'patches':{'DD_CIRCUIT_BREAKER':0.45}},
    {'name':'DD_50',  'axis':'DD',     'dte':'30', 'patches':{'DD_CIRCUIT_BREAKER':0.50}},
    {'name':'DD_55',  'axis':'DD',     'dte':'30', 'patches':{'DD_CIRCUIT_BREAKER':0.55}},
    {'name':'DD_60',  'axis':'DD',     'dte':'30', 'patches':{'DD_CIRCUIT_BREAKER':0.60}},
    {'name':'DD_65',  'axis':'DD',     'dte':'30', 'patches':{'DD_CIRCUIT_BREAKER':0.65}},

    # ── Axis: Call SL tighter (TP unchanged at 30/35; tighten SL) ──────
    # SL_SIGMA = abs(SL_PCT) * PREMIUM_MULT / DELTA. For PREMIUM_MULT=1.82:
    # SL=-0.25 → 0.910σ, SL=-0.27 → 0.983σ, SL=-0.30 (current) → 1.092σ.
    {'name':'SL_25',  'axis':'SL',     'dte':'30', 'cfg':{
        'sl_sigma_base': 0.25*1.82/0.5,    # 0.910
        'sl_sigma_stress': 0.30*1.82/0.5,  # 1.092
        'net_sl_base':   -0.25 + (-0.01) + (-0.013),
        'net_sl_stress': -0.30 + (-0.01) + (-0.013),
    }},
    {'name':'SL_27',  'axis':'SL',     'dte':'30', 'cfg':{
        'sl_sigma_base': 0.27*1.82/0.5,    # 0.983
        'sl_sigma_stress': 0.32*1.82/0.5,  # 1.165
        'net_sl_base':   -0.27 + (-0.01) + (-0.013),
        'net_sl_stress': -0.32 + (-0.01) + (-0.013),
    }},

    # ── Axis: HOLD_DAYS shorter ────────────────────────────────────────
    {'name':'HOLD_12','axis':'HOLD',   'dte':'30', 'cfg':{'hold_calendar_days':12}},
    {'name':'HOLD_13','axis':'HOLD',   'dte':'30', 'cfg':{'hold_calendar_days':13}},

    # ── Axis: MAX_POSITIONS ────────────────────────────────────────────
    {'name':'MP_10',  'axis':'MAXPOS', 'dte':'30', 'cfg':{'max_positions':10}},
    {'name':'MP_12',  'axis':'MAXPOS', 'dte':'30', 'cfg':{'max_positions':12}},
    {'name':'MP_16',  'axis':'MAXPOS', 'dte':'30', 'cfg':{'max_positions':16}},

    # ── Axis: Cascade rebalance (raise mid/low) ────────────────────────
    {'name':'CASC_18_18','axis':'CASCADE','dte':'30','cfg':{'tier_alloc':{
        '95+':0.18,'85-94':0.12,'80-84':0.18,'75-79':0.18,'70-74':0.0,
    }}},
    {'name':'CASC_20_20','axis':'CASCADE','dte':'30','cfg':{'tier_alloc':{
        '95+':0.18,'85-94':0.12,'80-84':0.20,'75-79':0.20,'70-74':0.0,
    }}},

    # ── Axis: ULTRA tier variations ────────────────────────────────────
    {'name':'ULTRA_13','axis':'ULTRA','dte':'30','cfg':{'tier_alloc':{
        '95+':0.13,'85-94':0.10,'80-84':0.15,'75-79':0.15,'70-74':0.0,
    }}},
    {'name':'ULTRA_15','axis':'ULTRA','dte':'30','cfg':{'tier_alloc':{
        '95+':0.15,'85-94':0.12,'80-84':0.15,'75-79':0.15,'70-74':0.0,
    }}},
    {'name':'ULTRA_22','axis':'ULTRA','dte':'30','cfg':{'tier_alloc':{
        '95+':0.22,'85-94':0.13,'80-84':0.15,'75-79':0.15,'70-74':0.0,
    }}},

    # ── Axis: PUT_TP tighter ───────────────────────────────────────────
    {'name':'PUT_TP_30','axis':'PUT_TP','dte':'30','cfg':{
        'put_tp_sigma': 0.30*1.82/0.5,  # 1.092 (tighter than 1.274 default)
        'put_net_tp':   0.30 + (-0.01) + 0.0,
    }},

    # ── Axis: Design B premium-based stop ──────────────────────────────
    {'name':'PREM_40','axis':'PREM','dte':'30','cfg':{'prem_stop_loss':-0.40}},
    {'name':'PREM_50','axis':'PREM','dte':'30','cfg':{'prem_stop_loss':-0.50}},
    {'name':'PREM_60','axis':'PREM','dte':'30','cfg':{'prem_stop_loss':-0.60}},

    # ── Axis: Reallocation strategies (min_advantage=5, default) ───────
    {'name':'RA_eslow', 'axis':'REALLOC','dte':'30','cfg':{'realloc_strategy':'entry_score_low'}},
    {'name':'RA_pnlhi', 'axis':'REALLOC','dte':'30','cfg':{'realloc_strategy':'current_pnl_high'}},
    {'name':'RA_pnllo', 'axis':'REALLOC','dte':'30','cfg':{'realloc_strategy':'current_pnl_low'}},
    {'name':'RA_old',   'axis':'REALLOC','dte':'30','cfg':{'realloc_strategy':'days_held_high'}},
    {'name':'RA_far',   'axis':'REALLOC','dte':'30','cfg':{'realloc_strategy':'sigma_distance_low'}},
]


def utility(final_eq, max_dd, initial=50000):
    """Geometric log return − quadratic DD penalty above 0.65."""
    log_ret = math.log(max(final_eq / initial, 1e-9))
    dd_excess = max(0.0, max_dd - 0.65)
    penalty = 200.0 * dd_excess ** 2
    return log_ret - penalty


def run_one(spec):
    from contextlib import contextmanager
    @contextmanager
    def _patched(module, **overrides):
        saved = {k: getattr(module, k) for k in overrides if hasattr(module, k)}
        for k, v in overrides.items():
            setattr(module, k, v)
        try:
            yield
        finally:
            for k, v in saved.items():
                setattr(module, k, v)

    import backtest_cascade as bt
    from database.models.core import AlgorithmVersion
    v = AlgorithmVersion.get_active_scores_version().id

    patches = spec.get('patches') or {}
    cfg = spec.get('cfg') or {}

    t0 = time.time()
    with _patched(bt, **patches):
        try:
            res = bt.run_cascade_backtest(
                version_id=v,
                from_date=FROM_DATE,
                to_date=TO_DATE,
                initial=50_000,
                verbose=False,
                cfg=dict(cfg),
            )
        except Exception as e:
            return {'name': spec['name'], 'axis': spec['axis'], 'error': str(e)}
    elapsed = time.time() - t0

    if not res or 'final_equity' not in res:
        return {'name': spec['name'], 'axis': spec['axis'], 'error': 'empty result'}

    log = res.get('trade_log', [])
    closed = [t for t in log if t.get('outcome') in ('tp', 'sl', 'hard', 'prem', 'realloc')]
    realloc_n = sum(1 for t in log if t.get('outcome') == 'realloc')
    prem_n    = sum(1 for t in log if t.get('outcome') == 'prem')
    calls = [t for t in closed if t.get('side') == 'call']
    puts  = [t for t in closed if t.get('side') == 'put']
    c_tp  = sum(1 for t in calls if t['outcome'] == 'tp')
    p_tp  = sum(1 for t in puts  if t['outcome'] == 'tp')

    util = utility(res['final_equity'], res['max_dd'])

    return {
        'name':       spec['name'],
        'axis':       spec['axis'],
        'patches':    patches,
        'cfg':        cfg,
        'final_eq':   res['final_equity'],
        'max_dd_pct': res['max_dd'] * 100,
        'utility':    util,
        'closed':     len(closed),
        'realloc_n':  realloc_n,
        'prem_n':     prem_n,
        'calls':      len(calls),
        'puts':       len(puts),
        'call_tp_pct': 100.0 * c_tp / len(calls) if calls else None,
        'put_tp_pct':  100.0 * p_tp / len(puts)  if puts  else None,
        'elapsed_sec': round(elapsed, 1),
    }


def main():
    from database.models.core import AlgorithmVersion
    print(f"OP2 PHASE 1 — independent axis screen (30 DTE)")
    print(f"Window: {FROM_DATE} -> {TO_DATE}  ·  v{AlgorithmVersion.get_active_scores_version().id}")
    print(f"Candidates: {len(CANDIDATES)}")
    print()

    results = []
    for spec in CANDIDATES:
        r = run_one(spec)
        if 'error' in r:
            print(f"  [{r['axis']:>8}] {r['name']:<14} ERROR: {r['error']}")
        else:
            mult = r['final_eq'] / 50_000
            extras = []
            if r['realloc_n']: extras.append(f"realloc_n={r['realloc_n']}")
            if r['prem_n']:    extras.append(f"prem_n={r['prem_n']}")
            ext_str = f"  [{', '.join(extras)}]" if extras else ""
            print(f"  [{r['axis']:>8}] {r['name']:<14} eq=${r['final_eq']:>20,.0f}  "
                  f"({mult:>9.2e}×)  DD={r['max_dd_pct']:>5.1f}%  "
                  f"util={r['utility']:+8.2f}  [{r['elapsed_sec']}s]{ext_str}")
        results.append(r)
        sys.stdout.flush()

    out_path = os.path.join(ROOT, 'experiments', 'op2', 'phase1_results.json')
    with open(out_path, 'w') as fh:
        json.dump({
            'window':     f'{FROM_DATE} to {TO_DATE}',
            'candidates': results,
        }, fh, indent=2, default=str)

    # ── Per-axis ranked sub-tables ─────────────────────────────────────
    print("\n" + "=" * 110)
    print("PER-AXIS RANKING (best to worst by utility)")
    print("=" * 110)
    valid = [r for r in results if 'error' not in r]
    baseline_util = next((r['utility'] for r in valid if r['name'] == 'baseline'), 0.0)
    baseline_eq   = next((r['final_eq'] for r in valid if r['name'] == 'baseline'), 50_000)
    baseline_dd   = next((r['max_dd_pct'] for r in valid if r['name'] == 'baseline'), 0.0)

    for axis in ['DD', 'SL', 'HOLD', 'MAXPOS', 'CASCADE', 'ULTRA', 'PUT_TP', 'PREM', 'REALLOC']:
        subs = [r for r in valid if r['axis'] == axis]
        if not subs:
            continue
        subs.sort(key=lambda r: -r['utility'])
        print(f"\n  {axis:<10}  (baseline util={baseline_util:+7.2f}, eq=${baseline_eq:,.0f}, DD={baseline_dd:.1f}%)")
        for r in subs:
            d_eq = (r['final_eq'] / baseline_eq - 1) * 100
            d_dd = r['max_dd_pct'] - baseline_dd
            d_ut = r['utility'] - baseline_util
            print(f"    {r['name']:<14} util {r['utility']:+7.2f} (Δ{d_ut:+6.2f})  "
                  f"eq Δ{d_eq:+7.1f}%  DD Δ{d_dd:+5.1f}pp")

    # ── Global ranking ────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print("GLOBAL RANKING — top 10 by utility")
    print("=" * 110)
    valid.sort(key=lambda r: -r['utility'])
    print(f"  {'rank':>4} {'name':<14} {'axis':<8}  {'final eq':>16}  {'maxDD':>6}  "
          f"{'utility':>8}  {'Δ vs baseline':>14}")
    for i, r in enumerate(valid[:10], 1):
        d_ut = r['utility'] - baseline_util
        print(f"  {i:>4} {r['name']:<14} {r['axis']:<8}  ${r['final_eq']:>14,.0f}  "
              f"{r['max_dd_pct']:>5.1f}%  {r['utility']:>+8.2f}  Δ{d_ut:>+12.2f}")

    print(f"\nSaved -> {out_path}")


if __name__ == '__main__':
    main()
