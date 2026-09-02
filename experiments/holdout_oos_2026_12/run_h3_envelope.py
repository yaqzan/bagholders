#!/usr/bin/env python
"""H3 envelope generator -- FROZEN Apex-15DTE Monte Carlo recipe.

Part of the 2026-12-15 pre-registered OOS evaluation package
(experiments/holdout_oos_2026_12/PREREGISTRATION.md section 4 "H3"). Reproduces the LIVE Apex
15-DTE risk-budget-elbow config on monte_carlo.py (the 30-DTE engine, generalized to 15-DTE via
NOMINAL_CAL_DTE/HOLD_CAL_DAYS -- the proven experiments/concentration_2x pattern; NOT
monte_carlo_15dte.py, which has no calendar-hold support), runs it as a clean subprocess (matching
monte_carlo.py's own documented "bare invocation" + MC_RESULTS_JSON "external A/B driver"
interface -- see .claude/skills/run-monte-carlo/SKILL.md), and writes a percentile-band envelope
(envelope_h3.json) that the live Portfolio ledger's realized return/DD can be compared against.

FROZEN: every env var below except WIN_END is pre-registered and must not change without a dated
Amendment Log entry in PREREGISTRATION.md section 6. Re-running this script with only --win-end
extended toward the eval date (same recipe, more elapsed calendar time) is NOT an amendment --
it is how the December read completes the protocol.

Usage:
    python experiments/holdout_oos_2026_12/run_h3_envelope.py [--win-end 2026-07-10] [--dry-run]

Queued (the ONE MC job this pre-registration package submits):
    trader queue submit --priority normal --db heavy --cpu 6 --restartable \
      --dedup oos2026_envelope --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
      -- python experiments/holdout_oos_2026_12/run_h3_envelope.py

Windows note: this box's shell is PowerShell/Git-Bash; this script itself is pure Python and does
not care which invoked it (subprocess.run below uses sys.executable + an explicit env dict, no
shell-specific syntax).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------------------------
# FROZEN recipe -- reproduces algorithm_versions/portfolio_profiles.json key="apex" (version=3)
# on top of STRATEGY_30DTE (portfolio_profiles.py:324 "base = sc.STRATEGY_30DTE"). See
# PREREGISTRATION.md section 1a/4-H3 for the params table + rationale for every line and for
# what is DELIBERATELY left unset.
# ---------------------------------------------------------------------------------------------
FROZEN_ENV = {
    # DTE / hold -- honest-calendar 15-DTE via the 30-DTE engine (concentration_2x precedent)
    "NOMINAL_CAL_DTE": "15",
    "HOLD_CAL_DAYS": "13",
    "CALENDAR_HOLD": "1",
    # TP/SL -- Apex overrides SL only (TP inherits STRATEGY_30DTE default; do not set TP_BASE_OV)
    "SL_BASE_OV": "-0.85",
    "SL_STRESS_OV": "-0.85",
    # Sizing -- flat 25% x 4 names, calls only
    "TIER_ULTRA_OV": "0.25",
    "TIER_TOP_OV": "0.25",
    "TIER_MID_OV": "0.25",
    "TIER_LOW_OV": "0.25",
    "TIER_OVERFLOW_OV": "0.0",
    "PUT_TIER_TOP_OV": "0.0",
    "PUT_TIER_MID_OV": "0.0",
    "PUT_TIER_LOW_OV": "0.0",
    "MAX_POSITIONS_OVERRIDE": "4",
    "MAX_POSITIONS_CALL": "4",
    "MAX_POSITIONS_PUT": "0",
    # Exposure caps + saturation (PROFILE_STRATEGY_ATTRS block)
    "GROSS_PREMIUM_CAP": "0.9",
    "CALL_PREMIUM_CAP": "0.9",
    "PUT_PREMIUM_CAP": "0.0",
    "OPP_SAT_CALL_REF": "16.0",
    "OPP_SAT_PUT_REF": "4.0",
    "OPP_SAT_POWER": "0.5",
    "OPP_SAT_FLOOR": "0.55",
    "PRACTICAL_EXPOSURE_ENABLED": "1",
    "PRACTICAL_CAPITAL_CEILING": "0.0",
    # DD soft band
    "DD_SOFT_BAND_LO": "0.35",
    "DD_SOFT_BAND_HI": "0.55",
    "DD_SOFT_CALL_FLOOR": "0.4",
}
# Deliberately NOT set here (see PREREGISTRATION.md section 4 H3 "frozen_env_recipe.deliberately_unset"):
#   TP_BASE_OV / TP_STRESS_OV       -- Apex does not override TP; inherits STRATEGY_30DTE default.
#   RXDD/MWDD/TVDD/BDIV/SVR/SPREAD_TILT *_ENABLED
#                                    -- INHERITED True from STRATEGY_30DTE (Apex's base config).
#                                       Do NOT set these False to mimic STRATEGY_15DTE -- that is a
#                                       different, mostly-legacy dict the live Apex profile does not use.
#   STARTING_CASH_OV                -- left at the $50k module default; %-based DD/return metrics
#                                       this hypothesis reads are dollar-scale-invariant.

FROZEN_WIN_START = "2026-06-01"     # live ledger's PortfolioRun.start_date -- FROZEN, do not change
DEFAULT_WIN_END = "2026-07-10"      # live ledger's last_processed_date at authoring (2026-07-13);
                                     # the pre-registered non-amendment axis -- extend this at the
                                     # Dec-2026 re-run, nothing else.
DEFAULT_N_ITER = 500
DEFAULT_WIN_LABEL = "h3_live_envelope"


def build_env(win_start: str, win_end: str, win_label: str, n_iter: int,
              results_json_path: Path) -> dict:
    env = dict(os.environ)  # preserve PATH + any queue-injected TRADER_RECALC_MAX_WORKERS /
                             # MC_WORKERS / TRADER_QUEUE_CORES so the subprocess respects the
                             # queue's CPU grant instead of running unrestricted.
    env.update(FROZEN_ENV)
    env["WIN_START"] = win_start
    env["WIN_END"] = win_end
    env["WIN_LABEL"] = win_label
    env["N_ITER_OVERRIDE"] = str(n_iter)
    env["MC_RESULTS_JSON"] = str(results_json_path)
    env["MC_RETURN_PATHS"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def percentiles(values, pcts=(5, 10, 25, 50, 75, 90, 95)):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {f"p{p:02d}": None for p in pcts}
    n = len(vals)
    out = {}
    for p in pcts:
        idx = min(n - 1, max(0, round((p / 100.0) * (n - 1))))
        out[f"p{p:02d}"] = vals[idx]
    out["n"] = n
    out["mean"] = statistics.fmean(vals)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--win-start", default=FROZEN_WIN_START,
                     help="FROZEN -- changing this is an Amendment (PREREGISTRATION.md section 6)")
    ap.add_argument("--win-end", default=DEFAULT_WIN_END,
                     help="the ONE field this recipe is pre-registered to extend over calendar "
                          "time (e.g. --win-end 2026-12-12 at the Dec-2026 re-run)")
    ap.add_argument("--n-iter", type=int, default=DEFAULT_N_ITER)
    ap.add_argument("--win-label", default=DEFAULT_WIN_LABEL)
    ap.add_argument("--out", default=str(HERE / "envelope_h3.json"))
    ap.add_argument("--dry-run", action="store_true",
                     help="print the exact env recipe + command and exit without running anything")
    ap.add_argument("--skip-mc", action="store_true",
                     help="reprocess an already-written raw MC_RESULTS_JSON dump into the envelope "
                          "without re-running monte_carlo.py (e.g. to fix envelope post-processing "
                          "after the expensive simulation already ran)")
    args = ap.parse_args()

    if args.win_start != FROZEN_WIN_START:
        print(f"WARNING: --win-start={args.win_start} differs from the FROZEN {FROZEN_WIN_START}. "
              f"This is an Amendment (PREREGISTRATION.md section 6) -- confirm it is intended and "
              f"logged before trusting this result.", file=sys.stderr)

    raw_results_path = HERE / f"_mc_raw_{args.win_label}.json"
    env = build_env(args.win_start, args.win_end, args.win_label, args.n_iter, raw_results_path)
    cmd = [sys.executable, str(ROOT / "monte_carlo.py")]

    if args.dry_run:
        print("Command:", " ".join(cmd))
        print("cwd:", ROOT)
        print("Env overrides (recipe fields only -- PATH/etc inherited, omitted for brevity):")
        for k, v in sorted(FROZEN_ENV.items()):
            print(f"  {k}={v}")
        for k in ("WIN_START", "WIN_END", "WIN_LABEL", "N_ITER_OVERRIDE",
                  "MC_RESULTS_JSON", "MC_RETURN_PATHS"):
            print(f"  {k}={env[k]}")
        return 0

    if args.skip_mc:
        if not raw_results_path.exists():
            print(f"[run_h3_envelope] ERROR: --skip-mc given but {raw_results_path} does not "
                  f"exist -- nothing to reprocess.", file=sys.stderr)
            return 5
        print(f"[run_h3_envelope] --skip-mc: reprocessing existing {raw_results_path} "
              f"(no MC re-run)", flush=True)
    else:
        print(f"[run_h3_envelope] launching monte_carlo.py  window={args.win_start}..{args.win_end}  "
              f"n_iter={args.n_iter}  label={args.win_label}", flush=True)
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env)
        if proc.returncode != 0:
            print(f"[run_h3_envelope] monte_carlo.py exited {proc.returncode}", file=sys.stderr)
            return proc.returncode

        if not raw_results_path.exists():
            print(f"[run_h3_envelope] ERROR: expected {raw_results_path} was not written by "
                  f"monte_carlo.py. If this repo's MC_RESULTS_JSON code path has changed since this "
                  f"script was written (2026-07-13), re-verify against monte_carlo.py source before "
                  f"trusting the frozen recipe further.", file=sys.stderr)
            return 3

    with open(raw_results_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    window_result = raw.get(args.win_label)
    if window_result is None:
        print(f"[run_h3_envelope] ERROR: label {args.win_label!r} not found in "
              f"{raw_results_path} -- keys present: {list(raw.keys())}", file=sys.stderr)
        return 4

    paths = window_result.get("paths") or {}
    finals_usd = paths.get("finals") or []          # raw ending equity in DOLLARS, confirmed by
                                                      # inspection (2026-07-13 smoke run: values
                                                      # ~$41k-58k on a $50k start, NOT a %) --
                                                      # despite the source-code variable being
                                                      # named 'finals', it is not pre-converted.
    dds_frac = paths.get("dds") or []                 # raw drawdown as a 0-1 FRACTION, confirmed by
                                                      # inspection (values ~0.44-0.46) -- distinct
                                                      # from aggregate['worst_dd']/['mean_dd'], which
                                                      # ARE already *100 percentages. Two different
                                                      # unit conventions in the same engine dump --
                                                      # do not compare them without converting first.
    starting_cash = paths.get("starting_cash")
    if not starting_cash:
        # FROZEN_ENV deliberately never sets STARTING_CASH_OV -- the module default applies.
        starting_cash = 50_000.0
        print(f"[run_h3_envelope] NOTE: 'starting_cash' absent from the raw dump; falling back to "
              f"the known module default $50,000 (this recipe never overrides STARTING_CASH_OV).",
              file=sys.stderr)

    if not finals_usd or not dds_frac:
        print(f"[run_h3_envelope] WARNING: per-iteration 'finals'/'dds' arrays are empty -- "
              f"MC_RETURN_PATHS may not have been honored. aggregate-only envelope written.",
              file=sys.stderr)

    finals_pct = [(v / starting_cash - 1.0) * 100.0 for v in finals_usd]
    dds_pct = [v * 100.0 for v in dds_frac]

    envelope = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "recipe": "experiments/holdout_oos_2026_12/run_h3_envelope.py (FROZEN except --win-end; "
                  "see PREREGISTRATION.md section 4 'H3')",
        "window": {"start": args.win_start, "end": args.win_end, "label": args.win_label},
        "n_iter_requested": args.n_iter,
        "n_iter_realized": len(finals_usd),
        "starting_cash_usd": starting_cash,
        "aggregate": {k: window_result.get(k) for k in
                      ("mean_ret", "med_ret", "worst_dd", "mean_dd", "p_coll")},
        "aggregate_units": "mean_ret/med_ret = % return; worst_dd/mean_dd = % drawdown (0-100 scale); p_coll = % of iterations collapsed",
        "percentile_bands": {
            "finals_pct_return": percentiles(finals_pct),
            "dds_pct_drawdown": percentiles(dds_pct),
        },
        "percentile_bands_units": "finals_pct_return: % return on starting_cash_usd (0 = breakeven). dds_pct_drawdown: % worst peak-to-trough drawdown (0-100 scale, matches aggregate.worst_dd/mean_dd convention). Both converted from the engine's raw per-iteration units (dollar equity / 0-1 fraction respectively) -- see inline comments above 'finals_usd'/'dds_frac' for the raw values and how they were confirmed.",
        "raw_meta": raw.get("_meta"),
        "note": "Sanity cross-check: mean(finals_pct_return) should be close to aggregate.mean_ret, "
                "and mean(dds_pct_drawdown) close to aggregate.mean_dd, for the same run (both "
                "derived from the same per-iteration data, just aggregated differently -- mean_ret "
                "in monte_carlo.py's own aggregate may use a slightly different mean convention e.g. "
                "geometric vs arithmetic, so expect 'close', not bit-exact). Dispersion in these "
                "bands reflects ONLY gap-fill randomness on ONE realized historical price tape for "
                "this window, not resampled market histories -- see PREREGISTRATION.md H3 'noise "
                "caveat' before reading these bands as if they were a full unconditional forecast "
                "distribution.",
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(envelope, f, indent=2)

    print(f"[run_h3_envelope] wrote {args.out}")
    print(f"  window {args.win_start}..{args.win_end}  n_iter_realized={len(finals_usd)}")
    print(f"  aggregate: {envelope['aggregate']}")
    print(f"  finals (%return) percentile bands: {envelope['percentile_bands']['finals_pct_return']}")
    print(f"  dds (%drawdown) percentile bands:  {envelope['percentile_bands']['dds_pct_drawdown']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
