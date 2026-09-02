"""Assess whether production scores carry directional WR on sector ETFs.

This is a Stage 1 diagnostic only. It does not alter scoring; it measures
existing Score.overall rows at lower ETF-friendly thresholds and compares them
with stock scores on the same generic barrier cache semantics.

Outputs:
  experiments/sector_etf_alpha/dd_probe/etf_score_thresholds_v{version}.csv
  experiments/sector_etf_alpha/dd_probe/etf_score_by_symbol_v{version}.csv
  experiments/sector_etf_alpha/dd_probe/etf_score_buckets_v{version}.csv
  experiments/sector_etf_alpha/dd_probe/etf_score_ranges_v{version}.csv
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from database.barrier_cache import compute_outcomes_for_symbol
from database.trader_database import DB


VERSION_ID = 46
LOOKBACK_DAYS = 1825
PERIODS = [7, 15, 30]
CALL_THRESHOLDS = [60, 65, 70, 75, 80, 85, 90]
PUT_THRESHOLDS = [40, 35, 30, 25, 20, 15]

SECTOR_ETFS = ["XLK", "XLV", "XLY", "XLI", "XLB", "XLRE", "XLF", "XLE", "XLP", "XLU", "XLC"]
BROAD_ETFS = ["SPY", "QQQ", "SOXX"]
ETF_SYMBOLS = SECTOR_ETFS + BROAD_ETFS

OUT_DIR = ROOT / "experiments" / "sector_etf_alpha" / "dd_probe"
CACHE_DIR = ROOT / ".cache" / "sector_etf_alpha"
RUNNER_BARRIER_PATH = ROOT / ".cache" / "runner" / "barriers_1825.parquet"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _today() -> date:
    return date.today()


def _sql_symbols(symbols: list[str]) -> str:
    return "', '".join(symbols)


def fetch_etf_all_scores(version_id: int, start_date: str, end_date: str) -> pl.DataFrame:
    cache_pq = CACHE_DIR / f"etf_all_scores_v{version_id}_{start_date}_{end_date}.parquet"
    if cache_pq.exists():
        return pl.read_parquet(cache_pq)

    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=600000")
    syms = _sql_symbols(ETF_SYMBOLS)
    cur = DB.execute_sql(
        f"""
        SELECT symbol, date, overall
        FROM scores
        WHERE version_id = {version_id}
          AND symbol IN ('{syms}')
          AND date >= '{start_date}' AND date <= '{end_date}'
          AND overall IS NOT NULL
        """
    )
    rows = cur.fetchall()
    df = pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date": [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
            "overall": [int(r[2]) for r in rows],
        }
    )
    df.write_parquet(cache_pq)
    return df


def fetch_signal_scores(
    version_id: int,
    start_date: str,
    end_date: str,
    universe: str,
    lookback_days: int,
) -> pl.DataFrame:
    cache_pq = CACHE_DIR / f"{universe}_score_signals_v{version_id}_{start_date}_{end_date}.parquet"
    if cache_pq.exists():
        df = pl.read_parquet(cache_pq)
        print(f"[scores:{universe}] cache hit {len(df):,} rows", flush=True)
        return df

    if universe == "stock":
        runner_score_path = ROOT / ".cache" / "runner" / f"scores_v{version_id}_{lookback_days}.parquet"
        if runner_score_path.exists():
            df = (
                pl.read_parquet(runner_score_path, columns=["symbol", "date", "overall"])
                .filter((pl.col("date") >= start_date) & (pl.col("date") <= end_date))
                .filter(~pl.col("symbol").is_in(ETF_SYMBOLS))
                .filter((pl.col("overall") >= min(CALL_THRESHOLDS)) | (pl.col("overall") <= max(PUT_THRESHOLDS)))
            )
            df.write_parquet(cache_pq)
            print(f"[scores:{universe}] runner cache {len(df):,} rows from {runner_score_path}", flush=True)
            return df

    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=600000")
    rows = []
    start_year = int(start_date[:4])
    end_year = int(end_date[:4])
    etf_list = _sql_symbols(ETF_SYMBOLS)
    t0 = time.time()

    for yr in range(start_year, end_year + 1):
        y_start = max(start_date, f"{yr}-01-01")
        y_end = min(end_date, f"{yr}-12-31")
        if universe == "etf":
            where_universe = f"s.symbol IN ('{etf_list}')"
            join = ""
        elif universe == "stock":
            where_universe = (
                "st.flagged = 1 AND st.sector IS NOT NULL "
                f"AND s.symbol NOT IN ('{etf_list}')"
            )
            join = "JOIN stocks st ON st.symbol = s.symbol"
        else:
            raise ValueError(f"unknown universe: {universe}")

        ti = time.time()
        cur = DB.execute_sql(
            f"""
            SELECT s.symbol, s.date, s.overall
            FROM scores s
            {join}
            WHERE s.version_id = {version_id}
              AND s.date >= '{y_start}' AND s.date <= '{y_end}'
              AND s.overall IS NOT NULL
              AND ({where_universe})
              AND (s.overall >= {min(CALL_THRESHOLDS)} OR s.overall <= {max(PUT_THRESHOLDS)})
            """
        )
        chunk = cur.fetchall()
        rows.extend(chunk)
        print(f"[scores:{universe}] {yr}: {len(chunk):,} rows in {time.time() - ti:.1f}s", flush=True)

    df = pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date": [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
            "overall": [int(r[2]) for r in rows],
        }
    )
    df.write_parquet(cache_pq)
    print(f"[scores:{universe}] total {len(df):,} rows in {time.time() - t0:.1f}s", flush=True)
    return df


def build_etf_barriers(start_date: str, end_date: str) -> pl.DataFrame:
    cache_pq = CACHE_DIR / f"etf_barriers_{start_date}_{end_date}.parquet"
    if cache_pq.exists():
        df = pl.read_parquet(cache_pq)
        print(f"[etf_barriers] cache hit {len(df):,} rows", flush=True)
        return df

    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=600000")
    rows_out = []
    fetch_start = (date.fromisoformat(start_date) - timedelta(days=120)).isoformat()
    syms = _sql_symbols(ETF_SYMBOLS)
    cur = DB.execute_sql(
        f"""
        SELECT symbol, date, open, high, low, close
        FROM price_history
        WHERE symbol IN ('{syms}')
          AND date >= '{fetch_start}' AND date <= '{end_date}'
        ORDER BY symbol, date
        """
    )
    rows = cur.fetchall()
    prices = (
        pl.DataFrame(
            {
                "symbol": [r[0] for r in rows],
                "date": [r[1] for r in rows],
                "open": [float(r[2]) for r in rows],
                "high": [float(r[3]) for r in rows],
                "low": [float(r[4]) for r in rows],
                "close": [float(r[5]) for r in rows],
            }
        )
        if rows
        else pl.DataFrame({"symbol": [], "date": [], "open": [], "high": [], "low": [], "close": []})
    )

    for sym in ETF_SYMBOLS:
        pdf = prices.filter(pl.col("symbol") == sym).sort("date")
        bars = [
            (r["date"], r["close"], r["high"], r["low"], r["open"])
            for r in pdf.iter_rows(named=True)
        ]
        count_before = len(rows_out)
        for row in compute_outcomes_for_symbol(
            sym,
            bars,
            barrier_sets=["30dte_generic"],
            periods=PERIODS,
        ):
            row_date = row[1]
            if row_date < start_date or row_date > end_date:
                continue
            rows_out.append(
                {
                    "symbol": row[0],
                    "date": row[1],
                    "side": row[2],
                    "w_days": int(row[3]),
                    "result": row[5],
                    "exit_return": row[8],
                    "mae_pct": row[9],
                    "mfe_pct": row[10],
                    "result_u": row[11],
                }
            )
        print(f"[etf_barriers] {sym}: {len(rows_out) - count_before:,} rows", flush=True)

    df = pl.DataFrame(rows_out)
    df.write_parquet(cache_pq)
    return df


def load_stock_barriers() -> pl.DataFrame:
    df = pl.read_parquet(RUNNER_BARRIER_PATH)
    df = df.filter(pl.col("w_days").is_in(PERIODS)).select(
        ["symbol", "date", "side", "w_days", "result", "exit_return", "mae_pct", "mfe_pct", "result_u"]
    )
    return df.group_by(["symbol", "date", "side", "w_days"]).agg(
        [
            pl.col("result").first(),
            pl.col("exit_return").first(),
            pl.col("mae_pct").first(),
            pl.col("mfe_pct").first(),
            pl.col("result_u").first(),
        ]
    )


def _threshold_rows(scores: pl.DataFrame, barriers: pl.DataFrame, asset_class: str) -> list[dict]:
    rows = []
    configs = [
        ("CALL", "low", ">=", CALL_THRESHOLDS),
        ("PUT", "high", "<=", PUT_THRESHOLDS),
    ]

    for signal_type, barrier_side, op, thresholds in configs:
        side_bar = barriers.filter(pl.col("side") == barrier_side)
        for threshold in thresholds:
            if op == ">=":
                sig = scores.filter(pl.col("overall") >= threshold)
            else:
                sig = scores.filter(pl.col("overall") <= threshold)
            sig = sig.with_columns(pl.lit(barrier_side).alias("side"))
            joined = sig.join(side_bar, on=["symbol", "date", "side"], how="left")
            total_signals = len(sig)
            for w in PERIODS:
                wdf = joined.filter(pl.col("w_days") == w)
                covered = wdf.filter(pl.col("result").is_not_null())
                n = len(covered)
                rows.append(
                    {
                        "asset_class": asset_class,
                        "signal_type": signal_type,
                        "threshold": threshold,
                        "w_days": w,
                        "signals": total_signals,
                        "n": n,
                        "coverage": (n / total_signals) if total_signals else None,
                        "wr": covered["result"].mean() if n else None,
                        "avg_score": sig["overall"].mean() if total_signals else None,
                        "avg_exit_return": covered["exit_return"].mean() if n else None,
                        "avg_mae_pct": covered["mae_pct"].mean() if n else None,
                        "avg_mfe_pct": covered["mfe_pct"].mean() if n else None,
                    }
                )
    return rows


def _symbol_rows(scores: pl.DataFrame, barriers: pl.DataFrame) -> list[dict]:
    rows = []
    configs = [("CALL", "low", ">=", 60), ("CALL", "low", ">=", 70), ("PUT", "high", "<=", 40), ("PUT", "high", "<=", 30)]
    for signal_type, barrier_side, op, threshold in configs:
        if op == ">=":
            sig = scores.filter(pl.col("overall") >= threshold)
        else:
            sig = scores.filter(pl.col("overall") <= threshold)
        sig = sig.with_columns(pl.lit(barrier_side).alias("side"))
        joined = sig.join(barriers.filter(pl.col("side") == barrier_side), on=["symbol", "date", "side"], how="left")
        for sym in ETF_SYMBOLS:
            sdf = joined.filter((pl.col("symbol") == sym) & (pl.col("w_days") == 7) & pl.col("result").is_not_null())
            rows.append(
                {
                    "symbol": sym,
                    "etf_group": "sector" if sym in SECTOR_ETFS else "broad",
                    "signal_type": signal_type,
                    "threshold": threshold,
                    "w_days": 7,
                    "n": len(sdf),
                    "wr": sdf["result"].mean() if len(sdf) else None,
                    "avg_score": sdf["overall"].mean() if len(sdf) else None,
                    "avg_exit_return": sdf["exit_return"].mean() if len(sdf) else None,
                }
            )
    return rows


def score_ranges(etf_all: pl.DataFrame) -> pl.DataFrame:
    threshold_cols = []
    for t in CALL_THRESHOLDS:
        threshold_cols.append((pl.col("overall") >= t).sum().alias(f"n_ge_{t}"))
    for t in PUT_THRESHOLDS:
        threshold_cols.append((pl.col("overall") <= t).sum().alias(f"n_le_{t}"))
    return (
        etf_all.group_by("symbol")
        .agg(
            [
                pl.len().alias("rows"),
                pl.col("date").min().alias("min_date"),
                pl.col("date").max().alias("max_date"),
                pl.col("overall").min().alias("min_score"),
                pl.col("overall").quantile(0.05).alias("p05_score"),
                pl.col("overall").median().alias("median_score"),
                pl.col("overall").quantile(0.95).alias("p95_score"),
                pl.col("overall").max().alias("max_score"),
            ]
            + threshold_cols
        )
        .sort("symbol")
    )


def bucket_stats(etf_all: pl.DataFrame, etf_barriers: pl.DataFrame) -> pl.DataFrame:
    bins = []
    for lo in range(50, 100, 5):
        bins.append(("CALL", "low", lo, min(100, lo + 4), f"{lo}-{min(100, lo + 4)}"))
    bins.append(("CALL", "low", 100, 100, "100"))
    for hi in range(50, 0, -5):
        lo = max(0, hi - 4)
        bins.append(("PUT", "high", lo, hi, f"{lo}-{hi}"))
    rows = []
    for signal_type, side, lo, hi, label in bins:
        sig = etf_all.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi)).with_columns(pl.lit(side).alias("side"))
        if len(sig) == 0:
            continue
        joined = sig.join(etf_barriers.filter(pl.col("side") == side), on=["symbol", "date", "side"], how="left")
        for w in PERIODS:
            wdf = joined.filter((pl.col("w_days") == w) & pl.col("result").is_not_null())
            rows.append(
                {
                    "signal_type": signal_type,
                    "score_bucket": label,
                    "bucket_low": lo,
                    "bucket_high": hi,
                    "w_days": w,
                    "signals": len(sig),
                    "n": len(wdf),
                    "wr": wdf["result"].mean() if len(wdf) else None,
                    "avg_exit_return": wdf["exit_return"].mean() if len(wdf) else None,
                }
            )
    return pl.DataFrame(rows).sort(["signal_type", "bucket_low", "w_days"], descending=[False, True, False])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version-id", type=int, default=VERSION_ID)
    parser.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    parser.add_argument("--end-date", default=None)
    args = parser.parse_args()

    end = args.end_date or _today().isoformat()
    start = (date.fromisoformat(end) - timedelta(days=args.lookback_days)).isoformat()
    print(f"[run] version={args.version_id} start={start} end={end}", flush=True)

    etf_all = fetch_etf_all_scores(args.version_id, start, end)
    etf_scores = fetch_signal_scores(args.version_id, start, end, "etf", args.lookback_days)
    stock_scores = fetch_signal_scores(args.version_id, start, end, "stock", args.lookback_days)
    etf_barriers = build_etf_barriers(start, end)
    stock_barriers = load_stock_barriers()

    ranges = score_ranges(etf_all)
    threshold_df = pl.DataFrame(
        _threshold_rows(etf_scores, etf_barriers, "ETF_ALL")
        + _threshold_rows(
            etf_scores.filter(pl.col("symbol").is_in(SECTOR_ETFS)),
            etf_barriers.filter(pl.col("symbol").is_in(SECTOR_ETFS)),
            "ETF_SECTOR",
        )
        + _threshold_rows(
            etf_scores.filter(pl.col("symbol").is_in(BROAD_ETFS)),
            etf_barriers.filter(pl.col("symbol").is_in(BROAD_ETFS)),
            "ETF_BROAD",
        )
        + _threshold_rows(stock_scores, stock_barriers, "STOCK")
    )
    symbol_df = pl.DataFrame(_symbol_rows(etf_scores, etf_barriers))
    bucket_df = bucket_stats(etf_all, etf_barriers)

    suffix = f"v{args.version_id}_{args.lookback_days}"
    ranges_path = OUT_DIR / f"etf_score_ranges_{suffix}.csv"
    thresholds_path = OUT_DIR / f"etf_score_thresholds_{suffix}.csv"
    symbol_path = OUT_DIR / f"etf_score_by_symbol_{suffix}.csv"
    bucket_path = OUT_DIR / f"etf_score_buckets_{suffix}.csv"

    ranges.write_csv(ranges_path)
    threshold_df.write_csv(thresholds_path)
    symbol_df.write_csv(symbol_path)
    bucket_df.write_csv(bucket_path)

    print(f"[write] {ranges_path}", flush=True)
    print(f"[write] {thresholds_path}", flush=True)
    print(f"[write] {symbol_path}", flush=True)
    print(f"[write] {bucket_path}", flush=True)
    print("[primary] W7 threshold comparison", flush=True)
    print(
        threshold_df.filter(pl.col("w_days") == 7)
        .select(["asset_class", "signal_type", "threshold", "n", "coverage", "wr", "avg_score", "avg_exit_return"])
        .sort(["asset_class", "signal_type", "threshold"])
    )


if __name__ == "__main__":
    main()
