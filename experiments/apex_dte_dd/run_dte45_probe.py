"""
P3.3 -- 45-DTE sprint probe arm. Gameplan row P3.3: "env-swap NOMINAL_CAL_DTE=45,
n4/n10 + one Core cell, N=100 screen -> promote only if it beats 30-DTE on >=2 of
{compound, DD, collapse}. Kill = close the DTE axis permanently."

VEHICLE: reuses experiments/concentration_2x/sweep.py's drill stage exactly as
the dte30/dte15/dte15s70 arms did (see experiments/apex_dte_dd/FINDINGS.md
"Vehicle" note + analyze.py's ARM_LABEL dict, which already expects a
'sweep_drill_<tag>.json' per arm) -- this is a THIN wrapper subprocess call,
mirroring experiments/gamma_iv_phaseb/run_phaseb.py's shell-to-sweep.py
pattern (one script, env dict, subprocess.run of sweep.py, copy the result
next to this experiment's own results/ for analyze.py to pick up).

DTE/HOLD/SL RECIPE (env-swap onto the SAME 30-DTE-style SL used by the
existing 'dte30' arm, to isolate the DTE axis exactly the way dte15s70
isolated it against dte30's SL -- see FINDINGS.md Result 1's "pure-DTE
control" note):
  NOMINAL_CAL_DTE=45, HOLD_CAL_DAYS=40  -- HOLD_CAL_DAYS is NOT invented: it
    is the empirical per-trade option-EV optimum for D=45 from
    experiments/option_curve/results/curve.json (D45_W40: ev=+0.0553,
    wr=72.45%, tp_rate=73.78%, the best of the tested W grid for D=45 --
    verified by inspection this session, not re-derived by formula).
  SL_BASE_OV=-0.70, SL_STRESS_OV=-0.70  -- same SL the 'dte30' arm used
    (STRATEGY_30DTE's own default), so a 45-vs-30 comparison isolates DTE,
    not the stop, exactly like the existing dte15 vs dte15s70 control did.

CELLS: flat_n4_a25, flat_n10_a10 (the sprint n4/n10 cells) + cascade_ref (the
"one Core cell" -- sweep.py's own cascade_ref cell IS the Core recipe:
ultra/top/mid/low 20/15/8/3, MaxPos 14).

N=100 (screen tier per the task spec) via sweep.py's own --n-iter override on
the 'drill' stage (drill's own default is N=500 -- --n-iter downgrades it to
the requested screen N without touching the cell/window grid).

Tag 'dte45' -- follows the existing dte30/dte15/dte15s70 tag convention
(experiments/apex_dte_dd/analyze.py's ARM_LABEL dict); add
  ARM_LABEL['dte45'] = '45DTE  SL-.70 (P3.3 probe)'
there by hand before running analyze.py --arms dte30,dte45 (analyze.py itself
is not touched by this driver -- read-only reuse).

Resume-safe: sweep.py's own drill stage already checkpoints per monthly-roll
window under results/drill_dte45_partial/ and skips windows already on disk
(concentration_2x/sweep.py docstring "RESILIENCE" section) -- this wrapper
adds nothing on top except skip-if-final-output-exists.

Usage (no required args -- queue-submittable bare):
  python -u experiments/apex_dte_dd/run_dte45_probe.py [--n-iter 100] [--workers 6]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SWEEP = os.path.join(ROOT, 'experiments', 'concentration_2x', 'sweep.py')
SWEEP_RESULTS = os.path.join(ROOT, 'experiments', 'concentration_2x', 'results')
RESULTS = os.path.join(HERE, 'results')
PY = sys.executable

TAG = 'dte45'
CELLS = 'flat_n4_a25,flat_n10_a10,cascade_ref'

RECIPE_ENV = {
    'NOMINAL_CAL_DTE': '45',
    'HOLD_CAL_DAYS': '40',     # experiments/option_curve/results/curve.json D45 EV optimum
    'SL_BASE_OV': '-0.70',
    'SL_STRESS_OV': '-0.70',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-iter', type=int, default=100)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--starting-cash', type=float, default=50_000.0)
    args = ap.parse_args()

    dst = os.path.join(RESULTS, f'sweep_drill_{TAG}.json')
    os.makedirs(RESULTS, exist_ok=True)
    if os.path.exists(dst):
        print(f"[dte45_probe] {dst} already exists -- resume/skip (delete it to force a "
              f"fresh run; sweep.py's own per-window partials under "
              f"concentration_2x/results/drill_{TAG}_partial/ remain valid either way).",
              flush=True)
        return

    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    env.update(RECIPE_ENV)

    argv = [PY, '-u', SWEEP, '--stage', 'drill', '--cells', CELLS,
            '--n-iter', str(args.n_iter), '--step-months', '1', '--tag', TAG,
            '--workers', str(args.workers), '--starting-cash', str(args.starting_cash)]

    print('=' * 72, flush=True)
    print(f"[{TAG}] recipe={RECIPE_ENV}", flush=True)
    print(f"[{TAG}] {' '.join(argv)}", flush=True)
    print('=' * 72, flush=True)
    t0 = time.time()
    r = subprocess.run(argv, cwd=ROOT, env=env)
    mins = (time.time() - t0) / 60.0
    print(f"[{TAG}] exit={r.returncode} in {mins:.1f} min", flush=True)
    if r.returncode != 0:
        raise SystemExit(f"[{TAG}] sweep.py failed rc={r.returncode}")

    src = os.path.join(SWEEP_RESULTS, f'sweep_drill_{TAG}.json')
    if os.path.exists(src):
        shutil.copyfile(src, dst)
        print(f"[{TAG}] copied result -> {dst}", flush=True)
    else:
        raise SystemExit(f"[{TAG}] expected result missing: {src}")
    print(f"[{TAG}] DONE -- compare via: python experiments/apex_dte_dd/analyze.py "
          f"--arms dte30,{TAG} (add ARM_LABEL['{TAG}'] to analyze.py by hand first)",
          flush=True)


if __name__ == '__main__':
    main()
