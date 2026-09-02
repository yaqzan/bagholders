"""Bayesian optimization for the sector ETF reverse-breadth-thrust echo.

This is experiment-local. It optimizes a CALL score post-processing echo against
multiple breadth-shock windows, including the 2025 tariff crash, while penalizing
damage to reference and preserve months.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from skopt import Optimizer
from skopt.space import Real

import backtest_cascade as bc
from database.models.core import AlgorithmVersion
from experiments.calendar_drawdown_2020.analyze_and_sweep import (
    PRESERVE_MONTHS,
    REFERENCE_MONTHS,
    TARGET_MONTHS,
    month_key,
    month_return,
)
from experiments.calendar_drawdown_2020.sweep_breadth_echo_score_modifier import (
    EchoSeries,
    EchoVariant,
    add_deltas,
    repair_summary,
    run_variant,
    write_csv,
    write_json,
)


DIMENSIONS = [
    Real(0.10, 0.90, name="k"),
    Real(52.0, 68.0, name="target"),
    Real(0.82, 0.98, name="decay"),
    Real(0.20, 0.75, name="repair_k"),
    Real(10.0, 35.0, name="seed_level"),
    Real(20.0, 75.0, name="seed_velocity"),
    Real(25.0, 45.0, name="seed_rsi"),
    Real(35.0, 70.0, name="relief_brd_start"),
    Real(60.0, 100.0, name="relief_brd_full"),
    Real(20.0, 65.0, name="relief_avg5_start"),
    Real(50.0, 95.0, name="relief_avg5_full"),
    Real(0.70, 2.20, name="power"),
]
PARAM_NAMES = [d.name for d in DIMENSIONS]

COVID_MONTHS = [(2020, 3), (2020, 4), (2020, 5), (2020, 6)]
TARIFF_2025_MONTHS = [(2025, 3), (2025, 4)]
OTHER_SHOCK_MONTHS = [
    (2020, 9), (2020, 10),
    (2021, 9), (2021, 11),
    (2022, 1), (2022, 5), (2022, 8), (2022, 9),
    (2023, 3), (2023, 8), (2023, 9),
    (2024, 4), (2024, 8), (2024, 12),
    (2025, 1), (2025, 10), (2025, 11),
]
ALL_EVAL_MONTHS = sorted(set(COVID_MONTHS + TARIFF_2025_MONTHS + OTHER_SHOCK_MONTHS + REFERENCE_MONTHS + PRESERVE_MONTHS))


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def status_write(run_dir: Path, payload: dict[str, Any]) -> None:
    base = {
        "updated_at": now(),
        "output_paths": {
            "run_dir": str(run_dir),
            "results": str(run_dir / "bayes_results.jsonl"),
            "summary": str(run_dir / "bayes_summary.json"),
            "done": str(run_dir / "done.json"),
            "failed": str(run_dir / "failed.json"),
        },
    }
    base.update(payload)
    write_json(run_dir / "status.json", base)


def variant_from_x(x: list[float], name: str) -> EchoVariant:
    vals = dict(zip(PARAM_NAMES, [float(v) for v in x]))
    if vals["relief_brd_full"] <= vals["relief_brd_start"] + 5.0:
        vals["relief_brd_full"] = vals["relief_brd_start"] + 5.0
    if vals["relief_avg5_full"] <= vals["relief_avg5_start"] + 5.0:
        vals["relief_avg5_full"] = vals["relief_avg5_start"] + 5.0
    vals["relief_brd_full"] = min(100.0, vals["relief_brd_full"])
    vals["relief_avg5_full"] = min(100.0, vals["relief_avg5_full"])
    return EchoVariant(name=name, **vals)


def seed_variants() -> list[EchoVariant]:
    seeds = []
    for k, target, decay, repair_k, rbs, ras in [
        (0.70, 60.0, 0.96, 0.45, 50.0, 35.0),
        (0.55, 60.0, 0.94, 0.55, 50.0, 35.0),
        (0.40, 65.0, 0.92, 0.60, 55.0, 45.0),
        (0.35, 62.0, 0.90, 0.70, 60.0, 50.0),
    ]:
        seeds.append(EchoVariant(
            name=f"seed_K{int(k*100)}_T{int(target)}_D{int(decay*100)}",
            k=k,
            target=target,
            decay=decay,
            repair_k=repair_k,
            seed_level=20.0,
            seed_velocity=50.0,
            seed_rsi=35.0,
            relief_brd_start=rbs,
            relief_brd_full=min(100.0, rbs + 25.0),
            relief_avg5_start=ras,
            relief_avg5_full=min(100.0, ras + 35.0),
            power=1.0,
        ))
    return seeds


def x_from_variant(v: EchoVariant) -> list[float]:
    d = v.as_dict()
    return [float(d[name]) for name in PARAM_NAMES]


def baseline_row(result: dict) -> dict:
    row = {"name": "baseline"}
    for yr, mo in ALL_EVAL_MONTHS:
        mr = month_return(result, result["outcomes_by_date"], yr, mo)
        row[f"{mr['month']}_ret"] = mr["return_pct"]
        row[f"{mr['month']}_trades"] = mr["n_trades"]
    row["covid_sum"] = round(sum(row.get(f"{month_key(*m)}_ret") or 0 for m in COVID_MONTHS), 2)
    row["tariff_2025_sum"] = round(sum(row.get(f"{month_key(*m)}_ret") or 0 for m in TARIFF_2025_MONTHS), 2)
    row["other_shock_sum"] = round(sum(row.get(f"{month_key(*m)}_ret") or 0 for m in OTHER_SHOCK_MONTHS), 2)
    row["reference_sum"] = round(sum(row.get(f"{month_key(*m)}_ret") or 0 for m in REFERENCE_MONTHS), 2)
    row["preserve_sum"] = round(sum(row.get(f"{month_key(*m)}_ret") or 0 for m in PRESERVE_MONTHS), 2)
    row["target_sum"] = row["covid_sum"]
    return row


def enrich_row(row: dict, baseline: dict) -> dict:
    row["covid_sum"] = round(sum(row.get(f"{month_key(*m)}_ret") or 0 for m in COVID_MONTHS), 2)
    row["tariff_2025_sum"] = round(sum(row.get(f"{month_key(*m)}_ret") or 0 for m in TARIFF_2025_MONTHS), 2)
    row["other_shock_sum"] = round(sum(row.get(f"{month_key(*m)}_ret") or 0 for m in OTHER_SHOCK_MONTHS), 2)
    row["reference_sum"] = round(sum(row.get(f"{month_key(*m)}_ret") or 0 for m in REFERENCE_MONTHS), 2)
    row["preserve_sum"] = round(sum(row.get(f"{month_key(*m)}_ret") or 0 for m in PRESERVE_MONTHS), 2)
    row["covid_delta"] = round(row["covid_sum"] - baseline["covid_sum"], 2)
    row["tariff_2025_delta"] = round(row["tariff_2025_sum"] - baseline["tariff_2025_sum"], 2)
    row["other_shock_delta"] = round(row["other_shock_sum"] - baseline["other_shock_sum"], 2)
    row["reference_delta_vs_base"] = round(row["reference_sum"] - baseline["reference_sum"], 2)
    row["preserve_delta_vs_base"] = round(row["preserve_sum"] - baseline["preserve_sum"], 2)
    row["target_delta_vs_base"] = row["covid_delta"]

    preserve_loss = max(0.0, -row["preserve_delta_vs_base"])
    reference_loss = max(0.0, -row["reference_delta_vs_base"])
    broad_drop_penalty = max(0.0, float(row.get("call_dropped") or 0) - 5500) * 0.025
    row["objective"] = round(
        min(row["covid_delta"], 550.0)
        + 1.75 * row["tariff_2025_delta"]
        + 0.35 * min(row["other_shock_delta"], 900.0)
        + 0.35 * row["reference_delta_vs_base"]
        - 1.70 * preserve_loss
        - 0.80 * reference_loss
        - broad_drop_penalty,
        4,
    )
    return row


def evaluate(result: dict, baseline: dict, v: EchoVariant, full: bool = False) -> dict:
    series = EchoSeries.load([v])
    row = run_variant(result, series, v, ALL_EVAL_MONTHS, full=full)
    return enrich_row(row, baseline)


def compact_row(row: dict) -> dict:
    keep = [
        "name", "objective", "covid_delta", "tariff_2025_delta", "other_shock_delta",
        "reference_delta_vs_base", "preserve_delta_vs_base", "full_max_dd_pct",
        "call_dropped", "call_adjusted", "2020-03_ret", "2020-04_ret",
        "2025-03_ret", "2025-04_ret",
    ]
    out = {k: row.get(k) for k in keep if k in row}
    out["params"] = {k: row.get(k) for k in PARAM_NAMES}
    return out


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "calendar_drawdown_2020" / "runs" / f"breadth_echo_bayes_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "launch.json", {
        "started_at": now(),
        "cwd": str(ROOT),
        "argv": sys.argv,
        "git_commit": os.popen("git rev-parse HEAD").read().strip(),
        "windows": {
            "covid": COVID_MONTHS,
            "tariff_2025": TARIFF_2025_MONTHS,
            "other_shock": OTHER_SHOCK_MONTHS,
            "reference": REFERENCE_MONTHS,
            "preserve": PRESERVE_MONTHS,
        },
    })
    status_write(run_dir, {"phase": "baseline", "state": "running", "started_at": now()})

    av = AlgorithmVersion.get_active_scores_version()
    from_date = date.fromisoformat(args.from_date)
    print(f"[baseline] active v{av.id}, from {from_date}", flush=True)
    result = bc.run_cascade_backtest(av.id, from_date=from_date, initial=50_000.0, verbose=True)
    if not result:
        raise RuntimeError("No baseline result")
    baseline = baseline_row(result)
    write_json(run_dir / "baseline.json", baseline)

    optimizer = Optimizer(DIMENSIONS, base_estimator="ET", acq_func="EI", random_state=args.seed, n_initial_points=0)
    rng = random.Random(args.seed)
    evaluated: list[dict] = []
    results_path = run_dir / "bayes_results.jsonl"

    seeds = seed_variants()
    seed_xs = [x_from_variant(v) for v in seeds]
    while len(seed_xs) < args.initial_random:
        seed_xs.append([rng.uniform(dim.low, dim.high) for dim in DIMENSIONS])

    total = len(seed_xs) + args.trials
    with results_path.open("w", encoding="utf-8") as fh:
        for i, x in enumerate(seed_xs, 1):
            v = variant_from_x(x, f"seed_{i:03d}")
            row = evaluate(result, baseline, v, full=False)
            row["source"] = "seed"
            evaluated.append(row)
            optimizer.tell(x, -row["objective"])
            fh.write(json.dumps(compact_row(row), default=str) + "\n")
            fh.flush()

        status_write(run_dir, {"phase": "optimize", "state": "running", "completed": len(evaluated), "total": total})
        for i in range(1, args.trials + 1):
            x = optimizer.ask()
            v = variant_from_x(x, f"bayes_{i:03d}")
            row = evaluate(result, baseline, v, full=False)
            row["source"] = "bayes"
            evaluated.append(row)
            optimizer.tell(x, -row["objective"])
            fh.write(json.dumps(compact_row(row), default=str) + "\n")
            fh.flush()
            if i % args.status_every == 0 or i == args.trials:
                best = max(evaluated, key=lambda r: r["objective"])
                status_write(run_dir, {
                    "phase": "optimize",
                    "state": "running",
                    "completed": len(evaluated),
                    "total": total,
                    "best_so_far": compact_row(best),
                })
                print(f"[opt] {len(evaluated)}/{total} best={best['name']} obj={best['objective']:.2f}", flush=True)

    ranked = sorted(evaluated, key=lambda r: r["objective"], reverse=True)
    write_csv(run_dir / "bayes_ranked_screen.csv", ranked)

    full_rows = []
    print(f"[full] replaying top {args.full_top}", flush=True)
    status_write(run_dir, {"phase": "full_replay", "state": "running", "completed": 0, "total": args.full_top})
    for i, row in enumerate(ranked[:args.full_top], 1):
        v = EchoVariant(
            name=f"full_{i:03d}_{row['name']}",
            **{name: float(row[name]) for name in PARAM_NAMES},
        )
        full_row = evaluate(result, baseline, v, full=True)
        full_rows.append(full_row)
        write_csv(run_dir / "bayes_full.csv", full_rows)
        status_write(run_dir, {
            "phase": "full_replay",
            "state": "running",
            "completed": i,
            "total": args.full_top,
            "current": compact_row(full_row),
        })
        print(f"[full] {i}/{args.full_top} {row['name']} obj={full_row['objective']:.2f}", flush=True)

    full_ranked = sorted(full_rows, key=lambda r: (r["objective"], -float(r.get("full_max_dd_pct") or 999)), reverse=True)
    best = full_ranked[0]
    best_variant = EchoVariant(name=best["name"], **{name: float(best[name]) for name in PARAM_NAMES})
    repair = repair_summary(EchoSeries.load([best_variant]), best_variant)
    write_csv(run_dir / "best_echo_repair_path.csv", repair)

    summary = {
        "finished_at": now(),
        "active_version": av.id,
        "baseline": baseline,
        "top_screen": [compact_row(r) for r in ranked[:25]],
        "top_full": [compact_row(r) for r in full_ranked[:15]],
        "best_repair_path": repair,
    }
    write_json(run_dir / "bayes_summary.json", summary)
    write_json(run_dir / "done.json", {"finished_at": now(), "summary": str(run_dir / "bayes_summary.json")})
    status_write(run_dir, {"phase": "done", "state": "done", "completed": total, "total": total, "best": compact_row(best)})
    print(f"[done] {run_dir}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--from", dest="from_date", default="2020-01-01")
    ap.add_argument("--trials", type=int, default=120)
    ap.add_argument("--initial-random", type=int, default=24)
    ap.add_argument("--full-top", type=int, default=24)
    ap.add_argument("--status-every", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20260511)
    args = ap.parse_args()
    try:
        return run(args)
    except Exception as exc:
        run_dir = None
        for i, arg in enumerate(sys.argv):
            if arg == "--run-dir" and i + 1 < len(sys.argv):
                run_dir = Path(sys.argv[i + 1])
        if run_dir:
            write_json(run_dir / "failed.json", {"finished_at": now(), "error": repr(exc)})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
