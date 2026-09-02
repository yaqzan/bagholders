"""Peak-Fakeout study -- prep_lookups.py

Run ONCE, foreground (seconds). Materializes three tiny parquets so the main
mine.py run needs NO database access:
  .cache/peak_fakeout/lookup_mcap.parquet     (symbol, mcap_b_latest, anchor_close, anchor_date)
  .cache/peak_fakeout/lookup_earnings.parquet (symbol, earnings_date)  -- long format
  .cache/peak_fakeout/lookup_vix.parquet      (date, vix_close)

Usage:
    PYTHONIOENCODING=utf-8 python experiments/peak_fakeout/prep_lookups.py
"""
from __future__ import annotations

import os
import sys
import time

import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUT_DIR = os.path.join(_ROOT, ".cache", "peak_fakeout")
os.makedirs(OUT_DIR, exist_ok=True)

LEDGER_PATH = os.path.join(_ROOT, ".cache", "trend_ma_lattice", "ledger_v74_full.parquet")
OHLC_PATH = os.path.join(_ROOT, ".cache", "intraday_overnight", "ohlc.parquet")
VIX_SRC_PATH = os.path.join(_ROOT, ".cache", "iv_premium_model", "vix_series.parquet")

BATCH = 150   # matches trend_ma_lattice's own batched .in_() pattern (avoids the peewee FK-per-row trap)


def ledger_symbols() -> list[str]:
    df = pl.read_parquet(LEDGER_PATH, columns=["symbol"])
    syms = sorted(df["symbol"].unique().to_list())
    print(f"[symbols] {len(syms)} unique ledger symbols", flush=True)
    return syms


def build_mcap_lookup(symbols: list[str]) -> pl.DataFrame:
    """symbol -> mcap_b_latest (Stock.market_cap / 1e9 -- market_cap is a raw-
    dollar DecimalField, confirmed against database/models/core.py:710-711's
    own `_mcap_b = float(_stock_row.market_cap) / 1e9` usage) + anchor_close
    (that symbol's LAST close in the OHLC parquet, i.e. priced as of
    2026-06-08 -- the parquet's max date, NOT literally "today"/2026-07-15
    when Stock.market_cap was itself snapshotted by yfinance).

    Both feed database.utils.scoring.compute_pit_mcap_b(mcap_b_latest,
    close_t, anchor_close) at feature-extraction time (features.py does NOT
    reimplement this -- mine.py imports and calls it directly) to deflate the
    CURRENT mcap snapshot back to a point-in-time proxy at each historical
    entry date. This is the same fix pattern the live scoring system uses for
    its own point-in-time mcap dampener (database/models/core.py ~L704-720) --
    unrelated to this study's OWN "F4" (climax_day) feature; do not confuse
    the two while reading code/comments.

    NOTE (accepted PIT caveat, per PREREGISTRATION F1 + orchestrator prompt):
    anchor_close is priced as of the OHLC snapshot date, not the mcap
    snapshot date -- a several-week mismatch between the two anchors.
    Acceptable for TERCILE bucketing (rank-based, not absolute-level-
    sensitive).
    """
    from database.models.core import Stock

    t0 = time.time()
    mcap_by_sym: dict[str, float | None] = {}
    for i in range(0, len(symbols), BATCH):
        batch = symbols[i:i + BATCH]
        # .tuples() -- raw column values only, never touches the FK-per-row trap.
        for sym, mcap in (Stock.select(Stock.symbol, Stock.market_cap)
                           .where(Stock.symbol.in_(batch)).tuples()):
            mcap_by_sym[sym] = float(mcap) / 1e9 if mcap is not None else None

    ohlc = pl.read_parquet(OHLC_PATH, columns=["symbol", "date", "close"])
    ohlc = ohlc.filter(pl.col("symbol").is_in(symbols)).sort(["symbol", "date"])
    anchor = ohlc.group_by("symbol", maintain_order=True).tail(1).rename(
        {"close": "anchor_close", "date": "anchor_date"}
    )
    anchor_by_sym = {r["symbol"]: (r["anchor_close"], r["anchor_date"])
                     for r in anchor.iter_rows(named=True)}

    rows = []
    n_no_mcap = n_no_anchor = 0
    for sym in symbols:
        mcap_b = mcap_by_sym.get(sym)
        ac, ad = anchor_by_sym.get(sym, (None, None))
        n_no_mcap += mcap_b is None
        n_no_anchor += ac is None
        rows.append({"symbol": sym, "mcap_b_latest": mcap_b, "anchor_close": ac, "anchor_date": ad})

    df = pl.DataFrame(rows, infer_schema_length=None)
    path = os.path.join(OUT_DIR, "lookup_mcap.parquet")
    df.write_parquet(path)
    print(f"[mcap] {df.height} symbols -> {path} ({time.time()-t0:.1f}s) "
          f"missing_mcap={n_no_mcap} missing_anchor={n_no_anchor}", flush=True)
    return df


def build_earnings_lookup(symbols: list[str]) -> pl.DataFrame:
    """(symbol, earnings_date) long-format rows -- EarningsDate.date (the
    announcement date), NOT effective_date (which shifts AMC events to the
    next trading day; that's a scoring-pipeline convention this study does
    not need). PIT caveat (accepted, per PREREGISTRATION/prompt): this is the
    CURRENT earnings-calendar snapshot (yfinance-sourced, sometimes revised
    after the fact), not necessarily what was scheduled/knowable as of each
    historical entry date. F9 in mine.py treats this as a best-effort
    "how close is entry to an earnings event" measure.

    Query pattern mirrors database/models/core.py:3204
    (_load_earnings_by_symbol) EXACTLY -- batched .in_() + .tuples() -- but
    does NOT call that function directly: it early-returns {} whenever
    EARN_BOOST_ENABLED is False, which IS the case in the current v74
    strategy_config.py (confirmed: `EARN_BOOST_ENABLED=False,  # v74 lean`),
    and would silently empty this lookup. This query has no such gate.
    """
    from database.models.core import EarningsDate

    t0 = time.time()
    rows = []
    for i in range(0, len(symbols), BATCH):
        batch = symbols[i:i + BATCH]
        for sym, d in (EarningsDate.select(EarningsDate.symbol, EarningsDate.date)
                       .where(EarningsDate.symbol.in_(batch)).tuples()):
            if d is not None:
                rows.append({"symbol": sym, "earnings_date": d})

    if rows:
        df = pl.DataFrame(rows, infer_schema_length=None).sort(["symbol", "earnings_date"])
    else:
        df = pl.DataFrame({"symbol": [], "earnings_date": []},
                           schema={"symbol": pl.Utf8, "earnings_date": pl.Date})
    path = os.path.join(OUT_DIR, "lookup_earnings.parquet")
    df.write_parquet(path)
    n_syms_with = df["symbol"].n_unique() if df.height else 0
    print(f"[earnings] {df.height} rows across {n_syms_with}/{len(symbols)} symbols -> {path} "
          f"({time.time()-t0:.1f}s)", flush=True)
    return df


def build_vix_lookup() -> tuple[pl.DataFrame, str]:
    """F12 VIX band source. An existing parquet was found at
    .cache/iv_premium_model/vix_series.parquet (date:String ISO, vix_close:
    Float64, 1995-01-03..) -- reused rather than re-querying MySQL, per the
    prompt's "check .cache/ for an existing VIX parquet first" instruction.
    Copied into our OWN lookup (not read cross-experiment at mine.py time)
    so this study's cache is self-contained and isolated from that other
    experiment's cache lifecycle. Falls back to MySQL PriceHistory('^VIX'),
    then MarketRegime.vix_close, then gives up (mine.py marks F12
    not-computable in that case)."""
    t0 = time.time()
    if os.path.exists(VIX_SRC_PATH):
        src = pl.read_parquet(VIX_SRC_PATH)
        df = src.select([
            pl.col("date").str.to_date().alias("date"),
            pl.col("vix_close").cast(pl.Float64),
        ]).sort("date")
        path = os.path.join(OUT_DIR, "lookup_vix.parquet")
        df.write_parquet(path)
        print(f"[vix] reused existing cache {VIX_SRC_PATH} -> {df.height} rows -> {path} "
              f"({time.time()-t0:.1f}s) source=cache_reuse", flush=True)
        return df, "cache_reuse"

    try:
        from database.models.technical import PriceHistory
        rows = list(PriceHistory.select(PriceHistory.date, PriceHistory.close)
                    .where(PriceHistory.symbol == "^VIX").order_by(PriceHistory.date).tuples())
        if rows:
            df = pl.DataFrame(
                [{"date": d, "vix_close": float(c) if c is not None else None} for d, c in rows],
                infer_schema_length=None,
            )
            path = os.path.join(OUT_DIR, "lookup_vix.parquet")
            df.write_parquet(path)
            print(f"[vix] MySQL PriceHistory '^VIX' -> {df.height} rows -> {path} "
                  f"({time.time()-t0:.1f}s) source=mysql_pricehistory", flush=True)
            return df, "mysql_pricehistory"
    except Exception as e:
        print(f"[vix] MySQL '^VIX' PriceHistory query failed: {e}", flush=True)

    try:
        from database.models.core import MarketRegime
        rows = list(MarketRegime.select(MarketRegime.date, MarketRegime.vix_close)
                    .order_by(MarketRegime.date).tuples())
        if rows:
            df = pl.DataFrame(
                [{"date": d, "vix_close": float(c) if c is not None else None} for d, c in rows],
                infer_schema_length=None,
            )
            path = os.path.join(OUT_DIR, "lookup_vix.parquet")
            df.write_parquet(path)
            print(f"[vix] MarketRegime.vix_close -> {df.height} rows -> {path} "
                  f"({time.time()-t0:.1f}s) source=market_regime", flush=True)
            return df, "market_regime"
    except Exception as e:
        print(f"[vix] MarketRegime query failed: {e}", flush=True)

    print("[vix] NOT COMPUTABLE -- no VIX source found in cache/MySQL/MarketRegime -- "
          "F12 will be marked not-computable by mine.py (see DEVIATIONS)", flush=True)
    empty = pl.DataFrame({"date": [], "vix_close": []}, schema={"date": pl.Date, "vix_close": pl.Float64})
    empty.write_parquet(os.path.join(OUT_DIR, "lookup_vix.parquet"))
    return empty, "none"


def main():
    t_start = time.time()
    print("[start] peak_fakeout prep_lookups.py", flush=True)
    symbols = ledger_symbols()
    build_mcap_lookup(symbols)
    build_earnings_lookup(symbols)
    build_vix_lookup()
    print(f"[done] total wall time {time.time()-t_start:.1f}s", flush=True)


if __name__ == "__main__":
    main()
