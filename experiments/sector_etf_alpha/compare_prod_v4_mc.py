"""Seed-aligned production vs raw V4 MC comparison.

This answers the ship question directly: does the sector ETF alpha variant beat
the currently active production scores at the portfolio layer, before adding any
sector-allocation wrapper?

Experiment-local only. V4 is applied by patching signal scores at load time.
No production scoring or portfolio code is modified.

Output:
  experiments/sector_etf_alpha/dd_probe/prod_vs_v4_mc.csv
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from database.models.core import AlgorithmVersion
from experiments.sector_etf_alpha.sector_dd_probe import build_lookup, patch_signal_loaders

OUT = ROOT / "experiments" / "sector_etf_alpha" / "dd_probe" / "prod_vs_v4_mc.csv"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="2022,2024,22-now,5y")
    ap.add_argument("--variants", default="PROD,V4")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-mp", action="store_true")
    args = ap.parse_args()

    os.environ["N_ITER_OVERRIDE"] = str(args.n)
    os.environ["MC_NO_DB_PERSIST"] = "1"
    os.environ["MC_TRADE_TAPE"] = "0"
    os.environ["REALLOC_STRATEGY"] = ""
    if args.no_mp:
        os.environ["MC_NO_MP"] = "1"

    import monte_carlo as mc

    windows = [x.strip() for x in args.windows.split(",") if x.strip()]
    variants = [x.strip().upper() for x in args.variants.split(",") if x.strip()]
    wanted = set(windows)
    mc_windows = [w for w in mc.WINDOWS if w[0] in wanted]
    version = AlgorithmVersion.get_active_scores_version()
    out = Path(args.out).resolve()
    rows = []

    orig_call, orig_put = mc.load_signals, mc.load_put_signals
    lookup = None

    def flush_rows() -> None:
        if rows:
            out.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame(rows).write_csv(out)

    try:
        for variant in variants:
            mc.load_signals = orig_call
            mc.load_put_signals = orig_put
            if variant == "V4":
                if lookup is None:
                    lookup = build_lookup("V4")
                patch_signal_loaders(mc, lookup)
            elif variant != "PROD":
                raise SystemExit(f"unknown variant {variant}")

            print(f"\n[variant] {variant}", flush=True)
            for label, d_start, d_end in mc_windows:
                t0 = time.time()
                row = dict(mc.run_window(label, d_start, d_end, version)["seeded"])
                row["variant"] = variant
                row["window"] = label
                row["n"] = args.n
                rows.append(row)
                flush_rows()
                print(
                    f"  {label}: mean={row['mean_ret']:+.1f}% med={row['med_ret']:+.1f}% "
                    f"worstDD={row['worst_dd']:.1f}% meanDD={row['mean_dd']:.1f}% "
                    f"calls={row['call_trades']:.1f} puts={row['put_trades']:.1f} "
                    f"elapsed={time.time() - t0:.0f}s",
                    flush=True,
                )
    finally:
        mc.load_signals = orig_call
        mc.load_put_signals = orig_put

    flush_rows()
    try:
        shown = out.relative_to(ROOT)
    except ValueError:
        shown = out
    print(f"\n[save] {shown} rows={len(rows):,}", flush=True)

    df = pl.DataFrame(rows)
    if {"PROD", "V4"}.issubset(set(variants)):
        prod = df.filter(pl.col("variant") == "PROD").select([
            "window",
            pl.col("mean_ret").alias("prod_mean_ret"),
            pl.col("med_ret").alias("prod_med_ret"),
            pl.col("worst_dd").alias("prod_worst_dd"),
            pl.col("mean_dd").alias("prod_mean_dd"),
        ])
        delta = (
            df.filter(pl.col("variant") == "V4")
            .join(prod, on="window")
            .with_columns([
                (pl.col("mean_ret") - pl.col("prod_mean_ret")).alias("d_mean_ret"),
                (pl.col("med_ret") - pl.col("prod_med_ret")).alias("d_med_ret"),
                (pl.col("worst_dd") - pl.col("prod_worst_dd")).alias("d_worst_dd"),
                (pl.col("mean_dd") - pl.col("prod_mean_dd")).alias("d_mean_dd"),
            ])
            .select([
                "window", "d_mean_ret", "d_med_ret", "d_worst_dd", "d_mean_dd",
                "mean_ret", "med_ret", "worst_dd", "mean_dd",
                "prod_mean_ret", "prod_med_ret", "prod_worst_dd", "prod_mean_dd",
            ])
        )
        delta_path = out.with_name(out.stem + "_deltas.csv")
        delta.write_csv(delta_path)
        print(delta, flush=True)


if __name__ == "__main__":
    main()
