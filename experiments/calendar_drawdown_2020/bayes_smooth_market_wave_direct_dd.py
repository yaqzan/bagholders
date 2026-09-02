"""DD-primary Bayesian optimization for direct smooth Market Wave score impact.

Experiment-only. This tests a one-score Market Wave transform:

* low wave levels smoothly damp CALL scores;
* high wave levels smoothly neutralize PUT scores;
* no separate crash/repair echo state is derived from the wave.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from skopt import Optimizer
from skopt.space import Real

import backtest_cascade as bc
from database.models.core import AlgorithmVersion
from experiments.calendar_drawdown_2020.analyze_and_sweep import month_return
from experiments.calendar_drawdown_2020.bayes_breadth_dual_wave import compact as compact_dual
from experiments.calendar_drawdown_2020.bayes_breadth_dual_wave_dd import baseline_full_row, dd_objective
from experiments.calendar_drawdown_2020.bayes_breadth_echo import ALL_EVAL_MONTHS, baseline_row, now, status_write, write_json
from experiments.calendar_drawdown_2020.sweep_breadth_echo_score_modifier import clamp, write_csv


DIMENSIONS = [
    Real(0.05, 0.80, name="call_k"),
    Real(52.0, 68.0, name="call_target"),
    Real(35.0, 65.0, name="stress_start"),
    Real(5.0, 35.0, name="stress_full"),
    Real(0.70, 2.40, name="stress_power"),
    Real(0.00, 1.00, name="put_k"),
    Real(28.0, 45.0, name="put_target"),
    Real(45.0, 70.0, name="repair_start"),
    Real(60.0, 95.0, name="repair_full"),
    Real(0.70, 2.40, name="repair_power"),
]
PARAM_NAMES = [d.name for d in DIMENSIONS]


@dataclass(frozen=True)
class DirectVariant:
    name: str
    call_k: float
    call_target: float
    stress_start: float
    stress_full: float
    stress_power: float
    put_k: float
    put_target: float
    repair_start: float
    repair_full: float
    repair_power: float


class DirectWaveSeries:
    def __init__(self, values: dict[date, float]):
        self.values = values
        self.dates = sorted(values)

    @classmethod
    def load(cls, path: Path, score_col: str) -> "DirectWaveSeries":
        values = {}
        with path.open("r", encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                raw = r.get(score_col)
                if raw in (None, ""):
                    continue
                values[date.fromisoformat(str(r["date"])[:10])] = float(raw)
        return cls(values)

    def value_on_or_before(self, d: date) -> float:
        import bisect

        i = bisect.bisect_right(self.dates, d) - 1
        if i < 0:
            return 50.0
        return self.values[self.dates[i]]


def variant_from_x(x: list[float], name: str) -> DirectVariant:
    vals = dict(zip(PARAM_NAMES, [float(v) for v in x]))
    if vals["stress_full"] >= vals["stress_start"] - 5.0:
        vals["stress_full"] = vals["stress_start"] - 5.0
    vals["stress_full"] = max(0.0, vals["stress_full"])
    if vals["repair_full"] <= vals["repair_start"] + 5.0:
        vals["repair_full"] = vals["repair_start"] + 5.0
    vals["repair_full"] = min(100.0, vals["repair_full"])
    return DirectVariant(name=name, **vals)


def transform_outcomes(outcomes_by_date: dict, series: DirectWaveSeries, v: DirectVariant) -> tuple[dict, dict]:
    out = defaultdict(list)
    stats = {
        "call_adjusted": 0,
        "call_dropped": 0,
        "put_adjusted": 0,
        "put_dropped": 0,
        "max_stress": 0.0,
        "max_repair": 0.0,
    }
    for d, outs in outcomes_by_date.items():
        for o in outs:
            wave = series.value_on_or_before(o.signal_date)
            stress = clamp((v.stress_start - wave) / max(1.0, v.stress_start - v.stress_full))
            repair = clamp((wave - v.repair_start) / max(1.0, v.repair_full - v.repair_start))
            stats["max_stress"] = max(stats["max_stress"], stress)
            stats["max_repair"] = max(stats["max_repair"], repair)
            new_score = float(o.score)
            if o.side == "call":
                if stress > 0 and v.call_k > 0:
                    pressure = stress ** v.stress_power
                    candidate = new_score - v.call_k * pressure * max(0.0, new_score - v.call_target)
                    candidate = round(max(0.0, min(100.0, candidate)))
                    if candidate < new_score:
                        stats["call_adjusted"] += 1
                    new_score = candidate
                if new_score < 70:
                    stats["call_dropped"] += 1
                    continue
                tier = o.tier if o.tier == bc.CT_CALL_TIER else bc.score_to_tier(new_score)
                out[d].append(replace(o, score=new_score, tier=tier))
            elif o.side == "put":
                if repair > 0 and v.put_k > 0:
                    pressure = repair ** v.repair_power
                    candidate = new_score + v.put_k * pressure * max(0.0, v.put_target - new_score)
                    candidate = round(max(0.0, min(100.0, candidate)))
                    if candidate > new_score:
                        stats["put_adjusted"] += 1
                    new_score = candidate
                if new_score > 25:
                    stats["put_dropped"] += 1
                    continue
                tier = o.tier if o.tier == bc.CT_PUT_TIER else bc.put_score_to_tier(new_score)
                out[d].append(replace(o, score=new_score, tier=tier))
            else:
                out[d].append(o)

    for dd in list(out):
        out[dd].sort(key=lambda o: (0 if o.side == "call" else 1, -o.score if o.side == "call" else o.score, o.symbol))
    return dict(out), stats


def run_variant(result: dict, series: DirectWaveSeries, v: DirectVariant, full: bool = True) -> dict:
    transformed, stats = transform_outcomes(result["outcomes_by_date"], series, v)
    rows = [month_return(result, transformed, yr, mo) for yr, mo in ALL_EVAL_MONTHS]
    row = {"name": v.name, **v.__dict__, **stats}
    for r in rows:
        row[f"{r['month']}_ret"] = r["return_pct"]
        row[f"{r['month']}_trades"] = r["n_trades"]
    if full:
        full_result = bc.run_backtest(
            transformed,
            result["all_dates"],
            result["initial"],
            regime_dates=result["regime_dates"],
            regime_map=result["regime_map"],
            cfg={},
        )
        eq = full_result["equity_curve"][-1][1] if full_result["equity_curve"] else result["initial"]
        row["full_return_pct"] = round((eq / result["initial"] - 1.0) * 100, 2)
        row["full_max_dd_pct"] = round(full_result["max_dd"] * 100, 2)
        row["full_trades"] = len(full_result["trade_log"])
    return row


def compact(row: dict) -> dict:
    out = compact_dual(row)
    out["params"] = {k: row.get(k) for k in PARAM_NAMES}
    for key in ["dd_delta_vs_base", "full_return_pct", "full_trades", "max_stress", "max_repair"]:
        if key in row:
            out[key] = row[key]
    return out


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "calendar_drawdown_2020" / "runs" / f"smooth_wave_direct_dd_{now().replace(':', '')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "launch.json", {"started_at": now(), "argv": sys.argv, "cwd": str(ROOT), "wave_history_csv": args.wave_history_csv})
    status_write(run_dir, {"phase": "baseline", "state": "running", "started_at": now()})

    version_id = int(args.version_id) if args.version_id is not None else AlgorithmVersion.get_active_scores_version().id
    result = bc.run_cascade_backtest(version_id, from_date=date.fromisoformat(args.from_date), initial=50_000.0, verbose=True)
    baseline = baseline_row(result)
    baseline_full = baseline_full_row(result, baseline)
    write_json(run_dir / "baseline.json", baseline_full)
    series = DirectWaveSeries.load(Path(args.wave_history_csv), args.wave_score_col)

    optimizer = Optimizer(DIMENSIONS, base_estimator="ET", acq_func="EI", random_state=args.seed, n_initial_points=0)
    rng = random.Random(args.seed)
    seeds = [
        [0.25, 62.0, 45.0, 15.0, 1.20, 0.35, 38.0, 60.0, 80.0, 1.20],
        [0.45, 66.0, 50.0, 20.0, 1.40, 0.20, 35.0, 62.0, 85.0, 1.00],
        [0.15, 60.0, 40.0, 10.0, 0.90, 0.55, 40.0, 55.0, 75.0, 1.60],
    ]
    while len(seeds) < args.initial_random:
        seeds.append([rng.uniform(dim.low, dim.high) for dim in DIMENSIONS])

    evaluated = []
    total = len(seeds) + args.trials
    results_path = run_dir / "smooth_wave_direct_results.jsonl"
    with results_path.open("w", encoding="utf-8") as fh:
        for i, x in enumerate(seeds, 1):
            v = variant_from_x(x, f"seed_{i:03d}")
            row = run_variant(result, series, v, full=True)
            row = dd_objective_row(row, baseline, baseline_full, args)
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
            v = variant_from_x(x, f"bayes_{i:03d}")
            row = run_variant(result, series, v, full=True)
            row = dd_objective_row(row, baseline, baseline_full, args)
            evaluated.append(row)
            optimizer.tell(x, -row["objective"])
            fh.write(json.dumps(compact(row), default=str) + "\n")
            fh.flush()
            if i % args.status_every == 0 or i == args.trials:
                best = max(evaluated, key=lambda r: r["objective"])
                status_write(run_dir, {"phase": "optimize", "state": "running", "completed": len(evaluated), "total": total, "best_so_far": compact(best)})
                print(f"[opt] {len(evaluated)}/{total} best={best['name']} obj={best['objective']:.2f} dd={best['full_max_dd_pct']:.2f}", flush=True)

    ranked = sorted(evaluated, key=lambda r: (r["objective"], -float(r.get("full_max_dd_pct") or 999)), reverse=True)
    write_csv(run_dir / "smooth_wave_direct_ranked.csv", ranked)
    summary = {
        "finished_at": now(),
        "baseline": baseline_full,
        "top_full": [compact(r) for r in ranked[:25]],
        "lowest_dd": [compact(r) for r in sorted(evaluated, key=lambda r: float(r.get("full_max_dd_pct") or 999.0))[:15]],
    }
    write_json(run_dir / "smooth_wave_direct_summary.json", summary)
    write_json(run_dir / "done.json", {"finished_at": now(), "summary": str(run_dir / "smooth_wave_direct_summary.json")})
    status_write(run_dir, {"phase": "done", "state": "done", "completed": total, "total": total, "best": compact(ranked[0])})
    return 0


def dd_objective_row(row: dict, baseline: dict, baseline_full: dict, args: argparse.Namespace) -> dict:
    from experiments.calendar_drawdown_2020 import bayes_breadth_dual_wave as dual

    row = dual.enrich(row, baseline)
    row["shock_objective"] = row["objective"]
    row["objective"] = dd_objective(row, baseline_full, args)
    row["dd_delta_vs_base"] = round(float(baseline_full["full_max_dd_pct"]) - float(row["full_max_dd_pct"]), 2)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wave-history-csv", required=True)
    ap.add_argument("--wave-score-col", default="score")
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--from", dest="from_date", default="2020-01-01")
    ap.add_argument("--trials", type=int, default=80)
    ap.add_argument("--initial-random", type=int, default=16)
    ap.add_argument("--status-every", type=int, default=4)
    ap.add_argument("--seed", type=int, default=20260512)
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
        if args.run_dir:
            write_json(Path(args.run_dir) / "failed.json", {"finished_at": now(), "error": repr(exc)})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
