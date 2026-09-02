"""
build_forward_ranks.py -- Era-Conditioning Program, Phase B (DESIGN.md).

Idempotent catch-up forward-rank ledger: on every invocation, finds trading
days D >= FORWARD_START (2026-06-16, DESIGN.md's fixed forward-start = the
first true-holdout day) that have option_prices data and are not already in
the ledger, and processes ONLY those missing days. Safe to re-run anytime --
already-ledgered days are skipped, so a scheduled/queued re-invocation is a
no-op once caught up and a resume once new days accumulate. Evaluation is
BANKED (DESIGN.md): this script only appends rows; the pre-registered re-reads
happen separately at N>=60 and N>=120 forward trading days.

Per missing day D, for every option-covered symbol (has >=1 chain row in
option_prices on D):
  1. Pull that day's chain + spot via a BATCHED query adapted from
     experiments/gex/build_gex_cache.py's per-symbol-batch shape. That build
     batches MANY DATES per symbol (`op.date IN (...)`, symbol fixed) because
     it iterates symbol-by-symbol over a whole signal history; Phase B instead
     iterates day-by-day over the day's full covered universe, so the batch
     dimension here is SYMBOL instead (`o.symbol IN (...)`, date fixed) --
     same JOIN shape (options x option_prices x price_history), same session
     timeout guard, same filter contract (open_interest>0, iv in (0.01,5.0)
     open interval, dte in [1,180]).
  2. Compute the dealer-GEX feature row via experiments/gex/dealer_gex.py's
     `_process_one_group` (IMPORTED, not reimplemented -- MATH_SPEC.md-exact).
  3. Compute our-recipe skew replicating experiments/iv_skew/build_iv.py's
     `otm_iv()` (lines 47-55) EXACTLY -- DTE 20-45 inclusive, iv 0.05-5.0
     inclusive, OI>0, nearest-strike-to-a-10%-OTM-target, pooled across
     expirations -- but evaluated against the chain already pulled for GEX
     in step 1, instead of otm_iv's own 2 extra per-pair DB round-trips. This
     program's hard stop is "MySQL only via the batched chain/spot queries",
     so the recipe is copied (see `_compute_skew` below) rather than imported
     and called directly (which would re-query MySQL per symbol per day).
  4. Cross-sectional percentile ranks WITHIN day D: rank_skew, rank_gex_ratio,
     rank_local_density (NaN-safe: dealer_gex's NaN sentinels are converted to
     proper nulls before ranking, so degenerate rows are excluded from the
     percentile base rather than silently poisoning it -- see `_pct_rank_cols`).

Day-atomic persistence: a day is appended to the ledger ONLY when every batch
for it succeeds (after a retry). If any batch fails, nothing for that day is
written and it remains "missing" for the next invocation -- this is what makes
the catch-up idempotent rather than merely re-run-safe-with-gaps. A day that
resolves with zero real chain rows (no covered symbols, or every candidate
row is filtered out) is recorded as an "empty day" in the JSON report (NOT the
parquet) so it is not reprocessed forever.

Outputs (real):
    .cache/era_conditioning/forward_ranks.parquet        (append-only ledger)
    .cache/era_conditioning/forward_ranks_report.json    (coverage + banked
                                                           re-read gate status)

--smoke: processes exactly 1 (the earliest) missing day, symbols capped to 25,
written to *_smoke sidecar outputs only -- never touches the real ledger/report.

READ-ONLY toward experiments/gex/ and experiments/iv_skew/ (imports only, never
edits). New files only under experiments/era_conditioning/ and
.cache/era_conditioning/. No scoring code touched anywhere.

Usage:
    set PYTHONUTF8=1
    python experiments/era_conditioning/build_forward_ranks.py --smoke
    python experiments/era_conditioning/build_forward_ranks.py            # full catch-up (queue only)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from database.trader_database import DB  # noqa: E402
from experiments.gex.dealer_gex import _process_one_group  # noqa: E402

OUT_DIR = Path(_ROOT) / ".cache" / "era_conditioning"
LEDGER_PATH = OUT_DIR / "forward_ranks.parquet"
REPORT_PATH = OUT_DIR / "forward_ranks_report.json"
SMOKE_LEDGER_PATH = OUT_DIR / "forward_ranks_smoke.parquet"
SMOKE_REPORT_PATH = OUT_DIR / "forward_ranks_report_smoke.json"

FORWARD_START = "2026-06-16"  # DESIGN.md Phase B: fixed forward-start = first true-holdout day

# Chain-pull filters -- experiments/gex/build_gex_cache.py D3 (identical contract).
DTE_LO, DTE_HI = 1, 180
IV_LO, IV_HI = 0.01, 5.0          # open interval

# Our-recipe skew -- experiments/iv_skew/build_iv.py otm_iv() EXACT recipe (see
# module docstring point 3 above for why this is copied, not imported).
SKEW_DTE_LO, SKEW_DTE_HI = 20, 45
SKEW_IV_LO, SKEW_IV_HI = 0.05, 5.0
OTM_FRAC = 0.10

BATCH_SYMBOLS = 20          # experiments/gex/build_gex_cache.py BATCH_SYMBOLS
CHAIN_TIMEOUT_MS = 180000   # experiments/gex/build_gex_cache.py CHAIN_TIMEOUT_MS
SMOKE_SYMBOL_CAP = 25

REREAD_N_60 = 60            # DESIGN.md banked re-read: N>=60 forward trading days (~2026-10)
REREAD_N_120 = 120          # DESIGN.md banked re-read: N>=120 forward trading days (~2027-01)


def _set_session_timeout(ms: int) -> None:
    try:
        DB.execute_sql(f"SET SESSION MAX_EXECUTION_TIME={int(ms)}")
    except Exception as e:
        print(f"WARNING: could not set session timeout: {repr(e)[:80]}", flush=True)


def _safe_close() -> None:
    try:
        DB.close()
    except Exception:
        pass


def _with_retry(fn, *args, attempts=2, **kwargs):
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            print(f"  query attempt {attempt}/{attempts} failed: {repr(e)[:100]}", flush=True)
            _safe_close()
            _set_session_timeout(CHAIN_TIMEOUT_MS)
    raise last_err


def _atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    df.write_parquet(tmp, compression="snappy")
    os.replace(tmp, path)


def _atomic_write_json(obj: dict, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="ascii", errors="replace") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def _load_report(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="ascii") as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------------------------------------------------------- day/symbol discovery

def _existing_ledger_dates() -> set:
    if not LEDGER_PATH.exists():
        return set()
    df = pl.read_parquet(LEDGER_PATH, columns=["date"])
    return {str(d)[:10] for d in df["date"].unique().to_list()}


def _all_option_days_since(start: str) -> list:
    """Distinct trading days >= start with >=1 option_prices row. Bounded range
    scan on the (date, option_id) index -- not a full-table distinct."""
    cur = DB.execute_sql(
        "SELECT DISTINCT date FROM option_prices WHERE date >= %s ORDER BY date",
        (start,))
    return [d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]
            for (d,) in cur.fetchall()]


def _symbols_covered_on(date_str: str) -> list:
    """Distinct symbols with >=1 option chain row on this date (date-indexed
    filter on option_prices first, then join to options for the symbol)."""
    cur = DB.execute_sql(
        "SELECT DISTINCT o.symbol FROM options o "
        "JOIN option_prices op ON op.option_id = o.id "
        "WHERE op.date = %s", (date_str,))
    return sorted(r[0] for r in cur.fetchall())


# ---------------------------------------------------------------- batched chain pull
# Adapts experiments/gex/build_gex_cache.py's per-symbol-batch shape: that build
# batches MANY DATES per symbol (`op.date IN (...)`, symbol fixed); Phase B
# fixes the date and iterates the day's full covered universe, so the batch
# dimension here is SYMBOL instead (`o.symbol IN (...)`, date fixed). Same JOIN,
# same DATEDIFF filter, same session-timeout guard.

_CHAIN_SQL = (
    "SELECT o.symbol, ph.close AS spot, o.option_type, o.strike_price, "
    "       DATEDIFF(o.expiration_date, op.date) AS dte, op.open_interest, op.iv "
    "FROM options o "
    "JOIN option_prices op ON op.option_id = o.id "
    "JOIN price_history ph ON ph.symbol = o.symbol AND ph.date = op.date "
    "WHERE op.date = %s AND o.symbol IN ({placeholders}) "
    "  AND DATEDIFF(o.expiration_date, op.date) BETWEEN %s AND %s"
)


def _fetch_batch_chain(date_str: str, symbols: list) -> list:
    placeholders = ",".join(["%s"] * len(symbols))
    sql = _CHAIN_SQL.format(placeholders=placeholders)
    cur = DB.execute_sql(sql, tuple([date_str, *symbols, DTE_LO, DTE_HI]))
    return cur.fetchall()


def _filter_rows(rows: list) -> tuple:
    """OI>0, iv strict-open-interval, spot>0 -- build_gex_cache.py _process_symbol
    filter contract, applied across a symbol batch instead of one symbol."""
    recs = []
    drops = {"open_interest_not_positive": 0, "iv_out_of_range": 0, "bad_spot": 0}
    for sym, spot, otype, strike, dte, oi, iv in rows:
        if oi is None or int(oi) <= 0:
            drops["open_interest_not_positive"] += 1
            continue
        if iv is None or not (IV_LO < float(iv) < IV_HI):
            drops["iv_out_of_range"] += 1
            continue
        if spot is None or float(spot) <= 0:
            drops["bad_spot"] += 1
            continue
        recs.append({
            "symbol": sym, "spot": float(spot), "option_type": otype,
            "strike": float(strike), "dte": int(dte),
            "open_interest": int(oi), "iv": float(iv),
        })
    return recs, drops


# ---------------------------------------------------------------- our-recipe skew

def _compute_skew(g: pl.DataFrame) -> dict:
    """Replicates experiments/iv_skew/build_iv.py `otm_iv()` (lines 47-55)
    EXACTLY: DTE 20-45 inclusive, iv 0.05-5.0 inclusive, OI>0, nearest strike to
    a +/-10% OTM target, pooled across expirations (no expiration grouping) --
    against the chain already in memory for this (symbol, date) rather than 2
    extra per-pair DB round-trips (module docstring point 3)."""
    spot = float(g["spot"][0])
    cand = g.filter(
        (pl.col("dte") >= SKEW_DTE_LO) & (pl.col("dte") <= SKEW_DTE_HI)
        & (pl.col("iv") >= SKEW_IV_LO) & (pl.col("iv") <= SKEW_IV_HI)
        & (pl.col("open_interest") > 0)
    )

    def _nearest_iv(otype: str, frac: float):
        target = spot * (1.0 + frac)
        sub = cand.filter(pl.col("option_type") == otype)
        if sub.height == 0:
            return None
        idx = int((sub["strike"] - target).abs().arg_min())
        val = sub["iv"][idx]
        return float(val) if val is not None else None

    put_iv = _nearest_iv("put", -OTM_FRAC)
    call_iv = _nearest_iv("call", OTM_FRAC)
    skew = (put_iv - call_iv) if (put_iv is not None and call_iv is not None) else None
    return {"skew": skew, "otm_put_iv": put_iv, "otm_call_iv": call_iv}


# ---------------------------------------------------------------- per-day build

_GEX_DROP_KEYS = ("symbol", "date", "spot")  # our own symbol/date/spot take precedence
# (dealer_gex re-emits its own 'spot' from the DTE[1,90]-filtered subgroup, which
# is NaN in the degenerate case where that subgroup is empty even though we DO
# have a real spot for the (symbol,date) from the raw dte[1,180] pull -- keep ours.)


def _process_day(date_str: str, symbols: list) -> tuple:
    """Batched chain pull + per-symbol dealer_gex features + skew, for ALL
    `symbols` on `date_str`. Returns (day_rows, drops, n_batches_failed). A
    nonzero n_batches_failed means the caller must NOT persist this day."""
    day_rows = []
    drops_acc = {"open_interest_not_positive": 0, "iv_out_of_range": 0, "bad_spot": 0}
    n_failed = 0
    n_batches = (len(symbols) + BATCH_SYMBOLS - 1) // BATCH_SYMBOLS

    for bi in range(n_batches):
        batch = symbols[bi * BATCH_SYMBOLS:(bi + 1) * BATCH_SYMBOLS]
        try:
            raw = _with_retry(_fetch_batch_chain, date_str, batch)
        except Exception as e:
            print(f"  {date_str} batch {bi+1}/{n_batches} FAILED after retry: "
                  f"{repr(e)[:100]}", flush=True)
            n_failed += 1
            continue

        recs, drops = _filter_rows(raw)
        for k in drops:
            drops_acc[k] += drops[k]
        if not recs:
            continue

        df = pl.DataFrame(recs, infer_schema_length=None).with_columns([
            pl.col("strike").cast(pl.Float64),
            pl.col("dte").cast(pl.Int64),
            pl.col("open_interest").cast(pl.Int64),
            pl.col("iv").cast(pl.Float64),
            pl.col("spot").cast(pl.Float64),
        ])
        for key, g in df.group_by("symbol", maintain_order=True):
            sym = key[0] if isinstance(key, tuple) else key
            try:
                gex_row = _process_one_group(sym, date_str, g)
                skew_row = _compute_skew(g)
            except Exception as e:
                print(f"  {date_str} {sym}: feature computation failed "
                      f"{repr(e)[:100]} -- skipped", flush=True)
                continue
            row = {"date": date_str, "symbol": sym, "spot": float(g["spot"][0])}
            row.update(skew_row)
            for k, v in gex_row.items():
                if k not in _GEX_DROP_KEYS:
                    row[k] = v
            day_rows.append(row)

        print(f"  {date_str} batch {bi+1}/{n_batches} symbols={len(batch)} "
              f"rows_so_far={len(day_rows)}", flush=True)

    return day_rows, drops_acc, n_failed


def _pct_rank_cols(day_df: pl.DataFrame) -> pl.DataFrame:
    """Cross-sectional percentile ranks within the day for skew/gex_ratio/
    local_density. dealer_gex's NaN sentinels are converted to real nulls
    FIRST (polars' native rank() otherwise treats NaN as the largest value,
    corrupting the top percentile -- verified empirically), then ranked with
    method='average' and normalized by the count of non-null (non-degenerate)
    rows so the percentile base excludes rows where the underlying feature
    could not be computed."""
    float_cols = [c for c, dt in zip(day_df.columns, day_df.dtypes)
                  if dt in (pl.Float32, pl.Float64)]
    if float_cols:
        day_df = day_df.with_columns([pl.col(c).fill_nan(None) for c in float_cols])

    n = day_df.height
    exprs = []
    for col, rank_col in (("skew", "rank_skew"), ("gex_ratio", "rank_gex_ratio"),
                          ("local_density", "rank_local_density")):
        n_valid = int(day_df[col].is_not_null().sum()) if col in day_df.columns else 0
        if n_valid > 0:
            exprs.append((pl.col(col).rank(method="average") / n_valid).alias(rank_col))
        else:
            exprs.append(pl.lit(None, dtype=pl.Float64).alias(rank_col))
    day_df = day_df.with_columns(exprs)
    day_df = day_df.with_columns(pl.lit(n).alias("n_covered_that_day"))
    return day_df


# ---------------------------------------------------------------- report

def _write_report(path: Path, ledger_df, mode: str, processed_days: int,
                   elapsed: float, empty_days: list) -> dict:
    if ledger_df is not None and ledger_df.height:
        dates = sorted(ledger_df["date"].unique().to_list())
        n_days = len(dates)
        n_rows = ledger_df.height
        first_date, last_date = dates[0], dates[-1]
        columns = ledger_df.columns
    else:
        n_days, n_rows, first_date, last_date, columns = 0, 0, None, None, []

    n_days_incl_empty = n_days + len(empty_days)
    report = {
        "forward_start": FORWARD_START,
        "mode": mode,
        "days_with_rows": n_days,
        "empty_days": sorted(empty_days),
        "n_empty_days": len(empty_days),
        "forward_trading_days_covered": n_days_incl_empty,
        "first_date": first_date,
        "last_date": last_date,
        "total_rows": n_rows,
        "columns": columns,
        "processed_days_this_run": processed_days,
        "elapsed_seconds_this_run": round(elapsed, 1),
        "reread_targets": {
            "n60_forward_trading_days": REREAD_N_60,
            "n60_met": n_days_incl_empty >= REREAD_N_60,
            "n60_approx_calendar_design_md": "~2026-10",
            "n120_forward_trading_days": REREAD_N_120,
            "n120_met": n_days_incl_empty >= REREAD_N_120,
            "n120_approx_calendar_design_md": "~2027-01",
        },
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _atomic_write_json(report, path)
    return report


# ---------------------------------------------------------------- main catch-up

def run_catchup(smoke: bool) -> int:
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _set_session_timeout(CHAIN_TIMEOUT_MS)

    prior_report = _load_report(REPORT_PATH)          # always the REAL state
    real_empty_days = set(prior_report.get("empty_days", []))
    real_existing_dates = _existing_ledger_dates()     # always the REAL ledger
    known_dates = real_existing_dates | real_empty_days

    all_days = _with_retry(_all_option_days_since, FORWARD_START)
    missing_days = [d for d in all_days if d not in known_dates]
    n_missing_total = len(missing_days)

    print(f"forward_start={FORWARD_START} all_option_days_since={len(all_days)} "
          f"known(rows_or_empty)={len(known_dates)} missing={n_missing_total}",
          flush=True)

    if smoke:
        missing_days = missing_days[:1]
        if not missing_days:
            print("smoke: no missing day to demonstrate -- real ledger is fully "
                  "caught up", flush=True)
            return 0
    elif not missing_days:
        print("nothing to do -- ledger is up to date", flush=True)
        ledger_df = pl.read_parquet(LEDGER_PATH) if LEDGER_PATH.exists() else None
        _write_report(REPORT_PATH, ledger_df, "full", 0, time.time() - t0,
                      sorted(real_empty_days))
        return 0

    existing = None
    if not smoke and LEDGER_PATH.exists():
        existing = pl.read_parquet(LEDGER_PATH)

    new_empty_days = []
    processed = 0

    for day in missing_days:
        day_t0 = time.time()
        real_symbols = _with_retry(_symbols_covered_on, day)
        symbols = real_symbols[:SMOKE_SYMBOL_CAP] if smoke else real_symbols
        print(f"day {day}: real_covered_symbols={len(real_symbols)} "
              f"processing={len(symbols)}", flush=True)

        if not symbols:
            print(f"day {day}: zero covered symbols -- marking as an empty "
                  f"processed day (no chain rows to rank)", flush=True)
            if not smoke:
                new_empty_days.append(day)
            processed += 1
            continue

        day_rows, drops, n_failed = _process_day(day, symbols)
        elapsed_day = time.time() - day_t0

        if n_failed > 0:
            print(f"day {day}: {n_failed} batch(es) FAILED after retry -- NOT "
                  f"persisting (stays missing for the next invocation)", flush=True)
            if smoke:
                break
            continue

        if not day_rows:
            print(f"day {day}: 0 feature rows survived filtering ({drops}) -- "
                  f"marking as an empty processed day", flush=True)
            if not smoke:
                new_empty_days.append(day)
            processed += 1
            continue

        day_df = pl.DataFrame(day_rows, infer_schema_length=None)
        day_df = _pct_rank_cols(day_df)

        if smoke:
            _atomic_write_parquet(day_df, SMOKE_LEDGER_PATH)
            seconds_per_symbol = elapsed_day / max(len(symbols), 1)
            extrapolated_day = seconds_per_symbol * len(real_symbols)
            smoke_stats = {
                "date": day,
                "real_covered_symbols": len(real_symbols),
                "symbols_processed": len(symbols),
                "rows_written": day_df.height,
                "columns": day_df.columns,
                "elapsed_seconds": round(elapsed_day, 2),
                "seconds_per_symbol": round(seconds_per_symbol, 3),
                "extrapolated_seconds_per_day_full_coverage": round(extrapolated_day, 1),
                "pending_missing_days_total": n_missing_total,
                "extrapolated_catchup_seconds": round(extrapolated_day * n_missing_total, 1),
                "filter_drop_counts": drops,
            }
            _atomic_write_json(smoke_stats, SMOKE_REPORT_PATH)
            print(f"SMOKE day {day}: rows={day_df.height} elapsed={elapsed_day:.2f}s "
                  f"seconds_per_symbol={seconds_per_symbol:.3f} "
                  f"extrapolated_day={extrapolated_day:.1f}s "
                  f"extrapolated_catchup={extrapolated_day * n_missing_total:.1f}s "
                  f"(over {n_missing_total} pending days)", flush=True)
        else:
            existing = (pl.concat([existing, day_df], how="diagonal_relaxed")
                        if existing is not None else day_df)
            existing = existing.sort(["date", "symbol"])
            _atomic_write_parquet(existing, LEDGER_PATH)
            print(f"day {day}: rows={day_df.height} elapsed={elapsed_day:.1f}s "
                  f"drops={drops}", flush=True)

        processed += 1

    if not smoke:
        merged_empty = sorted(real_empty_days | set(new_empty_days))
        report = _write_report(REPORT_PATH, existing, "full", processed,
                               time.time() - t0, merged_empty)
        print(f"DONE: processed {processed} day(s) in {time.time()-t0:.1f}s -- "
              f"ledger now {report['days_with_rows']} days-with-rows + "
              f"{report['n_empty_days']} empty days = "
              f"{report['forward_trading_days_covered']} forward trading days, "
              f"{report['total_rows']} total rows", flush=True)

    return 0


# ---------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="process exactly 1 (the earliest) missing day, symbols "
                         "capped to 25, written to *_smoke sidecar outputs only "
                         "(never touches the real ledger/report)")
    a = ap.parse_args()
    return run_catchup(smoke=a.smoke)


if __name__ == "__main__":
    raise SystemExit(main())
