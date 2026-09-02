"""Run the daily opportunity allocation research pipeline."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXP_DIR = Path(__file__).resolve().parent


def _run(cmd: list[str], status: Path, phase: str) -> None:
    status.write_text(
        json.dumps(
            {
                "phase": phase,
                "state": "running",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "command": cmd,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[phase] {phase}", flush=True)
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--version-id", default=None)
    parser.add_argument("--out-dir", default=str(EXP_DIR))
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    status = out_dir / "status.json"

    started = datetime.now(timezone.utc).isoformat()
    try:
        build_cmd = [
            sys.executable,
            "-u",
            str(EXP_DIR / "build_daily_state.py"),
            "--start",
            args.start,
            "--out-dir",
            str(out_dir),
        ]
        if args.end:
            build_cmd.extend(["--end", args.end])
        if args.version_id:
            build_cmd.extend(["--version-id", args.version_id])
        _run(build_cmd, status, "build_daily_state")

        _run(
            [
                sys.executable,
                "-u",
                str(EXP_DIR / "analyze_daily_state.py"),
                "--daily",
                str(out_dir / "daily_state.csv"),
                "--out-dir",
                str(out_dir),
            ],
            status,
            "analyze_daily_state",
        )
        _run(
            [
                sys.executable,
                "-u",
                str(EXP_DIR / "ml_probe.py"),
                "--daily",
                str(out_dir / "daily_state.csv"),
                "--out-dir",
                str(out_dir),
            ],
            status,
            "ml_probe",
        )
    except Exception as exc:
        status.write_text(
            json.dumps(
                {
                    "phase": "failed",
                    "state": "failed",
                    "started_at": started,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(exc),
                    "outputs": {
                        "daily_state": str(out_dir / "daily_state.csv"),
                        "findings": str(out_dir / "FINDINGS.md"),
                        "ml_probe": str(out_dir / "ml_probe.json"),
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        raise

    done = {
        "phase": "done",
        "state": "done",
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "outputs": {
            "daily_state": str(out_dir / "daily_state.csv"),
            "meta": str(out_dir / "daily_state_meta.json"),
            "regime_correlations": str(out_dir / "opportunity_regime_correlations.csv"),
            "outcome_correlations": str(out_dir / "opportunity_outcome_correlations.csv"),
            "cohorts": str(out_dir / "opportunity_cohorts.csv"),
            "findings": str(out_dir / "FINDINGS.md"),
            "ml_probe": str(out_dir / "ml_probe.json"),
        },
    }
    status.write_text(json.dumps(done, indent=2), encoding="utf-8")
    (out_dir / "done.json").write_text(json.dumps(done, indent=2), encoding="utf-8")
    print("\nDONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
