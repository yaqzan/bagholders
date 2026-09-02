#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase D job runner -- TP/SL blast-radius refinement, FLAT N=500 confirmation
(PREREG.md section 6). Same architecture as phaseB_run.py (loader cache,
put-skip, per-cell set_tpsl + fresh-pool-via-default-path, CSV append +
atomic per-job state, resumable, progress lines) -- differences: N_ITER=500,
the full 12-window canonical set (default), a caller-supplied --cells list
(the Phase C/B-derived finalists; NOT a hand-copied locked grid -- Phase D's
cell list is fixed only once Phase C's outcome is known, which is NOT yet
the case as of this file's authorship), and two extra evidence modes
(--fill-probe, --survivor-only) that write to DISTINCT csv/state/log file
sets so they never collide with the flat confirmation or each other.
Reuses the SAME driver/state/meta.json version pin Phase A resolved.

    python experiments/tpsl_refine_2026_08/driver/phaseD_run.py \
        --job NAME --profile core|apex --cells "tp,sl;tp,sl;..." \
        [--windows LBL[,LBL...]] [--fill-probe | --survivor-only] [--smoke-n N]

--cells format is 'tp,sl;tp,sl;...' (comma inside a cell, semicolon between
cells -- deliberately DIFFERENT from phaseB_run.py's 'tp:sl,tp:sl,...' so the
two phases' invocations are never visually confusable). The baseline cell
(0.30,-0.70) is ALWAYS auto-included as an arm, whether or not the caller's
--cells spec names it (PREREG section 6: finalists ARE evaluated against an
in-phase baseline re-run, never a remembered cert number).

Stress=base always (flat) -- this runner does NOT support a regime-
conditional (stress != base) cell. If Phase C's winner is regime-conditional,
running IT here with only its (tp,sl) base pair would silently mis-represent
it as flat; that is a spec question for the orchestrator, not something this
file guesses at (STOP-rule: report, don't improvise -- see SUBMIT_PLAN.md).

Locked spec: experiments/tpsl_refine_2026_08/{PREREG,LESSONS,TASK}.md section 6.
Engine contract: .claude/skills/run-monte-carlo/SKILL.md.

HARD RULE: this file and driver/mc_patch.py NEVER edit monte_carlo.py /
strategy_config.py / any tracked production file. All variant behavior is
in-process patching of the imported `mc` module object or os.environ set
BEFORE `import monte_carlo`. This script never git-commits anything.

Windows/multiprocessing note: monte_carlo._simulate_window builds its own
fresh multiprocessing.Pool per call (see mc_patch.apply_frozen_pins
docstring) -- on Windows (spawn) that means every worker re-imports this
script as a non-`__main__` module. Everything with side effects (arg parsing,
DB access, the cell loop) MUST stay inside `main()`, guarded by
`if __name__ == '__main__':` at the bottom -- never at module level.

TP_FILL_MISS_P verified 2026-08-10 recon (monte_carlo.py:422): a plain
`os.environ.get('TP_FILL_MISS_P', '0.0')` MODULE-LEVEL read (import-time),
exactly like TP_BASE_OV/SL_BASE_OV -- but it is CONSUMED inside `resolve()`
(monte_carlo.py:2871+, the per-outcome fill-resolution function called once
per MC iteration from run_single_sim), which DOES execute inside spawned MP
workers via `_mc_iter_worker`. Because it is a plain os.environ-derived
module global (not a value threaded through the pickled prepare-time ctx),
correctness depends on every spawned worker's OWN fresh top-level import of
monte_carlo.py re-reading the SAME env var -- which requires the env var to
already be set in THIS process's os.environ before any Pool is created
(standard multiprocessing-spawn behavior: children inherit the parent's
os.environ at spawn time). Setting os.environ['TP_FILL_MISS_P'] in the
pre-import block (same place as the frozen pins) satisfies this. No
MC_NO_MP=1 forcing needed -- this is empirically re-verified by this file's
own --fill-probe smoke path (see SUBMIT_PLAN.md validation section): a tiny
N run at TP_FILL_MISS_P=0 vs 0.10 on the SAME cell must show a measurably
different tp_rate/outcome mix, or the smoke MUST fail loudly rather than
silently accept a no-op patch (mirrors the sigma-patch smoke-acceptance
pattern LESSONS.md established for set_tpsl()).

MC_UNIVERSE_FILE (--survivor-only) is consumed ONLY during signal loading
inside `_prepare_window` (parent process, single-threaded) via
`_apply_universe_filter` -- confirmed via source; no worker-propagation
concern. LESSONS.md's 2026-08-10 "survivorship-arm-semantics" entry is
authoritative: the DEFAULT run (no MC_UNIVERSE_FILE) is ALREADY the full
delisted-inclusive honest universe; survivor_universe_811.txt is the
SURVIVOR-ONLY counterfactual used here purely for CONTRAST (an edge that
exists only in the default arm and evaporates/inverts in the survivor-only
arm is concentrated in delisted names -> FLAG per PREREG section 6).
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from contextlib import redirect_stdout

# --- repo-root bootstrap (this file lives 3 levels under the repo root:
# experiments/tpsl_refine_2026_08/driver/phaseD_run.py). Explicit + asserted,
# never inferred from CWD -- see traps.md "Worktree PYTHONPATH trap": pin
# sys.path and verify __file__ resolves where expected rather than trusting
# ambient state. Safe to re-run (idempotent) inside spawned MP workers. -------
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
META_PATH = os.path.join(STATE_DIR, 'meta.json')   # SAME file Phase A wrote -- reused, not re-resolved

# Full 12-row canonical WINDOWS set -- LOCKED, PREREG.md section 6 ("all 12
# canonical windows"). Exact label strings + order verified against
# monte_carlo.py:1745-1758 (== experiments/_mc_pinned_runner.py STANDARD_12)
# 2026-08-10. Paired-seed rule: never invent/rename labels.
PHASE_D_WINDOWS_12 = ['2018', '2020', '2020_crash', '2021', '2022', '2023', '2024',
                      'dip', '22-now', '2025', '5y', '10y']

# Amendment 2026-08-10 (PREREG section 6): fill-pessimism probe restricted to
# these 2 windows only.
FILL_PROBE_WINDOWS = ['22-now', '5y']

# PREREG section 6: survivorship arm restricted to these 2 windows only.
SURVIVOR_WINDOWS = ['2022', '22-now']

SURVIVOR_FILE = os.path.join(_REPO_ROOT, 'experiments', 'survivorship_decomposition',
                             'survivor_universe_811.txt')

BASELINE_CELL = (0.30, -0.70)   # PREREG section 1 -- ALWAYS an arm in every Phase D component.

N_ITER_FULL = 500     # PREREG section 6

CSV_FIELDS = [
    'phase', 'mode', 'profile', 'window', 'tp', 'sl', 'n_iter', 'n_call_signals',
    'mean_ret', 'med_ret', 'p10_ret', 'p90_ret', 'worst_dd', 'mean_dd',
    'p_coll', 'tp_rate', 'sl_rate', 'hard_rate', 'both_rate',
    'elapsed_prepare_s', 'elapsed_sim_s',
    'realized_call_tp_pct', 'n_calls_delisted',
]
# 'mode' (NEW vs phaseB/A): 'default' | 'fill_probe' | 'survivor_only' -- each
# writes its OWN csv file (see _paths_for_mode), but the column travels WITH
# the row too so a future concatenation across mode-files is self-describing
# rather than relying on the filename alone.
# 'n_calls_delisted' (NEW): count of DISTINCT call-signal symbols in this
# window's baked ctx['call_outcomes'] that are NOT in SURVIVOR_FILE's
# allow-list -- computed ONCE per window (identical across cells of that
# window: signal LOADING does not depend on TP/SL) and repeated on every row
# of that window. Required (per Phase D spec) to be nonzero in 'default' mode
# (proves the default universe includes delisted names, per LESSONS.md's
# survivorship-arm-semantics correction); expected to collapse toward 0 in
# 'survivor_only' mode (the universe filter already excludes them) -- that
# contrast is itself a useful engagement cross-check, so this column is
# populated in ALL THREE modes, not gated to 'default' only.


def _paths_for_mode(job, mode):
    """(csv_path, parquet_path, state_path, log_path) for this job+mode. Each
    mode gets a DISTINCT file set (task spec: 'each writing to a DISTINCT csv
    suffix') so a --fill-probe or --survivor-only run of job X never shares
    (and can never corrupt) job X's default-mode resumable state."""
    tag = {'default': 'phaseD', 'fill_probe': 'phaseD_fillprobe',
           'survivor_only': 'phaseD_survivor'}[mode]
    return (
        os.path.join(OUT_DIR, f'{tag}_{job}.csv'),
        os.path.join(OUT_DIR, f'{tag}_paths_{job}.parquet'),
        os.path.join(STATE_DIR, f'{tag}_{job}.json'),
        os.path.join(LOG_DIR, f'{tag}_{job}.log'),
    )


def parse_cells_arg(spec):
    """Parse '--cells tp,sl;tp,sl;...' (semicolon between cells, comma inside
    a cell -- deliberately different separators from phaseB_run.py's
    'tp:sl,tp:sl,...' so the two are never visually confusable). Returns a
    list of (tp, sl) float tuples with BASELINE_CELL always present (prepended
    if the caller didn't name it, left in place if they did) and duplicates
    removed (first occurrence wins, order otherwise preserved). `spec=None`
    or empty returns just [BASELINE_CELL] (a baseline-only re-cert run is a
    valid, complete invocation -- e.g. the STOP-rule 'CONFIRMED-OPTIMAL'
    path, PREREG section 3)."""
    cells = []
    if spec:
        for tok in spec.split(';'):
            tok = tok.strip()
            if not tok:
                continue
            parts = tok.split(',')
            if len(parts) != 2:
                raise SystemExit(f"--cells token {tok!r} is not 'tp,sl' (e.g. '0.15,-0.90') -- "
                                 f"note the Phase D separator is COMMA inside/SEMICOLON between, "
                                 f"NOT phaseB_run.py's colon/comma")
            try:
                tp, sl = round(float(parts[0]), 2), round(float(parts[1]), 2)
            except ValueError:
                raise SystemExit(f"--cells token {tok!r} has a non-numeric tp or sl")
            cells.append((tp, sl))
    if BASELINE_CELL not in cells:
        cells = [BASELINE_CELL] + cells
    seen = set()
    out = []
    for c in cells:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--job', required=True, help='job name -- output files are out/phaseD[_<mode>]_<job>.csv etc')
    p.add_argument('--profile', required=True, choices=['core', 'apex'])
    p.add_argument('--cells', default=None,
                    help="'tp,sl;tp,sl;...' finalist list (e.g. '0.10,-0.60;0.15,-0.90'). "
                         "Baseline (0.30,-0.70) is ALWAYS included as an extra arm even if "
                         "omitted here. Omit entirely for a baseline-only re-cert run.")
    p.add_argument('--windows', default=None,
                    help='comma-separated window labels -- subset of this mode\'s allowed set '
                         '(default mode: all 12 canonical windows; --fill-probe: 22-now,5y only; '
                         '--survivor-only: 2022,22-now only). Omit for the full allowed set.')
    mode_group = p.add_mutually_exclusive_group()
    mode_group.add_argument('--fill-probe', action='store_true',
                            help='TP_FILL_MISS_P=0.10 fill-pessimism probe (PREREG section 6 amendment); '
                                 'windows restricted to 22-now,5y; writes to a DISTINCT csv suffix')
    mode_group.add_argument('--survivor-only', action='store_true',
                            help='MC_UNIVERSE_FILE=survivor_universe_811.txt (survivor-ONLY '
                                 'counterfactual, NOT the honest universe -- see LESSONS.md); '
                                 'windows restricted to 2022,22-now; writes to a DISTINCT csv suffix')
    p.add_argument('--smoke-n', type=int, default=None, help=argparse.SUPPRESS)   # hidden validation override
    return p.parse_args()


def _load_json(path, default):
    if os.path.exists(path):
        import json
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default


def _tee(msg, log_f):
    print(msg, flush=True)
    log_f.write(msg + '\n')
    log_f.flush()


def _load_survivor_set(path):
    """frozenset of UPPER ticker symbols from survivor_universe_811.txt.
    Comment/blank lines skipped (same convention monte_carlo._load_universe_allow
    uses on the SAME file). Raises SystemExit if the file is missing -- every
    mode needs this for the n_calls_delisted diagnostic, and --survivor-only
    additionally needs it to exist for MC_UNIVERSE_FILE itself."""
    if not os.path.isfile(path):
        raise SystemExit(f"survivor universe file not found: {path!r} -- "
                         f"expected from experiments/survivorship_decomposition/ "
                         f"(P2.A survivorship decomposition); STOP, do not improvise a substitute")
    with open(path, 'r', encoding='utf-8') as f:
        return frozenset(ln.strip().upper() for ln in f
                         if ln.strip() and not ln.strip().startswith('#'))


def main():
    args = parse_args()
    import mc_patch   # local module, driver/mc_patch.py -- safe: monte_carlo not yet imported

    for d in (OUT_DIR, LOG_DIR, STATE_DIR):
        os.makedirs(d, exist_ok=True)

    mode = 'fill_probe' if args.fill_probe else 'survivor_only' if args.survivor_only else 'default'
    allowed_windows = {'default': PHASE_D_WINDOWS_12, 'fill_probe': FILL_PROBE_WINDOWS,
                       'survivor_only': SURVIVOR_WINDOWS}[mode]

    if args.windows:
        window_labels = [w.strip() for w in args.windows.split(',') if w.strip()]
        bad = [w for w in window_labels if w not in allowed_windows]
        if bad:
            raise SystemExit(f"unknown/disallowed window label(s) {bad!r} for mode={mode!r} -- "
                             f"must be a subset of {allowed_windows}; never invent/rename labels "
                             f"(paired-seed rule), and mode-restricted sets are a PREREG lock, not "
                             f"a default you can override")
    else:
        window_labels = list(allowed_windows)

    cells = parse_cells_arg(args.cells)

    # Diagnostic survivor set -- loaded regardless of mode (cheap; see
    # CSV_FIELDS comment on n_calls_delisted). Loaded BEFORE any env/import
    # step so a missing file fails fast, before any compute is spent.
    survivor_set = _load_survivor_set(SURVIVOR_FILE)

    # 1) env BEFORE import -- frozen pins, profile overrides, version pin,
    # mode-specific env (TP_FILL_MISS_P / MC_UNIVERSE_FILE).
    mc_patch.apply_frozen_pins(max_workers=6)
    mc_patch.apply_profile_env(args.profile)
    if mode == 'fill_probe':
        # Import-time module global, consumed live inside spawned MP workers'
        # OWN fresh import (see module docstring) -- setting it here, before
        # import, is both necessary and sufficient; no MC_NO_MP needed.
        os.environ['TP_FILL_MISS_P'] = '0.10'
    if mode == 'survivor_only':
        if not os.path.isfile(SURVIVOR_FILE):
            raise SystemExit(f"--survivor-only: {SURVIVOR_FILE!r} does not exist -- STOP, "
                             f"do not improvise a substitute universe file")
        os.environ['MC_UNIVERSE_FILE'] = SURVIVOR_FILE
    version_meta = mc_patch.resolve_and_pin_version(META_PATH)   # reuses Phase A's pinned meta.json

    # 2) NOW import monte_carlo -- every env var above is already set.
    import monte_carlo as mc

    # 3) post-import patches.
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    window_lookup = {label: (label, d0, d1) for label, d0, d1 in mc.WINDOWS}
    missing = [w for w in window_labels if w not in window_lookup]
    if missing:
        raise SystemExit(f"window label(s) {missing!r} not in mc.WINDOWS {sorted(window_lookup)} "
                         f"-- engine WINDOWS list drifted from PHASE_D_WINDOWS_12; STOP and report, "
                         f"do not silently drop/rename")

    n_iter = args.smoke_n if args.smoke_n else N_ITER_FULL
    mc.N_ITER = n_iter

    csv_path, parquet_path, state_path, log_path = _paths_for_mode(args.job, mode)

    state = _load_json(state_path, {'done_cells': []})
    done_set = {tuple(c) for c in state.get('done_cells', [])}   # (window, tp, sl)

    csv_is_new = not os.path.exists(csv_path)
    csv_f = open(csv_path, 'a', newline='', encoding='utf-8')
    csv_w = csv.DictWriter(csv_f, fieldnames=CSV_FIELDS)
    if csv_is_new:
        csv_w.writeheader()
        csv_f.flush()

    # Per-iteration parquet accumulator. Parquet has no cheap append, so seed
    # from any existing file (earlier cells from a prior/interrupted run of
    # THIS job+mode) and rewrite the whole file after each cell.
    path_rows = []
    pl = None
    try:
        import polars as _pl
        pl = _pl
        if os.path.exists(parquet_path):
            path_rows = pl.read_parquet(parquet_path).to_dicts()
    except ImportError:
        print("[warn] polars unavailable -- per-iteration parquet dump DISABLED "
              "for this job. med_ret/p10_ret/p90_ret in the CSV are UNAFFECTED "
              "(computed in-process from result['finals'] regardless of polars).",
              flush=True)

    log_f = open(log_path, 'a', encoding='utf-8')

    _tee(f"\n{'='*100}", log_f)
    _tee(f"JOB {args.job}  mode={mode}  profile={args.profile}  windows={window_labels}  "
         f"n_iter={n_iter}  cells={cells}", log_f)
    _tee(f"[CONFIG] algorithm_version: id={version_meta['id']} "
         f"git_commit={version_meta['git_commit']} (pinned meta={META_PATH})", log_f)
    _tee(f"[CONFIG] MAX_POSITIONS={mc.MAX_POSITIONS} "
         f"MAX_POSITIONS_CALL={mc.MAX_POSITIONS_CALL} "
         f"MAX_POSITIONS_PUT={mc.MAX_POSITIONS_PUT}", log_f)
    _tee(f"[CONFIG] TIER_ALLOC={mc.TIER_ALLOC}  PUT_TIER_ALLOC={mc.PUT_TIER_ALLOC}", log_f)
    _tee(f"[CONFIG] GROSS_PREMIUM_CAP={mc.GROSS_PREMIUM_CAP} "
         f"CALL_PREMIUM_CAP={mc.CALL_PREMIUM_CAP} "
         f"PRACTICAL_EXPOSURE_ENABLED={mc.PRACTICAL_EXPOSURE_ENABLED}", log_f)
    _tee(f"[CONFIG] CALENDAR_HOLD={mc.CALENDAR_HOLD} NOMINAL_CAL_DTE={mc.NOMINAL_CAL_DTE} "
         f"HOLD_CAL_DAYS={mc.HOLD_CAL_DAYS}", log_f)
    _tee(f"[CONFIG] MC_WORKERS={os.environ.get('MC_WORKERS')} "
         f"MC_NO_DB_PERSIST={os.environ.get('MC_NO_DB_PERSIST')} "
         f"LIQUIDITY_FLOOR={mc.LIQUIDITY_FLOOR}", log_f)
    _tee(f"[CONFIG] mode-specific: TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} "
         f"MC_UNIVERSE_FILE={getattr(mc, 'MC_UNIVERSE_FILE', None)!r}", log_f)
    _tee(f"{'='*100}", log_f)

    total_cells = len(window_labels) * len(cells)
    i_cell = 0
    cells_run_now = 0
    t_job0 = time.time()
    delisted_by_window = {}   # label -> n_calls_delisted, computed once per window (see CSV_FIELDS)

    for label in window_labels:
        _, d_start, d_end = window_lookup[label]
        for tp, sl in cells:
            i_cell += 1
            key = (label, tp, sl)
            if key in done_set:
                print(f"[{i_cell}/{total_cells}] SKIP (already done) job={args.job} mode={mode} "
                      f"window={label} tp={tp} sl={sl}", flush=True)
                continue
            cells_run_now += 1

            mc_patch.set_tpsl(mc, tp, sl)   # stress=base (flat) -- Phase D is a FLAT confirmation only

            t0 = time.perf_counter()
            # Capture prepare's stdout into an in-memory buffer (not just the
            # log file) so --survivor-only can grep it programmatically for
            # the '[universe-filter]' engagement line BEFORE trusting this
            # cell's results -- task spec: FATAL if absent.
            buf = io.StringIO()
            with redirect_stdout(buf):
                ctx = mc._prepare_window(label, d_start, d_end, version_meta['id'])
            t_prepare = time.perf_counter() - t0
            captured = buf.getvalue()
            log_f.write(captured)
            log_f.flush()

            if mode == 'survivor_only' and '[universe-filter]' not in captured:
                # The engine prints '[universe-filter]' inside load_signals, which the
                # loader-memoization cache suppresses on every cache-HIT prepare (the
                # cached signal list was already filtered by the cache-MISS prepare that
                # populated it -- cell 1 of the window). Engagement is therefore proven
                # by the direct property instead: every loaded call-signal symbol must
                # be inside the survivor file. Only if THAT fails is the filter truly
                # not engaged -> FATAL per PREREG mechanics.
                _syms = {k[0] for k in ctx['call_outcomes'].keys()}
                _n_out = sum(1 for s in _syms if str(s).upper() not in survivor_set)
                if _n_out > 0:
                    csv_f.close()
                    log_f.close()
                    raise SystemExit(
                        f"FATAL: --survivor-only set MC_UNIVERSE_FILE={SURVIVOR_FILE!r} but the "
                        f"prepare for job={args.job} window={label} tp={tp} sl={sl} shows NO "
                        f"'[universe-filter]' engagement line AND {_n_out} loaded symbols are "
                        f"OUTSIDE the survivor file -- the filter did not fire. STOP per PREREG "
                        f"mechanics (the survivor arm must be PROVABLY engaged, never silently "
                        f"trusted). Captured prepare output: {log_path}; do not improvise.")
                _tee(f"[universe-check] window={label} tp={tp} sl={sl}: engagement proven via "
                     f"direct property on a cache-hit prepare ({len(_syms)} call-signal symbols, "
                     f"0 outside survivor file; engine print suppressed by loader cache)", log_f)

            if label not in delisted_by_window:
                call_syms = {k[0] for k in ctx['call_outcomes'].keys()}
                n_delisted = sum(1 for s in call_syms if str(s).upper() not in survivor_set)
                delisted_by_window[label] = n_delisted
                _tee(f"[universe-check] window={label} mode={mode}: "
                     f"{len(call_syms)} distinct call-signal symbols, "
                     f"{n_delisted} NOT in survivor_universe_811.txt "
                     f"({'expected >0 -- proves default universe includes delisted names' if mode == 'default' else 'expect ~0 in survivor_only mode'})",
                     log_f)
            n_calls_delisted = delisted_by_window[label]

            n_calls, tp_rate, sl_rate, hard_rate, both_rate = mc_patch.call_outcome_rates(ctx)

            t1 = time.perf_counter()
            with redirect_stdout(log_f):
                sim = mc._simulate_window(ctx)   # no external pool -> fresh pool, own teardown
            t_sim = time.perf_counter() - t1
            result = sim['seeded']

            finals = result.get('finals')
            p10_ret = p90_ret = None
            if finals:
                rets_pct = sorted((f / mc.STARTING_CASH - 1.0) * 100.0 for f in finals)
                p10_ret = mc_patch.pct(rets_pct, 0.10)
                p90_ret = mc_patch.pct(rets_pct, 0.90)
                for i_iter, r in enumerate(rets_pct):
                    path_rows.append({'mode': mode, 'profile': args.profile, 'window': label,
                                      'tp': tp, 'sl': sl, 'iter': i_iter, 'ret': r})

            row = {
                'phase': 'D', 'mode': mode, 'profile': args.profile, 'window': label,
                'tp': tp, 'sl': sl, 'n_iter': n_iter, 'n_call_signals': n_calls,
                'mean_ret': result.get('mean_ret'), 'med_ret': result.get('med_ret'),
                'p10_ret': p10_ret, 'p90_ret': p90_ret,
                'worst_dd': result.get('worst_dd'), 'mean_dd': result.get('mean_dd'),
                'p_coll': result.get('p_coll'),
                'tp_rate': tp_rate, 'sl_rate': sl_rate, 'hard_rate': hard_rate,
                'both_rate': both_rate,
                'elapsed_prepare_s': round(t_prepare, 3), 'elapsed_sim_s': round(t_sim, 3),
                'realized_call_tp_pct': result.get('call_tp'),
                'n_calls_delisted': n_calls_delisted,
            }
            csv_w.writerow(row)
            csv_f.flush()

            if pl is not None:
                try:
                    pl.DataFrame(path_rows).write_parquet(parquet_path)
                except Exception as e:
                    print(f"[warn] parquet write failed ({e}); continuing (CSV is authoritative)", flush=True)

            done_set.add(key)
            state['done_cells'] = [list(k) for k in sorted(done_set)]
            state['job'] = args.job
            state['mode'] = mode
            state['profile'] = args.profile
            state['windows'] = window_labels
            state['cells'] = [list(c) for c in cells]
            state['n_iter'] = n_iter
            state['algorithm_version'] = version_meta
            mc_patch.atomic_write_json(state_path, state)

            net_csl = mc.NET_SL_BASE
            print(f"[{i_cell}/{total_cells}] job={args.job} mode={mode} window={label} "
                  f"tp={tp:+.2f} sl={sl:+.2f} net_csl={net_csl:+.3f} n={n_iter} ncalls={n_calls} "
                  f"n_delisted={n_calls_delisted} prepare={t_prepare:.1f}s sim={t_sim:.1f}s | "
                  f"tp_rate={tp_rate:.1f}% sl_rate={sl_rate:.1f}% hard_rate={hard_rate:.1f}% | "
                  f"worst_dd={result.get('worst_dd'):.1f}% med_ret={result.get('med_ret'):+.1f}% "
                  f"p_coll={result.get('p_coll'):.1f}%", flush=True)

    csv_f.close()
    elapsed_job = time.time() - t_job0
    _tee(f"\n[DONE] job={args.job} mode={mode} cells_run_this_invocation={cells_run_now} "
         f"total_done={len(done_set)}/{total_cells} wall={elapsed_job:.1f}s "
         f"-> {csv_path}", log_f)
    log_f.close()


if __name__ == '__main__':
    main()
