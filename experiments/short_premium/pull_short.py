"""Real Polygon option-contract price ledger for the SHORT-PREMIUM study
(naked short puts/calls + poor-man's-covered-call), per PREREGISTRATION.md.

WHAT THIS IS
------------
For three signal populations under v74 (2022-08-01 .. today) --
  BULL  = overall >= 75                          (~3,904 rows)
  BEAR  = overall <= 30, stratified sample        (cap 85/month, seed 42)
  CTRL  = 40 <= overall <= 60, date-matched to BULL's monthly mix (2,000 rows)
-- pull the ACTUAL traded daily OHLC path of the contract each of the six
study arms (bull_put, bear_call, pmcc_long, pmcc_short, ctrl_put, ctrl_call)
would have sold/bought, across a moneyness x DTE-band grid, and write:

  .cache/short_premium/contracts.parquet  -- one row per attempted contract
      (study_arm, moneyness, dte_band, symbol, signal_date)
  .cache/short_premium/paths.parquet      -- raw forward daily bars per contract
      (entry bar at off=0 included; exit-policy grids are applied POST-HOC by
      the downstream ledger engine, not here)
  .cache/short_premium/_unadj_daily.parquet -- FULL as-traded daily close
      series (symbol, date, spot_unadj, spot_source) for every symbol that
      appears in any signal set, 2022-07-01 .. today. Needed downstream for
      expiry settlement and margin marking.
  .cache/short_premium/bear_monthly_counts.json -- raw (pre-cap) BEAR pool
      counts per calendar month, for later reweighting.

This is REAL PRINTS, not a model. Model-premium reference columns
(model_premium_pct/abs) use the SAME realized-vol formula the engine uses,
scaled by sqrt(dte_cal/30) since bands span 12/30/60/270 calendar days (the
reference pull in experiments/polygon_real_premium/ is fixed ~30 DTE ATM
calls only, so it never needed the sqrt scale).

CONVENTIONS INHERITED VERBATIM from experiments/polygon_real_premium/DESIGN.md
(read that file first if this docstring is not enough):
  * as_of chain lookup ALONE, never combined with expired=true.
  * strike/moneyness/settlement anchor on spot_unadj (the as-traded close),
    NEVER price_history.close (G51 spot trap) -- entry_price stays adjusted.
  * adjusted-contract hygiene: shares_per_contract != 100 or a non-standard
    OCC root are dropped; dedupe on (expiration, strike, type).
  * path offsets are MARKET TRADING DAYS since signal_date (calendar built
    from the union of price_history dates), not bar-sequence indices.
  * "usable" = entry-day print + >= MIN_FORWARD_BARS forward bars (2 for
    every arm except pmcc_long, which needs only 1 -- LEAPS print sparsely).
  * resumable JSONL journal; consolidation is last-write-wins; --consolidate-
    only rebuilds parquet from the journal with no key/network/DB.
  * NO MySQL writes, ever. Vendor data lands only under .cache/short_premium/.
  * PyMySQL is not thread-safe: ALL DB reads happen single-threaded before
    the thread pool starts. Worker threads do Polygon HTTP only.

DESIGN CHOICE (not a schema deviation): chain fetches are shared across arms
that need the SAME (signal_set, option type) rather than fetched once per
arm/band/moneyness. Task groups:
  BULL_calls -> serves pmcc_long + pmcc_short (BULL signals, calls)
  BULL_puts  -> serves bull_put                (BULL signals, puts)
  BEAR_calls -> serves bear_call                (BEAR signals, calls)
  CTRL_both  -> serves ctrl_put + ctrl_call     (CTRL signals, both types,
                                                  one un-filtered chain call)
This cuts chain-endpoint calls roughly in half vs one-chain-per-arm while
producing byte-identical selection results (the wider query window is a
superset of every band it serves; select_expiry/rank_strikes then narrow
per (arm, band, moneyness) exactly as the locked selection rule specifies).

Reused, unmodified, from experiments/polygon_real_premium/pull.py (proven,
offline-tested pure helpers -- see that module's own selftest):
  select_expiry, rank_strikes, bars_ohlc_by_date, build_trading_index,
  expected_forward_bars, apply_forward_splits, classify_path_end,
  is_liquid_entry, realized_vol, _is_timeframe_403, _dt, _f

Usage
-----
    python experiments/short_premium/pull_short.py --selftest
    python experiments/short_premium/pull_short.py --limit 15 --workers 4
    python experiments/short_premium/pull_short.py --workers 8      # QUEUE THIS
    python experiments/short_premium/pull_short.py --consolidate-only
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import threading
import time
import warnings
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]                     # short_premium -> experiments -> repo root
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))           # pin repo root FIRST (worktree PYTHONPATH trap)
_DATA_INGEST = _ROOT / "experiments" / "data_ingest"
if str(_DATA_INGEST) not in sys.path:
    sys.path.insert(0, str(_DATA_INGEST))
_REAL_PREMIUM = _ROOT / "experiments" / "polygon_real_premium"
if str(_REAL_PREMIUM) not in sys.path:
    sys.path.insert(0, str(_REAL_PREMIUM))

import polars as pl                          # noqa: E402

from polygon_client import PolygonClient, rate_sleep_seconds   # noqa: E402
# Reuse proven, offline-tested pure helpers from the reference pull -- see
# module docstring "DESIGN CHOICE" above.
from pull import (                            # noqa: E402
    select_expiry, rank_strikes, bars_ohlc_by_date, build_trading_index,
    expected_forward_bars, apply_forward_splits, classify_path_end,
    is_liquid_entry, realized_vol, _is_timeframe_403, _dt, _f,
)

print(f"[pull_short] module file: {__file__}", flush=True)

# ---------------------------------------------------------------------------
# Locked spec constants (PREREGISTRATION.md).
# ---------------------------------------------------------------------------
VERSION_ID = 74                # locked signal-source scoring version (not "active" --
                               # reproducibility for this study means v74 specifically)
BULL_MIN = 75
BEAR_MAX = 30
CTRL_LO, CTRL_HI = 40, 60
DEFAULT_START = "2022-08-01"

BEAR_MONTH_CAP = 85
BEAR_SEED = 42
CTRL_TOTAL = 2000
CTRL_SEED = 42

MAX_STRIKE_RANK = 3
LIQUID_MIN_VOLUME = 5
LIQUID_MIN_TRADES = 1
PATH_END_TOLERANCE_DAYS = 3

_PREMIUM_MULT_FALLBACK = 1.82
_VOL_LOOKBACK_FALLBACK = 60

FIXED_UNADJ_START = "2022-07-01"    # _unadj_daily.parquet coverage floor (spec-locked)

# DTE bands: name -> (dte_lo, dte_hi, target_dte), all in CALENDAR days.
BAND_DEFS = {
    "d15":   (7, 18, 12),
    "d30":   (21, 45, 30),
    "d60":   (46, 75, 60),
    "leaps": (180, 420, 270),
}

# Study arms: contract type, target moneyness grid, DTE bands, and the
# "usable" / walk-cap knobs that differ from the default.
#
# `extra_moneyness_by_band` (2026-07-26 optimization round): deeper-OTM put
# strikes added for SPECIFIC bands only -- deep-OTM short-DTE (d15) prints
# too thin to be usable, so it is deliberately excluded. See
# moneyness_for_band() below for how this combines with the base `moneyness`
# grid per (arm, band) cell.
ARMS = {
    "bull_put":   {"signal_set": "BULL", "contract_type": "put",
                   "moneyness": [1.00, 0.95, 0.90], "bands": ["d15", "d30", "d60"],
                   "min_forward_bars": 2, "walk_cap_cal_days": None,
                   "extra_moneyness_by_band": {"d30": [0.85, 0.80], "d60": [0.85, 0.80]}},
    "bear_call":  {"signal_set": "BEAR", "contract_type": "call",
                   "moneyness": [1.00, 1.05, 1.10], "bands": ["d15", "d30", "d60"],
                   "min_forward_bars": 2, "walk_cap_cal_days": None},
    "pmcc_long":  {"signal_set": "BULL", "contract_type": "call",
                   "moneyness": [0.75], "bands": ["leaps"],
                   "min_forward_bars": 1, "walk_cap_cal_days": 60},
    "pmcc_short": {"signal_set": "BULL", "contract_type": "call",
                   "moneyness": [1.05], "bands": ["d30"],
                   "min_forward_bars": 2, "walk_cap_cal_days": None},
    "ctrl_put":   {"signal_set": "CTRL", "contract_type": "put",
                   "moneyness": [1.00, 0.95], "bands": ["d30"],
                   "min_forward_bars": 2, "walk_cap_cal_days": None,
                   "extra_moneyness_by_band": {"d30": [0.85]}},
    "ctrl_call":  {"signal_set": "CTRL", "contract_type": "call",
                   "moneyness": [1.00], "bands": ["d30"],
                   "min_forward_bars": 2, "walk_cap_cal_days": None},
}

# Task groups: one shared chain fetch per (signal_set, option-type-union).
# `query_type` is passed straight to list_option_contracts (None = both types,
# used only by CTRL_both, whose two arms need opposite types off the same
# d30 window).
TASK_GROUPS = {
    "BULL_calls": {"signal_set": "BULL", "query_type": "call",
                   "arms": ["pmcc_long", "pmcc_short"], "chain_lo": 7, "chain_hi": 450},
    "BULL_puts":  {"signal_set": "BULL", "query_type": "put",
                   "arms": ["bull_put"], "chain_lo": 7, "chain_hi": 90},
    "BEAR_calls": {"signal_set": "BEAR", "query_type": "call",
                   "arms": ["bear_call"], "chain_lo": 7, "chain_hi": 90},
    "CTRL_both":  {"signal_set": "CTRL", "query_type": None,
                   "arms": ["ctrl_put", "ctrl_call"], "chain_lo": 7, "chain_hi": 60},
}


def moneyness_for_band(acfg: dict, band: str) -> list[float]:
    """PURE. Effective target-moneyness grid for one (arm, band) cell: the
    arm's base `moneyness` grid plus any band-specific additions from
    `extra_moneyness_by_band` (e.g. the deeper-OTM put strikes added
    2026-07-26 for bull_put d30/d60 and ctrl_put d30 only)."""
    extra = acfg.get("extra_moneyness_by_band", {}).get(band, [])
    return acfg["moneyness"] + extra


def expected_keys_for_group(group_name: str) -> list[tuple[str, float, str]]:
    """Static (arm, target_moneyness, dte_band) keys a group task must emit --
    the resumability journal key (minus symbol/signal_date/study_arm dupes)."""
    keys = []
    for arm in TASK_GROUPS[group_name]["arms"]:
        acfg = ARMS[arm]
        for band in acfg["bands"]:
            for mn in moneyness_for_band(acfg, band):
                keys.append((arm, mn, band))
    return keys


_EXPECTED_KEYS_CACHE = {g: expected_keys_for_group(g) for g in TASK_GROUPS}

# ---------------------------------------------------------------------------
# Paths.
# ---------------------------------------------------------------------------
OUT_DIR = _ROOT / ".cache" / "short_premium"
CONTRACTS_PROGRESS = OUT_DIR / "_contracts_progress.jsonl"
PATHS_PROGRESS = OUT_DIR / "_paths_progress.jsonl"
CONTRACTS_PARQUET = OUT_DIR / "contracts.parquet"
PATHS_PARQUET = OUT_DIR / "paths.parquet"
UNADJ_DAILY_PARQUET = OUT_DIR / "_unadj_daily.parquet"
BEAR_MONTHLY_COUNTS_JSON = OUT_DIR / "bear_monthly_counts.json"
META_JSON = OUT_DIR / "meta.json"
ENV_FILE = _ROOT / ".env"

CONTRACTS_COLUMNS = [
    "study_arm", "contract_type", "target_moneyness", "dte_band",
    "symbol", "signal_date", "overall", "status",
    "occ_ticker", "strike", "expiration", "dte_cal", "strike_rank",
    "spot_unadj", "spot_source", "entry_price", "adj_factor", "moneyness",
    "vol", "model_premium_pct", "model_premium_abs",
    "entry_premium_real", "entry_volume", "entry_trades", "entry_vwap", "liquid_entry",
    "bars_covered", "path_end_reason", "path_end_date", "stale_frac", "contract_id",
]
_C_FLOAT = ["target_moneyness", "overall", "strike", "spot_unadj", "entry_price",
            "adj_factor", "moneyness", "vol", "model_premium_pct", "model_premium_abs",
            "entry_premium_real", "entry_volume", "entry_vwap", "stale_frac"]
_C_INT = ["dte_cal", "entry_trades", "bars_covered"]
_C_INT8 = ["strike_rank"]
_C_BOOL = ["liquid_entry"]
_C_DATE = ["signal_date", "expiration", "path_end_date"]
# everything else (study_arm, contract_type, dte_band, symbol, status,
# occ_ticker, spot_source, path_end_reason, contract_id) is Utf8.

PATHS_COLUMNS = ["contract_id", "off", "date", "open", "high", "low", "close",
                 "volume", "trades", "vwap"]
_P_FLOAT = ["open", "high", "low", "close", "volume", "vwap"]
_P_INT = ["off", "trades"]
_P_DATE = ["date"]
# contract_id is Utf8.

UNADJ_DAILY_COLUMNS = ["symbol", "date", "spot_unadj", "spot_source"]


def log(msg) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Timeframe-403 + earliest-bar-date accounting (own state; workers are threads).
# ---------------------------------------------------------------------------
_tf403_lock = threading.Lock()
_tf403_state = {"logged": False, "n": 0, "dates": set()}
_bar_date_lock = threading.Lock()
_bar_date_state = {"earliest": None}
_paths_seen_lock = threading.Lock()
_paths_seen: set[str] = set()


def _note_tf403(d: str) -> None:
    with _tf403_lock:
        _tf403_state["n"] += 1
        _tf403_state["dates"].add(d)
        if not _tf403_state["logged"]:
            _tf403_state["logged"] = True
            log("[info] Polygon aggregates timeframe-403 (e.g. signal %s) -> counted as "
                "miss:timeframe_403 and continuing. Developer aggregates are ~4yr rolling; "
                "further 403s are counted silently (see meta.json)." % d)


def _note_bar_date(d: str) -> None:
    with _bar_date_lock:
        cur = _bar_date_state["earliest"]
        if cur is None or d < cur:
            _bar_date_state["earliest"] = d


def _claim_path(contract_id: str) -> bool:
    """True iff this call is the one that gets to write contract_id's path
    bars (dedupes the rare case where two (arm, moneyness) selections in the
    same run land on the identical contract)."""
    with _paths_seen_lock:
        if contract_id in _paths_seen:
            return False
        _paths_seen.add(contract_id)
        return True


# ---------------------------------------------------------------------------
# Key handling: env first, then .env. Never printed. (mirrors reference)
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


def _premium_mult() -> float:
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


def model_premium(vol: float | None, dte_cal: int, spot: float, mult: float) -> tuple:
    """PURE. model_premium_pct = mult * vol/100 * sqrt(dte_cal/30); model_premium_abs
    = pct * spot_unadj. This is the ONE place this study's premium reference differs
    from experiments/polygon_real_premium's (that one is fixed ~30 DTE so the
    sqrt(dte/30) term is trivially 1 and was omitted there)."""
    if vol is None or vol <= 0 or dte_cal is None or dte_cal <= 0:
        return None, None
    pct = mult * vol / 100.0 * math.sqrt(dte_cal / 30.0)
    abs_ = pct * spot if spot else None
    return pct, abs_


# ---------------------------------------------------------------------------
# Signal sampling (PURE -- offline testable, no DB).
# ---------------------------------------------------------------------------
def month_key(signal_date_iso: str) -> str:
    return signal_date_iso[:7]


def stratify_by_month_cap(rows: list[dict], cap: int = BEAR_MONTH_CAP,
                          seed: int = BEAR_SEED) -> tuple[list[dict], dict[str, int]]:
    """Stratified sample: <= `cap` rows per calendar month, deterministic under
    `seed`. Returns (sampled_rows, raw_per_month_counts) -- the raw counts are
    the FULL pool's monthly distribution, saved for later reweighting."""
    by_month: dict[str, list[dict]] = {}
    for r in rows:
        by_month.setdefault(month_key(r["signal_date"]), []).append(r)
    raw_counts = {m: len(v) for m, v in by_month.items()}
    rng = random.Random(seed)
    sampled = []
    for m in sorted(by_month):
        pool = sorted(by_month[m], key=lambda r: (r["signal_date"], r["symbol"]))
        k = min(cap, len(pool))
        sampled.extend(rng.sample(pool, k))
    sampled.sort(key=lambda r: (r["signal_date"], r["symbol"]))
    return sampled, raw_counts


def bull_monthly_shares(bull_rows: list[dict]) -> dict[str, float]:
    counts = Counter(month_key(r["signal_date"]) for r in bull_rows)
    total = sum(counts.values())
    if not total:
        return {}
    return {m: c / total for m, c in counts.items()}


def sample_ctrl(ctrl_pool: list[dict], bull_shares: dict[str, float],
                total: int = CTRL_TOTAL, seed: int = CTRL_SEED) -> list[dict]:
    """Sample CTRL rows date-matched to BULL's monthly distribution: per month,
    round(total * bull_month_share), capped at that month's CTRL pool size."""
    by_month: dict[str, list[dict]] = {}
    for r in ctrl_pool:
        by_month.setdefault(month_key(r["signal_date"]), []).append(r)
    rng = random.Random(seed)
    sampled = []
    for m in sorted(bull_shares):
        pool = sorted(by_month.get(m, []), key=lambda r: (r["signal_date"], r["symbol"]))
        k = min(round(total * bull_shares[m]), len(pool))
        if k > 0:
            sampled.extend(rng.sample(pool, k))
    sampled.sort(key=lambda r: (r["signal_date"], r["symbol"]))
    return sampled


# ---------------------------------------------------------------------------
# Signal source (MySQL, SINGLE-THREADED ONLY).
# ---------------------------------------------------------------------------
def load_score_rows(version_id: int, start: str, end: str,
                    lo: int | None = None, hi: int | None = None) -> list[dict]:
    from database.models.core import Score, AlgorithmVersion
    ver = AlgorithmVersion.get_by_id(version_id)
    conds = [Score.version == ver, Score.date >= _dt(start), Score.date <= _dt(end)]
    if lo is not None:
        conds.append(Score.overall >= lo)
    if hi is not None:
        conds.append(Score.overall <= hi)
    q = (Score.select(Score.symbol, Score.date, Score.overall)
         .where(*conds)
         .order_by(Score.date, Score.symbol))
    rows = []
    for s in q:
        sym = s.symbol_id             # FK column holds the ticker string (peewee FK-per-row trap)
        rows.append({"symbol": sym, "signal_date": s.date.isoformat(), "overall": int(s.overall)})
    return rows


def load_all_signal_sets(version_id: int, start: str, end: str,
                         bear_cap: int = BEAR_MONTH_CAP, bear_seed: int = BEAR_SEED,
                         ctrl_total: int = CTRL_TOTAL, ctrl_seed: int = CTRL_SEED,
                         symbols: set[str] | None = None) -> tuple:
    """SINGLE-THREADED. Returns (bull_rows, bear_rows, ctrl_rows, bear_raw_counts)."""
    bull_rows = load_score_rows(version_id, start, end, lo=BULL_MIN)
    bear_pool = load_score_rows(version_id, start, end, hi=BEAR_MAX)
    ctrl_pool = load_score_rows(version_id, start, end, lo=CTRL_LO, hi=CTRL_HI)
    bear_rows, bear_raw_counts = stratify_by_month_cap(bear_pool, cap=bear_cap, seed=bear_seed)
    shares = bull_monthly_shares(bull_rows)
    ctrl_rows = sample_ctrl(ctrl_pool, shares, total=ctrl_total, seed=ctrl_seed)
    if symbols:
        bull_rows = [r for r in bull_rows if r["symbol"] in symbols]
        bear_rows = [r for r in bear_rows if r["symbol"] in symbols]
        ctrl_rows = [r for r in ctrl_rows if r["symbol"] in symbols]
    return bull_rows, bear_rows, ctrl_rows, bear_raw_counts


# ---------------------------------------------------------------------------
# AS-TRADED (UNADJUSTED) daily series -- G51 spot trap fix, full-history form.
# See experiments/polygon_real_premium/DESIGN.md section 2 for the full
# derivation; this is the same fix, extended to the FULL daily series (not
# just signal dates) because the downstream ledger needs it for expiry
# settlement and margin marking on ANY calendar day, not only entry dates.
# ---------------------------------------------------------------------------
def load_unadj_daily(symbols: list[str], closes_obj,
                     cache: Path = UNADJ_DAILY_PARQUET, refresh: bool = False,
                     batch: int = 50) -> dict[str, dict[str, tuple]]:
    """SINGLE-THREADED. {symbol: {date: (close_unadj, source)}} for every symbol,
    2022-07-01 .. today, cached to parquet. `source` is 'yf_unadj' or, for a
    symbol yfinance has NO data for at all, 'ph_adjusted' (the adjusted
    price_history series, flagged not hidden)."""
    have: dict[str, dict[str, tuple]] = {}
    if cache.exists() and not refresh:
        df = pl.read_parquet(cache)
        for sym, d, c, src in zip(df["symbol"].to_list(), df["date"].to_list(),
                                  df["spot_unadj"].to_list(), df["spot_source"].to_list()):
            have.setdefault(sym, {})[d] = (c, src)
    need = [s for s in symbols if s not in have]
    if not need:
        log(f"unadjusted DAILY cache hit for all {len(symbols)} symbols")
        return have

    log(f"fetching FULL as-traded daily closes for {len(need)} symbols via yfinance "
        f"({FIXED_UNADJ_START}..today, cached to {cache.name})...")
    import yfinance as yf
    y_end = (date.today() + timedelta(days=2)).isoformat()
    got_any: set[str] = set()
    for i in range(0, len(need), batch):
        chunk = need[i:i + batch]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                data = yf.download(chunk, start=FIXED_UNADJ_START, end=y_end, interval="1d",
                                   auto_adjust=False, actions=True, group_by="ticker",
                                   progress=False, threads=True)
        except Exception as exc:
            log(f"[warn] yfinance batch {i // batch} failed: {exc!r}")
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
                m = {d: (v, "yf_unadj") for d, v in zip(dates, unadj) if v and v > 0}
                if m:
                    have[sym] = m
                    got_any.add(sym)
            except Exception:
                continue
        log(f"  unadj daily: {min(i + batch, len(need))}/{len(need)} symbols")

    fallback_syms = [s for s in need if s not in got_any]
    if fallback_syms:
        log(f"  {len(fallback_syms)} symbols had no yfinance data at all; falling back to "
            f"adjusted price_history close (spot_source=ph_adjusted)")
        for sym in fallback_syms:
            by_date = closes_obj._by_symbol.get(sym, {}) or {}
            m = {d: (float(c), "ph_adjusted") for d, c in by_date.items()
                 if c is not None and c > 0 and FIXED_UNADJ_START <= d <= y_end}
            if m:
                have[sym] = m

    recs = [{"symbol": s, "date": d, "spot_unadj": c, "spot_source": src}
            for s, m in have.items() for d, (c, src) in m.items()]
    if recs:
        cache.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(recs, infer_schema_length=None).write_parquet(cache)
        log(f"wrote {cache} ({len(recs)} rows, {len(have)} symbols)")
    return have


def enrich_base(signals: list[dict], closes_obj,
                unadj_daily: dict[str, dict[str, tuple]]) -> None:
    """SINGLE-THREADED. Attach entry_price (ADJUSTED close) / vol / spot_unadj /
    spot_source / adj_factor to each signal in place. model_premium_pct/abs are
    NOT computed here -- they depend on dte_cal, which is per-(arm,band), so
    they are computed per output row instead."""
    lb = _vol_lookback()
    series: dict[str, tuple] = {}
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
            sig.update({"entry_price": None, "vol": None, "spot_unadj": None,
                        "spot_source": None, "adj_factor": None})
            continue
        sig["entry_price"] = cs[i]
        sig["vol"] = realized_vol(cs, i, lookback=lb)
        u = (unadj_daily.get(sym) or {}).get(sig["signal_date"])
        if u:
            sig["spot_unadj"], sig["spot_source"] = float(u[0]), u[1]
        else:
            sig["spot_unadj"] = cs[i]           # fallback: adjusted close
            sig["spot_source"] = "ph_adjusted"
        sig["adj_factor"] = sig["spot_unadj"] / cs[i] if cs[i] else None


def load_done(progress_path: Path = CONTRACTS_PROGRESS) -> set[tuple]:
    done: set[tuple] = set()
    if progress_path.exists():
        for line in progress_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                done.add((r["study_arm"], r["symbol"], r["signal_date"],
                          r["target_moneyness"], r["dte_band"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def task_fully_done(done: set[tuple], group_name: str, sig: dict) -> bool:
    sym, d = sig["symbol"], sig["signal_date"]
    for arm, mn, band in _EXPECTED_KEYS_CACHE[group_name]:
        if (arm, sym, d, mn, band) not in done:
            return False
    return True


# ---------------------------------------------------------------------------
# Contract chain normalization -- PURE, keeps BOTH put and call rows (unlike
# the reference's normalize_chain, which is call-only). Same hygiene rules:
# drop non-100-deliverable / non-standard-OCC-root ("adjusted") contracts,
# dedupe on (expiration, strike, type) preferring the standard OCC ticker.
# ---------------------------------------------------------------------------
_STD_TICKER = re.compile(r"^O:[A-Z]+\d{6}[CP]\d+$")


def normalize_chain_multi(raw: list[dict], sym: str, d_date: date) -> list[dict]:
    best: dict[tuple, dict] = {}
    for c in raw:
        ctype = c.get("contract_type")
        if ctype not in ("call", "put"):
            continue
        if not c.get("strike_price") or not c.get("expiration_date") or not c.get("ticker"):
            continue
        spc = c.get("shares_per_contract")
        if spc is not None and int(spc) != 100:
            continue
        tick = str(c["ticker"])
        std = bool(_STD_TICKER.match(tick)) and tick.startswith(f"O:{sym}")
        exp = _dt(c["expiration_date"])
        key = (exp.isoformat(), float(c["strike_price"]), ctype)
        rec = {"ticker": tick, "strike": float(c["strike_price"]), "expiration_date": exp,
               "dte": (exp - d_date).days, "type": ctype, "std": std}
        prev = best.get(key)
        if prev is None or (rec["std"] and not prev["std"]):
            best[key] = rec
    return sorted(best.values(), key=lambda r: (r["dte"], r["strike"]))


# ---------------------------------------------------------------------------
# Forward path -- INCLUDES the entry bar at off=0 (paths.parquet spec), unlike
# the reference's build_forward_path (strictly forward, off>=1).
# ---------------------------------------------------------------------------
def build_full_path(by_date: dict, signal_date: str, end_date: str,
                    tdi: dict[str, int] | None = None) -> list[dict]:
    rows = []
    for d in sorted(by_date):
        if d < signal_date or d > end_date:
            continue
        b = by_date[d]
        if b.get("c") is None or b.get("c") <= 0:
            continue
        rows.append({"d": d, "o": b.get("o"), "h": b.get("h"), "l": b.get("l"),
                     "c": b.get("c"), "v": b.get("v"), "n": b.get("n"), "vw": b.get("vw")})
    base = tdi.get(signal_date) if tdi else None
    for i, r in enumerate(rows):
        off = None
        if base is not None and tdi is not None:
            j = tdi.get(r["d"])
            if j is not None:
                off = j - base
        r["off"] = off if off is not None else i    # fallback: sequential, 0-based (off=0 IS entry)
    return rows


def walk_meta(fwd_path: list[dict], expected: int | None) -> dict:
    """PURE. bars_covered + stale_frac over the FORWARD-only subset (off>=1) --
    mirrors experiments/polygon_real_premium's stale_frac definition exactly,
    minus the pnl/tp/sl touch-bar fields this study doesn't need in
    contracts.parquet (the ledger engine computes exits post-hoc from
    paths.parquet)."""
    bars_covered = len(fwd_path)
    n_zero_vol = sum(1 for b in fwd_path if not b.get("v"))
    denom = expected if (expected and expected > 0) else bars_covered
    n_missing = max(0, denom - bars_covered)
    stale_frac = min(1.0, (n_missing + n_zero_vol) / denom) if denom else None
    return {"bars_covered": bars_covered, "stale_frac": stale_frac}


# ---------------------------------------------------------------------------
# Row builders.
# ---------------------------------------------------------------------------
def _base_row(arm: str, moneyness: float, band: str, sig: dict, status: str) -> dict:
    row = {c: None for c in CONTRACTS_COLUMNS}
    row.update({
        "study_arm": arm, "contract_type": ARMS[arm]["contract_type"],
        "target_moneyness": moneyness, "dte_band": band,
        "symbol": sig["symbol"], "signal_date": sig["signal_date"],
        "overall": sig.get("overall"), "status": status,
        "spot_unadj": sig.get("spot_unadj"), "spot_source": sig.get("spot_source"),
        "entry_price": sig.get("entry_price"), "adj_factor": sig.get("adj_factor"),
        "vol": sig.get("vol"), "bars_covered": 0,
    })
    return row


def _fill_candidate(row: dict, cand: dict, spot: float, dte_cal: int,
                    mpp: float | None, mpa: float | None) -> None:
    row.update({
        "occ_ticker": cand["ticker"], "strike": cand["strike"],
        "expiration": cand["expiration_date"].isoformat(), "dte_cal": dte_cal,
        "strike_rank": cand["strike_rank"], "moneyness": cand["strike"] / spot if spot else None,
        "model_premium_pct": mpp, "model_premium_abs": mpa,
        "contract_id": f"{cand['ticker']}|{row['signal_date']}",
    })


def _fill_entry(row: dict, entry_bar: dict, entry_real: float) -> None:
    row.update({
        "entry_premium_real": entry_real,
        "entry_volume": entry_bar.get("v"), "entry_trades": entry_bar.get("n"),
        "entry_vwap": entry_bar.get("vw"),
        "liquid_entry": is_liquid_entry(entry_bar.get("v"), entry_bar.get("n")),
    })


def _fill_walk(row: dict, wmeta: dict, end_reason: str, end_date: str | None) -> None:
    row.update({"bars_covered": wmeta["bars_covered"], "stale_frac": wmeta["stale_frac"],
                "path_end_reason": end_reason, "path_end_date": end_date})


# ---------------------------------------------------------------------------
# Per-group-task processing (thread-safe: Polygon HTTP only, no DB).
# ---------------------------------------------------------------------------
def process_group_task(client, group_name: str, sig: dict, tdi: dict[str, int] | None,
                       tdays: list[str] | None, mult: float) -> tuple[list[dict], dict[str, list]]:
    """One (group, symbol, signal_date) -> a list of contract rows (one per
    (arm, target_moneyness, dte_band) the group serves) + a dict of NEWLY
    claimed contract_id -> raw bar list for paths.parquet."""
    gcfg = TASK_GROUPS[group_name]
    expected = _EXPECTED_KEYS_CACHE[group_name]
    sym, d = sig["symbol"], sig["signal_date"]
    d_date = _dt(d)
    spot = sig.get("spot_unadj") or sig.get("entry_price")
    rows: list[dict] = []
    paths_out: dict[str, list] = {}

    if spot is None or spot <= 0:
        return [_base_row(arm, mn, band, sig, "miss:no_underlying") for arm, mn, band in expected], {}

    exp_gte = (d_date + timedelta(days=gcfg["chain_lo"])).isoformat()
    exp_lte = (d_date + timedelta(days=gcfg["chain_hi"])).isoformat()
    raw = client.list_option_contracts(sym, as_of=d, exp_gte=exp_gte, exp_lte=exp_lte,
                                       contract_type=gcfg["query_type"])
    chain = normalize_chain_multi(raw, sym, d_date)
    if not chain:
        return [_base_row(arm, mn, band, sig, "miss:no_chain") for arm, mn, band in expected], {}

    for arm in gcfg["arms"]:
        acfg = ARMS[arm]
        for band in acfg["bands"]:
            lo, hi, target = BAND_DEFS[band]
            filtered = [c for c in chain if c["type"] == acfg["contract_type"]]
            dte, at_expiry = select_expiry(filtered, dte_lo=lo, dte_hi=hi, target=target)
            mn_list = moneyness_for_band(acfg, band)
            if dte is None:
                for mn in mn_list:
                    rows.append(_base_row(arm, mn, band, sig, "miss:no_dte_band"))
                continue
            for mn in mn_list:
                target_strike = mn * spot
                candidates = rank_strikes(at_expiry, target_strike, MAX_STRIKE_RANK)
                if not candidates:
                    rows.append(_base_row(arm, mn, band, sig, "miss:no_dte_band"))
                    continue
                row_out, path_bars, hit_403 = _select_one(
                    client, arm, mn, band, sig, acfg, candidates, spot, d, d_date,
                    tdi, tdays, mult)
                rows.append(row_out)
                if hit_403:
                    _note_tf403(d)
                if path_bars is not None and row_out.get("contract_id"):
                    if _claim_path(row_out["contract_id"]):
                        paths_out[row_out["contract_id"]] = path_bars
    return rows, paths_out


def _select_one(client, arm, mn, band, sig, acfg, candidates, spot, d, d_date,
                tdi, tdays, mult):
    """Try each ranked strike in order; return (row, path_bars_or_None, hit_403)."""
    best_partial = None    # (row, n_fwd, path_bars)
    for cand in candidates:
        if acfg["walk_cap_cal_days"]:
            end_walk = min(cand["expiration_date"], d_date + timedelta(days=acfg["walk_cap_cal_days"]))
        else:
            end_walk = cand["expiration_date"]
        try:
            bars = client.option_daily_bars(cand["ticker"], d, end_walk.isoformat())
        except RuntimeError as exc:
            if _is_timeframe_403(exc):
                row = _base_row(arm, mn, band, sig, "miss:timeframe_403")
                return row, None, True
            raise
        by_date = bars_ohlc_by_date(bars)
        entry_bar = by_date.get(d)
        if entry_bar is None or entry_bar.get("c") is None or entry_bar["c"] <= 0:
            continue                                          # try next-nearest strike
        for bd in by_date:
            _note_bar_date(bd)

        entry_real = float(entry_bar["c"])
        full_path = build_full_path(by_date, d, end_walk.isoformat(), tdi)
        fwd_path = [b for b in full_path if b.get("off") is not None and b["off"] > 0]
        expected_n = expected_forward_bars(tdays or [], d, end_walk.isoformat())
        wmeta = walk_meta(fwd_path, expected_n)
        end_reason, end_date_ = classify_path_end(fwd_path, end_walk.isoformat(),
                                                   cand["expiration_date"].isoformat())
        dte_cal = int(cand["dte"])
        mpp, mpa = model_premium(sig.get("vol"), dte_cal, spot, mult)

        row = _base_row(arm, mn, band, sig, "kept")
        _fill_candidate(row, cand, spot, dte_cal, mpp, mpa)
        _fill_entry(row, entry_bar, entry_real)
        _fill_walk(row, wmeta, end_reason, end_date_)

        n_fwd = len(fwd_path)
        if n_fwd >= acfg["min_forward_bars"]:
            return row, full_path, False
        row["status"] = "miss:no_forward_bars" if n_fwd == 0 else "miss:too_few_bars"
        if best_partial is None or n_fwd > best_partial[1]:
            best_partial = (row, n_fwd, full_path)
        continue

    if best_partial is not None:
        return best_partial[0], best_partial[2], False
    return _base_row(arm, mn, band, sig, "miss:no_atm_price"), None, False


# ---------------------------------------------------------------------------
# Consolidation: journal -> parquet (OFFLINE, no key, no DB).
# ---------------------------------------------------------------------------
def _cast_frame(df: pl.DataFrame, columns: list[str], float_cols, int_cols,
               int8_cols, bool_cols, date_cols) -> pl.DataFrame:
    casts = []
    for c in columns:
        if c in float_cols:
            casts.append(pl.col(c).cast(pl.Float64, strict=False).alias(c))
        elif c in int_cols:
            casts.append(pl.col(c).cast(pl.Int64, strict=False).alias(c))
        elif c in int8_cols:
            casts.append(pl.col(c).cast(pl.Int8, strict=False).alias(c))
        elif c in bool_cols:
            casts.append(pl.col(c).cast(pl.Boolean, strict=False).alias(c))
        elif c in date_cols:
            casts.append(pl.col(c).str.strptime(pl.Date, "%Y-%m-%d", strict=False).alias(c))
        else:
            casts.append(pl.col(c).cast(pl.Utf8, strict=False).alias(c))
    df = df.with_columns(casts).select(columns)
    fnan = [c for c in float_cols if c in df.columns]
    if fnan:
        df = df.with_columns([pl.col(c).fill_nan(None) for c in fnan])
    return df


def _empty_schema(columns, float_cols, int_cols, int8_cols, bool_cols, date_cols) -> dict:
    sch: dict = {}
    for c in columns:
        if c in float_cols:
            sch[c] = pl.Float64
        elif c in int_cols:
            sch[c] = pl.Int64
        elif c in int8_cols:
            sch[c] = pl.Int8
        elif c in bool_cols:
            sch[c] = pl.Boolean
        elif c in date_cols:
            sch[c] = pl.Date
        else:
            sch[c] = pl.Utf8
    return sch


def consolidate_contracts(progress_path: Path = CONTRACTS_PROGRESS,
                          parquet_path: Path = CONTRACTS_PARQUET) -> int:
    if not progress_path.exists():
        log("no contracts journal yet; nothing to consolidate")
        df = pl.DataFrame(schema=_empty_schema(CONTRACTS_COLUMNS, _C_FLOAT, _C_INT,
                                               _C_INT8, _C_BOOL, _C_DATE))
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(parquet_path)
        return 0
    rows: dict[tuple, dict] = {}
    for line in progress_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = (r.get("study_arm"), r.get("symbol"), r.get("signal_date"),
               r.get("target_moneyness"), r.get("dte_band"))
        if any(k is None for k in key):
            continue
        rows[key] = r
    if rows:
        recs = [{c: r.get(c) for c in CONTRACTS_COLUMNS} for r in rows.values()]
        df = pl.DataFrame(recs, infer_schema_length=None)
        df = _cast_frame(df, CONTRACTS_COLUMNS, _C_FLOAT, _C_INT, _C_INT8, _C_BOOL, _C_DATE)
    else:
        df = pl.DataFrame(schema=_empty_schema(CONTRACTS_COLUMNS, _C_FLOAT, _C_INT,
                                               _C_INT8, _C_BOOL, _C_DATE))
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(parquet_path)
    log(f"consolidated {df.height} rows -> {parquet_path}")
    return df.height


def consolidate_paths(progress_path: Path = PATHS_PROGRESS,
                      parquet_path: Path = PATHS_PARQUET) -> int:
    if not progress_path.exists():
        log("no paths journal yet; nothing to consolidate")
        df = pl.DataFrame(schema=_empty_schema(PATHS_COLUMNS, _P_FLOAT, _P_INT, [], [], _P_DATE))
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(parquet_path)
        return 0
    latest: dict[str, list] = {}
    for line in progress_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = r.get("contract_id")
        if not cid:
            continue
        latest[cid] = r.get("bars") or []
    recs = []
    for cid, bars in latest.items():
        for b in bars:
            recs.append({"contract_id": cid, "off": b.get("off"), "date": b.get("d"),
                         "open": b.get("o"), "high": b.get("h"), "low": b.get("l"),
                         "close": b.get("c"), "volume": b.get("v"), "trades": b.get("n"),
                         "vwap": b.get("vw")})
    if recs:
        df = pl.DataFrame(recs, infer_schema_length=None)
        df = _cast_frame(df, PATHS_COLUMNS, _P_FLOAT, _P_INT, [], [], _P_DATE)
    else:
        df = pl.DataFrame(schema=_empty_schema(PATHS_COLUMNS, _P_FLOAT, _P_INT, [], [], _P_DATE))
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(parquet_path)
    log(f"consolidated {df.height} path bars ({len(latest)} contracts) -> {parquet_path}")
    return df.height


def write_meta(args, universe: dict, elapsed: float, calls: int,
              bear_raw_counts: dict, contracts_path: Path = CONTRACTS_PARQUET,
              paths_path: Path = PATHS_PARQUET, meta_path: Path = META_JSON) -> None:
    status_counts: dict = {}
    arm_status: dict = {}
    n_rows = 0
    n_path_rows = 0
    n_contracts = 0
    if contracts_path.exists():
        df = pl.read_parquet(contracts_path)
        n_rows = df.height
        if n_rows:
            status_counts = dict(Counter(df["status"].to_list()))
            for arm in ARMS:
                sub = df.filter(pl.col("study_arm") == arm)
                if sub.height:
                    arm_status[arm] = dict(Counter(sub["status"].to_list()))
    if paths_path.exists():
        pdf = pl.read_parquet(paths_path)
        n_path_rows = pdf.height
        n_contracts = pdf["contract_id"].n_unique() if n_path_rows else 0
    meta = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scoring_version_id": VERSION_ID,
        "window_requested": {"start": args.start, "end": args.end},
        "universe_signals": universe,
        "bear_raw_monthly_counts_file": str(BEAR_MONTHLY_COUNTS_JSON),
        "contracts_rows": n_rows,
        "rows_by_status": status_counts,
        "rows_by_status_per_arm": arm_status,
        "paths_bar_rows": n_path_rows,
        "paths_distinct_contracts": n_contracts,
        "total_api_calls_this_run": calls,
        "wall_clock_seconds": round(elapsed, 1),
        "timeframe_403_count": _tf403_state["n"],
        "timeframe_403_signal_dates": len(_tf403_state["dates"]),
        "earliest_successful_bar_date_this_run": _bar_date_state["earliest"],
        "arms": {a: {"contract_type": c["contract_type"], "moneyness": c["moneyness"],
                     "extra_moneyness_by_band": c.get("extra_moneyness_by_band", {}),
                     "bands": c["bands"], "min_forward_bars": c["min_forward_bars"],
                     "walk_cap_cal_days": c["walk_cap_cal_days"]} for a, c in ARMS.items()},
        "band_defs": BAND_DEFS,
        "spot_anchor": ("strike selection anchors on the AS-TRADED close "
                        "(yfinance auto_adjust=False x forward split factors); "
                        "price_history.close is back-adjusted -- see DESIGN.md (G51)"),
        "path_offsets": "market trading days since signal_date; off=0 is the entry bar",
        "liquidity_rule": f"liquid_entry = entry_volume >= {LIQUID_MIN_VOLUME} "
                          f"AND entry_trades >= {LIQUID_MIN_TRADES}",
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
    mult = _premium_mult()

    symbols = {s.strip().upper() for s in args.symbols.split(",") if s.strip()} or None

    # ---- SINGLE-THREADED DB PHASE (PyMySQL is not thread-safe) ----
    log("loading BULL/BEAR/CTRL signal sets from MySQL (single-threaded)...")
    bull_rows, bear_rows, ctrl_rows, bear_raw_counts = load_all_signal_sets(
        args.version_id, args.start, args.end, bear_cap=args.bear_cap, bear_seed=args.seed,
        ctrl_total=args.ctrl_total, ctrl_seed=args.seed, symbols=symbols)
    log(f"universe: BULL={len(bull_rows)} BEAR(sampled)={len(bear_rows)} "
        f"CTRL(sampled)={len(ctrl_rows)} [v{args.version_id}, {args.start}..{args.end}]")
    BEAR_MONTHLY_COUNTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(BEAR_MONTHLY_COUNTS_JSON, "w", encoding="ascii") as f:
        json.dump({"raw_pool_monthly_counts": bear_raw_counts,
                   "cap_per_month": args.bear_cap, "seed": args.seed}, f, indent=2)
    log(f"wrote {BEAR_MONTHLY_COUNTS_JSON}")

    group_sigs = {"BULL_calls": bull_rows, "BULL_puts": bull_rows,
                 "BEAR_calls": bear_rows, "CTRL_both": ctrl_rows}

    # round-robin task order so a small --limit slice still samples every group
    tasks: list[tuple[str, dict]] = []
    iters = {g: iter(sigs) for g, sigs in group_sigs.items()}
    active = list(iters.keys())
    while active:
        nxt = []
        for g in active:
            try:
                tasks.append((g, next(iters[g])))
                nxt.append(g)
            except StopIteration:
                pass
        active = nxt

    done = load_done()
    fresh = [(g, s) for g, s in tasks if not task_fully_done(done, g, s)]
    already_done = len(tasks) - len(fresh)
    todo = fresh[:args.limit] if args.limit else fresh
    log(f"tasks_total={len(tasks)} already_done={already_done} fresh={len(fresh)} "
        f"todo(after --limit)={len(todo)} workers={args.workers}")
    if not todo:
        nc = consolidate_contracts()
        np_ = consolidate_paths()
        write_meta(args, {"BULL": len(bull_rows), "BEAR": len(bear_rows), "CTRL": len(ctrl_rows)},
                  0.0, 0, bear_raw_counts)
        log(f"nothing to do; contracts={nc} paths={np_}")
        return 0

    client = PolygonClient(sleep_seconds=rate_sleep_seconds(default=0.0))
    from polygon_iv_ingest import Closes
    closes = Closes(client)
    all_syms = sorted({s["symbol"] for _, s in tasks})
    log(f"preloading underlying (adjusted) closes for {len(all_syms)} symbols (single-threaded)...")
    closes.preload(all_syms)
    unadj_daily = load_unadj_daily(all_syms, closes, refresh=args.refresh_spot)

    merged: dict[tuple, dict] = {}
    for _, s in tasks:
        merged[(s["symbol"], s["signal_date"])] = s
    enrich_base(list(merged.values()), closes, unadj_daily)
    # tasks reference dicts inside group_sigs lists; propagate enrichment back
    for g, s in tasks:
        m = merged[(s["symbol"], s["signal_date"])]
        s.update(m)

    tdays, tdi = build_trading_index(closes)
    log(f"market trading calendar: {len(tdays)} days "
        f"[{tdays[0] if tdays else '-'}..{tdays[-1] if tdays else '-'}]")
    # ---- END DB PHASE. Worker threads below do Polygon HTTP only. ----

    t0 = time.time()
    ctr = {"n": 0, "kept": 0}
    c_fh = CONTRACTS_PROGRESS.open("a", encoding="utf-8")
    p_fh = PATHS_PROGRESS.open("a", encoding="utf-8")
    log_lock = threading.Lock()

    def record(rows: list[dict], paths_out: dict[str, list]) -> None:
        with log_lock:
            for row in rows:
                c_fh.write(json.dumps(row, default=str) + "\n")
                ctr["n"] += 1
                if row.get("status") == "kept":
                    ctr["kept"] += 1
            for cid, bars in paths_out.items():
                p_fh.write(json.dumps({"contract_id": cid, "bars": bars}, default=str) + "\n")
            c_fh.flush()
            p_fh.flush()
            if ctr["n"] % args.flush_every < len(rows):
                consolidate_contracts()
                consolidate_paths()
                rate = ctr["n"] / max(time.time() - t0, 1e-6)
                log(f"  rows={ctr['n']} kept={ctr['kept']} calls={client.calls} {rate:.2f}/s")

    def run_one(task: tuple[str, dict]) -> None:
        g, sig = task
        try:
            rows, paths_out = process_group_task(client, g, sig, tdi, tdays, mult)
        except Exception as exc:
            rows = [_base_row(arm, mn, band, sig, f"miss:err:{repr(exc)[:80]}")
                    for arm, mn, band in _EXPECTED_KEYS_CACHE[g]]
            paths_out = {}
        record(rows, paths_out)

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(run_one, todo))
    else:
        for t in todo:
            run_one(t)
    c_fh.close()
    p_fh.close()

    nc = consolidate_contracts()
    np_ = consolidate_paths()
    elapsed = time.time() - t0
    write_meta(args, {"BULL": len(bull_rows), "BEAR": len(bear_rows), "CTRL": len(ctrl_rows)},
              elapsed, client.calls, bear_raw_counts)
    log(f"DONE: rows_written={ctr['n']} kept={ctr['kept']} api_calls={client.calls} "
        f"in {elapsed:.0f}s -> {CONTRACTS_PARQUET} (rows={nc}), {PATHS_PARQUET} (bars={np_})")
    if _tf403_state["n"]:
        log(f"  note: {_tf403_state['n']} attempts missed as timeframe-403 across "
            f"{len(_tf403_state['dates'])} signal dates.")
    return 0


# ===========================================================================
# OFFLINE SELF-TESTS (no key, no network, no DB).
# ===========================================================================
def _bar(off, h, l, c, v=100, o=None, d=None):
    return {"off": off, "d": d or f"2022-09-{off + 1:02d}", "o": o if o is not None else c,
            "h": h, "l": l, "c": c, "v": v, "n": 10, "vw": c}


def selftest() -> int:
    log("=== short_premium/pull_short.py OFFLINE SELF-TESTS ===")

    # -- 1. band defs sanity + select_expiry reuse across all 4 bands --------
    d0 = date(2022, 9, 1)

    def mkraw(strike, dte, sym="FAKE", spc=100, ctype="call", tick=None):
        exp = d0 + timedelta(days=dte)
        letter = "C" if ctype == "call" else "P"
        return {"ticker": tick or f"O:{sym}{exp.strftime('%y%m%d')}{letter}{int(strike * 1000):08d}",
                "contract_type": ctype, "strike_price": float(strike),
                "expiration_date": exp.isoformat(), "shares_per_contract": spc}

    for band, (lo, hi, target) in BAND_DEFS.items():
        raw = [mkraw(100, lo, ctype="put"), mkraw(100, target, ctype="put"),
               mkraw(100, hi, ctype="put"), mkraw(100, hi + 5, ctype="put")]
        chain = normalize_chain_multi(raw, "FAKE", d0)
        puts = [c for c in chain if c["type"] == "put"]
        dte, at_exp = select_expiry(puts, dte_lo=lo, dte_hi=hi, target=target)
        assert dte == target, (band, dte)
        assert len(at_exp) == 1, (band, at_exp)
    log("  [1] BAND_DEFS + select_expiry across d15/d30/d60/leaps OK")

    # -- 2. normalize_chain_multi keeps both types, drops adjusted/malformed --
    raw = ([mkraw(k, 28, ctype="call") for k in (95, 100, 105)]
           + [mkraw(k, 28, ctype="put") for k in (95, 100, 105)]
           + [mkraw(100, 28, ctype="call", spc=10, tick="O:FAKE1220929C00100000")]   # adjusted
           + [{"ticker": "O:FAKE1bad", "contract_type": "call", "strike_price": None,
               "expiration_date": "2022-09-29", "shares_per_contract": 100}])        # malformed
    chain = normalize_chain_multi(raw, "FAKE", d0)
    assert sum(1 for c in chain if c["type"] == "call") == 3, chain
    assert sum(1 for c in chain if c["type"] == "put") == 3, chain
    assert not any("FAKE1" in c["ticker"] for c in chain), chain
    log("  [2] normalize_chain_multi (both types, adjusted/malformed drop) OK")

    # -- 3. strike targeting incl. MMM-style adjusted-vs-unadjusted case -----
    # MMM 2022-08-23: adjusted close 103.79, as-traded 141.75 (spinoff ratio 1.196).
    # The naive rule (adjusted close as anchor) would target ~104; the fix
    # (spot_unadj) targets ~142, matching the real listed strikes (131..140).
    assert round(apply_forward_splits(["2022-08-23"], [118.52], [("2024-04-01", 1.196)])[0], 2) == 141.75
    mmm_spot_unadj = 141.75
    mmm_raw = [mkraw(k, 30, sym="MMM", ctype="put") for k in range(131, 141)]
    mmm_chain = [c for c in normalize_chain_multi(mmm_raw, "MMM", d0) if c["type"] == "put"]
    dte, at_exp = select_expiry(mmm_chain, dte_lo=21, dte_hi=45, target=30)
    ranked = rank_strikes(at_exp, 1.00 * mmm_spot_unadj, MAX_STRIKE_RANK)
    assert ranked[0]["strike"] == 140.0, ranked      # nearest listed strike <= as-traded spot
    naive_ranked = rank_strikes(at_exp, 1.00 * 103.79, MAX_STRIKE_RANK)
    assert naive_ranked[0]["strike"] == 131.0, naive_ranked   # the WRONG anchor the bug produces
    assert ranked[0]["strike"] != naive_ranked[0]["strike"], "spot trap regression"
    log("  [3] strike targeting incl. MMM adjusted-vs-unadjusted spot trap OK")

    # -- 4. model_premium formula (incl. sqrt(dte/30) scaling, absent in the
    #        fixed-30DTE reference pull) -----------------------------------
    pct, abs_ = model_premium(vol=2.0, dte_cal=30, spot=100.0, mult=1.82)
    assert abs(pct - (1.82 * 2.0 / 100.0)) < 1e-12, pct
    assert abs(abs_ - pct * 100.0) < 1e-9, abs_
    pct60, _ = model_premium(vol=2.0, dte_cal=60, spot=100.0, mult=1.82)
    assert abs(pct60 - pct * math.sqrt(2)) < 1e-9, (pct, pct60)
    assert model_premium(None, 30, 100.0, 1.82) == (None, None)
    assert model_premium(2.0, 0, 100.0, 1.82) == (None, None)
    log("  [4] model_premium (PREMIUM_MULT x vol/100 x sqrt(dte_cal/30)) OK")

    # -- 5. sampling: stratify_by_month_cap + bull_monthly_shares + sample_ctrl
    bear_pool = []
    for month, n in (("2022-08", 200), ("2022-09", 50), ("2022-10", 10)):
        for i in range(n):
            bear_pool.append({"symbol": f"S{i}", "signal_date": f"{month}-15", "overall": 20})
    sampled, raw_counts = stratify_by_month_cap(bear_pool, cap=85, seed=42)
    assert raw_counts == {"2022-08": 200, "2022-09": 50, "2022-10": 10}, raw_counts
    by_month_sampled = Counter(month_key(r["signal_date"]) for r in sampled)
    assert by_month_sampled["2022-08"] == 85, by_month_sampled   # capped
    assert by_month_sampled["2022-09"] == 50, by_month_sampled   # under cap, all kept
    assert by_month_sampled["2022-10"] == 10, by_month_sampled
    # determinism: same seed -> identical sample
    sampled2, _ = stratify_by_month_cap(bear_pool, cap=85, seed=42)
    assert sampled == sampled2, "stratify_by_month_cap is not deterministic under a fixed seed"
    sampled3, _ = stratify_by_month_cap(bear_pool, cap=85, seed=43)
    assert sampled3 != sampled, "different seeds produced an identical sample (suspicious)"

    bull_rows = ([{"symbol": f"B{i}", "signal_date": "2022-08-05", "overall": 80} for i in range(80)]
                + [{"symbol": f"B{i}", "signal_date": "2022-09-05", "overall": 80} for i in range(20)])
    shares = bull_monthly_shares(bull_rows)
    assert abs(shares["2022-08"] - 0.8) < 1e-9 and abs(shares["2022-09"] - 0.2) < 1e-9, shares
    ctrl_pool = ([{"symbol": f"C{i}", "signal_date": "2022-08-10", "overall": 50} for i in range(500)]
                + [{"symbol": f"C{i}", "signal_date": "2022-09-10", "overall": 50} for i in range(500)])
    ctrl_sampled = sample_ctrl(ctrl_pool, shares, total=100, seed=42)
    ctrl_by_month = Counter(month_key(r["signal_date"]) for r in ctrl_sampled)
    assert ctrl_by_month["2022-08"] == 80 and ctrl_by_month["2022-09"] == 20, ctrl_by_month
    ctrl_sampled2 = sample_ctrl(ctrl_pool, shares, total=100, seed=42)
    assert ctrl_sampled == ctrl_sampled2, "sample_ctrl is not deterministic under a fixed seed"
    log("  [5] stratify_by_month_cap / bull_monthly_shares / sample_ctrl OK "
        "(cap enforcement + determinism + date-matching)")

    # -- 6. build_full_path includes off=0; walk_meta forward-only -----------
    ms = int((datetime(2022, 9, 1) - datetime(1970, 1, 1)).total_seconds() * 1000)
    day = 86_400_000
    bars = [{"t": ms + i * day, "o": 1.0, "h": 1.2, "l": 0.9, "c": 1.0 + 0.1 * i,
             "v": (0 if i == 2 else 10 * (i + 1)), "vw": 1.05, "n": 3} for i in range(4)]
    by_date = bars_ohlc_by_date(bars)
    cal = ["2022-09-01", "2022-09-02", "2022-09-03", "2022-09-04", "2022-09-05"]
    tdi_t = {d_: i for i, d_ in enumerate(cal)}
    full = build_full_path(by_date, "2022-09-01", "2022-09-04", tdi_t)
    assert [b["off"] for b in full] == [0, 1, 2, 3], full
    fwd = [b for b in full if b["off"] > 0]
    assert [b["off"] for b in fwd] == [1, 2, 3], fwd
    expected_n = expected_forward_bars(cal, "2022-09-01", "2022-09-04")
    wmeta = walk_meta(fwd, expected_n)
    assert wmeta["bars_covered"] == 3, wmeta
    assert wmeta["stale_frac"] == 1 / 3, wmeta          # one zero-volume forward bar out of 3
    # entry bar (off=0) is present but never counted in bars_covered/stale_frac
    assert all(b["off"] != 0 for b in fwd)
    log("  [6] build_full_path (off=0 entry bar included) + walk_meta (forward-only) OK")

    # -- 7. miss taxonomy + pmcc_long min_forward_bars=1 + walk cap, offline --
    class FakeClient:
        calls = 0

        def __init__(self, dry_strikes=(), raise_403=False, nbars=20, nbars_by_strike=None,
                    ctypes=("call", "put")):
            self.dry = set(dry_strikes)
            self.raise_403 = raise_403
            self.nbars = nbars
            self.nbars_by_strike = nbars_by_strike or {}
            self.ctypes = ctypes

        def list_option_contracts(self, sym, as_of, exp_gte, exp_lte, contract_type=None, **kw):
            self.calls += 1
            base = _dt(as_of)
            out = []
            types = [contract_type] if contract_type else list(self.ctypes)
            for ctype in types:
                letter = "C" if ctype == "call" else "P"
                for dte in (12, 28, 35, 60, 270):
                    exp = base + timedelta(days=dte)
                    for k in (70, 75, 90, 95, 100, 105, 110):
                        out.append({
                            "ticker": f"O:{sym}{exp.strftime('%y%m%d')}{letter}{int(k * 1000):08d}",
                            "contract_type": ctype, "strike_price": float(k),
                            "expiration_date": exp.isoformat(), "shares_per_contract": 100})
            return out

        def option_daily_bars(self, ticker, start, end):
            self.calls += 1
            if self.raise_403:
                raise RuntimeError('Polygon 403: body={"status":"NOT_AUTHORIZED",'
                                   '"message":"Your plan doesn\'t include this data timeframe."}')
            # parse strike robustly regardless of call('C')/put('P') ticker
            m = re.search(r"[CP](\d{8})$", ticker)
            strike = int(m.group(1)) / 1000.0
            if strike in self.dry:
                return []
            n = self.nbars_by_strike.get(strike, self.nbars)
            base = _dt(start)
            rows = []
            px = 3.00
            for i in range(0, n):
                dd = base + timedelta(days=i)
                if i:
                    px *= 1.01
                t = int((datetime(dd.year, dd.month, dd.day) - datetime(1970, 1, 1)).total_seconds() * 1000)
                rows.append({"t": t, "o": px, "h": px * 1.05, "l": px * 0.96,
                            "c": px, "v": 250, "vw": px, "n": 40})
            return rows

    sig = {"symbol": "FAKE", "signal_date": "2022-09-01", "overall": 82,
          "entry_price": 101.0, "spot_unadj": 101.0, "spot_source": "yf_unadj",
          "adj_factor": 1.0, "vol": 2.5}

    # bull_put (put, d30 target 30): ATM (100) has bars -> kept
    # d15: 3 moneyness (1.00/0.95/0.90); d30 & d60: 5 each (base 3 + deeper-OTM
    # 0.85/0.80 extra, 2026-07-26 optimization round) -> 3 + 5 + 5 = 13.
    rows, paths_out = process_group_task(FakeClient(), "BULL_puts", sig, None, None, mult=1.82)
    bp = [r for r in rows if r["study_arm"] == "bull_put"]
    assert len(bp) == 13, len(bp)
    kept = [r for r in bp if r["status"] == "kept"]
    assert len(kept) == 13, [r["status"] for r in bp]
    assert all(r["contract_type"] == "put" for r in bp), bp
    d30_atm = [r for r in bp if r["dte_band"] == "d30" and r["target_moneyness"] == 1.00][0]
    assert d30_atm["strike"] == 100.0 and d30_atm["dte_cal"] == 28, d30_atm
    assert d30_atm["model_premium_pct"] is not None and d30_atm["model_premium_abs"] is not None, d30_atm
    d15_mns = sorted(r["target_moneyness"] for r in bp if r["dte_band"] == "d15")
    assert d15_mns == [0.90, 0.95, 1.00], d15_mns        # d15 excludes deep-OTM (too thin)
    d30_mns = sorted(r["target_moneyness"] for r in bp if r["dte_band"] == "d30")
    assert d30_mns == [0.80, 0.85, 0.90, 0.95, 1.00], d30_mns
    d60_mns = sorted(r["target_moneyness"] for r in bp if r["dte_band"] == "d60")
    assert d60_mns == [0.80, 0.85, 0.90, 0.95, 1.00], d60_mns
    for cid, bars in paths_out.items():
        assert bars[0]["off"] == 0, (cid, bars[0])       # entry bar included
    log("  [7] bull_put group (13 kept rows incl. deep-OTM d30/d60 0.85/0.80, "
        "put type, ATM strike/dte, model premium) OK")

    # BULL_calls group: pmcc_long (min_forward_bars=1, walk cap 60cal) + pmcc_short
    rows2, paths_out2 = process_group_task(FakeClient(nbars=200), "BULL_calls", sig, None, None, mult=1.82)
    pl_rows = [r for r in rows2 if r["study_arm"] == "pmcc_long"]
    ps_rows = [r for r in rows2 if r["study_arm"] == "pmcc_short"]
    assert len(pl_rows) == 1 and len(ps_rows) == 1, (pl_rows, ps_rows)
    assert pl_rows[0]["status"] == "kept" and pl_rows[0]["dte_band"] == "leaps", pl_rows[0]
    assert pl_rows[0]["dte_cal"] == 270, pl_rows[0]
    assert pl_rows[0]["moneyness"] < 1.0, pl_rows[0]     # deep ITM (0.75 target)
    # walk cap: signal+60cal, contract has 200 synthetic bars available -> path truncated to ~60d
    assert pl_rows[0]["path_end_date"] <= "2022-11-01", pl_rows[0]["path_end_date"]
    assert ps_rows[0]["status"] == "kept" and ps_rows[0]["dte_band"] == "d30", ps_rows[0]
    log("  [8] BULL_calls group (pmcc_long walk-cap-60cal + min_forward_bars=1, pmcc_short) OK")

    # min_forward_bars=1 actually matters: 1-bar contract is 'kept' for pmcc_long
    # but would be 'miss:too_few_bars' for a min_forward_bars=2 arm (pmcc_short).
    thin_client = FakeClient(nbars_by_strike={75.0: 2})   # 2 bars total = 1 forward bar
    rows3, _ = process_group_task(thin_client, "BULL_calls", sig, None, None, mult=1.82)
    pl3 = [r for r in rows3 if r["study_arm"] == "pmcc_long"][0]
    assert pl3["status"] == "kept" and pl3["bars_covered"] == 1, pl3
    log("  [9] pmcc_long min_forward_bars=1 accepts a 1-forward-bar contract OK")

    # CTRL_both: one un-filtered chain call serves ctrl_put (put) + ctrl_call (call).
    # ctrl_put d30 grid is now 3 (base 1.00/0.95 + deeper-OTM 0.85 extra).
    rows4, _ = process_group_task(FakeClient(), "CTRL_both", sig, None, None, mult=1.82)
    cp = [r for r in rows4 if r["study_arm"] == "ctrl_put"]
    cc = [r for r in rows4 if r["study_arm"] == "ctrl_call"]
    assert len(cp) == 3 and len(cc) == 1, (cp, cc)
    assert sorted(r["target_moneyness"] for r in cp) == [0.85, 0.95, 1.00], cp
    assert all(r["contract_type"] == "put" for r in cp), cp
    assert all(r["contract_type"] == "call" for r in cc), cc
    log("  [10] CTRL_both group (single chain, both types split correctly, "
        "ctrl_put deep-OTM 0.85 included) OK")

    # miss taxonomy: no_underlying / no_chain / no_dte_band / no_atm_price / timeframe_403
    sig_nospot = {"symbol": "FAKE", "signal_date": "2022-09-01", "overall": 50,
                 "entry_price": None, "spot_unadj": None}
    rows5, _ = process_group_task(FakeClient(), "CTRL_both", sig_nospot, None, None, mult=1.82)
    assert all(r["status"] == "miss:no_underlying" for r in rows5), rows5

    class NoChainClient(FakeClient):
        def list_option_contracts(self, *a, **kw):
            self.calls += 1
            return []
    rows6, _ = process_group_task(NoChainClient(), "CTRL_both", sig, None, None, mult=1.82)
    assert all(r["status"] == "miss:no_chain" for r in rows6), rows6

    class NoBandClient(FakeClient):
        def list_option_contracts(self, sym, as_of, exp_gte, exp_lte, contract_type=None, **kw):
            self.calls += 1
            base = _dt(as_of)
            exp = base + timedelta(days=200)              # outside d30's [21,45]
            types = [contract_type] if contract_type else ["call", "put"]
            return [{"ticker": f"O:{sym}{exp.strftime('%y%m%d')}"
                             f"{'C' if t == 'call' else 'P'}00100000",
                    "contract_type": t, "strike_price": 100.0,
                    "expiration_date": exp.isoformat(), "shares_per_contract": 100}
                   for t in types]
    rows7, _ = process_group_task(NoBandClient(), "CTRL_both", sig, None, None, mult=1.82)
    assert all(r["status"] == "miss:no_dte_band" for r in rows7), rows7

    rows8, _ = process_group_task(FakeClient(raise_403=True), "BULL_puts", sig, None, None, mult=1.82)
    assert all(r["status"] == "miss:timeframe_403" for r in rows8), rows8

    all_dry = FakeClient(dry_strikes=(100.0, 95.0, 105.0, 90.0, 110.0))
    rows9, _ = process_group_task(all_dry, "BULL_puts", sig, None, None, mult=1.82)
    assert any(r["status"] == "miss:no_atm_price" for r in rows9), rows9
    log("  [11] miss taxonomy (no_underlying/no_chain/no_dte_band/timeframe_403/no_atm_price) OK")

    # -- 12. journal resume: task_fully_done -----------------------------------
    done_set = set()
    for arm, mn, band in _EXPECTED_KEYS_CACHE["CTRL_both"]:
        done_set.add((arm, "FAKE", "2022-09-01", mn, band))
    assert task_fully_done(done_set, "CTRL_both", sig) is True
    done_set.discard(("ctrl_put", "FAKE", "2022-09-01", 0.95, "d30"))
    assert task_fully_done(done_set, "CTRL_both", sig) is False
    log("  [12] task_fully_done resumability check OK")

    # -- 13. schema emit: consolidate round-trip for BOTH parquets -------------
    import tempfile
    import shutil
    tmp = Path(tempfile.mkdtemp(prefix="short_premium_selftest_"))
    try:
        cprog = tmp / "_c.jsonl"
        cparq = tmp / "contracts.parquet"
        lines = [json.dumps(r, default=str) for r in bp]     # from step [7]
        # duplicate key with a different status -> last write wins
        dup = dict(bp[0]); dup["status"] = "miss:err:dup_test"
        lines.append(json.dumps(dup, default=str))
        cprog.write_text("\n".join(lines) + "\n", encoding="utf-8")
        n = consolidate_contracts(cprog, cparq)
        assert n == 13, n     # bp from step [7]: bull_put's 13-row grid (dup collapses, no growth)
        cdf = pl.read_parquet(cparq)
        assert cdf.columns == CONTRACTS_COLUMNS, cdf.columns
        assert cdf.schema["signal_date"] == pl.Date, cdf.schema["signal_date"]
        assert cdf.schema["expiration"] == pl.Date, cdf.schema["expiration"]
        assert cdf.schema["strike_rank"] == pl.Int8, cdf.schema["strike_rank"]
        assert cdf.schema["liquid_entry"] == pl.Boolean, cdf.schema["liquid_entry"]
        dup_row = cdf.filter((pl.col("study_arm") == bp[0]["study_arm"])
                             & (pl.col("target_moneyness") == bp[0]["target_moneyness"])
                             & (pl.col("dte_band") == bp[0]["dte_band"]))
        assert dup_row["status"].to_list() == ["miss:err:dup_test"], "dedupe (last write wins) failed"

        pprog = tmp / "_p.jsonl"
        pparq = tmp / "paths.parquet"
        any_cid, any_bars = next(iter(paths_out.items()))
        pprog.write_text(json.dumps({"contract_id": any_cid, "bars": any_bars}, default=str) + "\n",
                         encoding="utf-8")
        np_ = consolidate_paths(pprog, pparq)
        assert np_ == len(any_bars), np_
        pdf = pl.read_parquet(pparq)
        assert pdf.columns == PATHS_COLUMNS, pdf.columns
        assert pdf.schema["date"] == pl.Date, pdf.schema["date"]
        assert (pdf["off"] == 0).sum() == 1, "off=0 entry bar missing from paths output"

        # empty journals -> empty typed frames with the right columns
        empty_c = tmp / "empty_c.parquet"
        (tmp / "_ce.jsonl").write_text("", encoding="utf-8")
        assert consolidate_contracts(tmp / "_ce.jsonl", empty_c) == 0
        assert pl.read_parquet(empty_c).columns == CONTRACTS_COLUMNS
        empty_p = tmp / "empty_p.parquet"
        (tmp / "_pe.jsonl").write_text("", encoding="utf-8")
        assert consolidate_paths(tmp / "_pe.jsonl", empty_p) == 0
        assert pl.read_parquet(empty_p).columns == PATHS_COLUMNS
        log(f"  [13] consolidate round-trip OK (contracts {cdf.height} rows / "
            f"paths {pdf.height} bars, dedupe + typed schema + empty-journal)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    log("=== SELFTEST PASS ===")
    return 0


# ---------------------------------------------------------------------------
# main / CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Real Polygon short-premium contract ledger (bull_put / bear_call / "
                   "pmcc_long / pmcc_short / ctrl_put / ctrl_call)")
    ap.add_argument("--start", default=DEFAULT_START, help=f"signal window start (default {DEFAULT_START})")
    ap.add_argument("--end", default=date.today().isoformat(), help="signal window end (default today)")
    ap.add_argument("--version-id", type=int, default=VERSION_ID,
                    help=f"scoring AlgorithmVersion.id to pull signals from (default {VERSION_ID}, "
                         f"locked to v74 per PREREGISTRATION.md)")
    ap.add_argument("--bear-cap", type=int, default=BEAR_MONTH_CAP, help="BEAR stratified sample cap/month")
    ap.add_argument("--ctrl-total", type=int, default=CTRL_TOTAL, help="CTRL sample size")
    ap.add_argument("--seed", type=int, default=42, help="sampling RNG seed (BEAR + CTRL)")
    ap.add_argument("--symbols", default="", help="comma-separated subset filter")
    ap.add_argument("--workers", type=int, default=8, help="concurrent Polygon fetchers")
    ap.add_argument("--flush-every", type=int, default=200, help="consolidate every ~N rows written")
    ap.add_argument("--limit", type=int, default=0, help="process only the first N tasks (smoke)")
    ap.add_argument("--refresh-spot", action="store_true",
                    help="re-fetch _unadj_daily.parquet from yfinance instead of reusing the cache")
    ap.add_argument("--consolidate-only", action="store_true",
                    help="rebuild both parquets from the journals and exit (offline)")
    ap.add_argument("--selftest", action="store_true",
                    help="run offline unit tests and exit (no key, no network, no DB)")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.consolidate_only:
        consolidate_contracts()
        consolidate_paths()
        return 0
    return run_pull(args)


if __name__ == "__main__":
    raise SystemExit(main())
