"""Bayesian-style refinement for Participation Wave Stage 1 candidates.

Uses skopt's model-guided optimizer seeded from the best prior sweeps. The
objective is intentionally gate-oriented: improve affected-cohort WR7 z while
preserving available WR7/WR15/WR30 consistency and tier-aware N behavior.

This remains experiment-local and does not touch production scoring.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polars as pl
from skopt import Optimizer
from skopt.space import Real

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter
from experiments.mcd_cwwd_wvd_recal import stage1_gate_check as gate
from experiments.mcd_cwwd_wvd_recal.participation_wave import (
    CONTEXT,
    WaveParams,
    _eval_rows,
    _row_dicts,
    apply_wave,
)


DIMENSIONS = [
    Real(0.20, 1.60, name="k"),
    Real(1.0, 8.0, name="cap"),
    Real(-0.30, 1.10, name="dv_mid"),
    Real(1.0, 4.5, name="dv_slope"),
    Real(0.0, 1.50, name="impulse_mid"),
    Real(0.8, 3.0, name="impulse_slope"),
    Real(-3.0, 12.0, name="vel_mid"),
    Real(0.15, 0.65, name="vel_slope"),
    Real(-2.0, 12.0, name="wadj_mid"),
    Real(0.20, 0.80, name="wadj_slope"),
    Real(-0.10, 0.80, name="force_peak"),
    Real(0.15, 1.00, name="force_width"),
    Real(0.0, 3.50, name="pressure_floor"),
]
PARAM_NAMES = [d.name for d in DIMENSIONS]
POET_DATES = {"2025-10-03", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def _params_from_x(x: list[float]) -> WaveParams:
    values = dict(zip(PARAM_NAMES, [float(v) for v in x]))
    values["cap"] = max(1.0, values["cap"])
    values["force_width"] = max(0.05, values["force_width"])
    return WaveParams(**values)


def _x_from_params(params: dict[str, Any]) -> list[float]:
    return [float(params[name]) for name in PARAM_NAMES]


def _rate_stats(df: pl.DataFrame, pred: pl.Expr, col: str) -> dict[str, Any]:
    aff = df.filter(pred & pl.col(col).is_not_null())
    rest = df.filter((~pred) & pl.col(col).is_not_null())
    wr_aff, n_aff = gate._wr(aff, col)
    wr_rest, n_rest = gate._wr(rest, col)
    return {
        "affected_wr": wr_aff,
        "affected_n": n_aff,
        "rest_wr": wr_rest,
        "rest_n": n_rest,
        "lift_pp": None if wr_aff is None or wr_rest is None else wr_aff - wr_rest,
        "z": None if wr_aff is None or wr_rest is None else gate._z_score(wr_aff, n_aff, wr_rest, n_rest),
    }


def _poet_rows(df: pl.DataFrame) -> list[dict[str, Any]]:
    rows = (
        df.filter((pl.col("symbol") == "POET") & pl.col("date").is_in(sorted(POET_DATES)))
        .select(["symbol", "date", "overall", "new_score", "wave_boost", "win_7d", "win_15d", "win_30d", "opt_result_15"])
        .sort("date")
        .to_dicts()
    )
    for row in rows:
        row["wave_boost"] = round(float(row["wave_boost"] or 0.0), 4)
    return rows


def _fast_eval(df: pl.DataFrame, params: WaveParams, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    varied = apply_wave(df, params)
    affected = pl.col("new_score") > pl.col("overall")
    metrics = {f"WR{w}": _rate_stats(varied, affected, f"win_{w}d") for w in (7, 15, 30)}
    period = {}
    for label, (start, stop) in gate.PERIODS.items():
        sub = varied.filter((pl.col("date") >= start) & (pl.col("date") <= stop))
        period[label] = _rate_stats(sub, pl.col("new_score") > pl.col("overall"), "win_7d")

    n_changed = varied.filter(affected).height
    n75_delta = (
        varied.filter((pl.col("new_score") >= 75) & (pl.col("overall") < 75)).height
        - varied.filter((pl.col("new_score") < 75) & (pl.col("overall") >= 75)).height
    )
    n80_delta = (
        varied.filter((pl.col("new_score") >= 80) & (pl.col("overall") < 80)).height
        - varied.filter((pl.col("new_score") < 80) & (pl.col("overall") >= 80)).height
    )
    n70_74_to_binding = varied.filter((pl.col("overall").is_between(70, 74)) & (pl.col("new_score") >= 75)).height
    poet = _poet_rows(varied)
    poet_75 = sum(1 for row in poet if int(row["new_score"]) >= 75 and int(row["overall"]) < 75)
    gate_diag: dict[str, Any] = {}
    gate_penalty = 0.0
    gate_bonus = 0.0
    if baseline is not None:
        candidate = _eval_rows(_row_dicts(varied))
        w4 = gate._discrete_bucket_gate(baseline, candidate)
        w6 = gate._gradient_gate(baseline, candidate)
        w4_penalty = sum(abs(float(b["delta_wr7_pp"])) for b in w4["breaches"]) * 7.0
        w6_penalty = sum(
            max(0.0, float(b["severity_pp"]) - float(b.get("baseline_severity_pp", 0.0)) - 0.05)
            for b in w6["worsened_breaches"]
        ) * 45.0

        bucket_rows = {r["bucket"]: r for r in w4["rows"]}
        n_shift_bonus = 0.0
        for bucket, weight in [("75-79", 0.025), ("80-84", 0.045), ("85-89", 0.055), ("90-94", 0.070)]:
            row = bucket_rows.get(bucket)
            if not row:
                continue
            n_delta = int(row["new"]["n"]) - int(row["base"]["n"])
            d_wr7 = row["delta_wr7_pp"] if row["delta_wr7_pp"] is not None else 0.0
            if n_delta > 0 and d_wr7 >= -0.20:
                n_shift_bonus += weight * n_delta
            if n_delta > 0 and d_wr7 < -0.35:
                gate_penalty += abs(d_wr7) * 2.0
        row_7074 = bucket_rows.get("70-74")
        if row_7074:
            n70_delta = int(row_7074["new"]["n"]) - int(row_7074["base"]["n"])
            if n70_delta < 0:
                n_shift_bonus += min(abs(n70_delta), 100) * 0.005

        gate_penalty += w4_penalty + w6_penalty
        gate_bonus += n_shift_bonus
        gate_diag = {
            "w4_pass": w4["pass"],
            "w4_breaches": w4["breaches"],
            "w6_pass": w6["pass"],
            "w6_worsened_breaches": w6["worsened_breaches"],
            "n_shift_bonus": n_shift_bonus,
            "gate_penalty": gate_penalty,
            "candidate_tiers": candidate["tiers"],
            "candidate_buckets": candidate["buckets"],
        }

    wr7 = metrics["WR7"]
    wr15 = metrics["WR15"]
    wr30 = metrics["WR30"]
    z = wr7["z"] if wr7["z"] is not None else -5.0
    lifts = [wr7["lift_pp"], wr15["lift_pp"], wr30["lift_pp"]]
    lift_penalty = sum(max(0.0, -(v if v is not None else -10.0)) for v in lifts)
    min_lift = min(v if v is not None else -10.0 for v in lifts)
    period_penalty = sum(max(0.0, -(v["lift_pp"] if v["lift_pp"] is not None else -10.0)) for v in period.values())

    # Reward z and consistent multi-window lift; lightly reward useful N
    # redistribution and POET recovery. Penalize tiny cohorts and broad changes.
    score = (
        3.6 * z
        + 0.18 * (wr7["lift_pp"] or 0.0)
        + 0.12 * (wr15["lift_pp"] or 0.0)
        + 0.10 * (wr30["lift_pp"] or 0.0)
        + 0.08 * min_lift
        + 0.015 * n75_delta
        + 0.025 * n80_delta
        + 0.02 * n70_74_to_binding
        + 4.0 * poet_75
        + gate_bonus
        - 2.0 * lift_penalty
        - 1.5 * period_penalty
        - gate_penalty
    )
    if z >= 3.0:
        score += 4.0 + (z - 3.0) * 2.0
    elif z >= 2.75:
        score += (z - 2.75) * 3.0
    if n_changed < 80:
        score -= (80 - n_changed) * 0.035
    if n_changed > 900:
        score -= (n_changed - 900) * 0.015
    if wr7["affected_n"] < 80:
        score -= (80 - wr7["affected_n"]) * 0.05
    if poet_75 <= 0:
        score -= 3.0
    if poet_75 > 0 and z >= 3.0 and gate_diag.get("w6_pass") and gate_diag.get("w4_pass"):
        score += 5.0

    return {
        "label": params.label(),
        "params": asdict(params),
        "objective": score,
        "metrics": metrics,
        "periods": period,
        "n_changed": n_changed,
        "n75_delta": n75_delta,
        "n80_delta": n80_delta,
        "n70_74_to_binding": n70_74_to_binding,
        "poet_75": poet_75,
        "poet": poet,
        "gate_diag": gate_diag,
    }


def _seed_records() -> list[dict[str, Any]]:
    sources = [
        ROOT / "experiments" / "mcd_cwwd_wvd_recal" / "runs" / "participation_multiseed_20260511_1318" / "aggregate_summary.json",
        ROOT / "experiments" / "mcd_cwwd_wvd_recal" / "runs" / "participation_verify_20260511_1320" / "participation_summary.json",
        ROOT / "experiments" / "mcd_cwwd_wvd_recal" / "runs" / "bayes_refine_20260511_1405" / "bayes_summary.json",
        ROOT / "experiments" / "mcd_cwwd_wvd_recal" / "runs" / "bayes_poet_refine_20260511_1713" / "bayes_summary.json",
    ]
    seeds: list[dict[str, Any]] = []
    for path in sources:
        if not path.exists():
            continue
        obj = json.loads(path.read_text(encoding="utf-8"))
        for key in ("strict_top", "utility_top", "top", "top_fast", "top_full_gate"):
            for row in obj.get(key, [])[:20]:
                if "params" in row:
                    seeds.append(row["params"])
    seen = set()
    out = []
    for params in seeds:
        key = json.dumps({k: params[k] for k in PARAM_NAMES}, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(params)
    return out


def _full_gate_for_record(df: pl.DataFrame, baseline: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    params = WaveParams(**rec["params"])
    varied = apply_wave(df, params)
    candidate = _eval_rows(_row_dicts(varied))
    changed = pl.col("new_score") > pl.col("overall")
    w_windows = gate._window_affected(varied, changed)
    w1 = w_windows["WR7"]
    w1_pass = w1["z"] is not None and w1["z"] >= 3.0 and (w1["lift_pp"] or 0.0) > 0.0
    available = [w_windows[f"WR{w}"]["lift_pp"] for w in (7, 15, 30)]
    w2_available_pass = all(v is not None and v > 0.0 for v in available)
    w3 = gate._period_gate(df, params)
    w4 = gate._discrete_bucket_gate(baseline, candidate)
    w5 = gate._capacity_gate(candidate)
    w6 = gate._gradient_gate(baseline, candidate)
    return {
        "label": rec["label"],
        "params": rec["params"],
        "objective": rec["objective"],
        "fast_metrics": rec["metrics"],
        "fast_periods": rec["periods"],
        "candidate_tiers": candidate["tiers"],
        "candidate_buckets": candidate["buckets"],
        "poet": candidate.get("poet", []),
        "gates": {
            "W1_cohort_z": {"pass": w1_pass, **w1},
            "W2_available_WR7_WR15_WR30": {"pass": w2_available_pass, "windows": w_windows},
            "W3_time_window": w3,
            "W4_discrete_bucket_non_regression": w4,
            "W5_capacity_floor": w5,
            "W6_gradient_preservation": w6,
        },
    }


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "mcd_cwwd_wvd_recal" / "runs" / f"bayes_refine_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "status.json"
    results_path = run_dir / "bayes_results.jsonl"
    summary_path = run_dir / "bayes_summary.json"
    _write_json(status_path, {"phase": "setup", "state": "running", "started_at": _now(), "run_dir": str(run_dir)})

    df = pl.read_parquet(CONTEXT)
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, "participation_wave/bayesian_refine_context")
    df = gate._join_extra_windows(df)
    baseline = _eval_rows(_row_dicts(gate._with_baseline_new(df)))

    optimizer = Optimizer(DIMENSIONS, base_estimator="ET", acq_func="EI", random_state=args.seed, n_initial_points=0)
    evaluated: list[dict[str, Any]] = []

    def evaluate_x(x: list[float], source: str) -> dict[str, Any]:
        params = _params_from_x(x)
        rec = _fast_eval(df, params, baseline=baseline)
        rec["source"] = source
        return rec

    seed_xs = []
    for params in _seed_records()[: args.seed_candidates]:
        seed_xs.append(_x_from_params(params))
    rng = random.Random(args.seed)
    while len(seed_xs) < max(args.initial_random, 1):
        seed_xs.append([rng.uniform(dim.low, dim.high) for dim in DIMENSIONS])

    with results_path.open("w", encoding="utf-8") as fh:
        for x in seed_xs:
            rec = evaluate_x(x, "seed")
            evaluated.append(rec)
            optimizer.tell(x, -rec["objective"])
            fh.write(json.dumps(rec, default=str) + "\n")

        total = len(seed_xs) + args.trials
        for i in range(1, args.trials + 1):
            x = optimizer.ask()
            rec = evaluate_x(x, "optimizer")
            evaluated.append(rec)
            optimizer.tell(x, -rec["objective"])
            fh.write(json.dumps(rec, default=str) + "\n")
            if i % args.status_every == 0 or i == args.trials:
                best = max(evaluated, key=lambda r: r["objective"])
                _write_json(
                    status_path,
                    {
                        "phase": "optimize",
                        "state": "running",
                        "completed": len(evaluated),
                        "total": total,
                        "best_so_far": {
                            "label": best["label"],
                            "objective": best["objective"],
                            "WR7": best["metrics"]["WR7"],
                            "WR15": best["metrics"]["WR15"],
                            "WR30": best["metrics"]["WR30"],
                            "n_changed": best["n_changed"],
                            "poet_75": best["poet_75"],
                        },
                        "updated_at": _now(),
                        "results": str(results_path),
                    },
                )
                print(f"[opt] {len(evaluated)}/{total} best={best['label']} obj={best['objective']:.3f}", flush=True)

    ranked = sorted(evaluated, key=lambda r: r["objective"], reverse=True)
    top_full = [_full_gate_for_record(df, baseline, rec) for rec in ranked[: args.full_gate_top]]
    summary = {
        "generated_at": _now(),
        "context": str(CONTEXT),
        "trials": len(evaluated),
        "top_fast": ranked[:50],
        "top_full_gate": top_full,
        "notes": [
            "W2 is limited to WR7/WR15/WR30 because current barrier cache lacks WR3/WR5.",
            "Production scoring was not modified.",
        ],
    }
    _write_json(summary_path, summary)
    _write_json(run_dir / "done.json", {"state": "done", "finished_at": _now(), "summary": str(summary_path), "results": str(results_path)})
    _write_json(status_path, {"phase": "done", "state": "done", "completed": len(evaluated), "total": len(evaluated), "summary": str(summary_path), "finished_at": _now()})
    print(f"[done] {summary_path}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20260511)
    parser.add_argument("--seed-candidates", type=int, default=40)
    parser.add_argument("--initial-random", type=int, default=40)
    parser.add_argument("--full-gate-top", type=int, default=25)
    parser.add_argument("--status-every", type=int, default=25)
    parser.add_argument("--run-dir")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
