#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Cost-audit Task 3 FOLLOW-UP -- surgical close-confirmed TP arm.

Why this file exists: audit_close_confirm.py's whole-bars high:=close swap
produced a striking result -- BOTH cells (0.15,-0.90) and (0.30,-0.70)
collapsed to nearly IDENTICAL catastrophic numbers (p_coll=100% both, worst_dd
81.9% both, med_ret -80.5%/-80.6%). That convergence to a common floor,
regardless of the TP/SL cell, is the signature of a common-mode structural
break, not a graduated per-cell friction effect -- and audit_close_confirm.py's
own docstring had already flagged the mechanism: `_compute_dead_hold_call`
(the post-SL-fire popout rescue walk) receives the SAME modified highs array
as the primary TP loop (verified: compute_trade_outcome passes its local
`highs` positionally into _compute_dead_hold_call at monte_carlo.py:2567-2569,
and that local `highs` is built from whatever `sym_bars` was handed in --
which is the close-as-high view under the whole-bars wrapper). Confirmed from
audit_close_confirm.py's own logged exit-kind mix: dh_expiry (rescue FAILED,
forced out at theta-decayed expiry) roughly DOUBLED under the whole-bars swap
for both cells (TP15: 9.2%->18.9%eq, TP30: ~9.7%->25.3% -- see
out/audit_close_confirm.csv), while dh_pop (rescue succeeded) barely moved --
i.e. the dominant effect was making the RESCUE mechanism fail more often, not
(only) making the primary TP touch stricter. That conflates two different
questions and defeats the point of comparing TP15 vs TP30.

This script isolates the primary-touch-only effect: it close-confirms the TP/
both/trail loop exactly as before, but monkeypatches `mc._compute_dead_hold_
call` to always receive the TRUE (original, wick) highs array, threaded
through a closure-captured side-channel set immediately before each
compute_trade_outcome call (single-threaded, parent-process-only -- prepare
never runs under multiprocessing, so no concurrency hazard). No production
file is touched; this is an additional in-process monkeypatch layered on the
same technique as audit_close_confirm.py.

    python experiments/tpsl_refine_2026_08/driver/audit_close_confirm_surgical.py

Reports, per cell: 'default' (unpatched) vs 'close_confirmed_full' (whole-
bars swap, already measured by audit_close_confirm.py -- rerun here too for a
single self-contained comparison table) vs 'close_confirmed_tp_only'
(surgical -- dead-hold exempted).
"""
from __future__ import annotations

import csv
import os
import statistics
import sys
import time
from collections import Counter

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
assert os.path.isfile(os.path.join(_REPO_ROOT, 'monte_carlo.py')), (
    f"repo root resolution failed: {_REPO_ROOT!r} has no monte_carlo.py "
    f"(computed from __file__={__file__!r}) -- fix the dirname() chain above"
)

EXP_DIR = os.path.dirname(_THIS_DIR)
OUT_DIR = os.path.join(EXP_DIR, 'out')
LOG_DIR = os.path.join(EXP_DIR, 'logs')
STATE_DIR = os.path.join(_THIS_DIR, 'state')
META_PATH = os.path.join(STATE_DIR, 'meta.json')

PROFILE = 'core'
WINDOW_LABEL = '22-now'
N_ITER = 100
CELLS = [(0.15, -0.90), (0.30, -0.70)]

_LOG_F = None


def _tee(msg):
    print(msg, flush=True)
    if _LOG_F is not None:
        _LOG_F.write(msg + '\n')
        _LOG_F.flush()


def _bars_close_as_high(sym_bars):
    return [(d, c, c, l, o) for (d, c, h, l, o) in sym_bars]


# Side-channel: the TRUE highs for whatever compute_trade_outcome call is
# currently in flight. Set immediately before each call, cleared after.
# Single-threaded (parent-process prepare only) -- no concurrency hazard.
_TRUE_HIGHS = {'val': None}


def install_close_confirmed_tp_only(mc):
    """Close-confirm the primary TP/both/trailing loop; dead-hold's popout
    re-walk keeps seeing the REAL wick highs via the _TRUE_HIGHS side-channel.
    Returns (original_compute_trade_outcome, original_compute_dead_hold_call)
    so the caller can restore both."""
    original_cto = mc.compute_trade_outcome
    original_dh = mc._compute_dead_hold_call

    def _dh_true_highs(highs, lows, closes, opens, fire_idx, end_idx,
                       entry, premium_pct, base_idx, dates=None):
        real_highs = _TRUE_HIGHS['val']
        use_highs = real_highs if real_highs is not None else highs
        return original_dh(use_highs, lows, closes, opens, fire_idx, end_idx,
                           entry, premium_pct, base_idx, dates=dates)

    def _close_confirmed_tp_only(sym_bars, signal_date, stressed, trail=False, symbol=None):
        _TRUE_HIGHS['val'] = [b[2] for b in sym_bars]
        try:
            return original_cto(_bars_close_as_high(sym_bars), signal_date, stressed,
                                trail=trail, symbol=symbol)
        finally:
            _TRUE_HIGHS['val'] = None

    mc.compute_trade_outcome = _close_confirmed_tp_only
    mc._compute_dead_hold_call = _dh_true_highs
    return original_cto, original_dh


def install_close_confirmed_full(mc, original_cto):
    """The audit_close_confirm.py whole-bars variant, rerun here for a
    single self-contained side-by-side (dead-hold ALSO gets closes)."""
    def _close_confirmed_full(sym_bars, signal_date, stressed, trail=False, symbol=None):
        return original_cto(_bars_close_as_high(sym_bars), signal_date, stressed,
                            trail=trail, symbol=symbol)
    mc.compute_trade_outcome = _close_confirmed_full


def restore(mc, original_cto, original_dh):
    mc.compute_trade_outcome = original_cto
    mc._compute_dead_hold_call = original_dh


_CAPTURED = {'rs': None}


def _capture_dump_trade_tape(label, seeds, results, trading_days):
    _CAPTURED['rs'] = results


IX_ENTRY_DATE, IX_EXIT_DATE = 1, 2
IX_OUTCOME = 10


def _turnover_stats(rs):
    trades_per_iter, hold_days_all = [], []
    outcome_counts = Counter()
    n_total = 0
    for r in rs:
        tape = r.get('_tape') or []
        trades_per_iter.append(len(tape))
        for t in tape:
            n_total += 1
            hold_days_all.append((t[IX_EXIT_DATE] - t[IX_ENTRY_DATE]).days)
            outcome_counts[t[IX_OUTCOME]] += 1
    return {
        'n_trades_total': n_total,
        'median_trades_per_iter': statistics.median(trades_per_iter) if trades_per_iter else 0,
        'median_hold_cal_days': statistics.median(hold_days_all) if hold_days_all else None,
        'outcome_counts': outcome_counts,
    }


def main():
    global _LOG_F
    import mc_patch

    for d in (OUT_DIR, LOG_DIR, STATE_DIR):
        os.makedirs(d, exist_ok=True)

    log_path = os.path.join(LOG_DIR, 'audit_close_confirm_surgical.log')
    _LOG_F = open(log_path, 'a', encoding='utf-8')

    mc_patch.apply_frozen_pins(max_workers=6)
    mc_patch.apply_profile_env(PROFILE)
    os.environ['MC_TRADE_TAPE'] = '1'
    version_meta = mc_patch.resolve_and_pin_version(META_PATH)

    import monte_carlo as mc

    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)
    mc._dump_trade_tape = _capture_dump_trade_tape
    mc.N_ITER = N_ITER
    ORIGINAL_CTO = mc.compute_trade_outcome
    ORIGINAL_DH = mc._compute_dead_hold_call

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    d_start, d_end = window_lookup[WINDOW_LABEL]

    _tee(f"\n{'='*100}")
    _tee(f"AUDIT TASK 3 FOLLOW-UP -- surgical close-confirmed TP (dead-hold exempted)  "
         f"profile={PROFILE} window={WINDOW_LABEL} n_iter={N_ITER}  cells={CELLS}")
    _tee(f"[CONFIG] algorithm_version: id={version_meta['id']} git_commit={version_meta['git_commit']}")
    _tee(f"{'='*100}")

    out_path = os.path.join(OUT_DIR, 'audit_close_confirm_surgical.csv')
    fields = ['profile', 'window', 'tp', 'sl', 'variant', 'n_iter',
              'baked_tp_rate', 'baked_sl_rate', 'baked_hard_rate', 'baked_both_rate',
              'n_trades_total', 'median_trades_per_iter', 'median_hold_cal_days',
              'pct_tp', 'pct_sl', 'pct_hard', 'pct_both', 'pct_dh_pop', 'pct_dh_expiry', 'pct_dh_open',
              'engine_med_ret_pct', 'engine_worst_dd_pct', 'engine_p_coll']
    f_out = open(out_path, 'w', newline='', encoding='utf-8')
    w = csv.DictWriter(f_out, fieldnames=fields)
    w.writeheader()

    results_by_cell_variant = {}
    known_kinds = ('tp', 'sl', 'hard', 'both', 'dh_pop', 'dh_expiry', 'dh_open')
    variants = ('default', 'close_confirmed_full', 'close_confirmed_tp_only')

    for tp, sl in CELLS:
        for variant in variants:
            mc_patch.set_tpsl(mc, tp, sl)
            if variant == 'close_confirmed_tp_only':
                install_close_confirmed_tp_only(mc)
            elif variant == 'close_confirmed_full':
                install_close_confirmed_full(mc, ORIGINAL_CTO)
                mc._compute_dead_hold_call = ORIGINAL_DH   # unmodified in this variant; the CTO wrapper itself feeds it closes
            else:
                restore(mc, ORIGINAL_CTO, ORIGINAL_DH)

            t0 = time.perf_counter()
            ctx = mc._prepare_window(WINDOW_LABEL, d_start, d_end, version_meta['id'])
            t_prepare = time.perf_counter() - t0
            restore(mc, ORIGINAL_CTO, ORIGINAL_DH)   # always restore right after prepare

            n_calls, tp_rate, sl_rate, hard_rate, both_rate = mc_patch.call_outcome_rates(ctx)

            _CAPTURED['rs'] = None
            t1 = time.perf_counter()
            sim = mc._simulate_window(ctx)
            t_sim = time.perf_counter() - t1
            result = sim['seeded']
            rs = _CAPTURED['rs'] or []
            stats = _turnover_stats(rs)
            oc = stats['outcome_counts']
            n_tot = stats['n_trades_total']
            pct = {k: (oc.get(k, 0) / n_tot * 100.0 if n_tot else 0.0) for k in known_kinds}

            row = {
                'profile': PROFILE, 'window': WINDOW_LABEL, 'tp': tp, 'sl': sl,
                'variant': variant, 'n_iter': N_ITER,
                'baked_tp_rate': round(tp_rate, 2), 'baked_sl_rate': round(sl_rate, 2),
                'baked_hard_rate': round(hard_rate, 2), 'baked_both_rate': round(both_rate, 2),
                'n_trades_total': n_tot,
                'median_trades_per_iter': stats['median_trades_per_iter'],
                'median_hold_cal_days': stats['median_hold_cal_days'],
                'engine_med_ret_pct': result.get('med_ret'),
                'engine_worst_dd_pct': result.get('worst_dd'),
                'engine_p_coll': result.get('p_coll'),
            }
            for k in known_kinds:
                row[f'pct_{k}'] = round(pct[k], 2)
            w.writerow(row)
            f_out.flush()
            results_by_cell_variant[(tp, sl, variant)] = row

            _tee(f"[tp={tp:+.2f} sl={sl:+.2f} variant={variant:>22}] prepare={t_prepare:.1f}s sim={t_sim:.1f}s "
                 f"baked tp={tp_rate:.1f}% sl={sl_rate:.1f}% hard={hard_rate:.1f}% both={both_rate:.1f}% | "
                 f"realized tp={pct['tp']:.1f}% dh_pop={pct['dh_pop']:.1f}% dh_expiry={pct['dh_expiry']:.1f}% | "
                 f"med_ret={result.get('med_ret'):+.1f}% worst_dd={result.get('worst_dd'):.1f}% "
                 f"p_coll={result.get('p_coll'):.1f}%")

    f_out.close()

    lines = ["# audit_close_confirm_surgical -- Task 3 follow-up delta summary\n",
             f"Generated {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"]
    for tp, sl in CELLS:
        base = results_by_cell_variant.get((tp, sl, 'default'))
        full = results_by_cell_variant.get((tp, sl, 'close_confirmed_full'))
        tponly = results_by_cell_variant.get((tp, sl, 'close_confirmed_tp_only'))
        lines.append(f"## tp={tp:+.2f} sl={sl:+.2f}")
        for label, row in (('default', base), ('close_confirmed_full (dead-hold ALSO close-confirmed)', full),
                           ('close_confirmed_tp_only (dead-hold exempted, TRUE wick highs)', tponly)):
            if row is None:
                continue
            lines.append(f"- {label}: med_ret={row['engine_med_ret_pct']:+.1f}% worst_dd={row['engine_worst_dd_pct']:.1f}% "
                        f"p_coll={row['engine_p_coll']:.1f}% baked_tp_rate={row['baked_tp_rate']:.1f}% "
                        f"dh_pop={row['pct_dh_pop']:.1f}% dh_expiry={row['pct_dh_expiry']:.1f}%")
        if base and tponly:
            d_med = tponly['engine_med_ret_pct'] - base['engine_med_ret_pct']
            d_dd = tponly['engine_worst_dd_pct'] - base['engine_worst_dd_pct']
            lines.append(f"  => TP-ONLY delta vs default: med_ret {d_med:+.1f}pp, worst_dd {d_dd:+.1f}pp, "
                        f"p_coll {base['engine_p_coll']:.0f}%->{tponly['engine_p_coll']:.0f}%")
        lines.append("")
    summary_path = os.path.join(OUT_DIR, 'audit_close_confirm_surgical_summary.md')
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    for line in lines:
        _tee(line)
    _tee(f"[summary] -> {summary_path}\n[DONE] -> {out_path}")
    _LOG_F.close()


if __name__ == '__main__':
    main()
