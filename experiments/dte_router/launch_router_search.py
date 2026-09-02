"""Launch the DTE router search with Codex-friendly run artifacts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def atomic_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def git_output(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8", errors="replace").strip()


def monitor_script() -> str:
    return r"""
param(
  [Parameter(Mandatory=$true)][int]$PidToWatch,
  [Parameter(Mandatory=$true)][string]$RunDir
)
$ErrorActionPreference = "SilentlyContinue"
$done = Join-Path $RunDir "done.json"
$failed = Join-Path $RunDir "failed.json"
$log = Join-Path $RunDir "run.log"
$recent = Join-Path $RunDir "run.recent.log"
$monitor = Join-Path $RunDir "monitor.log"
"monitor started $(Get-Date -Format o) pid=$PidToWatch" | Out-File -FilePath $monitor -Encoding utf8
while ($true) {
  if (Test-Path $done) { "done observed $(Get-Date -Format o)" | Out-File -FilePath $monitor -Append -Encoding utf8; break }
  if (Test-Path $failed) { "failed observed $(Get-Date -Format o)" | Out-File -FilePath $monitor -Append -Encoding utf8; break }
  if (Test-Path $log) {
    $lines = Get-Content -Path $log -Tail 120
    $tmp = "$recent.tmp"
    $lines | Out-File -FilePath $tmp -Encoding utf8
    Move-Item -Force -LiteralPath $tmp -Destination $recent
  }
  $proc = Get-Process -Id $PidToWatch
  if ($null -eq $proc) {
    if (-not (Test-Path $done) -and -not (Test-Path $failed)) {
      $payload = @{
        state = "failed"
        finished_at = (Get-Date).ToUniversalTime().ToString("o")
        exit_code = $null
        failure_reason = "process exited without done.json or failed.json"
        run_log = $log
      } | ConvertTo-Json -Depth 4
      $payload | Out-File -FilePath $failed -Encoding utf8
    }
    break
  }
  Start-Sleep -Seconds 20
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch detached DTE router search")
    parser.add_argument("--profile", default="sentinel")
    parser.add_argument("--max-candidates", type=int, default=240)
    parser.add_argument("--run-dir", default=None)
    args = parser.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else ROOT / ".codex" / "runs" / f"dte_router_{utc_stamp()}"
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    log_path = run_dir / "run.log"
    command = [
        sys.executable,
        "-u",
        str(ROOT / "experiments" / "dte_router" / "router_search.py"),
        "--run-dir",
        str(run_dir),
        "--profile",
        args.profile,
        "--max-candidates",
        str(args.max_candidates),
    ]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.setdefault("OPTION_PRICING_AWARE", "1")

    launch = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "cwd": str(ROOT),
        "command": command,
        "git_commit": git_output(["rev-parse", "HEAD"]),
        "git_branch": git_output(["rev-parse", "--abbrev-ref", "HEAD"]),
        "profile": args.profile,
        "max_candidates": args.max_candidates,
        "expected_outputs": {
            "status": str(run_dir / "status.json"),
            "done": str(run_dir / "done.json"),
            "failed": str(run_dir / "failed.json"),
            "run_log": str(log_path),
            "recent_log": str(run_dir / "run.recent.log"),
            "findings": str(run_dir / "findings.md"),
            "candidate_summary": str(run_dir / "candidate_summary.csv"),
            "candidate_windows": str(run_dir / "candidate_windows.csv"),
        },
    }
    atomic_json(run_dir / "launch.json", launch)

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    log_fh = log_path.open("ab", buffering=0)
    proc = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=False,
    )
    (run_dir / "pid.txt").write_text(str(proc.pid), encoding="utf-8")
    launch["pid"] = proc.pid
    atomic_json(run_dir / "launch.json", launch)

    monitor_path = run_dir / "monitor.ps1"
    monitor_path.write_text(monitor_script(), encoding="utf-8")
    monitor_cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(monitor_path),
        "-PidToWatch",
        str(proc.pid),
        "-RunDir",
        str(run_dir),
    ]
    subprocess.Popen(
        monitor_cmd,
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=False,
    )

    print(json.dumps({"run_dir": str(run_dir), "pid": proc.pid, "run_log": str(log_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
