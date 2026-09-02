"""
P1.7 -- N-floor table recalibration vs v74. Gameplan row P1.7: "supply moved
+106%/+77%/-38% across v71/v73/v74; the stale v46 floors produce false
REVIEW flags that erode gate trust."

WHAT THIS WRAPS: experiments/n_floor_v46/run.py then reaggregate.py, UNEDITED
(the engine-edit window covers monte_carlo.py/backtest_cascade.py/
option_pricing.py/strategy_config.py; these two experiment scripts are not on
that list, but per this batch's own charter ("write the experiment DRIVERS --
new files under experiments/ only") this is a NEW wrapper, not an edit to
those two files).

STALE-DEFAULTS TRAP -- ACTUAL FINDING (read before running): neither run.py
nor reaggregate.py takes ANY CLI args at all (confirmed by source read -- no
argparse in either file); both resolve the version via
`AlgorithmVersion.get_active_scores_version()` at call time, so they already
target whatever is CURRENTLY active (v74 today) without needing an explicit
version flag. The REAL stale-defaults trap here is different and was found
LIVE this session: their fixed output filenames (summary.json,
summary_v2.json, per_day.csv) carry NO version tag, and
experiments/n_floor_v46/summary.json currently shows
`"algorithm_version_id": 72` while experiments/n_floor_v46/FINDINGS.md's own
header still says "v46 (commit f274eb6)" -- i.e. a PRIOR ad-hoc rerun already
silently overwrote the v46 baseline with v72 numbers and nobody re-labeled
anything. (Separately: run.py's `commit = av.git_commit_hash` reads a field
that doesn't exist on AlgorithmVersion -- the real field is `git_commit` --
so that bare except always falls back to 'unknown', which is why
summary.json's meta shows `"algorithm_commit": "unknown"`. Not fixed here,
since it lives in a file this batch does not edit -- flagged for a separate
pass.)

THIS WRAPPER THEREFORE, before touching anything:
  1. Resolves + pins the active version (warns, does not block, if it is not
     v74 -- see experiments/_mc_pinned_runner.resolve_pinned_version).
  2. Archives whatever summary.json / summary_v2.json / per_day.csv already
     exist to *_preserved_v<old_id>.<ext> (skips archiving if that exact
     archive name already exists -- resume-safe / idempotent on requeue).
  3. Runs run.py then reaggregate.py fresh (subprocess, unedited).
  4. Copies the freshly-written summary.json / summary_v2.json / per_day.csv
     to *_v74.<ext> siblings, so THIS baseline also survives the next
     version's rerun instead of being silently overwritten again with no
     trace (the same bug this step just found and preserved evidence of).

Usage (no required args -- queue-submittable bare):
  python -u experiments/n_floor_v46/run_v74_recal.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments._mc_pinned_runner import resolve_pinned_version  # noqa: E402

RUN_PY = os.path.join(HERE, 'run.py')
REAGG_PY = os.path.join(HERE, 'reaggregate.py')

FILES = ['summary.json', 'summary_v2.json', 'per_day.csv']


def _existing_version_tag(path: str) -> str:
    """Best-effort tag for the archive filename: read algorithm_version_id out
    of an existing summary.json if present; 'unknown' otherwise."""
    summary_path = os.path.join(HERE, 'summary.json')
    if not os.path.exists(summary_path):
        return 'unknown'
    try:
        with open(summary_path, 'r', encoding='utf-8') as f:
            meta = json.load(f).get('meta', {})
        vid = meta.get('algorithm_version_id')
        return f'v{vid}' if vid is not None else 'unknown'
    except Exception:
        return 'unknown'


def _archive_existing():
    tag = _existing_version_tag(HERE)
    archived_any = False
    for fn in FILES:
        src = os.path.join(HERE, fn)
        if not os.path.exists(src):
            continue
        stem, ext = os.path.splitext(fn)
        dst = os.path.join(HERE, f'{stem}_preserved_{tag}{ext}')
        if os.path.exists(dst):
            print(f"  [SKIP-ARCHIVE] {dst} already exists (resume) -- leaving in place",
                  flush=True)
            continue
        shutil.copyfile(src, dst)
        print(f"  [ARCHIVED] {src} -> {dst}", flush=True)
        archived_any = True
    if not archived_any:
        print("  (nothing to archive -- no pre-existing summary.json/summary_v2.json/"
              "per_day.csv, or already archived)", flush=True)


def _run_subprocess(script_path: str, env: dict, timeout_s: int = 1800):
    t0 = time.time()
    print(f"[n_floor_v74_recal] running {os.path.basename(script_path)} ...", flush=True)
    proc = subprocess.run([sys.executable, '-u', script_path], cwd=ROOT, env=env,
                          timeout=timeout_s)
    dur = time.time() - t0
    if proc.returncode != 0:
        raise SystemExit(f"{script_path} failed rc={proc.returncode} after {dur:.0f}s")
    print(f"[n_floor_v74_recal] {os.path.basename(script_path)} done ({dur:.0f}s)", flush=True)


def main():
    import argparse
    argparse.ArgumentParser(description=__doc__).parse_args()  # accepts only --help; no flags needed

    pin_commit, pin_id = resolve_pinned_version()
    print(f"[n_floor_v74_recal] active version {pin_commit} (id={pin_id}) -- run.py/"
          f"reaggregate.py resolve this themselves via get_active_scores_version(), no "
          f"pin needed for those two (they don't read ALGORITHM_VERSION_PIN); recorded "
          f"here for the run log.", flush=True)

    fresh_marker = os.path.join(HERE, 'summary_v74.json')
    if os.path.exists(fresh_marker):
        print(f"[n_floor_v74_recal] {fresh_marker} already exists -- resume/skip. Delete "
              f"it (and optionally summary_v2_v74.json / per_day_v74.csv) to force a "
              f"fresh recalibration.", flush=True)
        return

    print("[n_floor_v74_recal] archiving any pre-existing outputs before overwrite ...",
          flush=True)
    _archive_existing()

    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'

    _run_subprocess(RUN_PY, env, timeout_s=1800)
    _run_subprocess(REAGG_PY, env, timeout_s=1800)

    # Verify the fresh run actually targeted the version we think it did, then
    # tag copies so THIS baseline is never silently overwritten trace-free.
    with open(os.path.join(HERE, 'summary.json'), 'r', encoding='utf-8') as f:
        fresh_meta = json.load(f).get('meta', {})
    fresh_vid = fresh_meta.get('algorithm_version_id')
    if fresh_vid != pin_id:
        print(f"[n_floor_v74_recal] WARNING: run.py's fresh summary.json shows "
              f"algorithm_version_id={fresh_vid}, but this driver resolved the active "
              f"version as id={pin_id} at start -- the active version likely changed "
              f"mid-run. Tagging outputs with the ACTUAL id={fresh_vid}, not the "
              f"originally-resolved one.", flush=True)
    tag = f'v{fresh_vid}' if fresh_vid is not None else 'unknown'
    for fn in FILES:
        src = os.path.join(HERE, fn)
        if not os.path.exists(src):
            continue
        stem, ext = os.path.splitext(fn)
        dst = os.path.join(HERE, f'{stem}_{tag}{ext}')
        shutil.copyfile(src, dst)
        print(f"[n_floor_v74_recal] tagged copy -> {dst}", flush=True)

    print("[n_floor_v74_recal] DONE. Canonical (untagged) summary.json/summary_v2.json/"
          "per_day.csv now reflect the fresh recalibration; version-tagged siblings and "
          "the pre-existing-state archive are preserved alongside for future diffing.",
          flush=True)


if __name__ == '__main__':
    main()
