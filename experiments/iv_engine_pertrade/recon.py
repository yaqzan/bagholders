"""RECON script for iv_engine_pertrade (per-trade F2-gamma engine validation gate).

READ-ONLY. No MySQL writes, no engine edits, no commits, no queue submissions.
Light foreground only: indexed aggregates + a few bounded/chunked queries
(each timed; anything slow is flagged for the queue instead of the ledger build).

Reuses existing caches wherever possible (zero new MySQL cost):
  .cache/rel_strength/rs_ledger.parquet          -- 10y 70+ CALL signal universe (thru 2026-05-15)
  .cache/iv_skew/iv_ledger.parquet               -- matched ATM-call ledger (2025-02-11..2026-05-15)
  .cache/iv_skew/iv_ledger_ext.parquet           -- matched ATM-call ledger ext (2026-05-18..2026-07-06)
  .cache/experiment_data/option_slice_ivledger.parquet -- +/-25% near-money chain per matched signal
  .cache/iv_premium_model/vix_series.parquet     -- MarketRegime.vix_close by date, 1995..now

New live MySQL touches (all small/bounded, timed):
  1. option_prices MIN/MAX(date)                          -- indexed, instant
  2. information_schema.TABLES row-count estimates          -- instant
  3. options: distinct symbol count                          -- small table
  4. option_prices: monthly row/contract/volume/OI health    -- chunked by month (date index)
  5. scores: candidate 70+ call signal count for the ONE gap month rs_ledger doesn't cover
     (2026-05-16..2026-06-15) -- bounded, ~1 month

Usage: python experiments/iv_engine_pertrade/recon.py
ASCII-only stdout (PYTHONUTF8=1).
"""
from __future__ import annotations

import os
import sys
import time
import json
from datetime import date, timedelta

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)          # pin repo root FIRST -- worktree PYTHONPATH trap guard

import polars as pl  # noqa: E402

from strategy_config import CALIBRATION_CUTOFF_DATE  # noqa: E402
from database.trader_database import DB  # noqa: E402
from database.models.core import AlgorithmVersion  # noqa: E402

CUTOFF = CALIBRATION_CUTOFF_DATE  # "2026-06-15"

RS_LEDGER = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")
IV_LEDGER = os.path.join(_ROOT, ".cache", "iv_skew", "iv_ledger.parquet")
IV_LEDGER_EXT = os.path.join(_ROOT, ".cache", "iv_skew", "iv_ledger_ext.parquet")
OPTION_SLICE = os.path.join(_ROOT, ".cache", "experiment_data", "option_slice_ivledger.parquet")
VIX_SERIES = os.path.join(_ROOT, ".cache", "iv_premium_model", "vix_series.parquet")

OUT_JSON = os.path.join(_HERE, "recon_results.json")
OUT_TXT = os.path.join(_HERE, "recon_results.txt")

report = {}
lines = []


def log(msg):
    print(msg, flush=True)
    lines.append(str(msg))


def timed(label, fn):
    t0 = time.time()
    r = fn()
    dt = time.time() - t0
    log(f"  [{label}] {dt:.2f}s" + (" *** SLOW (>5s) ***" if dt > 5 else ""))
    return r, dt


def main():
    t_start = time.time()
    log("=== iv_engine_pertrade RECON ===")
    log(f"CALIBRATION_CUTOFF_DATE = {CUTOFF}")

    av = AlgorithmVersion.get_active_scores_version()
    log(f"active scores version: id={av.id} commit={av.git_commit}")
    report["active_version_id"] = av.id
    report["active_version_commit"] = av.git_commit

    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=60000")  # 60s guard, never blind-retry

    # ------------------------------------------------------------------
    # 1. option_prices / options coverage (indexed, should be instant)
    # ------------------------------------------------------------------
    log("\n--- A. option_prices / options coverage ---")
    (op_min, op_max), dt = timed("op MIN/MAX(date)", lambda: DB.execute_sql(
        "SELECT MIN(date), MAX(date) FROM option_prices").fetchone())
    log(f"  option_prices date range: {op_min} .. {op_max}")

    (op_rows,), dt = timed("op TABLE_ROWS estimate", lambda: DB.execute_sql(
        "SELECT TABLE_ROWS FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='option_prices'").fetchone())
    log(f"  option_prices approx rows (information_schema): {op_rows:,}")

    (opt_rows,), dt = timed("options TABLE_ROWS estimate", lambda: DB.execute_sql(
        "SELECT TABLE_ROWS FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='options'").fetchone())
    log(f"  options approx rows (information_schema): {opt_rows:,}")

    (n_sym,), dt = timed("options distinct symbols", lambda: DB.execute_sql(
        "SELECT COUNT(DISTINCT symbol) FROM options").fetchone())
    log(f"  options distinct symbols: {n_sym:,}")

    (exp_min, exp_max), dt = timed("options expiration MIN/MAX", lambda: DB.execute_sql(
        "SELECT MIN(expiration_date), MAX(expiration_date) FROM options").fetchone())
    log(f"  options expiration_date range: {exp_min} .. {exp_max}")

    report["option_prices"] = {
        "min_date": str(op_min), "max_date": str(op_max), "approx_rows": op_rows,
    }
    report["options"] = {
        "approx_rows": opt_rows, "distinct_symbols": n_sym,
        "expiration_min": str(exp_min), "expiration_max": str(exp_max),
    }

    # ------------------------------------------------------------------
    # 2. Monthly health chunks: rows / distinct contracts / volume+OI health
    #    Chunked by month so each query is bounded by the (date) index.
    # ------------------------------------------------------------------
    log("\n--- A2. option_prices monthly health (chunked, timed) ---")
    op_min_d = op_min if isinstance(op_min, date) else date.fromisoformat(str(op_min)[:10])
    op_max_d = op_max if isinstance(op_max, date) else date.fromisoformat(str(op_max)[:10])

    # PRE-CAPTURED from the first (crashed-later, but this section completed
    # cleanly) run of this exact script, 2026-07-12 -- reused verbatim rather
    # than re-scanning the 90M-row table a second time (MySQL discipline: this
    # loop already ran 6-25s/chunk, ~3min cumulative; a bug further downstream
    # is not a reason to re-pay that cost). Delete this block and uncomment
    # the live loop below to refresh.
    monthly = [
        {"year_month": "2025-02", "n_rows": 3193115, "n_distinct_contracts": 337407, "zero_volume_frac": 0.7816, "avg_volume": 30.7, "zero_oi_frac": 0.2130, "avg_oi": None, "elapsed_s": 4.53},
        {"year_month": "2025-03", "n_rows": 2011737, "n_distinct_contracts": 383136, "zero_volume_frac": 0.7974, "avg_volume": 25.0, "zero_oi_frac": 0.0890, "avg_oi": None, "elapsed_s": 3.37},
        {"year_month": "2025-04", "n_rows": 6437679, "n_distinct_contracts": 412356, "zero_volume_frac": 0.7961, "avg_volume": 26.3, "zero_oi_frac": 0.1433, "avg_oi": None, "elapsed_s": 10.74},
        {"year_month": "2025-05", "n_rows": 3608117, "n_distinct_contracts": 419435, "zero_volume_frac": 0.8083, "avg_volume": 30.4, "zero_oi_frac": 0.1082, "avg_oi": None, "elapsed_s": 6.55},
        {"year_month": "2025-06", "n_rows": 3815230, "n_distinct_contracts": 422240, "zero_volume_frac": 0.7867, "avg_volume": 34.9, "zero_oi_frac": 0.1639, "avg_oi": None, "elapsed_s": 6.13},
        {"year_month": "2025-07", "n_rows": 3942252, "n_distinct_contracts": 374342, "zero_volume_frac": 0.7929, "avg_volume": 30.8, "zero_oi_frac": 0.1411, "avg_oi": None, "elapsed_s": 8.47},
        {"year_month": "2025-08", "n_rows": 4103193, "n_distinct_contracts": 429911, "zero_volume_frac": 0.7962, "avg_volume": 32.8, "zero_oi_frac": 0.0998, "avg_oi": None, "elapsed_s": 7.80},
        {"year_month": "2025-09", "n_rows": 4692071, "n_distinct_contracts": 452701, "zero_volume_frac": 0.7749, "avg_volume": 44.3, "zero_oi_frac": 0.0876, "avg_oi": None, "elapsed_s": 9.01},
        {"year_month": "2025-10", "n_rows": 5536436, "n_distinct_contracts": 500458, "zero_volume_frac": 0.7647, "avg_volume": 42.9, "zero_oi_frac": 0.0685, "avg_oi": None, "elapsed_s": 9.49},
        {"year_month": "2025-11", "n_rows": 4491377, "n_distinct_contracts": 511919, "zero_volume_frac": 0.7677, "avg_volume": 39.6, "zero_oi_frac": 0.0869, "avg_oi": None, "elapsed_s": 10.02},
        {"year_month": "2025-12", "n_rows": 5301164, "n_distinct_contracts": 511400, "zero_volume_frac": 0.7756, "avg_volume": 29.0, "zero_oi_frac": 0.1288, "avg_oi": None, "elapsed_s": 11.58},
        {"year_month": "2026-01", "n_rows": 5331296, "n_distinct_contracts": 553825, "zero_volume_frac": 0.7946, "avg_volume": 27.8, "zero_oi_frac": 0.1094, "avg_oi": None, "elapsed_s": 9.85},
        {"year_month": "2026-02", "n_rows": 5146059, "n_distinct_contracts": 516822, "zero_volume_frac": 0.7819, "avg_volume": 31.2, "zero_oi_frac": 0.0735, "avg_oi": None, "elapsed_s": 9.04},
        {"year_month": "2026-03", "n_rows": 4710097, "n_distinct_contracts": 567809, "zero_volume_frac": 0.7899, "avg_volume": 26.2, "zero_oi_frac": 0.0767, "avg_oi": None, "elapsed_s": 11.44},
        {"year_month": "2026-04", "n_rows": 9609680, "n_distinct_contracts": 668094, "zero_volume_frac": 0.7846, "avg_volume": 30.2, "zero_oi_frac": 0.1521, "avg_oi": None, "elapsed_s": 19.59},
        {"year_month": "2026-05", "n_rows": 9892650, "n_distinct_contracts": 761614, "zero_volume_frac": 0.8038, "avg_volume": 28.9, "zero_oi_frac": 0.1310, "avg_oi": None, "elapsed_s": 21.85},
        {"year_month": "2026-06", "n_rows": 10906422, "n_distinct_contracts": 797692, "zero_volume_frac": 0.7920, "avg_volume": 28.1, "zero_oi_frac": 0.0948, "avg_oi": None, "elapsed_s": 25.06},
        {"year_month": "2026-07", "n_rows": 4053355, "n_distinct_contracts": 637754, "zero_volume_frac": 0.8026, "avg_volume": 22.6, "zero_oi_frac": 0.1238, "avg_oi": None, "elapsed_s": 9.30},
    ]
    slow_flagged = [m["year_month"] for m in monthly if m["elapsed_s"] > 5]
    for rec in monthly:
        log(f"    {rec['year_month']}: rows={rec['n_rows']:,} contracts={rec['n_distinct_contracts']:,} "
            f"zero_vol={rec['zero_volume_frac']} zero_oi={rec['zero_oi_frac']} avg_vol={rec['avg_volume']} "
            f"[{rec['elapsed_s']}s]")
    log(f"  NOTE: reused pre-captured monthly numbers (2026-07-12 run) -- this section took "
        f"~{sum(r['elapsed_s'] for r in monthly):.0f}s cumulative, {len(slow_flagged)}/{len(monthly)} "
        f"chunks >5s -- borderline for 'light foreground'; flag for the queue if re-run at full scope.")

    report["option_prices_monthly"] = monthly
    report["slow_monthly_queries"] = slow_flagged

    # ------------------------------------------------------------------
    # 3. Signal population (denominator): rs_ledger (thru 2026-05-15) +
    #    one live gap-month query (2026-05-16..CUTOFF) for the remainder.
    # ------------------------------------------------------------------
    log("\n--- C. Signal population (candidate 70+ CALL signals, denominator) ---")
    rs = pl.read_parquet(RS_LEDGER)
    log(f"  rs_ledger.parquet: N={rs.height} range={rs['date'].min()}..{rs['date'].max()}")

    op_cov_start = str(op_min_d)
    rs_win = rs.filter((pl.col("date") >= op_cov_start) & (pl.col("date") <= "2026-05-15") & (pl.col("overall") >= 70))
    log(f"  rs_ledger in option-coverage window [{op_cov_start}..2026-05-15], overall>=70: N={rs_win.height}")

    gap_start = "2026-05-16"
    # NOTE: literal '%' in DATE_FORMAT must be doubled ('%%Y-%%m') -- pymysql's
    # parameterized execute() runs the SQL string through Python `%`-formatting
    # to splice in the %s placeholders, so an unescaped '%Y' is misparsed as a
    # format directive (crashed the first run of this script with "unsupported
    # format character 'Y'"). New trap for traps.md if not already documented.
    sql_gap = (
        "SELECT DATE_FORMAT(date,'%%Y-%%m') ym, "
        "SUM(overall>=75) n75, SUM(overall BETWEEN 70 AND 74) n70_74, COUNT(*) n_total "
        "FROM scores WHERE version_id=%s AND date BETWEEN %s AND %s AND overall>=70 "
        "GROUP BY ym ORDER BY ym"
    )
    gap_rows, dt = timed("scores gap-month query", lambda: DB.execute_sql(
        sql_gap, (av.id, gap_start, CUTOFF)).fetchall())
    log(f"  live scores gap query [{gap_start}..{CUTOFF}] version_id={av.id}:")
    gap_by_month = {}
    for ym, n75, n7074, ntot in gap_rows:
        gap_by_month[ym] = {"n75plus": int(n75 or 0), "n70_74": int(n7074 or 0), "n_total": int(ntot or 0)}
        log(f"    {ym}: 75+={n75} 70-74={n7074} total={ntot}")

    # rs_ledger by month/band
    rs_by_month = {}
    rsm = rs_win.with_columns(pl.col("date").str.slice(0, 7).alias("ym"))
    for ym in sorted(rsm["ym"].unique().to_list()):
        sub = rsm.filter(pl.col("ym") == ym)
        n75 = sub.filter(pl.col("overall") >= 75).height
        n7074 = sub.filter((pl.col("overall") >= 70) & (pl.col("overall") <= 74)).height
        rs_by_month[ym] = {"n75plus": n75, "n70_74": n7074, "n_total": sub.height}

    # BUG FIX (caught on first read of results, before DESIGN.md was finalized):
    # rs_win is filtered <= "2026-05-15" so rs_by_month HAS a "2026-05" key (the
    # 1st-15th), and the live gap query (2026-05-16..2026-06-15) ALSO produces a
    # "2026-05" key (the 16th-31st) -- a naive {**a, **b} merge silently
    # OVERWRITES rs_by_month's half-month count instead of adding the two
    # half-month contributions for the same calendar month. Merge additively.
    denom_by_month = {}
    for src in (rs_by_month, gap_by_month):
        for ym, v in src.items():
            if ym in denom_by_month:
                prev = denom_by_month[ym]
                denom_by_month[ym] = {k: prev[k] + v[k] for k in v}
            else:
                denom_by_month[ym] = dict(v)
    total_denom_75 = sum(v["n75plus"] for v in denom_by_month.values())
    total_denom_7074 = sum(v["n70_74"] for v in denom_by_month.values())
    log(f"  TOTAL candidate signals in [{op_cov_start}..{CUTOFF}]: 75+={total_denom_75} 70-74={total_denom_7074} "
        f"(sum={total_denom_75+total_denom_7074})")
    report["denominator_by_month"] = denom_by_month
    report["denominator_total"] = {"n75plus": total_denom_75, "n70_74": total_denom_7074}

    # ------------------------------------------------------------------
    # 4. Matched-chain numerator: iv_ledger (thru 5/15) + iv_ledger_ext gap
    #    (is_oos==False, i.e. date<=CUTOFF), by month/band.
    # ------------------------------------------------------------------
    log("\n--- C2. Matched ATM-call numerator (from cached ledgers, zero new MySQL) ---")
    ivl = pl.read_parquet(IV_LEDGER)
    log(f"  iv_ledger.parquet: N={ivl.height} range={ivl['date'].min()}..{ivl['date'].max()}")
    ivl_win = ivl.filter((pl.col("date") >= op_cov_start) & (pl.col("date") <= "2026-05-15"))

    ivx = pl.read_parquet(IV_LEDGER_EXT) if os.path.exists(IV_LEDGER_EXT) else None
    if ivx is not None:
        log(f"  iv_ledger_ext.parquet: N={ivx.height} range={ivx['date'].min()}..{ivx['date'].max()} "
            f"(is_oos counts: {ivx['is_oos'].value_counts().to_dicts()})")
        ivx_win = ivx.filter(~pl.col("is_oos"))
    else:
        log("  iv_ledger_ext.parquet: MISSING")
        ivx_win = pl.DataFrame()

    num_by_month = {}
    for label, frame in [("main", ivl_win), ("ext_gap", ivx_win)]:
        if frame.height == 0:
            continue
        fm = frame.with_columns(pl.col("date").cast(pl.Utf8).str.slice(0, 7).alias("ym"))
        for ym in sorted(fm["ym"].unique().to_list()):
            sub = fm.filter(pl.col("ym") == ym)
            n75 = sub.filter(pl.col("overall") >= 75).height
            n7074 = sub.filter((pl.col("overall") >= 70) & (pl.col("overall") <= 74)).height
            n_ripe75 = sub.filter((pl.col("overall") >= 75) & pl.col("pnl15").is_not_null()).height
            n_ripe7074 = sub.filter((pl.col("overall") >= 70) & (pl.col("overall") <= 74) & pl.col("pnl15").is_not_null()).height
            prev = num_by_month.get(ym, {"n75plus": 0, "n70_74": 0, "n75plus_ripe": 0, "n70_74_ripe": 0})
            num_by_month[ym] = {
                "n75plus": prev["n75plus"] + n75, "n70_74": prev["n70_74"] + n7074,
                "n75plus_ripe": prev["n75plus_ripe"] + n_ripe75, "n70_74_ripe": prev["n70_74_ripe"] + n_ripe7074,
            }

    total_num_75 = sum(v["n75plus"] for v in num_by_month.values())
    total_num_7074 = sum(v["n70_74"] for v in num_by_month.values())
    total_num_75_ripe = sum(v["n75plus_ripe"] for v in num_by_month.values())
    total_num_7074_ripe = sum(v["n70_74_ripe"] for v in num_by_month.values())
    log(f"  TOTAL matched (kept): 75+={total_num_75} 70-74={total_num_7074}")
    log(f"  TOTAL matched AND ripe (pnl15 not null): 75+={total_num_75_ripe} 70-74={total_num_7074_ripe}")
    report["numerator_by_month"] = num_by_month
    report["numerator_total"] = {
        "n75plus": total_num_75, "n70_74": total_num_7074,
        "n75plus_ripe": total_num_75_ripe, "n70_74_ripe": total_num_7074_ripe,
    }

    match_rate_75 = total_num_75 / total_denom_75 if total_denom_75 else None
    match_rate_7074 = total_num_7074 / total_denom_7074 if total_denom_7074 else None
    log(f"  match rate (matched/candidate): 75+={match_rate_75:.1%} 70-74={match_rate_7074:.1%}" if match_rate_75 and match_rate_7074 else "  match rate: N/A")
    report["match_rate"] = {"n75plus": match_rate_75, "n70_74": match_rate_7074}

    # ------------------------------------------------------------------
    # 5. Liquidity stratum on the matched population: reconstruct the
    #    EXACT atm_call() selection from option_slice_ivledger.parquet
    #    (mirrors build_iv.py's ORDER BY ABS(moneyness-1) LIMIT 1, 20-45 DTE,
    #    iv in [0.05,5.0], price>0, open_interest>0) then inspect its volume.
    # ------------------------------------------------------------------
    log("\n--- liquidity: matched-contract volume/OI (reconstructed from option_slice_ivledger) ---")
    if os.path.exists(OPTION_SLICE):
        sl = pl.read_parquet(OPTION_SLICE)
        log(f"  option_slice_ivledger.parquet: N={sl.height} signal-dates={sl.select(['symbol','date']).unique().height}")
        cand = sl.filter(
            (pl.col("option_type") == "call") & (pl.col("dte") >= 20) & (pl.col("dte") <= 45)
            & (pl.col("iv") >= 0.05) & (pl.col("iv") <= 5.0) & (pl.col("price") > 0)
            & (pl.col("open_interest") > 0)
        ).with_columns((pl.col("moneyness") - 1.0).abs().alias("_dist"))
        chosen = (
            cand.sort(["symbol", "date", "_dist"])
            .group_by(["symbol", "date"], maintain_order=True)
            .first()
        )
        n_chosen = chosen.height
        n_zero_vol = chosen.filter(pl.col("volume") == 0).height
        n_vol_lt5 = chosen.filter(pl.col("volume") < 5).height
        n_vol_ge5 = chosen.filter(pl.col("volume") >= 5).height
        log(f"  reconstructed chosen-ATM-call rows: N={n_chosen}")
        log(f"    zero-volume: {n_zero_vol} ({n_zero_vol/n_chosen:.1%})" if n_chosen else "    N=0")
        log(f"    volume<5: {n_vol_lt5} ({n_vol_lt5/n_chosen:.1%})" if n_chosen else "")
        log(f"    volume>=5 (liquid stratum): {n_vol_ge5} ({n_vol_ge5/n_chosen:.1%})" if n_chosen else "")
        vol_q = chosen["volume"].drop_nulls()
        oi_q = chosen["open_interest"].drop_nulls()
        if vol_q.len():
            qs = vol_q.quantile(0.10), vol_q.quantile(0.25), vol_q.quantile(0.50), vol_q.quantile(0.75), vol_q.quantile(0.90)
            log(f"    volume percentiles (p10/p25/p50/p75/p90): {qs}")
        if oi_q.len():
            qs2 = oi_q.quantile(0.10), oi_q.quantile(0.25), oi_q.quantile(0.50), oi_q.quantile(0.75), oi_q.quantile(0.90)
            log(f"    OI percentiles (p10/p25/p50/p75/p90): {qs2}")
        report["liquidity_matched_contracts"] = {
            "n_chosen": n_chosen, "n_zero_volume": n_zero_vol, "n_volume_lt5": n_vol_lt5, "n_volume_ge5": n_vol_ge5,
            "zero_volume_frac": round(n_zero_vol / n_chosen, 4) if n_chosen else None,
            "volume_ge5_frac": round(n_vol_ge5 / n_chosen, 4) if n_chosen else None,
        }
    else:
        log("  option_slice_ivledger.parquet MISSING -- skipped")
        report["liquidity_matched_contracts"] = {"skipped": True}

    # ------------------------------------------------------------------
    # 6. VIX join availability at signal entry dates (zero MySQL: use cache)
    # ------------------------------------------------------------------
    log("\n--- D. VIX join availability at signal entry dates ---")
    if os.path.exists(VIX_SERIES):
        vix = pl.read_parquet(VIX_SERIES)
        vix_dates = set(vix["date"].to_list())
        log(f"  vix_series.parquet: N={vix.height} range={vix['date'].min()}..{vix['date'].max()}")
        all_sig_dates = set(rs_win["date"].to_list()) | set(ivx_win["date"].cast(pl.Utf8).to_list() if ivx_win.height else [])
        hit = sum(1 for d in all_sig_dates if d in vix_dates)
        log(f"  signal dates checked: {len(all_sig_dates)}  VIX hit: {hit} ({hit/len(all_sig_dates):.2%})" if all_sig_dates else "  no signal dates to check")
        report["vix_join"] = {"n_signal_dates": len(all_sig_dates), "n_hit": hit}
    else:
        log("  vix_series.parquet MISSING -- skipped")
        report["vix_join"] = {"skipped": True}

    # ------------------------------------------------------------------
    # 7. Earnings-window overlap flag (cheap sanity: how common is it)
    # ------------------------------------------------------------------
    log("\n--- Sanity: option_prices freshness vs today ---")
    log(f"  option_prices MAX(date) = {op_max}  (today processing this recon)")

    elapsed = time.time() - t_start
    log(f"\n=== DONE in {elapsed:.1f}s ===")
    report["elapsed_s"] = round(elapsed, 2)

    with open(OUT_JSON, "w", encoding="ascii", errors="backslashreplace") as f:
        json.dump(report, f, indent=2, default=str)
    with open(OUT_TXT, "w", encoding="ascii", errors="replace") as f:
        f.write("\n".join(lines) + "\n")
    log(f"wrote {OUT_JSON}")
    log(f"wrote {OUT_TXT}")


if __name__ == "__main__":
    main()
