"""REAL option-contract price ledger for funded 75+ CALL signals (Polygon Developer).

WHAT THIS IS
------------
For every 75+ CALL signal under the ACTIVE scoring version (v74) in
[--start .. --end], pull the ACTUAL traded daily OHLC path of the ATM ~30-DTE
call the engine would have bought, and write one row per (symbol, signal_date)
to `.cache/polygon_real_premium/real_premium_ledger.parquet`.

This is REAL PRINTS, not a Black-Scholes / realized-vol model. It exists to
replace `premium_pct = PREMIUM_MULT * realized_vol / 100` (monte_carlo.py
~line 2256) with what the contract actually cost and actually did.

NOT a rebuild of prior work (deliberately distinct):
  * .cache/polygon_iv/iv_ledger_polygon.parquet  -- BS-DERIVED IV, one ATM row
    per signal, NO forward path.
  * .cache/iv_engine_pertrade/ledger_v1.parquet  -- yfinance lastPrice, only
    2025-02 -> now.
  * .cache/bankroll_ladder_otm_4yr/ladder_polygon_2021_2025.parquet -- real
    Polygon OHLC but OTM-LADDER shaped (5 moneyness x 3 DTE bins x 12 TP/SL
    combos), ends 2025-12.
This one: ATM, ~30 DTE, full forward daily OHLC path, 2022-08 -> today, one
row per signal, engine-config matched.

LOCKED CONTRACT SELECTION (do not vary -- see DESIGN.md)
  1. chain = list_option_contracts(sym, as_of=signal_date, contract_type='call')
     restricted to expirations in [signal_date+21, signal_date+45].
  2. expiration: calendar-day DTE closest to 30, restricted to DTE in [21,45];
     tie -> shorter DTE. No expiry in band -> status 'miss:no_dte_band'.
  3. strike: nearest to the signal-date underlying close (ATM); tie -> lower
     strike. If that strike has no bars, fall back to the 2nd and 3rd nearest
     strikes (strike_rank 0/1/2). All three dry -> 'miss:no_atm_price'.
  4. bars: option_daily_bars(signal_date, min(expiration, signal_date+45cal)).

TP / SL TOUCH CONVENTIONS (asymmetric ON PURPOSE)
  * tp30_touch_bar -- first forward trading-day offset with intraday HIGH >=
    1.30 * entry_premium_real. A TP is a RESTING LIMIT order: it fills the
    moment the print trades through it, so the intraday high is the honest
    detector.
  * sl70_touch_bar -- first forward trading-day offset with daily CLOSE <=
    0.30 * entry_premium_real. Our engine models a forced exit at END OF DAY,
    not as a live intraday stop order, so the daily CLOSE (not the LOW) is the
    honest detector. Using the LOW here would manufacture stop-outs the engine
    would never have taken.
  Both are recorded independently; whichever offset is smaller happened first.

HARD INVARIANTS (traps.md / data-acquisition.md)
  * Vendor data NEVER enters MySQL. This script READS `scores` + `price_history`
    and WRITES only under .cache/polygon_real_premium/. It never touches the
    `options` / `option_prices` tables.
  * PyMySQL is NOT thread-safe: EVERY database read (signals, underlying closes,
    realized vol, model premium) happens SINGLE-THREADED before the thread pool
    starts. Worker threads do Polygon HTTP only.
  * polars: build DataFrames with infer_schema_length=None (columns that are
    None early and populated later otherwise blow up after ~100 rows), and
    fill_nan(None) at one choke point (NaN passes is_not_null()).
  * The API key is never printed or logged.
  * ASCII-only stdout; PYTHONIOENCODING/PYTHONUTF8 set defensively.
  * This script does NOT self-submit to the task queue. Queue it yourself.

POLYGON DEVELOPER COVERAGE BOUNDARY (empirical)
  The AGGREGATES endpoint has a ~4-YEAR ROLLING lookback: option daily bars
  403 NOT_AUTHORIZED before roughly (today - 4y). The CHAIN endpoint is NOT
  time-limited, so selection succeeds pre-boundary and only the bar walk 403s.
  Those are counted as 'miss:timeframe_403' coverage misses, never crashes.

Usage
-----
    python experiments/polygon_real_premium/pull.py --selftest
    python experiments/polygon_real_premium/pull.py --limit 25 --workers 4
    python experiments/polygon_real_premium/pull.py --workers 8          # QUEUE THIS
    python experiments/polygon_real_premium/pull.py --consolidate-only   # offline
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]                     # polygon_real_premium -> experiments -> repo root
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))           # pin repo root FIRST (worktree PYTHONPATH trap)
_DATA_INGEST = _ROOT / "experiments" / "data_ingest"
if str(_DATA_INGEST) not in sys.path:
    sys.path.insert(0, str(_DATA_INGEST))

import polars as pl                          # noqa: E402

# polygon_client is import-safe without a key.
from polygon_client import PolygonClient, rate_sleep_seconds   # noqa: E402

# ---------------------------------------------------------------------------
# Locked spec constants.
# ---------------------------------------------------------------------------
SCORE_MIN = 75                 # funded CALL signal floor (CLAUDE.md: >=75 = call opportunity)
TARGET_DTE = 30                # engine nominal calendar DTE
DTE_LO, DTE_HI = 18, 50        # admissible calendar-day DTE band (SELECTION)
                               # Widened from 21-45 (2026-07-25, orchestrator): for a
                               # monthly-only name a signal near the start of a month
                               # straddles both monthlies (this month ~+18cd, next
                               # ~+46cd) and lands in NEITHER, dropping the signal and
                               # biasing coverage toward weekly-listed (large, liquid)
                               # names -- exactly the wrong bias for the cheap-end
                               # affordability study this feeds. dte_cal is recorded per
                               # row and premium scales ~sqrt(DTE), so the headline
                               # real-vs-model premium calibration is computed on the
                               # tight [25,38] sub-band while coverage/affordability
                               # uses the full band.
CHAIN_EXP_LO, CHAIN_EXP_HI = 7, 90   # wider window the chain is QUERIED over, so
                                     # 'no listed options' is distinguishable from
                                     # 'nothing landed in the 21-45 band'
MAX_STRIKE_RANK = 3            # ATM + next 2 nearest strikes
WALK_MAX_CAL_DAYS = 45         # bar walk end = min(expiration, signal_date + this)
MIN_FORWARD_BARS = 2           # a 'kept' row must have >= this many forward bars
LIQUID_MIN_VOLUME = 5          # entry-bar contracts traded (repo convention)
LIQUID_MIN_TRADES = 1          # entry-bar trade count (Polygon `n`)
PATH_END_TOLERANCE_DAYS = 3    # weekend/holiday slack when deciding "reached the end"

TP_MULT = 1.30                 # +30% TP, detected on intraday HIGH  (limit order)
SL_MULT = 0.30                 # -70% SL, detected on daily CLOSE    (EOD forced exit)
PNL_OFFSETS = (5, 10, 15)      # real_pnl_d5 / d10 / d15 (trading-day offsets)

# Engine model-premium reference (strategy_config.STRATEGY_30DTE), loaded lazily
# so --selftest never imports the config/DB stack.
_PREMIUM_MULT_FALLBACK = 1.82
_VOL_LOOKBACK_FALLBACK = 60

DEFAULT_START = "2022-08-01"

# ---------------------------------------------------------------------------
# Paths.
# ---------------------------------------------------------------------------
OUT_DIR = _ROOT / ".cache" / "polygon_real_premium"
PROGRESS = OUT_DIR / "_ingest_progress.jsonl"
OUT_PARQUET = OUT_DIR / "real_premium_ledger.parquet"
META_JSON = OUT_DIR / "meta.json"
ENV_FILE = _ROOT / ".env"

COLUMNS = [
    # identity / context
    "symbol", "signal_date", "overall", "entry_price",
    "spot_unadj", "spot_source", "adj_factor",
    "vol", "model_premium_pct", "model_premium_abs",
    "option_ticker", "expiration_date", "dte_cal", "atm_strike", "moneyness", "strike_rank",
    # real entry bar
    "entry_premium_real", "entry_open", "entry_high", "entry_low", "entry_vwap",
    "entry_volume", "entry_trades", "liquid_entry", "real_model_ratio",
    # forward path
    "path_json",
    "real_pnl_d5", "real_pnl_d10", "real_pnl_d15", "real_pnl_expiry",
    "real_max_mult", "real_min_mult", "tp30_touch_bar", "sl70_touch_bar",
    "bars_covered", "stale_frac", "path_end_reason", "path_end_date",
    # coverage / status
    "status", "n_api_calls",
]

_FLOAT_COLS = [
    "entry_price", "spot_unadj", "adj_factor",
    "vol", "model_premium_pct", "model_premium_abs", "atm_strike", "moneyness",
    "entry_premium_real", "entry_open", "entry_high", "entry_low", "entry_vwap",
    "entry_volume", "entry_trades", "real_model_ratio",
    "real_pnl_d5", "real_pnl_d10", "real_pnl_d15", "real_pnl_expiry",
    "real_max_mult", "real_min_mult", "stale_frac",
]
_INT_COLS = ["overall", "dte_cal", "strike_rank", "tp30_touch_bar",
             "sl70_touch_bar", "bars_covered", "n_api_calls"]
_BOOL_COLS = ["liquid_entry"]

# ---------------------------------------------------------------------------
# Timeframe-403 accounting (workers are threads).
# ---------------------------------------------------------------------------
_tf403_lock = threading.Lock()
_tf403_state = {"logged": False, "n": 0, "dates": set()}
_bar_date_lock = threading.Lock()
_bar_date_state = {"earliest": None}


def log(msg) -> None:
    print(msg, flush=True)


def _is_timeframe_403(exc: Exception) -> bool:
    """True iff exc is the Polygon aggregates timeframe-entitlement 403 (NOT a
    generic 4xx). Only this specific 403 is a coverage miss; 429/5xx/network stay
    on PolygonClient's retry/backoff path."""
    s = str(exc).upper()
    return "403" in s and ("NOT_AUTHORIZED" in s or "DATA TIMEFRAME" in s
                           or "DOESN'T INCLUDE THIS DATA" in s)


def _note_tf403(d: str) -> None:
    with _tf403_lock:
        _tf403_state["n"] += 1
        _tf403_state["dates"].add(d)
        if not _tf403_state["logged"]:
            _tf403_state["logged"] = True
            log("[info] Polygon aggregates timeframe-403 (e.g. signal %s) -> counted as "
                "miss:timeframe_403 and continuing. Developer aggregates are ~4yr rolling; "
                "the chain endpoint is not time-limited. Further 403s are counted silently "
                "(see meta.json)." % d)


def _note_bar_date(d: str) -> None:
    with _bar_date_lock:
        cur = _bar_date_state["earliest"]
        if cur is None or d < cur:
            _bar_date_state["earliest"] = d


# ---------------------------------------------------------------------------
# Key handling: env first, then .env. Never printed.
# ---------------------------------------------------------------------------
def load_env_key() -> str | None:
    key = os.environ.get("POLYGON_API_KEY", "").strip()
    if key:
        return key
    if ENV_FILE.exists():
        try:
            for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "POLYGON_API_KEY":
                    v = v.strip().strip('"').strip("'")
                    if v:
                        os.environ["POLYGON_API_KEY"] = v
                        return v
        except Exception as exc:
            log(f"[warn] failed reading {ENV_FILE}: {exc!r}")
    return None


def require_key() -> str:
    key = load_env_key()
    if not key:
        log("ERROR: POLYGON_API_KEY not found.")
        log(f"  Set it in the environment, or add 'POLYGON_API_KEY=...' to {ENV_FILE}")
        log("  (--selftest and --consolidate-only need NO key.)")
        raise SystemExit(2)
    return key


# ---------------------------------------------------------------------------
# Date helpers (pure).
# ---------------------------------------------------------------------------
def _dt(s) -> date:
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    return date.fromisoformat(str(s)[:10])


def _f(x):
    return float(x) if x is not None else None


# ---------------------------------------------------------------------------
# Realized vol -- FAITHFUL copy of monte_carlo.realized_vol (pure, offline-testable).
# `closes` is the symbol's ordered daily close series; base_idx is the signal
# date's index. Returns daily sigma in PERCENT, or None when underfed.
# ---------------------------------------------------------------------------
def realized_vol(closes, base_idx: int, lookback: int = _VOL_LOOKBACK_FALLBACK):
    if base_idx < lookback:
        return None
    rets = []
    for j in range(base_idx - lookback + 1, base_idx + 1):
        prev = closes[j - 1]
        if prev > 0:
            rets.append((closes[j] - prev) / prev)
    if len(rets) < lookback // 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * 100


# ---------------------------------------------------------------------------
# Signal source (MySQL, SINGLE-THREADED ONLY).
# ---------------------------------------------------------------------------
def load_signals(start: str, end: str, symbols: set[str] | None = None) -> tuple[list[dict], str]:
    """Funded 75+ CALL signals under the ACTIVE scoring version.

    Mirrors monte_carlo.load_signals' source of truth (the version-keyed `scores`
    table read through AlgorithmVersion.get_active_scores_version()) but keeps
    only what an option pull needs -- symbol / date / overall -- and pins the
    CALL floor at SCORE_MIN (75) rather than monte_carlo's 70 overflow tier,
    because 75+ is the tradable call signal per CLAUDE.md.

    Returns (rows, version_label). MUST be called before the thread pool starts.
    """
    from database.models.core import Score, AlgorithmVersion   # lazy: --selftest stays DB-free
    ver = AlgorithmVersion.get_active_scores_version()
    q = (Score.select(Score.symbol, Score.date, Score.overall)
         .where(Score.version == ver,
                Score.date >= _dt(start), Score.date <= _dt(end),
                Score.overall >= SCORE_MIN)
         .order_by(Score.date, Score.symbol))
    rows = []
    for s in q:
        sym = s.symbol_id            # FK column holds the ticker string (peewee FK-per-row trap)
        if symbols and sym not in symbols:
            continue
        rows.append({"symbol": sym, "signal_date": s.date.isoformat(),
                     "overall": int(s.overall)})
    return rows, f"v{ver.id}"


def _premium_mult() -> float:
    """Engine's 30-DTE ATM premium multiplier (premium_pct = MULT * sigma_daily)."""
    try:
        import strategy_config as sc
        return float(sc.STRATEGY_30DTE.PREMIUM_MULT)
    except Exception:
        return _PREMIUM_MULT_FALLBACK


def _vol_lookback() -> int:
    try:
        import strategy_config as sc
        return int(sc.STRATEGY_30DTE.VOL_LOOKBACK)
    except Exception:
        return _VOL_LOOKBACK_FALLBACK


# ---------------------------------------------------------------------------
# AS-TRADED (UNADJUSTED) SPOT -- required for honest ATM strike selection.
#
# WHY THIS EXISTS (the spec assumed it away; it does not hold):
#   MySQL `price_history.close` is written by trader.py's yfinance pull with
#   auto_adjust=True, i.e. it is BACK-ADJUSTED for splits AND dividends/spinoffs.
#   Historical option STRIKES are in as-traded dollars. Selecting "the strike
#   nearest the underlying close" against an adjusted close therefore picks the
#   wrong strike -- badly. Verified: MMM 2022-08-23 adjusted close = 103.79,
#   as-traded close = 141.75 (the listed $1-increment strikes that day ran
#   131..140 and the 134C closed at 9.44, i.e. ~7.75 intrinsic -> spot ~141.7).
#   The error is double-digit-% for most dividend payers and every later split.
#
#   Polygon cannot fix this: on the Options Developer key, EQUITY aggregates and
#   /v1/open-close are entitled only ~2 years back (2023-01 403s, 2024-08 works),
#   so there is no vendor unadjusted equity close for most of the window.
#
# WHAT WE DO: yfinance `auto_adjust=False` Close is split-adjusted but NOT
#   dividend-adjusted; un-applying the FORWARD split factors recovers the
#   as-traded close exactly:
#       spot_unadj(t) = yf_Close(t) * PROD(split_ratio(s) for s > t)
#   Validated: NVDA 2022-08-23 -> 17.18 * 10 = 171.81 (true pre-split print);
#   MMM -> 118.52 * 1.196 = 141.75 (matches the option chain, above);
#   AZN -> 133.28 * 0.5 = 66.64 (2026 ADR ratio change).
#
# `entry_price` (the ADJUSTED close) is kept untouched because that is what the
# engine's realized-vol / premium_pct path actually uses -- the two live in
# different price spaces on purpose, and `adj_factor` records the ratio.
# ---------------------------------------------------------------------------
UNADJ_CACHE = OUT_DIR / "_unadj_spot.parquet"


def apply_forward_splits(dates: list[str], closes: list[float],
                         splits: list[tuple[str, float]]) -> list[float]:
    """PURE. spot_unadj(t) = close(t) * PROD(ratio for every split dated > t).
    `splits` is [(YYYY-MM-DD, ratio)]; ratios <= 0 are ignored."""
    sp = sorted((d, float(r)) for d, r in splits if r and float(r) > 0)
    out = []
    for d, c in zip(dates, closes):
        f = 1.0
        for sd, r in sp:
            if sd > d:
                f *= r
        out.append(c * f)
    return out


def load_unadj_spots(symbols: list[str], start: str, end: str,
                     cache: Path = UNADJ_CACHE, refresh: bool = False,
                     batch: int = 50) -> dict[str, dict[str, float]]:
    """SINGLE-THREADED. {symbol: {date: as-traded close}} for every symbol, cached
    to parquet so re-runs are free. Missing symbols (delisted / no yfinance data)
    simply do not appear; the caller falls back to the adjusted close and tags
    spot_source='ph_adjusted'."""
    import warnings
    have: dict[str, dict[str, float]] = {}
    if cache.exists() and not refresh:
        df = pl.read_parquet(cache)
        for sym, d, c in zip(df["symbol"].to_list(), df["date"].to_list(),
                             df["close_unadj"].to_list()):
            have.setdefault(sym, {})[d] = c
    need = [s for s in symbols if s not in have]
    if not need:
        log(f"unadjusted spot cache hit for all {len(symbols)} symbols")
        return have

    log(f"fetching as-traded (unadjusted) closes for {len(need)} symbols via yfinance "
        f"(single-threaded, cached to {cache.name})...")
    import yfinance as yf
    y_start = (_dt(start) - timedelta(days=30)).isoformat()
    y_end = (_dt(end) + timedelta(days=2)).isoformat()
    for i in range(0, len(need), batch):
        chunk = need[i:i + batch]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                data = yf.download(chunk, start=y_start, end=y_end, interval="1d",
                                   auto_adjust=False, actions=True, group_by="ticker",
                                   progress=False, threads=True)
        except Exception as exc:
            log(f"[warn] yfinance batch {i//batch} failed: {exc!r}")
            continue
        for sym in chunk:
            try:
                sub = data[sym] if len(chunk) > 1 else data
                sub = sub.dropna(subset=["Close"])
                if sub.empty:
                    continue
                dates = [str(x)[:10] for x in sub.index]
                closes = [float(x) for x in sub["Close"].tolist()]
                splits = []
                if "Stock Splits" in sub.columns:
                    for d, r in zip(dates, sub["Stock Splits"].tolist()):
                        if r and float(r) > 0:
                            splits.append((d, float(r)))
                unadj = apply_forward_splits(dates, closes, splits)
                have[sym] = {d: v for d, v in zip(dates, unadj) if v and v > 0}
            except Exception:
                continue
        log(f"  unadjusted spot: {min(i+batch, len(need))}/{len(need)} symbols")

    recs = [{"symbol": s, "date": d, "close_unadj": c}
            for s, m in have.items() for d, c in m.items()]
    if recs:
        cache.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(recs, infer_schema_length=None).write_parquet(cache)
        log(f"wrote {cache} ({len(recs)} rows, {len(have)} symbols)")
    return have


def enrich_signals(signals: list[dict], closes_obj,
                   unadj: dict[str, dict[str, float]] | None = None) -> None:
    """SINGLE-THREADED. Attach entry_price / spot_unadj / spot_source / adj_factor /
    vol / model_premium_pct / model_premium_abs to each signal in place. After this
    returns, NO worker thread needs the database.

    entry_price  = ADJUSTED close (price_history) -- the engine's own price space,
                   the one realized_vol and premium_pct are computed in.
    spot_unadj   = AS-TRADED close -- the only honest anchor for strike selection.
    model_premium_abs = model_premium_pct * spot_unadj -- the engine's modeled
                   option cost expressed in the same dollars the real contract
                   traded in, so real-vs-model is an apples-to-apples ratio."""
    mult = _premium_mult()
    lb = _vol_lookback()
    unadj = unadj or {}
    series: dict[str, tuple[list[str], list[float], dict[str, int]]] = {}
    for sig in signals:
        sym = sig["symbol"]
        if sym not in series:
            by_date = closes_obj._by_symbol.get(sym, {}) or {}
            ds = sorted(d for d, c in by_date.items() if c is not None and c > 0)
            cs = [float(by_date[d]) for d in ds]
            series[sym] = (ds, cs, {d: i for i, d in enumerate(ds)})
        ds, cs, idx = series[sym]
        i = idx.get(sig["signal_date"])
        if i is None:
            sig.update({"entry_price": None, "vol": None, "model_premium_pct": None,
                        "spot_unadj": None, "spot_source": None, "adj_factor": None,
                        "model_premium_abs": None})
            continue
        sig["entry_price"] = cs[i]
        v = realized_vol(cs, i, lookback=lb)
        sig["vol"] = v
        pct = (mult * v / 100.0) if v else None
        sig["model_premium_pct"] = pct

        u = (unadj.get(sym) or {}).get(sig["signal_date"])
        if u and u > 0:
            sig["spot_unadj"] = float(u)
            sig["spot_source"] = "yf_unadj"
        else:
            sig["spot_unadj"] = cs[i]           # fallback: adjusted close
            sig["spot_source"] = "ph_adjusted"
        sig["adj_factor"] = sig["spot_unadj"] / cs[i] if cs[i] else None
        sig["model_premium_abs"] = (pct * sig["spot_unadj"]) if pct else None


def load_done(progress_path: Path = PROGRESS) -> set[tuple[str, str]]:
    done: set[tuple[str, str]] = set()
    if progress_path.exists():
        for line in progress_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                done.add((r["symbol"], r["signal_date"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


# ---------------------------------------------------------------------------
# Contract selection (PURE -- offline testable).
# ---------------------------------------------------------------------------
_STD_TICKER = re.compile(r"^O:[A-Z]+\d{6}[CP]\d+$")


def normalize_chain(raw: list[dict], sym: str, d_date: date) -> list[dict]:
    """Polygon contract rows -> [{'ticker','strike','expiration_date','dte'}].

    Drops non-calls, malformed rows, and ADJUSTED contracts (non-100 deliverable
    or a non-standard OCC root such as O:AAPL1...), which are not what the engine
    would ever trade. Dedupes on (expiration, strike), preferring the standard
    OCC ticker."""
    best: dict[tuple[str, float], dict] = {}
    for c in raw:
        if c.get("contract_type") != "call":
            continue
        if not c.get("strike_price") or not c.get("expiration_date") or not c.get("ticker"):
            continue
        spc = c.get("shares_per_contract")
        if spc is not None and int(spc) != 100:
            continue
        tick = str(c["ticker"])
        std = bool(_STD_TICKER.match(tick)) and tick.startswith(f"O:{sym}")
        exp = _dt(c["expiration_date"])
        key = (exp.isoformat(), float(c["strike_price"]))
        rec = {"ticker": tick, "strike": float(c["strike_price"]),
               "expiration_date": exp, "dte": (exp - d_date).days, "std": std}
        prev = best.get(key)
        if prev is None or (rec["std"] and not prev["std"]):
            best[key] = rec
    return sorted(best.values(), key=lambda r: (r["dte"], r["strike"]))


def select_expiry(chain: list[dict], dte_lo: int = DTE_LO, dte_hi: int = DTE_HI,
                  target: int = TARGET_DTE):
    """Calendar-day DTE closest to `target` within [dte_lo, dte_hi]; tie -> shorter.
    Returns (dte, [contracts at that expiry]) or (None, [])."""
    in_band = [c for c in chain if dte_lo <= c["dte"] <= dte_hi]
    if not in_band:
        return None, []
    best_dte = min(in_band, key=lambda c: (abs(c["dte"] - target), c["dte"]))["dte"]
    return best_dte, [c for c in in_band if c["dte"] == best_dte]


def rank_strikes(contracts: list[dict], spot: float, max_rank: int = MAX_STRIKE_RANK) -> list[dict]:
    """Contracts ordered by |strike - spot| (ATM first); tie -> lower strike.
    Truncated to `max_rank`. Each returned dict carries 'strike_rank'."""
    ordered = sorted(contracts, key=lambda c: (abs(c["strike"] - spot), c["strike"]))
    out = []
    for i, c in enumerate(ordered[:max_rank]):
        rec = dict(c)
        rec["strike_rank"] = i
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Bars (PURE helpers).
# ---------------------------------------------------------------------------
def bars_ohlc_by_date(bars: list[dict]) -> dict:
    """Polygon aggregates -> {YYYY-MM-DD: {'o','h','l','c','v','vw','n'}}.
    `t` is ms epoch UTC; Polygon stamps a US daily bar at 00:00 ET, so the UTC
    date equals the trading date."""
    out: dict = {}
    for b in bars:
        t = b.get("t")
        if t is None:
            continue
        d = datetime.fromtimestamp(t / 1000, tz=timezone.utc).date().isoformat()
        out[d] = {"o": _f(b.get("o")), "h": _f(b.get("h")), "l": _f(b.get("l")),
                  "c": _f(b.get("c")), "v": _f(b.get("v")), "vw": _f(b.get("vw")),
                  "n": _f(b.get("n"))}
    return out


def build_forward_path(by_date: dict, signal_date: str, end_date: str,
                       tdi: dict[str, int] | None = None) -> list[dict]:
    """Strictly-forward bars (bar date > signal_date, <= end_date), sorted ascending,
    stamped with `off` = MARKET TRADING DAYS elapsed since the signal date.

    WHY NOT A BAR INDEX: Polygon emits NO aggregate at all for a day an option did
    not trade. Numbering bars 1,2,3... would therefore make `real_pnl_d10` mean
    "the 10th day this contract happened to print", which on an illiquid contract
    can be 3 weeks out -- silently incomparable to the engine, which counts market
    trading days. `tdi` is a {date: trading_day_index} map built once from the
    repo's own price_history calendar; when it is absent we fall back to the bar
    sequence and say so in the docs."""
    rows = []
    for d in sorted(by_date):
        if d <= signal_date or d > end_date:
            continue
        b = by_date[d]
        if b.get("c") is None or b.get("c") <= 0:
            continue
        rows.append({"d": d, "o": b.get("o"), "h": b.get("h"), "l": b.get("l"),
                     "c": b.get("c"), "v": b.get("v"), "n": b.get("n")})
    base = tdi.get(signal_date) if tdi else None
    for i, r in enumerate(rows):
        off = None
        if base is not None and tdi is not None:
            j = tdi.get(r["d"])
            if j is not None:
                off = j - base
        r["off"] = off if off is not None else i + 1
    return rows


def build_trading_index(closes_obj) -> tuple[list[str], dict[str, int]]:
    """SINGLE-THREADED. The market trading calendar as this repo knows it: the
    sorted union of every date present in the preloaded price_history closes.
    Returns (sorted_dates, {date: index})."""
    seen: set[str] = set()
    for m in getattr(closes_obj, "_by_symbol", {}).values():
        seen.update(m.keys())
    days = sorted(seen)
    return days, {d: i for i, d in enumerate(days)}


def expected_forward_bars(tdays: list[str], signal_date: str, end_date: str) -> int:
    """PURE. How many market trading days SHOULD the forward path contain:
    count of calendar entries t with signal_date < t <= end_date, capped at the
    last date the calendar knows about (so an unexpired recent contract is not
    scored as 'stale' for days that have not happened yet)."""
    import bisect
    if not tdays:
        return 0
    cap = min(end_date, tdays[-1])
    lo = bisect.bisect_right(tdays, signal_date)
    hi = bisect.bisect_right(tdays, cap)
    return max(0, hi - lo)


# ---------------------------------------------------------------------------
# THE WALK (PURE -- this is what --selftest proves).
#
# `path` is a list of bar dicts each with 'off' (trading-day offset from the
# signal/entry bar), 'h', 'l', 'c' (and optionally 'v'). Production always
# passes a STRICTLY FORWARD path (off starts at 1), so a same-bar TP touch
# cannot occur on real data -- but the function is offset-indexed, so a caller
# that includes the entry bar (off=0) gets a correct off=0 answer. The selftest
# exercises that case explicitly.
# ---------------------------------------------------------------------------
def walk_path(path: list[dict], entry: float | None, expected: int | None = None) -> dict:
    out = {"real_pnl_d5": None, "real_pnl_d10": None, "real_pnl_d15": None,
           "real_pnl_expiry": None, "real_max_mult": None, "real_min_mult": None,
           "tp30_touch_bar": None, "sl70_touch_bar": None,
           "bars_covered": len(path), "stale_frac": None}
    if not path or entry is None or entry <= 0:
        return out

    by_off = {b["off"]: b for b in path}
    for k in PNL_OFFSETS:
        b = by_off.get(k)
        if b is not None and b.get("c") is not None:
            out[f"real_pnl_d{k}"] = b["c"] / entry - 1.0

    last = path[-1]
    if last.get("c") is not None:
        out["real_pnl_expiry"] = last["c"] / entry - 1.0

    highs = [b["h"] for b in path if b.get("h") is not None]
    lows = [b["l"] for b in path if b.get("l") is not None]
    if highs:
        out["real_max_mult"] = max(highs) / entry
    if lows:
        out["real_min_mult"] = min(lows) / entry

    tp_level = TP_MULT * entry
    sl_level = SL_MULT * entry
    for b in sorted(path, key=lambda x: x["off"]):
        if out["tp30_touch_bar"] is None and b.get("h") is not None and b["h"] >= tp_level:
            out["tp30_touch_bar"] = int(b["off"])            # LIMIT fill -> intraday HIGH
        if out["sl70_touch_bar"] is None and b.get("c") is not None and b["c"] <= sl_level:
            out["sl70_touch_bar"] = int(b["off"])            # EOD forced exit -> daily CLOSE
        if out["tp30_touch_bar"] is not None and out["sl70_touch_bar"] is not None:
            break

    # stale_frac = share of the walk window with NO usable print. Polygon omits a
    # no-trade day entirely rather than emitting a zero-volume bar, so a pure
    # "volume == 0" count would read 0.0 on even the most illiquid contract. We
    # therefore count MISSING market trading days as well as any zero-volume bars.
    n_zero_vol = sum(1 for b in path if not b.get("v"))
    denom = expected if (expected and expected > 0) else len(path)
    n_missing = max(0, denom - len(path))
    out["stale_frac"] = min(1.0, (n_missing + n_zero_vol) / denom) if denom else None
    return out


def classify_path_end(path: list[dict], walk_end: str, expiration: str) -> tuple:
    """PURE. Why did the forward path stop? -> (reason, last_bar_date).

      'empty'         -- no forward bars at all
      'expiry'        -- ran to the contract's expiration (the walk end IS expiry)
      'walk_limit'    -- ran to the signal_date+45cal cap, which came before expiry
      'no_more_bars'  -- the contract simply stopped printing early (illiquidity).
                         THIS is the honest label for a truncated path, and it is
                         what distinguishes "the option expired" from "nobody
                         traded it again" when real_pnl_d10/d15 come back null.

    A path is treated as having reached the end if its last bar is within
    PATH_END_TOLERANCE_DAYS calendar days of the walk end (weekend/holiday slack)."""
    if not path:
        return "empty", None
    last = max(b["d"] for b in path)
    reached = (_dt(walk_end) - _dt(last)).days <= PATH_END_TOLERANCE_DAYS
    if not reached:
        return "no_more_bars", last
    return ("expiry" if walk_end == expiration else "walk_limit"), last


def is_liquid_entry(volume, trades) -> bool:
    """PURE. Entry-bar liquidity flag: the real print must represent an actual,
    non-trivial trade before a real-vs-model premium ratio means anything.
    Threshold: contracts traded >= LIQUID_MIN_VOLUME AND trade count >=
    LIQUID_MIN_TRADES. A single 1-lot print on a wide market is exactly the
    stale-quote artifact that produces 20x ratio outliers."""
    if volume is None or volume < LIQUID_MIN_VOLUME:
        return False
    if trades is None:
        return False
    return trades >= LIQUID_MIN_TRADES


# ---------------------------------------------------------------------------
# Per-signal processing (thread-safe: Polygon HTTP only, no DB).
# ---------------------------------------------------------------------------
def _blank_row(sig: dict, status: str, n_calls: int = 0) -> dict:
    row = {c: None for c in COLUMNS}
    row.update({"symbol": sig["symbol"], "signal_date": sig["signal_date"],
                "overall": sig.get("overall"), "entry_price": sig.get("entry_price"),
                "spot_unadj": sig.get("spot_unadj"), "spot_source": sig.get("spot_source"),
                "adj_factor": sig.get("adj_factor"),
                "vol": sig.get("vol"), "model_premium_pct": sig.get("model_premium_pct"),
                "model_premium_abs": sig.get("model_premium_abs"),
                "status": status, "n_api_calls": n_calls, "bars_covered": 0})
    return row


def process_signal(client, sig: dict, tdi: dict[str, int] | None = None,
                   tdays: list[str] | None = None) -> dict:
    """One (symbol, signal_date) -> one ledger row. Never raises for expected data
    holes; returns a row whose `status` is 'kept' or 'miss:<reason>'.

    `tdi` / `tdays` are the precomputed market trading calendar (see
    build_trading_index) so path offsets are MARKET trading days, not bar indices.
    They are read-only and thread-safe."""
    sym = sig["symbol"]
    d = sig["signal_date"]
    d_date = _dt(d)
    # STRIKE SELECTION ANCHORS ON THE AS-TRADED CLOSE, never the back-adjusted one
    # (see load_unadj_spots for why). Falls back to the adjusted close only when
    # yfinance has no data for the symbol, and that is flagged in spot_source.
    spot = sig.get("spot_unadj") or sig.get("entry_price")
    n_calls = 0

    if spot is None or spot <= 0:
        return _blank_row(sig, "miss:no_underlying", n_calls)

    # Query a WIDER expiry window than the [21,45] selection band -- same single API
    # call -- so an empty result unambiguously means "this symbol had no listed calls
    # at all" (miss:no_chain) rather than being conflated with "no expiry landed in
    # the 21-45 band" (miss:no_dte_band). Names without weeklies routinely have a
    # 21-45 hole: e.g. a 2022-09-01 signal spans 09-22..10-16, which straddles the
    # 09-16 and 10-21 monthlies. The [21,45] band itself is unchanged.
    exp_gte = (d_date + timedelta(days=CHAIN_EXP_LO)).isoformat()
    exp_lte = (d_date + timedelta(days=CHAIN_EXP_HI)).isoformat()
    raw = client.list_option_contracts(sym, as_of=d, exp_gte=exp_gte, exp_lte=exp_lte,
                                       contract_type="call")
    n_calls += 1
    chain = normalize_chain(raw, sym, d_date)
    if not chain:
        return _blank_row(sig, "miss:no_chain", n_calls)

    dte, at_expiry = select_expiry(chain)
    if dte is None:
        return _blank_row(sig, "miss:no_dte_band", n_calls)

    candidates = rank_strikes(at_expiry, spot)
    if not candidates:
        return _blank_row(sig, "miss:no_dte_band", n_calls)

    # A candidate strike is USABLE only if it has an entry-day print AND at least
    # MIN_FORWARD_BARS forward bars -- a contract that printed once and never
    # again carries no path information and must not be emitted as 'kept'. An
    # unusable strike falls through to the next-nearest one (the same
    # "try the next 2 nearest strikes" rule the spec applies to a dry ATM).
    best_partial = None            # (row, status) for the least-bad rejected candidate
    for cand in candidates:
        end_walk = min(cand["expiration_date"], d_date + timedelta(days=WALK_MAX_CAL_DAYS))
        try:
            bars = client.option_daily_bars(cand["ticker"], d, end_walk.isoformat())
            n_calls += 1
        except RuntimeError as exc:
            n_calls += 1
            if _is_timeframe_403(exc):
                _note_tf403(d)
                return _blank_row(sig, "miss:timeframe_403", n_calls)
            raise
        by_date = bars_ohlc_by_date(bars)
        entry_bar = by_date.get(d)
        if entry_bar is None or entry_bar.get("c") is None or entry_bar["c"] <= 0:
            continue                                          # try next-nearest strike
        for bd in by_date:
            _note_bar_date(bd)

        entry_real = float(entry_bar["c"])
        path = build_forward_path(by_date, d, end_walk.isoformat(), tdi)
        expected = expected_forward_bars(tdays or [], d, end_walk.isoformat())
        scal = walk_path(path, entry_real, expected)
        end_reason, end_date = classify_path_end(path, end_walk.isoformat(),
                                                 cand["expiration_date"].isoformat())
        mpa = sig.get("model_premium_abs")

        row = _blank_row(sig, "kept", n_calls)
        row.update({
            "option_ticker": cand["ticker"],
            "expiration_date": cand["expiration_date"].isoformat(),
            "dte_cal": int(cand["dte"]),
            "atm_strike": float(cand["strike"]),
            "moneyness": float(cand["strike"]) / spot,
            "strike_rank": int(cand["strike_rank"]),
            "entry_premium_real": entry_real,          # the CONTRACT CLOSE on signal_date
            "entry_open": entry_bar.get("o"), "entry_high": entry_bar.get("h"),
            "entry_low": entry_bar.get("l"), "entry_vwap": entry_bar.get("vw"),
            "entry_volume": entry_bar.get("v"), "entry_trades": entry_bar.get("n"),
            "liquid_entry": is_liquid_entry(entry_bar.get("v"), entry_bar.get("n")),
            "real_model_ratio": (entry_real / mpa) if (mpa and mpa > 0) else None,
            "path_json": json.dumps(path, separators=(",", ":")),
            "path_end_reason": end_reason, "path_end_date": end_date,
        })
        row.update(scal)

        n_fwd = len(path)
        if n_fwd >= MIN_FORWARD_BARS:
            return row
        status = "miss:no_forward_bars" if n_fwd == 0 else "miss:too_few_bars"
        row["status"] = status
        if best_partial is None or n_fwd > best_partial[1]:
            best_partial = (row, n_fwd)                # keep the richest rejected row
        continue                                       # try next-nearest strike

    if best_partial is not None:
        row = best_partial[0]
        row["n_api_calls"] = n_calls
        return row
    return _blank_row(sig, "miss:no_atm_price", n_calls)


# ---------------------------------------------------------------------------
# Consolidation: journal -> parquet (OFFLINE, no key, no DB).
# ---------------------------------------------------------------------------
def _empty_schema() -> dict:
    sch: dict = {}
    for c in COLUMNS:
        if c in _FLOAT_COLS:
            sch[c] = pl.Float64
        elif c in _INT_COLS:
            sch[c] = pl.Int64
        elif c in _BOOL_COLS:
            sch[c] = pl.Boolean
        else:
            sch[c] = pl.Utf8
    return sch


def consolidate(progress_path: Path = PROGRESS, parquet_path: Path = OUT_PARQUET) -> int:
    if not progress_path.exists():
        log("no progress journal yet; nothing to consolidate")
        return 0
    rows: dict[tuple[str, str], dict] = {}
    for line in progress_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = (r.get("symbol"), r.get("signal_date"))
        if key[0] is None or key[1] is None:
            continue
        rows[key] = r                                   # last write wins (re-runs supersede)

    if rows:
        recs = [{c: r.get(c) for c in COLUMNS} for r in rows.values()]
        # infer_schema_length=None: columns that are None for the first N rows and
        # populated later (very common here -- an early run of misses) otherwise
        # crash the schema inference.
        df = pl.DataFrame(recs, infer_schema_length=None)
        casts = []
        for c in COLUMNS:
            if c in _FLOAT_COLS:
                casts.append(pl.col(c).cast(pl.Float64, strict=False).alias(c))
            elif c in _INT_COLS:
                casts.append(pl.col(c).cast(pl.Int64, strict=False).alias(c))
            elif c in _BOOL_COLS:
                casts.append(pl.col(c).cast(pl.Boolean, strict=False).alias(c))
            else:
                casts.append(pl.col(c).cast(pl.Utf8, strict=False).alias(c))
        df = df.with_columns(casts).select(COLUMNS)
        # NaN is NOT null in polars: one choke point before anything strict reads it.
        df = df.with_columns([pl.col(c).fill_nan(None) for c in _FLOAT_COLS])
    else:
        df = pl.DataFrame(schema=_empty_schema())

    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(parquet_path)
    log(f"consolidated {df.height} rows -> {parquet_path}")
    return df.height


def write_meta(args, version_label: str, universe_n: int, elapsed: float,
               calls: int, parquet_path: Path = OUT_PARQUET,
               meta_path: Path = META_JSON) -> None:
    status_counts: dict[str, int] = {}
    end_counts: dict[str, int] = {}
    spot_counts: dict[str, int] = {}
    n_liquid = 0
    kept_min = None
    dmin = dmax = None
    n_sym = 0
    n_rows = 0
    if parquet_path.exists():
        df = pl.read_parquet(parquet_path)
        n_rows = df.height
        if n_rows:
            status_counts = dict(Counter(df["status"].to_list()))
            end_counts = dict(Counter(x for x in df["path_end_reason"].to_list() if x))
            spot_counts = dict(Counter(x for x in df["spot_source"].to_list() if x))
            n_liquid = int(df["liquid_entry"].fill_null(False).sum())
            dmin = df["signal_date"].min()
            dmax = df["signal_date"].max()
            n_sym = df["symbol"].n_unique()
            kept = df.filter(pl.col("status") == "kept")
            kept_min = kept["signal_date"].min() if kept.height else None
    meta = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scoring_version": version_label,
        "score_min": SCORE_MIN,
        "window_requested": {"start": args.start, "end": args.end},
        "universe_signals": universe_n,
        "ledger_rows": n_rows,
        "rows_by_status": status_counts,
        "rows_by_path_end_reason": end_counts,
        "rows_by_spot_source": spot_counts,
        "rows_liquid_entry": n_liquid,
        "signal_date_range": {"min": dmin, "max": dmax},
        "distinct_symbols": n_sym,
        "total_api_calls_this_run": calls,
        "wall_clock_seconds": round(elapsed, 1),
        "timeframe_403_count": _tf403_state["n"],
        "timeframe_403_signal_dates": len(_tf403_state["dates"]),
        "earliest_successful_bar_date_this_run": _bar_date_state["earliest"],
        "earliest_kept_signal_date": kept_min,
        "contract_rule": {
            "target_dte_cal": TARGET_DTE, "dte_band": [DTE_LO, DTE_HI],
            "strike": "nearest to signal-date underlying close (ATM), tie -> lower",
            "max_strike_rank": MAX_STRIKE_RANK,
            "walk_end": "min(expiration, signal_date + %dcal)" % WALK_MAX_CAL_DAYS,
        },
        "spot_anchor": ("strike selection anchors on the AS-TRADED close "
                        "(yfinance auto_adjust=False x forward split factors); "
                        "price_history.close is back-adjusted and is NOT in the "
                        "strike price space -- see DESIGN.md section 2"),
        "path_offsets": "market trading days since signal_date (not bar indices)",
        "liquidity_rule": f"liquid_entry = entry_volume >= {LIQUID_MIN_VOLUME} "
                          f"AND entry_trades >= {LIQUID_MIN_TRADES}",
        "min_forward_bars_for_kept": MIN_FORWARD_BARS,
        "touch_conventions": {
            "tp30_touch_bar": "first forward offset with intraday HIGH >= %.2f x entry (limit fill)" % TP_MULT,
            "sl70_touch_bar": "first forward offset with daily CLOSE <= %.2f x entry (EOD forced exit)" % SL_MULT,
        },
        "source": "polygon_developer_daily_aggregates",
        "invariant": "vendor data written to .cache only; never MySQL",
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    with open(meta_path, "w", encoding="ascii", errors="backslashreplace") as f:
        json.dump(meta, f, indent=2, default=str)
    log(f"wrote {meta_path}")


# ---------------------------------------------------------------------------
# Live pull driver.
# ---------------------------------------------------------------------------
def run_pull(args) -> int:
    require_key()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    symbols = {s.strip().upper() for s in args.symbols.split(",") if s.strip()} or None

    # ---- SINGLE-THREADED DB PHASE (PyMySQL is not thread-safe) ----
    log("loading signals from MySQL (single-threaded)...")
    universe, version_label = load_signals(args.start, args.end, symbols)
    log(f"universe={len(universe)} 75+ CALL signals under {version_label} "
        f"[{args.start}..{args.end}]")
    done = load_done()
    todo = [r for r in universe if (r["symbol"], r["signal_date"]) not in done]
    if args.limit:
        todo = todo[:args.limit]
    log(f"done={len(done)} todo={len(todo)} workers={args.workers}")
    if not todo:
        n = consolidate()
        write_meta(args, version_label, len(universe), 0.0, 0)
        log(f"nothing to do; parquet has {n} rows")
        return 0

    client = PolygonClient(sleep_seconds=rate_sleep_seconds(default=0.0))
    from polygon_iv_ingest import Closes                # reuse; MySQL + Polygon fallback
    closes = Closes(client)
    syms = sorted({r["symbol"] for r in todo})
    log(f"preloading underlying closes for {len(syms)} symbols (single-threaded)...")
    closes.preload(syms)
    unadj = load_unadj_spots(syms, args.start, args.end, refresh=args.refresh_spot)
    enrich_signals(todo, closes, unadj)
    n_no_spot = sum(1 for s in todo if not s.get("entry_price"))
    n_no_vol = sum(1 for s in todo if s.get("vol") is None)
    n_fallback = sum(1 for s in todo if s.get("spot_source") == "ph_adjusted")
    log(f"enriched: missing entry_price={n_no_spot} missing vol={n_no_vol} "
        f"spot_source=ph_adjusted(fallback)={n_fallback}")
    tdays, tdi = build_trading_index(closes)
    log(f"market trading calendar: {len(tdays)} days "
        f"[{tdays[0] if tdays else '-'}..{tdays[-1] if tdays else '-'}]")
    # ---- END DB PHASE. Worker threads below do Polygon HTTP only. ----

    t0 = time.time()
    ctr = {"n": 0, "kept": 0}
    log_fh = PROGRESS.open("a", encoding="utf-8")
    log_lock = threading.Lock()

    def record(row: dict) -> None:
        with log_lock:
            log_fh.write(json.dumps(row, default=str) + "\n")
            log_fh.flush()
            ctr["n"] += 1
            if row.get("status") == "kept":
                ctr["kept"] += 1
            if ctr["n"] % args.flush_every == 0:
                consolidate()
                rate = ctr["n"] / max(time.time() - t0, 1e-6)
                log(f"  {ctr['n']}/{len(todo)} kept={ctr['kept']} calls={client.calls} "
                    f"{rate:.2f}/s")

    def run_one(sig: dict) -> None:
        try:
            row = process_signal(client, sig, tdi, tdays)
        except Exception as exc:
            row = _blank_row(sig, f"miss:err:{repr(exc)[:80]}")
        record(row)

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(run_one, todo))
    else:
        for sig in todo:
            run_one(sig)
    log_fh.close()

    n = consolidate()
    elapsed = time.time() - t0
    write_meta(args, version_label, len(universe), elapsed, client.calls)
    log(f"DONE: processed={ctr['n']} kept={ctr['kept']} api_calls={client.calls} "
        f"in {elapsed:.0f}s -> {OUT_PARQUET} (total rows={n})")
    if _tf403_state["n"]:
        log(f"  note: {_tf403_state['n']} signals missed as timeframe-403 across "
            f"{len(_tf403_state['dates'])} signal dates (outside the ~4yr aggregates window).")
    return 0


# ===========================================================================
# OFFLINE SELF-TESTS (no key, no network, no DB).
# ===========================================================================
def _bar(off, h, l, c, v=100, o=None, d=None):
    return {"off": off, "d": d or f"2022-09-{off+1:02d}", "o": o if o is not None else c,
            "h": h, "l": l, "c": c, "v": v, "n": 10}


def selftest() -> int:
    log("=== polygon_real_premium/pull.py OFFLINE SELF-TESTS ===")

    # -- 1. walk_path: empty path --------------------------------------------
    r = walk_path([], 1.0)
    assert r["bars_covered"] == 0, r
    assert r["real_pnl_expiry"] is None and r["real_max_mult"] is None, r
    assert r["tp30_touch_bar"] is None and r["sl70_touch_bar"] is None, r
    assert r["stale_frac"] is None, r
    # degenerate entry
    assert walk_path([_bar(1, 2, 1, 1.5)], 0.0)["real_max_mult"] is None
    assert walk_path([_bar(1, 2, 1, 1.5)], None)["real_pnl_expiry"] is None
    log("  [1] empty path / degenerate entry OK")

    # -- 2. walk_path: single bar --------------------------------------------
    r = walk_path([_bar(1, 1.20, 0.80, 1.10)], 1.00)
    assert r["bars_covered"] == 1, r
    assert abs(r["real_pnl_expiry"] - 0.10) < 1e-12, r
    assert abs(r["real_max_mult"] - 1.20) < 1e-12, r
    assert abs(r["real_min_mult"] - 0.80) < 1e-12, r
    assert r["real_pnl_d5"] is None and r["real_pnl_d10"] is None and r["real_pnl_d15"] is None, r
    assert r["tp30_touch_bar"] is None and r["sl70_touch_bar"] is None, r
    assert r["stale_frac"] == 0.0, r
    log("  [2] single bar OK")

    # -- 3. d5/d10/d15 + expiry pnl on a known path ---------------------------
    entry = 2.00
    path = []
    for k in range(1, 17):
        c = 2.00 + 0.10 * k                     # d5=2.50, d10=3.00, d15=3.50, last(16)=3.60
        path.append(_bar(k, c, c, c, v=(0 if k in (3, 7) else 50)))
    r = walk_path(path, entry)
    assert abs(r["real_pnl_d5"] - (2.50 / 2.00 - 1)) < 1e-12, r
    assert abs(r["real_pnl_d10"] - (3.00 / 2.00 - 1)) < 1e-12, r
    assert abs(r["real_pnl_d15"] - (3.50 / 2.00 - 1)) < 1e-12, r
    assert abs(r["real_pnl_expiry"] - (3.60 / 2.00 - 1)) < 1e-12, r
    assert abs(r["real_max_mult"] - 3.60 / 2.00) < 1e-12, r
    assert abs(r["real_min_mult"] - 2.10 / 2.00) < 1e-12, r
    # TP 1.30x2.00 = 2.60 -> first bar with high >= 2.60 is off=6 (c=2.60)
    assert r["tp30_touch_bar"] == 6, r
    assert r["sl70_touch_bar"] is None, r
    assert abs(r["stale_frac"] - 2 / 16) < 1e-12, r
    log("  [3] d5/d10/d15/expiry + max/min mult + stale_frac OK")

    # -- 4. TP touched on the ENTRY BAR itself (off=0) ------------------------
    # entry 1.00; the entry bar's own high already printed 1.50 (>= 1.30).
    p0 = [_bar(0, 1.50, 0.95, 1.00), _bar(1, 1.05, 0.90, 1.00)]
    r = walk_path(p0, 1.00)
    assert r["tp30_touch_bar"] == 0, r
    # and with a strictly-forward path (production shape) the same data does NOT
    # report a same-bar touch:
    r_fwd = walk_path([_bar(1, 1.05, 0.90, 1.00)], 1.00)
    assert r_fwd["tp30_touch_bar"] is None, r_fwd
    # SL on the entry bar too
    r = walk_path([_bar(0, 1.00, 0.20, 0.25)], 1.00)
    assert r["sl70_touch_bar"] == 0, r
    log("  [4] entry-bar (off=0) TP/SL touch OK; forward-only path unaffected")

    # -- 5. TP-first vs SL-first ordering -------------------------------------
    # TP first: off=1 high 1.40 (TP), off=3 close 0.20 (SL)
    p = [_bar(1, 1.40, 0.95, 1.10), _bar(2, 1.10, 0.90, 1.00), _bar(3, 1.00, 0.15, 0.20)]
    r = walk_path(p, 1.00)
    assert r["tp30_touch_bar"] == 1 and r["sl70_touch_bar"] == 3, r
    assert r["tp30_touch_bar"] < r["sl70_touch_bar"], r
    # SL first: off=1 close 0.20 (SL), off=3 high 1.40 (TP)
    p = [_bar(1, 0.90, 0.15, 0.20), _bar(2, 0.50, 0.20, 0.30), _bar(3, 1.40, 0.30, 1.35)]
    r = walk_path(p, 1.00)
    assert r["sl70_touch_bar"] == 1 and r["tp30_touch_bar"] == 3, r
    assert r["sl70_touch_bar"] < r["tp30_touch_bar"], r
    # exact-boundary touches count (>= / <=)
    assert walk_path([_bar(1, 1.30, 1.00, 1.20)], 1.00)["tp30_touch_bar"] == 1
    assert walk_path([_bar(1, 1.00, 0.10, 0.30)], 1.00)["sl70_touch_bar"] == 1
    # a HIGH that dips to the SL level but CLOSES above it is NOT a stop-out
    # (the deliberate close-not-low convention)
    assert walk_path([_bar(1, 1.00, 0.10, 0.90)], 1.00)["sl70_touch_bar"] is None
    log("  [5] TP-first / SL-first ordering + boundary + close-not-low convention OK")

    # -- 6. contract selection -------------------------------------------------
    d0 = date(2022, 9, 1)

    def mkraw(strike, dte, sym="FAKE", spc=100, tick=None):
        exp = d0 + timedelta(days=dte)
        return {"ticker": tick or f"O:{sym}{exp.strftime('%y%m%d')}C{int(strike*1000):08d}",
                "contract_type": "call", "strike_price": float(strike),
                "expiration_date": exp.isoformat(), "shares_per_contract": spc}

    raw = ([mkraw(k, 28) for k in (90, 95, 100, 105, 110)]
           + [mkraw(k, 35) for k in (95, 100, 105)]
           + [mkraw(100, 14)]                     # out of band (DTE 14)
           + [mkraw(100, 60)]                     # out of band (DTE 60)
           + [{"ticker": "O:FAKE220930P00100000", "contract_type": "put",
               "strike_price": 100.0, "expiration_date": "2022-09-29",
               "shares_per_contract": 100}]       # puts dropped
           + [mkraw(100, 28, spc=10, tick="O:FAKE1220929C00100000")])  # adjusted -> dropped
    chain = normalize_chain(raw, "FAKE", d0)
    assert all(c["dte"] in (14, 28, 35, 60) for c in chain), chain
    assert not any("FAKE1" in c["ticker"] for c in chain), chain
    dte, at_exp = select_expiry(chain)
    assert dte == 28, dte                          # |28-30|=2 beats |35-30|=5
    assert len(at_exp) == 5, at_exp
    # spot 101 -> |100-101|=1 < |105-101|=4 < |95-101|=6 < |110-101|=9
    ranked = rank_strikes(at_exp, 101.0)
    assert [c["strike"] for c in ranked] == [100.0, 105.0, 95.0], ranked
    assert [c["strike_rank"] for c in ranked] == [0, 1, 2], ranked
    # exact tie -> lower strike wins rank 0
    ranked = rank_strikes(at_exp, 102.5)
    assert ranked[0]["strike"] == 100.0 and ranked[1]["strike"] == 105.0, ranked
    # no expiry in the band
    dte, at_exp = select_expiry(normalize_chain([mkraw(100, 5), mkraw(100, 90)], "FAKE", d0))
    assert dte is None and at_exp == [], (dte, at_exp)
    # tie on DTE distance -> shorter DTE (25 vs 35, both |d-30|=5)
    dte, _ = select_expiry(normalize_chain([mkraw(100, 25), mkraw(100, 35)], "FAKE", d0))
    assert dte == 25, dte
    log("  [6] contract selection (expiry band / ATM rank / adjusted-contract drop) OK")

    # -- 7. bars_ohlc_by_date + forward path ----------------------------------
    ms = int((datetime(2022, 9, 1) - datetime(1970, 1, 1)).total_seconds() * 1000)
    day = 86_400_000
    bars = [{"t": ms + i * day, "o": 1.0, "h": 1.2, "l": 0.9, "c": 1.0 + 0.1 * i,
             "v": 10 * i, "vw": 1.05, "n": 3} for i in range(4)]
    by_date = bars_ohlc_by_date(bars)
    assert "2022-09-01" in by_date and by_date["2022-09-01"]["h"] == 1.2, by_date
    assert by_date["2022-09-01"]["n"] == 3.0, by_date
    fwd = build_forward_path(by_date, "2022-09-01", "2022-09-03")
    assert [b["off"] for b in fwd] == [1, 2], fwd
    assert [b["d"] for b in fwd] == ["2022-09-02", "2022-09-03"], fwd
    assert fwd[0]["v"] == 10.0, fwd
    log("  [7] bars_ohlc_by_date + build_forward_path OK")

    # -- 7b. MARKET-trading-day offsets + expected_forward_bars + stale_frac ----
    cal = ["2022-09-01", "2022-09-02", "2022-09-06", "2022-09-07", "2022-09-08",
           "2022-09-09", "2022-09-12"]              # Sep 5 = Labor Day, weekends out
    tdi_t = {d: i for i, d in enumerate(cal)}
    # the contract printed on 09-02 and 09-08 only (no print 09-06/07)
    bd = {"2022-09-01": {"c": 1.0, "h": 1.0, "l": 1.0, "o": 1.0, "v": 5, "n": 2},
          "2022-09-02": {"c": 1.1, "h": 1.1, "l": 1.1, "o": 1.1, "v": 5, "n": 2},
          "2022-09-08": {"c": 1.4, "h": 1.4, "l": 1.4, "o": 1.4, "v": 5, "n": 2}}
    p = build_forward_path(bd, "2022-09-01", "2022-09-12", tdi_t)
    assert [b["off"] for b in p] == [1, 4], [b["off"] for b in p]   # NOT [1, 2]
    # without a calendar we fall back to the bar sequence
    assert [b["off"] for b in build_forward_path(bd, "2022-09-01", "2022-09-12")] == [1, 2]
    assert expected_forward_bars(cal, "2022-09-01", "2022-09-12") == 6
    assert expected_forward_bars(cal, "2022-09-01", "2022-09-30") == 6   # capped at calendar end
    assert expected_forward_bars(cal, "2022-09-12", "2022-09-30") == 0
    assert expected_forward_bars([], "2022-09-01", "2022-09-30") == 0
    r = walk_path(p, 1.0, expected=6)
    assert r["bars_covered"] == 2 and abs(r["stale_frac"] - 4 / 6) < 1e-12, r
    # ... and with no `expected` the denominator degrades to the bar count
    assert walk_path(p, 1.0)["stale_frac"] == 0.0

    class _FakeCloses:
        _by_symbol = {"A": {"2022-09-01": 1.0, "2022-09-02": 1.0},
                      "B": {"2022-09-02": 1.0, "2022-09-06": 1.0}}
    days, idx = build_trading_index(_FakeCloses())
    assert days == ["2022-09-01", "2022-09-02", "2022-09-06"], days
    assert idx["2022-09-06"] == 2, idx
    log("  [7b] market-trading-day offsets + expected_forward_bars + stale_frac OK")

    # -- 8. realized_vol matches monte_carlo's formula -------------------------
    closes = [100.0 * (1.01 ** i) for i in range(120)]        # constant 1%/day -> zero variance
    v = realized_vol(closes, 100, lookback=60)
    assert v is not None and v < 1e-9, v
    assert realized_vol(closes, 10, lookback=60) is None       # underfed
    closes2 = list(closes)
    closes2[95] = closes2[95] * 1.5                            # inject a shock
    assert realized_vol(closes2, 100, lookback=60) > 1.0
    log("  [8] realized_vol OK")

    # -- 8b. as-traded spot reconstruction (forward split un-adjustment) -------
    ds = ["2022-08-23", "2023-01-03", "2024-06-11", "2026-07-01"]
    cs = [17.18, 20.0, 120.0, 160.0]
    # NVDA-shaped: a 10:1 split on 2024-06-10 -> every close BEFORE it is x10
    got = apply_forward_splits(ds, cs, [("2024-06-10", 10.0)])
    assert [round(x, 2) for x in got] == [171.8, 200.0, 120.0, 160.0], got
    # MMM-shaped spinoff recorded as ratio 1.196
    assert round(apply_forward_splits(["2022-08-23"], [118.52], [("2024-04-01", 1.196)])[0], 2) == 141.75
    # AZN-shaped reverse ratio 0.5
    assert round(apply_forward_splits(["2022-08-12"], [133.28], [("2026-02-02", 0.5)])[0], 2) == 66.64
    # no splits / zero-ratio noise -> identity
    assert apply_forward_splits(ds, cs, []) == cs
    assert apply_forward_splits(ds, cs, [("2024-06-10", 0.0)]) == cs
    # multiple splits compound
    assert round(apply_forward_splits(["2020-01-02"], [10.0],
                                      [("2021-07-20", 4.0), ("2024-06-10", 10.0)])[0], 2) == 400.0
    log("  [8b] apply_forward_splits (as-traded spot reconstruction) OK")

    # -- 8c. path-end classifier + liquidity flag ------------------------------
    p = [{"d": "2022-09-20", "off": 1}, {"d": "2022-09-23", "off": 2}]
    assert classify_path_end(p, "2022-09-23", "2022-09-23") == ("expiry", "2022-09-23")
    assert classify_path_end(p, "2022-09-30", "2022-09-30")[0] == "no_more_bars"
    # walk capped before expiry -> walk_limit
    assert classify_path_end(p, "2022-09-23", "2022-10-21")[0] == "walk_limit"
    # weekend slack: last bar Fri, walk end Sun-dated expiry 2 days later
    assert classify_path_end([{"d": "2022-09-23", "off": 1}], "2022-09-25",
                             "2022-09-25")[0] == "expiry"
    assert classify_path_end([], "2022-09-23", "2022-09-23") == ("empty", None)
    assert is_liquid_entry(250, 40) is True
    assert is_liquid_entry(4, 40) is False          # below the volume floor
    assert is_liquid_entry(250, 0) is False         # zero prints
    assert is_liquid_entry(None, 5) is False and is_liquid_entry(250, None) is False
    assert is_liquid_entry(5, 1) is True            # exact boundary
    log("  [8c] classify_path_end + is_liquid_entry OK")

    # -- 9. timeframe-403 classifier -------------------------------------------
    assert _is_timeframe_403(RuntimeError(
        'Polygon 403: https://... body={"status":"NOT_AUTHORIZED","message":'
        '"Your plan doesn\'t include this data timeframe."}'))
    assert not _is_timeframe_403(RuntimeError("Polygon 404: not found"))
    assert not _is_timeframe_403(RuntimeError("Polygon 403: some OTHER forbidden reason"))
    log("  [9] timeframe-403 classifier OK")

    # -- 10. process_signal end-to-end against a synthetic OFFLINE client -----
    class FakeClient:
        calls = 0

        def __init__(self, dry_strikes=(), raise_403=False):
            self.dry = set(dry_strikes)
            self.raise_403 = raise_403

        def list_option_contracts(self, sym, as_of, exp_gte, exp_lte, contract_type=None, **kw):
            self.calls += 1
            base = _dt(as_of)
            out = []
            for dte in (28, 35):
                exp = base + timedelta(days=dte)
                for k in (90, 95, 100, 105, 110):
                    out.append({"ticker": f"O:{sym}{exp.strftime('%y%m%d')}C{int(k*1000):08d}",
                                "contract_type": "call", "strike_price": float(k),
                                "expiration_date": exp.isoformat(), "shares_per_contract": 100})
            return out

        def __init_nbars__(self):
            pass

        nbars_by_strike: dict = {}
        default_nbars = 20

        def option_daily_bars(self, ticker, start, end):
            self.calls += 1
            if self.raise_403:
                raise RuntimeError('Polygon 403: body={"status":"NOT_AUTHORIZED",'
                                   '"message":"Your plan doesn\'t include this data timeframe."}')
            strike = int(ticker.split("C")[-1]) / 1000.0
            if strike in self.dry:
                return []
            n = self.nbars_by_strike.get(strike, self.default_nbars)
            base = _dt(start)
            rows = []
            px = 3.00
            for i in range(0, n):
                dd = base + timedelta(days=i)
                if i:
                    px *= 1.03
                t = int((datetime(dd.year, dd.month, dd.day) - datetime(1970, 1, 1)).total_seconds() * 1000)
                rows.append({"t": t, "o": px, "h": px * 1.05, "l": px * 0.96,
                             "c": px, "v": 250, "vw": px, "n": 40})
            return rows

    sig = {"symbol": "FAKE", "signal_date": "2022-09-01", "overall": 82,
           "entry_price": 101.0, "spot_unadj": 101.0, "spot_source": "yf_unadj",
           "adj_factor": 1.0, "vol": 2.5, "model_premium_pct": 0.0455,
           "model_premium_abs": 0.0455 * 101.0}
    row = process_signal(FakeClient(), sig)
    assert row["status"] == "kept", row["status"]
    assert row["liquid_entry"] is True, row["liquid_entry"]
    assert abs(row["real_model_ratio"] - 3.00 / (0.0455 * 101.0)) < 1e-9, row["real_model_ratio"]
    # 20 bars from the entry date, expiry 28 cal days out -> path stops early
    assert row["path_end_reason"] == "no_more_bars", row["path_end_reason"]
    assert row["path_end_date"] == "2022-09-20", row["path_end_date"]
    assert row["strike_rank"] == 0 and row["atm_strike"] == 100.0, row
    assert row["dte_cal"] == 28, row
    assert abs(row["moneyness"] - 100.0 / 101.0) < 1e-12, row
    assert abs(row["entry_premium_real"] - 3.00) < 1e-9, row
    assert row["bars_covered"] == 19, row["bars_covered"]
    assert row["tp30_touch_bar"] is not None and row["sl70_touch_bar"] is None, row
    assert row["real_pnl_d5"] is not None and row["real_pnl_d15"] is not None, row
    assert row["n_api_calls"] == 2, row["n_api_calls"]
    pj = json.loads(row["path_json"])
    assert len(pj) == 19 and pj[0]["off"] == 1, pj[:1]
    assert set(pj[0]) >= {"d", "o", "h", "l", "c", "v", "n", "off"}, pj[0]
    for c in COLUMNS:
        assert c in row, f"missing column {c}"
    # strike fallback: ATM (100) dry -> rank 1 (105) used
    row2 = process_signal(FakeClient(dry_strikes=(100.0,)), sig)
    assert row2["status"] == "kept" and row2["strike_rank"] == 1 and row2["atm_strike"] == 105.0, row2
    assert row2["n_api_calls"] == 3, row2["n_api_calls"]
    # all three dry -> miss:no_atm_price, exactly 3 bar attempts
    row3 = process_signal(FakeClient(dry_strikes=(100.0, 105.0, 95.0)), sig)
    assert row3["status"] == "miss:no_atm_price", row3["status"]
    assert row3["n_api_calls"] == 4, row3["n_api_calls"]
    # 403 -> coverage miss, no crash
    _tf403_state["logged"] = True
    row4 = process_signal(FakeClient(raise_403=True), sig)
    assert row4["status"] == "miss:timeframe_403", row4["status"]
    # no underlying -> miss, zero API calls
    row5 = process_signal(FakeClient(), {"symbol": "FAKE", "signal_date": "2022-09-01",
                                         "overall": 80, "entry_price": None})
    assert row5["status"] == "miss:no_underlying" and row5["n_api_calls"] == 0, row5
    # no expiry in the DTE band
    class NoBandClient(FakeClient):
        def list_option_contracts(self, sym, as_of, exp_gte, exp_lte, contract_type=None, **kw):
            self.calls += 1
            base = _dt(as_of)
            exp = base + timedelta(days=90)
            return [{"ticker": f"O:{sym}{exp.strftime('%y%m%d')}C00100000",
                     "contract_type": "call", "strike_price": 100.0,
                     "expiration_date": exp.isoformat(), "shares_per_contract": 100}]
    # chain has contracts, but none in the 21-45 DTE band -> no_dte_band (NOT no_chain)
    assert process_signal(NoBandClient(), sig)["status"] == "miss:no_dte_band"

    class NoChainClient(FakeClient):
        def list_option_contracts(self, sym, as_of, exp_gte, exp_lte, contract_type=None, **kw):
            self.calls += 1
            return []
    assert process_signal(NoChainClient(), sig)["status"] == "miss:no_chain"

    # short-path reclassification: entry print but no forward bars at all
    c0 = FakeClient(); c0.default_nbars = 1
    r0 = process_signal(c0, sig)
    assert r0["status"] == "miss:no_forward_bars" and r0["bars_covered"] == 0, r0["status"]
    assert r0["path_end_reason"] == "empty", r0["path_end_reason"]
    assert r0["n_api_calls"] == 4, r0["n_api_calls"]        # all 3 strikes attempted
    # exactly 1 forward bar is still below MIN_FORWARD_BARS
    c1 = FakeClient(); c1.default_nbars = 2
    r1 = process_signal(c1, sig)
    assert r1["status"] == "miss:too_few_bars" and r1["bars_covered"] == 1, r1["status"]
    # a thin ATM strike falls through to the next-nearest strike with a real path
    c2 = FakeClient(); c2.nbars_by_strike = {100.0: 1}
    r2 = process_signal(c2, sig)
    assert r2["status"] == "kept" and r2["strike_rank"] == 1 and r2["atm_strike"] == 105.0, r2
    # 2 forward bars is the accept boundary
    c3 = FakeClient(); c3.default_nbars = 3
    assert process_signal(c3, sig)["status"] == "kept"
    # walk_limit: expiry beyond signal+45cal cannot happen under DTE_HI=45, but the
    # classifier still labels it correctly (covered in [8c]).
    log("  [10] process_signal offline OK (kept / strike-fallback / short-path "
        "reclassification / 403 / no-band / no-spot)")

    # -- 11. consolidate round-trip incl the None-early / populated-late shape --
    import tempfile
    import shutil
    tmp = Path(tempfile.mkdtemp(prefix="realprem_selftest_"))
    try:
        prog = tmp / "_p.jsonl"
        parq = tmp / "out.parquet"
        lines = []
        # 150 all-None miss rows FIRST (this is exactly the shape that breaks
        # polars schema inference without infer_schema_length=None)
        for i in range(150):
            lines.append(json.dumps(_blank_row(
                {"symbol": f"M{i}", "signal_date": "2022-09-01", "overall": 76,
                 "entry_price": None, "vol": None, "model_premium_pct": None},
                "miss:no_underlying")))
        lines.append(json.dumps(row, default=str))
        row2j = dict(row2)
        row2j["signal_date"] = "2022-09-02"      # distinct key from `row`
        lines.append(json.dumps(row2j, default=str))
        # duplicate key -> last write wins
        dup = dict(row)
        dup["overall"] = 99
        lines.append(json.dumps(dup, default=str))
        prog.write_text("\n".join(lines) + "\n", encoding="utf-8")
        n = consolidate(prog, parq)
        assert n == 152, n                       # 150 misses + row2 + deduped row
        df = pl.read_parquet(parq)
        assert df.columns == COLUMNS, df.columns
        assert df.filter((pl.col("symbol") == "FAKE") &
                         (pl.col("signal_date") == "2022-09-01"))["overall"].to_list() == [99], \
            "dedupe (last write wins) failed"
        assert df["entry_premium_real"].null_count() == 150, df["entry_premium_real"].null_count()
        assert df.filter(pl.col("status") == "kept").height == 2
        # NaN-not-null guard: an explicit NaN in the journal must land as null
        prog2 = tmp / "_p2.jsonl"
        nanrow = dict(row)
        nanrow["symbol"] = "NANX"
        nanrow["real_pnl_d5"] = float("nan")
        prog2.write_text(json.dumps(nanrow, default=str) + "\n", encoding="utf-8")
        df2 = pl.read_parquet(consolidate(prog2, tmp / "out2.parquet") and (tmp / "out2.parquet"))
        assert df2["real_pnl_d5"].null_count() == 1, df2["real_pnl_d5"].to_list()
        # empty journal -> empty typed frame
        prog3 = tmp / "_p3.jsonl"
        prog3.write_text("", encoding="utf-8")
        assert consolidate(prog3, tmp / "out3.parquet") == 0
        assert pl.read_parquet(tmp / "out3.parquet").columns == COLUMNS
        log(f"  [11] consolidate round-trip OK ({df.height} rows, dedupe + NaN->null + empty)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    log("=== SELFTEST PASS ===")
    return 0


# ---------------------------------------------------------------------------
# main / CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Real Polygon option-premium ledger for funded 75+ CALL signals")
    ap.add_argument("--start", default=DEFAULT_START,
                    help=f"signal window start (default {DEFAULT_START}; Polygon Developer "
                         f"aggregates are ~4yr rolling, so earlier dates 403 on bars and are "
                         f"counted as miss:timeframe_403)")
    ap.add_argument("--end", default=date.today().isoformat(), help="signal window end (default today)")
    ap.add_argument("--symbols", default="", help="comma-separated subset filter")
    ap.add_argument("--workers", type=int, default=8, help="concurrent Polygon fetchers")
    ap.add_argument("--flush-every", type=int, default=200, help="consolidate every N signals")
    ap.add_argument("--limit", type=int, default=0, help="process only the first N signals (smoke)")
    ap.add_argument("--refresh-spot", action="store_true",
                    help="re-fetch the as-traded (unadjusted) underlying close cache from "
                         "yfinance instead of reusing .cache/polygon_real_premium/_unadj_spot.parquet")
    ap.add_argument("--consolidate-only", action="store_true",
                    help="rebuild the parquet from the journal and exit (offline: no key, no DB)")
    ap.add_argument("--selftest", action="store_true",
                    help="run offline unit tests and exit (no key, no network, no DB)")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.consolidate_only:
        consolidate()
        return 0
    return run_pull(args)


if __name__ == "__main__":
    raise SystemExit(main())
