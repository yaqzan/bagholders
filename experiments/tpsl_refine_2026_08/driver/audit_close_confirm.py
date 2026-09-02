#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Cost-audit Task 3 -- close-confirmed TP arm (wick-fill optimism bound),
evidence-honesty audit, 2026-08-10. See experiments/tpsl_refine_2026_08/
PREREG.md + LESSONS.md for the sweep this audit is checking.

    python experiments/tpsl_refine_2026_08/driver/audit_close_confirm.py

HARD RULE (inherited from mc_patch.py / PREREG section 7): this file NEVER
edits monte_carlo.py / strategy_config.py / any tracked production file --
the close-confirmed variant is a pure in-process wrapper around the imported
`mc.compute_trade_outcome`, installed only for the duration of `_prepare_
window()` (compute_trade_outcome runs single-threaded in the parent process
at prepare time -- see monte_carlo.py precompute_outcomes:2817-2833, which
calls `compute_trade_outcome(...)` UNQUALIFIED, so reassigning the module
attribute `mc.compute_trade_outcome` redirects that internal call, exactly
like mc_patch.py's existing load_signals/load_put_signals monkeypatches).

METHOD -- what gets swapped and why it's scoped correctly (Task-1 read):

The barrier walk (monte_carlo.py compute_trade_outcome, ~2400-2571) fires a
TP touch on `highs[i] >= tp_level` (line ~2494) -- an intrabar WICK touch,
not a close. This script builds a shallow bars view per symbol with
`high := close` for every bar, and runs the UNMODIFIED walk on that view, so
a TP only registers when the bar actually CLOSED through the barrier (a
materially stronger and more realistic confirmation that a resting limit
order was actually reachable) -- and, as a direct consequence of reusing the
same fire_high capture the walk already does, the downstream resolve() fill
range for a 'tp' kind also collapses from `[tp_level, wick_high]` to
`[tp_level, close]`, removing the wick-overshoot fill optimism too, not just
the touch-detection optimism.

Per Task-1 recon, `highs` inside compute_trade_outcome is consumed by:
  1. The TP-touch check (the intended target of this swap).
  2. Trailing-stop peak tracking (`do_trail` branch) -- STRUCTURALLY INERT
     under the current shipped config: TSL_ENABLED defaults False (strategy_
     config.py never sets a TSL_ENABLED attribute at all; monte_carlo.py:881
     falls back to `getattr(_cfg, 'TSL_ENABLED', False)` = False). Confirmed
     no contamination.
  3. `_compute_dead_hold_call`'s post-SL popout re-walk (called with the same
     local `highs` array, ~2567-2569) -- DEAD_HOLD_ENABLED=True (active) for
     both DTEs, so this IS a live consumer. This script's whole-bars swap
     therefore ALSO close-confirms the dead-hold popout touch check, which is
     a superset of the literal "TP comparison path" ask. Not neutralized
     separately (that would require forking ~170 lines of walk logic instead
     of reusing it verbatim -- more reimplementation-drift risk than value).
     Directionally this is NOT a confound: dead-hold's popout target is the
     exact same species of optimistic wick-touch mechanism the TP barrier is,
     so tightening it too makes the reported delta a MORE conservative (if
     anything, larger-magnitude) bound on wick-fill optimism, not a less
     faithful one. The audit report quantifies dh_pop's trade share (from the
     Task-2 tape) so a reader can judge how much of the delta this adds.
  4. PREM design-B stop (`prem_stop < 0.0` branch, uses fire_close only, and
     is gated behind PREM_STOP_LOSS which defaults 0.0 -- inert regardless).

Fill-price basis (Task-1e cross-reference): a TP fire's realized fill is
`Uniform(tp_level, fire_high)` in resolve() -- i.e. the model already treats
a TP as a resting limit filled at-or-better than the barrier, so the "wick
optimism" this script bounds is specifically the credit for intrabar
overshoot beyond the barrier, not a bid/ask markup (that's the separate
SLIP_TP knob, audited in Task 2).
"""
from __future__ import annotations

import csv
import os
import statistics
import sys
import time
from collections import Counter

# --- repo-root bootstrap (mirrors phaseA_run.py exactly) --------------------
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
META_PATH = os.path.join(STATE_DIR, 'meta.json')   # reuse Phase A's pinned version (read-only)

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


# ---------------------------------------------------------------------------
# Close-confirmed TP wrapper
# ---------------------------------------------------------------------------
def _bars_close_as_high(sym_bars):
    """sym_bars: list of (date, close, high, low, open) 5-tuples, exactly the
    shape load_price_history builds (monte_carlo.py:2378-2379). Returns a NEW
    list with high replaced by close -- the source ph cache (memoized by
    mc_patch.install_loader_cache) is never mutated in place."""
    return [(d, c, c, l, o) for (d, c, h, l, o) in sym_bars]


def install_close_confirmed_tp(mc):
    """Monkeypatch mc.compute_trade_outcome to run the UNMODIFIED walk on a
    close-as-high bars view. Returns the original function so the caller can
    restore it. Active only while installed -- call restore before/after use
    outside prepare (compute_trade_outcome is prepare-time-only, parent-
    process-only; no MP worker interaction to worry about)."""
    original = mc.compute_trade_outcome

    def _close_confirmed(sym_bars, signal_date, stressed, trail=False, symbol=None):
        return original(_bars_close_as_high(sym_bars), signal_date, stressed,
                        trail=trail, symbol=symbol)

    mc.compute_trade_outcome = _close_confirmed
    return original


def restore(mc, original):
    mc.compute_trade_outcome = original


# ---------------------------------------------------------------------------
# Tape capture (mirrors audit_tp15.py -- see that file's docstring note 1 for
# why _dump_trade_tape must be intercepted rather than reading result dicts)
# ---------------------------------------------------------------------------
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

    log_path = os.path.join(LOG_DIR, 'audit_close_confirm.log')
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
    ORIGINAL_CTO = mc.compute_trade_outcome   # unpatched reference, restored after each prepare

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    d_start, d_end = window_lookup[WINDOW_LABEL]

    _tee(f"\n{'='*100}")
    _tee(f"AUDIT TASK 3 -- close-confirmed TP arm  profile={PROFILE} window={WINDOW_LABEL} "
         f"({d_start}->{d_end})  n_iter={N_ITER}  cells={CELLS}")
    _tee(f"[CONFIG] algorithm_version: id={version_meta['id']} git_commit={version_meta['git_commit']}")
    _tee(f"[CONFIG] TSL_ENABLED={mc.TSL_ENABLED} DEAD_HOLD_ENABLED={mc.DEAD_HOLD_ENABLED} "
         f"PREM_STOP_LOSS={mc.PREM_STOP_LOSS}")
    _tee(f"{'='*100}")

    out_path = os.path.join(OUT_DIR, 'audit_close_confirm.csv')
    fields = ['profile', 'window', 'tp', 'sl', 'variant', 'n_iter',
              'baked_tp_rate', 'baked_sl_rate', 'baked_hard_rate', 'baked_both_rate',
              'n_trades_total', 'median_trades_per_iter', 'median_hold_cal_days',
              'pct_tp', 'pct_sl', 'pct_hard', 'pct_both', 'pct_dh_pop', 'pct_dh_expiry', 'pct_dh_open',
              'engine_med_ret_pct', 'engine_worst_dd_pct', 'engine_p_coll']
    f_out = open(out_path, 'w', newline='', encoding='utf-8')
    w = csv.DictWriter(f_out, fieldnames=fields)
    w.writeheader()

    results_by_cell_variant = {}   # (tp,sl,variant) -> row dict, for the delta summary

    known_kinds = ('tp', 'sl', 'hard', 'both', 'dh_pop', 'dh_expiry', 'dh_open')

    for tp, sl in CELLS:
        for variant in ('default', 'close_confirmed'):
            mc_patch.set_tpsl(mc, tp, sl)
            if variant == 'close_confirmed':
                install_close_confirmed_tp(mc)
            else:
                restore(mc, ORIGINAL_CTO)

            t0 = time.perf_counter()
            ctx = mc._prepare_window(WINDOW_LABEL, d_start, d_end, version_meta['id'])
            t_prepare = time.perf_counter() - t0
            # Restore immediately after prepare -- compute_trade_outcome is
            # never called again for this cell (simulate() only reads baked ctx).
            restore(mc, ORIGINAL_CTO)

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

            _tee(f"[tp={tp:+.2f} sl={sl:+.2f} variant={variant:>16}] prepare={t_prepare:.1f}s sim={t_sim:.1f}s "
                 f"n_calls={n_calls} baked tp={tp_rate:.1f}% sl={sl_rate:.1f}% hard={hard_rate:.1f}% both={both_rate:.1f}% | "
                 f"realized tp={pct['tp']:.1f}% sl={pct['sl']:.1f}% hard={pct['hard']:.1f}% "
                 f"dh_pop={pct['dh_pop']:.1f}% dh_expiry={pct['dh_expiry']:.1f}% dh_open={pct['dh_open']:.1f}% | "
                 f"med_ret={result.get('med_ret'):+.1f}% worst_dd={result.get('worst_dd'):.1f}% "
                 f"p_coll={result.get('p_coll'):.1f}%")

    f_out.close()

    # Delta summary: close_confirmed - default, per cell.
    lines = ["# audit_close_confirm -- Task 3 delta summary\n",
             f"Generated {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"]
    for tp, sl in CELLS:
        base = results_by_cell_variant.get((tp, sl, 'default'))
        cc = results_by_cell_variant.get((tp, sl, 'close_confirmed'))
        if not base or not cc:
            continue
        d_med = cc['engine_med_ret_pct'] - base['engine_med_ret_pct']
        d_dd = cc['engine_worst_dd_pct'] - base['engine_worst_dd_pct']
        d_tp_rate = cc['baked_tp_rate'] - base['baked_tp_rate']
        lines.append(f"## tp={tp:+.2f} sl={sl:+.2f}")
        lines.append(f"- baked TP touch-rate: default={base['baked_tp_rate']:.1f}% -> "
                     f"close_confirmed={cc['baked_tp_rate']:.1f}%  (delta {d_tp_rate:+.1f}pp)")
        lines.append(f"- engine med compound: default={base['engine_med_ret_pct']:+.1f}% -> "
                     f"close_confirmed={cc['engine_med_ret_pct']:+.1f}%  (delta {d_med:+.1f}pp)")
        lines.append(f"- engine worst DD:     default={base['engine_worst_dd_pct']:.1f}% -> "
                     f"close_confirmed={cc['engine_worst_dd_pct']:.1f}%  (delta {d_dd:+.1f}pp)")
        lines.append(f"- p_coll: default={base['engine_p_coll']:.1f}% -> close_confirmed={cc['engine_p_coll']:.1f}%")
        lines.append(f"- dead-hold share (close_confirmed, out-of-scope caveat per module docstring): "
                     f"dh_pop={cc['pct_dh_pop']:.1f}% dh_expiry={cc['pct_dh_expiry']:.1f}% dh_open={cc['pct_dh_open']:.1f}%\n")
    summary_path = os.path.join(OUT_DIR, 'audit_close_confirm_summary.md')
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    for line in lines:
        _tee(line)
    _tee(f"[summary] -> {summary_path}\n[DONE] -> {out_path}")
    _LOG_F.close()


if __name__ == '__main__':
    main()
