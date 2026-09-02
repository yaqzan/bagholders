from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polars as pl

from experiments.temporal_echo.sweep import TRADABLE_CALL, Variant, apply_variant, bucket_expr

DRILL_WINNER = Variant(
    tau=33.53068369258105,
    score_power=0.6484629680603173,
    norm=1.6244582174624052,
    alpha=1.1098625228335859,
    max_lift=5.324483581447521,
    target=82.0,
    min_echo=0.03564202937433027,
    w7=0.13129125416763923,
    w15=0.04059352676568437,
    w30=1.1451706248544933,
    w60=0.6634847709237296,
    loss_penalty=0.22488370555188553,
    fizzler_penalty=0.8123506166080852,
    allow_puts=False,
)

WINDOWS = (3, 5, 7, 15, 30)


def _variant_from_dict(raw: dict) -> Variant:
    data = raw.get("variant", raw)
    return Variant(**{field: data[field] for field in Variant.__dataclass_fields__})


def load_variant(path: Path | None, rank: int) -> Variant:
    if path is None:
        return DRILL_WINNER
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


def _stats(df: pl.DataFrame, bucket_col: str, bucket: str, outcome_col: str) -> dict:
    sub = df.filter((pl.col("side") == "low") & (pl.col(bucket_col) == bucket) & pl.col(outcome_col).is_not_null())
    n = sub.height
    wins = sub.filter(pl.col(outcome_col) == 1).height
    return {"n": n, "wins": wins, "rate": (100.0 * wins / n if n else None)}


def _return_stats(df: pl.DataFrame, bucket_col: str, bucket: str, ret_col: str) -> dict:
    sub = df.filter((pl.col("side") == "low") & (pl.col(bucket_col) == bucket) & pl.col(ret_col).is_not_null())
    n = sub.height
    if n == 0:
        return {"n": 0, "avg": None, "median": None}
    return {
        "n": n,
        "avg": sub.select(pl.col(ret_col).mean()).item(),
        "median": sub.select(pl.col(ret_col).median()).item(),
    }


def validate(signals_path: Path, priors_path: Path, out_dir: Path, variant: Variant, min_new_score: int | None) -> dict:
    signals = pl.read_parquet(signals_path).filter(pl.col("win_7d").is_not_null())
    priors = pl.read_parquet(priors_path)
    df = apply_min_new_score_gate(apply_variant(signals, priors, variant), min_new_score)
    changed = df.filter(pl.col("changed")).height
    promoted = df.filter(
        (pl.col("side") == "low")
        & (pl.col("bucket").is_in(["50-54", "55-59", "60-64", "65-69", "70-74"]))
        & (pl.col("new_bucket").is_in(list(TRADABLE_CALL)))
    ).height

    rows = []
    for bucket in TRADABLE_CALL:
        rec = {"bucket": bucket}
        for w in WINDOWS:
            base = _stats(df, "bucket", bucket, f"win_{w}d")
            post = _stats(df, "new_bucket", bucket, f"win_{w}d")
            rec[f"wr{w}"] = {
                "base_n": base["n"],
                "post_n": post["n"],
                "d_n": post["n"] - base["n"],
                "base_rate": base["rate"],
                "post_rate": post["rate"],
                "d_pp": (post["rate"] - base["rate"]) if post["rate"] is not None and base["rate"] is not None else None,
            }
            base_ret = _return_stats(df, "bucket", bucket, f"ret_{w}d")
            post_ret = _return_stats(df, "new_bucket", bucket, f"ret_{w}d")
            rec[f"ret{w}"] = {
                "base_n": base_ret["n"],
                "post_n": post_ret["n"],
                "d_n": post_ret["n"] - base_ret["n"],
                "base_avg": base_ret["avg"],
                "post_avg": post_ret["avg"],
                "d_avg": (post_ret["avg"] - base_ret["avg"]) if post_ret["avg"] is not None and base_ret["avg"] is not None else None,
                "base_median": base_ret["median"],
                "post_median": post_ret["median"],
                "d_median": (post_ret["median"] - base_ret["median"]) if post_ret["median"] is not None and base_ret["median"] is not None else None,
            }
        base_opt = _stats(df, "bucket", bucket, "opt_result_15")
        post_opt = _stats(df, "new_bucket", bucket, "opt_result_15")
        rec["opt15"] = {
            "base_n": base_opt["n"],
            "post_n": post_opt["n"],
            "d_n": post_opt["n"] - base_opt["n"],
            "base_rate": base_opt["rate"],
            "post_rate": post_opt["rate"],
            "d_pp": (post_opt["rate"] - base_opt["rate"]) if post_opt["rate"] is not None and base_opt["rate"] is not None else None,
        }
        rows.append(rec)

    summary = {
        "variant": asdict(variant),
        "min_new_score": min_new_score,
        "signals": len(signals),
        "priors": len(priors),
        "changed": changed,
        "promoted_to_trade": promoted,
        "rows": rows,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / "candidate_validation.json"
    md = out_dir / "candidate_validation.md"
    js.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "# Temporal Echo Candidate Validation",
        "",
        f"- signals: {len(signals):,}",
        f"- priors: {len(priors):,}",
        f"- changed rows: {changed:,}",
        f"- promoted-to-trade: {promoted:,}",
        "",
        "| bucket | dN | dWR3 | dWR5 | dWR7 | dWR15 | dWR30 | dOptTP15 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        d_n = r["wr7"]["d_n"]

        def fmt(key: str) -> str:
            d = r[key]["d_pp"]
            return "n/a" if d is None else f"{d:+.2f}pp"

        lines.append(
            f"| {r['bucket']} | {d_n:+,} | {fmt('wr3')} | {fmt('wr5')} | {fmt('wr7')} | "
            f"{fmt('wr15')} | {fmt('wr30')} | {fmt('opt15')} |"
        )
    lines.extend(
        [
            "",
            "| bucket | dRet3 | dRet5 | dRet7 | dRet15 | dRet30 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for r in rows:
        def fmt_ret(key: str) -> str:
            d = r[key]["d_avg"]
            return "n/a" if d is None else f"{d:+.3f}pp"

        lines.append(
            f"| {r['bucket']} | {fmt_ret('ret3')} | {fmt_ret('ret5')} | {fmt_ret('ret7')} | "
            f"{fmt_ret('ret15')} | {fmt_ret('ret30')} |"
        )
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[validate] wrote {md}", flush=True)
    print(f"[validate] wrote {js}", flush=True)
    return {"candidate_validation_md": str(md), "candidate_validation_json": str(js), **summary}


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", required=True)
    parser.add_argument("--priors", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--variant-file", default=None)
    parser.add_argument("--variant-rank", type=int, default=1)
    parser.add_argument("--min-new-score", type=int, default=None)
    args = parser.parse_args(argv)
    variant = load_variant(Path(args.variant_file) if args.variant_file else None, args.variant_rank)
    return validate(Path(args.signals), Path(args.priors), Path(args.out_dir), variant, args.min_new_score)


if __name__ == "__main__":
    main()
