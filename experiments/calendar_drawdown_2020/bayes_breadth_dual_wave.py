"""Bayesian optimization for dual sector-breadth waves.

Extends the reverse breadth-thrust crash echo with an inverse/rebound wave:

* crash_echo dampens CALL scores after a sector breadth cliff.
* bull_wave releases the call dampener and pushes PUT scores toward neutral
  during broad positive breadth thrust / post-washout repair.

Read-only experiment; production scoring is untouched.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl
from skopt import Optimizer
from skopt.space import Real

import backtest_cascade as bc
from database.models.core import AlgorithmVersion
from experiments.calendar_drawdown_2020.analyze_and_sweep import month_key, month_return
from experiments.calendar_drawdown_2020.bayes_breadth_echo import (
    ALL_EVAL_MONTHS,
    COVID_MONTHS,
    OTHER_SHOCK_MONTHS,
    PRESERVE_MONTHS,
    REFERENCE_MONTHS,
    TARIFF_2025_MONTHS,
    baseline_row,
    compact_row as compact_echo_row,
    status_write,
    write_json,
)
from experiments.calendar_drawdown_2020.sweep_breadth_echo_score_modifier import clamp, write_csv


DIMENSIONS = [
    Real(0.10, 0.80, name="crash_k"),
    Real(52.0, 68.0, name="call_target"),
    Real(0.82, 0.98, name="crash_decay"),
    Real(0.15, 0.75, name="repair_k"),
    Real(10.0, 35.0, name="seed_level"),
    Real(20.0, 75.0, name="seed_velocity"),
    Real(25.0, 45.0, name="seed_rsi"),
    Real(35.0, 70.0, name="relief_brd_start"),
    Real(60.0, 100.0, name="relief_brd_full"),
    Real(20.0, 65.0, name="relief_avg5_start"),
    Real(50.0, 95.0, name="relief_avg5_full"),
    Real(0.70, 2.20, name="crash_power"),
    Real(0.00, 1.00, name="bull_release_k"),
    Real(0.00, 1.00, name="put_k"),
    Real(28.0, 45.0, name="put_target"),
    Real(0.82, 0.98, name="bull_decay"),
    Real(20.0, 70.0, name="bull_velocity"),
    Real(45.0, 85.0, name="bull_level"),
    Real(15.0, 45.0, name="bull_wash_level"),
    Real(0.70, 2.20, name="bull_power"),
]
PARAM_NAMES = [d.name for d in DIMENSIONS]


@dataclass(frozen=True)
class DualVariant:
    name: str
    crash_k: float
    call_target: float
    crash_decay: float
    repair_k: float
    seed_level: float
    seed_velocity: float
    seed_rsi: float
    relief_brd_start: float
    relief_brd_full: float
    relief_avg5_start: float
    relief_avg5_full: float
    crash_power: float
    bull_release_k: float
    put_k: float
    put_target: float
    bull_decay: float
    bull_velocity: float
    bull_level: float
    bull_wash_level: float
    bull_power: float


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class DualWaveSeries:
    def __init__(self, rows: list[dict], crash: dict[date, float], bull: dict[date, float]):
        self.rows = rows
        self.dates = [r["date"] for r in rows]
        self.by_date = {r["date"]: r for r in rows}
        self.crash = crash
        self.bull = bull

    @classmethod
    def load(cls, v: DualVariant) -> "DualWaveSeries":
        path = ROOT / ".cache" / "sector_etf_screen" / "sector_breadth_daily_2020plus.csv"
        df = pl.read_csv(path).with_columns(pl.col("date").cast(pl.Utf8))
        rows = []
        for r in df.iter_rows(named=True):
            rows.append({
                "date": date.fromisoformat(r["date"]),
                "brd": float(r["sec_brd_ema50"]) if r.get("sec_brd_ema50") is not None else None,
                "rsi": float(r["sec_avg_rsi"]) if r.get("sec_avg_rsi") is not None else None,
            })
        rows.sort(key=lambda r: r["date"])
        brds = [r["brd"] for r in rows]
        for i, r in enumerate(rows):
            b = r["brd"]
            r["d5"] = b - brds[i - 5] if i >= 5 and b is not None and brds[i - 5] is not None else 0.0
            r["d10"] = b - brds[i - 10] if i >= 10 and b is not None and brds[i - 10] is not None else 0.0
            r["avg5"] = sum(x for x in brds[max(0, i - 4): i + 1] if x is not None) / max(1, len([x for x in brds[max(0, i - 4): i + 1] if x is not None]))
            r["min20"] = min(x for x in brds[max(0, i - 19): i + 1] if x is not None)

        crash = {}
        bull = {}
        ce = 0.0
        be = 0.0
        for r in rows:
            cseed = crash_seed(r, v)
            relief = repair_relief(r, v)
            ce = max(cseed, ce * v.crash_decay * (1.0 - v.repair_k * relief))
            ce = clamp(ce)
            bseed = bull_seed(r, v)
            be = max(bseed, be * v.bull_decay)
            be = clamp(be)
            crash[r["date"]] = ce
            bull[r["date"]] = be
        return cls(rows, crash, bull)

    def values_on_or_before(self, d: date) -> tuple[float, float]:
        import bisect
        i = bisect.bisect_right(self.dates, d) - 1
        if i < 0:
            return 0.0, 0.0
        dd = self.dates[i]
        return self.crash.get(dd, 0.0), self.bull.get(dd, 0.0)


def crash_seed(r: dict, v: DualVariant) -> float:
    brd = float(r.get("brd") or 50.0)
    rsi = float(r.get("rsi") or 50.0)
    d5 = float(r.get("d5") or 0.0)
    d10 = float(r.get("d10") or 0.0)
    low = clamp((v.seed_level - brd) / max(1.0, v.seed_level))
    velocity = clamp((max(-d5, -d10 * 0.75) - v.seed_velocity) / 40.0)
    oversold = clamp((v.seed_rsi - rsi) / max(1.0, v.seed_rsi - 15.0))
    return max(low, velocity, oversold)


def repair_relief(r: dict, v: DualVariant) -> float:
    brd = float(r.get("brd") or 0.0)
    avg5 = float(r.get("avg5") or 0.0)
    br = clamp((brd - v.relief_brd_start) / max(1.0, v.relief_brd_full - v.relief_brd_start))
    ar = clamp((avg5 - v.relief_avg5_start) / max(1.0, v.relief_avg5_full - v.relief_avg5_start))
    return min(br, ar)


def bull_seed(r: dict, v: DualVariant) -> float:
    brd = float(r.get("brd") or 0.0)
    avg5 = float(r.get("avg5") or 0.0)
    d5 = float(r.get("d5") or 0.0)
    d10 = float(r.get("d10") or 0.0)
    min20 = float(r.get("min20") if r.get("min20") is not None else brd)
    thrust = clamp((max(d5, d10 * 0.75) - v.bull_velocity) / 45.0)
    level = clamp((max(brd, avg5) - v.bull_level) / max(1.0, 100.0 - v.bull_level))
    wash = clamp((v.bull_wash_level - min20) / max(1.0, v.bull_wash_level))
    return max(level * 0.65, thrust * max(0.35, wash))


def transform_outcomes(outcomes_by_date: dict, series: DualWaveSeries, v: DualVariant) -> tuple[dict, dict]:
    out = defaultdict(list)
    stats = {
        "call_adjusted": 0,
        "call_dropped": 0,
        "put_adjusted": 0,
        "put_dropped": 0,
        "max_crash_echo": 0.0,
        "max_bull_wave": 0.0,
    }
    for d, outs in outcomes_by_date.items():
        for o in outs:
            crash, bull = series.values_on_or_before(o.signal_date)
            stats["max_crash_echo"] = max(stats["max_crash_echo"], crash)
            stats["max_bull_wave"] = max(stats["max_bull_wave"], bull)
            new_score = float(o.score)
            if o.side == "call":
                effective_crash = crash * (1.0 - v.bull_release_k * (bull ** v.bull_power))
                if effective_crash > 0:
                    pressure = effective_crash ** v.crash_power
                    candidate = new_score - v.crash_k * pressure * max(0.0, new_score - v.call_target)
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
                if bull > 0 and v.put_k > 0:
                    pressure = bull ** v.bull_power
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

    def sort_key(o):
        side_order = 0 if o.side == "call" else 1
        ct_priority = 0 if (o.side == "call" and o.tier == bc.CT_CALL_TIER and o.score < 95) \
            or (o.side == "put" and o.tier == bc.CT_PUT_TIER and o.score > 15) else 1
        score_key = -o.score if o.side == "call" else o.score
        return side_order, ct_priority, score_key, o.symbol

    for dd in list(out):
        out[dd].sort(key=sort_key)
    return dict(out), stats


def run_variant(result: dict, v: DualVariant, months: list[tuple[int, int]], full: bool = False) -> dict:
    series = DualWaveSeries.load(v)
    transformed, stats = transform_outcomes(result["outcomes_by_date"], series, v)
    rows = [month_return(result, transformed, yr, mo) for yr, mo in months]
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


def enrich(row: dict, baseline: dict) -> dict:
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
    preserve_loss = max(0.0, -row["preserve_delta_vs_base"])
    ref_loss = max(0.0, -row["reference_delta_vs_base"])
    call_drop_penalty = max(0.0, float(row.get("call_dropped") or 0) - 5000) * 0.025
    put_drop_penalty = max(0.0, float(row.get("put_dropped") or 0) - 2400) * 0.035
    row["objective"] = round(
        min(row["covid_delta"], 600.0)
        + 1.65 * row["tariff_2025_delta"]
        + 0.30 * min(row["other_shock_delta"], 900.0)
        + 0.25 * row["reference_delta_vs_base"]
        - 1.80 * preserve_loss
        - 0.80 * ref_loss
        - call_drop_penalty
        - put_drop_penalty,
        4,
    )
    return row


def variant_from_x(x: list[float], name: str) -> DualVariant:
    vals = dict(zip(PARAM_NAMES, [float(v) for v in x]))
    if vals["relief_brd_full"] <= vals["relief_brd_start"] + 5:
        vals["relief_brd_full"] = vals["relief_brd_start"] + 5
    if vals["relief_avg5_full"] <= vals["relief_avg5_start"] + 5:
        vals["relief_avg5_full"] = vals["relief_avg5_start"] + 5
    vals["relief_brd_full"] = min(100.0, vals["relief_brd_full"])
    vals["relief_avg5_full"] = min(100.0, vals["relief_avg5_full"])
    return DualVariant(name=name, **vals)


def compact(row: dict) -> dict:
    keep = [
        "name", "objective", "covid_delta", "tariff_2025_delta", "other_shock_delta",
        "reference_delta_vs_base", "preserve_delta_vs_base", "full_max_dd_pct",
        "call_dropped", "put_dropped", "call_adjusted", "put_adjusted",
        "2020-03_ret", "2020-04_ret", "2025-03_ret", "2025-04_ret",
    ]
    out = {k: row.get(k) for k in keep if k in row}
    out["params"] = {k: row.get(k) for k in PARAM_NAMES}
    return out


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "calendar_drawdown_2020" / "runs" / f"breadth_dual_wave_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "launch.json", {"started_at": now(), "argv": sys.argv, "cwd": str(ROOT)})
    status_write(run_dir, {"phase": "baseline", "state": "running", "started_at": now()})

    av = AlgorithmVersion.get_active_scores_version()
    result = bc.run_cascade_backtest(av.id, from_date=date.fromisoformat(args.from_date), initial=50_000.0, verbose=True)
    baseline = baseline_row(result)
    write_json(run_dir / "baseline.json", baseline)

    optimizer = Optimizer(DIMENSIONS, base_estimator="ET", acq_func="EI", random_state=args.seed, n_initial_points=0)
    rng = random.Random(args.seed)
    seeds = [
        [0.338, 53.95, 0.900, 0.384, 12.65, 38.02, 43.8, 42.3, 99.3, 40.3, 58.4, 1.53, 0.45, 0.45, 38.0, 0.90, 35.0, 60.0, 35.0, 1.2],
        [0.40, 65.0, 0.92, 0.60, 20.0, 50.0, 35.0, 55.0, 80.0, 45.0, 80.0, 1.0, 0.60, 0.40, 40.0, 0.90, 35.0, 65.0, 40.0, 1.0],
    ]
    while len(seeds) < args.initial_random:
        seeds.append([rng.uniform(dim.low, dim.high) for dim in DIMENSIONS])

    evaluated = []
    total = len(seeds) + args.trials
    results_path = run_dir / "dual_wave_results.jsonl"
    with results_path.open("w", encoding="utf-8") as fh:
        for i, x in enumerate(seeds, 1):
            v = variant_from_x(x, f"seed_{i:03d}")
            row = enrich(run_variant(result, v, ALL_EVAL_MONTHS), baseline)
            evaluated.append(row)
            optimizer.tell(x, -row["objective"])
            fh.write(json.dumps(compact(row), default=str) + "\n"); fh.flush()
        for i in range(1, args.trials + 1):
            x = optimizer.ask()
            v = variant_from_x(x, f"bayes_{i:03d}")
            row = enrich(run_variant(result, v, ALL_EVAL_MONTHS), baseline)
            evaluated.append(row)
            optimizer.tell(x, -row["objective"])
            fh.write(json.dumps(compact(row), default=str) + "\n"); fh.flush()
            if i % args.status_every == 0 or i == args.trials:
                best = max(evaluated, key=lambda r: r["objective"])
                status_write(run_dir, {"phase": "optimize", "state": "running", "completed": len(evaluated), "total": total, "best_so_far": compact(best)})
                print(f"[opt] {len(evaluated)}/{total} best={best['name']} obj={best['objective']:.2f}", flush=True)

    ranked = sorted(evaluated, key=lambda r: r["objective"], reverse=True)
    write_csv(run_dir / "dual_wave_ranked_screen.csv", ranked)
    full_rows = []
    status_write(run_dir, {"phase": "full_replay", "state": "running", "completed": 0, "total": args.full_top})
    for i, row in enumerate(ranked[:args.full_top], 1):
        v = DualVariant(name=f"full_{i:03d}_{row['name']}", **{k: float(row[k]) for k in PARAM_NAMES})
        full = enrich(run_variant(result, v, ALL_EVAL_MONTHS, full=True), baseline)
        full_rows.append(full)
        write_csv(run_dir / "dual_wave_full.csv", full_rows)
        status_write(run_dir, {"phase": "full_replay", "state": "running", "completed": i, "total": args.full_top, "current": compact(full)})
        print(f"[full] {i}/{args.full_top} {row['name']} obj={full['objective']:.2f}", flush=True)

    full_ranked = sorted(full_rows, key=lambda r: (r["objective"], -float(r.get("full_max_dd_pct") or 999)), reverse=True)
    summary = {"finished_at": now(), "baseline": baseline, "top_screen": [compact(r) for r in ranked[:25]], "top_full": [compact(r) for r in full_ranked[:15]]}
    write_json(run_dir / "dual_wave_summary.json", summary)
    write_json(run_dir / "done.json", {"finished_at": now(), "summary": str(run_dir / "dual_wave_summary.json")})
    status_write(run_dir, {"phase": "done", "state": "done", "completed": total, "total": total, "best": compact(full_ranked[0])})
    print(f"[done] {run_dir}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--from", dest="from_date", default="2020-01-01")
    ap.add_argument("--trials", type=int, default=80)
    ap.add_argument("--initial-random", type=int, default=16)
    ap.add_argument("--full-top", type=int, default=16)
    ap.add_argument("--status-every", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20260512)
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
