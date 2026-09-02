"""Per-trade F2-gamma engine validation -- LEDGER BUILD.

Builds the persisted per-trade ledger described in
experiments/iv_engine_pertrade/DESIGN.md (APPROVED AS AMENDED 2026-07-12,
FABLE; see AMENDMENTS A1-A3). One row per MATCHED (symbol, signal_date) 70+
CALL candidate signal in [2025-02-10, CALIBRATION_CUTOFF_DATE], plus a
watch-only is_shadow_oos=True slice past the cutoff (never used for M1-M3;
descriptive appendix only, matching iv_ledger_ext.parquet's is_oos precedent).

Four pricing arms per matched real contract (imported functions, never
reimplemented -- DESIGN.md section 0 + section 8 "no engine edits"):
  rvconst  = RV premium  (monte_carlo.PREMIUM_MULT*rv/100) x const-delta option_pnl_pct
             [PRODUCTION / shipped baseline]
  rvgamma  = RV premium                                    x gamma-BS bs_option_pnl_pct
             [diagnostic -- isolates the P&L-model axis]
  f2const  = F2 IV-anchored premium (monte_carlo._iv_premium_pct, IV_PREMIUM=1
             IV_MODEL=1, dte=the matched contract's OWN dte)  x const-delta
             [diagnostic -- isolates the premium axis]
  f2gamma  = F2 premium                                    x gamma-BS
             [ADOPTION CANDIDATE]

Queued (db=heavy, ~7,800 indexed per-signal/per-contract queries against a
~90M-row option_prices table). Do not run the full population directly --
submit via `trader queue submit` per DESIGN.md section 6 / the ship
instructions. LEDGER_SMOKE=<N> env var limits to the first N chainable
signals for a fast local correctness check (mirrors build_iv.py's IV_SMOKE).

Output:
  .cache/iv_engine_pertrade/ledger_v1.parquet       (the per-trade ledger)
  .cache/iv_engine_pertrade/ledger_v1_meta.json     (coverage + telemetry)

Granularity + guard conventions (DESIGN.md section 3, AMENDMENTS A2):
  - "Trading bar t" is anchored to the OPTION's own forward option_prices
    quote sequence (price>0 filtered, date-ordered) -- same convention as
    build_iv.py's fwd_pnl(); the underlying's close on that SAME calendar
    date gives the paired real_underlying_dt. Both real and predicted sides
    use EOD close-to-close granularity only (no intraday H/L peeking), by
    deliberate design choice (section 3 "Granularity choice").
  - A2 freshness guard interpretation (documented choice, not fully literal
    from the one-sentence amendment): checkpoint VALUES (real_premium_dX /
    real_iv_dX / real_underlying_dX / real_pnl_dX) are populated whenever the
    bar exists (idx < fwd_rows_available), regardless of freshness -- the
    amendment's one explicit behavioral mandate is that first-touch TP/SL
    EVENTS only count on fresh-quote days. Freshness itself (dX_fresh bool +
    per-trade stale_frac) is persisted as its own diagnostic column set so
    the fraction of trades losing each checkpoint can be reported per A2.
  - ripeness (>=15 forward option_prices rows) gates the NEGATIVE "none"
    touch read only; a positive TP/SL touch is trusted whenever it occurs
    within the available (possibly partial) window -- generalizes build_iv.py's
    FIXED scalar pnl15 guard (445658866) to an event label, both guards apply.

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

# IV_PREMIUM/IV_MODEL are module-load-time constants in monte_carlo.py -- MUST
# be set before `import monte_carlo` (DESIGN.md section 0). This process only
# ever CALLS monte_carlo._iv_premium_pct() to compute the F2 premium for this
# read-only ledger; it never flips these flags in production, never edits
# monte_carlo.py, and this script performs no commits (DESIGN.md section 8).
os.environ["IV_PREMIUM"] = "1"
os.environ["IV_MODEL"] = "1"
# GAMMA_AWARE is deliberately NEVER set: option_pnl_pct/bs_option_pnl_pct are
# called directly for the const/gamma arms respectively (DESIGN.md section 0
# -- "no GAMMA_AWARE env flip needed... exactly as validate_gamma.py already
# does"). Setting GAMMA_AWARE=1 would make option_pnl_pct silently redirect to
# bs_option_pnl_pct, collapsing the const-delta arms into the gamma arms.

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)          # pin repo root FIRST -- worktree PYTHONPATH trap guard

import numpy as np                      # noqa: E402
import polars as pl                     # noqa: E402

from strategy_config import CALIBRATION_CUTOFF_DATE, STRATEGY_30DTE  # noqa: E402
from database.trader_database import DB                              # noqa: E402
from database.models.core import AlgorithmVersion                    # noqa: E402
from option_pricing import option_pnl_pct, bs_option_pnl_pct, DEFAULT_DELTA  # noqa: E402
import monte_carlo as mc                                             # noqa: E402

assert mc.IV_PREMIUM and mc.IV_MODEL, (
    "monte_carlo loaded without IV_PREMIUM=1/IV_MODEL=1 -- the F2 arm would be inert "
    "(module-load-time constants; env vars must be set before `import monte_carlo`)"
)

# ---------------------------------------------------------------------------
# Constants (imported live, never hardcoded duplicates of shipped values)
# ---------------------------------------------------------------------------
CUTOFF = CALIBRATION_CUTOFF_DATE                              # "2026-06-15"
VERSION_ID = AlgorithmVersion.get_active_scores_version().id  # 74, matches recon
PREMIUM_MULT = mc.PREMIUM_MULT                                # 1.82 (30DTE, DTE-invariant per design section 0)
TOTAL_HOLD_BARS = STRATEGY_30DTE.HOLD_DAYS                    # 15
TP_PCT = STRATEGY_30DTE.option.TP_BASE                        # +0.30 (base==stress: inert breadth switch)
SL_PCT = STRATEGY_30DTE.option.SL_BASE                        # -0.70
# NOTE: TP_PCT/SL_PCT are not restated verbatim in DESIGN.md's M3 text (which
# defines TP/SL touches abstractly); using the LIVE production 30DTE option
# TP/SL thresholds is the least-arbitrary choice consistent with "shipped
# baseline" being the production arm's role and the "import real constants,
# never reimplement" mandate. Flagged here and in the final report.

OP_COVERAGE_START = "2025-02-10"          # option_prices MIN(date), recon section 1a
RS_LEDGER_CAP = "2026-05-15"              # rs_ledger.parquet's own coverage cap (recon section 1c)
RV_LOOKBACK_BUFFER_DAYS = 130             # calendar-day buffer before earliest signal for the 60-bar RV lookback
FWD_BUFFER_DAYS = 30                      # calendar-day buffer after latest signal for the forward walk
FWD_WINDOW_CAL_DAYS = 24                  # per-contract forward option_prices window (matches build_iv.py's fwd_pnl)
CHECKPOINTS = (5, 10, 15)

SELECTION_RULE = "nearest_moneyness_call_dte20_45_iv.05-5_price>0_oi>0_v1"
QUOTE_BASIS = "yfinance_lastPrice_postclose_pull"

RS_LEDGER = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")

OUT_DIR = os.path.join(_ROOT, ".cache", "iv_engine_pertrade")
OUT_PATH = os.path.join(OUT_DIR, "ledger_v1.parquet")
META_PATH = os.path.join(OUT_DIR, "ledger_v1_meta.json")

ARMS = [
    # (arm_name, premium_key, pnl_kind)
    ("rvconst", "rv", "const"),
    ("rvgamma", "rv", "gamma"),
    ("f2const", "f2", "const"),
    ("f2gamma", "f2", "gamma"),
]

LEDGER_SMOKE = int(os.environ.get("LEDGER_SMOKE", "0"))


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# 1. Candidate signal population (denominator) -- in-sample + shadow_oos
# ---------------------------------------------------------------------------
def load_candidate_signals():
    """Reproduces recon.py's exact candidate-signal population as actual
    (symbol, date, overall) ROWS (recon only computed month-bucketed counts).
    in-sample: rs_ledger.parquet (thru RS_LEDGER_CAP) + live scores gap query
    (RS_LEDGER_CAP+1 .. CUTOFF). shadow_oos: live scores query (CUTOFF+1 ..
    option_prices MAX(date)), flagged is_shadow_oos=True, NEVER used in M1-M3.
    """
    rs = pl.read_parquet(RS_LEDGER)
    rs_win = rs.filter(
        (pl.col("date") >= OP_COVERAGE_START) & (pl.col("date") <= RS_LEDGER_CAP) & (pl.col("overall") >= 70)
    ).select(["symbol", "date", "overall"])
    log(f"  rs_ledger.parquet: N={rs.height}, in-window overall>=70: N={rs_win.height}")

    gap_start = "2026-05-16"
    gap_rows = DB.execute_sql(
        "SELECT symbol, date, overall FROM scores WHERE version_id=%s AND date BETWEEN %s AND %s AND overall>=70",
        (VERSION_ID, gap_start, CUTOFF),
    ).fetchall()
    gap_syms, gap_dates, gap_overalls = [], [], []
    for sym, d, ov in gap_rows:
        gap_syms.append(sym)
        gap_dates.append(d.isoformat() if hasattr(d, "isoformat") else str(d)[:10])
        gap_overalls.append(int(ov))
    gap_df = pl.DataFrame({"symbol": gap_syms, "date": gap_dates, "overall": gap_overalls}) if gap_syms else \
        pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Utf8, "overall": pl.Int64})
    log(f"  live scores gap query [{gap_start}..{CUTOFF}]: N={gap_df.height}")

    insample = pl.concat([rs_win, gap_df], how="vertical_relaxed").unique(subset=["symbol", "date"])
    log(f"  in-sample candidate total (post-merge, deduped): N={insample.height}")

    op_max = DB.execute_sql("SELECT MAX(date) FROM option_prices").fetchone()[0]
    op_max_s = op_max.isoformat() if hasattr(op_max, "isoformat") else str(op_max)[:10]
    shadow_rows = DB.execute_sql(
        "SELECT symbol, date, overall FROM scores WHERE version_id=%s AND date > %s AND date <= %s AND overall>=70",
        (VERSION_ID, CUTOFF, op_max_s),
    ).fetchall()
    shadow_syms, shadow_dates, shadow_overalls = [], [], []
    for sym, d, ov in shadow_rows:
        shadow_syms.append(sym)
        shadow_dates.append(d.isoformat() if hasattr(d, "isoformat") else str(d)[:10])
        shadow_overalls.append(int(ov))
    shadow_df = pl.DataFrame({"symbol": shadow_syms, "date": shadow_dates, "overall": shadow_overalls}) if shadow_syms else \
        pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Utf8, "overall": pl.Int64})
    log(f"  shadow_oos (watch-only) candidate query ({CUTOFF}, {op_max_s}]: N={shadow_df.height}")

    out = []
    for row in insample.iter_rows(named=True):
        ov = int(row["overall"])
        out.append({"symbol": row["symbol"], "date": row["date"], "overall": ov,
                    "band": "75+" if ov >= 75 else "70-74", "is_shadow_oos": False})
    for row in shadow_df.iter_rows(named=True):
        ov = int(row["overall"])
        out.append({"symbol": row["symbol"], "date": row["date"], "overall": ov,
                     "band": "75+" if ov >= 75 else "70-74", "is_shadow_oos": True})
    out.sort(key=lambda r: (r["date"], r["symbol"]))
    return out, op_max_s


# ---------------------------------------------------------------------------
# 2. Bulk price_history pull (ONE query; covers RV lookback + forward walk)
# ---------------------------------------------------------------------------
def fetch_bulk_price_history(symbols, start, end):
    if not symbols:
        return {}, 0.0
    t0 = time.time()
    placeholders = ",".join(["%s"] * len(symbols))
    sql = (f"SELECT symbol, date, close FROM price_history "
           f"WHERE symbol IN ({placeholders}) AND date BETWEEN %s AND %s ORDER BY symbol, date")
    params = list(symbols) + [start, end]
    rows = DB.execute_sql(sql, params).fetchall()
    out = {}
    for sym, d, c in rows:
        if c is None:
            continue
        dd, cc = out.setdefault(sym, ([], []))
        dd.append(d if isinstance(d, date) else date.fromisoformat(str(d)[:10]))
        cc.append(float(c))
    return out, time.time() - t0


def fetch_bulk_earnings(symbols):
    if not symbols:
        return {}
    placeholders = ",".join(["%s"] * len(symbols))
    rows = DB.execute_sql(
        f"SELECT symbol, date FROM earnings_dates WHERE symbol IN ({placeholders})", symbols
    ).fetchall()
    out = {}
    for sym, d in rows:
        dd = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
        out.setdefault(sym, []).append(dd)
    for sym in out:
        out[sym].sort()
    return out


def _price_at(ph_by_symbol, symbol, d):
    """Exact-date bisect lookup. Returns (close, idx) or (None, None)."""
    rec = ph_by_symbol.get(symbol)
    if not rec:
        return None, None
    dates_arr, closes_arr = rec
    pos = bisect.bisect_left(dates_arr, d)
    if pos < len(dates_arr) and dates_arr[pos] == d:
        return closes_arr[pos], pos
    return None, None


def compute_earnings_flag(earn_dates_sorted, signal_date_obj, window_end_date_obj):
    if not earn_dates_sorted:
        return False
    lo = bisect.bisect_right(earn_dates_sorted, signal_date_obj)
    return lo < len(earn_dates_sorted) and earn_dates_sorted[lo] <= window_end_date_obj


# ---------------------------------------------------------------------------
# 3. Per-signal contract selection (chain-slice query -- selection + liquidity
#    in one round trip, per DESIGN.md section 6 point 2 / build_option_slice.py)
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


_FWD_SQL = (
    "SELECT date, price, iv, volume FROM option_prices "
    "WHERE option_id=%s AND date > %s AND date <= DATE_ADD(%s, INTERVAL %s DAY) ORDER BY date"
)


def fetch_forward_path(option_id, date_str):
    rows = DB.execute_sql(_FWD_SQL, (option_id, date_str, date_str, FWD_WINDOW_CAL_DAYS)).fetchall()
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
# 4. First-touch walk (shared by real path + all 4 predicted arms)
# ---------------------------------------------------------------------------
def first_touch_walk(pnl_by_bar, fresh_by_bar, n_available, ripe15, tp_pct, sl_pct):
    """Walks bars 1..min(15, n_available). fresh_by_bar=None means every bar
    counts (used for predicted arms -- freshness is a REAL-quote concept per
    AMENDMENTS A2, not applicable to a closed-form predicted path).
    A positive touch (TP/SL) is trusted whenever it occurs in the available
    window; a negative 'none' read is trusted only when ripe15=True (the
    honest generalization of build_iv.py's fixed scalar pnl15 guard)."""
    for t in range(1, min(15, n_available) + 1):
        pnl = pnl_by_bar.get(t)
        if pnl is None:
            continue
        fresh = True if fresh_by_bar is None else bool(fresh_by_bar.get(t, False))
        if not fresh:
            continue
        if pnl >= tp_pct:
            return t, "TP", True, False
        if pnl <= sl_pct:
            return t, "SL", False, True
    if ripe15:
        return None, "none", False, False
    return None, None, None, None


def _pnl(kind, side, u_t, u0, bars_held, premium_pct, total_dte, vega_ratio):
    if kind == "const":
        return option_pnl_pct(side, u_t, u0, bars_held, premium_pct, total_dte=total_dte,
                               vega_ratio=vega_ratio, delta=DEFAULT_DELTA)
    return bs_option_pnl_pct(side, u_t, u0, bars_held, premium_pct, total_dte=total_dte, vega_ratio=vega_ratio)


# ---------------------------------------------------------------------------
# 5. Per-signal processing (selection -> forward path -> 4 arms)
# ---------------------------------------------------------------------------
def process_signal(sig, ph_by_symbol, earn_by_symbol):
    symbol, date_str, overall, band, is_shadow = sig["symbol"], sig["date"], sig["overall"], sig["band"], sig["is_shadow_oos"]
    date_obj = date.fromisoformat(date_str)

    entry_close, idx = _price_at(ph_by_symbol, symbol, date_obj)
    if entry_close is None or entry_close <= 0:
        return None, "no_price_history_close"

    closes_arr = ph_by_symbol[symbol][1]
    rv = mc.realized_vol(closes_arr, idx)
    if rv is None or rv <= 0:
        return None, "insufficient_rv_history"

    contract = select_contract(symbol, date_str, entry_close)
    if contract is None:
        return None, "no_qualifying_contract"
    option_id, strike, expiration_date, dte_at_entry, entry_premium, entry_iv, entry_oi, entry_volume = contract

    vix = mc._load_vix_series().get(date_obj)

    premium_rv_pct = PREMIUM_MULT * rv / 100.0
    premium_f2_pct = mc._iv_premium_pct(symbol, date_obj, dte_at_entry, premium_rv_pct, rv=rv)

    dates_valid, prices_valid, ivs_valid, volumes_valid = fetch_forward_path(option_id, date_str)
    n_avail = len(dates_valid)
    ripe15 = n_avail >= 15
    bars_range = list(range(1, min(15, n_avail) + 1))

    real_pnl_by_bar, real_fresh_by_bar, underlying_by_bar, iv_by_bar = {}, {}, {}, {}
    for t in bars_range:
        bidx = t - 1
        real_pnl_by_bar[t] = prices_valid[bidx] / entry_premium - 1.0
        iv_by_bar[t] = ivs_valid[bidx]
        lo, hi = max(0, bidx - 1), min(n_avail - 1, bidx + 1)
        real_fresh_by_bar[t] = any(volumes_valid[j] > 0 for j in range(lo, hi + 1))
        u_t, _ = _price_at(ph_by_symbol, symbol, dates_valid[bidx])
        underlying_by_bar[t] = u_t

    real_bar, real_type, real_tp, real_sl = first_touch_walk(
        real_pnl_by_bar, real_fresh_by_bar, n_avail, ripe15, TP_PCT, SL_PCT)

    stale_frac = (sum(1 for t in bars_range if not real_fresh_by_bar[t]) / len(bars_range)) if bars_range else None

    def _checkpoint(t):
        bidx = t - 1
        if bidx < n_avail:
            return prices_valid[bidx], ivs_valid[bidx], underlying_by_bar.get(t), real_fresh_by_bar.get(t)
        return None, None, None, None

    d5_prem, d5_iv, d5_u, d5_fresh = _checkpoint(5)
    d10_prem, d10_iv, d10_u, d10_fresh = _checkpoint(10)
    d15_prem, d15_iv, d15_u, d15_fresh = _checkpoint(15)

    window_end_date = dates_valid[bars_range[-1] - 1] if bars_range else (date_obj + timedelta(days=22))
    earnings_span_flag = compute_earnings_flag(earn_by_symbol.get(symbol, []), date_obj, window_end_date)

    def _vega_ratio(iv_t):
        if earnings_span_flag and entry_iv and iv_t and entry_iv > 0:
            return max(0.20, min(2.0, iv_t / entry_iv))
        return 1.0

    premiums = {"rv": premium_rv_pct, "f2": premium_f2_pct}
    arm_out = {}
    for arm_name, prem_key, kind in ARMS:
        premium_pct = premiums[prem_key]
        pnl_by_bar = {}
        if premium_pct is not None and premium_pct > 0:
            for t in bars_range:
                u_t = underlying_by_bar[t]
                if u_t is None:
                    continue
                vega = _vega_ratio(iv_by_bar[t])
                pnl_by_bar[t] = _pnl(kind, "call", u_t, entry_close, t, premium_pct, dte_at_entry, vega)
        p_bar, p_type, p_tp, p_sl = first_touch_walk(pnl_by_bar, None, n_avail, ripe15, TP_PCT, SL_PCT)
        arm_out[arm_name] = {
            "d5": pnl_by_bar.get(5), "d10": pnl_by_bar.get(10), "d15": pnl_by_bar.get(15),
            "bar": p_bar, "type": p_type, "tp": p_tp, "sl": p_sl,
        }

    rec = {
        "symbol": symbol, "date": date_str, "overall": overall, "band": band, "is_shadow_oos": is_shadow,
        "option_id": option_id, "strike": strike, "expiration_date": expiration_date.isoformat(),
        "dte_at_entry": dte_at_entry, "selection_rule": SELECTION_RULE, "entry_quote_basis": QUOTE_BASIS,

        "entry_close": entry_close, "entry_premium": entry_premium, "entry_iv": entry_iv,
        "entry_volume": entry_volume, "entry_open_interest": entry_oi,
        "liquid_ge5": entry_volume >= 5, "liquid_gt0": entry_volume > 0,
        "real_premium_pct": entry_premium / entry_close,
        "rv": rv, "vix": vix, "era": "2025" if date_obj.year == 2025 else "2026H1",
        "earnings_span_flag": earnings_span_flag,

        "fwd_rows_available": n_avail, "ripe15": ripe15,
        "real_premium_d5": d5_prem, "real_premium_d10": d10_prem, "real_premium_d15": d15_prem,
        "real_iv_d5": d5_iv, "real_iv_d10": d10_iv, "real_iv_d15": d15_iv,
        "real_underlying_d5": d5_u, "real_underlying_d10": d10_u, "real_underlying_d15": d15_u,
        "real_pnl_d5": real_pnl_by_bar.get(5), "real_pnl_d10": real_pnl_by_bar.get(10), "real_pnl_d15": real_pnl_by_bar.get(15),
        "real_first_touch_bar": real_bar, "real_first_touch_type": real_type,
        "real_tp_hit": real_tp, "real_sl_hit": real_sl,
        "d5_fresh": d5_fresh, "d10_fresh": d10_fresh, "d15_fresh": d15_fresh, "stale_frac": stale_frac,

        "premium_rv_pct": premium_rv_pct, "premium_f2_pct": premium_f2_pct,
    }
    for arm_name, _, _ in ARMS:
        a = arm_out[arm_name]
        rec[f"pred_pnl_d5_{arm_name}"] = a["d5"]
        rec[f"pred_pnl_d10_{arm_name}"] = a["d10"]
        rec[f"pred_pnl_d15_{arm_name}"] = a["d15"]
        rec[f"pred_first_touch_bar_{arm_name}"] = a["bar"]
        rec[f"pred_first_touch_type_{arm_name}"] = a["type"]
        rec[f"pred_tp_hit_{arm_name}"] = a["tp"]
        rec[f"pred_sl_hit_{arm_name}"] = a["sl"]
    return rec, None


# ---------------------------------------------------------------------------
# 6. rv_tercile (post-hoc, cut points from the in-sample matched population)
# ---------------------------------------------------------------------------
def add_rv_tercile(df):
    insample_rv = df.filter((~pl.col("is_shadow_oos")) & pl.col("rv").is_not_null())["rv"].to_numpy()
    if insample_rv.size < 3:
        log("  rv_tercile: SKIPPED (insufficient in-sample rv values)")
        return df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("rv_tercile")), None, None
    t1, t2 = np.quantile(insample_rv, [1.0 / 3, 2.0 / 3])

    def _label(v):
        if v is None:
            return None
        if v <= t1:
            return "T1_low"
        if v <= t2:
            return "T2_mid"
        return "T3_high"

    labels = [_label(v) for v in df["rv"].to_list()]
    df = df.with_columns(pl.Series("rv_tercile", labels, dtype=pl.Utf8))
    log(f"  rv_tercile cuts (in-sample matched, N={insample_rv.size}): t1={t1:.4f} t2={t2:.4f}")
    return df, float(t1), float(t2)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    t_start = time.time()
    log("=== iv_engine_pertrade LEDGER BUILD ===")
    log(f"CALIBRATION_CUTOFF_DATE = {CUTOFF}")
    log(f"active scores version_id = {VERSION_ID}")
    log(f"TP_PCT={TP_PCT} SL_PCT={SL_PCT} (STRATEGY_30DTE.option, live import) TOTAL_HOLD_BARS={TOTAL_HOLD_BARS}")
    log(f"PREMIUM_MULT={PREMIUM_MULT} (monte_carlo.PREMIUM_MULT, DTE-invariant per design section 0)")
    if LEDGER_SMOKE:
        log(f"*** LEDGER_SMOKE={LEDGER_SMOKE} -- limiting to first {LEDGER_SMOKE} chainable signals ***")

    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=120000")

    log("\n--- 1. candidate signal population ---")
    all_candidates, op_max_s = load_candidate_signals()
    n_insample = sum(1 for c in all_candidates if not c["is_shadow_oos"])
    n_shadow = sum(1 for c in all_candidates if c["is_shadow_oos"])
    log(f"candidate signals: in-sample={n_insample} shadow_oos={n_shadow} total={len(all_candidates)} "
        f"(option_prices MAX(date)={op_max_s})")

    log("\n--- 2. chain-bearing symbol prefilter ---")
    chain_syms = set(r[0] for r in DB.execute_sql("SELECT DISTINCT symbol FROM options").fetchall())
    cand_syms = set(c["symbol"] for c in all_candidates)
    bulk_syms = sorted(chain_syms & cand_syms)
    chainable = [c for c in all_candidates if c["symbol"] in bulk_syms]
    log(f"chain-bearing symbols (options table): {len(chain_syms)}")
    log(f"candidate distinct symbols: {len(cand_syms)}; chain-bearing intersection: {len(bulk_syms)}")
    log(f"chainable signals (will be queried): {len(chainable)} of {len(all_candidates)}")

    if LEDGER_SMOKE:
        chainable = chainable[:LEDGER_SMOKE]
        log(f"SMOKE: reduced to {len(chainable)} signals")

    log("\n--- 3. bulk price_history pull ---")
    all_dates = [c["date"] for c in chainable] or [c["date"] for c in all_candidates]
    min_d, max_d = min(all_dates), max(all_dates)
    ph_start = (date.fromisoformat(min_d) - timedelta(days=RV_LOOKBACK_BUFFER_DAYS)).isoformat()
    ph_end = (date.fromisoformat(max_d) + timedelta(days=FWD_BUFFER_DAYS)).isoformat()
    ph_by_symbol, ph_elapsed = fetch_bulk_price_history(bulk_syms, ph_start, ph_end)
    n_ph_rows = sum(len(v[0]) for v in ph_by_symbol.values())
    log(f"bulk price_history: {n_ph_rows:,} rows / {len(ph_by_symbol)} symbols [{ph_start}..{ph_end}] in {ph_elapsed:.1f}s")

    log("\n--- 4. bulk earnings_dates pull ---")
    earn_by_symbol = fetch_bulk_earnings(bulk_syms)
    log(f"bulk earnings_dates: {sum(len(v) for v in earn_by_symbol.values())} rows / {len(earn_by_symbol)} symbols")

    log("\n--- 5. VIX series (lazy, shared with mc._iv_premium_pct) ---")
    vix_map = mc._load_vix_series()
    log(f"vix series loaded: N={len(vix_map)}")

    log("\n--- 6. per-signal selection + forward-path loop ---")
    records = []
    miss_counts = Counter()
    t_loop = time.time()
    n_total = len(chainable)
    for i, sig in enumerate(chainable):
        try:
            rec, miss_reason = process_signal(sig, ph_by_symbol, earn_by_symbol)
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
        sys.exit(1)

    df = pl.DataFrame(records, infer_schema_length=None)
    df, rv_t1, rv_t2 = add_rv_tercile(df)

    os.makedirs(OUT_DIR, exist_ok=True)
    df.write_parquet(OUT_PATH)
    log(f"\nwrote {OUT_PATH}  N={df.height}  cols={len(df.columns)}")

    n_matched_insample = sum(1 for r in records if not r["is_shadow_oos"])
    n_matched_shadow = sum(1 for r in records if r["is_shadow_oos"])
    n_liquid_insample = sum(1 for r in records if not r["is_shadow_oos"] and r["liquid_ge5"])
    match_rate = (n_matched_insample / n_insample) if n_insample else None

    log("\n--- coverage summary ---")
    log(f"in-sample: candidates={n_insample} matched={n_matched_insample} "
        f"match_rate={match_rate:.1%} liquid_ge5={n_liquid_insample} ({n_liquid_insample/n_matched_insample:.1%} of matched)"
        if n_matched_insample else f"in-sample: candidates={n_insample} matched=0")
    log(f"shadow_oos (watch-only): candidates={n_shadow} matched={n_matched_shadow}")

    meta = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version_id": VERSION_ID, "cutoff": CUTOFF, "option_prices_max_date": op_max_s,
        "tp_pct": TP_PCT, "sl_pct": SL_PCT, "premium_mult": PREMIUM_MULT, "total_hold_bars": TOTAL_HOLD_BARS,
        "candidate_insample": n_insample, "candidate_shadow_oos": n_shadow,
        "chain_bearing_symbols": len(chain_syms), "candidate_distinct_symbols": len(cand_syms),
        "chainable_symbols": len(bulk_syms), "chainable_signals": len(chainable),
        "matched_insample": n_matched_insample, "matched_shadow_oos": n_matched_shadow,
        "match_rate_insample": match_rate, "liquid_ge5_insample": n_liquid_insample,
        "miss_reasons": {str(k): v for k, v in miss_counts.items()},
        "rv_tercile_cuts": {"t1": rv_t1, "t2": rv_t2},
        "bulk_price_history_rows": n_ph_rows, "bulk_price_history_elapsed_s": round(ph_elapsed, 2),
        "loop_elapsed_s": round(loop_elapsed, 1),
        "total_elapsed_s": round(time.time() - t_start, 1),
        "smoke": LEDGER_SMOKE or None,
        "mc_model_hits": mc._MODEL_HITS, "mc_model_misses": mc._MODEL_MISSES,
        "mc_model_clamp_floor_hits": mc._MODEL_CLAMP_FLOOR_HITS, "mc_model_clamp_cap_hits": mc._MODEL_CLAMP_CAP_HITS,
        "mc_model_ratios_n": len(mc._MODEL_RATIOS),
        "mc_model_ratios_mean": (float(np.mean(mc._MODEL_RATIOS)) if mc._MODEL_RATIOS else None),
        "mc_model_ratios_median": (float(np.median(mc._MODEL_RATIOS)) if mc._MODEL_RATIOS else None),
    }
    with open(META_PATH, "w", encoding="ascii", errors="backslashreplace") as f:
        json.dump(meta, f, indent=2, default=str)
    log(f"wrote {META_PATH}")

    log(f"\n[IV_MODEL telemetry] hits={mc._MODEL_HITS} misses={mc._MODEL_MISSES} "
        f"clamp_floor_hits={mc._MODEL_CLAMP_FLOOR_HITS} clamp_cap_hits={mc._MODEL_CLAMP_CAP_HITS}")

    log(f"\n=== DONE in {time.time() - t_start:.0f}s ===")


if __name__ == "__main__":
    main()
