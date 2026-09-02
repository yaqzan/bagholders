"""Orchestrate the v60 daily opportunity smooth-controller research run.

This wrapper is meant to be launched by the Codex background pattern with
stdout redirected to run.log. It writes run-level status.json plus done/failed
terminal artifacts in the chosen out-dir.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
EXP_DIR = Path(__file__).resolve().parent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _run(cmd: list[str], out_dir: Path, phase: str, extra: dict[str, Any] | None = None) -> None:
    payload = {
        "phase": phase,
        "state": "running",
        "updated_at": _now(),
        "command": cmd,
        "outputs": _outputs(out_dir),
    }
    if extra:
        payload.update(extra)
    _write_json(out_dir / "status.json", payload)
    print(f"\n[phase] {phase}", flush=True)
    print(" ".join(cmd), flush=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    subprocess.run(cmd, cwd=ROOT, check=True, env=env)


def _outputs(out_dir: Path) -> dict[str, str]:
    return {
        "daily_state": str(out_dir / "daily_state.csv"),
        "daily_meta": str(out_dir / "daily_state_meta.json"),
        "smooth_ranked": str(out_dir / "smooth_controller_ranked.csv"),
        "smooth_top": str(out_dir / "smooth_controller_top_candidates.csv"),
        "failwindow_mc": str(out_dir / "smooth_controller_failwindow_mc.csv"),
        "failwindow_ranked": str(out_dir / "smooth_controller_failwindow_mc_ranked.csv"),
        "longwindow_mc": str(out_dir / "smooth_controller_longwindow_mc.csv"),
        "longwindow_ranked": str(out_dir / "smooth_controller_longwindow_mc_ranked.csv"),
        "summary": str(out_dir / "v60_smooth_research_summary.md"),
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _select_failwindow_survivors(path: Path, limit: int) -> list[str]:
    rows = _read_csv(path)
    by_policy: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_policy.setdefault(row["policy"], []).append(row)
    scored: list[tuple[float, str]] = []
    for policy, prs in by_policy.items():
        if policy == "baseline":
            continue
        if len({r["window"] for r in prs}) < 2:
            continue
        worst_dd_max = max(float(r["worst_dd_delta"]) for r in prs)
        mean_ret_avg = sum(float(r["mean_ret_delta"]) for r in prs) / len(prs)
        mean_dd_avg = sum(float(r["mean_dd_delta"]) for r in prs) / len(prs)
        y2024 = next((r for r in prs if r["window"] == "2024"), None)
        if worst_dd_max > 1.0:
            continue
        if y2024 and float(y2024["mean_ret_delta"]) < 0.0 and float(y2024["worst_dd_delta"]) > -0.50:
            continue
        score = -worst_dd_max + 0.01 * mean_ret_avg - 0.25 * max(0.0, mean_dd_avg)
        scored.append((score, policy))
    scored.sort(reverse=True)
    return [policy for _, policy in scored[:limit]]


def _top_rows(path: Path, n: int = 12) -> list[dict[str, str]]:
    return _read_csv(path)[:n]


def _write_summary(out_dir: Path, survivors: list[str]) -> None:
    daily_meta = {}
    meta_path = out_dir / "daily_state_meta.json"
    if meta_path.exists():
        daily_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    top_det = _top_rows(out_dir / "smooth_controller_ranked.csv", 12)
    fail_rows = _read_csv(out_dir / "smooth_controller_failwindow_mc_ranked.csv")
    long_rows = _read_csv(out_dir / "smooth_controller_longwindow_mc_ranked.csv")

    lines = [
        "# v60 Smooth Daily Opportunity Allocation Research",
        "",
        "Stage 3 research only. No score rows were written and ALGORITHM_VERSION was not changed.",
        "",
        "## Dataset",
        "",
        f"- version: v{daily_meta.get('version_id')} {daily_meta.get('commit')}",
        f"- rows: {daily_meta.get('n_days')}",
        f"- trades: {daily_meta.get('n_trades')}",
        f"- replay: {daily_meta.get('portfolio_replay_source')}",
        f"- max_positions: {daily_meta.get('max_positions')}, call_cap: {daily_meta.get('max_positions_call')}",
        "",
        "## Deterministic Top Candidates",
        "",
        "| rank | branch | policy | utility | crash DD d | 2024 DD d | 2024 log d | 5y DD d |",
        "|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in top_det:
        lines.append(
            f"| {row.get('rank')} | {row.get('branch')} | `{row.get('policy')}` | "
            f"{float(row.get('utility', 0.0)):+.2f} | {float(row.get('crash_dd_delta_pp', 0.0)):+.2f}pp | "
            f"{float(row.get('y2024_dd_delta_pp', 0.0)):+.2f}pp | {float(row.get('y2024_log_delta', 0.0)):+.2f} | "
            f"{float(row.get('fivey_dd_delta_pp', 0.0)):+.2f}pp |"
        )

    lines.extend(
        [
            "",
            "## Fail-Window MC",
            "",
            f"- survivors for broader MC: {', '.join(survivors) if survivors else 'none'}",
            "",
            "| window | policy | mean ret d | worst DD d | mean DD d |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in fail_rows:
        lines.append(
            f"| {row.get('window')} | `{row.get('policy')}` | {float(row.get('mean_ret_delta', 0.0)):+.2f} | "
            f"{float(row.get('worst_dd_delta', 0.0)):+.2f} | {float(row.get('mean_dd_delta', 0.0)):+.2f} |"
        )

    if long_rows:
        lines.extend(
            [
                "",
                "## Broader MC",
                "",
                "| window | policy | mean ret d | worst DD d | mean DD d |",
                "|---|---|---:|---:|---:|",
            ]
        )
        for row in long_rows:
            lines.append(
                f"| {row.get('window')} | `{row.get('policy')}` | {float(row.get('mean_ret_delta', 0.0)):+.2f} | "
                f"{float(row.get('worst_dd_delta', 0.0)):+.2f} | {float(row.get('mean_dd_delta', 0.0)):+.2f} |"
            )

    lines.extend(
        [
            "",
            "## Judgment",
            "",
            "- Ship/no-ship decision belongs in the final agent read after inspecting the MC artifacts.",
            "- A candidate is not ship-worthy unless 2020-crash and 2024 are stable, then 2022/2025/22-now/5y remain stable.",
        ]
    )
    (out_dir / "v60_smooth_research_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--version-id", type=int, default=None)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--budget-per-branch", type=int, default=180)
    parser.add_argument("--fail-n", type=int, default=240)
    parser.add_argument("--long-n", type=int, default=240)
    parser.add_argument("--survivor-limit", type=int, default=4)
    args = parser.parse_args(argv)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    started_at = _now()
    _write_json(
        out_dir / "status.json",
        {"phase": "start", "state": "running", "started_at": started_at, "outputs": _outputs(out_dir)},
    )

    try:
        py = sys.executable
        common_version = []
        if args.version_id:
            common_version = ["--version-id", str(args.version_id)]

        build = [
            py,
            "-u",
            str(EXP_DIR / "build_daily_state.py"),
            "--start",
            args.start,
            "--out-dir",
            str(out_dir),
            *common_version,
        ]
        if args.end:
            build.extend(["--end", args.end])
        _run(build, out_dir, "build_daily_state")

        _run(
            [
                py,
                "-u",
                str(EXP_DIR / "analyze_daily_state.py"),
                "--daily",
                str(out_dir / "daily_state.csv"),
                "--out-dir",
                str(out_dir),
            ],
            out_dir,
            "analyze_daily_state",
        )
        _run(
            [
                py,
                "-u",
                str(EXP_DIR / "ml_probe.py"),
                "--daily",
                str(out_dir / "daily_state.csv"),
                "--out-dir",
                str(out_dir),
            ],
            out_dir,
            "ml_probe",
        )
        _run(
            [
                py,
                "-u",
                str(EXP_DIR / "mine_conditional_alpha.py"),
                "--daily",
                str(out_dir / "daily_state.csv"),
                "--out-dir",
                str(out_dir),
            ],
            out_dir,
            "mine_conditional_alpha",
        )
        _run(
            [
                py,
                "-u",
                str(EXP_DIR / "reverse_engineer_drawdowns.py"),
                "--daily",
                str(out_dir / "daily_state.csv"),
                "--out-dir",
                str(out_dir),
            ],
            out_dir,
            "reverse_engineer_drawdowns",
        )
        _run(
            [
                py,
                "-u",
                str(EXP_DIR / "smooth_controller_sweep.py"),
                "--daily",
                str(out_dir / "daily_state.csv"),
                "--out-dir",
                str(out_dir),
                "--budget-per-branch",
                str(args.budget_per_branch),
                *common_version,
            ],
            out_dir,
            "smooth_controller_sweep",
        )
        _run(
            [
                py,
                "-u",
                str(EXP_DIR / "validate_smooth_controller_mc.py"),
                "--daily",
                str(out_dir / "daily_state.csv"),
                "--candidate-csv",
                str(out_dir / "smooth_controller_top_candidates.csv"),
                "--out-dir",
                str(out_dir),
                "--n",
                str(args.fail_n),
                "--windows",
                "2020-crash,2024",
                "--max-per-branch",
                "1",
                "--include-benchmark",
                "--output-prefix",
                "smooth_controller_failwindow_mc",
                *common_version,
            ],
            out_dir,
            "failwindow_mc",
        )

        survivors = _select_failwindow_survivors(
            out_dir / "smooth_controller_failwindow_mc_ranked.csv",
            args.survivor_limit,
        )
        (out_dir / "failwindow_survivors.json").write_text(
            json.dumps({"survivors": survivors}, indent=2),
            encoding="utf-8",
        )
        if survivors:
            _run(
                [
                    py,
                    "-u",
                    str(EXP_DIR / "validate_smooth_controller_mc.py"),
                    "--daily",
                    str(out_dir / "daily_state.csv"),
                    "--candidate-csv",
                    str(out_dir / "smooth_controller_top_candidates.csv"),
                    "--out-dir",
                    str(out_dir),
                    "--n",
                    str(args.long_n),
                    "--windows",
                    "2022,2025,22-now,5y",
                    "--policies",
                    ",".join(survivors),
                    "--output-prefix",
                    "smooth_controller_longwindow_mc",
                    *common_version,
                ],
                out_dir,
                "longwindow_mc",
                {"survivors": survivors},
            )
        else:
            print("No fail-window survivors; skipping broader MC.", flush=True)

        _write_summary(out_dir, survivors)
        done = {
            "phase": "done",
            "state": "done",
            "started_at": started_at,
            "finished_at": _now(),
            "survivors": survivors,
            "outputs": _outputs(out_dir),
        }
        _write_json(out_dir / "status.json", done)
        _write_json(out_dir / "done.json", done)
        return 0
    except Exception as exc:
        failed = {
            "phase": "failed",
            "state": "failed",
            "started_at": started_at,
            "finished_at": _now(),
            "error": str(exc),
            "outputs": _outputs(out_dir),
        }
        _write_json(out_dir / "status.json", failed)
        _write_json(out_dir / "failed.json", failed)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
