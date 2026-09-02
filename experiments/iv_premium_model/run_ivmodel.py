"""IV Premium Model -- 4-arm A/B driver (see DESIGN.md section 7, Amendments A1-A4).

Mirrors experiments/gamma_iv_phaseb/run_phaseb.py's shell-to-sweep.py pattern,
one arm per invocation (queue-friendly: each arm is its own queued task with
its own dedup key; sweep partials make every arm resume-safe). Tags are
prefixed ivm_ so partial-checkpoint dirs / result files never collide with
gamma_iv_phaseb's own phaseb_ tagged outputs.

Arms (DESIGN.md section 7 table; supersedes gamma_iv_phaseb's coverage-blocked
raw-panel iv/gammaiv arms -- the panel's job now is calibration input only):
  base           : both flags unset
  gamma          : GAMMA_AWARE=1
  ivmodel        : IV_PREMIUM=1 IV_MODEL=1  (skips the raw panel; F2 RV+VIX model)
  gamma_ivmodel  : GAMMA_AWARE=1 IV_PREMIUM=1 IV_MODEL=1

FULL 38-window quarterly grid, 2016-06-01..2026-04-15 (no --hist-start override
-- unlike gamma_iv_phaseb's panel-clipped 13-window grid, the model prices all
history, so the 2020-crash-era windows are explicitly IN this run).

Usage: python experiments/iv_premium_model/run_ivmodel.py --arm <arm>
ASCII-only output.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

REPO = r"C:\Development\Trader"
SWEEP = os.path.join(REPO, "experiments", "concentration_2x", "sweep.py")
SWEEP_RESULTS = os.path.join(REPO, "experiments", "concentration_2x", "results")
HERE = os.path.join(REPO, "experiments", "iv_premium_model")
RESULTS = os.path.join(HERE, "results")
PY = sys.executable

CELLS_AB = "flat_n4_a25,cascade_ref"
N_ITER_AB = "300"

ARMS = {
    # name            (gamma, iv_premium, iv_model)
    "base":            (False, False, False),
    "gamma":           (True,  False, False),
    "ivmodel":         (False, True,  True),
    "gamma_ivmodel":   (True,  True,  True),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=sorted(ARMS))
    ap.add_argument("--workers", default="6")
    args = ap.parse_args()

    gamma_on, iv_on, model_on = ARMS[args.arm]
    tag = f"ivm_{args.arm}"

    env = dict(os.environ)
    # Hard-clean all three A/B flags + coverage sink so arms never leak into each other.
    for k in ("GAMMA_AWARE", "IV_PREMIUM", "IV_MODEL", "IV_COVERAGE_DIR"):
        env.pop(k, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if gamma_on:
        env["GAMMA_AWARE"] = "1"
    if iv_on:
        env["IV_PREMIUM"] = "1"
        cov_dir = os.path.join(RESULTS, f"coverage_{args.arm}")
        os.makedirs(cov_dir, exist_ok=True)
        env["IV_COVERAGE_DIR"] = cov_dir
    if model_on:
        env["IV_MODEL"] = "1"

    # NO --hist-start: sweep.py's monthly_windows() default (HIST_START=2016-06-01,
    # HIST_END=2026-04-15, step-months=3) reproduces the full original 38-window grid.
    argv = [PY, "-u", SWEEP, "--stage", "drill", "--cells", CELLS_AB,
            "--n-iter", N_ITER_AB, "--step-months", "3", "--tag", tag,
            "--workers", args.workers, "--starting-cash", "50000"]

    print("=" * 72)
    print(f"[{tag}] GAMMA_AWARE={'1' if gamma_on else '(off)'} "
          f"IV_PREMIUM={'1' if iv_on else '(off)'} "
          f"IV_MODEL={'1' if model_on else '(off)'}")
    print(f"[{tag}] {' '.join(argv)}")
    print("=" * 72, flush=True)
    t0 = time.time()
    r = subprocess.run(argv, cwd=REPO, env=env)
    mins = (time.time() - t0) / 60.0
    print(f"[{tag}] exit={r.returncode} in {mins:.1f} min", flush=True)
    if r.returncode != 0:
        raise SystemExit(f"[{tag}] sweep failed rc={r.returncode}")

    src = os.path.join(SWEEP_RESULTS, f"sweep_drill_{tag}.json")
    if os.path.exists(src):
        os.makedirs(RESULTS, exist_ok=True)
        dst = os.path.join(RESULTS, f"sweep_drill_{tag}.json")
        shutil.copyfile(src, dst)
        print(f"[{tag}] copied result -> {dst}", flush=True)
    else:
        raise SystemExit(f"[{tag}] expected result missing: {src}")
    print(f"[{tag}] DONE", flush=True)


if __name__ == "__main__":
    main()
