"""Explore smooth wave replacements for MCD's score-band cliff.

This is experiment-local only. It does not patch production scoring code.

The shipped MCD is smooth inside score band [70, 84], but hard-stops at 85.
This runner tests score-wave alternatives with no upper score cutoff:

  - logistic market-cap pressure instead of clipped LOG_LO/LOG_HI bands
  - Gaussian or sigmoid-hump score waves instead of a hard score gate
  - direct adjacent-score jump audit around 84/85 at LUMN-like mcap

Input cache: .cache/mcap_dampener/calls_v39_3650.parquet
Output: run_dir/status.json, results.jsonl, summary.json, done.json/failed.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments._holdout import assert_no_holdout_leak

CACHE = ROOT / ".cache" / "mcap_dampener" / "calls_v39_3650.parquet"
BUCKETS = [
    ("70-74", 70, 74),
    ("75-79", 75, 79),
    ("80-84", 80, 84),
    ("85-89", 85, 89),
    ("90+", 90, 100),
]
THRESHOLDS = [70, 75, 80, 85, 90, 95]
WINDOWS = {
    "1y": 365,
    "3y": 1095,
    "5y": 1825,
    "10y": 3650,
}


@dataclass(frozen=True)
class Variant:
    label: str
    shape: str
    alpha: float
    target: float
    mcap_mid: float
    mcap_scale: float
    mcap_power: float
    score_center: float | None = None
    score_sigma: float | None = None
    rise_mid: float | None = None
    rise_scale: float | None = None
    fall_mid: float | None = None
    fall_scale: float | None = None


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def logistic_expr(x: pl.Expr) -> pl.Expr:
    return 1.0 / (1.0 + (-x).exp())


def mcap_pressure_expr(v: Variant) -> pl.Expr:
    log_mcap = pl.col("mcap_b").clip(0.001, None).log10()
    raw = logistic_expr((pl.lit(v.mcap_mid) - log_mcap) / pl.lit(v.mcap_scale))
    return raw.pow(v.mcap_power)


def score_wave_expr(v: Variant) -> pl.Expr:
    score = pl.col("overall").cast(pl.Float64)
    if v.shape == "gauss":
        assert v.score_center is not None and v.score_sigma is not None
        z = (score - pl.lit(v.score_center)) / pl.lit(v.score_sigma)
        return ((z * z) * -0.5).exp()
    if v.shape == "hump":
        assert None not in (v.rise_mid, v.rise_scale, v.fall_mid, v.fall_scale)
        rise = logistic_expr((score - pl.lit(v.rise_mid)) / pl.lit(v.rise_scale))
        fall = logistic_expr((pl.lit(v.fall_mid) - score) / pl.lit(v.fall_scale))
        return rise * fall
    raise ValueError(f"unknown shape {v.shape!r}")


def apply_wave(df: pl.DataFrame, v: Variant) -> pl.DataFrame:
    has_mcap = pl.col("mcap_b").is_not_null() & (pl.col("mcap_b") > 0)
    pressure = mcap_pressure_expr(v) * score_wave_expr(v)
    damp = pl.lit(v.alpha) * pressure * (pl.col("overall") - pl.lit(v.target))
    return df.with_columns(
        pl.when(has_mcap & (pl.col("overall") >= 50) & (damp > 0))
        .then(pl.col("overall") - damp)
        .otherwise(pl.col("overall"))
        .round()
        .clip(0, 100)
        .cast(pl.Int64)
        .alias("new_overall")
    )


def apply_shipped_mcd(df: pl.DataFrame) -> pl.DataFrame:
    has_mcap = pl.col("mcap_b").is_not_null() & (pl.col("mcap_b") > 0)
    log_mcap = pl.col("mcap_b").clip(0.001, None).log10()
    mcap_raw = ((1.90 - log_mcap) / (1.90 - 0.50)).clip(0.0, 1.0)
    mcap_factor = mcap_raw.pow(0.70)
    score_raw = ((pl.col("overall") - 70) / (84 - 70)).clip(0.0, 1.0)
    score_factor = score_raw.pow(1.50)
    weakness = mcap_factor * score_factor
    in_zone = (pl.col("overall") >= 70) & (pl.col("overall") <= 84) & has_mcap
    damp = 0.80 * weakness * (pl.col("overall") - 61)
    return df.with_columns(
        pl.when(in_zone)
        .then(pl.col("overall") - damp)
        .otherwise(pl.col("overall"))
        .round()
        .clip(0, 100)
        .cast(pl.Int64)
        .alias("new_overall")
    )


def tp_n(df: pl.DataFrame, col: str, lo: int, hi: int | None = None) -> dict[str, Any]:
    if hi is None:
        sub = df.filter(pl.col(col) >= lo)
    else:
        sub = df.filter((pl.col(col) >= lo) & (pl.col(col) <= hi))
    n = sub.height
    if n == 0:
        return {"tp": None, "n": 0}
    tp = sub.select((pl.col("opt_result_15") == 1).cast(pl.Float64).mean()).item() * 100.0
    return {"tp": tp, "n": n}


def base_stats(df: pl.DataFrame, col: str) -> dict[str, Any]:
    per = {label: tp_n(df, col, lo, hi) for label, lo, hi in BUCKETS}
    cum = {f"{thr}+": tp_n(df, col, thr, None) for thr in THRESHOLDS}
    return {"per": per, "cum": cum}


def compare_stats(df: pl.DataFrame, base: dict[str, Any]) -> dict[str, Any]:
    cur = base_stats(df, "new_overall")
    for section in ["per", "cum"]:
        for key, cell in cur[section].items():
            b = base[section][key]
            cell["base_tp"] = b["tp"]
            cell["base_n"] = b["n"]
            cell["delta"] = (
                None if cell["tp"] is None or b["tp"] is None else cell["tp"] - b["tp"]
            )
            cell["n_delta_pct"] = (
                None if b["n"] == 0 else (cell["n"] - b["n"]) / b["n"] * 100.0
            )
    tps = [cur["per"][label]["tp"] for label, _, _ in BUCKETS]
    gaps = [
        tps[i + 1] - tps[i]
        for i in range(len(tps) - 1)
        if tps[i] is not None and tps[i + 1] is not None
    ]
    cur["gaps"] = gaps
    cur["min_gap"] = min(gaps) if gaps else None
    cur["monotonic"] = all(g >= 0 for g in gaps)
    sse = 0.0
    n_total = 0
    for label, _, _ in BUCKETS:
        cell = cur["per"][label]
        if cell["delta"] is not None and cell["n"] > 0:
            sse += cell["delta"] ** 2 * cell["n"]
            n_total += cell["n"]
    cur["rmse"] = math.sqrt(sse / n_total) if n_total else None
    return cur


def apply_variant(df: pl.DataFrame, v: Variant | str) -> pl.DataFrame:
    if v == "baseline":
        return df.with_columns(pl.col("overall").alias("new_overall"))
    if v == "shipped_mcd":
        return apply_shipped_mcd(df)
    assert isinstance(v, Variant)
    return apply_wave(df, v)


def shape_outputs(v: Variant | str, mcap_b: float = 8.74) -> list[dict[str, Any]]:
    rows = pl.DataFrame(
        {
            "overall": list(range(70, 101)),
            "mcap_b": [mcap_b] * 31,
            "opt_result_15": [1] * 31,
        }
    )
    out = apply_variant(rows, v).select(["overall", "new_overall"]).to_dicts()
    result = []
    prev = None
    for row in out:
        jump = None if prev is None else row["new_overall"] - prev
        result.append({**row, "adjacent_jump": jump})
        prev = row["new_overall"]
    return result


def max_adjacent_jump(v: Variant | str, mcap_b: float = 8.74) -> int:
    jumps = [r["adjacent_jump"] for r in shape_outputs(v, mcap_b) if r["adjacent_jump"] is not None]
    return int(max(abs(j) for j in jumps)) if jumps else 0


def evaluate_variant(
    v: Variant | str,
    label: str,
    windows: dict[str, pl.DataFrame],
    baselines: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rec: dict[str, Any] = {"label": label}
    if isinstance(v, Variant):
        rec.update(asdict(v))
    else:
        rec["shape"] = str(v)
    rec["windows"] = {}
    for w_label, df in windows.items():
        d = apply_variant(df, v)
        rec["windows"][w_label] = compare_stats(d, baselines[w_label])
    rec["jump_mcap8_70_100"] = max_adjacent_jump(v, 8.74)

    w5 = rec["windows"]["5y"]
    w10 = rec["windows"]["10y"]
    rec["lift_75_5y"] = w5["cum"]["75+"]["delta"]
    rec["lift_75_10y"] = w10["cum"]["75+"]["delta"]
    rec["lift_75_3y"] = rec["windows"]["3y"]["cum"]["75+"]["delta"]
    rec["lift_75_1y"] = rec["windows"]["1y"]["cum"]["75+"]["delta"]
    rec["n_drop_75_5y"] = w5["cum"]["75+"]["n_delta_pct"]
    rec["spill_85_5y"] = w5["cum"]["85+"]["delta"]
    rec["spill_90_5y"] = w5["cum"]["90+"]["delta"]
    rec["min_gap_5y"] = w5["min_gap"]
    rec["rmse_5y"] = w5["rmse"]
    rec["monotonic_5y"] = w5["monotonic"]
    rec["monotonic_10y"] = w10["monotonic"]

    lifts = [rec["lift_75_1y"], rec["lift_75_3y"], rec["lift_75_5y"], rec["lift_75_10y"]]
    rec["all_window_positive"] = all(x is not None and x > 0 for x in lifts)
    worst_spill = min([x for x in [rec["spill_85_5y"], rec["spill_90_5y"]] if x is not None] or [0.0])
    rec["worst_top_spill_5y"] = min(0.0, worst_spill)
    rec["soft_pass"] = bool(
        rec["all_window_positive"]
        and rec["lift_75_5y"] is not None
        and rec["lift_75_5y"] >= 1.0
        and rec["lift_75_10y"] is not None
        and rec["lift_75_10y"] >= 1.0
        and rec["n_drop_75_5y"] is not None
        and rec["n_drop_75_5y"] >= -55.0
        and rec["jump_mcap8_70_100"] <= 5
        and (rec["min_gap_5y"] is None or rec["min_gap_5y"] >= 1.5)
        and rec["worst_top_spill_5y"] >= -1.5
    )
    rec["composite"] = (
        (rec["lift_75_5y"] or 0.0) * 0.45
        + (rec["lift_75_10y"] or 0.0) * 0.30
        + (rec["lift_75_3y"] or 0.0) * 0.15
        + (rec["lift_75_1y"] or 0.0) * 0.10
        - max(0, rec["jump_mcap8_70_100"] - 2) * 0.25
        - max(0, -(rec["worst_top_spill_5y"] or 0.0) - 0.5) * 0.50
        - (rec["rmse_5y"] or 0.0) * 0.05
    )
    if not rec["soft_pass"]:
        rec["composite"] -= 100.0
    return rec


def variants() -> list[Variant]:
    out: list[Variant] = []
    for center in [78, 80, 82, 84, 86]:
        for sigma in [4, 6, 8, 10]:
            for alpha in [0.30, 0.45, 0.60, 0.75]:
                for target in [61, 64, 67]:
                    for mcap_mid in [1.5, 1.7, 1.9]:
                        for mcap_scale in [0.25, 0.40, 0.60]:
                            for mcap_power in [0.60, 0.80, 1.00]:
                                out.append(
                                    Variant(
                                        label=(
                                            f"gauss_c{center}_s{sigma}_a{alpha}_t{target}_"
                                            f"mm{mcap_mid}_ms{mcap_scale}_mp{mcap_power}"
                                        ),
                                        shape="gauss",
                                        alpha=alpha,
                                        target=target,
                                        mcap_mid=mcap_mid,
                                        mcap_scale=mcap_scale,
                                        mcap_power=mcap_power,
                                        score_center=center,
                                        score_sigma=sigma,
                                    )
                                )
    for rise_mid in [70, 72, 74]:
        for fall_mid in [84, 86, 88, 90]:
            if fall_mid <= rise_mid + 8:
                continue
            for rise_scale in [1.5, 2.5, 4.0]:
                for fall_scale in [2.0, 3.5, 5.0]:
                    for alpha in [0.35, 0.50, 0.65]:
                        for target in [61, 64, 67]:
                            for mcap_mid in [1.5, 1.7, 1.9]:
                                for mcap_scale in [0.30, 0.50, 0.70]:
                                    for mcap_power in [0.60, 0.80, 1.00]:
                                        out.append(
                                            Variant(
                                                label=(
                                                    f"hump_r{rise_mid}_{rise_scale}_f{fall_mid}_{fall_scale}_"
                                                    f"a{alpha}_t{target}_mm{mcap_mid}_ms{mcap_scale}_mp{mcap_power}"
                                                ),
                                                shape="hump",
                                                alpha=alpha,
                                                target=target,
                                                mcap_mid=mcap_mid,
                                                mcap_scale=mcap_scale,
                                                mcap_power=mcap_power,
                                                rise_mid=rise_mid,
                                                rise_scale=rise_scale,
                                                fall_mid=fall_mid,
                                                fall_scale=fall_scale,
                                            )
                                        )
    return out


def load_data() -> tuple[dict[str, pl.DataFrame], dict[str, dict[str, Any]]]:
    df = pl.read_parquet(CACHE).filter(pl.col("opt_result_15").is_not_null())
    assert_no_holdout_leak(df, "mcap_dampener/wave_mcd_sweep")
    today = date.today()
    windows = {
        label: df.filter(pl.col("date") >= (today - timedelta(days=days)).isoformat())
        for label, days in WINDOWS.items()
    }
    baselines = {label: base_stats(wdf, "overall") for label, wdf in windows.items()}
    return windows, baselines


def summarize(results: list[dict[str, Any]], baselines: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(results, key=lambda r: r["composite"], reverse=True)
    soft = [r for r in ranked if r["soft_pass"]]
    shipped = next(r for r in results if r["label"] == "shipped_mcd")
    return {
        "finished_at": now_iso(),
        "cache": str(CACHE),
        "baseline_5y": baselines["5y"],
        "shipped_mcd": slim_record(shipped, include_shape=True),
        "n_results": len(results),
        "n_soft_pass": len(soft),
        "top": [slim_record(r, include_shape=i < 5) for i, r in enumerate(ranked[:25])],
        "top_soft_pass": [slim_record(r, include_shape=i < 5) for i, r in enumerate(soft[:25])],
    }


def slim_record(r: dict[str, Any], include_shape: bool = False) -> dict[str, Any]:
    keys = [
        "label",
        "shape",
        "alpha",
        "target",
        "mcap_mid",
        "mcap_scale",
        "mcap_power",
        "score_center",
        "score_sigma",
        "rise_mid",
        "rise_scale",
        "fall_mid",
        "fall_scale",
        "lift_75_1y",
        "lift_75_3y",
        "lift_75_5y",
        "lift_75_10y",
        "n_drop_75_5y",
        "spill_85_5y",
        "spill_90_5y",
        "worst_top_spill_5y",
        "min_gap_5y",
        "rmse_5y",
        "jump_mcap8_70_100",
        "soft_pass",
        "composite",
    ]
    out = {k: r.get(k) for k in keys if k in r}
    if include_shape and r.get("label") and r.get("label") not in {"baseline"}:
        if r.get("shape") == "shipped_mcd":
            out["shape_mcap8"] = shape_outputs("shipped_mcd", 8.74)
        elif "alpha" in r:
            out["shape_mcap8"] = shape_outputs(Variant(**{k: r.get(k) for k in Variant.__dataclass_fields__}), 8.74)
    return out


def run(run_dir: Path, limit: int | None = None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "status.json"
    results_path = run_dir / "results.jsonl"
    summary_path = run_dir / "summary.json"
    started_at = now_iso()
    write_json(
        status_path,
        {
            "phase": "loading",
            "state": "running",
            "started_at": started_at,
            "updated_at": started_at,
            "run_dir": str(run_dir),
            "cache": str(CACHE),
        },
    )

    windows, baselines = load_data()
    all_variants = variants()
    if limit is not None:
        all_variants = all_variants[:limit]

    baseline_rec = evaluate_variant("baseline", "baseline", windows, baselines)
    shipped_rec = evaluate_variant("shipped_mcd", "shipped_mcd", windows, baselines)
    results: list[dict[str, Any]] = [baseline_rec, shipped_rec]
    with results_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(slim_record(baseline_rec)) + "\n")
        f.write(json.dumps(slim_record(shipped_rec)) + "\n")

    total = len(all_variants)
    best = slim_record(max(results, key=lambda r: r["composite"]))
    t0 = time.time()
    for i, v in enumerate(all_variants, start=1):
        rec = evaluate_variant(v, v.label, windows, baselines)
        results.append(rec)
        with results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(slim_record(rec)) + "\n")
        if rec["composite"] > best.get("composite", -1e9):
            best = slim_record(rec)
        if i == 1 or i % 100 == 0 or i == total:
            elapsed = time.time() - t0
            eta = None if i == 0 else elapsed / i * (total - i)
            write_json(
                status_path,
                {
                    "phase": "sweeping",
                    "state": "running",
                    "started_at": started_at,
                    "updated_at": now_iso(),
                    "completed": i,
                    "total": total,
                    "elapsed_sec": round(elapsed, 1),
                    "eta_sec": None if eta is None else round(eta, 1),
                    "best_so_far": best,
                    "run_dir": str(run_dir),
                    "results_path": str(results_path),
                    "summary_path": str(summary_path),
                },
            )
            print(f"[wave_mcd] {i}/{total} best={best.get('label')} comp={best.get('composite'):.3f}", flush=True)

    summary = summarize(results, baselines)
    write_json(summary_path, summary)
    write_json(
        status_path,
        {
            "phase": "complete",
            "state": "done",
            "started_at": started_at,
            "updated_at": now_iso(),
            "completed": total,
            "total": total,
            "best_so_far": summary["top"][0] if summary["top"] else None,
            "run_dir": str(run_dir),
            "results_path": str(results_path),
            "summary_path": str(summary_path),
        },
    )
    write_json(
        run_dir / "done.json",
        {
            "finished_at": now_iso(),
            "exit_code": 0,
            "summary_path": str(summary_path),
            "results_path": str(results_path),
        },
    )
    print(f"[wave_mcd] done: {summary_path}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    try:
        run(run_dir, limit=args.limit)
    except Exception as exc:
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "finished_at": now_iso(),
            "exit_code": 1,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        write_json(run_dir / "failed.json", payload)
        write_json(
            run_dir / "status.json",
            {
                "phase": "failed",
                "state": "failed",
                "updated_at": now_iso(),
                "error": str(exc),
                "failed_path": str(run_dir / "failed.json"),
            },
        )
        raise


if __name__ == "__main__":
    # Keep redirected Windows output UTF-8 friendly when launched directly.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    main()
