from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Callable

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polars as pl

from database.models.core import AlgorithmVersion, MarketBreadth, MarketRegime, Score
from experiments.temporal_echo.sweep import ALLOC_WEIGHTS, TRADABLE_CALL, Variant, apply_variant, bucket_expr


WINDOWS = (3, 5, 7, 15, 30)
CALL_BUCKETS = ("95+", "90-94", "85-89", "80-84", "75-79", "70-74")


def write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


class Status:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.path = run_dir / "status.json"
        self.started_at = datetime.now().isoformat(timespec="seconds")

    def write(self, phase: str, state: str = "running", completed: int | None = None,
              total: int | None = None, **extra) -> None:
        payload = {
            "phase": phase,
            "state": state,
            "completed": completed,
            "total": total,
            "started_at": self.started_at,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "output_paths": {
                "run_dir": str(self.run_dir),
                "run_log": str(self.run_dir / "run.log"),
                "score_results": str(self.run_dir / "guard_destination_score_results.json"),
                "score_ranked_csv": str(self.run_dir / "guard_destination_score_ranked.csv"),
                "mc_results": str(self.run_dir / "guard_destination_mc_results.json"),
                "done_json": str(self.run_dir / "done.json"),
                "failed_json": str(self.run_dir / "failed.json"),
            },
        }
        payload.update(extra)
        write_json(self.path, payload)


def load_variant(path: Path, source_rank: int) -> Variant:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(f"Expected list variant file: {path}")
    matches = [r for r in raw if int(r.get("source_rank", r.get("rank", -1))) == source_rank]
    if not matches:
        raise SystemExit(f"Could not find source_rank={source_rank} in {path}")
    return Variant(**matches[0]["variant"])


def hard_gate(df: pl.DataFrame, gate: int) -> pl.DataFrame:
    score = (
        pl.when((pl.col("overall") < gate) & (pl.col("raw_new_score") < gate))
        .then(pl.col("overall"))
        .otherwise(pl.col("raw_new_score"))
    )
    return finalize_scores(df.with_columns(score.alias("new_score")))


def ramp_gate(df: pl.DataFrame, start: int, full: int) -> pl.DataFrame:
    denom = max(1e-9, float(full - start))
    mult = ((pl.col("raw_new_score").cast(pl.Float64) - float(start)) / denom).clip(0.0, 1.0)
    score = (
        pl.col("overall").cast(pl.Float64)
        + pl.col("raw_lift").cast(pl.Float64) * mult
    ).round().clip(0, 100).cast(pl.Int64)
    return finalize_scores(df.with_columns(score.alias("new_score")))


def finalize_scores(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        [
            (pl.col("new_score") != pl.col("overall")).alias("changed"),
            bucket_expr(pl.col("new_score")).alias("new_bucket"),
        ]
    )


def load_market_context(min_date: date, max_date: date) -> pl.DataFrame:
    breadth_rows = list(
        MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
        .where(MarketBreadth.date >= min_date, MarketBreadth.date <= max_date)
        .order_by(MarketBreadth.date)
        .dicts()
    )
    regime_rows = list(
        MarketRegime.select(
            MarketRegime.date,
            MarketRegime.regime_composite,
            MarketRegime.regime_multiplier,
            MarketRegime.vix_close,
            MarketRegime.spy_close,
            MarketRegime.spy_ema50,
            MarketRegime.spy_ema200,
        )
        .where(MarketRegime.date >= min_date, MarketRegime.date <= max_date)
        .order_by(MarketRegime.date)
        .dicts()
    )

    bdf = pl.DataFrame(breadth_rows) if breadth_rows else pl.DataFrame({"date": [], "breadth_score": []})
    rdf = pl.DataFrame(regime_rows) if regime_rows else pl.DataFrame({"date": []})
    if not bdf.is_empty():
        bdf = bdf.with_columns(
            [
                pl.col("date").cast(pl.Date),
                pl.col("breadth_score").cast(pl.Float64),
            ]
        )
    if not rdf.is_empty():
        rdf = rdf.with_columns(
            [
                pl.col("date").cast(pl.Date),
                pl.col("regime_composite").cast(pl.Float64),
                pl.col("regime_multiplier").cast(pl.Float64),
                pl.col("vix_close").cast(pl.Float64),
                pl.col("spy_close").cast(pl.Float64),
                pl.col("spy_ema50").cast(pl.Float64),
                pl.col("spy_ema200").cast(pl.Float64),
            ]
        )
    if bdf.is_empty():
        return rdf
    if rdf.is_empty():
        return bdf
    return bdf.join(rdf, on="date", how="outer_coalesce").sort("date")


def enrich_context(df: pl.DataFrame) -> pl.DataFrame:
    min_date = df.select(pl.col("date").min()).item()
    max_date = df.select(pl.col("date").max()).item()
    ctx = load_market_context(min_date, max_date)
    if ctx.is_empty():
        return df
    enriched = df.sort("date").join_asof(ctx.sort("date"), on="date", strategy="backward")
    daily = enriched.group_by("date").agg(
        [
            ((pl.col("overall") >= 75) & (pl.col("side") == "low")).sum().alias("base_call75_count"),
            ((pl.col("raw_new_score") >= 75) & (pl.col("side") == "low")).sum().alias("raw_call75_count"),
            ((pl.col("overall") < 75) & (pl.col("raw_new_score") >= 75) & (pl.col("side") == "low")).sum().alias("raw_promote75_count"),
        ]
    )
    return enriched.join(daily, on="date", how="left")


def trigger_expr(name: str) -> pl.Expr:
    if name == "none":
        return pl.lit(False)
    field, op, raw = name.rsplit("_", 2)
    value = float(raw)
    col = pl.col(field)
    if op == "le":
        return col <= value
    if op == "ge":
        return col >= value
    raise ValueError(f"Unsupported trigger {name}")


def guarded(df: pl.DataFrame, trigger: str, brake: str) -> pl.DataFrame:
    normal = hard_gate(df, 75).select(["symbol", "date", "new_score"]).rename({"new_score": "normal_score"})
    if brake == "hard76":
        braked = hard_gate(df, 76)
    elif brake == "ramp70_76":
        braked = ramp_gate(df, 70, 76)
    elif brake == "ramp72_76":
        braked = ramp_gate(df, 72, 76)
    else:
        raise ValueError(f"Unsupported brake {brake}")
    braked = braked.select(["symbol", "date", "new_score"]).rename({"new_score": "brake_score"})
    joined = df.join(normal, on=["symbol", "date"], how="left").join(braked, on=["symbol", "date"], how="left")
    trig = trigger_expr(trigger)
    score = pl.when(trig).then(pl.col("brake_score")).otherwise(pl.col("normal_score"))
    return finalize_scores(joined.with_columns(score.alias("new_score")))


def stat(df: pl.DataFrame, bucket_col: str, bucket: str, outcome_col: str) -> tuple[int, int, float | None]:
    sub = df.filter((pl.col("side") == "low") & (pl.col(bucket_col) == bucket) & pl.col(outcome_col).is_not_null())
    n = sub.height
    wins = sub.filter(pl.col(outcome_col) == 1).height
    return n, wins, (100.0 * wins / n if n else None)


def period_summary(df: pl.DataFrame, name: str, period_filter: pl.Expr | None = None) -> dict:
    scope = df.filter(period_filter) if period_filter is not None else df
    n75_base = scope.filter((pl.col("side") == "low") & (pl.col("overall") >= 75)).height
    n75_post = scope.filter((pl.col("side") == "low") & (pl.col("new_score") >= 75)).height
    out = {
        "name": name,
        "rows": scope.height,
        "changed": scope.filter(pl.col("changed")).height,
        "promoted75": scope.filter((pl.col("side") == "low") & (pl.col("overall") < 75) & (pl.col("new_score") >= 75)).height,
        "n75_delta": n75_post - n75_base,
        "buckets": {},
    }
    utility = 0.0
    weighted_winners = 0.0
    worst_75 = 0.0
    worst_tradable = 0.0
    for bucket in CALL_BUCKETS:
        rec = {}
        for w in WINDOWS:
            base_n, base_w, base_rate = stat(scope, "bucket", bucket, f"win_{w}d")
            post_n, post_w, post_rate = stat(scope, "new_bucket", bucket, f"win_{w}d")
            d_pp = post_rate - base_rate if post_rate is not None and base_rate is not None else None
            rec[f"wr{w}"] = {
                "base_n": base_n,
                "post_n": post_n,
                "d_n": post_n - base_n,
                "base_rate": base_rate,
                "post_rate": post_rate,
                "d_pp": d_pp,
                "d_winners": post_w - base_w,
            }
            if bucket == "75-79" and d_pp is not None:
                worst_75 = min(worst_75, d_pp)
            if bucket in TRADABLE_CALL and d_pp is not None:
                worst_tradable = min(worst_tradable, d_pp)
        base_n, base_w, base_rate = stat(scope, "bucket", bucket, "opt_result_15")
        post_n, post_w, post_rate = stat(scope, "new_bucket", bucket, "opt_result_15")
        d_pp = post_rate - base_rate if post_rate is not None and base_rate is not None else None
        rec["opt15"] = {
            "base_n": base_n,
            "post_n": post_n,
            "d_n": post_n - base_n,
            "base_rate": base_rate,
            "post_rate": post_rate,
            "d_pp": d_pp,
            "d_winners": post_w - base_w,
        }
        if bucket == "75-79" and d_pp is not None:
            worst_75 = min(worst_75, d_pp)
        if bucket in TRADABLE_CALL:
            if d_pp is not None:
                worst_tradable = min(worst_tradable, d_pp)
            weight = ALLOC_WEIGHTS[bucket]
            weighted_winners += weight * rec["wr7"]["d_winners"]
            utility += weight * rec["wr7"]["d_winners"] + 0.015 * rec["wr7"]["d_n"]
        out["buckets"][bucket] = rec
    out["weighted_incremental_wr7_winners"] = weighted_winners
    out["utility_like"] = utility
    out["worst_75_multi_or_opt_pp"] = worst_75
    out["worst_tradable_multi_or_opt_pp"] = worst_tradable
    return out


def summarize_variant(df: pl.DataFrame, name: str) -> dict:
    summary = period_summary(df, name)
    summary["yearly"] = {
        str(year): period_summary(df, name, pl.col("date").dt.year() == year)
        for year in range(2021, 2027)
    }
    return summary


def write_ranked_csv(path: Path, summaries: list[dict]) -> None:
    fields = [
        "name", "changed", "n75_delta", "promoted75", "utility_like",
        "worst_75_multi_or_opt_pp", "worst_tradable_multi_or_opt_pp",
        "y2025_n75_delta", "y2025_promoted75", "y2025_worst_75",
        "y2025_75_wr7", "y2025_75_opt15",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in summaries:
            y25 = s["yearly"].get("2025", {})
            b75 = y25.get("buckets", {}).get("75-79", {})
            w.writerow({
                "name": s["name"],
                "changed": s["changed"],
                "n75_delta": s["n75_delta"],
                "promoted75": s["promoted75"],
                "utility_like": round(s["utility_like"], 3),
                "worst_75_multi_or_opt_pp": round(s["worst_75_multi_or_opt_pp"], 3),
                "worst_tradable_multi_or_opt_pp": round(s["worst_tradable_multi_or_opt_pp"], 3),
                "y2025_n75_delta": y25.get("n75_delta"),
                "y2025_promoted75": y25.get("promoted75"),
                "y2025_worst_75": round(y25.get("worst_75_multi_or_opt_pp", 0.0), 3),
                "y2025_75_wr7": round(b75.get("wr7", {}).get("d_pp", 0.0), 3),
                "y2025_75_opt15": round(b75.get("opt15", {}).get("d_pp", 0.0), 3),
            })


def candidate_configs(df: pl.DataFrame) -> list[tuple[str, Callable[[], pl.DataFrame]]]:
    configs: list[tuple[str, Callable[[], pl.DataFrame]]] = [
        ("hard75", lambda: hard_gate(df, 75)),
        ("hard76", lambda: hard_gate(df, 76)),
        ("ramp70_76", lambda: ramp_gate(df, 70, 76)),
        ("ramp72_76", lambda: ramp_gate(df, 72, 76)),
    ]
    trigger_names = [
        "breadth_score_le_35", "breadth_score_le_45", "breadth_score_le_55",
        "breadth_score_ge_60", "breadth_score_ge_70",
        "regime_composite_le_45", "regime_composite_le_55",
        "regime_composite_ge_60",
        "vix_close_ge_18", "vix_close_ge_22",
        "base_call75_count_ge_10", "base_call75_count_ge_15", "base_call75_count_ge_20",
        "raw_promote75_count_ge_5", "raw_promote75_count_ge_10", "raw_promote75_count_ge_15",
        "raw_call75_count_ge_15", "raw_call75_count_ge_20", "raw_call75_count_ge_25",
    ]
    brakes = ["hard76", "ramp70_76", "ramp72_76"]
    for trigger in trigger_names:
        for brake in brakes:
            configs.append((f"guard_{trigger}__{brake}", lambda t=trigger, b=brake: guarded(df, t, b)))
    return configs


def rank_summaries(summaries: list[dict]) -> list[dict]:
    def key(s: dict) -> tuple:
        y25 = s["yearly"].get("2025", {})
        pass_75 = s["worst_75_multi_or_opt_pp"] >= -1.0
        y25_pass = y25.get("worst_75_multi_or_opt_pp", 0.0) >= -1.0
        n_keep = s["n75_delta"] >= 1800
        return (pass_75, y25_pass, n_keep, y25.get("promoted75", 0) <= 800, s["utility_like"])
    return sorted(summaries, key=key, reverse=True)


def build_base(signals_path: Path, priors_path: Path, variant: Variant) -> pl.DataFrame:
    signals = pl.read_parquet(signals_path).filter(pl.col("win_7d").is_not_null())
    priors = pl.read_parquet(priors_path)
    adjusted = apply_variant(signals, priors, variant)
    adjusted = adjusted.rename({"new_score": "raw_new_score"}).with_columns(
        (pl.col("raw_new_score") - pl.col("overall")).alias("raw_lift")
    )
    return enrich_context(adjusted)


def score_sweep(base: pl.DataFrame, run_dir: Path, status: Status) -> list[dict]:
    configs = candidate_configs(base)
    summaries = []
    total = len(configs)
    for i, (name, fn) in enumerate(configs, start=1):
        status.write("score_sweep", completed=i - 1, total=total, current=name)
        df = fn()
        s = summarize_variant(df, name)
        summaries.append(s)
        print(
            f"[score] {name} n75={s['n75_delta']:+,} "
            f"prom75={s['promoted75']:,} worst75={s['worst_75_multi_or_opt_pp']:+.2f} "
            f"util={s['utility_like']:+.1f}",
            flush=True,
        )
    ranked = rank_summaries(summaries)
    payload = {"ranked_names": [s["name"] for s in ranked], "summaries": summaries}
    write_json(run_dir / "guard_destination_score_results.json", payload)
    write_ranked_csv(run_dir / "guard_destination_score_ranked.csv", ranked)
    status.write("score_sweep", completed=total, total=total, best=ranked[0]["name"] if ranked else None)
    return ranked


def run_mc(
    base: pl.DataFrame,
    ranked: list[dict],
    run_dir: Path,
    status: Status,
    n_iter: int,
    windows: list[str],
    top_n: int,
    version_id: int,
    config_names: list[str] | None = None,
) -> None:
    if n_iter <= 0:
        return

    os.environ["MC_NO_DB_PERSIST"] = "1"
    os.environ["MC_WORKERS"] = os.environ.get("MC_WORKERS", "6")
    os.environ["N_ITER_OVERRIDE"] = str(n_iter)
    import monte_carlo as mc

    from experiments.temporal_echo.mc_smoke import (
        WINDOW_ALIASES,
        describe_adjustments,
        diff_summary,
        inject_echo_call_scores,
        score_map_for_window,
        summarize_run,
    )

    config_map = {name: fn for name, fn in candidate_configs(base)}
    if config_names:
        selected_names = [name for name in config_names if name in config_map]
        missing = [name for name in config_names if name not in config_map]
        if missing:
            raise SystemExit(f"Unknown --mc-configs entries: {missing}")
    else:
        if top_n <= 0:
            return
        selected = ranked[:top_n]
        selected_names = [s["name"] for s in selected]
    version = AlgorithmVersion.get_by_id(version_id)
    mc.N_ITER = n_iter
    selected_windows = [WINDOW_ALIASES[w] for w in windows]
    results = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "version": {"id": version.id, "git_commit": version.git_commit, "git_message": version.git_message},
        "n_iter": n_iter,
        "windows": windows,
        "configs": {},
    }
    total = len(selected_names) * len(selected_windows)
    done = 0
    baseline_cache = {}
    for label, d_start, d_end in selected_windows:
        print(f"[mc] baseline {label}", flush=True)
        baseline_cache[label] = summarize_run(mc.run_window(label, d_start, d_end, version))
        for name in selected_names:
            done += 1
            status.write("mc", completed=done - 1, total=total, current=f"{name}:{label}")
            df = config_map[name]()
            print(f"[mc] candidate {name} {label}", flush=True)
            score_map = score_map_for_window(df, d_start, d_end)
            desc = describe_adjustments(df, d_start, d_end)
            with inject_echo_call_scores(score_map):
                cand = summarize_run(mc.run_window(label, d_start, d_end, version))
            results["configs"].setdefault(name, {"windows": {}})["windows"][label] = {
                "date_start": d_start.isoformat(),
                "date_end": d_end.isoformat(),
                "adjustments": desc,
                "baseline": baseline_cache[label],
                "candidate": cand,
                "delta": diff_summary(baseline_cache[label], cand),
            }
            write_json(run_dir / "guard_destination_mc_partial.json", results)
    results["finished_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(run_dir / "guard_destination_mc_results.json", results)
    status.write("mc", completed=total, total=total)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", default=str(ROOT / ".cache" / "temporal_echo" / "signals_v50_1825_g1_90.parquet"))
    parser.add_argument("--priors", default=str(ROOT / ".cache" / "temporal_echo" / "priors_v50_1825_g1_90.parquet"))
    parser.add_argument("--variant-file", default=str(ROOT / ".cache" / "temporal_echo" / "runs" / "20260511_v50_drill_direct_retry" / "gate75_top30.json"))
    parser.add_argument("--variant-rank", type=int, default=3)
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--mc-iter", type=int, default=0)
    parser.add_argument("--mc-top", type=int, default=0)
    parser.add_argument("--mc-configs", default="", help="Comma-separated explicit config names for MC")
    parser.add_argument("--mc-windows", default="22-now,2025")
    parser.add_argument("--version-id", type=int, default=50)
    parser.add_argument("--skip-score", action="store_true", help="Skip score ranking; intended for explicit --mc-configs runs")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir) if args.run_dir else ROOT / ".cache" / "temporal_echo" / "runs" / f"guard_destination_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    status = Status(run_dir)
    try:
        status.write("load", variant_file=args.variant_file)
        variant = load_variant(Path(args.variant_file), args.variant_rank)
        print(f"[load] variant={asdict(variant)}", flush=True)
        base = build_base(Path(args.signals), Path(args.priors), variant)
        print(f"[load] rows={base.height:,} cols={len(base.columns)}", flush=True)
        ranked = [] if args.skip_score else score_sweep(base, run_dir, status)
        windows = [w.strip() for w in args.mc_windows.split(",") if w.strip()]
        mc_configs = [x.strip() for x in args.mc_configs.split(",") if x.strip()]
        run_mc(base, ranked, run_dir, status, args.mc_iter, windows, args.mc_top, args.version_id, mc_configs)
        write_json(run_dir / "done.json", {
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "best_score_config": ranked[0]["name"] if ranked else None,
            "run_dir": str(run_dir),
        })
        status.write("done", state="done", best=ranked[0]["name"] if ranked else None)
        return 0
    except Exception as exc:
        write_json(run_dir / "failed.json", {
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "error": repr(exc),
            "run_dir": str(run_dir),
        })
        status.write("failed", state="failed", error=repr(exc))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
