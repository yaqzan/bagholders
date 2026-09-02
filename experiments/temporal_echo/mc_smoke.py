from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polars as pl

import monte_carlo as mc
from database.models.core import AlgorithmVersion, Score
from experiments.temporal_echo.sweep import Variant, apply_variant, bucket_expr


RANK6_VARIANT = Variant(
    tau=40.167303886177535,
    score_power=0.6281942103863293,
    norm=1.2591289035385456,
    alpha=0.8303841575227674,
    max_lift=4.420164793824554,
    target=82.0,
    min_echo=0.04889786965184006,
    w7=0.15934815792492626,
    w15=0.08875940908243371,
    w30=0.5934974664235093,
    w60=0.65,
    loss_penalty=0.1885449909679346,
    fizzler_penalty=0.7348462309277648,
    allow_puts=False,
)


WINDOW_ALIASES = {
    label: (label, d_start, d_end)
    for label, d_start, d_end in mc.WINDOWS
}


def _iso(obj) -> str | None:
    if obj is None:
        return None
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _jsonable(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    return obj


def _parse_windows(names: Iterable[str]) -> list[tuple[str, date, date]]:
    out = []
    for name in names:
        key = name.strip()
        if not key:
            continue
        if key not in WINDOW_ALIASES:
            raise SystemExit(f"Unknown MC window {key!r}; choices: {sorted(WINDOW_ALIASES)}")
        out.append(WINDOW_ALIASES[key])
    if not out:
        raise SystemExit("At least one --windows label is required")
    return out


def build_adjusted_scores(signals_path: Path, priors_path: Path, variant: Variant) -> pl.DataFrame:
    signals = pl.read_parquet(signals_path)
    priors = pl.read_parquet(priors_path)
    adjusted = apply_variant(signals, priors, variant)
    return adjusted.filter(pl.col("side") == "low").select(
        ["symbol", "date", "side", "overall", "new_score", "changed", "bucket", "new_bucket"]
    )


def _variant_from_dict(raw: dict) -> Variant:
    data = raw.get("variant", raw)
    return Variant(**{field: data[field] for field in Variant.__dataclass_fields__})


def load_variant(path: Path | None, rank: int) -> Variant:
    if path is None:
        return RANK6_VARIANT
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        matches = [rec for rec in raw if int(rec.get("rank", -1)) == rank]
        if not matches:
            raise SystemExit(f"Could not find rank={rank} in {path}")
        return _variant_from_dict(matches[0])
    if isinstance(raw, dict):
        if "results" in raw and isinstance(raw["results"], list):
            matches = [rec for rec in raw["results"] if int(rec.get("rank", -1)) == rank]
            if not matches:
                raise SystemExit(f"Could not find rank={rank} in {path}")
            return _variant_from_dict(matches[0])
        return _variant_from_dict(raw)
    raise SystemExit(f"Unsupported variant file shape: {path}")


def apply_min_new_score_gate(df: pl.DataFrame, min_new_score: int | None) -> pl.DataFrame:
    if not min_new_score or min_new_score <= 0:
        return df
    gated_score = (
        pl.when(
            (pl.col("side") == "low")
            & (pl.col("overall") < min_new_score)
            & (pl.col("new_score") < min_new_score)
        )
        .then(pl.col("overall"))
        .otherwise(pl.col("new_score"))
    )
    return df.with_columns(gated_score.alias("new_score")).with_columns(
        [
            (pl.col("new_score") != pl.col("overall")).alias("changed"),
            bucket_expr(pl.col("new_score")).alias("new_bucket"),
        ]
    )


def infer_cache_paths(lookback_days: int, min_gap: int, max_gap: int) -> tuple[Path, Path]:
    version = AlgorithmVersion.get_active_scores_version()
    suffix = f"v{version.id}_{lookback_days}_g{min_gap}_{max_gap}"
    base = ROOT / ".cache" / "temporal_echo"
    return base / f"signals_{suffix}.parquet", base / f"priors_{suffix}.parquet"


def score_map_for_window(adjusted: pl.DataFrame, d_start: date, d_end: date) -> dict[tuple[str, date], int]:
    windowed = adjusted.filter(
        (pl.col("date") >= d_start)
        & (pl.col("date") <= d_end)
        & ((pl.col("overall") >= 50) | (pl.col("new_score") >= mc.OVERFLOW_THRESHOLD))
    )
    return {
        (str(row["symbol"]), row["date"]): int(row["new_score"])
        for row in windowed.iter_rows(named=True)
    }


def describe_adjustments(adjusted: pl.DataFrame, d_start: date, d_end: date) -> dict:
    windowed = adjusted.filter((pl.col("date") >= d_start) & (pl.col("date") <= d_end))
    promoted = windowed.filter((pl.col("overall") < mc.PRIMARY_THRESHOLD) & (pl.col("new_score") >= mc.PRIMARY_THRESHOLD))
    threshold70 = windowed.filter((pl.col("overall") < mc.OVERFLOW_THRESHOLD) & (pl.col("new_score") >= mc.OVERFLOW_THRESHOLD))
    changed = windowed.filter(pl.col("changed"))
    by_new_bucket = (
        windowed.filter(pl.col("new_score") >= mc.OVERFLOW_THRESHOLD)
        .group_by("new_bucket")
        .agg(pl.len().alias("n"))
        .sort("new_bucket")
        .to_dicts()
    )
    return {
        "rows": windowed.height,
        "changed": changed.height,
        "promoted_to_75_plus": promoted.height,
        "promoted_to_70_plus": threshold70.height,
        "new_call_bucket_counts": by_new_bucket,
    }


@contextlib.contextmanager
def inject_echo_call_scores(score_map: dict[tuple[str, date], int]):
    original_load_signals = mc.load_signals

    def patched_load_signals(version, d_start, d_end):
        rows = list(
            Score.select(Score.symbol, Score.date, Score.overall, Score.trend, Score.weight_info, Score.stoch)
            .where(
                Score.version == version,
                Score.date >= d_start,
                Score.date <= d_end,
                Score.overall >= 50,
            )
        )
        sigs = []
        for row in rows:
            symbol = str(getattr(row, "symbol_id", ""))
            adjusted = score_map.get((symbol, row.date), int(row.overall))
            if adjusted >= mc.OVERFLOW_THRESHOLD:
                row.overall = int(adjusted)
                sigs.append(row)
        sigs.sort(key=lambda r: (r.date, -int(r.overall)))
        return mc._apply_ctsl_to_signals(sigs, "call")

    mc.load_signals = patched_load_signals
    try:
        yield
    finally:
        mc.load_signals = original_load_signals


def summarize_run(result: dict) -> dict:
    seeded = result["seeded"]
    return {
        "mean_ret": seeded["mean_ret"],
        "med_ret": seeded["med_ret"],
        "mean_dd": seeded["mean_dd"],
        "worst_dd": seeded["worst_dd"],
        "p_coll": seeded["p_coll"],
        "call_tp": seeded["call_tp"],
        "put_tp": seeded["put_tp"],
        "call_trades": seeded["call_trades"],
        "put_trades": seeded["put_trades"],
    }


def diff_summary(base: dict, cand: dict) -> dict:
    return {
        key: cand[key] - base[key]
        for key in base.keys()
        if isinstance(base.get(key), (int, float)) and isinstance(cand.get(key), (int, float))
    }


def run_mc_smoke(
    signals_path: Path,
    priors_path: Path,
    out_dir: Path,
    windows: list[tuple[str, date, date]],
    n_iter: int,
    variant: Variant,
    min_new_score: int | None,
    version_id: int | None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MC_NO_MP"] = "1"
    mc.N_ITER = n_iter
    version = AlgorithmVersion.get_by_id(version_id) if version_id is not None else AlgorithmVersion.get_active_scores_version()
    adjusted = apply_min_new_score_gate(build_adjusted_scores(signals_path, priors_path, variant), min_new_score)
    results = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "version": {
            "id": version.id,
            "git_commit": version.git_commit,
            "git_message": version.git_message,
        },
        "n_iter": n_iter,
        "variant": asdict(variant),
        "min_new_score": min_new_score,
        "signals_path": str(signals_path),
        "priors_path": str(priors_path),
        "windows": {},
    }

    for label, d_start, d_end in windows:
        print(f"[mc-smoke] baseline {label} {d_start}..{d_end}", flush=True)
        baseline = summarize_run(mc.run_window(label, d_start, d_end, version))

        score_map = score_map_for_window(adjusted, d_start, d_end)
        adjust_desc = describe_adjustments(adjusted, d_start, d_end)
        print(
            f"[mc-smoke] candidate {label}: map={len(score_map):,} "
            f"changed={adjust_desc['changed']:,} promoted75={adjust_desc['promoted_to_75_plus']:,}",
            flush=True,
        )
        with inject_echo_call_scores(score_map):
            candidate = summarize_run(mc.run_window(label, d_start, d_end, version))

        results["windows"][label] = {
            "date_start": _iso(d_start),
            "date_end": _iso(d_end),
            "adjustments": adjust_desc,
            "baseline": baseline,
            "candidate": candidate,
            "delta": diff_summary(baseline, candidate),
        }
        (out_dir / "mc_smoke_partial.json").write_text(
            json.dumps(_jsonable(results), indent=2),
            encoding="utf-8",
        )

    results["finished_at"] = datetime.now().isoformat(timespec="seconds")
    out = out_dir / "mc_smoke_results.json"
    out.write_text(json.dumps(_jsonable(results), indent=2), encoding="utf-8")
    print(f"[mc-smoke] wrote {out}", flush=True)
    return results


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", default=None)
    parser.add_argument("--priors", default=None)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--windows", default="22-now", help="Comma-separated monte_carlo.WINDOWS labels")
    parser.add_argument("--n-iter", type=int, default=100)
    parser.add_argument("--lookback-days", type=int, default=1825)
    parser.add_argument("--min-gap", type=int, default=1)
    parser.add_argument("--max-gap", type=int, default=90)
    parser.add_argument("--variant-file", default=None, help="JSON variant object or sweep_results.json")
    parser.add_argument("--variant-rank", type=int, default=1)
    parser.add_argument("--min-new-score", type=int, default=None)
    parser.add_argument("--version-id", type=int, default=None, help="Pin MC DB score rows to this AlgorithmVersion id")
    args = parser.parse_args(argv)
    windows = _parse_windows(args.windows.split(","))
    inferred_signals, inferred_priors = infer_cache_paths(args.lookback_days, args.min_gap, args.max_gap)
    signals = Path(args.signals) if args.signals else inferred_signals
    priors = Path(args.priors) if args.priors else inferred_priors
    missing = [str(p) for p in (signals, priors) if not p.exists()]
    if missing:
        raise SystemExit(f"Missing temporal echo cache(s): {missing}")
    variant = load_variant(Path(args.variant_file) if args.variant_file else None, args.variant_rank)
    return run_mc_smoke(
        signals,
        priors,
        Path(args.out_dir),
        windows,
        args.n_iter,
        variant,
        args.min_new_score,
        args.version_id,
    )


if __name__ == "__main__":
    main()
