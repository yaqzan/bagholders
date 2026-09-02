"""
Production-equivalent MC validation for the v60 smooth put-wave reserve.

This runner keeps the candidate env-gated and default-inert in monte_carlo.py.
It executes baseline and candidate in separate Python processes so module-level
strategy constants are loaded cleanly for each policy.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DAILY = ROOT / ".codex" / "runs" / "v60_daily_opportunity_smooth_20260519_082415" / "daily_state.csv"

WINDOWS: dict[str, tuple[str, str]] = {
    "2020-crash": ("2020-02-18", "2020-04-30"),
    "2021": ("2021-01-01", "2021-12-31"),
    "2022": ("2022-01-01", "2022-12-31"),
    "2023": ("2023-01-01", "2023-12-31"),
    "2024": ("2024-01-01", "2024-12-31"),
    "2025": ("2025-01-01", "2025-12-31"),
    "dip": ("2025-11-01", "2026-04-24"),
    "22-now": ("2022-01-01", "2026-04-24"),
    "5y": ("2021-01-01", "2026-04-15"),
}

CANDIDATE_ENVS = {
    "reserve_0051": {
        "PUT_WAVE_RESERVE_STRENGTH": "0.75",
        "PUT_WAVE_RESERVE_FLOOR": "0.40",
        "PUT_WAVE_RESERVE_WEAK_TP_FLOOR": "0.539",
        "PUT_WAVE_RESERVE_WEAK_TP_WIDTH": "0.08",
        "PUT_WAVE_RESERVE_OPEN_PUT_TRIGGER": "13",
        "PUT_WAVE_RESERVE_OPEN_PUT_WIDTH": "10",
        "PUT_WAVE_RESERVE_FILLED_PUT_TRIGGER": "1",
        "PUT_WAVE_RESERVE_FILLED_PUT_WIDTH": "3",
        "PUT_WAVE_RESERVE_OPEN_PUT_SHARE_TRIGGER": "0.75",
        "PUT_WAVE_RESERVE_OPEN_PUT_SHARE_WIDTH": "0.15",
        "PUT_WAVE_RESERVE_WAVE_THRESHOLD": "0",
        "PUT_WAVE_RESERVE_WAVE_WIDTH": "16",
    },
    "reserve_0124": {
        "PUT_WAVE_RESERVE_STRENGTH": "0.55",
        "PUT_WAVE_RESERVE_FLOOR": "0.45",
        "PUT_WAVE_RESERVE_WEAK_TP_FLOOR": "0.539",
        "PUT_WAVE_RESERVE_WEAK_TP_WIDTH": "0.10",
        "PUT_WAVE_RESERVE_OPEN_PUT_TRIGGER": "9",
        "PUT_WAVE_RESERVE_OPEN_PUT_WIDTH": "8",
        "PUT_WAVE_RESERVE_FILLED_PUT_TRIGGER": "1",
        "PUT_WAVE_RESERVE_FILLED_PUT_WIDTH": "3",
        "PUT_WAVE_RESERVE_OPEN_PUT_SHARE_TRIGGER": "0.65",
        "PUT_WAVE_RESERVE_OPEN_PUT_SHARE_WIDTH": "0.25",
        "PUT_WAVE_RESERVE_WAVE_THRESHOLD": "0",
        "PUT_WAVE_RESERVE_WAVE_WIDTH": "6",
    },
    "reserve_0144": {
        "PUT_WAVE_RESERVE_STRENGTH": "0.70",
        "PUT_WAVE_RESERVE_FLOOR": "0.40",
        "PUT_WAVE_RESERVE_WEAK_TP_FLOOR": "0.5133",
        "PUT_WAVE_RESERVE_WEAK_TP_WIDTH": "0.06",
        "PUT_WAVE_RESERVE_OPEN_PUT_TRIGGER": "11",
        "PUT_WAVE_RESERVE_OPEN_PUT_WIDTH": "10",
        "PUT_WAVE_RESERVE_FILLED_PUT_TRIGGER": "2",
        "PUT_WAVE_RESERVE_FILLED_PUT_WIDTH": "3",
        "PUT_WAVE_RESERVE_OPEN_PUT_SHARE_TRIGGER": "0.55",
        "PUT_WAVE_RESERVE_OPEN_PUT_SHARE_WIDTH": "0.15",
        "PUT_WAVE_RESERVE_WAVE_THRESHOLD": "0",
        "PUT_WAVE_RESERVE_WAVE_WIDTH": "14",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_windows(raw: str) -> list[tuple[str, str, str]]:
    labels = [part.strip() for part in raw.split(",") if part.strip()]
    out = []
    for label in labels:
        if label not in WINDOWS:
            raise SystemExit(f"unknown window {label!r}; known={','.join(WINDOWS)}")
        start, end = WINDOWS[label]
        out.append((label, start, end))
    return out


def _child(args: argparse.Namespace) -> None:
    import contextlib

    try:
        from trader_database import DB
    except ImportError:
        from database.trader_database import DB
    from database.models.core import AlgorithmVersion
    import monte_carlo as mc

    def _reset_db() -> None:
        try:
            if not DB.is_closed():
                DB.close()
        except Exception:
            pass
        DB.connect(reuse_if_open=True)

    windows = json.loads(args.windows_json)
    _reset_db()
    version = AlgorithmVersion.get_active_scores_version()
    results: dict[str, Any] = {}
    with Path(args.engine_log).open("w", encoding="utf-8", errors="replace") as log:
        with contextlib.redirect_stdout(log):
            print(f"policy={args.policy}")
            print(f"version={version.git_commit} id={version.id}")
            for label, start, end in windows:
                for attempt in range(1, 4):
                    try:
                        _reset_db()
                        result = mc.run_window(label, date.fromisoformat(start), date.fromisoformat(end), version)
                        results[label] = result["seeded"]
                        break
                    except Exception as exc:
                        print(f"[warn] {label} attempt {attempt} failed: {exc}", flush=True)
                        if attempt >= 3:
                            raise
                        time.sleep(5 * attempt)
    _write_json(Path(args.result_json), results)


def _run_policy(policy: str, args: argparse.Namespace, windows: list[tuple[str, str, str]]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "MC_NO_DB_PERSIST": "1",
            "N_ITER_OVERRIDE": str(args.n),
        }
    )
    if args.no_mp:
        env["MC_NO_MP"] = "1"
    if policy != "baseline":
        if policy not in CANDIDATE_ENVS:
            raise SystemExit(f"unknown candidate {policy!r}; known={','.join(CANDIDATE_ENVS)}")
        env["PUT_WAVE_RESERVE_ENABLED"] = "1"
        env.update(CANDIDATE_ENVS[policy])
        env["PUT_WAVE_RESERVE_DAILY_STATE_CSV"] = str(args.daily)
    else:
        env["PUT_WAVE_RESERVE_ENABLED"] = "0"

    result_json = args.out_dir / f"{policy}_results.json"
    engine_log = args.out_dir / f"{policy}_engine.log"
    cmd = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        "--child",
        "--policy",
        policy,
        "--windows-json",
        json.dumps(windows),
        "--result-json",
        str(result_json),
        "--engine-log",
        str(engine_log),
    ]
    proc = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True)
    if proc.stdout:
        (args.out_dir / f"{policy}_stdout.log").write_text(proc.stdout, encoding="utf-8")
    if proc.stderr:
        (args.out_dir / f"{policy}_stderr.log").write_text(proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"{policy} failed with exit code {proc.returncode}; see {policy}_stderr.log")
    return json.loads(result_json.read_text(encoding="utf-8"))


def _write_outputs(
    out_dir: Path,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    windows: list[tuple[str, str, str]],
    n: int,
    candidate_name: str,
) -> None:
    rows = []
    def _log_ret(value: float) -> float:
        ret = value / 100.0
        return math.log1p(ret) if ret > -1.0 else float("-inf")

    for label, start, end in windows:
        b = baseline[label]
        c = candidate[label]
        rows.append(
            {
                "window": label,
                "start": start,
                "end": end,
                "n_iter": n,
                "baseline_mean_ret": b["mean_ret"],
                "candidate_mean_ret": c["mean_ret"],
                "delta_mean_ret_pp": c["mean_ret"] - b["mean_ret"],
                "baseline_med_ret": b["med_ret"],
                "candidate_med_ret": c["med_ret"],
                "delta_med_ret_pp": c["med_ret"] - b["med_ret"],
                "delta_mean_log_ret": _log_ret(c["mean_ret"]) - _log_ret(b["mean_ret"]),
                "delta_med_log_ret": _log_ret(c["med_ret"]) - _log_ret(b["med_ret"]),
                "baseline_worst_dd": b["worst_dd"],
                "candidate_worst_dd": c["worst_dd"],
                "delta_worst_dd_pp": c["worst_dd"] - b["worst_dd"],
                "baseline_mean_dd": b["mean_dd"],
                "candidate_mean_dd": c["mean_dd"],
                "delta_mean_dd_pp": c["mean_dd"] - b["mean_dd"],
                "baseline_p_coll": b["p_coll"],
                "candidate_p_coll": c["p_coll"],
                "delta_p_coll_pp": c["p_coll"] - b["p_coll"],
                "candidate_avg_reserved_cash_pct": c.get("avg_reserved_cash_pct", 0.0),
                "candidate_max_reserved_cash_pct": c.get("max_reserved_cash_pct", 0.0),
                "baseline_call_trades": b["call_trades"],
                "candidate_call_trades": c["call_trades"],
                "baseline_put_trades": b["put_trades"],
                "candidate_put_trades": c["put_trades"],
            }
        )

    csv_path = out_dir / "production_reserve_mc_summary.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Production-Equivalent Reserve MC Summary",
        "",
        f"- n_iter: {n}",
        f"- candidate: {candidate_name}",
        f"- generated_at: {_now()}",
        f"- summary_csv: `{csv_path}`",
        "",
        "| window | mean log delta | median log delta | worst DD delta | collapse delta | avg reserved | max reserved |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {window} | {delta_mean_log_ret:+.4f} | {delta_med_log_ret:+.4f} | "
            "{delta_worst_dd_pp:+.2f}pp | {delta_p_coll_pp:+.2f}pp | "
            "{candidate_avg_reserved_cash_pct:.2f}% | {candidate_max_reserved_cash_pct:.2f}% |".format(**row)
        )
    lines.extend(
        [
            "",
            "Gate interpretation:",
            "- Fail-window blocker if 2020-crash or 2024 worst DD regresses materially.",
            "- Broad-window blocker if 5y/22-now worst DD regresses or return drag dominates the DD benefit.",
        ]
    )
    (out_dir / "production_reserve_mc_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--n", type=int, default=240)
    parser.add_argument("--windows", default="2020-crash,2024,2025,2022,22-now,5y")
    parser.add_argument("--baseline-json", type=Path)
    parser.add_argument("--candidate", default="reserve_0051", choices=sorted(CANDIDATE_ENVS))
    parser.add_argument("--no-mp", action="store_true")
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--policy", default="")
    parser.add_argument("--windows-json", default="")
    parser.add_argument("--result-json", default="")
    parser.add_argument("--engine-log", default="")
    args = parser.parse_args()

    if args.child:
        _child(args)
        return

    if args.out_dir is None:
        raise SystemExit("--out-dir is required")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    windows = _parse_windows(args.windows)
    status_path = args.out_dir / "status.json"
    _write_json(
        status_path,
        {
            "phase": "production_reserve_mc",
            "state": "running",
            "started_at": _now(),
            "updated_at": _now(),
            "n_iter": args.n,
            "windows": [w[0] for w in windows],
            "daily_state": str(args.daily),
        },
    )

    if args.baseline_json:
        baseline = json.loads(args.baseline_json.read_text(encoding="utf-8"))
    else:
        baseline = _run_policy("baseline", args, windows)
    _write_json(
        status_path,
        {
            "phase": "production_reserve_mc",
            "state": "running",
            "completed": 1,
            "total": 2,
            "updated_at": _now(),
            "current": args.candidate,
        },
    )
    candidate = _run_policy(args.candidate, args, windows)
    _write_outputs(args.out_dir, baseline, candidate, windows, args.n, args.candidate)
    _write_json(
        status_path,
        {
            "phase": "production_reserve_mc",
            "state": "done",
            "completed": 2,
            "total": 2,
            "updated_at": _now(),
            "summary_csv": str(args.out_dir / "production_reserve_mc_summary.csv"),
            "summary_md": str(args.out_dir / "production_reserve_mc_summary.md"),
        },
    )


if __name__ == "__main__":
    main()
