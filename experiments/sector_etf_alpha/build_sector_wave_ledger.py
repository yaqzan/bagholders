"""Build a sector-wave DD ledger from sector_etf_alpha/dd_probe tapes.

This is the Stage A data foundation for portfolio-scale sector ETF alpha work.
It joins MC trade tape to sector ETF features and reconstructs open sector
exposure at each trade entry.

Output:
  experiments/sector_etf_alpha/dd_probe/sector_wave_ledger.parquet
"""
from __future__ import annotations

import argparse
import heapq
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "experiments" / "sector_etf_alpha" / "dd_probe"
COHORT = ROOT / ".cache" / "sector_etf_alpha" / "cohort_v46_1825.parquet"
STOCK_META = ROOT / ".cache" / "sector_etf_screen" / "stock_meta.parquet"

WINDOWS = ["2022", "2024", "22-now", "5y"]
VARIANTS = ["BASELINE", "V4"]


def load_tapes(variants: list[str], windows: list[str]) -> tuple[pl.DataFrame, pl.DataFrame]:
    tapes = []
    episodes = []
    for variant in variants:
        for window in windows:
            tape_path = PROBE / f"{variant}_{window}_tape.parquet"
            ep_path = PROBE / f"{variant}_{window}_episodes.parquet"
            if not tape_path.exists() or not ep_path.exists():
                print(f"[skip] missing {variant} {window}", flush=True)
                continue
            tapes.append(
                pl.read_parquet(tape_path).with_columns([
                    pl.lit(variant).alias("variant"),
                    pl.lit(window).alias("window"),
                ])
            )
            episodes.append(
                pl.read_parquet(ep_path).with_columns([
                    pl.lit(variant).alias("variant"),
                    pl.lit(window).alias("window"),
                ])
            )
            print(f"[load] {variant} {window}: tape={tape_path.stat().st_size:,} bytes", flush=True)
    if not tapes:
        raise SystemExit("no tapes loaded")
    return pl.concat(tapes, how="vertical_relaxed"), pl.concat(episodes, how="vertical_relaxed")


def add_features(tape: pl.DataFrame, episodes: pl.DataFrame) -> pl.DataFrame:
    feats = pl.read_parquet(COHORT)
    feat_cols = [
        "symbol", "date", "sector", "industry", "market_cap", "sector_etf",
        "sec_etf_pct_ema50", "sec_etf_rsi", "sec_etf_macd",
        "sec_etf_ret_5d", "sec_etf_ret_20d",
        "stock_ret_5d", "stock_ret_10d", "stock_ret_20d",
        "breadth_score",
    ]
    feats = (
        feats.select([c for c in feat_cols if c in feats.columns])
        .with_columns(pl.col("date").cast(pl.Utf8).str.strptime(pl.Date, strict=False).alias("date"))
        .unique(["symbol", "date"])
    )

    meta = pl.read_parquet(STOCK_META)
    meta_cols = [c for c in ["symbol", "sector", "industry", "market_cap"] if c in meta.columns]
    meta = meta.select(meta_cols).unique("symbol")

    out = tape.with_row_index("row_id")
    out = out.join(
        feats,
        left_on=["sym_id", "entry_date"],
        right_on=["symbol", "date"],
        how="left",
    )
    # Backfill sector metadata for unresolved/unmatched rows.
    out = out.join(meta, left_on="sym_id", right_on="symbol", how="left", suffix="_meta")
    out = out.with_columns([
        pl.coalesce([pl.col("sector"), pl.col("sector_meta"), pl.lit("unknown")]).alias("sector"),
        pl.coalesce([pl.col("industry"), pl.col("industry_meta"), pl.lit("unknown")]).alias("industry"),
        pl.coalesce([pl.col("market_cap"), pl.col("market_cap_meta")]).alias("market_cap"),
    ])

    out = out.with_columns([
        (pl.col("premium_cost") * pl.col("option_pnl")).alias("pnl_dollars"),
        (pl.col("stock_ret_5d") - pl.col("sec_etf_ret_5d")).alias("stock_rs_5d"),
        ((pl.col("sec_etf_rsi") - 50.0) / 25.0).clip(-1.0, 1.0).alias("sector_rsi_phase"),
        (pl.col("sec_etf_pct_ema50") / 8.0).clip(-1.0, 1.0).alias("sector_ema_phase"),
    ])
    out = out.with_columns(
        ((pl.col("sector_rsi_phase").fill_null(0.0) + pl.col("sector_ema_phase").fill_null(0.0)) / 2.0)
        .alias("sector_phase")
    )

    worst = episodes.filter(pl.col("rank") == 0).select(
        "variant", "window", "seed",
        pl.col("start_date").alias("ep_start"),
        pl.col("trough_date").alias("ep_trough"),
        (pl.col("peak_value") - pl.col("trough_value")).alias("ep_loss_dollars"),
        pl.col("magnitude").alias("ep_magnitude"),
    )
    out = out.join(worst, on=["variant", "window", "seed"], how="left")
    out = out.with_columns([
        ((pl.col("entry_date") <= pl.col("ep_trough").cast(pl.Date))
         & (pl.col("exit_date") >= pl.col("ep_start").cast(pl.Date)))
        .fill_null(False).alias("in_worst_episode"),
    ])
    seed_ep_pnl = (
        out.filter(pl.col("in_worst_episode"))
        .group_by(["variant", "window", "seed"])
        .agg(pl.col("pnl_dollars").sum().alias("seed_ep_total_pnl"))
    )
    out = out.join(seed_ep_pnl, on=["variant", "window", "seed"], how="left")
    out = out.with_columns(
        pl.when(pl.col("in_worst_episode") & (pl.col("seed_ep_total_pnl") != 0))
        .then(pl.col("pnl_dollars") / pl.col("seed_ep_total_pnl"))
        .otherwise(0.0)
        .alias("dd_contribution_share")
    )

    out = out.with_columns([
        pl.when(pl.col("entry_dd") < 0.05).then(pl.lit("none"))
          .when(pl.col("entry_dd") < 0.20).then(pl.lit("shallow"))
          .when(pl.col("entry_dd") < 0.40).then(pl.lit("mid"))
          .otherwise(pl.lit("deep"))
          .alias("entry_dd_band"),
        pl.when(pl.col("sec_etf_rsi") < 30).then(pl.lit("rsi_lt30"))
          .when(pl.col("sec_etf_rsi") < 45).then(pl.lit("rsi_30_45"))
          .when(pl.col("sec_etf_rsi") <= 55).then(pl.lit("rsi_45_55"))
          .when(pl.col("sec_etf_rsi") <= 70).then(pl.lit("rsi_55_70"))
          .otherwise(pl.lit("rsi_gt70"))
          .alias("sector_rsi_bin"),
        pl.when(pl.col("sector_phase") < -0.60).then(pl.lit("phase_deep_oversold"))
          .when(pl.col("sector_phase") < -0.25).then(pl.lit("phase_oversold"))
          .when(pl.col("sector_phase") <= 0.25).then(pl.lit("phase_mid"))
          .when(pl.col("sector_phase") <= 0.60).then(pl.lit("phase_overheated"))
          .otherwise(pl.lit("phase_deep_overheated"))
          .alias("sector_phase_bin"),
        pl.when(pl.col("stock_rs_5d").abs() < 0.03).then(pl.lit("rs_lockstep"))
          .when(pl.col("stock_rs_5d").abs() < 0.07).then(pl.lit("rs_moderate"))
          .otherwise(pl.lit("rs_extreme"))
          .alias("stock_rs_abs_bin"),
    ])
    return out


def _exposure_for_group(rows: list[tuple]) -> list[tuple[int, float, int]]:
    """Return row_id, open_premium, open_count for a sorted group.

    rows tuple: (row_id, entry_date, exit_date, premium_cost)
    """
    heap: list[tuple[str, float]] = []
    open_premium = 0.0
    out = []
    for row_id, entry_date, exit_date, premium in rows:
        while heap and heap[0][0] <= entry_date:
            _, old_p = heapq.heappop(heap)
            open_premium -= old_p
        out.append((row_id, open_premium, len(heap)))
        heapq.heappush(heap, (exit_date, premium))
        open_premium += premium
    return out


def add_open_exposure(df: pl.DataFrame) -> pl.DataFrame:
    """Reconstruct open sector exposure at each entry.

    Adds side-specific and gross sector exposure, normalized by entry_value.
    """
    print("[exposure] reconstruct side-sector exposure", flush=True)
    rows_side = []
    side_groups = df.select([
        "variant", "window", "seed", "side", "sector",
        "row_id", "entry_date", "exit_date", "premium_cost",
    ]).sort(["variant", "window", "seed", "side", "sector", "entry_date"]).partition_by(
        ["variant", "window", "seed", "side", "sector"],
        as_dict=False,
        include_key=False,
    )
    for g in side_groups:
        rows = list(g.select(["row_id", "entry_date", "exit_date", "premium_cost"]).iter_rows())
        rows_side.extend(_exposure_for_group(rows))
    side_df = pl.DataFrame(rows_side, schema=["row_id", "open_side_sector_premium", "open_side_sector_count"], orient="row")

    print("[exposure] reconstruct gross sector exposure", flush=True)
    rows_total = []
    total_groups = df.select([
        "variant", "window", "seed", "sector",
        "row_id", "entry_date", "exit_date", "premium_cost",
    ]).sort(["variant", "window", "seed", "sector", "entry_date"]).partition_by(
        ["variant", "window", "seed", "sector"],
        as_dict=False,
        include_key=False,
    )
    for g in total_groups:
        rows = list(g.select(["row_id", "entry_date", "exit_date", "premium_cost"]).iter_rows())
        rows_total.extend(_exposure_for_group(rows))
    total_df = pl.DataFrame(rows_total, schema=["row_id", "open_total_sector_premium", "open_total_sector_count"], orient="row")

    out = df.join(side_df, on="row_id", how="left").join(total_df, on="row_id", how="left")
    out = out.with_columns([
        (pl.col("open_side_sector_premium").fill_null(0.0) / pl.col("entry_value")).alias("open_side_sector_exposure"),
        (pl.col("open_total_sector_premium").fill_null(0.0) / pl.col("entry_value")).alias("open_total_sector_exposure"),
    ])
    out = out.with_columns([
        pl.when(pl.col("open_side_sector_exposure") < 0.10).then(pl.lit("exp_lt10"))
          .when(pl.col("open_side_sector_exposure") < 0.20).then(pl.lit("exp_10_20"))
          .when(pl.col("open_side_sector_exposure") < 0.35).then(pl.lit("exp_20_35"))
          .when(pl.col("open_side_sector_exposure") < 0.50).then(pl.lit("exp_35_50"))
          .otherwise(pl.lit("exp_ge50"))
          .alias("open_side_sector_exp_bin"),
        pl.when(pl.col("open_total_sector_exposure") < 0.10).then(pl.lit("gross_lt10"))
          .when(pl.col("open_total_sector_exposure") < 0.20).then(pl.lit("gross_10_20"))
          .when(pl.col("open_total_sector_exposure") < 0.35).then(pl.lit("gross_20_35"))
          .when(pl.col("open_total_sector_exposure") < 0.50).then(pl.lit("gross_35_50"))
          .otherwise(pl.lit("gross_ge50"))
          .alias("open_total_sector_exp_bin"),
    ])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--windows", default=",".join(WINDOWS))
    ap.add_argument("--out", default=str(PROBE / "sector_wave_ledger.parquet"))
    args = ap.parse_args()

    variants = [x.strip() for x in args.variants.split(",") if x.strip()]
    windows = [x.strip() for x in args.windows.split(",") if x.strip()]
    tape, episodes = load_tapes(variants, windows)
    print(f"[join] tapes={tape.height:,} episodes={episodes.height:,}", flush=True)
    ledger = add_features(tape, episodes)
    ledger = add_open_exposure(ledger)
    out_path = Path(args.out)
    ledger.write_parquet(out_path)
    print(f"[save] {out_path} rows={ledger.height:,} cols={len(ledger.columns)}", flush=True)
    print(ledger.group_by(["variant", "window"]).len().sort(["variant", "window"]), flush=True)


if __name__ == "__main__":
    main()
