"""short_premium/short_ledger.py -- per-trade P&L + frontier engine for the
SHORT-premium (option selling) exploration study. See PREREGISTRATION.md for
the locked design (hypotheses H1-H4, arms, exit-policy grid, margin model,
haircuts, metrics, holdout discipline). Touch conventions and the market
trading-day `off` semantics are inherited verbatim from
experiments/polygon_real_premium/DESIGN.md sections 3-4.

WHAT THIS BUILDS
----------------
Reads (real mode, --run):
  .cache/short_premium/contracts.parquet   -- one row per (arm, symbol, signal)
  .cache/short_premium/paths.parquet       -- per-contract daily OHLCV path
  .cache/short_premium/_unadj_daily.parquet -- full as-traded daily close per symbol

Writes:
  .cache/short_premium/cells.parquet   -- all cells (arm x moneyness x band x
                                           policy x haircut), all metrics
  .cache/short_premium/trades.parquet  -- per-trade resolved rows, PRIMARY
                                           haircut 0.90, all 9 exit policies

LOCKED MECHANICS (do not vary without updating PREREGISTRATION.md)
  * Short leg receives entry_premium_real * HAIRCUT at entry.
  * Exit-policy grid = 3 TP {none, tp50, tp75} x 3 SL {none, sl2x, sl3x} = 9.
    tp50: first forward bar (off>=1) LOW <= 0.50*entry -> buy back at exactly
          0.50*entry (resting limit, no extra cost). tp75: LOW <= 0.25*entry
          -> buy back at 0.25*entry.
    sl2x: first forward bar CLOSE >= 2.0*entry -> buy back at that bar's
          ACTUAL close x (2 - HAIRCUT) (forced EOD exit, pays the half-spread).
          sl3x analogous at 3.0x.
    SAME BAR: SL beats TP (conservative, locked).
    Neither fires -> hold to expiry, settle at INTRINSIC from the as-traded
    spot (no haircut -- settlement, not a market order).
  * Margin (locked broker formula, per share):
      put:  max(0.20*S - max(S-K,0), 0.10*K) + prem
      call: max(0.20*S - max(K-S,0), 0.10*S) + prem
    margin0 uses entry S0/prem (contracts.parquet's own spot_unadj/entry_premium_real).
    peak_margin marks the SAME formula daily along the path (S_t from
    _unadj_daily, prem = that day's option print if any else last print),
    from entry to this policy's exit date, and takes the max.
  * PMCC: long (0.75 moneyness, ~270cd LEAPS) pays entry*(2-HAIRCUT) at
    entry; short (1.05 moneyness, d30) resolves through the same exit grid;
    PMCC P&L = short-leg P&L + (long_mark - long_entry_cost); long_mark is
    the long leg's last print <= the short cycle's exit date (staleness
    flagged if > 10 market days old); capital at risk = long-leg debit.
  * Holdout (G15): ranking/primary metrics use signal_date <=
    CALIBRATION_CUTOFF_DATE (strategy_config.py); post-cutoff rows feed
    separate n_oos/ev_oos columns, never the ranking.

TRAPS HONORED
  G5   ASCII-only stdout, no unicode in prints.
  G7/G47 polars infer_schema_length=None on every raw-record DataFrame build;
         one fill_nan(None) choke point after load; finite-mask any array
         before it reaches a t-stat (never let a silent NaN read as a null
         verdict -- see clustered_mean_t's explicit n/finite guards).
  G49  cells are never ranked by raw t alone (--run summary gates on
       pre-cutoff EV with t as a screen); n_clusters < 20 -> t reported as
       NaN, not a fabricated number.
  G51  spot_unadj is the only honest anchor for strike/moneyness/settlement;
       entry_price (adjusted) is never used for those (this module doesn't
       even read entry_price -- it isn't needed for P&L).
  Worktree PYTHONPATH trap: repo root pinned at sys.path[0] before any
  project import, so this always imports the checkout's own strategy_config.

USAGE
-----
    python experiments/short_premium/short_ledger.py --selftest
    python experiments/short_premium/short_ledger.py --run [--arm bull_put]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]                     # short_premium -> experiments -> repo root
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))           # pin repo root FIRST (worktree PYTHONPATH trap)
assert (_ROOT / "strategy_config.py").exists(), (
    f"sys.path[0] pin didn't land on the repo root: {_ROOT}"
)

import numpy as np                            # noqa: E402
import polars as pl                           # noqa: E402

try:
    from strategy_config import CALIBRATION_CUTOFF_DATE as _CUTOFF_STR
except Exception:                             # pragma: no cover -- defensive only
    _CUTOFF_STR = "2026-06-15"

CACHE_DIR = _ROOT / ".cache" / "short_premium"
CONTRACTS_PATH = CACHE_DIR / "contracts.parquet"
PATHS_PATH = CACHE_DIR / "paths.parquet"
SPOTS_PATH = CACHE_DIR / "_unadj_daily.parquet"
CELLS_OUT = CACHE_DIR / "cells.parquet"
TRADES_OUT = CACHE_DIR / "trades.parquet"

# ---------------------------------------------------------------------------
# Locked grids
# ---------------------------------------------------------------------------
HAIRCUTS = [1.00, 0.90, 0.85, 0.75]
PRIMARY_HAIRCUT = 0.90

TP_GRID = {"none": None, "tp50": 0.50, "tp75": 0.25}
SL_GRID = {"none": None, "sl2x": 2.0, "sl3x": 3.0}
POLICIES = [
    (f"{tp_name}_{sl_name}", tp_frac, sl_mult)
    for tp_name, tp_frac in TP_GRID.items()
    for sl_name, sl_mult in SL_GRID.items()
]  # 9 combos

PREMIUM_FLOOR = 0.05          # ratio-metric eligibility floor (entry_premium_real can be 0.01)
MIN_CLUSTERS = 20             # G49 guard: below this, CR1 t is reported NaN not fabricated
STALE_MARK_DAYS = 10          # PMCC long-leg mark staleness threshold (market days)
ANNUALIZATION_DAYS = 252

_CUTOFF_DATE = _dt.date.fromisoformat(_CUTOFF_STR) if _CUTOFF_STR else _dt.date(2026, 6, 15)


def _log(msg: str) -> None:
    print(msg, flush=True)


# ===========================================================================
# Section 1 -- loading (G7/G47: infer_schema_length=None + one fill_nan choke)
# ===========================================================================

_FLOAT_LIKE_HINT = (
    "target_moneyness", "overall", "strike", "dte_cal", "spot_unadj", "adj_factor",
    "entry_price", "moneyness", "vol", "model_premium_pct", "model_premium_abs",
    "entry_premium_real", "entry_volume", "entry_trades", "entry_vwap",
    "bars_covered", "stale_frac", "open", "high", "low", "close", "volume",
    "trades", "vwap", "off",
)


def _fill_nan_choke(df: pl.DataFrame) -> pl.DataFrame:
    """One choke point: NaN (which passes is_not_null()) is normalized to a
    real null on every float column actually present. G7/G47."""
    float_cols = [c for c, dt in zip(df.columns, df.dtypes) if dt in (pl.Float32, pl.Float64)]
    if not float_cols:
        return df
    return df.with_columns([pl.col(c).fill_nan(None) for c in float_cols])


def load_frames():
    """Real-mode loader. Raises FileNotFoundError with a clear message if the
    upstream pull hasn't landed yet (this script never fabricates input)."""
    for p in (CONTRACTS_PATH, PATHS_PATH, SPOTS_PATH):
        if not p.exists():
            raise FileNotFoundError(
                f"missing input {p} -- the upstream data pull (parallel agent) "
                "hasn't produced it yet."
            )
    contracts = _fill_nan_choke(pl.read_parquet(CONTRACTS_PATH))
    paths = _fill_nan_choke(pl.read_parquet(PATHS_PATH))
    spots = _fill_nan_choke(pl.read_parquet(SPOTS_PATH))
    contracts = _normalize_dates(contracts, ["signal_date", "expiration", "path_end_date"])
    paths = _normalize_dates(paths, ["date"])
    spots = _normalize_dates(spots, ["date"])
    return contracts, paths, spots


def _normalize_dates(df: pl.DataFrame, cols) -> pl.DataFrame:
    exprs = []
    for c in cols:
        if c in df.columns and df.schema[c] != pl.Date:
            exprs.append(pl.col(c).cast(pl.Date))
    return df.with_columns(exprs) if exprs else df


# ===========================================================================
# Section 2 -- calendar (market-trading-day offsets, cf. polygon_real_premium
# DESIGN.md section 4: `off` = trading days elapsed against a calendar built
# from the union of daily close dates)
# ===========================================================================

def build_calendar(spots: pl.DataFrame) -> pl.DataFrame:
    """Global market-day calendar: union of every date present in the
    as-traded daily close series, with a dense integer index. Deviation note:
    the prereg doesn't specify how `days_held` to EXPIRY should be computed
    for contracts that never printed on the expiration date itself -- this
    module uses this same calendar (searchsorted-style, via an asof join) to
    stay consistent with the `off` convention already baked into
    paths.parquet for the TP/SL-exit case."""
    cal = spots.select(pl.col("date").unique()).sort("date")
    return cal.with_row_index("cal_idx")


def attach_calendar_idx(df: pl.DataFrame, date_col: str, calendar: pl.DataFrame, out_col: str) -> pl.DataFrame:
    """asof-backward join: out_col = the calendar index of the nearest
    trading day at-or-before date_col (robust to a weekend/holiday date that
    isn't itself a trading day)."""
    d = df.sort(date_col)
    cal = calendar.rename({"date": "__cal_date__"}).sort("__cal_date__")
    joined = d.join_asof(cal, left_on=date_col, right_on="__cal_date__", strategy="backward")
    return joined.rename({"cal_idx": out_col}).drop("__cal_date__")


# ===========================================================================
# Section 3 -- exit-bar resolution (vectorized polars: filter + first-per-group)
# ===========================================================================

def resolve_exit_bars(paths: pl.DataFrame, entry_prem: pl.DataFrame, tp_frac, sl_mult) -> pl.DataFrame:
    """For one (tp_frac, sl_mult) policy: first forward bar (off>=1) hitting
    TP and/or SL, SL winning a same-bar tie. Returns one row per contract_id
    that resolved before expiry; contracts absent from the result fall
    through to expiry settlement by construction (no explicit "none" row is
    emitted)."""
    df = paths.join(entry_prem, on="contract_id", how="inner").filter(pl.col("off") >= 1)
    tp_hit = (pl.col("low") <= tp_frac * pl.col("entry_premium_real")) if tp_frac is not None else pl.lit(False)
    sl_hit = (pl.col("close") >= sl_mult * pl.col("entry_premium_real")) if sl_mult is not None else pl.lit(False)
    df = df.with_columns([tp_hit.alias("tp_hit"), sl_hit.alias("sl_hit")])
    if tp_frac is None and sl_mult is None:
        # nothing can ever fire under the null policy -- short-circuit rather
        # than run an all-lit(False) filter (same result, cheaper, and avoids
        # a degenerate group_by on an empty frame in some polars versions).
        return df.clear().select(["contract_id", "off", "date", "close", "low"]).rename(
            {"off": "exit_off", "date": "exit_date", "close": "exit_close", "low": "exit_low"}
        ).with_columns(pl.lit(None, dtype=pl.Utf8).alias("exit_kind"))
    df = df.with_columns(
        pl.when(pl.col("sl_hit")).then(pl.lit("sl"))
          .when(pl.col("tp_hit")).then(pl.lit("tp"))
          .otherwise(None)
          .alias("exit_kind")
    )
    df = df.filter(pl.col("exit_kind").is_not_null()).sort(["contract_id", "off"])
    first = df.group_by("contract_id", maintain_order=True).first()
    return first.select(["contract_id", "off", "date", "exit_kind", "close", "low"]).rename(
        {"off": "exit_off", "date": "exit_date", "close": "exit_close", "low": "exit_low"}
    )


# ===========================================================================
# Section 4 -- expiry settlement (intrinsic from the as-traded spot series)
# ===========================================================================

def settle_expiry(rows: pl.DataFrame, spots: pl.DataFrame) -> pl.DataFrame:
    """rows: contract_id, symbol, contract_type, strike, expiration.
    Returns rows + expiry_spot, expiry_spot_date, resolve_fail. Spot lookup:
    exact expiration date, else last available <= expiration + 2 calendar
    days (both via a backward asof join per symbol -- locked fallback in the
    prereg)."""
    if rows.height == 0:
        return rows.with_columns([
            pl.lit(None, dtype=pl.Float64).alias("expiry_spot"),
            pl.lit(None, dtype=pl.Date).alias("expiry_spot_date"),
            pl.lit(False, dtype=pl.Boolean).alias("resolve_fail"),
        ])
    target = rows.with_columns((pl.col("expiration") + pl.duration(days=2)).alias("__lookup_to__"))
    sp = spots.select(["symbol", "date", "spot_unadj"]).sort(["symbol", "date"])
    target = target.sort(["symbol", "__lookup_to__"])
    joined = target.join_asof(
        sp, left_on="__lookup_to__", right_on="date", by="symbol", strategy="backward"
    )
    joined = joined.rename({"spot_unadj": "expiry_spot", "date": "expiry_spot_date"}).drop("__lookup_to__")
    joined = joined.with_columns(pl.col("expiry_spot").is_null().alias("resolve_fail"))
    return joined


# ===========================================================================
# Section 5 -- margin (entry + daily path-marked peak, vectorized via asof)
# ===========================================================================

def _margin_expr(spot_col: str, strike_col: str, prem_col: str, type_col: str) -> pl.Expr:
    put_term1 = 0.20 * pl.col(spot_col) - pl.max_horizontal(
        [pl.col(spot_col) - pl.col(strike_col), pl.lit(0.0)]
    )
    put_margin = pl.max_horizontal([put_term1, 0.10 * pl.col(strike_col)]) + pl.col(prem_col)
    call_term1 = 0.20 * pl.col(spot_col) - pl.max_horizontal(
        [pl.col(strike_col) - pl.col(spot_col), pl.lit(0.0)]
    )
    call_margin = pl.max_horizontal([call_term1, 0.10 * pl.col(spot_col)]) + pl.col(prem_col)
    return pl.when(pl.col(type_col) == "put").then(put_margin).otherwise(call_margin)


def compute_margin0(contracts: pl.DataFrame) -> pl.DataFrame:
    """Entry margin: uses the contract's own day-0 spot_unadj/entry_premium_real
    (already in contracts.parquet -- no join needed)."""
    return contracts.with_columns(
        _margin_expr("spot_unadj", "strike", "entry_premium_real", "contract_type").alias("margin0")
    )


def compute_peak_margin(exit_facts: pl.DataFrame, paths: pl.DataFrame, spots: pl.DataFrame) -> pl.DataFrame:
    """exit_facts needs: contract_id, symbol, signal_date, exit_date (this
    policy's resolution date), strike, contract_type, entry_premium_real.
    Vectorized via: explode the calendar-day range [signal_date, exit_date],
    inner-join to `spots` (drops non-trading days for free since spots only
    has trading-day rows) to get S_t, asof-join (backward) the option's own
    path to forward-fill the premium mark, then max the margin formula per
    contract. This is the polars-native realization of "PATH margin marked
    daily" -- no per-trade Python loop."""
    if exit_facts.height == 0:
        return pl.DataFrame({"contract_id": [], "peak_margin": []}).with_columns(
            pl.col("peak_margin").cast(pl.Float64)
        )
    ef = exit_facts.with_columns(
        pl.date_ranges(pl.col("signal_date"), pl.col("exit_date"), interval="1d").alias("date")
    ).explode("date")
    ef = ef.join(spots.select(["symbol", "date", "spot_unadj"]), on=["symbol", "date"], how="inner")
    px = paths.select(["contract_id", "date", "close"]).sort(["contract_id", "date"])
    ef = ef.sort(["contract_id", "date"])
    ef = ef.join_asof(px, on="date", by="contract_id", strategy="backward")
    ef = ef.with_columns(pl.col("close").fill_null(pl.col("entry_premium_real")).alias("prem_mark"))
    ef = ef.with_columns(
        _margin_expr("spot_unadj", "strike", "prem_mark", "contract_type").alias("margin_t")
    )
    return ef.group_by("contract_id").agg(pl.col("margin_t").max().alias("peak_margin"))


# ===========================================================================
# Section 6 -- assemble one policy's exit facts (haircut-independent) then
# expand across the haircut grid (cheap: 4 scalar passes on a small frame)
# ===========================================================================

def build_policy_exit_facts(contracts: pl.DataFrame, paths: pl.DataFrame, spots: pl.DataFrame,
                             calendar: pl.DataFrame, tp_frac, sl_mult) -> pl.DataFrame:
    entry_prem = contracts.select(["contract_id", "entry_premium_real"])
    exits = resolve_exit_bars(paths, entry_prem, tp_frac, sl_mult)
    joined = contracts.join(exits, on="contract_id", how="left")

    have_exit = joined.filter(pl.col("exit_kind").is_not_null())
    need_expiry = joined.filter(pl.col("exit_kind").is_null())

    have_exit = have_exit.with_columns([
        pl.col("exit_close").alias("buyback_fixed_sl"),
        (pl.col("exit_kind") == "sl").alias("haircut_dependent"),
        pl.col("exit_off").alias("days_held"),
        pl.col("exit_date").alias("resolved_date"),
    ])
    have_exit = have_exit.with_columns([
        pl.when(pl.col("exit_kind") == "tp").then(
            pl.lit(tp_frac if tp_frac is not None else 0.0) * pl.col("entry_premium_real")
        ).otherwise(pl.col("buyback_fixed_sl")).alias("buyback_fixed"),
    ]).drop("buyback_fixed_sl")

    if need_expiry.height:
        settled = settle_expiry(
            need_expiry.select(["contract_id", "symbol", "contract_type", "strike", "expiration"]),
            spots,
        )
        need_expiry = need_expiry.join(
            settled.select(["contract_id", "expiry_spot", "expiry_spot_date", "resolve_fail"]),
            on="contract_id", how="left",
        )
        intrinsic = pl.when(pl.col("contract_type") == "put").then(
            pl.max_horizontal([pl.col("strike") - pl.col("expiry_spot"), pl.lit(0.0)])
        ).otherwise(
            pl.max_horizontal([pl.col("expiry_spot") - pl.col("strike"), pl.lit(0.0)])
        )
        need_expiry = need_expiry.with_columns([
            intrinsic.alias("intrinsic"),
            pl.lit(False).alias("haircut_dependent"),
            pl.col("expiration").alias("resolved_date"),
        ])
        need_expiry = need_expiry.with_columns([
            pl.when(pl.col("resolve_fail")).then(None).otherwise(pl.col("intrinsic")).alias("buyback_fixed"),
            pl.when(pl.col("resolve_fail")).then(pl.lit("resolve_fail"))
              .when(pl.col("intrinsic") > 0).then(pl.lit("expiry_itm"))
              .otherwise(pl.lit("expiry_otm")).alias("exit_kind"),
        ])
        # calendar-based days_held to expiry (asof-index lookup, both ends)
        need_expiry = attach_calendar_idx(need_expiry, "signal_date", calendar, "__idx0__")
        need_expiry = attach_calendar_idx(need_expiry, "expiration", calendar, "__idx1__")
        need_expiry = need_expiry.with_columns(
            (pl.col("__idx1__") - pl.col("__idx0__")).cast(pl.Int64).alias("days_held")
        ).drop(["__idx0__", "__idx1__"])
    else:
        need_expiry = need_expiry.with_columns([
            pl.lit(None, dtype=pl.Float64).alias("buyback_fixed"),
            pl.lit(False, dtype=pl.Boolean).alias("haircut_dependent"),
            pl.lit(None, dtype=pl.Date).alias("resolved_date"),
            pl.lit(0, dtype=pl.Int64).alias("days_held"),
        ])

    common_cols = [
        "contract_id", "study_arm", "contract_type", "target_moneyness", "dte_band",
        "symbol", "signal_date", "strike", "spot_unadj", "entry_premium_real",
        "liquid_entry", "stale_frac", "exit_kind", "buyback_fixed", "haircut_dependent",
        "resolved_date", "days_held",
    ]
    for c in common_cols:
        if c not in have_exit.columns:
            have_exit = have_exit.with_columns(pl.lit(None).alias(c))
        if c not in need_expiry.columns:
            need_expiry = need_expiry.with_columns(pl.lit(None).alias(c))

    out = pl.concat([have_exit.select(common_cols), need_expiry.select(common_cols)], how="vertical")
    return out


def expand_haircuts(exit_facts: pl.DataFrame, haircuts=HAIRCUTS) -> pl.DataFrame:
    """Cheap fan-out: buyback cost only depends on HAIRCUT for 'sl' rows
    (pays the half-spread on a forced EOD exit); tp/expiry rows are
    haircut-independent on the buyback side. Premium RECEIVED always scales
    with HAIRCUT."""
    frames = []
    for hc in haircuts:
        f = exit_facts.with_columns([
            pl.lit(hc).alias("haircut"),
            (pl.col("entry_premium_real") * hc).alias("premium_received"),
        ])
        f = f.with_columns(
            pl.when(pl.col("haircut_dependent"))
              .then(pl.col("buyback_fixed") * (2.0 - hc))
              .otherwise(pl.col("buyback_fixed"))
              .alias("buyback_cost")
        )
        f = f.with_columns((pl.col("premium_received") - pl.col("buyback_cost")).alias("pnl_share"))
        frames.append(f)
    return pl.concat(frames, how="vertical")


# ===========================================================================
# Section 7 -- full single-leg build across all 9 policies
# ===========================================================================

def build_single_leg_trades(contracts: pl.DataFrame, paths: pl.DataFrame, spots: pl.DataFrame,
                             calendar: pl.DataFrame, arm_filter=None) -> pl.DataFrame:
    kept = contracts.filter(pl.col("status") == "kept")
    if arm_filter:
        kept = kept.filter(pl.col("study_arm") == arm_filter)
    kept = compute_margin0(kept)

    all_policy_frames = []
    for policy_label, tp_frac, sl_mult in POLICIES:
        facts = build_policy_exit_facts(kept, paths, spots, calendar, tp_frac, sl_mult)
        peak = compute_peak_margin(
            facts.filter(pl.col("exit_kind") != "resolve_fail").select(
                ["contract_id", "symbol", "signal_date", "strike", "contract_type",
                 "entry_premium_real", "resolved_date"]
            ).rename({"resolved_date": "exit_date"}),
            paths, spots,
        )
        facts = facts.join(peak, on="contract_id", how="left")
        facts = facts.join(
            kept.select(["contract_id", "margin0"]), on="contract_id", how="left"
        )
        facts = facts.with_columns(pl.lit(policy_label).alias("policy"))
        all_policy_frames.append(facts)

    facts_all = pl.concat(all_policy_frames, how="vertical")
    trades = expand_haircuts(facts_all, HAIRCUTS)

    trades = trades.filter(pl.col("exit_kind") != "resolve_fail")
    trades = trades.with_columns([
        (pl.col("pnl_share") * 100.0).alias("pnl_contract"),
        pl.when(pl.col("contract_type") == "put").then(pl.col("strike")).otherwise(None).alias("cash_secured"),
        pl.col("signal_date").dt.year().alias("year"),
        (pl.col("signal_date") <= _CUTOFF_DATE).alias("pre_cutoff"),
        (pl.col("premium_received") < PREMIUM_FLOOR).alias("premium_floored"),
    ])
    trades = trades.with_columns([
        (pl.col("pnl_share") / pl.col("margin0")).alias("ret_margin"),
        pl.when(pl.col("cash_secured").is_not_null() & (pl.col("cash_secured") > 0))
          .then(pl.col("pnl_share") / pl.col("cash_secured")).otherwise(None).alias("ret_cash"),
        (pl.col("pnl_share") / pl.max_horizontal([pl.col("premium_received"), pl.lit(PREMIUM_FLOOR)]))
          .alias("ret_premium"),
    ])
    return trades


# ===========================================================================
# Section 8 -- PMCC (paired long/short)
# ===========================================================================

def build_pmcc_trades(contracts: pl.DataFrame, paths: pl.DataFrame, spots: pl.DataFrame,
                       calendar: pl.DataFrame) -> pl.DataFrame:
    longs = contracts.filter((pl.col("study_arm") == "pmcc_long") & (pl.col("status") == "kept"))
    shorts = contracts.filter((pl.col("study_arm") == "pmcc_short") & (pl.col("status") == "kept"))
    if longs.height == 0 or shorts.height == 0:
        return pl.DataFrame()

    pairs = shorts.select([
        "contract_id", "symbol", "signal_date", "strike", "contract_type",
        "target_moneyness", "dte_band", "spot_unadj", "entry_premium_real", "expiration",
    ]).rename({c: f"short_{c}" for c in [
        "contract_id", "strike", "contract_type", "target_moneyness", "dte_band",
        "spot_unadj", "entry_premium_real", "expiration",
    ]}).join(
        longs.select([
            "contract_id", "symbol", "signal_date", "strike", "entry_premium_real",
        ]).rename({c: f"long_{c}" for c in ["contract_id", "strike", "entry_premium_real"]}),
        on=["symbol", "signal_date"], how="inner",
    )
    if pairs.height == 0:
        return pl.DataFrame()

    short_only = pairs.select(["short_contract_id"]).rename({"short_contract_id": "contract_id"}).join(
        shorts.rename({"contract_id": "__cid__"}).rename({"__cid__": "contract_id"}), on="contract_id",
    )
    kept_short = compute_margin0(short_only)

    all_frames = []
    for policy_label, tp_frac, sl_mult in POLICIES:
        facts = build_policy_exit_facts(kept_short, paths, spots, calendar, tp_frac, sl_mult)
        peak = compute_peak_margin(
            facts.filter(pl.col("exit_kind") != "resolve_fail").select(
                ["contract_id", "symbol", "signal_date", "strike", "contract_type",
                 "entry_premium_real", "resolved_date"]
            ).rename({"resolved_date": "exit_date"}),
            paths, spots,
        )
        facts = facts.join(peak, on="contract_id", how="left")
        facts = facts.join(kept_short.select(["contract_id", "margin0"]), on="contract_id", how="left")
        facts = facts.with_columns(pl.lit(policy_label).alias("policy"))
        facts = facts.rename({"contract_id": "short_contract_id"})
        all_frames.append(facts)
    short_facts = pl.concat(all_frames, how="vertical")
    short_trades = expand_haircuts(short_facts, HAIRCUTS)
    short_trades = short_trades.filter(pl.col("exit_kind") != "resolve_fail")

    full = short_trades.join(
        pairs.select(["short_contract_id", "long_contract_id", "long_strike", "long_entry_premium_real"]),
        on="short_contract_id", how="inner",
    )

    long_px = paths.select(["contract_id", "date", "close"]).rename(
        {"contract_id": "long_contract_id", "close": "long_mark"}
    ).sort(["long_contract_id", "date"])
    full = full.sort(["long_contract_id", "resolved_date"])
    full = full.join_asof(
        long_px, left_on="resolved_date", right_on="date", by="long_contract_id", strategy="backward"
    )
    full = full.with_columns(pl.col("long_mark").fill_null(pl.col("long_entry_premium_real")))

    long_dates = paths.select(["contract_id", "date"]).rename(
        {"contract_id": "long_contract_id", "date": "long_mark_date"}
    ).sort(["long_contract_id", "long_mark_date"])
    full = full.join_asof(
        long_dates, left_on="resolved_date", right_on="long_mark_date", by="long_contract_id", strategy="backward"
    )

    full = attach_calendar_idx(full, "resolved_date", calendar, "__idx_exit__")
    full = attach_calendar_idx(full, "long_mark_date", calendar, "__idx_mark__")
    full = full.with_columns(
        (pl.col("__idx_exit__") - pl.col("__idx_mark__")).alias("mark_staleness_days")
    ).drop(["__idx_exit__", "__idx_mark__"])
    full = full.with_columns(
        (pl.col("mark_staleness_days") > STALE_MARK_DAYS).fill_null(True).alias("stale_mark")
    )

    full = full.with_columns([
        (pl.col("long_entry_premium_real") * (2.0 - pl.col("haircut"))).alias("long_entry_cost"),
    ])
    full = full.with_columns([
        (pl.col("long_mark") - pl.col("long_entry_cost")).alias("long_leg_pnl"),
        pl.lit("pmcc").alias("study_arm"),
    ])
    full = full.with_columns([
        (pl.col("pnl_share") + pl.col("long_leg_pnl")).alias("pmcc_pnl_share"),
        pl.col("long_entry_cost").alias("pmcc_capital"),
        pl.col("signal_date").dt.year().alias("year"),
        (pl.col("signal_date") <= _CUTOFF_DATE).alias("pre_cutoff"),
    ])
    full = full.with_columns(
        (pl.col("pmcc_pnl_share") / pl.col("pmcc_capital")).alias("ret_margin")
    )
    full = full.rename({"short_contract_id": "contract_id"})
    full = full.with_columns([
        pl.lit(1.05).alias("target_moneyness"),
        pl.lit("pmcc_leaps_d30").alias("dte_band"),
    ])
    return full


# ===========================================================================
# Section 9 -- CR1 date-clustered t-stat (one-sample: mean(y) vs 0)
# ===========================================================================

def _cluster_sandwich_se(bread, score, clusters, n, k):
    """Copied (pattern) from experiments/peak_fakeout/mine.py -- the repo's
    existing CR1 clustered-sandwich implementation, specialized here to the
    one-regressor (intercept-only) case."""
    _, inv = np.unique(clusters, return_inverse=True)
    G = int(inv.max()) + 1 if n else 0
    sums = np.zeros((G, k))
    np.add.at(sums, inv, score)
    meat = sums.T @ sums
    corr = (G / max(G - 1, 1)) * ((n - 1) / max(n - k, 1))
    cov = corr * (bread @ meat @ bread)
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    return se, G


def clustered_mean_t(y, clusters, min_clusters=MIN_CLUSTERS):
    """One-sample CR1-clustered t-test of mean(y) vs 0, clustered by
    `clusters` (signal_date). Equivalent to an intercept-only OLS with a
    cluster-robust sandwich SE. G47: never let a NaN in y silently produce a
    null verdict -- non-finite rows are masked out explicitly and counted,
    not passed through."""
    y = np.asarray(y, dtype=float)
    finite = np.isfinite(y)
    n_dropped = int((~finite).sum())
    y = y[finite]
    clusters = np.asarray(clusters)[finite]
    n = y.size
    if n == 0:
        return dict(t=float("nan"), mean=float("nan"), se=float("nan"), n=0, n_clusters=0,
                     n_dropped_nonfinite=n_dropped, method="empty")
    X = np.ones((n, 1))
    XtX = X.T @ X
    bread = np.linalg.pinv(XtX)
    beta = bread @ (X.T @ y)
    mean = float(beta[0])
    resid = y - X @ beta
    score = X * resid[:, None]
    se, G = _cluster_sandwich_se(bread, score, clusters, n, 1)
    se0 = float(se[0]) if se.size and np.isfinite(se[0]) else float("nan")
    if G < min_clusters or not np.isfinite(se0) or se0 <= 1e-12:
        return dict(t=float("nan"), mean=mean, se=se0, n=n, n_clusters=int(G),
                     n_dropped_nonfinite=n_dropped, method="degenerate_or_few_clusters")
    t = mean / se0
    if not np.isfinite(t):
        return dict(t=float("nan"), mean=mean, se=se0, n=n, n_clusters=int(G),
                     n_dropped_nonfinite=n_dropped, method="nonfinite_t")
    return dict(t=float(t), mean=mean, se=se0, n=n, n_clusters=int(G),
                n_dropped_nonfinite=n_dropped, method="ok")


# ===========================================================================
# Section 10 -- cell metrics
# ===========================================================================

def _pct(a, q):
    a = a[np.isfinite(a)]
    return float(np.percentile(a, q)) if a.size else float("nan")


def _metrics_for_group(g: pl.DataFrame) -> dict:
    ret_margin = g["ret_margin"].to_numpy()
    ret_cash = g["ret_cash"].drop_nulls().to_numpy() if "ret_cash" in g.columns else np.array([])
    ret_premium = g["ret_premium"].to_numpy()
    pnl = g["pnl_share"].to_numpy()
    days = g["days_held"].to_numpy().astype(float)
    dates = g["signal_date"].to_numpy()
    years = g["year"].to_numpy()

    fin_rm = np.isfinite(ret_margin)
    n = int(fin_rm.sum())
    win_rate = float(np.mean(pnl[np.isfinite(pnl)] > 0)) if np.isfinite(pnl).any() else float("nan")
    mean_days = float(np.nanmean(days)) if np.isfinite(days).any() else float("nan")
    mean_pnl = float(np.nanmean(pnl)) if np.isfinite(pnl).any() else float("nan")
    ev_per_day = mean_pnl / mean_days if (mean_days and mean_days > 0 and np.isfinite(mean_pnl)) else float("nan")
    mean_rm = float(np.nanmean(ret_margin[fin_rm])) if fin_rm.any() else float("nan")
    ann_roi = mean_rm * (ANNUALIZATION_DAYS / mean_days) if (mean_days and mean_days > 0 and np.isfinite(mean_rm)) else float("nan")

    ct = clustered_mean_t(ret_margin, dates)

    per_year = {}
    for yr in sorted(set(int(y) for y in years if y is not None and not (isinstance(y, float) and math.isnan(y)))):
        m = years == yr
        vals = ret_margin[m]
        vals = vals[np.isfinite(vals)]
        if vals.size:
            per_year[str(yr)] = dict(mean=float(np.mean(vals)), n=int(vals.size), sign=int(np.sign(np.mean(vals))))
    signs = [v["sign"] for v in per_year.values() if v["n"] >= 5]
    sign_stability = (
        float(sum(1 for s in signs if s == (1 if mean_rm >= 0 else -1)) / len(signs))
        if signs else float("nan")
    )

    if "premium_floored" in g.columns:
        _floored_sum = g["premium_floored"].fill_null(False).sum()
        n_floored = int(_floored_sum) if _floored_sum is not None else 0
    else:
        n_floored = 0

    return dict(
        n=n,
        win_rate=win_rate,
        mean_ret_margin=mean_rm,
        median_ret_margin=float(np.nanmedian(ret_margin[fin_rm])) if fin_rm.any() else float("nan"),
        p05_ret_margin=_pct(ret_margin[fin_rm], 5) if fin_rm.any() else float("nan"),
        p01_ret_margin=_pct(ret_margin[fin_rm], 1) if fin_rm.any() else float("nan"),
        worst_ret_margin=float(np.nanmin(ret_margin[fin_rm])) if fin_rm.any() else float("nan"),
        mean_ret_cash=float(np.nanmean(ret_cash)) if ret_cash.size else float("nan"),
        median_ret_cash=float(np.nanmedian(ret_cash)) if ret_cash.size else float("nan"),
        mean_ret_premium=float(np.nanmean(ret_premium)) if np.isfinite(ret_premium).any() else float("nan"),
        median_ret_premium=float(np.nanmedian(ret_premium)) if np.isfinite(ret_premium).any() else float("nan"),
        mean_days_held=mean_days,
        ev_per_day=ev_per_day,
        annualized_roi_proxy=ann_roi,
        t_clust=ct["t"], t_n_clusters=ct["n_clusters"], t_n_dropped_nonfinite=ct["n_dropped_nonfinite"],
        per_year_json=json.dumps(per_year),
        sign_stability=sign_stability,
        n_premium_floored=n_floored,
    )


def compute_coverage(contracts: pl.DataFrame) -> pl.DataFrame:
    keys = ["study_arm", "target_moneyness", "dte_band"]
    attempted = contracts.group_by(keys).agg(pl.len().alias("n_attempted"))
    kept = contracts.filter(pl.col("status") == "kept")
    kept_agg = kept.group_by(keys).agg([
        pl.len().alias("n_kept"),
        pl.col("liquid_entry").mean().alias("liquid_share"),
        pl.col("stale_frac").mean().alias("mean_stale_frac"),
    ])
    cov = attempted.join(kept_agg, on=keys, how="left")
    return cov


def compute_cells(trades: pl.DataFrame, arm_col="study_arm") -> pl.DataFrame:
    keys = [arm_col, "target_moneyness", "dte_band", "policy", "haircut"]
    pre = trades.filter(pl.col("pre_cutoff"))
    post = trades.filter(~pl.col("pre_cutoff"))

    rows = []
    for key_vals, g in pre.group_by(keys, maintain_order=True):
        m = _metrics_for_group(g)
        row = dict(zip(keys, key_vals))
        row.update(m)
        rows.append(row)
    cells = pl.DataFrame(rows) if rows else pl.DataFrame({k: [] for k in keys})

    if post.height:
        oos = post.group_by(keys).agg([
            pl.len().alias("n_oos"),
            pl.col("ret_margin").mean().alias("ev_oos"),
        ])
        cells = cells.join(oos, on=keys, how="left")
    else:
        cells = cells.with_columns([
            pl.lit(0, dtype=pl.Int64).alias("n_oos"),
            pl.lit(None, dtype=pl.Float64).alias("ev_oos"),
        ])
    return cells


# ===========================================================================
# Section 11 -- ASCII summary
# ===========================================================================

def print_summary(cells: pl.DataFrame, trades: pl.DataFrame, coverage: pl.DataFrame) -> None:
    _log("=" * 78)
    _log("SHORT PREMIUM LEDGER -- SUMMARY")
    _log("=" * 78)

    _log("\n-- coverage by arm/moneyness/band --")
    if coverage.height:
        cov = coverage.sort(["study_arm", "target_moneyness", "dte_band"])
        for r in cov.iter_rows(named=True):
            n_att = r.get("n_attempted") or 0
            n_kept = r.get("n_kept") or 0
            pct = (100.0 * n_kept / n_att) if n_att else 0.0
            liq = r.get("liquid_share")
            stale = r.get("mean_stale_frac")
            _log(f"  {r['study_arm']:<12} moneyness={r['target_moneyness']:<6} "
                 f"band={str(r['dte_band']):<8} kept={n_kept}/{n_att} ({pct:5.1f}%) "
                 f"liquid={liq:.2f} stale_frac={stale:.2f}" if liq is not None and stale is not None
                 else f"  {r['study_arm']:<12} moneyness={r['target_moneyness']:<6} "
                      f"band={str(r['dte_band']):<8} kept={n_kept}/{n_att} ({pct:5.1f}%)")

    primary = cells.filter(pl.col("haircut") == PRIMARY_HAIRCUT) if "haircut" in cells.columns else cells
    if primary.height:
        gated = primary.filter(
            (pl.col("t_clust").abs() >= 2.0) & (pl.col("n") >= 30)
        ).sort("mean_ret_margin", descending=True)
        _log(f"\n-- top cells by pre-cutoff EV-on-margin, gated at |t_clust|>=2 & n>=30 "
             f"(haircut={PRIMARY_HAIRCUT}) --")
        if gated.height == 0:
            _log("  (none clear the gate)")
        for r in gated.head(20).iter_rows(named=True):
            _log(f"  arm={r['study_arm']:<10} m={r['target_moneyness']:<5} band={str(r['dte_band']):<8} "
                 f"policy={r['policy']:<14} n={r['n']:>5} WR={r['win_rate']:.2f} "
                 f"EV/margin={r['mean_ret_margin']:+.4f} t={r['t_clust']:+.2f} "
                 f"ann_roi={r['annualized_roi_proxy']:+.3f} n_oos={r.get('n_oos', 0)} "
                 f"ev_oos={r.get('ev_oos')}")

    _log("\n-- H1-H4 preliminary reads --")
    for arm, hyp in [("bull_put", "H1"), ("bear_call", "H2"), ("pmcc", "H3")]:
        sub = primary.filter(pl.col("study_arm") == arm) if primary.height else primary
        if sub.height == 0:
            _log(f"  {hyp} ({arm}): no cells (arm not present in this ledger build)")
            continue
        best = sub.sort("mean_ret_margin", descending=True).head(1)
        r = best.row(0, named=True) if best.height else None
        if r is None:
            _log(f"  {hyp} ({arm}): no rows")
        else:
            clears = (abs(r["t_clust"]) >= 2.0) and (r["n"] >= 30) if math.isfinite(r["t_clust"]) else False
            _log(f"  {hyp} ({arm}): best cell EV/margin={r['mean_ret_margin']:+.4f} "
                 f"t={r['t_clust']:+.2f} n={r['n']} -> {'CLEARS bar' if clears else 'does not clear bar'}")
    if primary.height:
        bull = primary.filter(pl.col("study_arm") == "bull_put")
        ctrl = primary.filter(pl.col("study_arm") == "ctrl_put")
        if bull.height and ctrl.height:
            bull_ev = bull["mean_ret_margin"].mean()
            ctrl_ev = ctrl["mean_ret_margin"].mean()
            _log(f"  H4 (signal_lift, put): bull_put mean EV/margin={bull_ev:+.4f} "
                 f"vs ctrl_put={ctrl_ev:+.4f} -> lift={bull_ev - ctrl_ev:+.4f}")
    _log("=" * 78)


# ===========================================================================
# Section 12 -- run mode
# ===========================================================================

def run(arm_filter=None) -> None:
    _log(f"[short_ledger] loading from {CACHE_DIR}")
    contracts, paths, spots = load_frames()
    calendar = build_calendar(spots)

    is_pmcc_only = arm_filter in ("pmcc", "pmcc_long", "pmcc_short")
    frames = []
    if not is_pmcc_only:
        single_arms = [a for a in ("bull_put", "bear_call", "ctrl_put", "ctrl_call")
                        if arm_filter is None or arm_filter == a]
        for a in single_arms:
            if contracts.filter(pl.col("study_arm") == a).height == 0:
                continue
            _log(f"[short_ledger] building single-leg trades for arm={a}")
            frames.append(build_single_leg_trades(contracts, paths, spots, calendar, arm_filter=a))
    if arm_filter is None or is_pmcc_only:
        _log("[short_ledger] building PMCC trades")
        pmcc = build_pmcc_trades(contracts, paths, spots, calendar)
        if pmcc.height:
            frames.append(pmcc)

    if not frames:
        _log("[short_ledger] no trades resolved -- nothing to write")
        return

    # Canonical schema for the unified trades table. PMCC uses different
    # column names for its P&L/capital (pmcc_pnl_share/pmcc_capital rather
    # than pnl_share/margin0) since capital-at-risk there is the long-leg
    # debit, not a margin requirement -- normalize both onto the same
    # columns here so compute_cells / the output schema doesn't need to know
    # which arm produced the row.
    CANON_COLS = [
        "contract_id", "study_arm", "symbol", "signal_date", "target_moneyness", "dte_band",
        "policy", "haircut", "pnl_share", "pnl_contract", "margin0", "cash_secured", "peak_margin",
        "days_held", "exit_kind", "ret_margin", "ret_cash", "ret_premium", "premium_floored",
        "year", "pre_cutoff",
    ]

    normalized = []
    for f in frames:
        g = f
        if "pmcc_pnl_share" in g.columns:   # PMCC frame
            g = g.with_columns([
                pl.col("pmcc_pnl_share").alias("pnl_share"),
                (pl.col("pmcc_pnl_share") * 100.0).alias("pnl_contract"),
                pl.col("pmcc_capital").alias("margin0"),
            ])
        for c in CANON_COLS:
            if c not in g.columns:
                g = g.with_columns(pl.lit(None).alias(c))
        normalized.append(g.select(CANON_COLS))

    all_trades = pl.concat(normalized, how="diagonal")
    all_trades = all_trades.rename({"exit_kind": "exit_reason"})

    cells = compute_cells(all_trades)
    coverage_src = contracts if arm_filter is None else contracts.filter(
        pl.col("study_arm").is_in(
            [arm_filter, "pmcc_long", "pmcc_short"] if is_pmcc_only else [arm_filter]
        )
    )
    coverage = compute_coverage(coverage_src)
    join_keys = [c for c in ["study_arm", "target_moneyness", "dte_band"]
                 if c in cells.columns and c in coverage.columns]
    cells = cells.join(coverage, on=join_keys, how="left")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cells.write_parquet(CELLS_OUT)
    _log(f"[short_ledger] wrote {CELLS_OUT} ({cells.height} cells)")

    primary_trades = all_trades.filter(pl.col("haircut") == PRIMARY_HAIRCUT)
    primary_trades.write_parquet(TRADES_OUT)
    _log(f"[short_ledger] wrote {TRADES_OUT} ({primary_trades.height} trades, haircut={PRIMARY_HAIRCUT})")

    print_summary(cells, all_trades, coverage)


# ===========================================================================
# Section 13 -- selftest (synthetic data, no network/DB/parquet needed)
# ===========================================================================

def _mk_date(y, m, d):
    return _dt.date(y, m, d)


def _build_synth_calendar(n_days=400, start=_dt.date(2022, 1, 3)):
    """Weekday-only synthetic trading calendar."""
    days = []
    d = start
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d = d + _dt.timedelta(days=1)
    return days


def selftest() -> bool:
    results = {}
    cal_days = _build_synth_calendar(500)

    def date_at(off_from_start):
        return cal_days[off_from_start]

    # -----------------------------------------------------------------
    # (a) tp50 fills before sl2x (first-in-time, different bars)
    # -----------------------------------------------------------------
    entry = 1.00
    contracts_a = pl.DataFrame({
        "contract_id": ["A1"], "study_arm": ["bull_put"], "contract_type": ["put"],
        "target_moneyness": [1.00], "dte_band": ["d30"], "symbol": ["ZZZ"],
        "signal_date": [date_at(0)], "strike": [100.0], "spot_unadj": [100.0],
        "entry_premium_real": [entry], "liquid_entry": [True], "stale_frac": [0.0],
        "status": ["kept"], "expiration": [date_at(20)],
    })
    paths_a = pl.DataFrame({
        "contract_id": ["A1", "A1", "A1"],
        "off": [0, 1, 2],
        "date": [date_at(0), date_at(1), date_at(2)],
        "open": [entry, 0.5, 2.0], "high": [entry, 0.55, 2.6],
        "low": [entry, 0.45, 1.9], "close": [entry, 0.60, 2.5],
        "volume": [10, 10, 10], "trades": [1, 1, 1], "vwap": [entry, 0.5, 2.2],
    })
    facts_a = build_policy_exit_facts(
        contracts_a, paths_a,
        pl.DataFrame({"symbol": ["ZZZ"], "date": [date_at(0)], "spot_unadj": [100.0]}),
        pl.DataFrame({"date": cal_days}).with_row_index("cal_idx"),
        tp_frac=0.50, sl_mult=2.0,
    )
    row_a = facts_a.row(0, named=True)
    ok_a = (row_a["exit_kind"] == "tp" and row_a["days_held"] == 1
            and abs(row_a["buyback_fixed"] - 0.50 * entry) < 1e-9)
    results["a_tp50_before_sl2x"] = ok_a
    _log(f"[selftest a] exit_kind={row_a['exit_kind']} days_held={row_a['days_held']} "
         f"buyback_fixed={row_a['buyback_fixed']} -> {'PASS' if ok_a else 'FAIL'}")

    # -----------------------------------------------------------------
    # (b) sl2x same-bar beats tp
    # -----------------------------------------------------------------
    paths_b = pl.DataFrame({
        "contract_id": ["B1"], "off": [1], "date": [date_at(1)],
        "open": [entry], "high": [2.3], "low": [0.10], "close": [2.2],
        "volume": [10], "trades": [1], "vwap": [1.0],
    })
    contracts_b = contracts_a.with_columns(pl.lit("B1").alias("contract_id"))
    facts_b = build_policy_exit_facts(
        contracts_b, paths_b,
        pl.DataFrame({"symbol": ["ZZZ"], "date": [date_at(0)], "spot_unadj": [100.0]}),
        pl.DataFrame({"date": cal_days}).with_row_index("cal_idx"),
        tp_frac=0.50, sl_mult=2.0,
    )
    row_b = facts_b.row(0, named=True)
    ok_b = (row_b["exit_kind"] == "sl" and abs(row_b["buyback_fixed"] - 2.2) < 1e-9)
    results["b_sl_beats_tp_same_bar"] = ok_b
    _log(f"[selftest b] exit_kind={row_b['exit_kind']} buyback_fixed={row_b['buyback_fixed']} "
         f"-> {'PASS' if ok_b else 'FAIL'}")
    hc = 0.90
    sl_cost = row_b["buyback_fixed"] * (2.0 - hc)
    pnl_b = entry * hc - sl_cost
    ok_b2 = abs(sl_cost - 2.2 * 1.10) < 1e-9
    results["b_sl_haircut_arithmetic"] = ok_b2
    _log(f"[selftest b2] sl_cost={sl_cost:.4f} (expect {2.2*1.10:.4f}) pnl={pnl_b:.4f} "
         f"-> {'PASS' if ok_b2 else 'FAIL'}")

    # -----------------------------------------------------------------
    # (c) expiry ITM put settle from spot (assigned loss)
    # -----------------------------------------------------------------
    contracts_c = pl.DataFrame({
        "contract_id": ["C1"], "study_arm": ["bull_put"], "contract_type": ["put"],
        "target_moneyness": [1.00], "dte_band": ["d30"], "symbol": ["ZZZ"],
        "signal_date": [date_at(0)], "strike": [100.0], "spot_unadj": [100.0],
        "entry_premium_real": [2.0], "liquid_entry": [True], "stale_frac": [0.0],
        "status": ["kept"], "expiration": [date_at(10)],
    })
    paths_c = pl.DataFrame({
        "contract_id": ["C1"], "off": [1], "date": [date_at(1)],
        "open": [2.0], "high": [2.1], "low": [1.9], "close": [1.95],
        "volume": [10], "trades": [1], "vwap": [2.0],
    })
    spots_c = pl.DataFrame({
        "symbol": ["ZZZ", "ZZZ"], "date": [date_at(0), date_at(10)],
        "spot_unadj": [100.0, 90.0],
    })
    facts_c = build_policy_exit_facts(
        contracts_c, paths_c, spots_c,
        pl.DataFrame({"date": cal_days}).with_row_index("cal_idx"),
        tp_frac=None, sl_mult=None,
    )
    row_c = facts_c.row(0, named=True)
    # put strike 100, spot 90 -> intrinsic = 10, ITM
    ok_c = (row_c["exit_kind"] == "expiry_itm" and abs(row_c["buyback_fixed"] - 10.0) < 1e-9)
    results["c_expiry_itm_put"] = ok_c
    _log(f"[selftest c] exit_kind={row_c['exit_kind']} intrinsic={row_c['buyback_fixed']} "
         f"-> {'PASS' if ok_c else 'FAIL'}")

    # -----------------------------------------------------------------
    # (d) expiry OTM full premium capture
    # -----------------------------------------------------------------
    contracts_d = contracts_c.with_columns(pl.lit("D1").alias("contract_id"))
    paths_d = paths_c.with_columns(pl.lit("D1").alias("contract_id"))
    spots_d = pl.DataFrame({
        "symbol": ["ZZZ", "ZZZ"], "date": [date_at(0), date_at(10)],
        "spot_unadj": [100.0, 110.0],
    })
    facts_d = build_policy_exit_facts(
        contracts_d, paths_d, spots_d,
        pl.DataFrame({"date": cal_days}).with_row_index("cal_idx"),
        tp_frac=None, sl_mult=None,
    )
    row_d = facts_d.row(0, named=True)
    ok_d = (row_d["exit_kind"] == "expiry_otm" and abs(row_d["buyback_fixed"] - 0.0) < 1e-9)
    results["d_expiry_otm_full_capture"] = ok_d
    _log(f"[selftest d] exit_kind={row_d['exit_kind']} buyback_fixed={row_d['buyback_fixed']} "
         f"-> {'PASS' if ok_d else 'FAIL'}")

    # -----------------------------------------------------------------
    # (e) haircut arithmetic both legs (short receive 0.90x, PMCC long pays 1.10x)
    # -----------------------------------------------------------------
    exit_facts_e = facts_d.with_columns(pl.lit(False).alias("haircut_dependent"))
    trades_e = expand_haircuts(exit_facts_e, [0.90])
    prem_recv = trades_e.row(0, named=True)["premium_received"]
    ok_e1 = abs(prem_recv - 2.0 * 0.90) < 1e-9
    long_entry = 5.0
    long_cost = long_entry * (2.0 - 0.90)
    ok_e2 = abs(long_cost - 5.5) < 1e-9
    ok_e = ok_e1 and ok_e2
    results["e_haircut_both_legs"] = ok_e
    _log(f"[selftest e] premium_received={prem_recv:.4f} (expect {2.0*0.90:.4f}) "
         f"long_cost={long_cost:.4f} (expect 5.5) -> {'PASS' if ok_e else 'FAIL'}")

    # -----------------------------------------------------------------
    # (f) margin formula OTM/ITM cases (put + call)
    # -----------------------------------------------------------------
    def margin_put_manual(s, k, p):
        return max(0.20 * s - max(s - k, 0.0), 0.10 * k) + p

    def margin_call_manual(s, k, p):
        return max(0.20 * s - max(k - s, 0.0), 0.10 * s) + p

    cf = pl.DataFrame({
        "spot_unadj": [110.0, 90.0, 90.0, 110.0],
        "strike": [100.0, 100.0, 100.0, 100.0],
        "entry_premium_real": [1.0, 1.0, 1.0, 1.0],
        "contract_type": ["put", "put", "call", "call"],
    })
    cf = cf.with_columns(_margin_expr("spot_unadj", "strike", "entry_premium_real", "contract_type").alias("m"))
    got = cf["m"].to_list()
    expect = [
        margin_put_manual(110, 100, 1.0),   # OTM put
        margin_put_manual(90, 100, 1.0),    # ITM put
        margin_call_manual(90, 100, 1.0),   # OTM call
        margin_call_manual(110, 100, 1.0),  # ITM call
    ]
    ok_f = all(abs(g - e) < 1e-9 for g, e in zip(got, expect))
    results["f_margin_formula"] = ok_f
    _log(f"[selftest f] margins got={[round(x,4) for x in got]} expect={[round(x,4) for x in expect]} "
         f"-> {'PASS' if ok_f else 'FAIL'}")

    # -----------------------------------------------------------------
    # (g) PMCC pairing + long mark staleness
    # -----------------------------------------------------------------
    long_contracts = pl.DataFrame({
        "contract_id": ["L1"], "study_arm": ["pmcc_long"], "contract_type": ["call"],
        "target_moneyness": [0.75], "dte_band": ["leaps"], "symbol": ["ZZZ"],
        "signal_date": [date_at(0)], "strike": [75.0], "spot_unadj": [100.0],
        "entry_premium_real": [26.0], "liquid_entry": [True], "stale_frac": [0.0],
        "status": ["kept"], "expiration": [date_at(200)],
    })
    short_contracts = pl.DataFrame({
        "contract_id": ["S1"], "study_arm": ["pmcc_short"], "contract_type": ["call"],
        "target_moneyness": [1.05], "dte_band": ["d30"], "symbol": ["ZZZ"],
        "signal_date": [date_at(0)], "strike": [105.0], "spot_unadj": [100.0],
        "entry_premium_real": [1.50], "liquid_entry": [True], "stale_frac": [0.0],
        "status": ["kept"], "expiration": [date_at(20)],
    })
    contracts_g = pl.concat([long_contracts, short_contracts], how="vertical")
    # long leg prints only through off=5, then goes silent (illiquid LEAPS)
    long_path_dates = [date_at(off) for off in range(0, 6)]
    paths_long = pl.DataFrame({
        "contract_id": ["L1"] * len(long_path_dates),
        "off": list(range(0, 6)),
        "date": long_path_dates,
        "open": [26.0] * 6, "high": [26.5] * 6, "low": [25.5] * 6,
        "close": [26.0, 26.2, 26.4, 26.6, 26.8, 27.0],
        "volume": [10] * 6, "trades": [1] * 6, "vwap": [26.0] * 6,
    })
    # short leg exits via expiry with no TP/SL hit at off=20 (~30cd)
    short_path_dates = [date_at(0), date_at(1)]
    paths_short = pl.DataFrame({
        "contract_id": ["S1", "S1"], "off": [0, 1], "date": short_path_dates,
        "open": [1.50, 1.55], "high": [1.55, 1.60], "low": [1.45, 1.50],
        "close": [1.50, 1.55], "volume": [10, 10], "trades": [1, 1], "vwap": [1.5, 1.55],
    })
    paths_g = pl.concat([paths_long, paths_short], how="vertical")
    spots_g = pl.DataFrame({
        "symbol": ["ZZZ", "ZZZ"], "date": [date_at(0), date_at(20)],
        "spot_unadj": [100.0, 108.0],   # short call ITM -> assigned, doesn't matter for staleness check
    })
    cal_g = pl.DataFrame({"date": cal_days}).with_row_index("cal_idx")
    pmcc_g = build_pmcc_trades(contracts_g, paths_g, spots_g, cal_g)
    row_g = pmcc_g.filter(pl.col("haircut") == 0.90).filter(pl.col("policy") == "none_none").row(0, named=True)
    # short exits at off=20 (expiration), long's last print is off=5 -> gap = 15 market days > 10
    ok_g1 = row_g["stale_mark"] is True
    ok_g2 = abs(row_g["long_mark"] - 27.0) < 1e-9   # last long print before exit
    ok_g = ok_g1 and ok_g2
    results["g_pmcc_pairing_staleness"] = ok_g
    _log(f"[selftest g] long_mark={row_g['long_mark']} mark_staleness_days={row_g['mark_staleness_days']} "
         f"stale_mark={row_g['stale_mark']} -> {'PASS' if ok_g else 'FAIL'}")

    # -----------------------------------------------------------------
    # (h) clustered-t on constructed two-cluster (25-cluster) dataset, known answer
    # -----------------------------------------------------------------
    rng = np.random.RandomState(20260726)
    G, m = 25, 8
    mu_g = rng.normal(loc=0.02, scale=0.05, size=G)   # cluster-level means, no within-cluster noise
    y_h = np.repeat(mu_g, m)
    dates_h = np.repeat(np.arange(G), m)
    got_h = clustered_mean_t(y_h, dates_h, min_clusters=20)
    expect_t = float(np.mean(mu_g) / (np.std(mu_g, ddof=1) / math.sqrt(G)))
    ok_h = abs(got_h["t"] - expect_t) < 1e-6 and got_h["n_clusters"] == G
    results["h_clustered_t_known_answer"] = ok_h
    _log(f"[selftest h] got_t={got_h['t']:.6f} expect_t={expect_t:.6f} n_clusters={got_h['n_clusters']} "
         f"-> {'PASS' if ok_h else 'FAIL'}")
    # sub-case: below min_clusters -> NaN not fabricated
    got_h2 = clustered_mean_t(y_h[:8 * 5], dates_h[:8 * 5], min_clusters=20)
    ok_h2 = math.isnan(got_h2["t"]) and got_h2["n_clusters"] == 5
    results["h2_min_clusters_guard"] = ok_h2
    _log(f"[selftest h2] n_clusters=5 t={got_h2['t']} -> {'PASS' if ok_h2 else 'FAIL'} (expect NaN, guarded)")
    # sub-case: NaN in y must not silently pass
    y_bad = y_h.copy()
    y_bad[0] = np.nan
    got_h3 = clustered_mean_t(y_bad, dates_h, min_clusters=20)
    ok_h3 = got_h3["n_dropped_nonfinite"] == 1 and got_h3["n"] == len(y_h) - 1 and np.isfinite(got_h3["t"])
    results["h3_nan_masked_explicitly"] = ok_h3
    _log(f"[selftest h3] n_dropped_nonfinite={got_h3['n_dropped_nonfinite']} n={got_h3['n']} "
         f"t={got_h3['t']:.4f} -> {'PASS' if ok_h3 else 'FAIL'}")

    # -----------------------------------------------------------------
    # (i) holdout split
    # -----------------------------------------------------------------
    pre_d = _CUTOFF_DATE - _dt.timedelta(days=10)
    post_d = _CUTOFF_DATE + _dt.timedelta(days=10)
    trades_i = pl.DataFrame({
        "contract_id": ["I1", "I2", "I3"],
        "study_arm": ["bull_put"] * 3, "target_moneyness": [1.00] * 3, "dte_band": ["d30"] * 3,
        "policy": ["none_none"] * 3, "haircut": [0.90] * 3,
        "signal_date": [pre_d, pre_d, post_d],
        "pnl_share": [1.0, 2.0, 5.0], "days_held": [10, 10, 10],
        "ret_margin": [0.10, 0.20, 0.90], "ret_cash": [None, None, None],
        "ret_premium": [0.20, 0.40, 1.80],
        "premium_floored": [False, False, False], "year": [pre_d.year, pre_d.year, post_d.year],
        "pre_cutoff": [pre_d <= _CUTOFF_DATE, pre_d <= _CUTOFF_DATE, post_d <= _CUTOFF_DATE],
    })
    cells_i = compute_cells(trades_i)
    r_i = cells_i.row(0, named=True)
    ok_i = (r_i["n"] == 2 and abs(r_i["mean_ret_margin"] - 0.15) < 1e-9
            and r_i["n_oos"] == 1 and abs(r_i["ev_oos"] - 0.90) < 1e-9)
    results["i_holdout_split"] = ok_i
    _log(f"[selftest i] n={r_i['n']} mean_ret_margin={r_i['mean_ret_margin']} n_oos={r_i['n_oos']} "
         f"ev_oos={r_i['ev_oos']} -> {'PASS' if ok_i else 'FAIL'}")

    _log("\n" + "=" * 60)
    all_ok = all(results.values())
    for k, v in results.items():
        _log(f"  {k}: {'PASS' if v else 'FAIL'}")
    _log("=" * 60)
    _log(f"SELFTEST {'PASS' if all_ok else 'FAIL'} ({sum(results.values())}/{len(results)})")
    return all_ok


# ===========================================================================
# CLI
# ===========================================================================

def main():
    ap = argparse.ArgumentParser(description="short_premium ledger engine")
    ap.add_argument("--selftest", action="store_true", help="synthetic-data unit tests, offline")
    ap.add_argument("--run", action="store_true", help="real-data run from .cache/short_premium/*.parquet")
    ap.add_argument("--arm", default=None, help="restrict --run to one study_arm")
    args = ap.parse_args()

    if args.selftest:
        ok = selftest()
        sys.exit(0 if ok else 1)
    if args.run:
        run(arm_filter=args.arm)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
