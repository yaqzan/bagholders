"""
build_features.py -- weekly_5dte_movers, Stage B (BUILD_BRIEF.md).

Binding spec: PREREG.md (LOCKED) + BUILD_BRIEF.md. Read those first.

Joins underlying/indicator/score/market-context/earnings features onto each Stage A
ledger row (one row per (contract, entry_date) entry event). Two kinds of computation:

  1. F3 (returns/vol/dist-from-high-low) + F4 (MA ladder) + F5 (stored-indicator
     reconciliation) need a DENSE daily panel per symbol (rolling windows need
     continuity) -- built once as `underlying_panel.parquet`, then the ledger is
     left-joined onto it at (underlying=symbol, entry_date=date).
  2. F6 (score) / F7 (regime+breadth) / F8 (earnings+sector) are genuinely PER-ENTRY-EVENT
     features (PREREG's own framing: "Metric log -- per entry event"; F6 heading literally
     says "where the underlying is scored" for entry rows). These are pulled directly at
     the ledger's sparse (symbol, entry_date) grain, NOT for the full dense panel.
     DOCUMENTED DEVIATION from BUILD_BRIEF's literal "pull ... for the panel dates"
     wording (Stage B step 3): the Score table's `weight_info` column is large JSON TEXT
     (pre_regime/pre_boost live inside it, see BUILD_REPORT.md "Score pre_regime/
     pre_boost source"); pulling it for every trading day of a multi-thousand-symbol
     panel (vs. only the sparse ~400 distinct entry dates x covered symbols across the
     full window) is a large, avoidable MySQL/TextField cost with zero benefit -- nothing
     downstream needs a daily score *series*, only the entry-event snapshot. This is the
     "most conservative interpretation that keeps the PREREG semantics" the brief asks
     for when literal brief text and PREREG intent diverge.

MA/indicator formula fidelity: production computes stored ma_9/ema_9/.../ma_200/ema_200
via `talib.SMA`/`talib.EMA` over the symbol's FULL adjusted-close history
(database/models/core.py Stock.calculate_indicators, confirmed by direct read
2026-08-17: lines ~3799-3817). This module uses the SAME talib calls (not a hand-rolled
EMA) for the F4 ladder, over each symbol's panel-window close array, to make the F5
reconciliation as tight as possible -- the panel start date (2021-09-01) gives ~220
trading days of warmup before the archive itself begins (2022-08-01) and 1.5-4+ years
before any actual smoke entry date, which is why BUILD_BRIEF's 2% EMA tolerance
("seed differences") is expected to be comfortably met, not routinely hit.

CLI:
    python build_features.py --smoke     # <=10 curated symbols, isolated _smoke/ output
    python build_features.py --year YYYY # one ledger year (real output) -- NOT run here
    python build_features.py --full      # all years -- NOT run here
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
assert os.path.isfile(os.path.join(_ROOT, "CLAUDE.md")), \
    f"sys.path pin landed on {_ROOT!r}, expected the Trader repo root"

_FF_DIR = os.path.join(_ROOT, "experiments", "flatfile_exploitation")
if _FF_DIR not in sys.path:
    sys.path.insert(0, _FF_DIR)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import polars as pl  # noqa: E402
import talib  # noqa: E402

import ff_common  # noqa: E402

from experiments._holdout import CUTOFF, assert_no_holdout_leak  # noqa: E402
from experiments.weekly_5dte_movers.build_ledger import (  # noqa: E402
    LAST_EXPIRY, OUT_ROOT, LEDGER_DIR, LEDGER_SMOKE_DIR, SMOKE_LEDGER_PARQUET,
    week_monday_of, log, _atomic_write_parquet, _atomic_write_json,
)

# ---------------------------------------------------------------- constants

FEATURES_ROOT = OUT_ROOT / "features"
FEATURES_SMOKE_DIR = FEATURES_ROOT / "_smoke"
PANEL_SMOKE_PARQUET = FEATURES_SMOKE_DIR / "underlying_panel_smoke.parquet"
ANALYSIS_SMOKE_PARQUET = FEATURES_SMOKE_DIR / "analysis_smoke.parquet"
PANEL_PARQUET = FEATURES_ROOT / "underlying_panel.parquet"

PANEL_START = date(2020, 1, 1)   # warmup for 200d MAs -- BUILD_BRIEF Stage B step 1 says
# "2021-09-01 .. 2026-06-12 (warmup for 200d MAs)"; empirically (BUILD_REPORT.md "Panel
# warmup extension") that buffer (~460 trading days before the earliest smoke week) left
# EMA200 reconciliation at 2.55-2.59% for COIN (highest-realized-vol smoke symbol, IPO
# 2021-04-14) at the 2023-07-24/25 entries -- just over the brief's own 2% "seed
# differences" tolerance, while every other symbol/period/week was comfortably under.
# SMA reconciled to ~2e-5 regardless (no seed-memory term), proving the join/date-align/
# talib-call logic itself is correct -- this is purely an EMA warmup-length effect, and
# the brief's OWN stated purpose for this date ("warmup for 200d MAs") licenses widening
# it. Pushed back to 2020-01-01 (safely before COIN's actual listing, so COIN gets its
# full available history) -- verified empirically to bring the worst case to <0.6%. Pure
# implementation buffer: does not touch any PREREG-locked window (ledger entry weeks are
# still exactly 2022-08-01 .. expiry<=2026-06-12) or floor/threshold definition.
PANEL_END = LAST_EXPIRY          # 2026-06-12

MA_PERIODS = [5, 8, 9, 10, 12, 20, 21, 26, 34, 50, 100, 150, 200]
RECON_PERIODS = [9, 21, 50, 200]  # stored Indicator columns available for reconciliation

SMOKE_MAX_SYMBOLS = 10
SMOKE_ANCHOR_SYMBOL = "NVDA"

# Index/ETF-underlying flag: true index roots (no PriceHistory, ff_common.KNOWN_INDEX_ROOTS)
# PLUS major index-tracking ETFs, which DO have PriceHistory but are still "the market",
# not a single-name bet (PREREG F8 example list: "SPX/SPXW/SPY/QQQ/IWM etc.").
INDEX_ETF_ADDENDUM = frozenset({"SPY", "QQQ", "IWM", "DIA", "MDY"})
INDEX_OR_ETF_UNDERLYING = ff_common.KNOWN_INDEX_ROOTS | INDEX_ETF_ADDENDUM

SPY_SYMBOL = "SPY"


# ---------------------------------------------------------------- small numpy/pandas helpers

def _rolling_mean(x: np.ndarray, k: int) -> np.ndarray:
    return pd.Series(x).rolling(k, min_periods=k).mean().to_numpy()


def _rolling_std(x: np.ndarray, k: int) -> np.ndarray:
    return pd.Series(x).rolling(k, min_periods=k).std(ddof=1).to_numpy()


def _rolling_max(x: np.ndarray, k: int) -> np.ndarray:
    return pd.Series(x).rolling(k, min_periods=k).max().to_numpy()


def _rolling_min(x: np.ndarray, k: int) -> np.ndarray:
    return pd.Series(x).rolling(k, min_periods=k).min().to_numpy()


def _shift(x: np.ndarray, k: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan)
    if n > k:
        out[k:] = x[: n - k]
    return out


def _slope_5d(ma: np.ndarray) -> np.ndarray:
    prior = _shift(ma, 5)
    with np.errstate(invalid="ignore", divide="ignore"):
        return ma / prior - 1.0


def _days_since_cross(fast: np.ndarray, slow: np.ndarray, cap: int = 250) -> np.ndarray:
    """Signed days-since-crossover: positive while fast>slow (golden), negative while
    fast<=slow (death), magnitude = trading bars since the last sign flip, capped."""
    n = len(fast)
    out = np.full(n, np.nan)
    state = 0
    last_cross_idx = None
    for i in range(n):
        if np.isnan(fast[i]) or np.isnan(slow[i]):
            state = 0
            last_cross_idx = None
            continue
        new_state = 1 if fast[i] > slow[i] else -1
        if state == 0 or new_state != state:
            state = new_state
            last_cross_idx = i
        days = min(i - last_cross_idx, cap)
        out[i] = state * days
    return out


def _prior_week_return(dates: list, close: np.ndarray) -> np.ndarray:
    """Constant-per-week value: (last close of week W-1) / (last close of week W-2) - 1,
    using only FULLY COMPLETED prior weeks present in this symbol's own data (no
    look-ahead -- W itself, the current/in-progress week, is never used)."""
    n = len(dates)
    week_of_row = [week_monday_of(d) for d in dates]
    last_idx_per_week: dict = {}
    for i, wm in enumerate(week_of_row):
        last_idx_per_week[wm] = i  # ascending iteration -> ends up as the LAST index per week
    week_list = sorted(last_idx_per_week.keys())
    week_close = {wm: close[last_idx_per_week[wm]] for wm in week_list}
    week_pos = {wm: idx for idx, wm in enumerate(week_list)}
    out = np.full(n, np.nan)
    for i, wm in enumerate(week_of_row):
        pos = week_pos[wm]
        if pos >= 2:
            c1 = week_close[week_list[pos - 1]]
            c2 = week_close[week_list[pos - 2]]
            if c2:
                out[i] = c1 / c2 - 1.0
    return out


def mcap_bucket(mcap_usd) -> "str | None":
    if mcap_usd is None:
        return None
    v = float(mcap_usd)
    if v >= 200e9:
        return "mega"
    if v >= 10e9:
        return "large"
    if v >= 2e9:
        return "mid"
    if v >= 300e6:
        return "small"
    if v >= 50e6:
        return "micro"
    return "nano"


# ---------------------------------------------------------------- F3 + F4 per-symbol build

def compute_symbol_features(sym_df: pl.DataFrame) -> pl.DataFrame:
    """sym_df: one symbol's rows (symbol,date,open,high,low,close,close_unadj,volume),
    sorted ascending by date. Returns sym_df with all F3/F4 columns appended, plus the
    raw computed SMA/EMA arrays (prefixed `_computed_`) used for F5 reconciliation."""
    close = sym_df["close"].to_numpy().astype(np.float64)
    high = sym_df["high"].to_numpy().astype(np.float64)
    low = sym_df["low"].to_numpy().astype(np.float64)
    open_ = sym_df["open"].to_numpy().astype(np.float64)
    volume = sym_df["volume"].to_numpy().astype(np.float64)
    dates = sym_df["date"].to_list()
    n = len(close)

    out: dict = {}

    for k in (1, 2, 3, 5, 10, 20, 60):
        prior = _shift(close, k)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[f"ret_{k}d"] = close / prior - 1.0

    close_prev = _shift(close, 1)
    open_missing = np.isnan(open_)
    with np.errstate(invalid="ignore", divide="ignore"):
        gap_from_open = open_ / close_prev - 1.0
        gap_from_close = close / close_prev - 1.0
    out["gap_1d"] = np.where(open_missing, gap_from_close, gap_from_open)
    out["gap_1d_open_missing"] = open_missing.astype(np.int64)

    with np.errstate(invalid="ignore", divide="ignore"):
        log_ret = np.log(close / close_prev)
    for k in (5, 10, 20, 60):
        out[f"realized_vol_{k}d"] = _rolling_std(log_ret, k) * math.sqrt(252)

    tr = np.maximum(high - low, np.maximum(np.abs(high - close_prev), np.abs(low - close_prev)))
    atr14 = _rolling_mean(tr, 14)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["atr14_pct"] = atr14 / close * 100.0

    roll_high_20 = _rolling_max(high, 20)
    roll_low_20 = _rolling_min(low, 20)
    roll_high_252 = _rolling_max(high, 252)
    roll_low_252 = _rolling_min(low, 252)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["dist_from_20d_high"] = close / roll_high_20 - 1.0
        out["dist_from_20d_low"] = close / roll_low_20 - 1.0
        out["dist_from_52w_high"] = close / roll_high_252 - 1.0
        out["dist_from_52w_low"] = close / roll_low_252 - 1.0

    dollar_vol = close * volume
    dv20 = _rolling_mean(dollar_vol, 20)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["dollar_vol_20d_avg"] = dv20
        out["dollar_vol_ratio"] = dollar_vol / dv20

    out["prior_week_return"] = _prior_week_return(dates, close)

    ma_arrays: dict = {}
    for period in MA_PERIODS:
        sma = talib.SMA(close, timeperiod=period)
        ema = talib.EMA(close, timeperiod=period)
        ma_arrays[("sma", period)] = sma
        ma_arrays[("ema", period)] = ema
        with np.errstate(invalid="ignore", divide="ignore"):
            out[f"sma_{period}_pxrel"] = close / sma - 1.0
            out[f"ema_{period}_pxrel"] = close / ema - 1.0
        out[f"sma_{period}_slope5d"] = _slope_5d(sma)
        out[f"ema_{period}_slope5d"] = _slope_5d(ema)

    stack = np.zeros(n)
    for arr in ma_arrays.values():
        above = np.where(np.isnan(arr), 0.0, (close > arr).astype(np.float64))
        stack += above
    out["ma_stack_count"] = stack

    def _cross(a_key, b_key):
        a, b = ma_arrays[a_key], ma_arrays[b_key]
        with np.errstate(invalid="ignore"):
            r = (a > b).astype(np.float64)
        return np.where(np.isnan(a) | np.isnan(b), np.nan, r)

    out["cross_sma5_gt_sma20"] = _cross(("sma", 5), ("sma", 20))
    out["cross_sma10_gt_sma50"] = _cross(("sma", 10), ("sma", 50))
    out["cross_sma20_gt_sma50"] = _cross(("sma", 20), ("sma", 50))
    out["cross_sma50_gt_sma200"] = _cross(("sma", 50), ("sma", 200))
    out["cross_ema12_gt_ema26"] = _cross(("ema", 12), ("ema", 26))

    out["days_since_50_200_cross"] = _days_since_cross(ma_arrays[("sma", 50)], ma_arrays[("sma", 200)])

    for period in RECON_PERIODS:
        out[f"_computed_sma_{period}"] = ma_arrays[("sma", period)]
        out[f"_computed_ema_{period}"] = ma_arrays[("ema", period)]

    feat_df = pl.DataFrame(out)
    return pl.concat([sym_df, feat_df], how="horizontal")


# ---------------------------------------------------------------- MySQL pulls (raw SQL,
# matching house style in experiments/mcap_dampener/build_features.py and
# experiments/dynamic_tpsl/build_features.py -- DB.execute_sql, chunked by year).

def _sym_list_sql(symbols: list) -> str:
    return ", ".join("'" + s.replace("'", "''") + "'" for s in symbols)


def pull_covered_symbols(candidates: list) -> list:
    from database.trader_database import DB
    if not candidates:
        return []
    cur = DB.execute_sql(f"SELECT symbol FROM stocks WHERE symbol IN ({_sym_list_sql(candidates)})")
    return [r[0] for r in cur.fetchall()]


def pull_price_history(symbols: list, start: date, end: date) -> pl.DataFrame:
    from database.trader_database import DB
    if not symbols:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Date, "open": pl.Float64,
                                     "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
                                     "close_unadj": pl.Float64, "volume": pl.Int64})
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=300000")
    rows = []
    for yr in range(start.year, end.year + 1):
        y_start = max(date(yr, 1, 1), start).isoformat()
        y_end = min(date(yr, 12, 31), end).isoformat()
        cur = DB.execute_sql(
            f"SELECT symbol, date, open, high, low, close, close_unadj, volume "
            f"FROM price_history WHERE symbol IN ({_sym_list_sql(symbols)}) "
            f"AND date BETWEEN '{y_start}' AND '{y_end}'"
        )
        rows.extend(cur.fetchall())
    if not rows:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Date, "open": pl.Float64,
                                     "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
                                     "close_unadj": pl.Float64, "volume": pl.Int64})
    df = pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "date": [r[1] for r in rows],
        "open": [float(r[2]) if r[2] is not None else None for r in rows],
        "high": [float(r[3]) if r[3] is not None else None for r in rows],
        "low": [float(r[4]) if r[4] is not None else None for r in rows],
        "close": [float(r[5]) if r[5] is not None else None for r in rows],
        "close_unadj": [float(r[6]) if r[6] is not None else None for r in rows],
        "volume": [int(r[7]) if r[7] is not None else None for r in rows],
    })
    return df.sort(["symbol", "date"])


def pull_indicators(symbols: list, start: date, end: date) -> pl.DataFrame:
    from database.trader_database import DB
    cols = ["rsi", "macd_hist", "stoch", "upper_band", "middle_band", "lower_band",
            "ma_9", "ema_9", "ma_21", "ema_21", "ma_50", "ema_50", "ma_200", "ema_200"]
    schema = {"symbol": pl.Utf8, "date": pl.Date, **{c: pl.Float64 for c in cols}}
    if not symbols:
        return pl.DataFrame(schema=schema)
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=300000")
    rows = []
    for yr in range(start.year, end.year + 1):
        y_start = max(date(yr, 1, 1), start).isoformat()
        y_end = min(date(yr, 12, 31), end).isoformat()
        cur = DB.execute_sql(
            f"SELECT symbol, date, {', '.join(cols)} FROM indicators "
            f"WHERE symbol IN ({_sym_list_sql(symbols)}) AND date BETWEEN '{y_start}' AND '{y_end}'"
        )
        rows.extend(cur.fetchall())
    if not rows:
        return pl.DataFrame(schema=schema)
    data = {"symbol": [r[0] for r in rows], "date": [r[1] for r in rows]}
    for i, c in enumerate(cols, start=2):
        data[c] = [float(r[i]) if r[i] is not None else None for r in rows]
    return pl.DataFrame(data).sort(["symbol", "date"])


def pull_scores_sparse(symbols: list, entry_dates: list, version_id: int) -> pl.DataFrame:
    """Sparse pull: symbols x the (small) set of distinct entry dates that actually occur
    in the ledger -- see module docstring "DOCUMENTED DEVIATION". Over-fetches only the
    cartesian (symbol not-necessarily-entering on every date) product of a handful of
    dates, then callers inner-join back to the exact (symbol,entry_date) ledger pairs."""
    from database.trader_database import DB
    cols = ["bb", "trend", "volume", "rsi", "macd", "stoch", "ma20", "technical_alignment",
            "overall", "weight_info"]
    schema = {"symbol": pl.Utf8, "date": pl.Date,
              "score_bb": pl.Int64, "score_trend": pl.Int64, "score_volume": pl.Int64,
              "score_rsi": pl.Int64, "score_macd": pl.Int64, "score_stoch": pl.Int64,
              "score_ma20": pl.Int64, "score_technical_alignment": pl.Int64,
              "overall": pl.Int64, "pre_regime": pl.Int64, "pre_boost": pl.Int64}
    if not symbols or not entry_dates:
        return pl.DataFrame(schema=schema)
    date_list = ", ".join("'" + d.isoformat() + "'" for d in sorted(set(entry_dates)))
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=300000")
    cur = DB.execute_sql(
        f"SELECT symbol, date, {', '.join(cols)} FROM scores "
        f"WHERE version_id = {int(version_id)} AND symbol IN ({_sym_list_sql(symbols)}) "
        f"AND date IN ({date_list})"
    )
    rows = cur.fetchall()
    if not rows:
        return pl.DataFrame(schema=schema)
    out = {"symbol": [], "date": [], "score_bb": [], "score_trend": [], "score_volume": [],
           "score_rsi": [], "score_macd": [], "score_stoch": [], "score_ma20": [],
           "score_technical_alignment": [], "overall": [], "pre_regime": [], "pre_boost": []}
    for r in rows:
        symbol, d, bb, trend, vol, rsi, macd, stoch, ma20, ta, overall, weight_info = r
        out["symbol"].append(symbol)
        out["date"].append(d)
        out["score_bb"].append(bb)
        out["score_trend"].append(trend)
        out["score_volume"].append(vol)
        out["score_rsi"].append(rsi)
        out["score_macd"].append(macd)
        out["score_stoch"].append(stoch)
        out["score_ma20"].append(ma20)
        out["score_technical_alignment"].append(ta)
        out["overall"].append(overall)
        pre_regime = pre_boost = None
        if weight_info:
            try:
                parsed = json.loads(weight_info)
                pre_regime = parsed.get("pre_regime")
                pre_boost = parsed.get("pre_boost")
            except (json.JSONDecodeError, TypeError):
                pass
        out["pre_regime"].append(pre_regime)
        out["pre_boost"].append(pre_boost)
    return pl.DataFrame(out, schema=schema)


def pull_market_regime(start: date, end: date) -> pl.DataFrame:
    from database.trader_database import DB
    cur = DB.execute_sql(
        f"SELECT date, regime_composite, regime_multiplier, vix_close FROM market_regime "
        f"WHERE date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
    )
    rows = cur.fetchall()
    return pl.DataFrame({
        "date": [r[0] for r in rows],
        "regime_composite": [float(r[1]) if r[1] is not None else None for r in rows],
        "regime_multiplier": [float(r[2]) if r[2] is not None else None for r in rows],
        "vix_close": [float(r[3]) if r[3] is not None else None for r in rows],
    }, schema={"date": pl.Date, "regime_composite": pl.Float64, "regime_multiplier": pl.Float64,
               "vix_close": pl.Float64}) if rows else pl.DataFrame(
        schema={"date": pl.Date, "regime_composite": pl.Float64, "regime_multiplier": pl.Float64,
                "vix_close": pl.Float64})


def pull_market_breadth(start: date, end: date) -> pl.DataFrame:
    from database.trader_database import DB
    cur = DB.execute_sql(
        f"SELECT date, mcclellan_oscillator, trin, ad_diff FROM market_breadth "
        f"WHERE date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
    )
    rows = cur.fetchall()
    schema = {"date": pl.Date, "mcclellan_oscillator": pl.Float64, "trin": pl.Float64,
              "ad_diff": pl.Int64}
    if not rows:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame({
        "date": [r[0] for r in rows],
        "mcclellan_oscillator": [float(r[1]) if r[1] is not None else None for r in rows],
        "trin": [float(r[2]) if r[2] is not None else None for r in rows],
        "ad_diff": [int(r[3]) if r[3] is not None else None for r in rows],
    }, schema=schema)


def pull_earnings(symbols: list, end: date) -> pl.DataFrame:
    from database.trader_database import DB
    schema = {"symbol": pl.Utf8, "eff_date": pl.Date}
    if not symbols:
        return pl.DataFrame(schema=schema)
    cur = DB.execute_sql(
        f"SELECT symbol, date, effective_date FROM earnings_dates "
        f"WHERE symbol IN ({_sym_list_sql(symbols)}) AND date <= '{end.isoformat()}'"
    )
    rows = cur.fetchall()
    if not rows:
        return pl.DataFrame(schema=schema)
    out_symbol, out_eff = [], []
    for symbol, d, eff in rows:
        eff_use = eff if eff is not None else d
        if eff_use is None or eff_use > end:
            continue
        out_symbol.append(symbol)
        out_eff.append(eff_use)
    return pl.DataFrame({"symbol": out_symbol, "eff_date": out_eff}, schema=schema).sort(["symbol", "eff_date"])


def pull_stock_meta(symbols: list) -> pl.DataFrame:
    from database.trader_database import DB
    schema = {"symbol": pl.Utf8, "sector": pl.Utf8, "market_cap": pl.Float64}
    if not symbols:
        return pl.DataFrame(schema=schema)
    cur = DB.execute_sql(f"SELECT symbol, sector, market_cap FROM stocks WHERE symbol IN ({_sym_list_sql(symbols)})")
    rows = cur.fetchall()
    if not rows:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "sector": [r[1] for r in rows],
        "market_cap": [float(r[2]) if r[2] is not None else None for r in rows],
    }, schema=schema)


# ---------------------------------------------------------------- symbol selection (smoke)

def select_smoke_symbols(ledger_df: pl.DataFrame, max_symbols: int = SMOKE_MAX_SYMBOLS) -> list:
    """Deterministic: NVDA (owner-relevant anchor) + exactly one KNOWN_INDEX_ROOTS
    underlying (to exercise the covered=0 path) + the remaining slots filled by the
    highest-entry-count other underlyings across the 4 smoke weeks (tie-break: symbol
    ascending, for reproducibility)."""
    counts = (
        ledger_df.filter(pl.col("underlying") != SMOKE_ANCHOR_SYMBOL)
        .group_by("underlying").len()
        .sort(["len", "underlying"], descending=[True, False])
    )
    all_others = counts["underlying"].to_list()
    index_candidates = [u for u in all_others if u in ff_common.KNOWN_INDEX_ROOTS]

    chosen = [SMOKE_ANCHOR_SYMBOL]
    if index_candidates:
        chosen.append(index_candidates[0])
    else:
        log("WARNING: no KNOWN_INDEX_ROOTS underlying found in the smoke ledger; "
            "the covered=0 path will not be exercised by an index root.")

    for u in all_others:
        if len(chosen) >= max_symbols:
            break
        if u in chosen or u in ff_common.KNOWN_INDEX_ROOTS:
            continue
        chosen.append(u)

    return chosen[:max_symbols]


# ---------------------------------------------------------------- assembly

def build_underlying_panel(covered_symbols: list, start: date, end: date) -> pl.DataFrame:
    prices = pull_price_history(covered_symbols, start, end)
    if prices.height == 0:
        return prices

    per_symbol = []
    for sym in covered_symbols:
        sym_df = prices.filter(pl.col("symbol") == sym).sort("date")
        if sym_df.height == 0:
            continue
        per_symbol.append(compute_symbol_features(sym_df))
    panel = pl.concat(per_symbol, how="vertical") if per_symbol else prices

    indicators = pull_indicators(covered_symbols, start, end)
    panel = panel.join(indicators, on=["symbol", "date"], how="left")

    panel = panel.with_columns(
        pl.when((pl.col("upper_band") - pl.col("lower_band")) != 0)
        .then((pl.col("close") - pl.col("lower_band")) / (pl.col("upper_band") - pl.col("lower_band")))
        .otherwise(None)
        .alias("bb_pctb")
    )

    for period in RECON_PERIODS:
        for kind, stored_col in (("sma", f"ma_{period}"), ("ema", f"ema_{period}")):
            computed_col = f"_computed_{kind}_{period}"
            panel = panel.with_columns(
                pl.when(pl.col(stored_col).is_not_null() & (pl.col(stored_col) != 0) &
                        pl.col(computed_col).is_not_null())
                .then(((pl.col(computed_col) - pl.col(stored_col)) / pl.col(stored_col)).abs())
                .otherwise(None)
                .alias(f"recon_{kind}{period}_relerr")
            )

    return panel


def _asof_next_earnings(entry_syms: list, entry_dates: list, earn: pl.DataFrame) -> list:
    """Per-row scan (small N -- entry-event grain, not the dense panel): first eff_date
    strictly AFTER entry_date, for that symbol."""
    out = []
    grouped = {}
    if earn.height:
        for sym, sub in earn.group_by("symbol"):
            grouped[sym[0] if isinstance(sym, tuple) else sym] = sorted(sub["eff_date"].to_list())
    for sym, ed in zip(entry_syms, entry_dates):
        dates_ = grouped.get(sym, [])
        nxt = next((d for d in dates_ if d > ed), None)
        out.append(nxt)
    return out


def _asof_last_earnings(entry_syms: list, entry_dates: list, earn: pl.DataFrame) -> list:
    out = []
    grouped = {}
    if earn.height:
        for sym, sub in earn.group_by("symbol"):
            grouped[sym[0] if isinstance(sym, tuple) else sym] = sorted(sub["eff_date"].to_list())
    for sym, ed in zip(entry_syms, entry_dates):
        dates_ = grouped.get(sym, [])
        last = None
        for d in dates_:
            if d <= ed:
                last = d
            else:
                break
        out.append(last)
    return out


def build_analysis_frame(ledger_sub: pl.DataFrame, panel: pl.DataFrame, covered_symbols: list,
                          uncovered_symbols: list, version_id: int) -> tuple:
    ledger_sub = ledger_sub.with_columns(
        pl.col("underlying").is_in(covered_symbols).cast(pl.Int64).alias("covered")
    )

    panel_join_cols = [c for c in panel.columns if c not in ("symbol", "date") and not c.startswith("_computed_")]
    panel_for_join = panel.select(["symbol", "date"] + panel_join_cols).rename(
        {"symbol": "underlying", "date": "entry_date"}
    )
    joined = ledger_sub.join(panel_for_join, on=["underlying", "entry_date"], how="left")

    # F1 continuation: moneyness using close_unadj (AS-TRADED spot) -- never adjusted close.
    joined = joined.with_columns(
        pl.col("close_unadj").is_null().cast(pl.Int64).alias("spot_missing")
    )
    joined = joined.with_columns(
        pl.when(pl.col("close_unadj").is_not_null() & (pl.col("close_unadj") > 0))
        .then(pl.col("strike") / pl.col("close_unadj") - 1.0)
        .otherwise(None)
        .alias("moneyness_pct")
    )
    joined = joined.with_columns(
        pl.when(pl.col("moneyness_pct").is_null())
        .then(None)
        .when(pl.col("cp") == "C")
        .then(pl.col("moneyness_pct"))
        .otherwise(-pl.col("moneyness_pct"))
        .alias("otm_pct")
    )
    joined = joined.with_columns(
        pl.when(pl.col("close_unadj").is_not_null() & (pl.col("close_unadj") > 0))
        .then(pl.col("entry_close") / pl.col("close_unadj"))
        .otherwise(None)
        .alias("premium_over_spot")
    )

    # F6 score (sparse pull at entry-event grain -- see module docstring)
    entry_dates_needed = joined.filter(pl.col("covered") == 1)["entry_date"].unique().to_list()
    scores = pull_scores_sparse(covered_symbols, entry_dates_needed, version_id)
    scores_for_join = scores.rename({"symbol": "underlying", "date": "entry_date"})
    joined = joined.join(scores_for_join, on=["underlying", "entry_date"], how="left")
    joined = joined.with_columns(
        pl.when(pl.col("overall").is_null()).then(None)
        .when(pl.col("overall") >= 75).then(pl.lit("call"))
        .when(pl.col("overall") <= 25).then(pl.lit("put"))
        .otherwise(pl.lit("mid"))
        .alias("score_bucket")
    )

    # F7 regime + breadth (date-only, no symbol) -- cheap, pulled once for the full window.
    regime = pull_market_regime(PANEL_START, PANEL_END)
    breadth = pull_market_breadth(PANEL_START, PANEL_END)
    joined = joined.join(regime.rename({"date": "entry_date"}), on="entry_date", how="left")
    joined = joined.join(breadth.rename({"date": "entry_date"}), on="entry_date", how="left")

    # SPY 5/20d returns as market context (join by date only -- SPY may or may not be one
    # of the curated smoke symbols; pulled independently either way, negligible marginal cost).
    spy_panel = build_underlying_panel([SPY_SYMBOL], PANEL_START, PANEL_END)
    if spy_panel.height:
        spy_ctx = spy_panel.select(["date", "ret_5d", "ret_20d"]).rename(
            {"ret_5d": "spy_ret_5d", "ret_20d": "spy_ret_20d", "date": "entry_date"}
        )
        joined = joined.join(spy_ctx, on="entry_date", how="left")
    else:
        joined = joined.with_columns([pl.lit(None, dtype=pl.Float64).alias("spy_ret_5d"),
                                       pl.lit(None, dtype=pl.Float64).alias("spy_ret_20d")])

    # F8 earnings (per entry event, entry_date < eff_date <= expiry_day) + sector/mcap.
    earn = pull_earnings(covered_symbols, CUTOFF)
    rows = joined.select(["underlying", "entry_date", "expiry_day"]).to_dicts()
    syms = [r["underlying"] for r in rows]
    eds = [r["entry_date"] for r in rows]
    exps = [r["expiry_day"] for r in rows]
    next_earn = _asof_next_earnings(syms, eds, earn)
    last_earn = _asof_last_earnings(syms, eds, earn)
    earnings_in_window = [1 if (ne is not None and ne <= exp) else 0 for ne, exp in zip(next_earn, exps)]
    days_to_next = [(ne - ed).days if ne is not None else None for ne, ed in zip(next_earn, eds)]
    days_since_last = [(ed - le).days if le is not None else None for le, ed in zip(last_earn, eds)]
    joined = joined.with_columns([
        pl.Series("earnings_in_window", earnings_in_window, dtype=pl.Int64),
        pl.Series("days_to_next_earnings", days_to_next, dtype=pl.Int64),
        pl.Series("days_since_last_earnings", days_since_last, dtype=pl.Int64),
    ])

    meta = pull_stock_meta(covered_symbols)
    meta = meta.with_columns(
        pl.col("market_cap").map_elements(mcap_bucket, return_dtype=pl.Utf8).alias("mcap_snapshot_bucket")
    ).with_columns(pl.lit(1).alias("mcap_is_snapshot"))
    joined = joined.join(
        meta.select(["symbol", "sector", "mcap_snapshot_bucket", "mcap_is_snapshot"]).rename(
            {"symbol": "underlying"}),
        on="underlying", how="left",
    )
    joined = joined.with_columns(
        pl.col("underlying").is_in(list(INDEX_OR_ETF_UNDERLYING)).cast(pl.Int64).alias("index_or_etf_underlying")
    )

    return joined, scores


# ---------------------------------------------------------------- smoke driver

def run_smoke(verbose: bool = True) -> dict:
    t0 = time.time()
    if not SMOKE_LEDGER_PARQUET.exists():
        raise FileNotFoundError(
            f"{SMOKE_LEDGER_PARQUET} not found -- run build_ledger.py --smoke first"
        )
    ledger = pl.read_parquet(SMOKE_LEDGER_PARQUET)

    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    label = av.production_label
    assert label == "v74", f"expected active scores version label 'v74', got {label!r} (id={av.id})"
    if verbose:
        log(f"active scores version: {label} (id={av.id}, commit={av.git_commit})")

    smoke_symbols = select_smoke_symbols(ledger)
    covered_symbols = pull_covered_symbols(smoke_symbols)
    uncovered_symbols = [s for s in smoke_symbols if s not in covered_symbols]
    if verbose:
        log(f"smoke symbols ({len(smoke_symbols)}): {smoke_symbols}")
        log(f"  covered ({len(covered_symbols)}): {covered_symbols}")
        log(f"  uncovered/covered=0 ({len(uncovered_symbols)}): {uncovered_symbols}")

    panel = build_underlying_panel(covered_symbols, PANEL_START, PANEL_END)
    _atomic_write_parquet(panel, PANEL_SMOKE_PARQUET)

    ledger_sub = ledger.filter(pl.col("underlying").is_in(smoke_symbols))
    analysis, scores = build_analysis_frame(ledger_sub, panel, covered_symbols, uncovered_symbols, av.id)

    assert_no_holdout_leak(analysis.rename({"entry_date": "date"}) if "date" not in analysis.columns
                            else analysis, context="weekly_5dte_movers/build_features smoke")

    _atomic_write_parquet(analysis, ANALYSIS_SMOKE_PARQUET)

    # ---- reconciliation report (used by smoke_test.py hard asserts) ----
    # Measured at ENTRY-EVENT grain (unique covered underlying+entry_date pairs in the
    # analysis frame), NOT the raw dense panel. The raw panel's earliest rows are, by
    # construction, a fresh MA/EMA seed with little-to-no warmup (day 1 has zero history)
    # -- that is expected and uninformative; nothing in the ledger ever joins to those
    # dates. "100 sampled rows" in BUILD_BRIEF means the entry events actually under
    # analysis, which is what this samples (up to 100; smoke has 63 available).
    recon_source = (
        analysis.filter(pl.col("covered") == 1)
        .select(["underlying", "entry_date"] + [c for c in analysis.columns if c.startswith("recon_")])
        .unique(subset=["underlying", "entry_date"])
    )
    recon_sample = recon_source.sample(n=min(100, recon_source.height), seed=20260817, shuffle=True) \
        if recon_source.height else recon_source
    recon = {}
    for kind, periods in (("sma", [21, 50, 200]), ("ema", [21, 50, 200])):
        worst = {}
        for p in periods:
            col = f"recon_{kind}{p}_relerr"
            if col in recon_sample.columns:
                vals = recon_sample[col].drop_nulls()
                worst[p] = float(vals.max()) if vals.len() else None
            else:
                worst[p] = None
        recon[kind] = worst
    recon_n_sampled = recon_sample.height

    n_moneyness_null = analysis.filter(pl.col("covered") == 1)["moneyness_pct"].null_count()
    moneyness_vals = analysis.filter(pl.col("covered") == 1)["moneyness_pct"].drop_nulls()
    moneyness_in_band = moneyness_vals.filter((moneyness_vals > -0.9) & (moneyness_vals < 10)).len()
    moneyness_oob = moneyness_vals.len() - moneyness_in_band

    nvda_earn_row = analysis.filter(
        (pl.col("underlying") == "NVDA") & (pl.col("entry_date") == date(2024, 5, 20))
    )
    nvda_earn_flag = (
        int(nvda_earn_row["earnings_in_window"][0]) if nvda_earn_row.height else None
    )

    elapsed = time.time() - t0
    report = {
        "smoke_symbols": smoke_symbols,
        "covered_symbols": covered_symbols,
        "uncovered_symbols": uncovered_symbols,
        "panel_path": str(PANEL_SMOKE_PARQUET),
        "analysis_path": str(ANALYSIS_SMOKE_PARQUET),
        "panel_rows": panel.height,
        "analysis_rows": analysis.height,
        "analysis_rows_covered": int(analysis["covered"].sum()),
        "recon_worst_relerr": recon,
        "recon_n_sampled": recon_n_sampled,
        "moneyness_null_count": int(n_moneyness_null),
        "moneyness_oob_count": int(moneyness_oob),
        "moneyness_checked": int(moneyness_vals.len()),
        "nvda_earnings_in_window_20240520": nvda_earn_flag,
        "elapsed_seconds": round(elapsed, 2),
    }
    if verbose:
        log(f"SMOKE features DONE: panel={panel.height} rows, analysis={analysis.height} rows "
            f"({report['analysis_rows_covered']} covered) in {elapsed:.2f}s")
        log(f"  MA reconciliation worst relerr: {recon}")
        log(f"  moneyness: null={n_moneyness_null} oob={moneyness_oob} of {moneyness_vals.len()} checked")
        log(f"  NVDA 2024-05-20 earnings_in_window: {nvda_earn_flag}")
    return report


# ---------------------------------------------------------------- year / full drivers
# (implemented for the orchestrator's later queued run -- NOT executed by this build
# session; --smoke is the only mode exercised here per BUILD_BRIEF hard rule.)

def run_year(year: int, verbose: bool = True, panel: "pl.DataFrame | None" = None,
             covered_symbols: "list | None" = None) -> dict:
    """Standalone (--year): builds its own panel from this year's underlyings and writes
    PANEL_PARQUET. From run_full: receives the shared union-covered panel + covered list
    (orchestrator audit 2026-08-17 -- the per-year rebuild was 4-5x the needed MySQL load,
    and each year's write clobbered PANEL_PARQUET with a per-year symbol subset)."""
    ledger_path = LEDGER_DIR / f"ledger_{year}.parquet"
    if not ledger_path.exists():
        raise FileNotFoundError(f"{ledger_path} not found -- run build_ledger.py --year {year} first")
    ledger = pl.read_parquet(ledger_path)

    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()

    all_underlyings = sorted(ledger["underlying"].unique().to_list())
    if covered_symbols is None:
        covered_symbols = pull_covered_symbols(all_underlyings)
    uncovered_symbols = [s for s in all_underlyings if s not in covered_symbols]

    if panel is None:
        panel = build_underlying_panel(covered_symbols, PANEL_START, PANEL_END)
        _atomic_write_parquet(panel, PANEL_PARQUET)

    analysis, _ = build_analysis_frame(ledger, panel, covered_symbols, uncovered_symbols, av.id)
    # Same rename dance as run_smoke: the analysis frame's date column is `entry_date`,
    # and assert_no_holdout_leak hard-requires a `date` column on DataFrame input
    # (orchestrator audit 2026-08-17 -- without this the full run died AFTER the panel
    # build with "[holdout] ... cannot extract dates").
    assert_no_holdout_leak(
        analysis.rename({"entry_date": "date"}) if "date" not in analysis.columns else analysis,
        context=f"weekly_5dte_movers/build_features year={year}",
    )

    out_path = FEATURES_ROOT / f"analysis_{year}.parquet"
    _atomic_write_parquet(analysis, out_path)
    if verbose:
        log(f"YEAR {year} features DONE: {analysis.height} rows -- {out_path}")
    return {"year": year, "rows": analysis.height, "out_path": str(out_path)}


def run_full(verbose: bool = True) -> list:
    years = sorted(int(p.stem.split("_")[1]) for p in LEDGER_DIR.glob("ledger_*.parquet"))
    if not years:
        raise FileNotFoundError(
            f"no ledger_*.parquet under {LEDGER_DIR} -- run build_ledger.py --full first")
    union: set = set()
    for y in years:
        union |= set(
            pl.read_parquet(LEDGER_DIR / f"ledger_{y}.parquet", columns=["underlying"])
            ["underlying"].unique().to_list()
        )
    covered = pull_covered_symbols(sorted(union))
    if verbose:
        log(f"FULL: {len(union)} distinct underlyings across {len(years)} ledger years; "
            f"{len(covered)} covered")
    panel = build_underlying_panel(covered, PANEL_START, PANEL_END)
    _atomic_write_parquet(panel, PANEL_PARQUET)
    return [run_year(y, verbose=verbose, panel=panel, covered_symbols=covered) for y in years]


# ---------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--smoke", action="store_true", help="<=10 curated symbols, isolated _smoke output")
    ap.add_argument("--year", type=int, help="process one ledger year (real output)")
    ap.add_argument("--full", action="store_true", help="process all available ledger years")
    a = ap.parse_args()

    if a.smoke:
        run_smoke()
        return 0
    if a.year:
        run_year(a.year)
        return 0
    if a.full:
        run_full()
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
