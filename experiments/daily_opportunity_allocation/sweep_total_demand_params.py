"""Focused parameter sweep for the daily total-demand allocation family.

This second-stage probe takes the first-pass winner (`total_demand_wave`) and
tests whether the edge survives parameter movement, sector concentration guards,
put-pressure handling, and named stress windows.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backtest_cascade as bc
from database.models.core import AlgorithmVersion
from experiments.daily_opportunity_allocation.sweep_daily_policies import (
    DEFAULT_INITIAL,
    DEFAULT_START,
    DEFAULT_TEST_START,
    DEFAULT_TRAIN_END,
    Policy,
    _as_date,
    _bounds,
    _read_daily,
    run_policy,
)


EXP_DIR = Path(__file__).resolve().parent

WINDOWS = [
    ("train", DEFAULT_START, DEFAULT_TRAIN_END),
    ("test", DEFAULT_TEST_START, None),
    ("full", DEFAULT_START, None),
    ("stress_2020_crash", date(2020, 2, 18), date(2020, 4, 30)),
    ("stress_2022_bear", date(2022, 1, 3), date(2022, 10, 31)),
    ("recent_2024", date(2024, 1, 2), date(2024, 12, 31)),
]


def _candidate_policies(limit: int | None = None) -> list[Policy]:
    policies = [Policy("baseline")]
    floors = [0.84, 0.90, 0.96]
    ceils = [1.08, 1.14, 1.20]
    quantile_pairs = [("q20", "q80"), ("q40", "q80"), ("q40", "q60")]
    families = [
        ("both_total", "total", "total", False, 0.78, 0.70),
        ("sector_total", "total", "total", True, 0.84, 0.70),
        ("sector_hard_total", "total", "total", True, 0.72, 0.70),
        ("call_total_put_pressure", "total", "put_pressure", False, 0.78, 0.70),
        ("sector_call_total_put_pressure", "total", "put_pressure", True, 0.84, 0.70),
        ("mild_put_pressure", "total", "put_pressure", False, 0.78, 0.82),
        ("sector_mild_put_pressure", "total", "put_pressure", True, 0.84, 0.82),
    ]
    for floor in floors:
        for ceil in ceils:
            if ceil <= floor:
                continue
            for qlo, qhi in quantile_pairs:
                for family, call_mode, put_mode, sector_guard, sector_floor, put_floor in families:
                    name = f"{family}_f{floor:.2f}_c{ceil:.2f}_{qlo}_{qhi}"
                    policies.append(
                        Policy(
                            name=name,
                            call_mode=call_mode,
                            put_mode=put_mode,
                            sector_guard=sector_guard,
                            total_floor=floor,
                            total_ceil=ceil,
                            total_lo_quantile=qlo,
                            total_hi_quantile=qhi,
                            put_pressure_floor=put_floor,
                            put_pressure_ceil=1.05,
                            sector_guard_floor=sector_floor,
                        )
                    )
    if limit is not None:
        return policies[: max(1, limit)]
    return policies


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                cols.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _score_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_policy: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_policy.setdefault(str(row["policy"]), {})[str(row["window"])] = row
    baseline = by_policy.get("baseline", {})
    scored: list[dict[str, Any]] = []
    for policy, windows in by_policy.items():
        if policy == "baseline":
            continue
        test = windows.get("test")
        full = windows.get("full")
        train = windows.get("train")
        if not test or not full or not train:
            continue
        b_test = baseline.get("test", {})
        b_full = baseline.get("full", {})
        rec = {
            "policy": policy,
            "train_utility": train.get("dd_primary_utility"),
            "test_utility": test.get("dd_primary_utility"),
            "full_utility": full.get("dd_primary_utility"),
            "test_utility_delta": float(test["dd_primary_utility"]) - float(b_test.get("dd_primary_utility", 0.0)),
            "full_utility_delta": float(full["dd_primary_utility"]) - float(b_full.get("dd_primary_utility", 0.0)),
            "test_log_delta": float(test["log_return"]) - float(b_test.get("log_return", 0.0)),
            "test_dd_delta": float(test["max_dd"]) - float(b_test.get("max_dd", 0.0)),
            "full_dd_delta": float(full["max_dd"]) - float(b_full.get("max_dd", 0.0)),
            "test_trades": test.get("trades"),
            "full_trades": full.get("trades"),
        }
        stress = [w for name, w in windows.items() if name.startswith("stress_")]
        if stress:
            rec["worst_stress_dd"] = max(float(w["max_dd"]) for w in stress)
            rec["mean_stress_utility"] = sum(float(w["dd_primary_utility"]) for w in stress) / len(stress)
        scored.append(rec)
    scored.sort(
        key=lambda r: (
            float(r["test_utility_delta"]),
            -float(r["test_dd_delta"]),
            float(r["full_utility_delta"]),
            float(r.get("mean_stress_utility") or 0.0),
        ),
        reverse=True,
    )
    return scored


def _write_markdown(path: Path, rows: list[dict[str, Any]], scored: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    baseline_rows = [r for r in rows if r["policy"] == "baseline"]
    lines = [
        "# Focused Total-Demand Allocation Sweep",
        "",
        "Experiment-local Stage 3 validation probe. This is not a ship result.",
        "",
        "## Metadata",
        "",
        f"- version: v{meta['version_id']} {meta.get('commit')}",
        f"- generated_at: {meta['generated_at']}",
        f"- policies: {meta['policies']}",
        f"- daily_state: {meta['daily_state']}",
        "",
        "## Baseline Windows",
        "",
        "| window | utility | log_return | max_dd | trades |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in baseline_rows:
        lines.append(
            f"| {row['window']} | {float(row['dd_primary_utility']):.3f} | "
            f"{float(row['log_return']):.3f} | {float(row['max_dd']):.1%} | {int(row['trades'])} |"
        )
    lines.extend(
        [
            "",
            "## Top Candidates By Forward Utility Delta",
            "",
            "| policy | test dU | test dDD | full dU | worst stress DD | mean stress U |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in scored[:25]:
        lines.append(
            f"| `{row['policy']}` | {float(row['test_utility_delta']):+.3f} | "
            f"{float(row['test_dd_delta']):+.1%} | {float(row['full_utility_delta']):+.3f} | "
            f"{float(row.get('worst_stress_dd') or 0.0):.1%} | "
            f"{float(row.get('mean_stress_utility') or 0.0):+.3f} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- Prefer candidates that improve forward utility by reducing DD, not by only raising compound.",
            "- Reject candidates that win forward but materially worsen stress-window DD.",
            "- A candidate here still needs stochastic MC and production-equivalent portfolio validation.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", default=str(EXP_DIR / "daily_state.csv"))
    parser.add_argument("--out-dir", default=str(EXP_DIR))
    parser.add_argument("--version-id", type=int, default=None)
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=None)
    parser.add_argument("--initial", type=float, default=DEFAULT_INITIAL)
    parser.add_argument("--limit-policies", type=int, default=None)
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    status = out_dir / "focused_status.json"
    started_at = datetime.now(timezone.utc).isoformat()
    status.write_text(
        json.dumps({"phase": "focused_total_demand_sweep", "state": "running", "started_at": started_at}, indent=2),
        encoding="utf-8",
    )

    daily_path = Path(args.daily)
    daily = _read_daily(daily_path)
    bounds = _bounds(daily, DEFAULT_TRAIN_END)
    start = _as_date(args.start)
    end = _as_date(args.end) if args.end else None
    av = AlgorithmVersion.get_by_id(args.version_id) if args.version_id else AlgorithmVersion.get_active_scores_version()
    version_id = int(av.id)

    print(f"Loading cascade outcomes for v{version_id}...", flush=True)
    result = bc.run_cascade_backtest(
        version_id=version_id,
        min_score=70.0,
        from_date=start,
        to_date=end,
        initial=args.initial,
        verbose=True,
    )
    if not result:
        raise RuntimeError("No cascade result returned.")

    policies = _candidate_policies(args.limit_policies)
    rows: list[dict[str, Any]] = []
    windows = [(name, start_date, end if end_date is None else end_date) for name, start_date, end_date in WINDOWS]
    for i, policy in enumerate(policies, start=1):
        print(f"[policy {i}/{len(policies)}] {policy.name}", flush=True)
        for label, w_start, w_end in windows:
            rec = run_policy(
                policy,
                result["outcomes_by_date"],
                result["all_dates"],
                daily,
                bounds,
                result["regime_dates"],
                result["regime_map"],
                args.initial,
                w_start,
                w_end,
            )
            rec["window"] = label
            rows.append(rec)
        if i % 10 == 0 or i == len(policies):
            partial = _score_candidates(rows)
            _write_csv(out_dir / "focused_total_demand_sweep.csv", rows)
            _write_csv(out_dir / "focused_total_demand_ranked.csv", partial)
            status.write_text(
                json.dumps(
                    {
                        "phase": "focused_total_demand_sweep",
                        "state": "running",
                        "started_at": started_at,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "completed_policies": i,
                        "total_policies": len(policies),
                        "best_policy": partial[0]["policy"] if partial else None,
                        "best_test_utility_delta": partial[0]["test_utility_delta"] if partial else None,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

    scored = _score_candidates(rows)
    out_csv = out_dir / "focused_total_demand_sweep.csv"
    ranked_csv = out_dir / "focused_total_demand_ranked.csv"
    out_md = out_dir / "focused_total_demand_summary.md"
    _write_csv(out_csv, rows)
    _write_csv(ranked_csv, scored)
    meta = {
        "version_id": version_id,
        "commit": getattr(av, "git_commit", None),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policies": len(policies),
        "daily_state": str(daily_path),
    }
    _write_markdown(out_md, rows, scored, meta)
    done = {
        "phase": "done",
        "state": "done",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "outputs": {
            "sweep": str(out_csv),
            "ranked": str(ranked_csv),
            "summary": str(out_md),
        },
        "best_policy": scored[0]["policy"] if scored else None,
        "best_test_utility_delta": scored[0]["test_utility_delta"] if scored else None,
    }
    status.write_text(json.dumps(done, indent=2), encoding="utf-8")
    (out_dir / "done.json").write_text(json.dumps(done, indent=2), encoding="utf-8")
    print(f"Wrote {out_csv}", flush=True)
    print(f"Wrote {ranked_csv}", flush=True)
    print(f"Wrote {out_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
