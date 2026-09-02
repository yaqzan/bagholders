#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase C job runner -- TP/SL regime/market-wave-conditional sweep
(PREREG.md section 5 + the 2026-08-10 CLARIFICATIONS block). Thin variant of
phaseB_run.py: SAME architecture (loader cache, put-skip, fresh-pool-via-
default-path, CSV append + atomic per-job state, resumable, progress lines,
frozen pins, profile env recipes, paired window labels) -- the grid is now
PARAMETERIZED by a base (TP,SL) pair given at submit time (the Phase-B winner,
or the incumbent under the PREREG section 3 stop rule) and conditioned on one
of four market-state sources via driver/phaseC_patch.py instead of being a
flat TP/SL cell.

    python experiments/tpsl_refine_2026_08/driver/phaseC_run.py \\
        --job NAME --profile core|apex --base-tp F --base-sl F \\
        --windows LBL[,LBL...] [--sources s1,s2,...] [--smoke]

    python experiments/tpsl_refine_2026_08/driver/phaseC_run.py \\
        --job NAME --profile core|apex --validate-only

Locked spec: experiments/tpsl_refine_2026_08/{PREREG,LESSONS,TASK}.md section 5.
Engine contract: .claude/skills/run-monte-carlo/SKILL.md.
Injection mechanism + predicate derivation + trap evidence: driver/phaseC_patch.py
module docstring (read that FIRST if anything here looks surprising).

HARD RULE: this file, driver/phaseC_patch.py, and driver/mc_patch.py NEVER
edit monte_carlo.py / strategy_config.py / any tracked production file, and
this file never edits driver/mc_patch.py / driver/phaseA_run.py /
driver/phaseB_run.py either (Phase B is running concurrently off those exact
files as of 2026-08-10 -- new files only). All variant behavior is in-process
patching of the imported `mc` module object. This script never git-commits
anything, and this BUILDER does not invoke the full ~56-cell/profile grid --
only --validate-only (the mandatory injection-safety battery). The
orchestrator submits the real sweep once the Phase B winner pair is known,
using --base-tp/--base-sl from that result.

Windows/multiprocessing note: monte_carlo._simulate_window builds its own
fresh multiprocessing.Pool per call (see mc_patch.apply_frozen_pins
docstring) -- on Windows (spawn) that means every worker re-imports this
script as a non-`__main__` module. Everything with side effects (arg parsing,
DB access, the cell loop) MUST stay inside `main()`/its helpers, guarded by
`if __name__ == '__main__':` at the bottom -- never at module level.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from contextlib import redirect_stdout

# --- repo-root bootstrap (this file lives 3 levels under the repo root:
# experiments/tpsl_refine_2026_08/driver/phaseC_run.py). Explicit + asserted,
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

N_ITER_SMOKE = 20

# Mandatory identity-validation battery constants -- PREREG section 5
# CLARIFICATIONS + the Phase C build spec's literal "incumbent base pair
# (0.30,-0.70)" / "same window label (22-now)" / "N=50" instructions.
# HARDCODED regardless of --base-tp/--base-sl (validation proves the
# INJECTION MECHANISM is leak-free; it is not a science run, and Phase B's
# winner is not known yet when this validation is built/run).
VALIDATE_TP, VALIDATE_SL = 0.30, -0.70
VALIDATE_WINDOW = '22-now'          # full prepare+simulate identity check
VALIDATE_FRAC_WINDOW = '2022'       # prepare-only supply-sanity fired-fraction check
VALIDATE_N = 50
# (source, thr) arms validated, in report order. breadth validated at BOTH
# thresholds (spec only requires one identity cell per source, but running
# both thr40 AND thr50 identity checks is strictly more validation at
# negligible extra cost, AND directly produces the thr40-vs-50 differential
# classification evidence the spec separately asks for -- see phaseC_patch.py
# docstring's "MANDATORY IDENTITY VALIDATION" report section).
VALIDATE_ARMS = [('breadth', 40), ('breadth', 50), ('mwdd_band', None),
                  ('rxdd_band', None), ('regime_down', None)]

CSV_FIELDS = [
    'phase', 'profile', 'window', 'tp', 'sl', 'n_iter', 'n_call_signals',
    'mean_ret', 'med_ret', 'p10_ret', 'p90_ret', 'worst_dd', 'mean_dd',
    'p_coll', 'tp_rate', 'sl_rate', 'hard_rate', 'both_rate',
    'elapsed_prepare_s', 'elapsed_sim_s',
    'realized_call_tp_pct',
    'source', 'thr', 'tp_stress', 'sl_stress', 'stressed_call_frac',
    'dte_routed_frac',
]
# Columns beyond a byte-for-byte copy of phaseB_run.py's CSV_FIELDS (per the
# Phase C build spec: "CSV = phaseB schema + columns: source, thr (blank for
# non-breadth), tp_stress, sl_stress, stressed_call_frac"):
#   'tp'/'sl' now carry the BASE pair (constant across a whole job/profile --
#   what --base-tp/--base-sl set), so every row is self-describing without
#   needing external context; 'tp_stress'/'sl_stress' carry the cell's actual
#   (possibly source-conditioned) stress levels.
#   'source' is 'flat_base' for the always-present in-phase paired baseline
#   row (PREREG CLARIFICATIONS), else one of phaseC_patch.SOURCES.
#   'stressed_call_frac' is the deterministic (N_ITER-independent) fraction
#   of this window's call signals classified stressed under the active
#   source/thr -- phaseC_patch.stress_source_diagnostics, same data source as
#   the existing tp_rate/sl_rate columns (baked ctx['call_outcomes']).
#   'dte_routed_frac' is a FREE bonus beyond the spec's literal column list
#   (same precedent as phaseB_run.py adding 'realized_call_tp_pct' beyond
#   phaseA's schema): the % of this window's call_outcomes that were silently
#   rerouted through monte_carlo_15dte.py's OWN separate is_stressed/TP-SL
#   state via the DTE-router second pass (DTE_ROUTER_ENABLED=True,
#   SCORE_MIN=80, DAY_CAP=1 in STRATEGY_30DTE) -- see phaseC_patch.py's
#   module docstring "CAVEAT DISCOVERED" section. Zero extra cost (read off
#   the same ctx['call_outcomes'] dict); highly relevant for interpreting
#   how COMPLETE a given cell's predicate coverage actually is.


def parse_args():
    import phaseC_patch   # local module -- safe here too (no DB/monte_carlo import at its module level)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job', required=True, help='job name -- output files are out/phaseC_<job>.csv etc')
    p.add_argument('--profile', required=True, choices=['core', 'apex'])
    p.add_argument('--base-tp', type=float, default=None,
                    help='base-pair TP (the Phase-B winner or incumbent); required unless --validate-only')
    p.add_argument('--base-sl', type=float, default=None,
                    help='base-pair SL (the Phase-B winner or incumbent); required unless --validate-only')
    p.add_argument('--windows', default=None,
                    help='comma-separated subset of the locked Phase C window set '
                         f'{list(phaseC_patch.PHASE_C_WINDOWS)}; required unless --validate-only '
                         '(paired-seed rule: never invent/rename labels)')
    p.add_argument('--sources', default=None,
                    help="comma-separated subset of {'breadth','mwdd_band','rxdd_band','regime_down'} "
                         '(default: all 4). Canonicalized to that fixed order regardless of input order.')
    p.add_argument('--validate-only', action='store_true',
                    help='run the mandatory injection-safety identity battery instead of the main grid '
                         '(ignores --base-tp/--base-sl/--windows/--sources; see VALIDATE_* constants)')
    p.add_argument('--smoke', action='store_true',
                    help='main-grid mode only: 1 cell/source at N_ITER=20 instead of the full grid x N=300')
    p.add_argument('--n-iter', type=int, default=None,
                    help='override N_ITER (Phase D conditional confirm uses 500; default = smoke 20 / full 300)')
    p.add_argument('--only-stress-cell', default=None,
                    help='"tp,sl" -- restrict the source grid to cells with this exact (tp_stress, sl_stress); '
                         'used by the Phase D conditional confirm to re-run one pre-registered cell at N=500')
    args = p.parse_args()
    if not args.validate_only:
        missing = [f for f in ('base_tp', 'base_sl', 'windows') if getattr(args, f) is None]
        if missing:
            raise SystemExit(f"--{missing[0].replace('_', '-')} is required unless --validate-only")
    return args


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


def _print_config(mc, version_meta, log_f, extra=''):
    _tee(f"[CONFIG] algorithm_version: id={version_meta['id']} "
         f"git_commit={version_meta['git_commit']} (pinned meta={META_PATH})", log_f)
    _tee(f"[CONFIG] MAX_POSITIONS={mc.MAX_POSITIONS} MAX_POSITIONS_CALL={mc.MAX_POSITIONS_CALL} "
         f"MAX_POSITIONS_PUT={mc.MAX_POSITIONS_PUT}", log_f)
    _tee(f"[CONFIG] TIER_ALLOC={mc.TIER_ALLOC}  PUT_TIER_ALLOC={mc.PUT_TIER_ALLOC}", log_f)
    _tee(f"[CONFIG] GROSS_PREMIUM_CAP={mc.GROSS_PREMIUM_CAP} CALL_PREMIUM_CAP={mc.CALL_PREMIUM_CAP} "
         f"PRACTICAL_EXPOSURE_ENABLED={mc.PRACTICAL_EXPOSURE_ENABLED}", log_f)
    _tee(f"[CONFIG] CALENDAR_HOLD={mc.CALENDAR_HOLD} NOMINAL_CAL_DTE={mc.NOMINAL_CAL_DTE} "
         f"HOLD_CAL_DAYS={mc.HOLD_CAL_DAYS}", log_f)
    _tee(f"[CONFIG] MC_WORKERS={os.environ.get('MC_WORKERS')} MC_NO_DB_PERSIST={os.environ.get('MC_NO_DB_PERSIST')} "
         f"LIQUIDITY_FLOOR={mc.LIQUIDITY_FLOOR}", log_f)
    _tee(f"[CONFIG] DTE_ROUTER_ENABLED={mc.DTE_ROUTER_ENABLED} DTE_ROUTER_TARGET_DTE={mc.DTE_ROUTER_TARGET_DTE} "
         f"DTE_ROUTER_SCORE_MIN={mc.DTE_ROUTER_SCORE_MIN} DTE_ROUTER_DAY_CAP={mc.DTE_ROUTER_DAY_CAP}  "
         f"(routed signals bypass BOTH this predicate and set_tpsl -- see phaseC_patch.py docstring)", log_f)
    if extra:
        _tee(extra, log_f)


def _nearly_equal(a, b, tol=1e-9):
    if a is None or b is None:
        return a == b
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def _row_common(mc, phase_tag, profile, window, tp_b, sl_b, source, thr, ts, ss, n_iter,
                 ctx, mc_patch, phaseC_patch, t_prepare, t_sim=None, sim_result=None):
    n_calls, tp_rate, sl_rate, hard_rate, both_rate = mc_patch.call_outcome_rates(ctx)
    n_stress, stressed_frac, n_dte, dte_frac = phaseC_patch.stress_source_diagnostics(ctx)
    row = {
        'phase': phase_tag, 'profile': profile, 'window': window,
        'tp': tp_b, 'sl': sl_b, 'n_iter': n_iter, 'n_call_signals': n_calls,
        'mean_ret': None, 'med_ret': None, 'p10_ret': None, 'p90_ret': None,
        'worst_dd': None, 'mean_dd': None, 'p_coll': None,
        'tp_rate': tp_rate, 'sl_rate': sl_rate, 'hard_rate': hard_rate, 'both_rate': both_rate,
        'elapsed_prepare_s': round(t_prepare, 3), 'elapsed_sim_s': round(t_sim, 3) if t_sim is not None else None,
        'realized_call_tp_pct': None,
        'source': source, 'thr': thr if thr is not None else '',
        'tp_stress': ts, 'sl_stress': ss,
        'stressed_call_frac': round(stressed_frac, 2) if stressed_frac is not None else None,
        'dte_routed_frac': round(dte_frac, 2) if dte_frac is not None else None,
    }
    if sim_result is not None:
        finals = sim_result.get('finals')
        p10_ret = p90_ret = None
        rets_pct = None
        if finals:
            rets_pct = sorted((f / mc.STARTING_CASH - 1.0) * 100.0 for f in finals)
            p10_ret = mc_patch.pct(rets_pct, 0.10)
            p90_ret = mc_patch.pct(rets_pct, 0.90)
        row.update({
            'mean_ret': sim_result.get('mean_ret'), 'med_ret': sim_result.get('med_ret'),
            'p10_ret': p10_ret, 'p90_ret': p90_ret,
            'worst_dd': sim_result.get('worst_dd'), 'mean_dd': sim_result.get('mean_dd'),
            'p_coll': sim_result.get('p_coll'),
            'realized_call_tp_pct': sim_result.get('call_tp'),
        })
    return row


def run_validate(mc, mc_patch, phaseC_patch, args, version_meta, window_lookup):
    """Mandatory injection-safety battery (PREREG section 5 CLARIFICATIONS +
    Phase C build spec). Two parts:
      1. IDENTITY: at VALIDATE_WINDOW ('22-now'), VALIDATE_N=50, the incumbent
         base pair (0.30,-0.70) -- run one UNPATCHED flat baseline plus one
         patched identity cell (tp_stress==tp_base, sl_stress==sl_base) per
         VALIDATE_ARMS entry (5: breadth@40, breadth@50, mwdd_band, rxdd_band,
         regime_down). Since TP_SIGMA_STRESS==TP_SIGMA_BASE bit-for-bit
         whenever tp_stress==tp_base (same for SL), compute_trade_outcome's
         `tp_sigma = TP_SIGMA_STRESS if stressed else TP_SIGMA_BASE` selects
         between two IDENTICAL floats regardless of the `stressed` boolean --
         so a leak-free patch must reproduce mean_ret/worst_dd EXACTLY. Any
         drift is proof the patch reaches somewhere it shouldn't.
      2. FIRED-FRACTION: prepare-only (no simulate -- fired-fraction is a
         deterministic property of ctx['call_outcomes'], independent of
         N_ITER) at VALIDATE_FRAC_WINDOW ('2022') for the same 5 arms, plus
         the already-computed VALIDATE_WINDOW fractions from part 1 -- gives
         a 22-now and a 2022 fired-fraction per predicate (supply/degeneracy
         sanity) at near-zero extra compute.
    Writes out/phaseC_validate_<job>.csv (phase='C_VALIDATE') and
    out/phaseC_validate_<job>_summary.md (short, human-readable PASS/FAIL +
    fired-fraction report -- this is the file to actually read afterward,
    never the raw log). Resumable via driver/state/phaseC_validate_<job>.json
    exactly like the main grid (same atomic-write/done-set pattern).
    Returns True if every identity arm PASSED, False otherwise (used as the
    process exit code so `trader queue wait` / a foreground check sees
    failure without anyone reading the log)."""
    csv_path = os.path.join(OUT_DIR, f'phaseC_validate_{args.job}.csv')
    state_path = os.path.join(STATE_DIR, f'phaseC_validate_{args.job}.json')
    log_path = os.path.join(LOG_DIR, f'phaseC_validate_{args.job}.log')
    summary_path = os.path.join(OUT_DIR, f'phaseC_validate_{args.job}_summary.md')

    state = _load_json(state_path, {'done_cells': []})
    done_set = {tuple(c) for c in state.get('done_cells', [])}   # (window, source, thr_str)

    csv_is_new = not os.path.exists(csv_path)
    csv_f = open(csv_path, 'a', newline='', encoding='utf-8')
    csv_w = csv.DictWriter(csv_f, fieldnames=CSV_FIELDS)
    if csv_is_new:
        csv_w.writeheader()
        csv_f.flush()
    log_f = open(log_path, 'a', encoding='utf-8')

    _tee(f"\n{'='*100}", log_f)
    _tee(f"VALIDATE job={args.job} profile={args.profile} tp={VALIDATE_TP} sl={VALIDATE_SL} "
         f"identity_window={VALIDATE_WINDOW} frac_window={VALIDATE_FRAC_WINDOW} n={VALIDATE_N} "
         f"arms={VALIDATE_ARMS}", log_f)
    _print_config(mc, version_meta, log_f)
    _tee(f"{'='*100}", log_f)

    original_is_stressed = mc.is_stressed
    original_threshold = mc.BREADTH_THRESHOLD

    label_v, d0_v, d1_v = window_lookup[VALIDATE_WINDOW]
    label_f, d0_f, d1_f = window_lookup[VALIDATE_FRAC_WINDOW]
    maps_v = phaseC_patch.load_window_maps(mc, d0_v, d1_v)
    maps_f = phaseC_patch.load_window_maps(mc, d0_f, d1_f)

    mc.N_ITER = VALIDATE_N
    identity_rows = {}   # source_tag -> row dict (from VALIDATE_WINDOW, simulated)
    frac_rows = {}        # source_tag -> row dict (from VALIDATE_FRAC_WINDOW, prepare-only)

    def _run_cell(source, thr, window_label, d_start, d_end, maps, do_simulate):
        phaseC_patch.activate_source(mc, source, thr, maps, original_is_stressed, original_threshold)
        mc_patch.set_tpsl(mc, VALIDATE_TP, VALIDATE_SL)   # tp_stress/sl_stress default to base -> IDENTITY
        t0 = time.perf_counter()
        with redirect_stdout(log_f):
            ctx = mc._prepare_window(window_label, d_start, d_end, version_meta['id'])
        t_prepare = time.perf_counter() - t0
        sim_result = None
        t_sim = None
        if do_simulate:
            t1 = time.perf_counter()
            with redirect_stdout(log_f):
                sim = mc._simulate_window(ctx)
            t_sim = time.perf_counter() - t1
            sim_result = sim['seeded']
        row = _row_common(mc, 'C_VALIDATE', args.profile, window_label, VALIDATE_TP, VALIDATE_SL,
                           source, thr, VALIDATE_TP, VALIDATE_SL, VALIDATE_N if do_simulate else 0,
                           ctx, mc_patch, phaseC_patch, t_prepare, t_sim, sim_result)
        return row

    # --- part 1: identity battery at VALIDATE_WINDOW (simulate) ---
    key = (VALIDATE_WINDOW, 'flat_base', '')
    if key not in done_set:
        row = _run_cell('flat_base', None, label_v, d0_v, d1_v, None, do_simulate=True)
        csv_w.writerow(row); csv_f.flush()
        identity_rows['flat_base'] = row
        done_set.add(key)
        state['done_cells'] = [list(k) for k in sorted(done_set)]
        mc_patch.atomic_write_json(state_path, state)
        print(f"[VALIDATE] flat_base  22-now  mean_ret={row['mean_ret']:+.2f}%  "
              f"worst_dd={row['worst_dd']:.2f}%", flush=True)
    else:
        print("[VALIDATE] SKIP (already done) flat_base 22-now -- re-reading requires the CSV; "
              "re-run without a stale state file to regenerate in-memory comparison", flush=True)

    for source, thr in VALIDATE_ARMS:
        tag = f"{source}@{thr}" if thr is not None else source
        key = (VALIDATE_WINDOW, source, str(thr) if thr is not None else '')
        if key not in done_set:
            row = _run_cell(source, thr, label_v, d0_v, d1_v, maps_v, do_simulate=True)
            csv_w.writerow(row); csv_f.flush()
            identity_rows[tag] = row
            done_set.add(key)
            state['done_cells'] = [list(k) for k in sorted(done_set)]
            mc_patch.atomic_write_json(state_path, state)
            print(f"[VALIDATE] {tag:<14s} 22-now  mean_ret={row['mean_ret']:+.2f}%  "
                  f"worst_dd={row['worst_dd']:.2f}%  stressed_frac={row['stressed_call_frac']}%", flush=True)
        else:
            print(f"[VALIDATE] SKIP (already done) {tag} 22-now", flush=True)

    # --- part 2: fired-fraction supply sanity at VALIDATE_FRAC_WINDOW (prepare-only) ---
    for source, thr in VALIDATE_ARMS:
        tag = f"{source}@{thr}" if thr is not None else source
        key = (VALIDATE_FRAC_WINDOW, source, str(thr) if thr is not None else '')
        if key not in done_set:
            row = _run_cell(source, thr, label_f, d0_f, d1_f, maps_f, do_simulate=False)
            csv_w.writerow(row); csv_f.flush()
            frac_rows[tag] = row
            done_set.add(key)
            state['done_cells'] = [list(k) for k in sorted(done_set)]
            mc_patch.atomic_write_json(state_path, state)
            print(f"[VALIDATE] {tag:<14s} 2022 (prepare-only)  stressed_frac={row['stressed_call_frac']}%  "
                  f"dte_routed_frac={row['dte_routed_frac']}%", flush=True)
        else:
            print(f"[VALIDATE] SKIP (already done) {tag} 2022", flush=True)

    csv_f.close()

    # Re-read the CSV so the summary is correct even on a resumed (partially
    # skipped) run where some rows live only on disk from an earlier invocation.
    with open(csv_path, newline='', encoding='utf-8') as f:
        all_rows = list(csv.DictReader(f))
    by_key = {}
    for r in all_rows:
        by_key[(r['window'], r['source'], r['thr'])] = r
    flat = by_key.get((VALIDATE_WINDOW, 'flat_base', ''))

    all_pass = True
    lines = []
    lines.append("# Phase C injection-safety validation -- MANDATORY IDENTITY BATTERY")
    lines.append("")
    lines.append(f"job={args.job} profile={args.profile} base=({VALIDATE_TP},{VALIDATE_SL}) "
                  f"window={VALIDATE_WINDOW} n={VALIDATE_N}")
    lines.append("")
    lines.append("## Identity check (patched identity cell must reproduce the unpatched flat baseline EXACTLY)")
    lines.append("")
    if flat is None:
        lines.append("**FAIL -- flat_base row missing, cannot compare.**")
        all_pass = False
    else:
        flat_mean, flat_dd = float(flat['mean_ret']), float(flat['worst_dd'])
        lines.append(f"Unpatched flat baseline: mean_ret={flat_mean:+.6f}%  worst_dd={flat_dd:.6f}%")
        lines.append("")
        lines.append("| arm | mean_ret | worst_dd | mean_ret delta | worst_dd delta | verdict |")
        lines.append("|---|---|---|---|---|---|")
        for source, thr in VALIDATE_ARMS:
            tag = f"{source}@{thr}" if thr is not None else source
            r = by_key.get((VALIDATE_WINDOW, source, str(thr) if thr is not None else ''))
            if r is None:
                lines.append(f"| {tag} | -- | -- | -- | -- | **MISSING** |")
                all_pass = False
                continue
            m, dd = float(r['mean_ret']), float(r['worst_dd'])
            dm, ddd = m - flat_mean, dd - flat_dd
            ok = _nearly_equal(m, flat_mean) and _nearly_equal(dd, flat_dd)
            all_pass = all_pass and ok
            verdict = 'PASS' if ok else '**FAIL -- INJECTION LEAK**'
            lines.append(f"| {tag} | {m:+.6f}% | {dd:.6f}% | {dm:+.2e} | {ddd:+.2e} | {verdict} |")
    lines.append("")
    lines.append("## Fired-fraction (supply sanity -- <2% or >90% on 22-now or 2022 is DEGENERATE)")
    lines.append("")
    lines.append("| arm | 22-now stressed_frac | 22-now dte_routed_frac | 2022 stressed_frac | "
                  "2022 dte_routed_frac | flag |")
    lines.append("|---|---|---|---|---|---|")
    for source, thr in VALIDATE_ARMS:
        tag = f"{source}@{thr}" if thr is not None else source
        r22 = by_key.get((VALIDATE_WINDOW, source, str(thr) if thr is not None else ''))
        r_2022 = by_key.get((VALIDATE_FRAC_WINDOW, source, str(thr) if thr is not None else ''))
        f22 = r22['stressed_call_frac'] if r22 else ''
        d22 = r22['dte_routed_frac'] if r22 else ''
        f2022 = r_2022['stressed_call_frac'] if r_2022 else ''
        d2022 = r_2022['dte_routed_frac'] if r_2022 else ''
        flag = ''
        for v in (f22, f2022):
            try:
                fv = float(v)
                if fv < 2.0 or fv > 90.0:
                    flag = 'DEGENERATE'
            except (TypeError, ValueError):
                pass
        lines.append(f"| {tag} | {f22}% | {d22}% | {f2022}% | {d2022}% | {flag} |")
    lines.append("")
    b40 = by_key.get((VALIDATE_WINDOW, 'breadth', '40'))
    b50 = by_key.get((VALIDATE_WINDOW, 'breadth', '50'))
    if b40 and b50:
        lines.append(f"breadth thr40 vs thr50 classification-count difference (22-now): "
                      f"{b40['stressed_call_frac']}% vs {b50['stressed_call_frac']}% "
                      f"(n_call_signals={b40['n_call_signals']}) -- "
                      f"{'DIFFERENT (expected)' if b40['stressed_call_frac'] != b50['stressed_call_frac'] else 'IDENTICAL (unexpected -- check BREADTH_THRESHOLD is actually being read)'}")
    lines.append("")
    lines.append(f"## Overall verdict: {'PASS' if all_pass else 'FAIL'}")
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    _tee(f"\n[DONE] VALIDATE job={args.job} -> {csv_path}  summary -> {summary_path}  "
         f"overall={'PASS' if all_pass else 'FAIL'}", log_f)
    log_f.close()
    return all_pass


def run_main_grid(mc, mc_patch, phaseC_patch, args, version_meta, window_lookup):
    """The real ~56-cell/profile Phase C sweep for a given --base-tp/--base-sl.
    NOT invoked by this builder session (Phase B's winner is not known yet) --
    built and proven via run_validate() instead. The orchestrator runs this
    once Phase B lands, via the submit command template in the builder's
    final report."""
    tp_b = phaseC_patch._r2(args.base_tp)
    sl_b = phaseC_patch._r2(args.base_sl)

    window_labels = [w.strip() for w in args.windows.split(',') if w.strip()]
    # PREREG section 5: screen runs on the locked 5-window set; a cell that passes the
    # screen is then "evaluated on the 9-window set" -- so the four remaining canonical
    # Phase-B labels are legal here for the pre-registered follow-up (engine-preset
    # labels, so paired seeds are preserved). Anything else is still rejected.
    _FOLLOWUP_WINDOWS = ('2021', '2023', '2025', 'dip', '2018', '2020', '10y')  # PREREG follow-up + D-confirm full-12 coverage; all engine-preset labels
    _allowed = set(phaseC_patch.PHASE_C_WINDOWS) | set(_FOLLOWUP_WINDOWS)
    bad = [w for w in window_labels if w not in _allowed]
    if bad:
        raise SystemExit(f"unknown Phase C window label(s) {bad!r} -- must be a subset of "
                          f"the locked screen set {list(phaseC_patch.PHASE_C_WINDOWS)} plus the "
                          f"pre-registered follow-up set {list(_FOLLOWUP_WINDOWS)}; never invent/rename labels")
    missing = [w for w in window_labels if w not in window_lookup]
    if missing:
        raise SystemExit(f"window label(s) {missing!r} not in mc.WINDOWS {sorted(window_lookup)} "
                          f"-- engine WINDOWS list drifted from the locked Phase C set")

    if args.sources is None:
        sources = list(phaseC_patch.SOURCES)
    else:
        requested = {s.strip() for s in args.sources.split(',') if s.strip()}
        bad_src = requested - set(phaseC_patch.SOURCES)
        if bad_src:
            raise SystemExit(f"--sources token(s) {sorted(bad_src)} not in {phaseC_patch.SOURCES}")
        sources = [s for s in phaseC_patch.SOURCES if s in requested]   # canonical order

    csv_path = os.path.join(OUT_DIR, f'phaseC_{args.job}.csv')
    parquet_path = os.path.join(OUT_DIR, f'phaseC_paths_{args.job}.parquet')
    state_path = os.path.join(STATE_DIR, f'phaseC_{args.job}.json')
    log_path = os.path.join(LOG_DIR, f'phaseC_{args.job}.log')

    state = _load_json(state_path, {'done_cells': []})
    done_set = {tuple(c) for c in state.get('done_cells', [])}   # (window, source, thr_str, tp_stress, sl_stress)

    csv_is_new = not os.path.exists(csv_path)
    csv_f = open(csv_path, 'a', newline='', encoding='utf-8')
    csv_w = csv.DictWriter(csv_f, fieldnames=CSV_FIELDS)
    if csv_is_new:
        csv_w.writeheader()
        csv_f.flush()

    path_rows = []
    pl = None
    try:
        import polars as _pl
        pl = _pl
        if os.path.exists(parquet_path):
            path_rows = pl.read_parquet(parquet_path).to_dicts()
    except ImportError:
        print("[warn] polars unavailable -- per-iteration parquet dump DISABLED for this job. "
              "med_ret/p10_ret/p90_ret in the CSV are UNAFFECTED.", flush=True)

    log_f = open(log_path, 'a', encoding='utf-8')
    _tee(f"\n{'='*100}", log_f)
    _tee(f"JOB {args.job}  profile={args.profile}  base=({tp_b},{sl_b})  windows={window_labels}  "
         f"sources={sources}  smoke={args.smoke}", log_f)
    _print_config(mc, version_meta, log_f)
    _tee(f"{'='*100}", log_f)

    original_is_stressed = mc.is_stressed
    original_threshold = mc.BREADTH_THRESHOLD
    n_iter = args.n_iter if args.n_iter else (N_ITER_SMOKE if args.smoke else phaseC_patch.N_ITER_FULL)
    mc.N_ITER = n_iter

    # cell plan per window: always the flat baseline first, then the
    # requested sources' grid (smoke mode: 1 arbitrary cell per requested
    # source instead of the full ~11/22-cell grid, for a cheap end-to-end check).
    if args.smoke:
        full_grid = phaseC_patch.build_all_cells(sources, tp_b, sl_b)
        seen_src = set()
        grid = []
        for cell in full_grid:
            src = cell[0]
            if src in seen_src:
                continue
            seen_src.add(src)
            grid.append(cell)
    else:
        grid = phaseC_patch.build_all_cells(sources, tp_b, sl_b)

    if args.only_stress_cell:
        _t, _s = (round(float(x), 2) for x in args.only_stress_cell.split(','))
        grid = [c for c in grid if (round(float(c[2]), 2), round(float(c[3]), 2)) == (_t, _s)]
        if not grid:
            raise SystemExit(f"--only-stress-cell {args.only_stress_cell} matches no cell in the "
                             f"pre-registered grid for base ({tp_b},{sl_b}) -- refusing to invent cells")

    needs_maps = any(s != 'breadth' for s in sources)
    total_cells = len(window_labels) * (1 + len(grid))
    i_cell = 0
    cells_run_now = 0
    t_job0 = time.time()

    for label in window_labels:
        _, d_start, d_end = window_lookup[label]
        maps = phaseC_patch.load_window_maps(mc, d_start, d_end) if needs_maps else None

        # flat baseline row -- always run once per window regardless of --sources
        i_cell += 1
        key = (label, 'flat_base', '', tp_b, sl_b)
        if key in done_set:
            print(f"[{i_cell}/{total_cells}] SKIP (already done) job={args.job} window={label} flat_base", flush=True)
        else:
            cells_run_now += 1
            phaseC_patch.activate_source(mc, 'flat_base', None, maps, original_is_stressed, original_threshold)
            mc_patch.set_tpsl(mc, tp_b, sl_b)
            t0 = time.perf_counter()
            with redirect_stdout(log_f):
                ctx = mc._prepare_window(label, d_start, d_end, version_meta['id'])
            t_prepare = time.perf_counter() - t0
            t1 = time.perf_counter()
            with redirect_stdout(log_f):
                sim = mc._simulate_window(ctx)
            t_sim = time.perf_counter() - t1
            result = sim['seeded']
            row = _row_common(mc, 'C', args.profile, label, tp_b, sl_b, 'flat_base', None, tp_b, sl_b,
                               n_iter, ctx, mc_patch, phaseC_patch, t_prepare, t_sim, result)
            csv_w.writerow(row); csv_f.flush()
            finals = result.get('finals')
            if finals and pl is not None:
                for i_iter, f in enumerate(finals):
                    path_rows.append({'profile': args.profile, 'window': label, 'source': 'flat_base',
                                       'thr': '', 'tp_stress': tp_b, 'sl_stress': sl_b, 'iter': i_iter,
                                       'ret': (f / mc.STARTING_CASH - 1.0) * 100.0})
            done_set.add(key)
            state['done_cells'] = [list(k) for k in sorted(done_set)]
            state.update(job=args.job, profile=args.profile, windows=window_labels, sources=sources,
                          base_tp=tp_b, base_sl=sl_b, smoke=bool(args.smoke), n_iter=n_iter,
                          algorithm_version=version_meta)
            mc_patch.atomic_write_json(state_path, state)
            print(f"[{i_cell}/{total_cells}] job={args.job} window={label} flat_base  "
                  f"worst_dd={result.get('worst_dd'):.1f}% med_ret={result.get('med_ret'):+.1f}% "
                  f"p_coll={result.get('p_coll'):.1f}%", flush=True)

        for source, thr, ts, ss in grid:
            i_cell += 1
            thr_str = str(thr) if thr is not None else ''
            key = (label, source, thr_str, ts, ss)
            if key in done_set:
                print(f"[{i_cell}/{total_cells}] SKIP (already done) job={args.job} window={label} "
                      f"source={source} thr={thr_str} ts={ts} ss={ss}", flush=True)
                continue
            cells_run_now += 1
            phaseC_patch.activate_source(mc, source, thr, maps, original_is_stressed, original_threshold)
            mc_patch.set_tpsl(mc, tp_b, sl_b, tp_stress=ts, sl_stress=ss)

            t0 = time.perf_counter()
            with redirect_stdout(log_f):
                ctx = mc._prepare_window(label, d_start, d_end, version_meta['id'])
            t_prepare = time.perf_counter() - t0
            t1 = time.perf_counter()
            with redirect_stdout(log_f):
                sim = mc._simulate_window(ctx)
            t_sim = time.perf_counter() - t1
            result = sim['seeded']

            row = _row_common(mc, 'C', args.profile, label, tp_b, sl_b, source, thr, ts, ss,
                               n_iter, ctx, mc_patch, phaseC_patch, t_prepare, t_sim, result)
            csv_w.writerow(row); csv_f.flush()

            finals = result.get('finals')
            if finals and pl is not None:
                for i_iter, f in enumerate(finals):
                    path_rows.append({'profile': args.profile, 'window': label, 'source': source,
                                       'thr': thr_str, 'tp_stress': ts, 'sl_stress': ss, 'iter': i_iter,
                                       'ret': (f / mc.STARTING_CASH - 1.0) * 100.0})
            if pl is not None:
                try:
                    pl.DataFrame(path_rows).write_parquet(parquet_path)
                except Exception as e:
                    print(f"[warn] parquet write failed ({e}); continuing (CSV is authoritative)", flush=True)

            done_set.add(key)
            state['done_cells'] = [list(k) for k in sorted(done_set)]
            state.update(job=args.job, profile=args.profile, windows=window_labels, sources=sources,
                          base_tp=tp_b, base_sl=sl_b, smoke=bool(args.smoke), n_iter=n_iter,
                          algorithm_version=version_meta)
            mc_patch.atomic_write_json(state_path, state)

            print(f"[{i_cell}/{total_cells}] job={args.job} window={label} source={source} thr={thr_str} "
                  f"tp_stress={ts:+.2f} sl_stress={ss:+.2f} stressed_frac={row['stressed_call_frac']}% "
                  f"dte_frac={row['dte_routed_frac']}% | worst_dd={result.get('worst_dd'):.1f}% "
                  f"med_ret={result.get('med_ret'):+.1f}% p_coll={result.get('p_coll'):.1f}%", flush=True)

    csv_f.close()
    elapsed_job = time.time() - t_job0
    _tee(f"\n[DONE] job={args.job} cells_run_this_invocation={cells_run_now} "
         f"total_done={len(done_set)}/{total_cells} wall={elapsed_job:.1f}s -> {csv_path}", log_f)
    log_f.close()


def main():
    args = parse_args()
    import mc_patch      # local module, driver/mc_patch.py -- REUSED, never edited
    import phaseC_patch  # local module, driver/phaseC_patch.py (this Phase's new file)

    for d in (OUT_DIR, LOG_DIR, STATE_DIR):
        os.makedirs(d, exist_ok=True)

    # 1) env BEFORE import -- frozen pins, profile overrides, version pin
    # (reuses the SAME driver/state/meta.json Phase A resolved and Phase B is
    # currently reading -- read-only reuse, never re-resolved here).
    mc_patch.apply_frozen_pins(max_workers=6)
    mc_patch.apply_profile_env(args.profile)
    version_meta = mc_patch.resolve_and_pin_version(META_PATH)

    # 2) NOW import monte_carlo.
    import monte_carlo as mc

    # 3) post-import patches -- SAME two as Phase A/B (loader cache, put skip).
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    window_lookup = {label: (label, d0, d1) for label, d0, d1 in mc.WINDOWS}

    if args.validate_only:
        ok = run_validate(mc, mc_patch, phaseC_patch, args, version_meta, window_lookup)
        raise SystemExit(0 if ok else 1)
    else:
        run_main_grid(mc, mc_patch, phaseC_patch, args, version_meta, window_lookup)


if __name__ == '__main__':
    main()
