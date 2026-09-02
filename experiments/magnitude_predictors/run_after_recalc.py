"""Wait for a scoring recalc to finish, then run magnitude portfolio MC.

This keeps portfolio validation from accidentally reading a partial score
version while a full recalc is still writing scores.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _run_logged(cmd: list[str], cwd: Path, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return proc.wait()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recalc-dir", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--lookback", type=int, default=1825)
    parser.add_argument("--variants", required=True)
    parser.add_argument("--windows", required=True)
    parser.add_argument("--n-iter", type=int, default=500)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    parser.add_argument("--poll-sec", type=int, default=60)
    args = parser.parse_args()

    recalc_dir = Path(args.recalc_dir)
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "status.json"

    base_status: dict[str, Any] = {
        "state": "waiting_recalc",
        "phase": "waiting_recalc",
        "started_at": _now(),
        "updated_at": _now(),
        "cwd": str(ROOT),
        "run_dir": str(run_dir),
        "recalc_dir": str(recalc_dir),
        "lookback": args.lookback,
        "variants": args.variants,
        "windows": args.windows,
        "n_iter": args.n_iter,
        "workers": args.workers,
    }
    _write_json(status_path, base_status)

    try:
        while True:
            done = recalc_dir / "done.json"
            failed = recalc_dir / "failed.json"
            if failed.exists():
                payload = dict(base_status)
                payload.update(
                    {
                        "state": "failed",
                        "phase": "recalc_failed",
                        "finished_at": _now(),
                        "recalc_failed": _read_json(failed),
                    }
                )
                _write_json(run_dir / "failed.json", payload)
                _write_json(status_path, payload)
                return
            if done.exists():
                break
            recalc_status = _read_json(recalc_dir / "status.json")
            base_status.update(
                {
                    "updated_at": _now(),
                    "recalc_status": recalc_status,
                }
            )
            _write_json(status_path, base_status)
            time.sleep(max(5, args.poll_sec))

        base_status.update({"state": "building_features", "phase": "build_features", "updated_at": _now()})
        _write_json(status_path, base_status)
        build_rc = _run_logged(
            [sys.executable, "-u", "experiments/magnitude_predictors/build_features.py", str(args.lookback)],
            ROOT,
            run_dir / "build_features.log",
        )
        if build_rc != 0:
            payload = dict(base_status)
            payload.update(
                {
                    "state": "failed",
                    "phase": "build_features_failed",
                    "finished_at": _now(),
                    "exit_code": build_rc,
                    "log": str(run_dir / "build_features.log"),
                }
            )
            _write_json(run_dir / "failed.json", payload)
            _write_json(status_path, payload)
            return

        portfolio_dir = run_dir / "portfolio"
        base_status.update(
            {
                "state": "running_portfolio_mc",
                "phase": "portfolio_mc",
                "updated_at": _now(),
                "portfolio_dir": str(portfolio_dir),
            }
        )
        _write_json(status_path, base_status)
        mc_rc = _run_logged(
            [
                sys.executable,
                "-u",
                "experiments/magnitude_predictors/portfolio_tp_mc.py",
                "--lookback",
                str(args.lookback),
                "--variants",
                args.variants,
                "--windows",
                args.windows,
                "--n-iter",
                str(args.n_iter),
                "--workers",
                str(args.workers),
                "--run-dir",
                str(portfolio_dir),
            ],
            ROOT,
            run_dir / "portfolio_mc.log",
        )
        if mc_rc != 0:
            payload = dict(base_status)
            payload.update(
                {
                    "state": "failed",
                    "phase": "portfolio_mc_failed",
                    "finished_at": _now(),
                    "exit_code": mc_rc,
                    "log": str(run_dir / "portfolio_mc.log"),
                    "portfolio_status": _read_json(portfolio_dir / "status.json"),
                }
            )
            _write_json(run_dir / "failed.json", payload)
            _write_json(status_path, payload)
            return

        payload = dict(base_status)
        payload.update(
            {
                "state": "done",
                "phase": "complete",
                "finished_at": _now(),
                "portfolio_status": _read_json(portfolio_dir / "status.json"),
                "summary_csv": str(portfolio_dir / "summary.csv"),
            }
        )
        _write_json(run_dir / "done.json", payload)
        _write_json(status_path, payload)
    except Exception as exc:
        payload = dict(base_status)
        payload.update({"state": "failed", "phase": "exception", "finished_at": _now(), "error": repr(exc)})
        _write_json(run_dir / "failed.json", payload)
        _write_json(status_path, payload)
        raise


if __name__ == "__main__":
    main()
