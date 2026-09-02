"""Real-Priced Ledger Replay -- LEDGER BUILD.

Builds the persisted per-trade ledger described in
experiments/real_priced_replay/DESIGN.md section 1 (construction) and
section 5 (build instructions). One row per MATCHED (symbol, signal_date)
v74 CALL signal (overall>=75) in [2025-02-10, run_date-30cd] -- NO in-sample
cutoff restriction (DESIGN section 0 "Holdout note": this probe tests PRICING
fidelity conditional on a fixed signal set, not signal quality; post-cutoff
signals' realized performance is already visible via the live forward
ledger, so nothing new leaks).

Contract selection (nearest-ATM CALL, 20-45 DTE band, iv/price/oi filters,
liquidity classification incl. the liquid-primary definition) is REUSED
VERBATIM from experiments/iv_engine_pertrade/build_ledger.py, per DESIGN
section 1: "No new selection logic -- the selection layer was already
validated there." Functions below are COPIED (not imported) with attribution
-- importing that module directly would set IV_PREMIUM=1/IV_MODEL=1
process-wide at import time (its own F2-premium-engine validation harness)
and would perform an eager DB call for VERSION_ID; both are wrong here: this
probe's MODEL-CG arm wants the plain SHIPPED RV premium (IV_PREMIUM/IV_MODEL
OFF, the production default -- the F2/gamma pair is parked A/B-only per
experiments/iv_engine_pertrade/VERDICT.md). Only the SQL/selection shape is
unchanged; the one deliberate difference is the forward-path window, widened
from the source's fixed 24-calendar-day cap to DESIGN's own
min(expiry, entry+30cd) (section 1), via an explicit end-DATE query param
instead of a day-count.

Additionally persists the underlying's own forward daily OHLC
(model_dates/model_{open,high,low,close}) over the SAME entry..min(expiry,
entry+30cd) window, sourced from price_history. DESIGN section 1 only lists
"entry S" (singular) as a persisted field, but the twin close-grain replay
(replay.py) cannot compute MODEL-CG's day-by-day P&L -- or the intraday-grain
reference column -- without the underlying's own forward series; persisting
it here is what makes "replay walk is seconds" (DESIGN section 5) true: a
fully offline replay against the parquet, no live DB pull inside replay.py.
Disclosed as OPEN QUESTIONS #1 in DESIGN.md's RESULTS section.

Queued (db=heavy, ~1 query pair per signal against a ~90M-row option_prices
table, same access pattern as the iv_engine_pertrade precedent that produced
queue #597 / N=2,551 matched). Do not run the full population directly --
submit via `trader queue submit` per DESIGN.md section 5. LEDGER_SMOKE=<N>
env var limits to the first N chainable signals for a fast local correctness
check (mirrors build_ledger.py's own LEDGER_SMOKE convention).

Output:
  .cache/real_priced_replay/replay_ledger.parquet       (the per-trade ledger)
  .cache/real_priced_replay/replay_ledger_meta.json     (coverage summary)

Every invocation appends one line to experiments/real_priced_replay/results/
attempt_log.md (timestamp, git HEAD, exact command, outcome) -- same rail as
the gamma-curve-calibration prereg (append-only, never overwritten).

ASCII-only stdout. PYTHONUTF8=1/PYTHONIOENCODING=utf-8 set here defensively
(also passed via --env on the queue submission).
"""
from __future__ import annotations

import os
import sys
import time
import json
import bisect
from collections import Counter
from datetime import date, timedelta

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)          # pin repo root FIRST -- worktree PYTHONPATH trap guard

import polars as pl                     # noqa: E402

from database.trader_database import DB               # noqa: E402
from database.models.core import AlgorithmVersion      # noqa: E402
import monte_carlo as mc                                # noqa: E402
# NOTE: `import monte_carlo` performs NO eager DB calls at module scope (only
# constant/function definitions; DB access happens lazily inside functions we
# never call here except mc.realized_vol/mc.score_to_tier/mc._load_vix_series,
# all pure or lazily-cached). Safe for `python -c import` compile-checking.

# ---------------------------------------------------------------------------
# DESIGN.md-pinned constants (section 0/1) -- literal, not dynamically
# resolved, because DESIGN explicitly pins v74 regardless of whatever is
# "active" when this eventually runs ("no in-sample cutoff restriction" is
# specifically about auditing the CURRENTLY SHIPPED v74 algorithm).
# ---------------------------------------------------------------------------
VERSION_ID = 74
SIGNAL_START = "2025-02-10"
CALL_MIN_OVERALL = 75
RUN_DATE_LAG_DAYS = 30                    # signal window end = run_date - 30cd

RV_LOOKBACK_BUFFER_DAYS = 130              # calendar-day buffer before earliest signal (>=60 trading bars)
FWD_CAL_DAYS = 30                          # DESIGN section 1: entry..min(expiry, entry+30cd)
FWD_QUERY_BUFFER_DAYS = 34                 # small safety margin past FWD_CAL_DAYS (weekends/holidays)

SELECTION_RULE = "nearest_moneyness_call_dte20_45_iv.05-5_price>0_oi>0_v1"   # unchanged from source
QUOTE_BASIS = "yfinance_lastPrice_postclose_pull"                            # unchanged from source

OUT_DIR = os.path.join(_ROOT, ".cache", "real_priced_replay")
OUT_PATH = os.path.join(OUT_DIR, "replay_ledger.parquet")
META_PATH = os.path.join(OUT_DIR, "replay_ledger_meta.json")

RESULTS_DIR = os.path.join(_HERE, "results")
ATTEMPT_LOG = os.path.join(RESULTS_DIR, "attempt_log.md")

LEDGER_SMOKE = int(os.environ.get("LEDGER_SMOKE", "0"))


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Attempt log (append-only; every invocation, including aborts/failures)
# ---------------------------------------------------------------------------
def _git_head():
    try:
        import subprocess
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_ROOT,
                              capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()[:12]
    except Exception:
        pass
    return "unknown"


def _append_attempt_log(outcome):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    head = _git_head()
    cmd = " ".join(sys.argv)
    line = f"- {ts} | HEAD={head} | cmd: {cmd} | outcome: {outcome}\n"
    try:
        with open(ATTEMPT_LOG, "a", encoding="ascii", errors="backslashreplace") as f:
            f.write(line)
    except Exception as e:
        log(f"WARNING: failed to append attempt log: {e!r}")


# ---------------------------------------------------------------------------
# Contract selection -- REUSED VERBATIM from experiments/iv_engine_pertrade/
# build_ledger.py (see module docstring for why this is copied, not imported).
# ---------------------------------------------------------------------------
_SELECT_SQL = (
    "SELECT o.id, o.strike_price, o.expiration_date, "
    "DATEDIFF(o.expiration_date, op.date) AS dte, "
    "op.price, op.iv, op.open_interest, op.volume "
    "FROM options o JOIN option_prices op ON op.option_id = o.id "
    "WHERE o.symbol=%s AND o.option_type='call' AND op.date=%s "
    "AND DATEDIFF(o.expiration_date, op.date) BETWEEN 20 AND 45 "
    "AND op.iv BETWEEN 0.05 AND 5.0 AND op.price>0 AND op.open_interest>0"
)


def select_contract(symbol, date_str, entry_close):
    """Verbatim from build_ledger.py's select_contract() -- nearest-ATM CALL,
    deterministic tie-break by lowest option_id."""
    rows = DB.execute_sql(_SELECT_SQL, (symbol, date_str)).fetchall()
    if not rows:
        return None
    best = None
    best_dist = None
    for oid, strike, exp, dte, price, iv, oi, vol in rows:
        strike_f = float(strike)
        dist = abs(strike_f / entry_close - 1.0)
        if best is None or dist < best_dist or (dist == best_dist and oid < best[0]):
            best = (int(oid), strike_f, exp, int(dte), float(price), float(iv), int(oi or 0), int(vol or 0))
            best_dist = dist
    return best


# DESIGN.md section 1 difference from the source: forward window is
# min(expiry, entry+30cd), not the source's fixed 24-calendar-day cap -- the
# query takes an explicit end DATE (computed in Python from the selected
# contract's own expiration_date) instead of a day-count. Selection SHAPE
# (option_id, date>entry, date<=window_end, ORDER BY date, price>0 filter)
# is otherwise identical to build_ledger.py's fetch_forward_path().
_FWD_SQL = (
    "SELECT date, price, iv, volume FROM option_prices "
    "WHERE option_id=%s AND date > %s AND date <= %s ORDER BY date"
)


def fetch_forward_path(option_id, date_str, window_end_str):
    rows = DB.execute_sql(_FWD_SQL, (option_id, date_str, window_end_str)).fetchall()
    dates_valid, prices_valid, ivs_valid, volumes_valid = [], [], [], []
    for d, p, iv, vol in rows:
        if p is None or float(p) <= 0:
            continue
        dates_valid.append(d if isinstance(d, date) else date.fromisoformat(str(d)[:10]))
        prices_valid.append(float(p))
        ivs_valid.append(float(iv) if iv is not None else None)
        volumes_valid.append(int(vol) if vol is not None else 0)
    return dates_valid, prices_valid, ivs_valid, volumes_valid


# ---------------------------------------------------------------------------
# Bulk price_history OHLC pull (chunked) -- adapted from build_ledger.py's
# fetch_bulk_price_history (close-only there; OHLC needed here for MODEL-CG's
# close-grain walk AND the intraday-grain reference column, both in replay.py).
# ---------------------------------------------------------------------------
def fetch_bulk_price_history_ohlc(symbols, start, end, chunk_size=150):
    """Chunked bulk price_history pull, batched over chunk_size symbols per
    query (avoids one giant IN-list against a large table). Returns
    {symbol: (dates[], opens[], highs[], lows[], closes[])}, all parallel
    lists sorted by date."""
    if not symbols:
        return {}, 0.0
    t0 = time.time()
    out = {}
    symbols_sorted = sorted(symbols)
    for i in range(0, len(symbols_sorted), chunk_size):
        batch = symbols_sorted[i:i + chunk_size]
        placeholders = ",".join(["%s"] * len(batch))
        sql = (f"SELECT symbol, date, open, high, low, close FROM price_history "
               f"WHERE symbol IN ({placeholders}) AND date BETWEEN %s AND %s ORDER BY symbol, date")
        rows = DB.execute_sql(sql, list(batch) + [start, end]).fetchall()
        for sym, d, o, h, l, c in rows:
            if c is None:
                continue
            rec = out.setdefault(sym, ([], [], [], [], []))
            dd_, oo_, hh_, ll_, cc_ = rec
            cc = float(c)
            dd_.append(d if isinstance(d, date) else date.fromisoformat(str(d)[:10]))
            oo_.append(float(o) if o is not None else cc)
            hh_.append(float(h) if h is not None else cc)
            ll_.append(float(l) if l is not None else cc)
            cc_.append(cc)
    return out, time.time() - t0


def _idx_at(dates_arr, d):
    """Exact-date bisect lookup into a sorted dates array. Returns index or None."""
    pos = bisect.bisect_left(dates_arr, d)
    if pos < len(dates_arr) and dates_arr[pos] == d:
        return pos
    return None


# ---------------------------------------------------------------------------
# Candidate signal population (DESIGN section 1: v74, overall>=75, CALL side
# -- HIGH score IS the call criterion in this project's convention, see
# CLAUDE.md "CRITICAL -- scoring direction" -- no separate side filter needed)
# ---------------------------------------------------------------------------
def load_candidate_signals(run_date):
    window_end_date = run_date - timedelta(days=RUN_DATE_LAG_DAYS)
    window_end = window_end_date.isoformat()
    rows = DB.execute_sql(
        "SELECT symbol, date, overall FROM scores "
        "WHERE version_id=%s AND date BETWEEN %s AND %s AND overall>=%s",
        (VERSION_ID, SIGNAL_START, window_end, CALL_MIN_OVERALL),
    ).fetchall()
    out = []
    for sym, d, ov in rows:
        ds = d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]
        out.append({"symbol": sym, "date": ds, "overall": int(ov)})
    out.sort(key=lambda r: (r["date"], r["symbol"]))
    return out, window_end


# ---------------------------------------------------------------------------
# Per-signal processing: entry S/rv/vix -> contract selection -> real forward
# path -> underlying forward OHLC.
# ---------------------------------------------------------------------------
def process_signal(sig, ph_by_symbol, vix_map):
    symbol, date_str, overall = sig["symbol"], sig["date"], sig["overall"]
    date_obj = date.fromisoformat(date_str)

    rec_ph = ph_by_symbol.get(symbol)
    if not rec_ph:
        return None, "no_price_history_symbol"
    dates_arr, opens_arr, highs_arr, lows_arr, closes_arr = rec_ph
    idx = _idx_at(dates_arr, date_obj)
    if idx is None:
        return None, "no_price_history_close"
    entry_close = closes_arr[idx]
    if entry_close is None or entry_close <= 0:
        return None, "no_price_history_close"

    rv = mc.realized_vol(closes_arr, idx)
    if rv is None or rv <= 0:
        return None, "insufficient_rv_history"

    contract = select_contract(symbol, date_str, entry_close)
    if contract is None:
        return None, "no_qualifying_contract"
    (option_id, strike, expiration_date, dte_at_entry,
     entry_premium, entry_iv, entry_oi, entry_volume) = contract

    vix_entry = vix_map.get(date_obj)
    model_premium_pct = mc.PREMIUM_MULT * rv / 100.0   # 1.82 x sigma_daily (rv is percent units)

    cal30_end = date_obj + timedelta(days=FWD_CAL_DAYS)
    window_end = min(expiration_date, cal30_end)
    real_dates, real_prices, _real_ivs, real_volumes = fetch_forward_path(
        option_id, date_str, window_end.isoformat())

    # Underlying forward OHLC over the SAME entry..min(expiry,+30cd) window
    # (see module docstring / DESIGN.md OPEN QUESTIONS #1).
    m_end_idx = idx
    while m_end_idx + 1 < len(dates_arr) and dates_arr[m_end_idx + 1] <= window_end:
        m_end_idx += 1
    model_dates = dates_arr[idx + 1: m_end_idx + 1]
    model_opens = opens_arr[idx + 1: m_end_idx + 1]
    model_highs = highs_arr[idx + 1: m_end_idx + 1]
    model_lows = lows_arr[idx + 1: m_end_idx + 1]
    model_closes = closes_arr[idx + 1: m_end_idx + 1]

    tier = mc.score_to_tier(overall)

    rec = {
        "symbol": symbol, "date": date_str, "overall": int(overall), "tier": tier,
        "entry_close": float(entry_close), "rv": float(rv),
        "vix_entry": (float(vix_entry) if vix_entry is not None else None),
        "option_id": option_id, "strike": strike,
        "expiration_date": expiration_date.isoformat(), "dte_at_entry": dte_at_entry,
        "entry_premium": entry_premium, "entry_iv": entry_iv,
        "entry_volume": entry_volume, "entry_open_interest": entry_oi,
        "liquid_ge5": entry_volume >= 5, "liquid_gt0": entry_volume > 0,
        "real_premium_pct": entry_premium / entry_close,
        "model_premium_pct": model_premium_pct,
        "forward_window_end": window_end.isoformat(),
        "fwd_rows_available": len(real_dates),
        "real_dates": [d.isoformat() for d in real_dates],
        "real_prices": real_prices,
        "real_volumes": real_volumes,
        "model_dates": [d.isoformat() for d in model_dates],
        "model_opens": model_opens, "model_highs": model_highs,
        "model_lows": model_lows, "model_closes": model_closes,
        "selection_rule": SELECTION_RULE, "quote_basis": QUOTE_BASIS,
    }
    return rec, None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    t_start = time.time()
    log("=== real_priced_replay LEDGER BUILD ===")
    run_date = date.today()
    log(f"run_date = {run_date.isoformat()}")
    log(f"VERSION_ID (DESIGN.md-pinned) = {VERSION_ID}")
    try:
        active_id = AlgorithmVersion.get_active_scores_version().id
        if active_id != VERSION_ID:
            log(f"NOTE: active_scores_version={active_id} != pinned VERSION_ID={VERSION_ID} "
                f"-- DESIGN.md section 1 pins v74 explicitly; querying version_id={VERSION_ID} regardless")
        else:
            log(f"active_scores_version={active_id} matches pinned VERSION_ID (sanity check OK)")
    except Exception as e:
        log(f"NOTE: could not read active_scores_version ({e!r}); continuing with pinned VERSION_ID={VERSION_ID}")

    if LEDGER_SMOKE:
        log(f"*** LEDGER_SMOKE={LEDGER_SMOKE} -- limiting to first {LEDGER_SMOKE} chainable signals ***")

    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=120000")

    log("\n--- 1. candidate signal population ---")
    candidates, window_end = load_candidate_signals(run_date)
    log(f"signal window: [{SIGNAL_START}, {window_end}]  (run_date - {RUN_DATE_LAG_DAYS}cd, NO in-sample cutoff)")
    log(f"candidate signals: N={len(candidates)}")

    if not candidates:
        log("ERROR: zero candidate signals -- aborting")
        _append_attempt_log("ABORTED: zero candidate signals")
        sys.exit(1)

    log("\n--- 2. chain-bearing symbol prefilter ---")
    chain_syms = set(r[0] for r in DB.execute_sql("SELECT DISTINCT symbol FROM options").fetchall())
    cand_syms = set(c["symbol"] for c in candidates)
    bulk_syms = sorted(chain_syms & cand_syms)
    chainable = [c for c in candidates if c["symbol"] in bulk_syms]
    log(f"chain-bearing symbols (options table): {len(chain_syms)}")
    log(f"candidate distinct symbols: {len(cand_syms)}; chain-bearing intersection: {len(bulk_syms)}")
    log(f"chainable signals (will be queried): {len(chainable)} of {len(candidates)}")

    if LEDGER_SMOKE:
        chainable = chainable[:LEDGER_SMOKE]
        log(f"SMOKE: reduced to {len(chainable)} signals")

    log("\n--- 3. bulk price_history OHLC pull (chunked) ---")
    all_dates = [c["date"] for c in chainable] or [c["date"] for c in candidates]
    min_d, max_d = min(all_dates), max(all_dates)
    ph_start = (date.fromisoformat(min_d) - timedelta(days=RV_LOOKBACK_BUFFER_DAYS)).isoformat()
    ph_end = (date.fromisoformat(max_d) + timedelta(days=FWD_QUERY_BUFFER_DAYS)).isoformat()
    ph_by_symbol, ph_elapsed = fetch_bulk_price_history_ohlc(bulk_syms, ph_start, ph_end)
    n_ph_rows = sum(len(v[0]) for v in ph_by_symbol.values())
    log(f"bulk price_history: {n_ph_rows:,} rows / {len(ph_by_symbol)} symbols [{ph_start}..{ph_end}] in {ph_elapsed:.1f}s")

    log("\n--- 4. VIX series (lazy, shared with mc._iv_premium_pct's cache) ---")
    vix_map = mc._load_vix_series()
    log(f"vix series loaded: N={len(vix_map)}")
    vix_span_ok = bool(vix_map) and (min(vix_map) <= date.fromisoformat(SIGNAL_START)) \
        and (max(vix_map) >= date.fromisoformat(window_end))
    log(f"vix coverage spans signal window [{SIGNAL_START}..{window_end}]: {vix_span_ok}")
    if not vix_span_ok:
        log("WARNING: VIX series does not fully cover the signal window -- replay.py should mark "
            "VIX-band breakdowns SKIPPED-NO-DATA rather than reporting a degenerate table "
            "(per the implementation brief's explicit instruction: do not fetch new VIX data).")

    log("\n--- 5. per-signal selection + forward-path loop ---")
    records = []
    miss_counts = Counter()
    t_loop = time.time()
    n_total = len(chainable)
    for i, sig in enumerate(chainable):
        try:
            rec, miss_reason = process_signal(sig, ph_by_symbol, vix_map)
        except Exception as e:
            rec, miss_reason = None, f"exception:{repr(e)[:120]}"
        if rec is None:
            miss_counts[miss_reason] += 1
        else:
            records.append(rec)
        if (i + 1) % 250 == 0 or (i + 1) == n_total:
            el = time.time() - t_loop
            rate = (i + 1) / el if el > 0 else 0.0
            eta = ((n_total - (i + 1)) / rate) if rate > 0 else 0.0
            log(f"  {i + 1}/{n_total} matched={len(records)} miss={sum(miss_counts.values())} "
                f"({el:.0f}s, {rate:.1f}/s, eta={eta:.0f}s)")

    loop_elapsed = time.time() - t_loop
    log(f"\nper-signal loop done: matched={len(records)} miss={sum(miss_counts.values())} in {loop_elapsed:.0f}s")
    log(f"miss reasons: {dict(miss_counts)}")

    if not records:
        log("ERROR: zero matched records -- aborting write")
        _append_attempt_log("ABORTED: zero matched records")
        sys.exit(1)

    df = pl.DataFrame(records, infer_schema_length=None)
    os.makedirs(OUT_DIR, exist_ok=True)
    df.write_parquet(OUT_PATH)
    log(f"\nwrote {OUT_PATH}  N={df.height}  cols={len(df.columns)}")

    n_matched = len(records)
    n_liquid = sum(1 for r in records if r["liquid_ge5"])
    match_rate = (n_matched / len(candidates)) if candidates else None

    log("\n--- coverage: match rate by month ---")
    cand_by_month = Counter(c["date"][:7] for c in candidates)
    matched_by_month = Counter(r["date"][:7] for r in records)
    month_table = []
    for month in sorted(cand_by_month):
        nc = cand_by_month[month]
        nm = matched_by_month.get(month, 0)
        rate = (nm / nc) if nc else 0.0
        month_table.append({"month": month, "candidates": nc, "matched": nm, "match_rate": round(rate, 4)})
        log(f"  {month}: candidates={nc:4d} matched={nm:4d} rate={rate:6.1%}")

    log("\n--- coverage summary ---")
    log(f"N candidate signals: {len(candidates)}")
    log(f"N matched (contract selected): {n_matched}")
    log(f"overall match rate: {match_rate:.1%}" if match_rate is not None else "overall match rate: n/a")
    log(f"N liquid-primary (liquid_ge5) matched: {n_liquid} "
        f"({(n_liquid / n_matched):.1%} of matched)" if n_matched else "")

    floor_ok = n_liquid >= 800
    log(f"\nN-floor check (DESIGN.md section 5, >=800 liquid-primary): "
        f"{'PASS' if floor_ok else 'BELOW FLOOR -- replay.py must report-only, no flags'} (N={n_liquid})")

    meta = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "run_date": run_date.isoformat(),
        "version_id": VERSION_ID,
        "signal_start": SIGNAL_START, "signal_window_end": window_end,
        "candidate_n": len(candidates), "chainable_n": len(chainable),
        "matched_n": n_matched, "liquid_primary_n": n_liquid,
        "match_rate": match_rate, "liquid_primary_floor_800_pass": floor_ok,
        "match_rate_by_month": month_table,
        "miss_reasons": {str(k): v for k, v in miss_counts.items()},
        "chain_bearing_symbols": len(chain_syms), "candidate_distinct_symbols": len(cand_syms),
        "chainable_symbols": len(bulk_syms),
        "bulk_price_history_rows": n_ph_rows, "bulk_price_history_elapsed_s": round(ph_elapsed, 2),
        "vix_series_n": len(vix_map), "vix_coverage_spans_signal_window": vix_span_ok,
        "loop_elapsed_s": round(loop_elapsed, 1),
        "total_elapsed_s": round(time.time() - t_start, 1),
        "smoke": LEDGER_SMOKE or None,
    }
    with open(META_PATH, "w", encoding="ascii", errors="backslashreplace") as f:
        json.dump(meta, f, indent=2, default=str)
    log(f"wrote {META_PATH}")

    log(f"\n=== DONE in {time.time() - t_start:.0f}s ===")
    outcome = (f"SUCCESS candidates={len(candidates)} matched={n_matched} liquid_primary={n_liquid} "
               f"match_rate={match_rate:.1%} floor_pass={floor_ok} elapsed={time.time() - t_start:.0f}s")
    _append_attempt_log(outcome)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _append_attempt_log(f"FAILED: {repr(e)[:300]}")
        raise
