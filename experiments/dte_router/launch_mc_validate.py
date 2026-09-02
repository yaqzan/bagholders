"""Launch DTE router Monte Carlo validation as a detached Codex-aware run."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = ROOT / ".codex" / "runs"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def latest_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return None


def build_monitor(run_dir: Path, pid: int) -> Path:
    script = run_dir / "monitor.ps1"
    done = str(run_dir / "done.json")
    failed = str(run_dir / "failed.json")
    status = str(run_dir / "status.json")
    log = str(run_dir / "run.log")
    recent = str(run_dir / "run.recent.log")
    body = f"""
$pidToWatch = {pid}
$donePath = '{done}'
$failedPath = '{failed}'
$statusPath = '{status}'
$logPath = '{log}'
$recentPath = '{recent}'
while ($true) {{
    if (Test-Path -LiteralPath $logPath) {{
        Get-Content -LiteralPath $logPath -Tail 120 | Set-Content -LiteralPath $recentPath -Encoding UTF8
    }}
    if ((Test-Path -LiteralPath $donePath) -or (Test-Path -LiteralPath $failedPath)) {{ break }}
    $proc = Get-Process -Id $pidToWatch -ErrorAction SilentlyContinue
    if ($null -eq $proc) {{
        Start-Sleep -Seconds 2
        if (-not (Test-Path -LiteralPath $donePath) -and -not (Test-Path -LiteralPath $failedPath)) {{
            $payload = [ordered]@{{
                state = 'failed'
                finished_at = (Get-Date).ToUniversalTime().ToString('o')
                error = 'process exited without done.json'
                pid = $pidToWatch
                status_path = $statusPath
                log_path = $logPath
            }}
            $payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $failedPath -Encoding UTF8
        }}
        break
    }}
    Start-Sleep -Seconds 10
}}
"""
    script.write_text(body.strip() + "\n", encoding="utf-8")
    return script


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate",
        default="call_ge_80",
        choices=[
            "call_ge_80",
            "call_ge_85",
            "call_ge_80_midalloc",
            "call_ge_80_lowalloc",
            "call_ge_85_midalloc",
            "call_ge_85_lowalloc",
            "call_ge_80_daycap1",
            "call_ge_85_daycap1",
            "call_ge_80_pressure_le_16",
            "call_ge_80_pressure_le_8",
            "call_ge_85_pressure_le_16",
            "call_ge_80_daycap1_breadth_le_55",
            "call_ge_80_daycap1_breadth_le_65",
            "call_ge_80_daycap1_breadth_36_65",
            "call_ge_80_daycap1_puts_ge_4",
            "call_ge_80_daycap1_puts_ge_8",
            "call_ge_80_daycap1_puts_ge_12",
            "call_ge_80_daycap1_trend_lt_65",
            "call_ge_80_daycap1_trend_lt_50",
            "call_ge_80_daycap1_trend_lt_40",
            "call_ge_80_daycap1_trend_lt_35",
            "call_ge_80_daycap1_trend_35_65",
            "call_ge_80_daycap1_trend_20_30",
            "call_ge_80_daycap1_trend_20_30_vix_ge_25_no_haven_etf_regime_50_85",
            "call_ge_80_daycap1_trend_lt_35_vix_25_35",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25_no_haven_etf",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25_no_haven_etf_regime_50_85",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25_no_haven_etf_regime_50_85_midalloc",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25_stock_only_regime_50_85",
            "call_ge_80_daycap1_trend_lt_35_vix_lt_35",
            "call_80_84_breadth_le_35",
            "call_80_84_breadth_36_55",
            "call_80_84_breadth_46_55",
            "call_80_84_breadth_le_55",
            "put_le_15",
            "put_6_10",
            "put_11_15_breadth_66_75",
        ],
    )
    parser.add_argument("--n-iter", type=int, default=160)
    parser.add_argument("--windows", default="all")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--no-mp", action="store_true")
    parser.add_argument("--tag", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = args.tag or f"dte_router_mc_{args.candidate}_{stamp}"
    run_dir = RUN_ROOT / tag
    run_dir.mkdir(parents=True, exist_ok=False)

    log_path = run_dir / "run.log"
    cmd = [
        sys.executable,
        "-u",
        str(ROOT / "experiments" / "dte_router" / "mc_validate_router.py"),
        "--run-dir",
        str(run_dir),
        "--candidate",
        args.candidate,
        "--n-iter",
        str(args.n_iter),
        "--windows",
        args.windows,
    ]
    if args.workers:
        cmd.extend(["--workers", str(args.workers)])
    if args.no_mp:
        cmd.append("--no-mp")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["MC_NO_DB_PERSIST"] = "1"

    with log_path.open("ab", buffering=0) as log:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        )

    (run_dir / "pid.txt").write_text(str(proc.pid), encoding="utf-8")
    monitor = build_monitor(run_dir, proc.pid)
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(monitor),
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
    )

    atomic_json(
        run_dir / "launch.json",
        {
            "started_at": utc_now(),
            "cwd": str(ROOT),
            "cmd": cmd,
            "pid": proc.pid,
            "git_commit": latest_commit(),
            "env": {
                "PYTHONIOENCODING": env.get("PYTHONIOENCODING"),
                "PYTHONUTF8": env.get("PYTHONUTF8"),
                "MC_NO_DB_PERSIST": env.get("MC_NO_DB_PERSIST"),
            },
            "expected_outputs": [
                str(run_dir / "done.json"),
                str(run_dir / "failed.json"),
                str(run_dir / "status.json"),
                str(run_dir / "mc_window_summary.csv"),
                str(run_dir / "findings.md"),
            ],
            "log_path": str(log_path),
            "recent_log_path": str(run_dir / "run.recent.log"),
            "monitor_path": str(monitor),
        },
    )
    print(json.dumps({"run_dir": str(run_dir), "pid": proc.pid, "log_path": str(log_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
