"""DD-primary Bayesian optimization for dual sector-breadth waves.

This is an experiment-only runner. It reuses the dual-wave transform, but scores
each candidate by full replay max drawdown first instead of shock-window return.
Production scoring and portfolio code are untouched.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from skopt import Optimizer

import backtest_cascade as bc
from database.models.core import AlgorithmVersion
from experiments.calendar_drawdown_2020 import bayes_breadth_dual_wave as dual
from experiments.calendar_drawdown_2020.bayes_breadth_echo import (
    ALL_EVAL_MONTHS,
    baseline_row,
    status_write,
    write_json,
)
from experiments.calendar_drawdown_2020.sweep_breadth_echo_score_modifier import write_csv


def baseline_full_row(result: dict, baseline: dict) -> dict:
    full = bc.run_backtest(
        result["outcomes_by_date"],
        result["all_dates"],
        result["initial"],
        regime_dates=result["regime_dates"],
        regime_map=result["regime_map"],
        cfg={},
    )
    eq = full["equity_curve"][-1][1] if full["equity_curve"] else result["initial"]
    out = dict(baseline)
    out["full_return_pct"] = round((eq / result["initial"] - 1.0) * 100, 2)
    out["full_max_dd_pct"] = round(full["max_dd"] * 100, 2)
    out["full_trades"] = len(full["trade_log"])
    return out


def dd_objective(row: dict, baseline_full: dict, args: argparse.Namespace | None = None) -> float:
    args = args or argparse.Namespace()
    dd_gain = float(baseline_full["full_max_dd_pct"]) - float(row["full_max_dd_pct"])
    baseline_return = max(float(baseline_full.get("full_return_pct") or 1.0), 1.0)
    candidate_return = max(float(row.get("full_return_pct") or 1.0), 1.0)
    return_log_delta = math.log10(candidate_return) - math.log10(baseline_return)

    preserve_delta = float(row.get("preserve_delta_vs_base") or 0.0)
    reference_delta = float(row.get("reference_delta_vs_base") or 0.0)
    other_delta = float(row.get("other_shock_delta") or 0.0)
    covid_delta = float(row.get("covid_delta") or 0.0)
    tariff_delta = float(row.get("tariff_2025_delta") or 0.0)

    preserve_loss = max(0.0, -preserve_delta)
    reference_loss = max(0.0, -reference_delta)
    call_drop_penalty = max(0.0, float(row.get("call_dropped") or 0.0) - getattr(args, "call_drop_limit", 1800.0)) * getattr(args, "call_drop_weight", 0.04)
    put_drop_penalty = max(0.0, float(row.get("put_dropped") or 0.0) - getattr(args, "put_drop_limit", 2400.0)) * getattr(args, "put_drop_weight", 0.05)
    weak_tariff_penalty = max(0.0, getattr(args, "tariff_floor", 80.0) - tariff_delta) * getattr(args, "tariff_floor_weight", 0.60)
    covid_loss_penalty = max(0.0, -covid_delta) * getattr(args, "covid_loss_weight", 3.00)
    other_floor_penalty = max(0.0, getattr(args, "other_floor", -10000.0) - other_delta) * getattr(args, "other_floor_weight", 0.0)

    return round(
        getattr(args, "dd_weight", 42.0) * dd_gain
        + getattr(args, "return_log_weight", 18.0) * return_log_delta
        + getattr(args, "tariff_weight", 0.95) * min(tariff_delta, 180.0)
        + getattr(args, "covid_weight", 0.35) * min(covid_delta, 120.0)
        + getattr(args, "other_weight", 0.12) * other_delta
        + getattr(args, "preserve_weight", 0.32) * preserve_delta
        + getattr(args, "reference_weight", 0.45) * reference_delta
        - getattr(args, "preserve_loss_weight", 2.30) * preserve_loss
        - getattr(args, "reference_loss_weight", 1.70) * reference_loss
        - call_drop_penalty
        - put_drop_penalty
        - weak_tariff_penalty
        - covid_loss_penalty
        - other_floor_penalty,
        4,
    )


def score_row(result: dict, baseline: dict, baseline_full: dict, variant: dual.DualVariant, args: argparse.Namespace | None = None) -> dict:
    row = dual.enrich(dual.run_variant(result, variant, ALL_EVAL_MONTHS, full=True), baseline)
    row["shock_objective"] = row["objective"]
    row["objective"] = dd_objective(row, baseline_full, args)
    row["dd_delta_vs_base"] = round(float(baseline_full["full_max_dd_pct"]) - float(row["full_max_dd_pct"]), 2)
    return row


def row_to_x(row: dict) -> list[float] | None:
    try:
        return [float(row[name]) for name in dual.PARAM_NAMES]
    except (KeyError, TypeError, ValueError):
        return None


def add_seed(seeds: list[list[float]], seen: set[tuple[float, ...]], x: list[float] | None) -> None:
    if not x:
        return
    key = tuple(round(v, 8) for v in x)
    if key in seen:
        return
    seen.add(key)
    seeds.append(x)


def load_seed_rows(path: Path, limit: int) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_dd = sorted(rows, key=lambda r: float(r.get("full_max_dd_pct") or 999.0))
    by_obj = sorted(rows, key=lambda r: float(r.get("objective") or -999999.0), reverse=True)
    by_preserve = sorted(rows, key=lambda r: float(r.get("preserve_delta_vs_base") or -999999.0), reverse=True)
    selected = by_dd[:limit] + by_obj[: max(4, limit // 2)] + by_preserve[: max(4, limit // 2)]
    return selected


def compact(row: dict) -> dict:
    out = dual.compact(row)
    for key in [
        "shock_objective",
        "dd_delta_vs_base",
        "full_return_pct",
        "full_max_dd_pct",
        "full_trades",
        "covid_sum",
        "tariff_2025_sum",
        "other_shock_sum",
        "reference_sum",
        "preserve_sum",
    ]:
        if key in row:
            out[key] = row[key]
    return out


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "calendar_drawdown_2020" / "runs" / f"breadth_dual_wave_dd_{dual.datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "launch.json", {"started_at": dual.now(), "argv": sys.argv, "cwd": str(ROOT), "version_id": args.version_id})
    status_write(run_dir, {"phase": "baseline", "state": "running", "started_at": dual.now()})

    if args.version_id is not None:
        version_id = int(args.version_id)
    else:
        version_id = AlgorithmVersion.get_active_scores_version().id
    result = bc.run_cascade_backtest(version_id, from_date=date.fromisoformat(args.from_date), initial=50_000.0, verbose=True)
    baseline = baseline_row(result)
    baseline_full = baseline_full_row(result, baseline)
    write_json(run_dir / "baseline.json", baseline_full)

    optimizer = Optimizer(dual.DIMENSIONS, base_estimator="ET", acq_func="EI", random_state=args.seed, n_initial_points=0)
    rng = random.Random(args.seed)
    seeds: list[list[float]] = []
    seen: set[tuple[float, ...]] = set()

    default_seeds = [
        [0.338, 53.95, 0.900, 0.384, 12.65, 38.02, 43.8, 42.3, 99.3, 40.3, 58.4, 1.53, 0.45, 0.45, 38.0, 0.90, 35.0, 60.0, 35.0, 1.2],
        [0.40, 65.0, 0.92, 0.60, 20.0, 50.0, 35.0, 55.0, 80.0, 45.0, 80.0, 1.0, 0.60, 0.40, 40.0, 0.90, 35.0, 65.0, 40.0, 1.0],
    ]
    for x in default_seeds:
        add_seed(seeds, seen, x)
    if args.seed_csv:
        for row in load_seed_rows(Path(args.seed_csv), args.seed_candidates):
            add_seed(seeds, seen, row_to_x(row))
    while len(seeds) < args.initial_random:
        add_seed(seeds, seen, [rng.uniform(dim.low, dim.high) for dim in dual.DIMENSIONS])

    evaluated: list[dict] = []
    total = len(seeds) + args.trials
    results_path = run_dir / "dual_wave_dd_results.jsonl"
    with results_path.open("w", encoding="utf-8") as fh:
        for i, x in enumerate(seeds, 1):
            v = dual.variant_from_x(x, f"seed_{i:03d}")
            row = score_row(result, baseline, baseline_full, v, args)
            evaluated.append(row)
            optimizer.tell(x, -row["objective"])
            fh.write(json.dumps(compact(row), default=str) + "\n")
            fh.flush()
            if i % args.status_every == 0 or i == len(seeds):
                best = max(evaluated, key=lambda r: r["objective"])
                status_write(run_dir, {"phase": "seed", "state": "running", "completed": i, "total": total, "best_so_far": compact(best)})
                print(f"[seed] {i}/{total} best={best['name']} obj={best['objective']:.2f} dd={best['full_max_dd_pct']:.2f}", flush=True)

        for i in range(1, args.trials + 1):
            x = optimizer.ask()
            v = dual.variant_from_x(x, f"bayes_{i:03d}")
            row = score_row(result, baseline, baseline_full, v, args)
            evaluated.append(row)
            optimizer.tell(x, -row["objective"])
            fh.write(json.dumps(compact(row), default=str) + "\n")
            fh.flush()
            if i % args.status_every == 0 or i == args.trials:
                best = max(evaluated, key=lambda r: r["objective"])
                status_write(run_dir, {"phase": "optimize", "state": "running", "completed": len(evaluated), "total": total, "best_so_far": compact(best)})
                print(f"[opt] {len(evaluated)}/{total} best={best['name']} obj={best['objective']:.2f} dd={best['full_max_dd_pct']:.2f}", flush=True)

    ranked = sorted(evaluated, key=lambda r: (r["objective"], -float(r.get("full_max_dd_pct") or 999)), reverse=True)
    write_csv(run_dir / "dual_wave_dd_ranked.csv", ranked)
    summary = {
        "finished_at": dual.now(),
        "baseline": baseline_full,
        "top_full": [compact(r) for r in ranked[:25]],
        "lowest_dd": [compact(r) for r in sorted(evaluated, key=lambda r: float(r.get("full_max_dd_pct") or 999.0))[:15]],
    }
    write_json(run_dir / "dual_wave_dd_summary.json", summary)
    write_json(run_dir / "done.json", {"finished_at": dual.now(), "summary": str(run_dir / "dual_wave_dd_summary.json")})
    status_write(run_dir, {"phase": "done", "state": "done", "completed": total, "total": total, "best": compact(ranked[0])})
    print(f"[done] {run_dir}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--from", dest="from_date", default="2020-01-01")
    ap.add_argument("--trials", type=int, default=120)
    ap.add_argument("--initial-random", type=int, default=24)
    ap.add_argument("--seed-csv", default=None)
    ap.add_argument("--seed-candidates", type=int, default=12)
    ap.add_argument("--status-every", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20260511)
    ap.add_argument("--version-id", type=int, default=None)
    ap.add_argument("--dd-weight", type=float, default=42.0)
    ap.add_argument("--return-log-weight", type=float, default=18.0)
    ap.add_argument("--tariff-weight", type=float, default=0.95)
    ap.add_argument("--covid-weight", type=float, default=0.35)
    ap.add_argument("--other-weight", type=float, default=0.12)
    ap.add_argument("--preserve-weight", type=float, default=0.32)
    ap.add_argument("--reference-weight", type=float, default=0.45)
    ap.add_argument("--preserve-loss-weight", type=float, default=2.30)
    ap.add_argument("--reference-loss-weight", type=float, default=1.70)
    ap.add_argument("--call-drop-limit", type=float, default=1800.0)
    ap.add_argument("--call-drop-weight", type=float, default=0.04)
    ap.add_argument("--put-drop-limit", type=float, default=2400.0)
    ap.add_argument("--put-drop-weight", type=float, default=0.05)
    ap.add_argument("--tariff-floor", type=float, default=80.0)
    ap.add_argument("--tariff-floor-weight", type=float, default=0.60)
    ap.add_argument("--covid-loss-weight", type=float, default=3.00)
    ap.add_argument("--other-floor", type=float, default=-10000.0)
    ap.add_argument("--other-floor-weight", type=float, default=0.0)
    args = ap.parse_args()
    try:
        return run(args)
    except Exception as exc:
        run_dir = None
        for i, arg in enumerate(sys.argv):
            if arg == "--run-dir" and i + 1 < len(sys.argv):
                run_dir = Path(sys.argv[i + 1])
        if run_dir:
            write_json(run_dir / "failed.json", {"finished_at": dual.now(), "error": repr(exc)})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
