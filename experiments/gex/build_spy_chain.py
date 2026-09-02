"""GEX Phase 2, Track A -- SPY daily full-chain cache (DESIGN_CLUSTER.md Track A).

Track A studies SPY market-level GEX regime/escape dynamics, one row per
(date, strike, option_type) rather than per-name signal pairs (Track B, see
build_gex_cache.py). This module REUSES build_gex_cache.py's fetch machinery
verbatim (imported, not copied): the per-symbol batched query with
SET SESSION MAX_EXECUTION_TIME, the three filters (open_interest > 0,
iv in (0.01, 5.0) open interval, dte in [1, 180]), and the final output
schema (_FINAL_SCHEMA). Only the pair-SOURCE (all SPY trading dates, not
signal dates) and the chunking (date-batches of one symbol, not symbol-batches
of one date-set) differ.

Requested range per DESIGN_CLUSTER.md Track A ("Data"): SPY full chains, ALL
trading days 2025-02-10 -> 2026-07-06 (~350). The coverage probe (step 1)
checks this against the REAL option_prices/price_history bounds and reports
any shortfall honestly -- the actual signal set is the intersection, not the
requested range.

Restartable: per-date-chunk shards under .cache/experiment_data/spy_chain_shards/
(shard_{NNNN}.parquet + .meta.json sidecar), skip existing on rerun.
--finalize concatenates to .cache/experiment_data/spy_gex_chain.parquet +
spy_build_report.json. Schema is EXACTLY _FINAL_SCHEMA from build_gex_cache.py
(symbol column = 'SPY' for every row).

Usage:
    set PYTHONUTF8=1
    python experiments/gex/build_spy_chain.py --probe-only   # coverage probe only, no chain pull
    python experiments/gex/build_spy_chain.py --smoke        # first 2 covered dates -> *_smoke outputs
    python experiments/gex/build_spy_chain.py                # full build (queue only)
    python experiments/gex/build_spy_chain.py --finalize     # concat shards -> final outputs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import polars as pl

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from database.trader_database import DB  # noqa: E402

# Reused verbatim from build_gex_cache.py -- same fetch path, same filters,
# same output schema. Do not reimplement the chain query here.
from experiments.gex.build_gex_cache import (  # noqa: E402
    _FINAL_SCHEMA,
    _process_symbol,
    _recs_to_frame,
    _atomic_write_parquet,
    _atomic_write_json,
    CHAIN_TIMEOUT_MS,
)

SYMBOL = "SPY"
REQUESTED_START = "2025-02-10"
REQUESTED_END = "2026-07-06"

OUT_DIR = _ROOT / ".cache" / "experiment_data"
SHARDS_DIR = OUT_DIR / "spy_chain_shards"
PLAN_PATH = SHARDS_DIR / "dates_plan.json"

# Sized from smoke-chunk timing (see run_smoke docstring / build report).
# SPY chains run ~10k rows/date post-filter; a 20-30 date chunk in one
# `op.date IN (...)` query is expected to land well under CHAIN_TIMEOUT_MS
# (180s) even at the high end. Revised once smoke confirms per-date elapsed.
DATES_PER_CHUNK = 25


def _set_session_timeout(ms: int) -> None:
    try:
        DB.execute_sql(f"SET SESSION MAX_EXECUTION_TIME={int(ms)}")
    except Exception as e:
        print(f"WARNING: could not set session timeout: {repr(e)[:80]}", flush=True)


# ---------------------------------------------------------------- coverage probe

def probe_coverage() -> dict:
    """One cheap MIN/MAX/COUNT DISTINCT query against option_prices (join
    options) and one against price_history, for SPY only. Reports the
    requested Track A range vs the real probed bounds."""
    t0 = time.time()
    cur = DB.execute_sql(
        "SELECT MIN(op.date), MAX(op.date), COUNT(DISTINCT op.date) "
        "FROM options o JOIN option_prices op ON op.option_id = o.id "
        "WHERE o.symbol = %s", (SYMBOL,))
    opt_lo, opt_hi, opt_n = cur.fetchone()

    cur = DB.execute_sql(
        "SELECT MIN(date), MAX(date), COUNT(DISTINCT date) "
        "FROM price_history WHERE symbol = %s", (SYMBOL,))
    px_lo, px_hi, px_n = cur.fetchone()

    report = {
        "symbol": SYMBOL,
        "requested_range": [REQUESTED_START, REQUESTED_END],
        "option_prices_coverage": {
            "min_date": opt_lo.isoformat() if opt_lo else None,
            "max_date": opt_hi.isoformat() if opt_hi else None,
            "n_distinct_dates": int(opt_n) if opt_n else 0,
        },
        "price_history_coverage": {
            "min_date": px_lo.isoformat() if px_lo else None,
            "max_date": px_hi.isoformat() if px_hi else None,
            "n_distinct_dates": int(px_n) if px_n else 0,
        },
        "probe_elapsed_seconds": round(time.time() - t0, 2),
    }
    shortfall = None
    if opt_lo and opt_lo.isoformat() > REQUESTED_START:
        shortfall = (
            f"option_prices coverage starts {opt_lo.isoformat()}, "
            f"AFTER requested start {REQUESTED_START} -- Track A signal set "
            f"will be truncated to the intersection, not the full requested range"
        )
    report["shortfall_note"] = shortfall
    print(
        f"probe: option_prices SPY {report['option_prices_coverage']['min_date']}"
        f"..{report['option_prices_coverage']['max_date']} "
        f"n_dates={report['option_prices_coverage']['n_distinct_dates']} | "
        f"price_history SPY {report['price_history_coverage']['min_date']}"
        f"..{report['price_history_coverage']['max_date']} "
        f"n_dates={report['price_history_coverage']['n_distinct_dates']} "
        f"({report['probe_elapsed_seconds']}s)", flush=True)
    if shortfall:
        print(f"probe WARNING: {shortfall}", flush=True)
    return report


# ---------------------------------------------------------------- signal set

def build_signal_dates() -> list:
    """Intersect: SPY option_prices dates (join options) x SPY price_history
    dates, both within [REQUESTED_START, REQUESTED_END]. Two light queries
    (no join between the two tables -- avoids the 90M-row-join timeout risk
    build_gex_cache.py documents for the aggregate coverage attempt)."""
    cur = DB.execute_sql(
        "SELECT DISTINCT op.date FROM options o "
        "JOIN option_prices op ON op.option_id = o.id "
        "WHERE o.symbol = %s AND op.date BETWEEN %s AND %s",
        (SYMBOL, REQUESTED_START, REQUESTED_END))
    opt_dates = {r[0] for r in cur.fetchall()}

    cur = DB.execute_sql(
        "SELECT DISTINCT date FROM price_history "
        "WHERE symbol = %s AND date BETWEEN %s AND %s",
        (SYMBOL, REQUESTED_START, REQUESTED_END))
    px_dates = {r[0] for r in cur.fetchall()}

    both = sorted(opt_dates & px_dates)
    print(f"signal set: option_dates={len(opt_dates)} price_dates={len(px_dates)} "
          f"intersection={len(both)}", flush=True)
    return [d.isoformat() for d in both]


# ---------------------------------------------------------------- chunked build

def _chunk(seq: list, n: int) -> list:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def build_full(dates_per_chunk: int = DATES_PER_CHUNK) -> int:
    _set_session_timeout(CHAIN_TIMEOUT_MS)
    SHARDS_DIR.mkdir(parents=True, exist_ok=True)

    if PLAN_PATH.exists():
        with open(PLAN_PATH, encoding="ascii") as f:
            plan = json.load(f)
        dates = plan["dates"]
        dates_per_chunk = plan["dates_per_chunk"]
        print(f"resuming persisted plan ({len(dates)} dates, "
              f"chunk={dates_per_chunk})", flush=True)
    else:
        dates = build_signal_dates()
        plan = {
            "symbol": SYMBOL,
            "requested_range": [REQUESTED_START, REQUESTED_END],
            "dates": dates,
            "dates_per_chunk": dates_per_chunk,
            "n_chunks": (len(dates) + dates_per_chunk - 1) // dates_per_chunk,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        tmp = PLAN_PATH.with_name(PLAN_PATH.name + ".tmp")
        with open(tmp, "w", encoding="ascii", errors="replace") as f:
            json.dump(plan, f, indent=2)
        os.replace(tmp, PLAN_PATH)
        print(f"plan persisted ({len(dates)} dates, "
              f"{plan['n_chunks']} chunks of {dates_per_chunk})", flush=True)

    chunks = _chunk(dates, dates_per_chunk)
    t0 = time.time()
    for ci, chunk_dates in enumerate(chunks):
        shard = SHARDS_DIR / f"shard_{ci:04d}.parquet"
        meta_p = SHARDS_DIR / f"shard_{ci:04d}.meta.json"
        if shard.exists() and meta_p.exists():
            print(f"chunk {ci+1}/{len(chunks)}: shard exists, skip", flush=True)
            continue
        ct0 = time.time()
        recs, drops, chain_dates = _process_symbol(SYMBOL, chunk_dates)
        df = _recs_to_frame(recs)
        _atomic_write_parquet(df, shard)
        _atomic_write_json({
            "chunk_index": ci,
            "dates_requested": chunk_dates,
            "n_dates_requested": len(chunk_dates),
            "n_dates_with_chain": len(chain_dates),
            "rows": df.height,
            "filter_drop_counts": drops,
            "elapsed_seconds": round(time.time() - ct0, 1),
        }, meta_p)
        print(f"chunk {ci+1}/{len(chunks)} dates={len(chunk_dates)} "
              f"rows={df.height} with_chain={len(chain_dates)} "
              f"chunk_elapsed={time.time()-ct0:.1f}s total_elapsed={time.time()-t0:.0f}s",
              flush=True)

    n_chunks = plan.get("n_chunks", len(chunks))
    missing = [i for i in range(n_chunks)
               if not (SHARDS_DIR / f"shard_{i:04d}.parquet").exists()]
    if missing:
        print(f"build pass done but {len(missing)} shards missing -- "
              f"rerun to resume, then --finalize", flush=True)
        return 1
    print("all shards present -- finalizing", flush=True)
    return finalize()


# ---------------------------------------------------------------- finalize

def finalize() -> int:
    if not PLAN_PATH.exists():
        print("ERROR: no plan -- run a build first", flush=True)
        return 1
    with open(PLAN_PATH, encoding="ascii") as f:
        plan = json.load(f)
    n_chunks = plan["n_chunks"]

    present, missing = [], []
    for i in range(n_chunks):
        shard = SHARDS_DIR / f"shard_{i:04d}.parquet"
        meta = SHARDS_DIR / f"shard_{i:04d}.meta.json"
        (present if (shard.exists() and meta.exists()) else missing).append(i)

    frames, metas = [], []
    for i in present:
        frames.append(pl.read_parquet(SHARDS_DIR / f"shard_{i:04d}.parquet"))
        with open(SHARDS_DIR / f"shard_{i:04d}.meta.json", encoding="ascii") as f:
            metas.append(json.load(f))

    df = pl.concat(frames) if frames else pl.DataFrame(schema=_FINAL_SCHEMA)
    if df.height:
        df = df.sort(["date", "strike", "option_type"])

    drops = {"dte_out_of_range": 0, "open_interest_not_positive": 0, "iv_out_of_range": 0}
    n_dates_with_chain = 0
    n_dates_requested = 0
    for m in metas:
        for k in drops:
            drops[k] += m["filter_drop_counts"][k]
        n_dates_with_chain += m["n_dates_with_chain"]
        n_dates_requested += m["n_dates_requested"]

    rows_per_date = None
    if df.height:
        per_date = df.group_by("date").len()
        rows_per_date = {
            "min": int(per_date["len"].min()),
            "max": int(per_date["len"].max()),
            "mean": round(float(per_date["len"].mean()), 1),
            "median": int(per_date["len"].median()),
        }

    total_elapsed = round(sum(m["elapsed_seconds"] for m in metas), 1)
    report = {
        "symbol": SYMBOL,
        "requested_range": plan["requested_range"],
        "n_dates_requested": n_dates_requested,
        "n_dates_with_chain": n_dates_with_chain,
        "total_rows": df.height,
        "rows_per_date": rows_per_date,
        "build_complete": not missing,
        "n_chunks_present": len(present),
        "n_chunks_missing": len(missing),
        "missing_chunks": missing[:40],
        "filter_drop_counts": drops,
        "filter_drop_rates": {
            k: (round(v / (df.height + sum(drops.values())), 4)
                if (df.height + sum(drops.values())) else None)
            for k, v in drops.items()
        },
        "date_min": df["date"].min().isoformat() if df.height else None,
        "date_max": df["date"].max().isoformat() if df.height else None,
        "elapsed_seconds_sum_of_chunks": total_elapsed,
        "mode": "full",
    }
    _atomic_write_parquet(df, OUT_DIR / "spy_gex_chain.parquet")
    _atomic_write_json(report, OUT_DIR / "spy_build_report.json")
    print(f"wrote {OUT_DIR / 'spy_gex_chain.parquet'} "
          f"({(OUT_DIR / 'spy_gex_chain.parquet').stat().st_size/1e6:.1f} MB, "
          f"{df.height:,} rows)", flush=True)
    print(f"wrote {OUT_DIR / 'spy_build_report.json'} "
          f"(complete={report['build_complete']} missing_chunks={len(missing)})", flush=True)
    return 0 if not missing else 1


# ---------------------------------------------------------------- smoke

def run_smoke() -> int:
    """First 2 covered signal dates only. Direct write to *_smoke outputs;
    no shards, no plan. Two chunks of 1 date each so the report shows
    per-date elapsed cleanly."""
    _set_session_timeout(CHAIN_TIMEOUT_MS)
    t0 = time.time()
    dates = build_signal_dates()
    if not dates:
        print("smoke: no covered dates found -- aborting", flush=True)
        return 1
    chosen = dates[:2]
    print(f"smoke: chose dates {chosen}", flush=True)

    recs = []
    drops = {"dte_out_of_range": 0, "open_interest_not_positive": 0, "iv_out_of_range": 0}
    with_chain = 0
    per_date_elapsed = []
    for i, d in enumerate(chosen):
        dt0 = time.time()
        r, dr, cd = _process_symbol(SYMBOL, [d])
        recs.extend(r)
        for k in drops:
            drops[k] += dr[k]
        with_chain += len(cd)
        el = time.time() - dt0
        per_date_elapsed.append(el)
        print(f"date {i+1}/{len(chosen)} {d} rows={len(recs)} "
              f"elapsed={el:.1f}s (total={time.time()-t0:.1f}s)", flush=True)

    df = _recs_to_frame(recs)
    total_checked = df.height + sum(drops.values())
    rows_per_date = None
    if df.height:
        per_date = df.group_by("date").len()
        rows_per_date = {
            "min": int(per_date["len"].min()),
            "max": int(per_date["len"].max()),
            "mean": round(float(per_date["len"].mean()), 1),
        }
    mean_elapsed = sum(per_date_elapsed) / len(per_date_elapsed) if per_date_elapsed else 0.0
    n_full_dates = 350  # DESIGN_CLUSTER.md Track A estimate, ~2025-02-10..2026-07-06
    extrapolated_seconds = mean_elapsed * n_full_dates
    report = {
        "symbol": SYMBOL,
        "smoke_dates": chosen,
        "n_dates_requested": len(chosen),
        "n_dates_with_chain": with_chain,
        "total_rows": df.height,
        "rows_per_date": rows_per_date,
        "filter_drop_counts": drops,
        "filter_drop_rates": {
            k: (round(v / total_checked, 4) if total_checked else None)
            for k, v in drops.items()
        },
        "dtypes": {c: str(t) for c, t in zip(df.columns, df.dtypes)},
        "per_date_elapsed_seconds": [round(e, 1) for e in per_date_elapsed],
        "mean_elapsed_seconds_per_date": round(mean_elapsed, 2),
        "extrapolated_full_pull": {
            "n_dates_assumed": n_full_dates,
            "extrapolated_seconds": round(extrapolated_seconds, 0),
            "extrapolated_minutes": round(extrapolated_seconds / 60, 1),
            "extrapolated_hours": round(extrapolated_seconds / 3600, 2),
        },
        "date_min": df["date"].min().isoformat() if df.height else None,
        "date_max": df["date"].max().isoformat() if df.height else None,
        "elapsed_seconds": round(time.time() - t0, 1),
        "mode": "smoke",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_parquet(df, OUT_DIR / "spy_gex_chain_smoke.parquet")
    _atomic_write_json(report, OUT_DIR / "spy_build_report_smoke.json")
    print(f"wrote spy_gex_chain_smoke.parquet ({df.height:,} rows) + "
          f"spy_build_report_smoke.json in {report['elapsed_seconds']}s", flush=True)
    print(f"extrapolated full pull ({n_full_dates} dates @ "
          f"{mean_elapsed:.2f}s/date): {extrapolated_seconds/60:.1f} min "
          f"= {extrapolated_seconds/3600:.2f}h", flush=True)
    return 0


# ---------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-only", action="store_true",
                     help="coverage probe only (2 light queries), no chain pull, no signal-set build")
    ap.add_argument("--smoke", action="store_true",
                     help="first 2 covered signal dates -> *_smoke outputs")
    ap.add_argument("--dates-per-chunk", type=int, default=DATES_PER_CHUNK,
                     help="date-chunk size for the full build (default %(default)s)")
    ap.add_argument("--finalize", action="store_true",
                     help="concatenate existing shards -> spy_gex_chain.parquet + report (no DB)")
    a = ap.parse_args()

    if a.finalize:
        return finalize()
    if a.probe_only:
        probe_coverage()
        return 0
    if a.smoke:
        probe_coverage()
        return run_smoke()
    probe_coverage()
    return build_full(dates_per_chunk=a.dates_per_chunk)


if __name__ == "__main__":
    raise SystemExit(main())
