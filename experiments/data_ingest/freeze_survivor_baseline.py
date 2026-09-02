"""
P2.A STEP 0 (BLOCKING) -- freeze the survivor-only deep-window baseline BEFORE any
Sharadar ingest touches the DB.

Every deep-window DD/collapse number computed to date was produced on a universe that
contains only companies alive in 2026. Once delisted names land in price_history those
numbers become unreproducible. This snapshots them, tagged "survivor-only, pre-Sharadar",
so step 4's paired survivorship-discount report has a real before-side.

Read-only apart from writing the snapshot. Output:
  experiments/data_ingest/SURVIVOR_BASELINE_PRE_SHARADAR.json

  python freeze_survivor_baseline.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)

OUT_JSON = os.path.join(REPO, "experiments", "data_ingest",
                        "SURVIVOR_BASELINE_PRE_SHARADAR.json")
COPY_DIR = os.path.join(REPO, "experiments", "data_ingest",
                        "survivor_baseline_pre_sharadar")

# Artifact sets that carry deep-window (pre-2010) DD/collapse numbers.
SOURCES = [
    ("deep_crash_screen", os.path.join(REPO, "experiments", "deep_crash_screen", "results")),
    ("newbox_ecert", os.path.join(REPO, "experiments", "newbox_rebaseline", "results_ecert")),
    ("v74_research_pack", os.path.join(REPO, ".cache", "algorithm_versions", "v74",
                                       "research_pack")),
]
PACK_FILES = ("stress_windows.json", "stress_windows_apex.json",
              "stress_windows_core.json", "stress_windows_sentinel.json")
DEEP_WINDOWS = ("ltcm_1998", "dotcom_crash_2000_2002", "gfc_crash_2007_2009", "2007_now")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _git_head():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:
        return None


def _walk(root):
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if fn.endswith(".json") or fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def main():
    os.makedirs(COPY_DIR, exist_ok=True)
    snap = {
        "tag": "survivor-only, pre-Sharadar",
        "frozen_at": datetime.now().isoformat(timespec="seconds"),
        "git_head": _git_head(),
        "why": ("Deep-window DD/collapse computed on a 2026-survivor universe only. "
                "Sharadar delisted-equity ingest will make these unreproducible. "
                "Doctrine unchanged: deep windows are SCREENS, not GATES."),
        "deep_windows_of_record": list(DEEP_WINDOWS),
        "sources": {},
        "extracted_deep_numbers": {},
    }

    for label, root in SOURCES:
        if not os.path.isdir(root):
            snap["sources"][label] = {"present": False, "path": root}
            continue
        files = []
        dest_root = os.path.join(COPY_DIR, label)
        for p in _walk(root):
            rel = os.path.relpath(p, root)
            if label == "v74_research_pack" and os.path.basename(p) not in PACK_FILES:
                continue
            dest = os.path.join(dest_root, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(p, dest)
            files.append({"rel": rel.replace("\\", "/"), "sha256": _sha256(p),
                          "bytes": os.path.getsize(p)})
        snap["sources"][label] = {"present": True, "path": root, "files": files}

    # Pull the actual deep-window numbers into the snapshot body so the before-side is
    # readable without re-opening every artifact.
    for label, root in SOURCES:
        if not os.path.isdir(root):
            continue
        for p in _walk(root):
            if not p.endswith(".json"):
                continue
            try:
                with open(p) as f:
                    js = json.load(f)
            except Exception:
                continue
            hits = {}

            def _scan(obj, path=""):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k in DEEP_WINDOWS:
                            hits[f"{path}/{k}".lstrip("/")] = v
                        else:
                            _scan(v, f"{path}/{k}")
                elif isinstance(obj, list):
                    for n, v in enumerate(obj):
                        _scan(v, f"{path}[{n}]")

            _scan(js)
            if hits:
                key = f"{label}:{os.path.relpath(p, root)}".replace("\\", "/")
                snap["extracted_deep_numbers"][key] = hits

    with open(OUT_JSON, "w") as f:
        json.dump(snap, f, indent=2)

    n_files = sum(len(v.get("files", [])) for v in snap["sources"].values())
    print(f"frozen {n_files} artifacts -> {COPY_DIR}")
    print(f"snapshot -> {OUT_JSON}")
    print(f"deep-window number sets extracted: {len(snap['extracted_deep_numbers'])}")
    for k in sorted(snap["extracted_deep_numbers"]):
        print(f"  {k}: {sorted(snap['extracted_deep_numbers'][k])}")


if __name__ == "__main__":
    main()
