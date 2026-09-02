#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Cost-audit Task 2 -- instrumented turnover + friction sensitivity for the
suspected TP15 cost-model artifact (evidence-honesty audit, 2026-08-10).

See experiments/tpsl_refine_2026_08/PREREG.md + LESSONS.md for the sweep this
audit is checking, and .claude/docs/monte-carlo-sweeps.md for the 2026-04
catastrophic-TP20-under-slip precedent that motivated the audit.

    python experiments/tpsl_refine_2026_08/driver/audit_tp15.py

HARD RULE (inherited from mc_patch.py / PREREG section 7): this file NEVER
edits monte_carlo.py / strategy_config.py / any tracked production file. All
variant behavior is in-process patching of the imported `mc` module object.

Windows/multiprocessing note (mirrors phaseA_run.py): monte_carlo._simulate_
window builds its own fresh multiprocessing.Pool per call -- on Windows
(spawn) every worker re-imports this script as a non-`__main__` module.
Everything with side effects MUST stay inside `main()`, guarded by
`if __name__ == '__main__':` at the bottom.

METHOD NOTES (read before trusting the numbers):

1. Trade-tape exposure (the task's own open question, answered by reading
   monte_carlo.py 3120-3125 + 4656-4658): MC_TRADE_TAPE=1 makes
   run_single_sim attach `result['_tape']` (a list of per-trade tuples) to
   EACH iteration's own result dict. `_simulate_window` collects all N_ITER
   of those dicts into a local `rs` list, but its own RETURN VALUE (the
   aggregate `result` dict) never carries `_tape` -- `rs` is only handed to
   `_dump_trade_tape(label, seeds, rs, trading_days)`, a module-level
   function that writes `.cache/dd_ledger/tape_{label}.parquet`, KEYED ONLY
   BY WINDOW LABEL (not by TP/SL cell) -- a second cell on the same window
   would silently overwrite the first cell's file. So: NOT the result dict,
   NOT a module buffer -- it is exposed only as an argument to a function
   call. This script monkeypatches `mc._dump_trade_tape` itself to intercept
   that argument (capturing `rs` into an in-process buffer) instead of
   calling through -- avoids both the overwrite hazard and touching the
   shared `.cache/dd_ledger/` directory that a real (non-audit) feature owns.

2. SLIP_ENTRY_OV worker-propagation trap (discovered during this audit, not
   previously documented): unlike TP_BASE/SL_BASE (which mc_patch.set_tpsl
   safely mutates post-import because they are consumed ONLY during
   `_prepare_window`, which runs in the parent process), SLIP_ENTRY is
   consumed inside `resolve()`, which executes INSIDE THE MP WORKERS for any
   N_ITER >= 16 (this script's N=100 always takes that path). On Windows
   (spawn), each `multiprocessing.Pool(...)` built inside `_simulate_window`
   spawns workers that re-import monte_carlo.py fresh -- so a pure in-process
   `mc.SLIP_ENTRY = -f` mutation in the parent is a SILENT NO-OP for the
   simulate phase (workers never see it). The fix used below: set
   `os.environ['SLIP_ENTRY_OV']` BEFORE calling `_simulate_window`, so each
   freshly-spawned worker's own import-time evaluation
   (monte_carlo.py:402, `SLIP_ENTRY = float(os.environ.get('SLIP_ENTRY_OV', ...))`)
   picks up the new value, and NET_HARD_SELL (monte_carlo.py:433, consumed by
   the end-of-window forced-liquidation branch that also runs inside workers)
   is correctly re-derived along with it automatically, since it's computed
   from the same env-derived globals at each worker's own import time. No
   ctx re-prepare is needed for this -- SLIP_* never feeds the barrier walk.

3. Tape-recompounding approximation: for each iteration (seed), the engine's
   `portfolio_value = cash + sum(open positions' premium_cost)` only changes
   at trade CLOSE events -- opening a position moves `premium_cost` from cash
   into the positions sum with zero net change (mark-to-COST, not
   mark-to-market, while held). So a chronological walk over just the
   (open, close) events per trade -- processing same-day closes before opens
   (matching the engine's own day-loop order: exits are booked before new
   entries each day) -- exactly reproduces the peak/trough series needed for
   DD, GIVEN the same trade schedule and sizing. What this script does NOT
   re-derive: (a) each trade's dollar size, using the tape's own recorded
   `alloc_frac = premium_cost / entry_value` REPLAYED against a freshly
   recompounded (friction-perturbed) portfolio-value path, rather than the
   original run's absolute dollar premium_cost -- and (b) trade ELIGIBILITY
   / TIMING / COUNT are taken as given from the original (baseline-friction)
   run; a counterfactually worse path might have hit MAX_POSITIONS caps
   differently or (in the extreme) triggered the engine's early collapse-
   break (which freezes new entries) sooner than reconstructed here. Net
   effect: this is a first-order-accurate, mildly-conservative approximation
   for small f, cross-checked against genuine re-simulation at 3 anchor f
   values (method 2 above) which DOES correctly model both effects.
"""
from __future__ import annotations

import csv
import os
import statistics
import sys
import time
from collections import Counter

# --- repo-root bootstrap (mirrors phaseA_run.py exactly) --------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))                       # .../driver
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))    # repo root
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
assert os.path.isfile(os.path.join(_REPO_ROOT, 'monte_carlo.py')), (
    f"repo root resolution failed: {_REPO_ROOT!r} has no monte_carlo.py "
    f"(computed from __file__={__file__!r}) -- fix the dirname() chain above"
)

EXP_DIR = os.path.dirname(_THIS_DIR)                       # experiments/tpsl_refine_2026_08
OUT_DIR = os.path.join(EXP_DIR, 'out')
LOG_DIR = os.path.join(EXP_DIR, 'logs')
STATE_DIR = os.path.join(_THIS_DIR, 'state')
META_PATH = os.path.join(STATE_DIR, 'meta.json')   # reuse Phase A's pinned version (read-only)

PROFILE = 'core'
WINDOW_LABEL = '22-now'
N_ITER = 100
CELLS = [(0.15, -0.90), (0.30, -0.70), (0.15, -0.30)]   # suspect / incumbent / cheap-third
FRICTION_GRID_TAPE = [0.005, 0.01, 0.015, 0.02, 0.03]     # recompounded from the captured tape
FRICTION_GRID_RESIM = [0.005, 0.015, 0.03]                # genuine re-simulation cross-check

# Calibration anchors (FF-2 R6, experiments/flatfile_exploitation/FF2_RESULTS.md
# section (a), N=5.35M contract-day rows, calls DTE18-50 moneyness 0.90-1.10):
# Roll effective FULL round-trip spread by FF-3' liquidity-map quintile:
#   <=320 opt_vol/day: 2.84% | 320-1191: 2.28% | 1191-3486: 2.13% |
#   3486-14524: 1.92% | >14524 (most liquid): 1.04%.
FF2_R6_TIER_MEDIANS_PCT = {'t1_illiquid': 2.84, 't2': 2.28, 't3': 2.13,
                           't4': 1.92, 't5_liquid': 1.04}

_LOG_F = None


def _tee(msg):
    print(msg, flush=True)
    if _LOG_F is not None:
        _LOG_F.write(msg + '\n')
        _LOG_F.flush()


# ---------------------------------------------------------------------------
# Tape capture (replaces mc._dump_trade_tape -- see module docstring note 1)
# ---------------------------------------------------------------------------
_CAPTURED = {'label': None, 'seeds': None, 'rs': None}


def _capture_dump_trade_tape(label, seeds, results, trading_days):
    """Drop-in replacement for mc._dump_trade_tape: stash (label, seeds,
    results) in-process instead of writing .cache/dd_ledger/tape_*.parquet.
    `results` is the same `rs` list _simulate_window built -- each element is
    one iteration's result dict, carrying `_tape` (list of trade tuples,
    schema below) when TRADE_TAPE_ENABLED. No disk write, no shared-cache
    collision across cells."""
    _CAPTURED['label'] = label
    _CAPTURED['seeds'] = seeds
    _CAPTURED['rs'] = results


# Tape row schema, verified against monte_carlo.py _dump_trade_tape (~3929):
# (seed omitted here -- we key by which _simulate_window call this came from)
#   sym_id, entry_date, exit_date, side, score, tier, ct, ern,
#   premium_cost, option_pnl, outcome, entry_value, entry_peak, entry_dd,
#   entry_open_calls, entry_open_puts
IX_ENTRY_DATE, IX_EXIT_DATE = 1, 2
IX_PREMIUM_COST, IX_OPTION_PNL, IX_OUTCOME = 8, 9, 10
IX_ENTRY_VALUE = 11


def recompound_tape(tape_rows, starting_cash, friction, collapse_threshold):
    """Reconstruct ONE iteration's equity path from its trade tape with an
    extra `-friction` applied to every closed trade's pnl fraction
    (regardless of exit kind), replaying each trade's ORIGINAL alloc_frac
    against the freshly recompounded portfolio-value path (see module
    docstring note 3 for the approximation this implies).

    Returns (final_return_pct, worst_dd_pct, collapsed: bool).
    """
    rows = []
    for r in tape_rows:
        entry_date, exit_date = r[IX_ENTRY_DATE], r[IX_EXIT_DATE]
        premium_cost, option_pnl, entry_value = r[IX_PREMIUM_COST], r[IX_OPTION_PNL], r[IX_ENTRY_VALUE]
        alloc_frac = (premium_cost / entry_value) if entry_value else 0.0
        rows.append({
            'entry_date': entry_date, 'exit_date': exit_date,
            'alloc_frac': alloc_frac, 'pnl_adj': option_pnl - friction,
            'premium_replayed': None,
        })

    events = []
    for i, r in enumerate(rows):
        events.append((r['entry_date'], 1, i))   # open  -- sorts AFTER close on a tied date
        events.append((r['exit_date'],  0, i))   # close -- sorts BEFORE open on a tied date
    events.sort(key=lambda e: (e[0], e[1]))

    portfolio_value = starting_cash
    peak = starting_cash
    worst_dd = 0.0
    collapsed = False
    collapse_floor = starting_cash * collapse_threshold
    for _date, kind, i in events:
        r = rows[i]
        if kind == 1:   # open -- mark-to-cost neutral, no value change
            r['premium_replayed'] = r['alloc_frac'] * portfolio_value
        else:            # close -- realize pnl on the replayed dollar size
            portfolio_value += r['premium_replayed'] * r['pnl_adj']
            if portfolio_value <= collapse_floor:
                collapsed = True
            if portfolio_value > peak:
                peak = portfolio_value
            else:
                dd = (peak - portfolio_value) / peak if peak > 0 else 0.0
                if dd > worst_dd:
                    worst_dd = dd

    final_return_pct = (portfolio_value / starting_cash - 1.0) * 100.0
    return final_return_pct, worst_dd * 100.0, collapsed


def main():
    global _LOG_F
    import mc_patch   # local module, driver/mc_patch.py

    for d in (OUT_DIR, LOG_DIR, STATE_DIR):
        os.makedirs(d, exist_ok=True)

    log_path = os.path.join(LOG_DIR, 'audit_tp15.log')
    _LOG_F = open(log_path, 'a', encoding='utf-8')

    # 1) env BEFORE import -- frozen pins, profile overrides, version pin,
    # trade-tape capture (module-level TRADE_TAPE_ENABLED read at import).
    mc_patch.apply_frozen_pins(max_workers=6)
    mc_patch.apply_profile_env(PROFILE)
    os.environ['MC_TRADE_TAPE'] = '1'
    version_meta = mc_patch.resolve_and_pin_version(META_PATH)

    # 2) import monte_carlo now that every env var above is set.
    import monte_carlo as mc

    # 3) post-import patches.
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)
    mc._dump_trade_tape = _capture_dump_trade_tape   # see note 1
    mc.N_ITER = N_ITER

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    if WINDOW_LABEL not in window_lookup:
        raise SystemExit(f"window {WINDOW_LABEL!r} not in mc.WINDOWS {sorted(window_lookup)}")
    d_start, d_end = window_lookup[WINDOW_LABEL]
    window_years = (d_end - d_start).days / 365.25

    _tee(f"\n{'='*100}")
    _tee(f"AUDIT TASK 2 -- turnover + friction sensitivity  profile={PROFILE} "
         f"window={WINDOW_LABEL} ({d_start}->{d_end}, {window_years:.2f}y)  n_iter={N_ITER}  "
         f"cells={CELLS}")
    _tee(f"[CONFIG] algorithm_version: id={version_meta['id']} git_commit={version_meta['git_commit']}")
    _tee(f"[CONFIG] SLIP_ENTRY={mc.SLIP_ENTRY} SLIP_TP={mc.SLIP_TP} SLIP_SL={mc.SLIP_SL} "
         f"SLIP_HARD={mc.SLIP_HARD} DH_POP_SLIP={mc.DH_POP_SLIP} "
         f"TP_FILL_MISS_P={mc.TP_FILL_MISS_P} TSL_ENABLED={mc.TSL_ENABLED} "
         f"DEAD_HOLD_ENABLED={mc.DEAD_HOLD_ENABLED} LIQ_PENALTY_ENABLED={mc.LIQ_PENALTY_ENABLED}")
    _tee(f"[CONFIG] STARTING_CASH={mc.STARTING_CASH} COLLAPSE_THRESHOLD={mc.COLLAPSE_THRESHOLD}")
    _tee(f"{'='*100}")

    turnover_path = os.path.join(OUT_DIR, 'audit_tp15_turnover.csv')
    friction_tape_path = os.path.join(OUT_DIR, 'audit_tp15_friction_tape.csv')
    friction_resim_path = os.path.join(OUT_DIR, 'audit_tp15_friction_resim.csv')

    turnover_fields = ['profile', 'window', 'tp', 'sl', 'n_iter',
                        'median_trades_per_iter', 'median_hold_cal_days', 'trades_per_year',
                        'n_trades_total'] + [f'pct_{k}' for k in
                        ('tp', 'sl', 'hard', 'both', 'prem', 'trail', 'dh_pop', 'dh_expiry', 'dh_open', 'other')] + [
                        'engine_med_ret_pct', 'engine_worst_dd_pct', 'engine_p_coll',
                        'recon_f0_med_ret_pct', 'recon_f0_worst_dd_pct',
                        'recon_vs_engine_med_delta_pct', 'recon_vs_engine_dd_delta_pp']
    friction_tape_fields = ['profile', 'window', 'tp', 'sl', 'friction',
                             'med_final_return_pct', 'worst_dd_pct', 'p_coll_pct']
    friction_resim_fields = ['profile', 'window', 'tp', 'sl', 'friction',
                              'med_ret_pct', 'worst_dd_pct', 'p_coll_pct',
                              'call_trades_mean']

    f_turnover = open(turnover_path, 'w', newline='', encoding='utf-8')
    w_turnover = csv.DictWriter(f_turnover, fieldnames=turnover_fields)
    w_turnover.writeheader()

    f_ftape = open(friction_tape_path, 'w', newline='', encoding='utf-8')
    w_ftape = csv.DictWriter(f_ftape, fieldnames=friction_tape_fields)
    w_ftape.writeheader()

    f_fresim = open(friction_resim_path, 'w', newline='', encoding='utf-8')
    w_fresim = csv.DictWriter(f_fresim, fieldnames=friction_resim_fields)
    w_fresim.writeheader()

    for tp, sl in CELLS:
        os.environ.pop('SLIP_ENTRY_OV', None)   # baseline -- SLIP_ENTRY back to shipped 0.0
        mc_patch.set_tpsl(mc, tp, sl)

        t0 = time.perf_counter()
        ctx = mc._prepare_window(WINDOW_LABEL, d_start, d_end, version_meta['id'])
        t_prepare = time.perf_counter() - t0
        n_calls, tp_rate, sl_rate, hard_rate, both_rate = mc_patch.call_outcome_rates(ctx)
        _tee(f"[cell tp={tp:+.2f} sl={sl:+.2f}] prepare={t_prepare:.1f}s n_call_signals={n_calls} "
             f"baked-rates tp={tp_rate:.1f}% sl={sl_rate:.1f}% hard={hard_rate:.1f}% both={both_rate:.1f}%")

        # --- baseline simulate (captures the tape) ---
        _CAPTURED['rs'] = None
        t1 = time.perf_counter()
        sim = mc._simulate_window(ctx)
        t_sim = time.perf_counter() - t1
        result = sim['seeded']
        rs = _CAPTURED['rs']
        if not rs:
            raise SystemExit(f"tape capture empty for cell tp={tp} sl={sl} -- "
                              f"_dump_trade_tape monkeypatch did not fire as expected")

        all_trades = []
        trades_per_iter = []
        hold_days_all = []
        outcome_counts = Counter()
        for r in rs:
            tape = r.get('_tape') or []
            trades_per_iter.append(len(tape))
            for t in tape:
                all_trades.append(t)
                hold_days_all.append((t[IX_EXIT_DATE] - t[IX_ENTRY_DATE]).days)
                outcome_counts[t[IX_OUTCOME]] += 1

        n_trades_total = len(all_trades)
        median_trades_per_iter = statistics.median(trades_per_iter) if trades_per_iter else 0
        median_hold_days = statistics.median(hold_days_all) if hold_days_all else None
        trades_per_year = (median_trades_per_iter / window_years) if window_years else None

        known_kinds = ('tp', 'sl', 'hard', 'both', 'prem', 'trail', 'dh_pop', 'dh_expiry', 'dh_open')
        pct_by_kind = {}
        for k in known_kinds:
            pct_by_kind[k] = (outcome_counts.get(k, 0) / n_trades_total * 100.0) if n_trades_total else 0.0
        other_n = n_trades_total - sum(outcome_counts.get(k, 0) for k in known_kinds)
        pct_by_kind['other'] = (other_n / n_trades_total * 100.0) if n_trades_total else 0.0

        # f=0 recon (tape recompounded with ZERO extra friction) vs the
        # engine's own reported aggregate -- validates the reconstruction
        # method BEFORE trusting it at f>0. Should closely track (small
        # float-rounding-scale deltas only), since f=0 changes nothing.
        recon_finals_f0, recon_dds_f0 = [], []
        for r in rs:
            tape = r.get('_tape') or []
            fr, dd, _coll = recompound_tape(tape, mc.STARTING_CASH, 0.0, mc.COLLAPSE_THRESHOLD)
            recon_finals_f0.append(fr)
            recon_dds_f0.append(dd)
        recon_med_f0 = statistics.median(recon_finals_f0) if recon_finals_f0 else None
        recon_worst_dd_f0 = max(recon_dds_f0) if recon_dds_f0 else None

        turnover_row = {
            'profile': PROFILE, 'window': WINDOW_LABEL, 'tp': tp, 'sl': sl, 'n_iter': N_ITER,
            'median_trades_per_iter': median_trades_per_iter,
            'median_hold_cal_days': median_hold_days,
            'trades_per_year': round(trades_per_year, 1) if trades_per_year else None,
            'n_trades_total': n_trades_total,
            'engine_med_ret_pct': result.get('med_ret'), 'engine_worst_dd_pct': result.get('worst_dd'),
            'engine_p_coll': result.get('p_coll'),
            'recon_f0_med_ret_pct': recon_med_f0, 'recon_f0_worst_dd_pct': recon_worst_dd_f0,
            'recon_vs_engine_med_delta_pct': (recon_med_f0 - result.get('med_ret'))
                if recon_med_f0 is not None and result.get('med_ret') is not None else None,
            'recon_vs_engine_dd_delta_pp': (recon_worst_dd_f0 - result.get('worst_dd'))
                if recon_worst_dd_f0 is not None and result.get('worst_dd') is not None else None,
        }
        for k in known_kinds + ('other',):
            turnover_row[f'pct_{k}'] = round(pct_by_kind[k], 2)
        w_turnover.writerow(turnover_row)
        f_turnover.flush()

        _tee(f"  turnover: median_trades/iter={median_trades_per_iter} median_hold_days={median_hold_days} "
             f"trades/yr={turnover_row['trades_per_year']} n_total={n_trades_total}")
        _tee(f"  exit-kind mix: " + " ".join(f"{k}={pct_by_kind[k]:.1f}%" for k in known_kinds if pct_by_kind[k] > 0)
             + (f" other={pct_by_kind['other']:.1f}%" if pct_by_kind['other'] > 0 else ""))
        _tee(f"  engine: med_ret={result.get('med_ret'):+.1f}% worst_dd={result.get('worst_dd'):.1f}% "
             f"p_coll={result.get('p_coll'):.1f}%  |  recon@f=0: med_ret={recon_med_f0:+.1f}% "
             f"worst_dd={recon_worst_dd_f0:.1f}%  (validates recompounding method)")

        # --- friction sensitivity: tape recompounding (no re-simulation) ---
        for f in FRICTION_GRID_TAPE:
            finals, dds, n_coll = [], [], 0
            for r in rs:
                tape = r.get('_tape') or []
                fr, dd, coll = recompound_tape(tape, mc.STARTING_CASH, f, mc.COLLAPSE_THRESHOLD)
                finals.append(fr)
                dds.append(dd)
                if coll:
                    n_coll += 1
            row = {
                'profile': PROFILE, 'window': WINDOW_LABEL, 'tp': tp, 'sl': sl, 'friction': f,
                'med_final_return_pct': statistics.median(finals) if finals else None,
                'worst_dd_pct': max(dds) if dds else None,
                'p_coll_pct': (n_coll / len(rs) * 100.0) if rs else None,
            }
            w_ftape.writerow(row)
            f_ftape.flush()
            _tee(f"  [tape f={f:.3f}] med_ret={row['med_final_return_pct']:+.1f}% "
                 f"worst_dd={row['worst_dd_pct']:.1f}% p_coll={row['p_coll_pct']:.1f}%")

        # --- friction sensitivity: genuine re-simulation cross-check ---
        # SLIP_ENTRY_OV set via os.environ (NOT mc.SLIP_ENTRY in-process --
        # see module docstring note 2) BEFORE each _simulate_window call, so
        # freshly-spawned MP workers see it on their own fresh import. No
        # re-prepare needed: SLIP_* never feeds the barrier walk / ctx.
        for f in FRICTION_GRID_RESIM:
            os.environ['SLIP_ENTRY_OV'] = str(-f)
            _CAPTURED['rs'] = None
            sim_f = mc._simulate_window(ctx)
            result_f = sim_f['seeded']
            row = {
                'profile': PROFILE, 'window': WINDOW_LABEL, 'tp': tp, 'sl': sl, 'friction': f,
                'med_ret_pct': result_f.get('med_ret'), 'worst_dd_pct': result_f.get('worst_dd'),
                'p_coll_pct': result_f.get('p_coll'), 'call_trades_mean': result_f.get('call_trades'),
            }
            w_fresim.writerow(row)
            f_fresim.flush()
            _tee(f"  [resim f={f:.3f}] med_ret={row['med_ret_pct']:+.1f}% worst_dd={row['worst_dd_pct']:.1f}% "
                 f"p_coll={row['p_coll_pct']:.1f}% call_trades_mean={row['call_trades_mean']:.1f}")
        os.environ.pop('SLIP_ENTRY_OV', None)   # reset before next cell

    f_turnover.close()
    f_ftape.close()
    f_fresim.close()

    _write_summary(turnover_path, friction_tape_path, friction_resim_path)
    _tee(f"\n[DONE] wrote {turnover_path}, {friction_tape_path}, {friction_resim_path}")
    _LOG_F.close()


def _crossover(rows_a, rows_b, value_key, lower_is_better):
    """rows_a/rows_b: list of (friction, value) sorted by friction ascending,
    for cell A (e.g. TP15) and cell B (e.g. TP30 incumbent). Returns a string
    describing the bracketing friction interval where A stops beating B
    (A assumed to beat B at the lowest sampled friction), plus a linear-
    interpolation point estimate when a bracket is found."""
    a_by_f = dict(rows_a)
    b_by_f = dict(rows_b)
    fs = sorted(set(a_by_f) & set(b_by_f))
    if not fs:
        return "no overlapping friction points"

    def a_beats_b(f):
        va, vb = a_by_f[f], b_by_f[f]
        return (va < vb) if lower_is_better else (va > vb)

    if not a_beats_b(fs[0]):
        return f"A does not beat B even at the lowest sampled f={fs[0]}"
    prev = fs[0]
    for f in fs[1:]:
        if not a_beats_b(f):
            va0, vb0 = a_by_f[prev], b_by_f[prev]
            va1, vb1 = a_by_f[f], b_by_f[f]
            diff0, diff1 = (va0 - vb0), (va1 - vb1)
            if lower_is_better:
                diff0, diff1 = -diff0, -diff1
            if diff1 != diff0:
                t = diff0 / (diff0 - diff1)
                f_star = prev + t * (f - prev)
            else:
                f_star = (prev + f) / 2.0
            return f"between f={prev} and f={f}  (linear-interp estimate f*~={f_star:.4f})"
        prev = f
    return f"A still beats B at every sampled f up to {fs[-1]} (no crossover observed in-grid)"


def _write_summary(turnover_path, friction_tape_path, friction_resim_path):
    import csv as _csv
    tp15_key, tp30_key = (0.15, -0.90), (0.30, -0.70)

    def _load(path):
        with open(path, newline='', encoding='utf-8') as f:
            return list(_csv.DictReader(f))

    ftape_rows = _load(friction_tape_path)
    fresim_rows = _load(friction_resim_path)

    def _series(rows, tp, sl, fkey, vkey):
        out = []
        for r in rows:
            if float(r['tp']) == tp and float(r['sl']) == sl:
                out.append((float(r[fkey]), float(r[vkey])))
        return sorted(out)

    tape_ret_15 = _series(ftape_rows, *tp15_key, 'friction', 'med_final_return_pct')
    tape_ret_30 = _series(ftape_rows, *tp30_key, 'friction', 'med_final_return_pct')
    tape_dd_15 = _series(ftape_rows, *tp15_key, 'friction', 'worst_dd_pct')
    tape_dd_30 = _series(ftape_rows, *tp30_key, 'friction', 'worst_dd_pct')

    resim_ret_15 = _series(fresim_rows, *tp15_key, 'friction', 'med_ret_pct')
    resim_ret_30 = _series(fresim_rows, *tp30_key, 'friction', 'med_ret_pct')
    resim_dd_15 = _series(fresim_rows, *tp15_key, 'friction', 'worst_dd_pct')
    resim_dd_30 = _series(fresim_rows, *tp30_key, 'friction', 'worst_dd_pct')

    lines = []
    lines.append("# audit_tp15 -- Task 2 crossover summary\n")
    lines.append(f"Generated {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    lines.append("## Crossover: TP15/SL-90 vs TP30/SL-70 (incumbent), Core, 22-now, N=100\n")
    lines.append(f"- TAPE recompounding, median compound: {_crossover(tape_ret_15, tape_ret_30, 'ret', lower_is_better=False)}")
    lines.append(f"- TAPE recompounding, worst DD:         {_crossover(tape_dd_15, tape_dd_30, 'dd', lower_is_better=True)}")
    lines.append(f"- RESIM cross-check, median compound:   {_crossover(resim_ret_15, resim_ret_30, 'ret', lower_is_better=False)}")
    lines.append(f"- RESIM cross-check, worst DD:           {_crossover(resim_dd_15, resim_dd_30, 'dd', lower_is_better=True)}")
    lines.append("\n## FF-2 R6 calibration anchors (real measured full round-trip spread by liquidity tier)\n")
    for k, v in FF2_R6_TIER_MEDIANS_PCT.items():
        lines.append(f"- {k}: {v}% full round-trip  (half-spread {v/2:.2f}%)")
    lines.append(f"\nCurrent shipped canon charges a flat 1.5% half-spread on FORCED exits only "
                 f"(SLIP_SL=SLIP_HARD=-0.015) and 0% on TP/entry. f=0.01 in this sweep ~ most-liquid "
                 f"tier's full spread; f=0.015 ~ the shipped half-spread canon value; f=0.028-0.03 ~ "
                 f"least-liquid tier's full spread / the historically-cited \"~3% spread\".")

    summary_path = os.path.join(OUT_DIR, 'audit_tp15_summary.md')
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    for line in lines:
        _tee(line)
    _tee(f"[summary] -> {summary_path}")


if __name__ == '__main__':
    main()
