"""S3 bankroll-ladder screen driver -- runs every barrier group in the manifest.

Python rather than shell deliberately: on this box the task queue resolves a bare
`bash` to WSL (which has no Git Bash), so a .sh driver dies instantly with
execvpe(/bin/bash) failed. A Python driver has no shell-resolution dependency.

dte/tp/sl are import-time axes in ladder_mc, so each barrier group must be its
own process; n_slots/slot_frac/instrument are free within a group. Resumable:
a group whose out_*.json already exists is skipped.

ASCII output only.
"""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GRID = ROOT / "experiments/bankroll_ladder/results/s3_grid"
LADDER = ROOT / "experiments/bankroll_ladder/ladder_mc.py"

N_ITER = int(os.environ.get("N_ITER", "300"))
WORKERS = int(os.environ.get("WORKERS", "4"))
MAX_CONCURRENT_GROUPS = int(os.environ.get("MAX_GROUPS", "4"))


def run_group(entry):
    grp = entry["group"]
    cfg = ROOT / entry["config"]
    out = ROOT / entry["out"]
    log = GRID / f"log_{grp}.txt"
    if out.exists():
        print(f"  [skip] {grp} (out exists)", flush=True)
        return grp, 0, 0.0
    env = dict(os.environ)
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONHASHSEED="0")
    cmd = [sys.executable, "-u", str(LADDER),
           "--mode", "screen", "--config", str(cfg), "--out", str(out),
           "--n-iter", str(N_ITER), "--workers", str(WORKERS)]
    t0 = time.time()
    print(f"  [launch] {grp}", flush=True)
    with open(log, "w", encoding="utf-8") as fh:
        rc = subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT,
                             cwd=str(ROOT), env=env)
    dt = time.time() - t0
    print(f"  [done ] {grp} rc={rc} {dt / 60:.1f}min", flush=True)
    return grp, rc, dt


def main():
    man = json.loads((GRID / "MANIFEST.json").read_text(encoding="utf-8"))
    print(f"S3 screen: N={N_ITER} workers/group={WORKERS} "
          f"concurrent groups={MAX_CONCURRENT_GROUPS} groups={len(man)}")
    print(time.strftime("%Y-%m-%d %H:%M:%S"), flush=True)

    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_GROUPS) as ex:
        for r in ex.map(run_group, man):
            results.append(r)

    fails = [g for g, rc, _ in results if rc != 0]
    print(f"\ngroups finished in {(time.time() - t0) / 60:.1f}min; "
          f"failures={len(fails)} {fails if fails else ''}", flush=True)

    merged = GRID / "S3_MERGED.json"
    rc = subprocess.call(
        [sys.executable, "-u", str(LADDER), "--merge", str(GRID), "--out", str(merged)],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    if rc != 0:
        print("[warn] merge failed; out_*.json are intact, merge by hand")
    else:
        print(f"merged -> {merged}")
    print("DONE", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
