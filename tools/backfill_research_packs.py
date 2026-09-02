from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_versions import manager  # noqa: E402
from algorithm_versions.research_pack import (  # noqa: E402
    DEFAULT_DTE,
    DEFAULT_LOOKBACKS,
    assessment_runs_payload,
    ensure_db_open,
    json_safe,
    parse_int_csv,
    parse_portfolio_profiles,
    resolve_version,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_versions(tokens: list[str] | None, min_version: int | None, max_version: int | None) -> list[str]:
    if tokens:
        versions: list[str] = []
        for token in tokens:
            for part in token.replace(";", ",").split(","):
                part = part.strip()
                if part:
                    versions.append(manager.normalize_version_key(part))
        return list(dict.fromkeys(versions))

    if min_version is None or max_version is None:
        raise SystemExit("Pass --versions or both --min-version and --max-version.")
    if min_version > max_version:
        raise SystemExit("--min-version must be <= --max-version.")
    return [f"v{n}" for n in range(min_version, max_version + 1)]


def run_assessment(version_key: str, dte: str, log_path: Path, profiles: list[str] | None = None) -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    cmd = [sys.executable, "-u", "trader.py", "assess", "--force", "--dte", str(dte), "--version", version_key]
    if profiles:
        cmd.extend(["--profiles", ",".join(profiles)])
    with log_path.open("ab") as log:
        log.write(("COMMAND " + " ".join(cmd) + "\n").encode("utf-8"))
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    return int(proc.returncode)


def run_pack_build(
    version_key: str,
    lookbacks: list[int],
    dte: str,
    profiles: list[str],
    portfolio_windows: str | None,
    run_portfolio_windows: bool,
    log_path: Path,
) -> tuple[int, dict[str, Any] | None]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    cmd = [
        sys.executable,
        "-u",
        "tools/build_research_pack.py",
        "--version",
        version_key,
        "--lookbacks",
        ",".join(str(v) for v in lookbacks),
        "--dte",
        str(dte),
        "--profiles",
        ",".join(profiles),
        # Bulk backfill builds many versions; the supply-row + PRF comparability
        # tail is a deliberate per-version op (DB scan each), not run in bulk here.
        # Single-version ship builds get it by default (research_pack.main).
        "--no-comparability",
    ]
    if portfolio_windows:
        cmd.extend(["--portfolio-windows", portfolio_windows])
    if run_portfolio_windows:
        cmd.append("--run-portfolio-windows")
    with log_path.open("ab") as log:
        log.write(("COMMAND " + " ".join(cmd) + "\n").encode("utf-8"))
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)

    manifest = None
    try:
        ensure_db_open(force_reconnect=True)
        version = resolve_version(version_key)
        manifest_path = ROOT / ".cache" / "algorithm_versions" / f"v{int(version.id)}" / "research_pack" / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        manifest = None
    return int(proc.returncode), manifest


def parse_temporal_dtes(raw: str) -> list[str]:
    token = str(raw or DEFAULT_DTE).strip().lower()
    if token == "both":
        return ["30", "15"]
    if token in ("30", "15"):
        return [token]
    raise SystemExit("--temporal-dte must be 15, 30, or both.")


def run_temporal_refresh(version_key: str, dtes: list[str], profiles: list[str], log_path: Path) -> list[dict[str, Any]]:
    from backtest_cascade import compute_and_store_temporal as temporal_30
    from backtest_cascade_15dte import compute_and_store_temporal as temporal_15

    version = resolve_version(version_key)
    rows: list[dict[str, Any]] = []
    with log_path.open("ab") as log:
        for dte in dtes:
            for profile in profiles:
                started = utc_now()
                log.write((f"TEMPORAL version={version_key} dte={dte} profile={profile} started_at={started}\n").encode("utf-8"))
                log.flush()
                try:
                    if dte == "15":
                        temporal_15(version=version, dte_strategy="15", portfolio_profile=profile)
                    else:
                        temporal_30(version=version, dte_strategy="30", portfolio_profile=profile)
                    finished = utc_now()
                    rows.append({
                        "version": version_key,
                        "dte": dte,
                        "profile": profile,
                        "started_at": started,
                        "finished_at": finished,
                        "ok": True,
                    })
                    log.write((f"TEMPORAL version={version_key} dte={dte} profile={profile} finished_at={finished} ok=true\n").encode("utf-8"))
                except Exception as exc:
                    finished = utc_now()
                    rows.append({
                        "version": version_key,
                        "dte": dte,
                        "profile": profile,
                        "started_at": started,
                        "finished_at": finished,
                        "ok": False,
                        "error": str(exc),
                    })
                    log.write((f"TEMPORAL version={version_key} dte={dte} profile={profile} finished_at={finished} ok=false error={exc}\n").encode("utf-8"))
                log.flush()
    return rows


def launch_detached(argv: list[str], requested_run_dir: str | None) -> int:
    run_dir = Path(requested_run_dir) if requested_run_dir else ROOT / ".codex" / "runs" / f"research_pack_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    child_args = [arg for arg in argv if arg != "--detach"]
    if "--run-dir" not in child_args:
        child_args.extend(["--run-dir", str(run_dir)])

    log_path = run_dir / "run.log"
    status_path = run_dir / "status.json"
    cmd = [sys.executable, "-u", str(Path(__file__).resolve()), *child_args]
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )

    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )

    launch_payload = {
        "launched_at": utc_now(),
        "pid": proc.pid,
        "cwd": str(ROOT),
        "command": cmd,
        "outputs": {
            "run_dir": str(run_dir),
            "run_log": str(log_path),
            "status": str(status_path),
            "done": str(run_dir / "done.json"),
            "failed": str(run_dir / "failed.json"),
        },
    }
    write_json(run_dir / "launch.json", launch_payload)
    (run_dir / "pid.txt").write_text(str(proc.pid) + "\n", encoding="utf-8")
    if not status_path.exists():
        write_json(
            status_path,
            {
                "started_at": utc_now(),
                "updated_at": utc_now(),
                "state": "launched",
                "phase": "detached",
                "pid": proc.pid,
                "outputs": launch_payload["outputs"],
            },
        )

    print(f"pid={proc.pid}")
    print(f"run_log={log_path}")
    print(f"status={status_path}")
    print(f"done={run_dir / 'done.json'}")
    print(f"failed={run_dir / 'failed.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Weekend-safe harness for hydrating assessment rows and research packs "
            "after score recalcs finish. It never recalculates scores."
        )
    )
    parser.add_argument("--versions", nargs="*", help="Version list, e.g. v39,v40 v58.")
    parser.add_argument("--min-version", type=int, default=None)
    parser.add_argument("--max-version", type=int, default=None)
    parser.add_argument("--lookbacks", default=None, help="Comma-separated lookbacks to require. Default pack set.")
    parser.add_argument("--dte", default=DEFAULT_DTE)
    parser.add_argument("--profiles", default=None, help="Portfolio profiles: all or comma-separated keys. Default: sentinel.")
    parser.add_argument("--portfolio-windows", default=None, help="Portfolio window names to build: comma-separated. Default: all.")
    parser.add_argument("--run-temporal", action="store_true", help="Refresh BacktestTemporalStats rows for selected versions/profiles before pack build.")
    parser.add_argument("--temporal-dte", default="both", help="Temporal DTE refresh set: 15, 30, or both. Default: both.")
    parser.add_argument("--run-assess-missing", action="store_true", help="Run trader assess --force for versions with missing WR/TP rows.")
    parser.add_argument("--force-assess", action="store_true", help="Run trader assess --force for every selected version before building packs.")
    parser.add_argument("--run-portfolio-windows", action="store_true", help="Run deterministic portfolio windows inside each pack.")
    parser.add_argument("--skip-pack", action="store_true", help="Only inspect or assess; do not build research packs.")
    parser.add_argument("--run-dir", default=None, help="Status/log directory. Defaults under .codex/runs.")
    parser.add_argument("--detach", action="store_true", help="Launch the backfill harness in the background and return paths.")
    args = parser.parse_args(argv)

    if args.detach:
        return launch_detached(list(argv or []), args.run_dir)

    ensure_db_open()
    versions = parse_versions(args.versions, args.min_version, args.max_version)
    lookbacks = parse_int_csv(args.lookbacks, DEFAULT_LOOKBACKS)
    profile_keys = parse_portfolio_profiles(args.profiles)
    temporal_dtes = parse_temporal_dtes(args.temporal_dte)

    run_dir = Path(args.run_dir) if args.run_dir else ROOT / ".codex" / "runs" / f"research_pack_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "run.log"
    status_path = run_dir / "status.json"
    pid_path = run_dir / "pid.txt"
    done_path = run_dir / "done.json"
    failed_path = run_dir / "failed.json"
    pid_path.write_text(str(os.getpid()) + "\n", encoding="utf-8")

    status: dict[str, Any] = {
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "state": "running",
        "phase": "initializing",
        "versions": versions,
        "profiles": profile_keys,
        "portfolio_windows": args.portfolio_windows or "all",
        "temporal_dtes": temporal_dtes if args.run_temporal else [],
        "completed": 0,
        "total": len(versions),
        "outputs": {
            "run_dir": str(run_dir),
            "run_log": str(log_path),
            "status": str(status_path),
            "pid": str(pid_path),
            "done": str(done_path),
            "failed": str(failed_path),
        },
        "results": [],
    }
    write_json(status_path, status)

    try:
        for index, version_key in enumerate(versions, start=1):
            ensure_db_open(force_reconnect=True)
            version = resolve_version(version_key)
            row: dict[str, Any] = {
                "version": version_key,
                "version_id": int(version.id),
                "started_at": utc_now(),
                "profiles": profile_keys,
                "temporal_ran": False,
                "temporal_rows": [],
                "assessment_ran": False,
                "assessment_exit_code": None,
                "pack_built": False,
                "pack_manifest": None,
                "errors": [],
            }
            status.update({"phase": f"version {version_key}", "updated_at": utc_now(), "current": row})
            write_json(status_path, status)

            if args.run_temporal:
                row["temporal_ran"] = True
                temporal_rows = run_temporal_refresh(version_key, temporal_dtes, profile_keys, log_path)
                row["temporal_rows"] = temporal_rows
                for temporal_row in temporal_rows:
                    if not temporal_row.get("ok"):
                        row["errors"].append(
                            "temporal failed: "
                            f"dte={temporal_row.get('dte')} profile={temporal_row.get('profile')} "
                            f"{temporal_row.get('error')}"
                        )
                status.update({"updated_at": utc_now(), "current": row})
                write_json(status_path, status)
                ensure_db_open(force_reconnect=True)

            coverage = assessment_runs_payload(version, lookbacks, dte=args.dte)
            row["assessment_missing_before"] = coverage["missing"]
            if args.force_assess or (coverage["missing"] and args.run_assess_missing):
                row["assessment_ran"] = True
                exit_code = run_assessment(version_key, args.dte, log_path, profiles=profile_keys)
                row["assessment_exit_code"] = exit_code
                if exit_code != 0:
                    row["errors"].append(f"assess exited {exit_code}")
                coverage = assessment_runs_payload(version, lookbacks, dte=args.dte)
            row["assessment_missing_after"] = coverage["missing"]

            if not args.skip_pack:
                try:
                    ensure_db_open(force_reconnect=True)
                    exit_code, manifest = run_pack_build(
                        version_key,
                        lookbacks,
                        args.dte,
                        profile_keys,
                        args.portfolio_windows,
                        args.run_portfolio_windows,
                        log_path,
                    )
                    row["pack_exit_code"] = exit_code
                    if exit_code != 0:
                        row["errors"].append(f"pack exited {exit_code}")
                    row["pack_built"] = bool(exit_code == 0 and manifest)
                    row["pack_manifest"] = (manifest or {}).get("outputs", {}).get("manifest")
                    row["pack_health"] = (manifest or {}).get("health")
                except Exception as exc:
                    row["errors"].append(f"pack failed: {exc}")

            row["finished_at"] = utc_now()
            status["results"].append(row)
            status["completed"] = index
            status["current"] = None
            status["updated_at"] = utc_now()
            write_json(status_path, status)

        status["state"] = "done"
        status["phase"] = "complete"
        status["finished_at"] = utc_now()
        write_json(done_path, status)
        write_json(status_path, status)
        print(f"status={status_path}")
        print(f"done={done_path}")
        return 0
    except Exception as exc:
        status["state"] = "failed"
        status["phase"] = "failed"
        status["error"] = str(exc)
        status["finished_at"] = utc_now()
        write_json(failed_path, status)
        write_json(status_path, status)
        print(f"status={status_path}")
        print(f"failed={failed_path}")
        raise


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
