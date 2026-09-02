"""Stage 1 W1-W6 gate check for Participation Wave candidates.

This is an experiment-local approximation over the v46 current call-peak cache.
It does not modify production scoring and is not a substitute for a faithful
ScoreSimulator/recalc pass if a candidate clears these gates.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polars as pl

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter
from experiments.mcd_cwwd_wvd_recal.participation_wave import (
    CALL_BUCKETS,
    CALL_TIERS,
    CONTEXT,
    WaveParams,
    _eval_rows,
    _row_dicts,
    apply_wave,
)

WINDOW_DAYS = [3, 5, 7, 15, 30]
PERIODS = {
    "1y": ("2025-05-15", "2026-05-14"),
    "3y": ("2023-05-15", "2026-05-14"),
    "5y": ("1900-01-01", "2026-05-14"),
}
N_FLOORS_PER_YEAR = {
    "70+": 100,
    "75+": 40,
    "80+": 20,
    "85+": 8,
    "90+": 3,
    "95+": 1,
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _wr(df: pl.DataFrame, col: str) -> tuple[float | None, int]:
    resolved = df.filter(pl.col(col).is_not_null())
    n = resolved.height
    if n == 0:
        return None, 0
    return 100.0 * resolved.filter(pl.col(col) == 1).height / n, n


def _join_extra_windows(df: pl.DataFrame) -> pl.DataFrame:
    missing = [w for w in (3, 5) if f"win_{w}d" not in df.columns]
    if not missing:
        return df
    df = df.with_columns(pl.col("date").cast(pl.Utf8))
    barriers_path = ROOT / ".cache" / "runner" / "barriers_1825.parquet"
    barriers = pl.DataFrame()
    if barriers_path.exists():
        barriers = (
            pl.read_parquet(barriers_path)
            .filter((pl.col("side") == "low") & pl.col("w_days").is_in(missing))
            .select(["symbol", "date", "w_days", "result"])
            .with_columns(pl.col("date").cast(pl.Utf8))
        )
    if barriers.is_empty():
        import duckdb

        duck_path = ROOT / ".cache" / "barrier_outcomes.duckdb"
        pairs = df.select(["symbol", "date"]).unique().to_arrow()
        con = duckdb.connect(str(duck_path), read_only=True)
        con.register("pairs", pairs)
        placeholders = ", ".join(str(int(w)) for w in missing)
        arrow = con.execute(
            f"""
            SELECT p.symbol, CAST(p.date AS VARCHAR) AS date, b.w_days, b.result
            FROM pairs p
            JOIN barrier_outcomes b
              ON b.symbol = p.symbol
             AND CAST(b.date AS VARCHAR) = CAST(p.date AS VARCHAR)
            WHERE b.barrier_set = '30dte_generic'
              AND b.side = 'low'
              AND b.w_days IN ({placeholders})
            """
        ).to_arrow_table()
        barriers = pl.from_arrow(arrow)
    barriers = pre_cutoff_filter(barriers)
    assert_no_holdout_leak(barriers, "participation_wave/stage1_extra_windows")
    for w in missing:
        piece = (
            barriers.filter(pl.col("w_days") == w)
            .select(["symbol", "date", pl.col("result").alias(f"win_{w}d")])
            .unique(["symbol", "date"], keep="first")
        )
        df = df.join(piece, on=["symbol", "date"], how="left")
    return df


def _z_score(p1: float, n1: int, p0: float, n0: int) -> float | None:
    if n1 <= 0 or n0 <= 0:
        return None
    p1f = p1 / 100.0
    p0f = p0 / 100.0
    pooled = ((p1f * n1) + (p0f * n0)) / (n1 + n0)
    se = math.sqrt(max(pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n0), 1e-12))
    return (p1f - p0f) / se


def _with_baseline_new(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        [
            pl.col("overall").alias("new_score"),
            pl.lit(0.0).alias("wave_boost"),
        ]
    )


def _per_bucket_wr(eval_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return eval_result["buckets"]


def _window_affected(df: pl.DataFrame, affected_expr: pl.Expr) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for w in WINDOW_DAYS:
        col = f"win_{w}d"
        aff = df.filter(affected_expr & pl.col(col).is_not_null())
        rest = df.filter((~affected_expr) & pl.col(col).is_not_null())
        wr_aff, n_aff = _wr(aff, col)
        wr_rest, n_rest = _wr(rest, col)
        out[f"WR{w}"] = {
            "affected_wr": wr_aff,
            "affected_n": n_aff,
            "rest_wr": wr_rest,
            "rest_n": n_rest,
            "lift_pp": None if wr_aff is None or wr_rest is None else wr_aff - wr_rest,
            "z": None if wr_aff is None or wr_rest is None else _z_score(wr_aff, n_aff, wr_rest, n_rest),
        }
    return out


def _discrete_bucket_gate(base: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    breaches = []
    rows = []
    for label, b in _per_bucket_wr(base).items():
        n = _per_bucket_wr(new)[label]
        if (b.get("wr7_n") or 0) < 30 or (n.get("wr7_n") or 0) < 30:
            delta = None
        else:
            delta = (n.get("wr7") or 0.0) - (b.get("wr7") or 0.0)
            if delta < -0.50:
                breaches.append({"bucket": label, "delta_wr7_pp": delta, "n": n.get("wr7_n")})
        rows.append({"bucket": label, "base": b, "new": n, "delta_wr7_pp": delta})
    return {"pass": len(breaches) == 0, "breaches": breaches, "rows": rows}


def _capacity_gate(new: dict[str, Any]) -> dict[str, Any]:
    breaches = []
    rows = []
    for tier in [f"{t}+" for t in CALL_TIERS]:
        n = new["tiers"][tier]["n"]
        per_year = n / 5.0
        floor = N_FLOORS_PER_YEAR[tier]
        ok = per_year >= floor
        if not ok:
            breaches.append({"tier": tier, "per_year": per_year, "floor": floor})
        rows.append({"tier": tier, "n": n, "per_year": per_year, "floor": floor, "pass": ok})
    return {"pass": len(breaches) == 0, "breaches": breaches, "rows": rows}


def _gradient_breaches(result: dict[str, Any]) -> tuple[list[dict[str, Any]], list[tuple[str, float | None, int]]]:
    tiers = [f"{t}+" for t in [95, 90, 85, 80, 75, 70]]
    vals = [(tier, result["tiers"][tier]["wr7"], result["tiers"][tier]["wr7_n"]) for tier in tiers]
    breaches = []
    prev_wr = None
    prev_tier = None
    for tier, wr, n in vals:
        if wr is None or n < 30:
            continue
        if prev_wr is not None and wr > prev_wr:
            breaches.append(
                {
                    "higher_tier": prev_tier,
                    "lower_tier": tier,
                    "higher_wr7": prev_wr,
                    "lower_wr7": wr,
                    "severity_pp": wr - prev_wr,
                }
            )
        prev_wr = wr
        prev_tier = tier
    return breaches, vals


def _gradient_gate(base: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    base_breaches, base_rows = _gradient_breaches(base)
    new_breaches, new_rows = _gradient_breaches(new)
    base_by_pair = {(b["higher_tier"], b["lower_tier"]): b["severity_pp"] for b in base_breaches}
    worsened = []
    for b in new_breaches:
        old = base_by_pair.get((b["higher_tier"], b["lower_tier"]), 0.0)
        if b["severity_pp"] > old + 0.05:
            bb = dict(b)
            bb["baseline_severity_pp"] = old
            worsened.append(bb)
    return {
        "pass": len(worsened) == 0,
        "worsened_breaches": worsened,
        "baseline_breaches": base_breaches,
        "candidate_breaches": new_breaches,
        "baseline_rows": base_rows,
        "candidate_rows": new_rows,
    }


def _period_gate(df: pl.DataFrame, params: WaveParams) -> dict[str, Any]:
    out = {}
    for label, (start, stop) in PERIODS.items():
        sub = df.filter((pl.col("date") >= start) & (pl.col("date") <= stop))
        varied = apply_wave(sub, params)
        aff = pl.col("wave_boost") >= 0.50
        out[label] = _window_affected(varied, aff)["WR7"]
    signs = []
    for v in out.values():
        lift = v["lift_pp"]
        signs.append(None if lift is None else (1 if lift > 0 else -1 if lift < 0 else 0))
    known = [s for s in signs if s is not None]
    return {"pass": bool(known) and all(s > 0 for s in known), "periods": out}


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "mcd_cwwd_wvd_recal" / "runs" / f"stage1_gate_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)

    params = WaveParams(
        k=args.k,
        cap=args.cap,
        dv_mid=args.dv_mid,
        dv_slope=args.dv_slope,
        impulse_mid=args.impulse_mid,
        impulse_slope=args.impulse_slope,
        vel_mid=args.vel_mid,
        vel_slope=args.vel_slope,
        wadj_mid=args.wadj_mid,
        wadj_slope=args.wadj_slope,
        force_peak=args.force_peak,
        force_width=args.force_width,
        pressure_floor=args.pressure_floor,
    )

    df = pl.read_parquet(CONTEXT)
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, "participation_wave/stage1_gate_context")
    df = _join_extra_windows(df)

    baseline = _eval_rows(_row_dicts(_with_baseline_new(df)))
    varied = apply_wave(df, params)
    candidate = _eval_rows(_row_dicts(varied))
    affected_expr = pl.col("wave_boost") >= 0.50
    affected = varied.filter(affected_expr)

    w_windows = _window_affected(varied, affected_expr)
    w1 = w_windows["WR7"]
    w1_pass = w1["z"] is not None and w1["z"] >= 3.0 and (w1["lift_pp"] or 0) > 0
    w2_lifts = [w_windows[f"WR{w}"]["lift_pp"] for w in WINDOW_DAYS]
    w2_pass = all(v is not None and v > 0 for v in w2_lifts)
    w3 = _period_gate(df, params)
    w4 = _discrete_bucket_gate(baseline, candidate)
    w5 = _capacity_gate(candidate)
    w6 = _gradient_gate(baseline, candidate)

    summary = {
        "generated_at": _now(),
        "context": str(CONTEXT),
        "params": asdict(params),
        "candidate_label": params.label(),
        "scope_note": "Approximation over v46 current call-peak cache; requires faithful recalc if gates pass.",
        "counts": {
            "rows": df.height,
            "affected_wave_boost_ge_0_5": affected.height,
            "affected_pct": 100.0 * affected.height / df.height if df.height else 0.0,
        },
        "baseline_tiers": baseline["tiers"],
        "candidate_tiers": candidate["tiers"],
        "poet": candidate.get("poet", []),
        "gates": {
            "W1_cohort_z": {"pass": w1_pass, **w1},
            "W2_multi_window": {"pass": w2_pass, "windows": w_windows},
            "W3_time_window": w3,
            "W4_discrete_bucket_non_regression": w4,
            "W5_capacity_floor": w5,
            "W6_gradient_preservation": w6,
        },
    }
    summary["overall_pass"] = all(
        [
            summary["gates"]["W1_cohort_z"]["pass"],
            summary["gates"]["W2_multi_window"]["pass"],
            summary["gates"]["W3_time_window"]["pass"],
            summary["gates"]["W4_discrete_bucket_non_regression"]["pass"],
            summary["gates"]["W5_capacity_floor"]["pass"],
            summary["gates"]["W6_gradient_preservation"]["pass"],
        ]
    )
    out = run_dir / "stage1_gate_summary.json"
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (run_dir / "done.json").write_text(json.dumps({"state": "done", "finished_at": _now(), "summary": str(out)}, indent=2), encoding="utf-8")
    print(json.dumps({"overall_pass": summary["overall_pass"], "summary": str(out), "gates": {k: v["pass"] for k, v in summary["gates"].items()}}, indent=2))
    return 0 if summary["overall_pass"] else 2


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir")
    p.add_argument("--k", type=float, default=0.90)
    p.add_argument("--cap", type=float, default=2.0)
    p.add_argument("--dv-mid", type=float, default=0.7)
    p.add_argument("--dv-slope", type=float, default=1.5)
    p.add_argument("--impulse-mid", type=float, default=0.0)
    p.add_argument("--impulse-slope", type=float, default=1.0)
    p.add_argument("--vel-mid", type=float, default=-2.0)
    p.add_argument("--vel-slope", type=float, default=0.35)
    p.add_argument("--wadj-mid", type=float, default=4.0)
    p.add_argument("--wadj-slope", type=float, default=0.25)
    p.add_argument("--force-peak", type=float, default=0.7)
    p.add_argument("--force-width", type=float, default=0.6)
    p.add_argument("--pressure-floor", type=float, default=1.5)
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
