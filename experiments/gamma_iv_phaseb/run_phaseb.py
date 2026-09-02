"""
Gamma x IV Phase B — per-arm driver (see DESIGN.md in this directory).

Mirrors experiments/gamma_validation/run_gamma_ab.py's shell-to-sweep.py pattern,
one arm per invocation (queue-friendly: each arm is its own queued task with its
own dedup key; sweep partials make every arm resume-safe).

Arms:
  valgoff : validation arm — both flags unset, ORIGINAL goff settings
            (N=150, full 2016-06-01 grid, cells flat_n4_a25) — must reproduce
            experiments/concentration_2x/results/sweep_drill_goff.json exactly.
  base    : flags unset,        clipped grid (2022-08-01+), N=300, 2 cells
  gamma   : GAMMA_AWARE=1,      same
  iv      : IV_PREMIUM=1,       same
  gammaiv : GAMMA_AWARE=1 + IV_PREMIUM=1, same

Usage: python experiments/gamma_iv_phaseb/run_phaseb.py --arm <arm>
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
HERE = os.path.join(REPO, "experiments", "gamma_iv_phaseb")
RESULTS = os.path.join(HERE, "results")
PY = sys.executable

CELLS_AB = "flat_n4_a25,cascade_ref"
HIST_START_AB = "2022-08-01"
N_ITER_AB = "300"

ARMS = {
    # name    (gamma, iv,   cells,          n_iter, hist_start)
    "valgoff": (False, False, "flat_n4_a25", "150",  None),
    "base":    (False, False, CELLS_AB,      N_ITER_AB, HIST_START_AB),
    "gamma":   (True,  False, CELLS_AB,      N_ITER_AB, HIST_START_AB),
    "iv":      (False, True,  CELLS_AB,      N_ITER_AB, HIST_START_AB),
    "gammaiv": (True,  True,  CELLS_AB,      N_ITER_AB, HIST_START_AB),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=sorted(ARMS))
    ap.add_argument("--workers", default="6")
    args = ap.parse_args()

    gamma_on, iv_on, cells, n_iter, hist_start = ARMS[args.arm]
    tag = f"phaseb_{args.arm}"

    env = dict(os.environ)
    # Hard-clean the two A/B flags + coverage sink so arms never leak into each other.
    for k in ("GAMMA_AWARE", "IV_PREMIUM", "IV_COVERAGE_DIR"):
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

    argv = [PY, "-u", SWEEP, "--stage", "drill", "--cells", cells,
            "--n-iter", n_iter, "--step-months", "3", "--tag", tag,
            "--workers", args.workers, "--starting-cash", "50000"]
    if hist_start:
        argv += ["--hist-start", hist_start]

    print("=" * 72)
    print(f"[{tag}] GAMMA_AWARE={'1' if gamma_on else '(off)'} "
          f"IV_PREMIUM={'1' if iv_on else '(off)'}")
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
