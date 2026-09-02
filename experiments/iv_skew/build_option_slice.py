"""Materialize a LOCAL option-chain slice for the iv_ledger signal universe, so
option-structure hypotheses (term structure, skew curve, OI imbalance) can be
iterated at columnar speed WITHOUT re-hammering the 62M-row MySQL option_prices
(which times out on aggregates and contends when two agents query it at once).

For every (symbol, signal_date) in .cache/iv_skew/iv_ledger.parquet, pulls the
near-the-money chain (±25% moneyness, 15-90 DTE, both types) with price/iv/OI/vol
+ the underlying close, into .cache/experiment_data/option_slice_ivledger.parquet
via the house bulk_cache pattern. ~433k rows / ~30 MB — RAM-safe in one shot.

Read it back with pl.read_parquet() or duckdb; join to iv_ledger on (symbol,date)
for the label (pnl15) + the already-computed controls (atm_iv, skew=opt_skew,
iv_rv, overall). This slice is the RAW substrate; features are derived downstream.

    python experiments/iv_skew/build_option_slice.py            # build (cache-guarded)
    python experiments/iv_skew/build_option_slice.py --force    # rebuild
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import polars as pl

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from database.bulk_cache import cache_path, is_fresh  # noqa: E402
from database.trader_database import DB  # noqa: E402

IV_LEDGER = _ROOT / ".cache" / "iv_skew" / "iv_ledger.parquet"
NAME = "option_slice_ivledger"

DTE_LO, DTE_HI = 15, 90          # spans near (~30) and far (~60-90) -> term structure
MONEYNESS = 0.25                 # ±25% around spot -> ATM + both 10% OTM wings + tails

_SQL = (
    "SELECT o.option_type, o.strike_price, o.expiration_date, "
    "       DATEDIFF(o.expiration_date, op.date) AS dte, "
    "       op.price, op.iv, op.open_interest, op.volume "
    "FROM options o JOIN option_prices op ON op.option_id = o.id "
    "WHERE o.symbol=%s AND op.date=%s "
    "  AND DATEDIFF(o.expiration_date, op.date) BETWEEN %s AND %s "
    "  AND o.strike_price BETWEEN %s AND %s"
)


def _close(symbol: str, d: str) -> float | None:
    cur = DB.execute_sql(
        "SELECT close FROM price_history WHERE symbol=%s AND date=%s", (symbol, d))
    row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


def build() -> pl.DataFrame:
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=120000")  # dodge the default kill
    led = pl.read_parquet(IV_LEDGER).select(["symbol", "date"]).unique()
    pairs = list(zip(led["symbol"].to_list(), led["date"].to_list()))
    pairs.sort()
    recs: list[dict] = []
    t0 = time.time()
    missing_close = no_rows = 0
    for i, (sym, d) in enumerate(pairs):
        close = _close(sym, d)
        if not close or close <= 0:
            missing_close += 1
            continue
        cur = DB.execute_sql(_SQL, (sym, d, DTE_LO, DTE_HI, close * (1 - MONEYNESS), close * (1 + MONEYNESS)))
        rows = cur.fetchall()
        if not rows:
            no_rows += 1
            continue
        for otype, strike, exp, dte, price, iv, oi, vol in rows:
            k = float(strike)
            recs.append({
                "symbol": sym, "date": d, "close": close,
                "option_type": otype, "strike": k,
                "expiration": exp.isoformat() if hasattr(exp, "isoformat") else str(exp),
                "dte": int(dte), "moneyness": round(k / close, 4),
                "price": (float(price) if price is not None else None),
                "iv": (float(iv) if iv is not None else None),
                "open_interest": (int(oi) if oi is not None else None),
                "volume": (int(vol) if vol is not None else None),
            })
        if (i + 1) % 300 == 0:
            print(f"  {i+1}/{len(pairs)} signals, {len(recs):,} rows "
                  f"({time.time()-t0:.0f}s, miss_close={missing_close}, no_chain={no_rows})", flush=True)
    print(f"built {len(recs):,} rows from {len(pairs)} signals "
          f"(missing_close={missing_close}, no_chain={no_rows}) in {time.time()-t0:.0f}s", flush=True)
    return pl.DataFrame(recs, infer_schema_length=None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    path = cache_path(NAME)
    if not a.force and is_fresh(path, IV_LEDGER):
        print(f"[hit] {path} ({path.stat().st_size/1e6:.1f} MB) — use --force to rebuild")
        return 0
    df = build()
    df.write_parquet(path, compression="snappy")
    print(f"[wrote] {path} ({path.stat().st_size/1e6:.1f} MB, {df.height:,} rows)")
    print(f"        symbols={df['symbol'].n_unique()} signal-dates={df.select(['symbol','date']).unique().height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
