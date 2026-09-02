"""GEX experiment -- deliverable 1: full option-chain cache for dealer-gamma features.

Signal universe (DESIGN.md D5 + orchestrator amendments):
  - IN-SAMPLE: `.cache/rel_strength/rs_ledger.parquet`, date >= 2025-01-01.
  - FORWARD: `scores` table, date BETWEEN 2026-05-16 AND 2026-07-06, overall >= 70,
    version_id = AlgorithmVersion.get_active_scores_version().id (rs_ledger stops
    at 2026-05-15, so this is the only source for the OOS window the DESIGN.md
    holdout note requires the cache to include). Raw-SQL SELECT, no peewee row
    materialization (Score.symbol is a lazy FK -- never touch it per-row).
  - ORTHO PANEL: `.cache/iv_skew/proxy_ledger.parquet` filtered opt_skew NOT NULL
    (~1,998 pairs) -- the decisive orthogonalization panel, pulled FIRST.

COVERAGE-AWARE PAIR FILTER (default ON): most symbols' option_prices history is
far shorter than the signal window (e.g. AA starts 2026-05-06), so pairs whose
date falls outside the symbol's [MIN(op.date), MAX(op.date)] are guaranteed-empty
and are dropped BEFORE querying. The per-symbol coverage map (min/max/n_dates)
is built via ONE aggregate query when possible, else per-symbol probes, cached
to .cache/experiment_data/gex_coverage_map.parquet, and embedded in the build
report (downstream analysis needs it to reason about panel composition).
NOTE on the aggregate attempt: the client socket read_timeout is 30s
(database/trader_database.py -- not editable from an experiment). A GROUP BY
over the ~90M-row join does not stream until complete, so the server-side
MAX_EXECUTION_TIME for the attempt is set just BELOW 30s: if it cannot finish
in time the SERVER kills it cleanly (no client 2013 disconnect, no orphaned
server-side zombie query) and we fall back to per-symbol probes (~1 fast
indexed query per symbol).

CORE-FIRST ORDERING: pairs are pulled tier a (ortho panel) -> tier b (forward
window) -> tier c (remaining covered in-sample), so an interrupted job still
yields the decisive panels first.

RESTARTABLE SHARD WRITES: per-batch parquet shards (~20 symbols per shard,
deterministic names shard_{tier}_{NNNN}.parquet + .meta.json sidecar) under
.cache/experiment_data/gex_chain_shards/. A batch whose shard+meta both exist
is skipped (restart = resume). The pair plan (pairs_plan.parquet +
manifest.json) is persisted on first build so shard composition stays
deterministic across restarts even if the scores table gains rows between
runs. Writes are atomic (tmp + os.replace; parquet first, meta last; skip
only when BOTH exist). --finalize concatenates shards into
.cache/experiment_data/gex_chain.parquet + gex_build_report.json (no DB
access; runs automatically at the end of a complete build pass).

Chain query shape (experiments/iv_skew/build_option_slice.py + build_iv.py):
one batched query PER SYMBOL with `op.date IN (...)`, joining options x
option_prices x price_history in a single round trip (price_history PK is
(symbol, date) so the spot join is free). FULL chain -- no moneyness filter
(netGEX needs the whole chain, DESIGN.md D3). DTE kept at 1-180 and no
signal-date thinning (both explicitly rejected by the orchestrator).

Filters (DESIGN.md D3): open_interest > 0, iv in (0.01, 5.0), dte in [1, 180].
Drop counts tracked per filter, reported per shard and aggregated at finalize.

Holdout note (DESIGN.md hard constraints): the CACHE must include OOS dates
(2026-05-16..2026-07-06); no upper-date bound is applied here. Leak
enforcement happens at ANALYSIS time via experiments._holdout.

Usage:
    set PYTHONUTF8=1
    python experiments/gex/build_gex_cache.py --smoke              # coverage-aware 2-symbol smoke
    python experiments/gex/build_gex_cache.py --coverage-only      # coverage map + revised estimate
    python experiments/gex/build_gex_cache.py --limit-symbols 40   # partial (restartable) build
    python experiments/gex/build_gex_cache.py                      # full build (queue only)
    python experiments/gex/build_gex_cache.py --finalize           # concat shards -> final outputs
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

LEDGER = _ROOT / ".cache" / "rel_strength" / "rs_ledger.parquet"
PROXY_LEDGER = _ROOT / ".cache" / "iv_skew" / "proxy_ledger.parquet"
EXT_LEDGER = _ROOT / ".cache" / "iv_skew" / "iv_ledger_ext.parquet"
OUT_DIR = _ROOT / ".cache" / "experiment_data"
SHARDS_DIR = OUT_DIR / "gex_chain_shards"
COVERAGE_CACHE = OUT_DIR / "gex_coverage_map.parquet"
PLAN_PATH = SHARDS_DIR / "pairs_plan.parquet"
MANIFEST_PATH = SHARDS_DIR / "manifest.json"

SIGNAL_START = "2025-01-01"
FORWARD_START = "2026-05-16"
FORWARD_END = "2026-07-06"
FORWARD_MIN_OVERALL = 70

DTE_LO, DTE_HI = 1, 180
IV_LO, IV_HI = 0.01, 5.0
BATCH_SYMBOLS = 20

# Measured 2026-07-06 diagnostic (6 symbols, 465 signal-dates incl. uncovered
# ones, 136,300 rows, 292s): 0.628 s/pair blended rate. Used for the
# coordinator-requested revised estimate; covered-only rate runs higher since
# uncovered pairs return empty near-instantly.
SEC_PER_PAIR = 0.628

# Client socket read_timeout is 30s (database/trader_database.py). Server cap
# for the one-shot aggregate goes just below it -> clean server-side kill on
# overrun instead of a client 2013 + server-side zombie query.
AGG_TIMEOUT_MS = 28000
CHAIN_TIMEOUT_MS = 180000

_FINAL_SCHEMA: dict = {
    "symbol": pl.Utf8, "date": pl.Date, "spot": pl.Float64,
    "expiration": pl.Date, "dte": pl.Int32, "strike": pl.Float64,
    "option_type": pl.Utf8, "open_interest": pl.Int64, "iv": pl.Float64,
}

_SQL = (
    "SELECT op.date AS sig_date, ph.close AS spot, "
    "       o.option_type, o.strike_price, o.expiration_date, "
    "       DATEDIFF(o.expiration_date, op.date) AS dte, "
    "       op.open_interest, op.iv "
    "FROM options o "
    "JOIN option_prices op ON op.option_id = o.id "
    "JOIN price_history ph ON ph.symbol = o.symbol AND ph.date = op.date "
    "WHERE o.symbol = %s AND op.date IN ({placeholders}) "
    "  AND DATEDIFF(o.expiration_date, op.date) BETWEEN %s AND %s"
)


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


# ---------------------------------------------------------------- pair sources

def _load_insample_pairs() -> set:
    led = pl.read_parquet(LEDGER).select(["symbol", "date"]).unique()
    led = led.filter(pl.col("date") >= SIGNAL_START)
    return {(s, str(d)[:10]) for s, d in zip(led["symbol"].to_list(), led["date"].to_list())}


def _load_forward_pairs() -> set:
    from database.models.core import AlgorithmVersion
    version = AlgorithmVersion.get_active_scores_version()
    if version is None:
        print("WARNING: no active scores version -- forward window skipped", flush=True)
        return set()
    cur = DB.execute_sql(
        "SELECT symbol, date FROM scores "
        "WHERE version_id = %s AND overall >= %s AND date BETWEEN %s AND %s",
        (version.id, FORWARD_MIN_OVERALL, FORWARD_START, FORWARD_END),
    )
    return {(s, d.isoformat() if hasattr(d, "isoformat") else str(d)[:10])
            for s, d in cur.fetchall()}


def _load_ortho_pairs() -> set:
    if not PROXY_LEDGER.exists():
        print("WARNING: proxy_ledger.parquet missing -- ortho tier empty", flush=True)
        return set()
    df = (pl.read_parquet(PROXY_LEDGER)
          .filter(pl.col("opt_skew").is_not_null())
          .select(["symbol", "date"]).unique())
    return {(s, str(d)[:10]) for s, d in zip(df["symbol"].to_list(), df["date"].to_list())}


def _load_ext_pairs() -> set:
    """Coordinator delta: iv_ledger_ext.parquet (845 rows, 2026-05-18..2026-07-06,
    238 symbols) -- belt-and-suspenders union into tier b (forward) alongside the
    scores-table forward pairs, in case its signal definition diverges from the
    forward-window query for a handful of rows."""
    if not EXT_LEDGER.exists():
        print("WARNING: iv_ledger_ext.parquet missing -- ext tier empty", flush=True)
        return set()
    df = pl.read_parquet(EXT_LEDGER).select(["symbol", "date"]).unique()
    return {(s, str(d)[:10]) for s, d in zip(df["symbol"].to_list(), df["date"].to_list())}


def _options_symbol_universe() -> set:
    cur = DB.execute_sql("SELECT DISTINCT symbol FROM options")
    return {r[0] for r in cur.fetchall()}


# ---------------------------------------------------------------- coverage map

def _probe_symbol_coverage(sym: str):
    cur = DB.execute_sql(
        "SELECT MIN(op.date), MAX(op.date), COUNT(DISTINCT op.date) "
        "FROM options o JOIN option_prices op ON op.option_id = o.id "
        "WHERE o.symbol = %s", (sym,))
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return (row[0].isoformat(), row[1].isoformat(), int(row[2]))


def _try_aggregate_coverage():
    """One-shot coverage aggregate; clean server-side kill on overrun."""
    _set_session_timeout(AGG_TIMEOUT_MS)
    try:
        cur = DB.execute_sql(
            "SELECT o.symbol, MIN(op.date), MAX(op.date), COUNT(DISTINCT op.date) "
            "FROM options o JOIN option_prices op ON op.option_id = o.id "
            "GROUP BY o.symbol")
        return {sym: (lo.isoformat(), hi.isoformat(), int(n))
                for sym, lo, hi, n in cur.fetchall()}
    except Exception as e:
        print(f"aggregate coverage query failed ({repr(e)[:90]}) -- "
              f"falling back to per-symbol probes", flush=True)
        _safe_close()
        return None
    finally:
        _set_session_timeout(CHAIN_TIMEOUT_MS)


def build_coverage_map(symbols: list, refresh: bool = False) -> pl.DataFrame:
    """Per-symbol option_prices coverage: min_date, max_date, n_dates, probe_ok.
    Cached to COVERAGE_CACHE; reused when it covers all requested symbols."""
    symbols = sorted(set(symbols))
    if COVERAGE_CACHE.exists() and not refresh:
        cov = pl.read_parquet(COVERAGE_CACHE)
        if set(symbols) <= set(cov["symbol"].to_list()):
            print(f"coverage map: cache hit ({cov.height} symbols)", flush=True)
            return cov
        print("coverage map: cache incomplete -- rebuilding", flush=True)

    t0 = time.time()
    recs = []
    agg = _try_aggregate_coverage()
    if agg is not None:
        print(f"coverage map: aggregate query ok ({len(agg)} symbols, "
              f"{time.time()-t0:.0f}s)", flush=True)
        for s in symbols:
            lo, hi, n = agg.get(s, (None, None, 0))
            recs.append({"symbol": s, "min_date": lo, "max_date": hi,
                         "n_dates": n, "probe_ok": True})
    else:
        print(f"coverage map: probing {len(symbols)} symbols individually", flush=True)
        for i, s in enumerate(symbols):
            try:
                r = _probe_symbol_coverage(s)
                recs.append({"symbol": s,
                             "min_date": r[0] if r else None,
                             "max_date": r[1] if r else None,
                             "n_dates": r[2] if r else 0,
                             "probe_ok": True})
            except Exception as e:
                print(f"  probe failed {s}: {repr(e)[:80]}", flush=True)
                _safe_close()
                _set_session_timeout(CHAIN_TIMEOUT_MS)
                recs.append({"symbol": s, "min_date": None, "max_date": None,
                             "n_dates": 0, "probe_ok": False})
            if (i + 1) % 50 == 0:
                print(f"  coverage probe {i+1}/{len(symbols)} "
                      f"({time.time()-t0:.0f}s)", flush=True)

    cov = pl.DataFrame(recs, schema={"symbol": pl.Utf8, "min_date": pl.Utf8,
                                     "max_date": pl.Utf8, "n_dates": pl.Int64,
                                     "probe_ok": pl.Boolean})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = COVERAGE_CACHE.with_name(COVERAGE_CACHE.name + ".tmp")
    cov.write_parquet(tmp)
    os.replace(tmp, COVERAGE_CACHE)
    print(f"coverage map: built {cov.height} symbols in {time.time()-t0:.0f}s "
          f"-> {COVERAGE_CACHE.name}", flush=True)
    return cov


def coverage_summary(cov: pl.DataFrame) -> dict:
    ok = cov.filter(pl.col("min_date").is_not_null())
    spans = ok.with_columns(
        (pl.col("max_date").str.strptime(pl.Date, "%Y-%m-%d")
         - pl.col("min_date").str.strptime(pl.Date, "%Y-%m-%d"))
        .dt.total_days().alias("span_days"))
    return {
        "n_symbols_probed": cov.height,
        "n_symbols_zero_option_rows": cov.height - ok.height,
        "n_probe_failed": cov.filter(~pl.col("probe_ok")).height,
        "span_ge_365d": spans.filter(pl.col("span_days") >= 365).height,
        "span_180_365d": spans.filter(
            (pl.col("span_days") >= 180) & (pl.col("span_days") < 365)).height,
        "span_90_180d": spans.filter(
            (pl.col("span_days") >= 90) & (pl.col("span_days") < 180)).height,
        "span_lt_90d": spans.filter(pl.col("span_days") < 90).height,
        "median_span_days": int(spans["span_days"].median()) if spans.height else None,
        "median_n_dates": int(ok["n_dates"].median()) if ok.height else None,
    }


# ---------------------------------------------------------------- planning

def plan_pairs(refresh_coverage: bool = False):
    """Tiered, coverage-filtered pair plan + accounting manifest."""
    p_in = _load_insample_pairs()
    p_fw_scores = _load_forward_pairs()
    p_or = _load_ortho_pairs()
    p_ext = _load_ext_pairs()
    ext_not_in_forward = p_ext - p_fw_scores
    p_fw = p_fw_scores | p_ext  # tier b = scores-table forward U iv_ledger_ext
    p_all = p_in | p_fw | p_or

    universe = _options_symbol_universe()
    syms_no_opt = {s for s, _ in p_all} - universe
    pairs_no_opt = {(s, d) for (s, d) in p_all if s in syms_no_opt}

    cov = build_coverage_map(sorted({s for s, _ in p_all} & universe),
                             refresh=refresh_coverage)
    cov_map = {r["symbol"]: (r["min_date"], r["max_date"], r["probe_ok"])
               for r in cov.iter_rows(named=True)}

    def covered(s: str, d: str) -> bool:
        c = cov_map.get(s)
        if c is None:
            return False
        lo, hi, probe_ok = c
        if not probe_ok:
            return True   # unknown coverage -> conservative include
        if lo is None:
            return False  # probed fine, zero option rows
        return lo <= d <= hi

    c_all = {(s, d) for (s, d) in (p_all - pairs_no_opt) if covered(s, d)}
    tier_a = p_or & c_all
    tier_b = (p_fw & c_all) - tier_a
    tier_c = (p_in & c_all) - tier_a - tier_b

    covered_forward = {(s, d) for (s, d) in c_all if d >= FORWARD_START}

    rows = ([{"tier": "a", "symbol": s, "date": d} for s, d in sorted(tier_a)]
            + [{"tier": "b", "symbol": s, "date": d} for s, d in sorted(tier_b)]
            + [{"tier": "c", "symbol": s, "date": d} for s, d in sorted(tier_c)])
    plan = pl.DataFrame(rows, schema={"tier": pl.Utf8, "symbol": pl.Utf8, "date": pl.Utf8})

    expected_shards = []
    for tier in ("a", "b", "c"):
        syms = sorted({r["symbol"] for r in rows if r["tier"] == tier})
        n_batches = (len(syms) + BATCH_SYMBOLS - 1) // BATCH_SYMBOLS
        expected_shards.extend(f"shard_{tier}_{i:04d}.parquet" for i in range(n_batches))

    manifest = {
        "signal_start": SIGNAL_START,
        "forward_window": [FORWARD_START, FORWARD_END],
        "forward_min_overall": FORWARD_MIN_OVERALL,
        "dte_range": [DTE_LO, DTE_HI],
        "iv_range": [IV_LO, IV_HI],
        "pairs_pre_coverage": len(p_all),
        "pairs_insample_source": len(p_in),
        "pairs_forward_source": len(p_fw),
        "pairs_forward_scores_only": len(p_fw_scores),
        "pairs_ext_ledger_source": len(p_ext),
        "pairs_ext_not_in_forward_scores": len(ext_not_in_forward),
        "pairs_ortho_source": len(p_or),
        "pairs_dropped_symbol_no_options": len(pairs_no_opt),
        "pairs_dropped_outside_coverage": len(p_all) - len(pairs_no_opt) - len(c_all),
        "pairs_covered": len(c_all),
        "pairs_covered_insample": len(c_all) - len(covered_forward),
        "pairs_covered_forward": len(covered_forward),
        "tier_a_ortho": len(tier_a),
        "tier_b_forward": len(tier_b),
        "tier_c_insample_rest": len(tier_c),
        "estimate_seconds_at_0628_per_pair": round(len(c_all) * SEC_PER_PAIR),
        "batch_symbols": BATCH_SYMBOLS,
        "expected_shards": expected_shards,
        "coverage_summary": coverage_summary(cov),
        "coverage_map": [
            {"symbol": r["symbol"], "min_date": r["min_date"],
             "max_date": r["max_date"], "n_dates": r["n_dates"]}
            for r in cov.iter_rows(named=True)
        ],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return plan, manifest


def _print_accounting(m: dict) -> None:
    print(f"pairs: pre_coverage={m['pairs_pre_coverage']} "
          f"(insample={m['pairs_insample_source']} forward={m['pairs_forward_source']} "
          f"ortho={m['pairs_ortho_source']})", flush=True)
    print(f"forward tier detail: scores_only={m['pairs_forward_scores_only']} "
          f"ext_ledger={m['pairs_ext_ledger_source']} "
          f"ext_not_in_forward_scores={m['pairs_ext_not_in_forward_scores']}", flush=True)
    print(f"dropped: symbol_no_options={m['pairs_dropped_symbol_no_options']} "
          f"outside_coverage={m['pairs_dropped_outside_coverage']}", flush=True)
    print(f"COVERED pairs={m['pairs_covered']} "
          f"(insample={m['pairs_covered_insample']} forward={m['pairs_covered_forward']}) "
          f"tiers a/b/c={m['tier_a_ortho']}/{m['tier_b_forward']}/{m['tier_c_insample_rest']}",
          flush=True)
    est = m["estimate_seconds_at_0628_per_pair"]
    print(f"revised estimate @ {SEC_PER_PAIR} s/pair: {est}s = {est/60:.0f}min "
          f"= {est/3600:.2f}h", flush=True)
    cs = m["coverage_summary"]
    print(f"coverage spans: >=365d:{cs['span_ge_365d']} 180-365d:{cs['span_180_365d']} "
          f"90-180d:{cs['span_90_180d']} <90d:{cs['span_lt_90d']} "
          f"zero_rows:{cs['n_symbols_zero_option_rows']} probe_failed:{cs['n_probe_failed']}",
          flush=True)


# ---------------------------------------------------------------- chain pulls

def _fetch_symbol_chain(symbol: str, dates: list) -> list:
    placeholders = ",".join(["%s"] * len(dates))
    sql = _SQL.format(placeholders=placeholders)
    cur = DB.execute_sql(sql, tuple([symbol, *dates, DTE_LO, DTE_HI]))
    return cur.fetchall()


def _process_symbol(sym: str, dates: list):
    """Fetch + filter one symbol's chains. Returns (recs, drops, chain_dates)."""
    drops = {"dte_out_of_range": 0, "open_interest_not_positive": 0, "iv_out_of_range": 0}
    recs = []
    chain_dates = set()
    for sig_date, spot, otype, strike, exp, dte, oi, iv in _fetch_symbol_chain(sym, dates):
        if dte is None or not (DTE_LO <= int(dte) <= DTE_HI):
            drops["dte_out_of_range"] += 1
            continue
        if oi is None or int(oi) <= 0:
            drops["open_interest_not_positive"] += 1
            continue
        # open interval per DESIGN.md D3 "iv in (0.01, 5.0)" -- boundary values
        # (exact 0.0100 / 5.0000 in the DECIMAL(5,4) column) are excluded;
        # MATH_SPEC.md 4.x assumes the open interval and clips defensively.
        if iv is None or not (IV_LO < float(iv) < IV_HI):
            drops["iv_out_of_range"] += 1
            continue
        if spot is None or float(spot) <= 0:
            continue
        sd = sig_date.isoformat() if hasattr(sig_date, "isoformat") else str(sig_date)[:10]
        recs.append({
            "symbol": sym, "date": sd, "spot": float(spot),
            "expiration": exp.isoformat() if hasattr(exp, "isoformat") else str(exp)[:10],
            "dte": int(dte), "strike": float(strike), "option_type": otype,
            "open_interest": int(oi), "iv": float(iv),
        })
        chain_dates.add(sd)
    return recs, drops, chain_dates


def _recs_to_frame(recs: list) -> pl.DataFrame:
    if not recs:
        return pl.DataFrame(schema=_FINAL_SCHEMA)
    return (pl.DataFrame(recs, infer_schema_length=None)
            .with_columns([
                pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"),
                pl.col("expiration").str.strptime(pl.Date, "%Y-%m-%d"),
                pl.col("spot").cast(pl.Float64),
                pl.col("dte").cast(pl.Int32),
                pl.col("strike").cast(pl.Float64),
                pl.col("open_interest").cast(pl.Int64),
                pl.col("iv").cast(pl.Float64),
            ])
            .select(list(_FINAL_SCHEMA)))


def _atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    df.write_parquet(tmp, compression="snappy")
    os.replace(tmp, path)


def _atomic_write_json(obj: dict, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="ascii", errors="replace") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- full build

def build_full(limit_symbols, refresh_coverage: bool) -> int:
    _set_session_timeout(CHAIN_TIMEOUT_MS)
    SHARDS_DIR.mkdir(parents=True, exist_ok=True)

    if PLAN_PATH.exists() and MANIFEST_PATH.exists() and not refresh_coverage:
        plan = pl.read_parquet(PLAN_PATH)
        with open(MANIFEST_PATH, encoding="ascii") as f:
            manifest = json.load(f)
        print(f"resuming persisted plan ({plan.height} pairs)", flush=True)
    else:
        plan, manifest = plan_pairs(refresh_coverage=refresh_coverage)
        _atomic_write_parquet(plan, PLAN_PATH)
        _atomic_write_json(manifest, MANIFEST_PATH)
        print(f"plan persisted ({plan.height} pairs)", flush=True)
    _print_accounting(manifest)

    dates_by = {}
    for tier, sym, d in plan.iter_rows():
        dates_by.setdefault((tier, sym), []).append(d)

    t0 = time.time()
    fetched_symbols = 0
    for tier in ("a", "b", "c"):
        syms = sorted({s for (t, s) in dates_by if t == tier})
        batches = [syms[i:i + BATCH_SYMBOLS] for i in range(0, len(syms), BATCH_SYMBOLS)]
        for bi, batch in enumerate(batches):
            shard = SHARDS_DIR / f"shard_{tier}_{bi:04d}.parquet"
            meta_p = SHARDS_DIR / f"shard_{tier}_{bi:04d}.meta.json"
            if shard.exists() and meta_p.exists():
                print(f"tier {tier} batch {bi+1}/{len(batches)}: shard exists, skip", flush=True)
                continue
            bt0 = time.time()
            recs = []
            drops = {"dte_out_of_range": 0, "open_interest_not_positive": 0,
                     "iv_out_of_range": 0}
            with_chain = 0
            n_pairs = 0
            for si, sym in enumerate(batch):
                dates = sorted(dates_by[(tier, sym)])
                n_pairs += len(dates)
                r, dr, cd = _process_symbol(sym, dates)
                recs.extend(r)
                for k in drops:
                    drops[k] += dr[k]
                with_chain += len(cd)
                print(f"tier {tier} batch {bi+1}/{len(batches)} "
                      f"symbol {si+1}/{len(batch)} {sym} dates={len(dates)} "
                      f"rows={len(recs)} ({time.time()-t0:.0f}s)", flush=True)
            df = _recs_to_frame(recs)
            _atomic_write_parquet(df, shard)
            _atomic_write_json({
                "tier": tier, "batch_index": bi, "symbols": batch,
                "pairs": n_pairs, "signals_with_chain": with_chain,
                "rows": df.height, "filter_drop_counts": drops,
                "elapsed_seconds": round(time.time() - bt0, 1),
            }, meta_p)
            fetched_symbols += len(batch)
            if limit_symbols is not None and fetched_symbols >= limit_symbols:
                print(f"limit-symbols={limit_symbols} reached after "
                      f"{fetched_symbols} fetched symbols -- stopping (restartable)",
                      flush=True)
                return 0

    missing = [s for s in manifest["expected_shards"] if not (SHARDS_DIR / s).exists()]
    if missing:
        print(f"build pass done but {len(missing)} shards missing -- "
              f"rerun to resume, then --finalize", flush=True)
        return 1
    print("all shards present -- finalizing", flush=True)
    return finalize()


# ---------------------------------------------------------------- finalize

def finalize() -> int:
    if not MANIFEST_PATH.exists():
        print("ERROR: no manifest -- run a build first", flush=True)
        return 1
    with open(MANIFEST_PATH, encoding="ascii") as f:
        manifest = json.load(f)
    expected = manifest["expected_shards"]
    present, missing = [], []
    for name in expected:
        shard = SHARDS_DIR / name
        meta = SHARDS_DIR / name.replace(".parquet", ".meta.json")
        (present if (shard.exists() and meta.exists()) else missing).append(name)

    frames, metas = [], []
    for name in present:
        frames.append(pl.read_parquet(SHARDS_DIR / name))
        with open(SHARDS_DIR / name.replace(".parquet", ".meta.json"), encoding="ascii") as f:
            metas.append(json.load(f))

    df = pl.concat(frames) if frames else pl.DataFrame(schema=_FINAL_SCHEMA)
    if df.height:
        df = df.sort(["symbol", "date"])

    drops = {"dte_out_of_range": 0, "open_interest_not_positive": 0, "iv_out_of_range": 0}
    with_chain = 0
    for m in metas:
        for k in drops:
            drops[k] += m["filter_drop_counts"][k]
        with_chain += m["signals_with_chain"]
    total_checked = df.height + sum(drops.values())

    counts = (df.group_by("symbol").len().sort("len", descending=True)
              if df.height else None)
    report = {
        "signals_requested": manifest["pairs_covered"],
        "signals_with_chain": with_chain,
        "total_rows": df.height,
        "build_complete": not missing,
        "n_shards_present": len(present),
        "n_shards_missing": len(missing),
        "missing_shards": missing[:40],
        "filter_drop_counts": drops,
        "filter_drop_rates": {
            k: (round(v / total_checked, 4) if total_checked else None)
            for k, v in drops.items()
        },
        "per_symbol_row_counts_top20": (
            [{"symbol": r[0], "rows": int(r[1])} for r in counts.head(20).iter_rows()]
            if counts is not None else []),
        "n_symbols_in_output": counts.height if counts is not None else 0,
        "date_min": df["date"].min().isoformat() if df.height else None,
        "date_max": df["date"].max().isoformat() if df.height else None,
        "elapsed_seconds_sum_of_shards": round(sum(m["elapsed_seconds"] for m in metas), 1),
        "pair_accounting": {k: manifest[k] for k in (
            "pairs_pre_coverage", "pairs_insample_source", "pairs_forward_source",
            "pairs_forward_scores_only", "pairs_ext_ledger_source",
            "pairs_ext_not_in_forward_scores",
            "pairs_ortho_source", "pairs_dropped_symbol_no_options",
            "pairs_dropped_outside_coverage", "pairs_covered",
            "pairs_covered_insample", "pairs_covered_forward",
            "tier_a_ortho", "tier_b_forward", "tier_c_insample_rest")},
        "coverage_summary": manifest["coverage_summary"],
        "coverage_map": manifest["coverage_map"],
        "mode": "full",
    }
    _atomic_write_parquet(df, OUT_DIR / "gex_chain.parquet")
    _atomic_write_json(report, OUT_DIR / "gex_build_report.json")
    print(f"wrote {OUT_DIR / 'gex_chain.parquet'} "
          f"({(OUT_DIR / 'gex_chain.parquet').stat().st_size/1e6:.1f} MB, "
          f"{df.height:,} rows)", flush=True)
    print(f"wrote {OUT_DIR / 'gex_build_report.json'} "
          f"(complete={report['build_complete']} missing_shards={len(missing)})", flush=True)
    return 0 if not missing else 1


# ---------------------------------------------------------------- smoke

def run_smoke() -> int:
    """Coverage-aware smoke: first 2 symbols (alphabetical) with >= 5 signal
    dates INSIDE their option coverage window; first 5 covered dates each.
    Direct write to *_smoke outputs; no shards, no plan."""
    _set_session_timeout(CHAIN_TIMEOUT_MS)
    t0 = time.time()
    pairs_by = {}
    for s, d in (_load_insample_pairs() | _load_forward_pairs()):
        pairs_by.setdefault(s, set()).add(d)
    universe = _options_symbol_universe()
    candidates = sorted(s for s in pairs_by if s in universe)

    cov_lookup = {}
    if COVERAGE_CACHE.exists():
        cov = pl.read_parquet(COVERAGE_CACHE)
        cov_lookup = {r["symbol"]: (r["min_date"], r["max_date"])
                      for r in cov.iter_rows(named=True) if r["min_date"]}
        print(f"smoke: using cached coverage map ({len(cov_lookup)} symbols)", flush=True)

    chosen = []
    probes = 0
    for s in candidates:
        if s in cov_lookup:
            lo, hi = cov_lookup[s]
        else:
            probes += 1
            r = _probe_symbol_coverage(s)
            if r is None:
                continue
            lo, hi = r[0], r[1]
        covered_dates = sorted(d for d in pairs_by[s] if lo <= d <= hi)
        if len(covered_dates) >= 5:
            chosen.append((s, covered_dates[:5]))
        if len(chosen) == 2:
            break
    print(f"smoke: chose {[s for s, _ in chosen]} (probes={probes})", flush=True)

    recs = []
    drops = {"dte_out_of_range": 0, "open_interest_not_positive": 0, "iv_out_of_range": 0}
    with_chain = 0
    for i, (sym, dates) in enumerate(chosen):
        r, dr, cd = _process_symbol(sym, dates)
        recs.extend(r)
        for k in drops:
            drops[k] += dr[k]
        with_chain += len(cd)
        print(f"symbol {i+1}/{len(chosen)} {sym} dates={len(dates)} "
              f"rows={len(recs)} ({time.time()-t0:.0f}s)", flush=True)

    df = _recs_to_frame(recs)
    n_pairs = sum(len(d) for _, d in chosen)
    total_checked = df.height + sum(drops.values())
    report = {
        "signals_requested": n_pairs,
        "signals_with_chain": with_chain,
        "total_rows": df.height,
        "smoke_symbols": [{"symbol": s, "dates": d} for s, d in chosen],
        "filter_drop_counts": drops,
        "filter_drop_rates": {
            k: (round(v / total_checked, 4) if total_checked else None)
            for k, v in drops.items()
        },
        "per_symbol_row_counts_top20": (
            [{"symbol": r[0], "rows": int(r[1])}
             for r in df.group_by("symbol").len().sort("len", descending=True)
             .head(20).iter_rows()]
            if df.height else []),
        "n_symbols_in_output": df["symbol"].n_unique() if df.height else 0,
        "date_min": df["date"].min().isoformat() if df.height else None,
        "date_max": df["date"].max().isoformat() if df.height else None,
        "elapsed_seconds": round(time.time() - t0, 1),
        "mode": "smoke",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_parquet(df, OUT_DIR / "gex_chain_smoke.parquet")
    _atomic_write_json(report, OUT_DIR / "gex_build_report_smoke.json")
    print(f"wrote gex_chain_smoke.parquet ({df.height:,} rows) + "
          f"gex_build_report_smoke.json in {report['elapsed_seconds']}s", flush=True)
    return 0


# ---------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="coverage-aware smoke: first 2 alphabetical symbols with "
                         ">=5 covered signal dates, 5 dates each -> *_smoke outputs")
    ap.add_argument("--limit-symbols", type=int, default=None,
                    help="fetch at most ~N symbols of NEW shards this run "
                         "(whole batches; already-present shards not counted), then stop")
    ap.add_argument("--coverage-only", action="store_true",
                    help="build/refresh coverage map, print pair accounting + "
                         "revised estimate, exit (no chain pulls)")
    ap.add_argument("--refresh-coverage", action="store_true",
                    help="force coverage-map rebuild (also re-plans shards)")
    ap.add_argument("--finalize", action="store_true",
                    help="concatenate existing shards -> gex_chain.parquet + report (no DB)")
    a = ap.parse_args()

    if a.finalize:
        return finalize()
    if a.coverage_only:
        _, manifest = plan_pairs(refresh_coverage=a.refresh_coverage)
        _print_accounting(manifest)
        return 0
    if a.smoke:
        return run_smoke()
    return build_full(limit_symbols=a.limit_symbols, refresh_coverage=a.refresh_coverage)


if __name__ == "__main__":
    raise SystemExit(main())
