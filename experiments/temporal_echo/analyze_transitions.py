from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polars as pl

from experiments.temporal_echo.sweep import Variant, aggregate_echo

PERIODS = (1, 3, 5, 7, 15, 30, 60, 90)

STRICT_REFERENCE_VARIANT = Variant(
    tau=32.57839933317171,
    score_power=0.8650331075499574,
    norm=1.630249748194968,
    alpha=0.9570882509275846,
    max_lift=5.666403955707368,
    target=80.0,
    min_echo=0.05962736940450597,
    w7=0.2703203500421868,
    w15=0.06078496070870909,
    w30=0.8496621742131409,
    w60=0.9184520076468756,
    loss_penalty=0.3270416480352185,
    fizzler_penalty=0.6470899467981728,
    allow_puts=False,
)


def _pct(n: int, d: int) -> str:
    return "n/a" if d <= 0 else f"{100.0 * n / d:.1f}%"


def _rate(df: pl.DataFrame, expr: pl.Expr, outcome_col: str) -> dict:
    sub = df.filter(expr & pl.col(outcome_col).is_not_null())
    n = len(sub)
    wins = sub.filter(pl.col(outcome_col) == 1).height
    return {"n": n, "wins": wins, "rate": (100.0 * wins / n if n else None)}


def add_pattern_columns(priors: pl.DataFrame) -> pl.DataFrame:
    def bit_col(w: int) -> pl.Expr:
        c = pl.col(f"prior_win_{w}d")
        return (
            pl.when(c.is_null())
            .then(pl.lit("?"))
            .when(c == 1)
            .then(pl.lit("1"))
            .otherwise(pl.lit("0"))
        )

    return priors.with_columns(
        [
            pl.concat_str([bit_col(7), bit_col(15), bit_col(30), bit_col(60)], separator="").alias("p_7_15_30_60"),
            pl.when(pl.col("gap_days") <= 7)
            .then(pl.lit("01-07"))
            .when(pl.col("gap_days") <= 15)
            .then(pl.lit("08-15"))
            .when(pl.col("gap_days") <= 30)
            .then(pl.lit("16-30"))
            .when(pl.col("gap_days") <= 60)
            .then(pl.lit("31-60"))
            .otherwise(pl.lit("61-90"))
            .alias("gap_band"),
            pl.when(pl.col("side") == "low").then(pl.lit("call")).otherwise(pl.lit("put")).alias("trade_side"),
        ]
    )


def write_transition_report(signals_path: Path, priors_path: Path, out_dir: Path) -> dict:
    signals = pl.read_parquet(signals_path)
    priors = add_pattern_columns(pl.read_parquet(priors_path))
    current_cols = ["symbol", "date", "side", "raw_score", "overall", "bucket", "win_7d", "win_15d", "win_30d", "opt_result_15"]
    joined = priors.join(signals.select(current_cols), on=["symbol", "date", "side"], how="left")

    md: list[str] = []
    md.append("# Temporal Echo Transition Stats\n")
    md.append(f"- signals: {len(signals):,}")
    md.append(f"- prior pairs: {len(priors):,}")
    md.append("")

    summary: dict = {"signals": len(signals), "prior_pairs": len(priors), "tables": {}}

    md.append("## Unique Current-Signal Echo Deciles\n")
    md.append(
        "Reference formula: best <=1pp screen candidate from the first broad sweep. "
        "Rows are unique current call signals with resolved outcomes, not prior-pair rows."
    )
    echo = aggregate_echo(priors, STRICT_REFERENCE_VARIANT)
    echoed = (
        signals.join(echo, on=["symbol", "date", "side"], how="left")
        .with_columns(pl.col("echo").fill_null(0.0))
        .filter((pl.col("side") == "low") & pl.col("win_7d").is_not_null())
        .sort("echo")
        .with_row_index("echo_rank")
    )
    if len(echoed) > 0:
        echoed = echoed.with_columns(
            ((pl.col("echo_rank") * 10 / len(echoed)).floor() + 1)
            .clip(1, 10)
            .cast(pl.Int64)
            .alias("echo_decile")
        )
        deciles = (
            echoed.group_by("echo_decile")
            .agg(
                [
                    pl.len().alias("n"),
                    pl.col("echo").min().alias("echo_min"),
                    pl.col("echo").max().alias("echo_max"),
                    pl.col("win_7d").mean().alias("wr7"),
                    pl.col("win_15d").mean().alias("wr15"),
                    pl.col("win_30d").mean().alias("wr30"),
                    pl.col("opt_result_15").mean().alias("opt15"),
                    (pl.col("raw_score") >= 75).sum().alias("already_tradable"),
                ]
            )
            .sort("echo_decile")
        )
        md.append("| echo decile | N | echo range | WR7 | WR15 | WR30 | OptTP15 | already >=75 |")
        md.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
        decile_rows = []
        for r in deciles.iter_rows(named=True):
            row = {
                "decile": int(r["echo_decile"]),
                "n": int(r["n"]),
                "echo_min": float(r["echo_min"]),
                "echo_max": float(r["echo_max"]),
                "wr7": float(r["wr7"]) * 100.0 if r["wr7"] is not None else None,
                "wr15": float(r["wr15"]) * 100.0 if r["wr15"] is not None else None,
                "wr30": float(r["wr30"]) * 100.0 if r["wr30"] is not None else None,
                "opt15": float(r["opt15"]) * 100.0 if r["opt15"] is not None else None,
                "already_tradable": int(r["already_tradable"]),
            }
            decile_rows.append(row)
            md.append(
                f"| {row['decile']} | {row['n']:,} | {row['echo_min']:.3f}..{row['echo_max']:.3f} | "
                f"{row['wr7']:.1f}% | {row['wr15']:.1f}% | {row['wr30']:.1f}% | "
                f"{row['opt15']:.1f}% | {row['already_tradable']:,} |"
            )
        summary["echo_deciles"] = decile_rows
    md.append("")

    for side_name, side_value in (("CALL", "low"), ("PUT", "high")):
        side_df = joined.filter(pl.col("side") == side_value)
        md.append(f"## {side_name} Prior Pattern -> Current Outcomes\n")
        rows = []
        grouped = (
            side_df.group_by("p_7_15_30_60")
            .agg(
                [
                    pl.len().alias("pairs"),
                    pl.col("win_7d").drop_nulls().count().alias("n_wr7"),
                    pl.col("win_7d").sum().alias("wins_wr7"),
                    pl.col("win_15d").drop_nulls().count().alias("n_wr15"),
                    pl.col("win_15d").sum().alias("wins_wr15"),
                    pl.col("win_30d").drop_nulls().count().alias("n_wr30"),
                    pl.col("win_30d").sum().alias("wins_wr30"),
                    pl.col("opt_result_15").drop_nulls().count().alias("n_opt15"),
                    pl.col("opt_result_15").sum().alias("wins_opt15"),
                ]
            )
            .sort("pairs", descending=True)
        )
        md.append("| pattern W7/W15/W30/W60 | pairs | WR7 | WR15 | WR30 | OptTP15 |")
        md.append("|---|---:|---:|---:|---:|---:|")
        for r in grouped.iter_rows(named=True):
            row = {
                "pattern": r["p_7_15_30_60"],
                "pairs": int(r["pairs"]),
                "wr7": (100.0 * r["wins_wr7"] / r["n_wr7"] if r["n_wr7"] else None),
                "wr15": (100.0 * r["wins_wr15"] / r["n_wr15"] if r["n_wr15"] else None),
                "wr30": (100.0 * r["wins_wr30"] / r["n_wr30"] if r["n_wr30"] else None),
                "opt15": (100.0 * r["wins_opt15"] / r["n_opt15"] if r["n_opt15"] else None),
            }
            rows.append(row)
            md.append(
                f"| `{row['pattern']}` | {row['pairs']:,} | "
                f"{_pct(int(r['wins_wr7'] or 0), int(r['n_wr7'] or 0))} | "
                f"{_pct(int(r['wins_wr15'] or 0), int(r['n_wr15'] or 0))} | "
                f"{_pct(int(r['wins_wr30'] or 0), int(r['n_wr30'] or 0))} | "
                f"{_pct(int(r['wins_opt15'] or 0), int(r['n_opt15'] or 0))} |"
            )
        md.append("")
        summary["tables"][side_name.lower()] = rows

    md.append("## Conditional Echo Checks\n")
    checks = [
        ("prior W7 win", pl.col("prior_win_7d") == 1),
        ("prior W7 miss", pl.col("prior_win_7d") == 0),
        ("prior W15 win", pl.col("prior_win_15d") == 1),
        ("prior W30 win", pl.col("prior_win_30d") == 1),
        ("prior W60 win", pl.col("prior_win_60d") == 1),
        ("early fizzler W7/W15 win then W30 miss", (pl.col("prior_win_7d") == 1) & (pl.col("prior_win_15d") == 1) & (pl.col("prior_win_30d") == 0)),
    ]
    md.append("| side | condition | N | current WR7 | current WR15 | opt TP15 |")
    md.append("|---|---|---:|---:|---:|---:|")
    summary["checks"] = []
    for side_name, side_value in (("call", "low"), ("put", "high")):
        side_df = joined.filter(pl.col("side") == side_value)
        for label, expr in checks:
            n = side_df.filter(expr).height
            wr7 = _rate(side_df, expr, "win_7d")
            wr15 = _rate(side_df, expr, "win_15d")
            opt15 = _rate(side_df, expr, "opt_result_15")
            summary["checks"].append({"side": side_name, "condition": label, "n": n, "wr7": wr7, "wr15": wr15, "opt15": opt15})
            md.append(
                f"| {side_name} | {label} | {n:,} | "
                f"{wr7['rate']:.1f}% | " if wr7["rate"] is not None else f"| {side_name} | {label} | {n:,} | n/a | "
            )
            if wr7["rate"] is not None:
                md[-1] += f"{wr15['rate']:.1f}% | " if wr15["rate"] is not None else "n/a | "
                md[-1] += f"{opt15['rate']:.1f}% |" if opt15["rate"] is not None else "n/a |"
    md.append("")

    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "transition_stats.md"
    js_path = out_dir / "transition_stats.json"
    md_path.write_text("\n".join(md), encoding="utf-8")
    js_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[stats] wrote {md_path}", flush=True)
    print(f"[stats] wrote {js_path}", flush=True)
    return {"transition_md": str(md_path), "transition_json": str(js_path)}


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", required=True)
    parser.add_argument("--priors", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    return write_transition_report(Path(args.signals), Path(args.priors), Path(args.out_dir))


if __name__ == "__main__":
    main()
