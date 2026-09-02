"""CORRECTED Polygon Black-Scholes IV panel -- as-traded spot throughout.

WHAT THIS IS
------------
A drop-in-comparable rebuild of `.cache/polygon_iv/iv_ledger_polygon.parquet`
(built by `experiments/data_ingest/polygon_iv_ingest.py`) that fixes ONE defect
and changes NOTHING else: the underlying spot.

THE DEFECT (measured in PANEL_AUDIT.md)
---------------------------------------
The old ingest used MySQL `price_history.close` as the underlying spot. That
column is written by trader.py's yfinance pull with `auto_adjust=True`, i.e. it
is back-adjusted for splits AND dividends. Option STRIKES are quoted in
AS-TRADED dollars. The old panel therefore used a price from the wrong price
space BOTH to pick the "ATM" strike AND as `S` in the Black-Scholes solve:

  * 31.4% of rows drift (adj_factor > 1.001); median drift among them 1.022,
    max 1.522; concentrated in 2022 (44.5% of 2022 rows drift >2%) and in CALM
    dividend-paying names (symbol-level spearman(vol, adj_factor) = -0.402).
  * 8.6% of rows selected an "ATM" contract that was actually >5% ITM
    (30.3% of 2022 rows).
  * On drifted rows the panel disagrees with a correctly-built ledger on strike
    61.4% of the time, median premium ratio 1.207 (2.45x at >5% drift).

The derived IV on those rows is unreliable: wrong strike, wrong S, wrong
moneyness, wrong time value.

WHAT THIS SCRIPT CHANGES (exhaustive -- see REBUILD_NOTES.md)
------------------------------------------------------------
  1. ATM strike selection anchors on `spot_unadj` (as-traded close).
  2. `S` passed to `implied_vol` for the ATM leg is `spot_unadj`.
  3. The +/-10% OTM leg TARGET strikes are `spot_unadj * (1 + otm_frac)`.
  4. `S` passed to `implied_vol` for both OTM legs is `spot_unadj`.
Everything else -- universe, score floor (70), expiry window [d+20, d+45],
nearest-priced-strike walk (6 tries), forward-P&L convention (24 calendar-day
window, 15th forward bar), iv_rv definition, r=0.04, q=0.0, rounding -- is a
faithful mirror of the old ingest so the two panels are comparable row-for-row.

AS-TRADED SPOT
--------------
`pull.load_unadj_spots` (reused verbatim, NOT reimplemented): yfinance
`auto_adjust=False` Close (split-adjusted, NOT dividend-adjusted) times the
cumulative product of FORWARD split ratios. Validated in pull.py's selftest
against NVDA / MMM / AZN prints.

HARD INVARIANTS (traps.md / data-acquisition.md)
  * Vendor data NEVER enters MySQL. Reads `price_history` (adjusted closes, for
    the `adj_factor` audit column only) and the rs_ledger parquet; writes only
    under .cache/polygon_real_premium/.
  * `.cache/polygon_iv/*` is READ-ONLY here. This script is purely additive.
  * PyMySQL is NOT thread-safe: every DB read happens single-threaded before the
    thread pool starts. Worker threads do Polygon HTTP only.
  * polars: DataFrames from row dicts use infer_schema_length=None; one
    fill_nan(None) choke point before strict casts.
  * The API key is never printed or logged.
  * ASCII-only stdout; PYTHONIOENCODING/PYTHONUTF8 set defensively.
  * Does NOT self-submit to the task queue. Queue it yourself.

POLYGON DEVELOPER COVERAGE BOUNDARY
  Option AGGREGATES have a ~4-YEAR ROLLING lookback (today ~2022-07). The CHAIN
  endpoint is NOT time-limited, so selection succeeds pre-boundary and only the
  bar walk 403s. Those are `miss:timeframe_403` coverage misses, never crashes.
  As the window rolls, the earliest 2022-08 signals will start 403-ing.

Usage
-----
    python experiments/polygon_real_premium/rebuild_iv_panel.py --selftest
    python experiments/polygon_real_premium/rebuild_iv_panel.py --limit 40 --workers 4
    python experiments/polygon_real_premium/rebuild_iv_panel.py --workers 8   # QUEUE THIS
    python experiments/polygon_real_premium/rebuild_iv_panel.py --consolidate-only
    python experiments/polygon_real_premium/rebuild_iv_panel.py --compare-old
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]                       # polygon_real_premium -> experiments -> root
# Insert in reverse priority order so _ROOT ends up FIRST (worktree PYTHONPATH trap).
for _p in (str(_HERE.parent), str(_ROOT / "experiments" / "data_ingest"), str(_ROOT)):
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import polars as pl                                                    # noqa: E402

from polygon_client import (                                           # noqa: E402
    PolygonClient, bs_price, implied_vol, rate_sleep_seconds,
)
# REUSE, do not reinvent: the as-traded spot machinery, the 403 classifier and
# the OHLC bar mapper all live in pull.py already.
import pull as _pull                                                   # noqa: E402
from pull import (                                                     # noqa: E402
    _STD_TICKER, _dt, _is_timeframe_403, _note_tf403, apply_forward_splits,
    bars_ohlc_by_date, is_liquid_entry, load_env_key, load_unadj_spots,
)
from polygon_iv_ingest import Closes, load_universe                    # noqa: E402

# ---------------------------------------------------------------------------
# Paths. `.cache/polygon_iv/*` is READ-ONLY from here.
# ---------------------------------------------------------------------------
OLD_PANEL = _ROOT / ".cache" / "polygon_iv" / "iv_ledger_polygon.parquet"   # READ-ONLY
OUT_DIR = _ROOT / ".cache" / "polygon_real_premium"
PROGRESS = OUT_DIR / "_iv_panel_progress.jsonl"
OUT_PARQUET = OUT_DIR / "iv_panel_corrected.parquet"
META_JSON = OUT_DIR / "iv_panel_corrected_meta.json"
SPOT_CACHE = OUT_DIR / "_iv_panel_spot.parquet"
SEED_SPOT = OUT_DIR / "_panel_audit_spot.parquet"     # audit already resolved 699 symbols
SEED_SPOT_2 = OUT_DIR / "_unadj_spot.parquet"         # pull.py's cache (fallback seed)

# ---------------------------------------------------------------------------
# Schema. The first 15 columns ARE the old panel's columns, in the old panel's
# order, so a downstream consumer can swap the parquet path unchanged.
# ---------------------------------------------------------------------------
OLD_PANEL_COLUMNS = [
    "symbol", "date", "overall", "vol_pct", "atm_iv", "entry_premium",
    "iv_rv", "skew", "pnl15", "pnl_max", "pnl_min",
    "otm_put_iv", "otm_call_iv", "atm_strike", "dte",
]
AUDIT_COLUMNS = [
    # the correction itself, made auditable
    "spot_unadj", "spot_adjusted", "adj_factor", "spot_source",
    "moneyness_true", "strike_rank",
    # entry-bar liquidity (a 1-lot print on a wide market is a stale-quote artifact)
    "entry_volume", "entry_trades", "liquid_entry",
    # contract identity / hygiene
    "option_ticker", "expiration_date", "std_contract",
    "otm_put_strike", "otm_call_strike", "fwd_bars", "n_api_calls",
]
COLUMNS = OLD_PANEL_COLUMNS + AUDIT_COLUMNS

_FLOAT_COLS = [
    "vol_pct", "atm_iv", "entry_premium", "iv_rv", "skew",
    "pnl15", "pnl_max", "pnl_min", "otm_put_iv", "otm_call_iv", "atm_strike",
    "spot_unadj", "spot_adjusted", "adj_factor", "moneyness_true",
    "entry_volume", "entry_trades", "otm_put_strike", "otm_call_strike",
]
_INT_COLS = ["overall", "dte", "strike_rank", "fwd_bars", "n_api_calls"]
_BOOL_COLS = ["liquid_entry", "std_contract"]
_STR_COLS = [c for c in COLUMNS if c not in _FLOAT_COLS + _INT_COLS + _BOOL_COLS]

# Defaults mirror polygon_iv_ingest's argparse defaults EXACTLY.
DEF_MIN_OVERALL = 70          # NOT 75 -- the old panel's floor
DEF_DTE_LO, DEF_DTE_HI = 20, 45
DEF_OTM_PUT, DEF_OTM_CALL = -0.10, 0.10
DEF_STRIKE_TRIES = 6
DEF_RATE, DEF_DIV = 0.04, 0.0
FWD_WINDOW_CAL_DAYS = 24      # forward-P&L bar window: (d, d+24 calendar days]
FWD_BARS_REQUIRED = 15        # pnl15 needs >= 15 forward bars; uses the 15th

# The old panel's realized window (it was run with these, not the argparse
# defaults -- verified: its 10,793-pair journal == rs_ledger 70+ in this window).
DEFAULT_START = "2022-08-01"
DEFAULT_END = "2026-05-15"


def log(msg) -> None:
    print(msg, flush=True)


class _TF403(Exception):
    """Internal: a Polygon aggregates timeframe-403 bubbled out of a leg walk."""


# ---------------------------------------------------------------------------
# Single-threaded enrichment: attach both spots to every universe row.
# After this returns NO worker thread needs the database.
# ---------------------------------------------------------------------------
def enrich_universe(rows: list[dict], closes_by_symbol: dict[str, dict[str, float]],
                    unadj: dict[str, dict[str, float]]) -> None:
    """SINGLE-THREADED, in place.

    spot_adjusted -- MySQL price_history.close (split AND dividend back-adjusted).
                     AUDIT ONLY. It is never used to pick a strike or as S.
    spot_unadj    -- as-traded close (yfinance auto_adjust=False x forward splits).
                     THIS is the strike-selection anchor and the BS spot.
    adj_factor    -- spot_unadj / spot_adjusted; 1.0 == uncontaminated row.

    When yfinance has no series for a symbol (delisted / renamed, ~1.3% of the
    old panel's rows) spot_unadj degrades to the adjusted close and spot_source
    records 'ph_adjusted' so those rows can be excluded downstream.
    """
    for r in rows:
        sym, d = r["symbol"], r["date"]
        adj = (closes_by_symbol.get(sym) or {}).get(d)
        u = (unadj.get(sym) or {}).get(d)
        r["spot_adjusted"] = float(adj) if (adj and adj > 0) else None
        if u and u > 0:
            r["spot_unadj"] = float(u)
            r["spot_source"] = "yf_unadj"
        elif adj and adj > 0:
            r["spot_unadj"] = float(adj)
            r["spot_source"] = "ph_adjusted"
        else:
            r["spot_unadj"] = None
            r["spot_source"] = None
        r["adj_factor"] = ((r["spot_unadj"] / r["spot_adjusted"])
                           if (r["spot_unadj"] and r["spot_adjusted"]) else None)


def load_done(progress_path: Path = PROGRESS) -> set[tuple[str, str]]:
    done: set[tuple[str, str]] = set()
    if progress_path.exists():
        for line in progress_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                done.add((r["symbol"], r["date"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


# ---------------------------------------------------------------------------
# PURE helpers (offline-testable).
# ---------------------------------------------------------------------------
def order_by_distance(candidates: list[dict], target: float) -> list[dict]:
    """Contracts ordered by |strike - target|. FAITHFUL to polygon_iv_ingest's
    `priced()` ordering: a plain stable sort on the absolute strike distance, so
    exact ties keep the chain's own (expiration-ascending) order. `target` is the
    ONLY thing this rebuild changes -- it is now derived from spot_unadj."""
    return sorted(candidates, key=lambda x: abs(float(x["strike_price"]) - target))


def years_to_expiry(expiration: str, d: str) -> float:
    """Mirror of the old ingest's T(): calendar days / 365, floored at 1e-6."""
    return max((_dt(expiration) - _dt(d)).days / 365.0, 1e-6)


def forward_pnl(by_date: dict, d: str, fwd_end: str, entry: float) -> tuple:
    """Mirror of polygon_iv_ingest's CURRENT forward P&L block: strictly-forward
    closes in (d, fwd_end], and pnl15/pnl_max/pnl_min ONLY when at least
    FWD_BARS_REQUIRED forward bars exist. Returns (pnl15, pmax, pmin, n_fwd_bars).

    SECOND DEFECT IN THE OLD PANEL (found while smoke-testing this rebuild, and
    NOT the spot bug): the shipped `.cache/polygon_iv/iv_ledger_polygon.parquet`
    was built 2026-07-07, one day BEFORE commit ebca1e1b removed the
    truncate-substitution `fwd[min(14, len(fwd)-1)]` from this very block. Its
    stored pnl15/pnl_max/pnl_min are therefore the LAST AVAILABLE price whenever
    the 15-bar horizon was unripe -- which is why the old panel's pnl15 null rate
    is 0.7% while an honest rebuild's is ~50% in the 2022 era. Verified exactly:
    ELF 2022-08-04, entry 2.45, only 12 forward bars; stored pnl15 0.1020 ==
    fwd[11]/2.45 - 1. This rebuild follows the FIXED convention (null when
    unripe) and records `fwd_bars` so ripeness is visible per row.

    CAVEAT kept identical to the old ingest on purpose: this counts BAR INDEX,
    not market trading days -- Polygon emits no bar on a no-trade day, so on an
    illiquid contract the '15th bar' can be more than 15 trading days out."""
    fwd = [b["c"] for dd, b in sorted(by_date.items())
           if d < dd <= fwd_end and b.get("c") is not None and b["c"] > 0]
    n = len(fwd)
    if n < FWD_BARS_REQUIRED or not entry or entry <= 0:
        return None, None, None, n
    p15 = fwd[FWD_BARS_REQUIRED - 1]
    return (round(p15 / entry - 1.0, 4),
            round(max(fwd[:FWD_BARS_REQUIRED]) / entry - 1.0, 4),
            round(min(fwd[:FWD_BARS_REQUIRED]) / entry - 1.0, 4),
            n)


def iv_over_rv(atm_iv: float | None, vol_pct: float | None) -> float | None:
    """Mirror: iv_rv = atm_iv / (daily sigma pct / 100 * sqrt(252)).

    `vol_pct` comes from the rs_ledger and is computed on the ADJUSTED close
    series. That is deliberate and unchanged -- it is the engine's own realized
    vol, and returns are near-invariant to back-adjustment. Only the OPTION side
    of this ratio is corrected."""
    if atm_iv is None or not vol_pct:
        return None
    rv_ann = (float(vol_pct) / 100.0) * math.sqrt(252)
    return round(atm_iv / rv_ann, 4) if rv_ann > 1e-6 else None


def is_std_contract(contract: dict, sym: str) -> bool:
    """True iff a plain 100-share-deliverable contract with a standard OCC root.

    NOT a filter (the old panel did not filter these, and adding a filter would
    confound the old-vs-corrected comparison) -- recorded as a column so a
    downstream consumer can screen adjusted/non-standard contracts itself."""
    spc = contract.get("shares_per_contract")
    if spc is not None and int(spc) != 100:
        return False
    tick = str(contract.get("ticker") or "")
    return bool(_STD_TICKER.match(tick)) and tick.startswith("O:%s" % sym)


# ---------------------------------------------------------------------------
# Per-signal processing (thread-safe: Polygon HTTP only, no DB).
# ---------------------------------------------------------------------------
def _base_row(sig: dict) -> dict:
    row = {c: None for c in COLUMNS}
    row.update({
        "symbol": sig["symbol"], "date": sig["date"],
        "overall": (int(sig["overall"]) if sig.get("overall") is not None else None),
        "vol_pct": (float(sig["vol_pct"]) if sig.get("vol_pct") is not None else None),
        "spot_unadj": sig.get("spot_unadj"), "spot_adjusted": sig.get("spot_adjusted"),
        "adj_factor": sig.get("adj_factor"), "spot_source": sig.get("spot_source"),
        "n_api_calls": 0,
    })
    return row


def process_one(client, sig: dict, args) -> dict:
    """One (symbol, date) -> one record with a `status` of 'kept' or 'miss:<why>'.
    Never raises for an expected data hole."""
    sym, d = sig["symbol"], sig["date"]
    row = _base_row(sig)

    # ==================================================================
    # THE CORRECTION. `spot` is the AS-TRADED close and is the ONLY price
    # used for (a) ATM strike selection, (b) the OTM leg targets, and
    # (c) S in every implied_vol call below. The back-adjusted close is
    # carried as `spot_adjusted` for auditing and is never read again.
    # ==================================================================
    spot = sig.get("spot_unadj")
    if spot is None or spot <= 0:
        return {**row, "status": "miss:no_underlying"}

    n_calls = {"n": 0}

    exp_gte = (_dt(d) + timedelta(days=args.dte_lo)).isoformat()
    exp_lte = (_dt(d) + timedelta(days=args.dte_hi)).isoformat()
    try:
        contracts = client.list_option_contracts(sym, as_of=d, exp_gte=exp_gte,
                                                 exp_lte=exp_lte)
    except RuntimeError as exc:
        n_calls["n"] += 1
        if _is_timeframe_403(exc):
            _note_tf403(d)
            return {**row, "status": "miss:timeframe_403", "n_api_calls": n_calls["n"]}
        raise
    n_calls["n"] += 1
    calls = [c for c in contracts
             if c.get("contract_type") == "call" and c.get("strike_price")]
    puts = [c for c in contracts
            if c.get("contract_type") == "put" and c.get("strike_price")]
    if not calls or not puts:
        return {**row, "status": "miss:no_chain", "n_api_calls": n_calls["n"]}

    def priced(candidates: list[dict], target: float, end: str):
        """Walk out from `target` strike; return (contract, ohlc_by_date, rank) for
        the first that actually TRADED on `d`. Mirror of the old `priced()` except
        that `target` is anchored on the as-traded spot and the bar map carries
        full OHLCV (so entry volume / trade count can be recorded)."""
        for rank, c in enumerate(order_by_distance(candidates, target)[:args.strike_tries]):
            try:
                raw = client.option_daily_bars(c["ticker"], d, end)
            except RuntimeError as exc:
                n_calls["n"] += 1
                if _is_timeframe_403(exc):
                    _note_tf403(d)
                    raise _TF403() from exc
                raise
            n_calls["n"] += 1
            bd = bars_ohlc_by_date(raw)
            bar = bd.get(d)
            if bar is not None and bar.get("c") is not None and bar["c"] > 0:
                return c, bd, rank
        return None, None, None

    fwd_end = (_dt(d) + timedelta(days=FWD_WINDOW_CAL_DAYS)).isoformat()

    try:
        # ---- ATM call: nearest PRICED strike to the AS-TRADED spot -------------
        atm, atm_bd, atm_rank = priced(calls, spot, fwd_end)
        if atm is None:
            return {**row, "status": "miss:no_atm_price", "n_api_calls": n_calls["n"]}
        entry_bar = atm_bd[d]
        entry = float(entry_bar["c"])
        atm_strike = float(atm["strike_price"])
        atm_iv = implied_vol("call", entry, spot, atm_strike,
                             years_to_expiry(atm["expiration_date"], d),
                             r=args.rate, q=args.div)      # S = spot_unadj
        if atm_iv is None:
            return {**row, "status": "miss:atm_iv_unsolved", "n_api_calls": n_calls["n"]}

        # ---- +/-10% OTM legs, targets ALSO anchored on the as-traded spot ------
        otm_put, put_bd, _ = priced(puts, spot * (1.0 + args.otm_frac_put), d)
        otm_call, call_bd, _ = priced(calls, spot * (1.0 + args.otm_frac_call), d)
    except _TF403:
        return {**row, "status": "miss:timeframe_403", "n_api_calls": n_calls["n"]}

    put_iv = put_strike = None
    if otm_put is not None:
        put_strike = float(otm_put["strike_price"])
        put_iv = implied_vol("put", put_bd[d]["c"], spot, put_strike,
                             years_to_expiry(otm_put["expiration_date"], d),
                             r=args.rate, q=args.div)      # S = spot_unadj
    call_iv = call_strike = None
    if otm_call is not None:
        call_strike = float(otm_call["strike_price"])
        call_iv = implied_vol("call", call_bd[d]["c"], spot, call_strike,
                              years_to_expiry(otm_call["expiration_date"], d),
                              r=args.rate, q=args.div)     # S = spot_unadj
    skew = (round(put_iv - call_iv, 4)
            if (put_iv is not None and call_iv is not None) else None)

    pnl15, pmax, pmin, n_fwd = forward_pnl(atm_bd, d, fwd_end, entry)

    row.update({
        "atm_iv": round(atm_iv, 4),
        "entry_premium": round(entry, 4),
        "iv_rv": iv_over_rv(atm_iv, sig.get("vol_pct")),
        "skew": skew,
        "pnl15": pnl15, "pnl_max": pmax, "pnl_min": pmin,
        "otm_put_iv": (round(put_iv, 4) if put_iv is not None else None),
        "otm_call_iv": (round(call_iv, 4) if call_iv is not None else None),
        "atm_strike": atm_strike,
        "dte": (_dt(atm["expiration_date"]) - _dt(d)).days,
        "moneyness_true": atm_strike / spot,          # strike / AS-TRADED spot
        "strike_rank": int(atm_rank),
        "entry_volume": entry_bar.get("v"),
        "entry_trades": entry_bar.get("n"),
        "liquid_entry": is_liquid_entry(entry_bar.get("v"), entry_bar.get("n")),
        "option_ticker": str(atm.get("ticker")),
        "expiration_date": str(atm.get("expiration_date"))[:10],
        "std_contract": is_std_contract(atm, sym),
        "otm_put_strike": put_strike, "otm_call_strike": call_strike,
        "fwd_bars": int(n_fwd), "n_api_calls": n_calls["n"],
        "status": "kept",
    })
    return row


# ---------------------------------------------------------------------------
# Consolidation: journal -> parquet (OFFLINE; no key, no DB).
# Only 'kept' rows are written, exactly like the old ingest, so the parquet is a
# drop-in swap. Miss accounting lives in the journal + meta.json.
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


def consolidate(progress_path: Path = PROGRESS,
                parquet_path: Path = OUT_PARQUET) -> int:
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
        key = (r.get("symbol"), r.get("date"))
        if key[0] is None or key[1] is None:
            continue
        rows[key] = r                       # last write wins (a re-run supersedes)

    kept = [r for r in rows.values() if r.get("status") == "kept"]
    if kept:
        recs = [{c: r.get(c) for c in COLUMNS} for r in kept]
        # infer_schema_length=None: columns that are None for the first N rows and
        # populated later otherwise blow up polars' schema inference.
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
        df = df.sort(["date", "symbol"])
    else:
        df = pl.DataFrame(schema=_empty_schema())

    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(parquet_path)
    log("consolidated %d kept rows (of %d journal keys) -> %s"
        % (df.height, len(rows), parquet_path))
    return df.height


def journal_status_counts(progress_path: Path = PROGRESS) -> dict:
    if not progress_path.exists():
        return {}
    seen: dict[tuple[str, str], str] = {}
    for line in progress_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        k = (r.get("symbol"), r.get("date"))
        if k[0] is None:
            continue
        seen[k] = r.get("status")
    return dict(Counter(seen.values()))


def write_meta(args, universe_n: int, elapsed: float, api_calls: int) -> None:
    stat = journal_status_counts()
    n_rows = 0
    dmin = dmax = None
    n_sym = 0
    spot_counts: dict = {}
    n_liquid = 0
    n_drifted = 0
    if OUT_PARQUET.exists():
        df = pl.read_parquet(OUT_PARQUET)
        n_rows = df.height
        if n_rows:
            dmin, dmax = df["date"].min(), df["date"].max()
            n_sym = df["symbol"].n_unique()
            spot_counts = dict(Counter(x for x in df["spot_source"].to_list() if x))
            n_liquid = int(df["liquid_entry"].fill_null(False).sum())
            n_drifted = int(df.filter(pl.col("adj_factor") > 1.001).height)
    meta = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": ("corrected rebuild of .cache/polygon_iv/iv_ledger_polygon.parquet "
                    "using the AS-TRADED underlying spot for strike selection AND as "
                    "S in the Black-Scholes solve (see PANEL_AUDIT.md)"),
        "old_panel_read_only": str(OLD_PANEL),
        "universe_source": "rs_ledger.parquet (same as the old panel)",
        "min_overall": args.min_overall,
        "window_requested": {"start": args.start, "end": args.end},
        "universe_pairs": universe_n,
        "kept_rows": n_rows,
        "rows_by_status": stat,
        "rows_by_spot_source": spot_counts,
        "rows_liquid_entry": n_liquid,
        "rows_adj_factor_gt_1.001": n_drifted,
        "date_range": {"min": dmin, "max": dmax},
        "distinct_symbols": n_sym,
        "selection_rule": {
            "expiry_window_cal_days": [args.dte_lo, args.dte_hi],
            "atm": "nearest PRICED strike to spot_unadj across the whole window "
                   "(stable sort -> tie keeps chain order); %d tries" % args.strike_tries,
            "otm_put_target": "spot_unadj * (1 %+0.2f)" % args.otm_frac_put,
            "otm_call_target": "spot_unadj * (1 %+0.2f)" % args.otm_frac_call,
            "forward_pnl": "closes in (d, d+%dcal]; needs >= %d bars; uses the %dth"
                           % (FWD_WINDOW_CAL_DAYS, FWD_BARS_REQUIRED, FWD_BARS_REQUIRED),
            "bs": {"r": args.rate, "q": args.div, "T": "calendar days / 365"},
        },
        "spot_anchor": ("spot_unadj = yfinance auto_adjust=False Close x cumulative "
                        "FORWARD split ratios (pull.load_unadj_spots). "
                        "price_history.close is split+dividend back-adjusted and is "
                        "carried ONLY as spot_adjusted / adj_factor."),
        "total_api_calls_this_run": api_calls,
        "wall_clock_seconds": round(elapsed, 1),
        "timeframe_403_count": _pull._tf403_state["n"],
        "timeframe_403_signal_dates": len(_pull._tf403_state["dates"]),
        "invariant": "vendor data written to .cache only; never MySQL",
    }
    META_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(META_JSON, "w", encoding="ascii", errors="backslashreplace") as f:
        json.dump(meta, f, indent=2, default=str)
    log("wrote %s" % META_JSON)


# ---------------------------------------------------------------------------
# Live pull driver.
# ---------------------------------------------------------------------------
def _seed_spot_cache() -> None:
    """Copy an already-resolved as-traded spot cache in, so a rebuild does not
    re-hammer yfinance for the ~700 symbols the audit already resolved."""
    if SPOT_CACHE.exists():
        return
    for seed in (SEED_SPOT, SEED_SPOT_2):
        if seed.exists():
            import shutil
            SPOT_CACHE.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(seed, SPOT_CACHE)
            log("seeded as-traded spot cache from %s" % seed.name)
            return


def run_pull(args) -> int:
    if not load_env_key():
        log("ERROR: POLYGON_API_KEY not found (env or .env).")
        log("  (--selftest, --consolidate-only and --compare-old need NO key.)")
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    symbols = {s.strip().upper() for s in args.symbols.split(",") if s.strip()} or None

    # ---- SINGLE-THREADED PHASE (PyMySQL is not thread-safe) ------------------
    universe = load_universe(args.min_overall, args.start, args.end, symbols)
    rows = [{"symbol": s, "date": d, "overall": o, "vol_pct": v}
            for s, d, o, v in universe]
    log("universe=%d pairs (overall>=%d, %s..%s) from rs_ledger"
        % (len(rows), args.min_overall, args.start, args.end))

    done = load_done()
    todo = [r for r in rows if (r["symbol"], r["date"]) not in done]
    if args.limit:
        todo = todo[:args.limit]
    log("done=%d todo=%d workers=%d" % (len(done), len(todo), args.workers))
    if not todo:
        n = consolidate()
        write_meta(args, len(rows), 0.0, 0)
        log("nothing to do; parquet has %d rows" % n)
        return 0

    client = PolygonClient(sleep_seconds=rate_sleep_seconds(default=0.0))
    syms = sorted({r["symbol"] for r in todo})

    log("preloading ADJUSTED closes (price_history) for %d symbols, single-threaded..."
        % len(syms))
    closes = Closes(client)
    closes.preload(syms)

    _seed_spot_cache()
    unadj = load_unadj_spots(syms, args.start, args.end, cache=SPOT_CACHE,
                             refresh=args.refresh_spot)
    enrich_universe(todo, closes._by_symbol, unadj)
    n_no_unadj = sum(1 for r in todo if r.get("spot_unadj") is None)
    n_fallback = sum(1 for r in todo if r.get("spot_source") == "ph_adjusted")
    n_drift = sum(1 for r in todo if (r.get("adj_factor") or 0) > 1.001)
    log("enriched: no spot=%d  spot_source=ph_adjusted(fallback)=%d  drifted(>1.001)=%d"
        % (n_no_unadj, n_fallback, n_drift))
    # ---- END DB PHASE. Worker threads below do Polygon HTTP only. ------------

    t0 = time.time()
    ctr = {"n": 0, "kept": 0, "miss": 0}
    fh = PROGRESS.open("a", encoding="utf-8")
    lock = threading.Lock()

    def record(rec: dict) -> None:
        with lock:
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()
            ctr["n"] += 1
            ctr["kept" if rec.get("status") == "kept" else "miss"] += 1
            if ctr["n"] % args.flush_every == 0:
                consolidate()
                rate = ctr["n"] / max(time.time() - t0, 1e-6)
                log("  %d/%d kept=%d miss=%d calls=%d %.2f/s"
                    % (ctr["n"], len(todo), ctr["kept"], ctr["miss"],
                       client.calls, rate))

    def run_one(sig: dict) -> None:
        try:
            rec = process_one(client, sig, args)
        except Exception as exc:
            rec = {**_base_row(sig), "status": "miss:err:%s" % repr(exc)[:80]}
        record(rec)

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(run_one, todo))
    else:
        for sig in todo:
            run_one(sig)
    fh.close()

    n = consolidate()
    elapsed = time.time() - t0
    write_meta(args, len(rows), elapsed, client.calls)
    log("DONE: processed=%d kept=%d miss=%d api_calls=%d in %.0fs -> %s (rows=%d)"
        % (ctr["n"], ctr["kept"], ctr["miss"], client.calls, elapsed,
           OUT_PARQUET, n))
    if _pull._tf403_state["n"]:
        log("  note: %d signals missed as timeframe_403 across %d signal dates "
            "(outside the ~4yr aggregates window)."
            % (_pull._tf403_state["n"], len(_pull._tf403_state["dates"])))
    return 0


# ===========================================================================
# OFFLINE comparison against the OLD panel (no key, no DB, read-only).
# ===========================================================================
def _q(vals, p):
    vals = sorted(v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v)))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    i = p * (len(vals) - 1)
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return vals[lo] + (vals[hi] - vals[lo]) * (i - lo)


def _dist(name, vals):
    vals = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return "| %s | 0 | - | - | - | - | - |" % name
    return "| %s | %d | %.4f | %.4f | %.4f | %.4f | %.4f |" % (
        name, len(vals), min(vals), _q(vals, 0.25), _q(vals, 0.50),
        _q(vals, 0.75), max(vals))


def compare_old(old_path: Path = OLD_PANEL, new_path: Path = OUT_PARQUET) -> int:
    """OFFLINE. Join old vs corrected on (symbol, date) and report the correctness
    check: undrifted rows MUST agree; drifted rows are where the fix bites."""
    if not old_path.exists():
        log("old panel not found: %s" % old_path)
        return 1
    if not new_path.exists():
        log("corrected panel not found: %s" % new_path)
        return 1
    old = pl.read_parquet(old_path).select(
        ["symbol", "date", "atm_iv", "entry_premium", "atm_strike", "dte",
         "skew", "iv_rv", "pnl15"]).rename({
            "atm_iv": "old_atm_iv", "entry_premium": "old_premium",
            "atm_strike": "old_strike", "dte": "old_dte", "skew": "old_skew",
            "iv_rv": "old_iv_rv", "pnl15": "old_pnl15"})
    new = pl.read_parquet(new_path)
    j = new.join(old, on=["symbol", "date"], how="inner")
    log("=" * 78)
    log("OLD vs CORRECTED -- overlap on (symbol, date)")
    log("=" * 78)
    log("old panel rows      : %d" % pl.read_parquet(old_path).height)
    log("corrected rows      : %d" % new.height)
    log("overlap rows        : %d" % j.height)
    if not j.height:
        return 0
    j = j.with_columns([
        (pl.col("atm_strike") != pl.col("old_strike")).alias("strike_diff"),
        (pl.col("dte") != pl.col("old_dte")).alias("dte_diff"),
        (pl.col("old_premium") / pl.col("entry_premium")).alias("prem_ratio"),
        (pl.col("old_atm_iv") - pl.col("atm_iv")).alias("iv_delta"),
        (pl.col("adj_factor").fill_null(1.0) > 1.001).alias("drifted"),
    ])
    for label, sub in (("UNDRIFTED (adj_factor <= 1.001)", j.filter(~pl.col("drifted"))),
                       ("DRIFTED   (adj_factor >  1.001)", j.filter(pl.col("drifted"))),
                       ("ALL", j)):
        n = sub.height
        if not n:
            log("\n%s : n=0" % label)
            continue
        nd = int(sub["strike_diff"].sum())
        ne = int(sub["dte_diff"].sum())
        log("\n%s : n=%d" % (label, n))
        log("  different strike : %d (%.1f%%)" % (nd, 100.0 * nd / n))
        log("  different expiry : %d (%.1f%%)" % (ne, 100.0 * ne / n))
        log("  | quantity | n | min | p25 | median | p75 | max |")
        log("  |---|---|---|---|---|---|---|")
        log("  " + _dist("old_atm_iv", sub["old_atm_iv"].to_list()))
        log("  " + _dist("corrected_atm_iv", sub["atm_iv"].to_list()))
        log("  " + _dist("iv_delta (old-new)", sub["iv_delta"].to_list()))
        log("  " + _dist("prem_ratio (old/new)", sub["prem_ratio"].to_list()))
        log("  " + _dist("adj_factor", sub["adj_factor"].to_list()))
        log("  " + _dist("moneyness_true", sub["moneyness_true"].to_list()))
        iv = [x for x in sub["iv_delta"].to_list() if x is not None]
        if iv:
            same = sum(1 for x in iv if abs(x) < 1e-4)
            log("  atm_iv identical (<1e-4): %d/%d (%.1f%%)  mean|delta|=%.4f"
                % (same, len(iv), 100.0 * same / len(iv),
                   sum(abs(x) for x in iv) / len(iv)))
    # ---- the OLD panel's SECOND defect: truncate-substituted pnl15 -----------
    unripe = j.filter(pl.col("fwd_bars") < FWD_BARS_REQUIRED)
    if unripe.height:
        subbed = int(unripe["old_pnl15"].is_not_null().sum())
        log("\npnl15 ripeness (independent of the spot fix):")
        log("  overlap rows with < %d forward bars : %d / %d (%.1f%%)"
            % (FWD_BARS_REQUIRED, unripe.height, j.height,
               100.0 * unripe.height / j.height))
        log("  ... of those, OLD panel reports a non-null pnl15 : %d (%.1f%%)"
            % (subbed, 100.0 * subbed / unripe.height))
        log("  The old panel was built 2026-07-07, one day before commit ebca1e1b")
        log("  removed fwd[min(14, len(fwd)-1)] from polygon_iv_ingest. Those values")
        log("  are the LAST AVAILABLE price, not a 15-bar outcome. The corrected")
        log("  panel nulls them and records fwd_bars.")
    return 0


# ===========================================================================
# OFFLINE SELF-TESTS (no key, no network, no DB).
# ===========================================================================
class _FakeClient:
    """Offline Polygon stand-in. `strikes` is the listed strike ladder; `dry` are
    strikes with no print on the signal date; `nbars` controls forward coverage."""
    calls = 0

    def __init__(self, strikes=(95, 100, 105, 110, 130, 135, 140, 141, 142, 145, 150),
                 dry=(), nbars=20, dtes=(28, 35), raise_403=False, px=3.0):
        self.strikes = list(strikes)
        self.dry = set(float(x) for x in dry)
        self.nbars = nbars
        self.dtes = dtes
        self.raise_403 = raise_403
        self.px = px
        self.bar_requests: list[float] = []
        self.calls = 0

    def list_option_contracts(self, sym, as_of, exp_gte, exp_lte,
                              contract_type=None, **kw):
        self.calls += 1
        base = _dt(as_of)
        out = []
        for dte in self.dtes:
            exp = base + timedelta(days=dte)
            for k in self.strikes:
                for side in ("call", "put"):
                    out.append({
                        "ticker": "O:%s%s%s%08d" % (sym, exp.strftime("%y%m%d"),
                                                    "C" if side == "call" else "P",
                                                    int(k * 1000)),
                        "contract_type": side, "strike_price": float(k),
                        "expiration_date": exp.isoformat(),
                        "shares_per_contract": 100})
        return out

    def option_daily_bars(self, ticker, start, end):
        self.calls += 1
        if self.raise_403:
            raise RuntimeError('Polygon 403: body={"status":"NOT_AUTHORIZED",'
                               '"message":"Your plan doesn\'t include this data '
                               'timeframe."}')
        strike = int(ticker[-8:]) / 1000.0
        self.bar_requests.append(strike)
        if strike in self.dry:
            return []
        base = _dt(start)
        rows = []
        px = self.px
        for i in range(self.nbars):
            dd = base + timedelta(days=i)
            if i:
                px *= 1.02
            t = int((datetime(dd.year, dd.month, dd.day)
                     - datetime(1970, 1, 1)).total_seconds() * 1000)
            rows.append({"t": t, "o": px, "h": px * 1.05, "l": px * 0.95,
                         "c": px, "v": 250, "vw": px, "n": 40})
        return rows


class _Args:
    min_overall = DEF_MIN_OVERALL
    dte_lo, dte_hi = DEF_DTE_LO, DEF_DTE_HI
    otm_frac_put, otm_frac_call = DEF_OTM_PUT, DEF_OTM_CALL
    strike_tries = DEF_STRIKE_TRIES
    rate, div = DEF_RATE, DEF_DIV
    start, end = DEFAULT_START, DEFAULT_END
    symbols = ""
    workers = 1
    limit = 0
    flush_every = 200
    refresh_spot = False


def selftest() -> int:
    log("=== rebuild_iv_panel.py OFFLINE SELF-TESTS ===")

    # -- 1. BS solver round-trips: price -> iv -> price ------------------------
    for side in ("call", "put"):
        for S, K, T, sig in [(100, 100, 30 / 365, 0.30), (100, 110, 45 / 365, 0.55),
                             (100, 90, 20 / 365, 0.80), (250, 250, 30 / 365, 0.22),
                             (141.75, 140, 31 / 365, 0.35), (18.0, 17.5, 28 / 365, 0.62)]:
            px = bs_price(side, S, K, T, sig, r=DEF_RATE, q=DEF_DIV)
            iv = implied_vol(side, px, S, K, T, r=DEF_RATE, q=DEF_DIV)
            assert iv is not None and abs(iv - sig) < 1e-3, (side, S, K, T, sig, px, iv)
            px2 = bs_price(side, S, K, T, iv, r=DEF_RATE, q=DEF_DIV)
            assert abs(px2 - px) < 1e-3, (side, px, px2)     # price -> iv -> price
    assert implied_vol("call", 0.01, 100, 50, 30 / 365) is None      # below intrinsic
    log("  [1] BS price->iv->price round-trip OK (both sides, 6 param sets)")

    # -- 2. THE CORRECTION: a drifted name picks a DIFFERENT strike -------------
    # MMM 2022-08-23 (PANEL_AUDIT / pull.py DESIGN): as-traded 141.75,
    # back-adjusted 103.79 -> adj_factor 1.366.
    ladder = [float(k) for k in range(95, 155)]
    spot_true, spot_adj = 141.75, 103.79
    k_true = order_by_distance([{"strike_price": k} for k in ladder], spot_true)[0]["strike_price"]
    k_adj = order_by_distance([{"strike_price": k} for k in ladder], spot_adj)[0]["strike_price"]
    assert k_true == 142.0 and k_adj == 104.0, (k_true, k_adj)
    assert k_true != k_adj, "drifted name must select a different strike"
    # ... and the adjusted-spot pick is deep ITM against the real spot
    assert k_adj / spot_true < 0.75, k_adj / spot_true
    # the OTM legs move too
    assert (order_by_distance([{"strike_price": k} for k in ladder],
                              spot_true * 0.90)[0]["strike_price"] !=
            order_by_distance([{"strike_price": k} for k in ladder],
                              spot_adj * 0.90)[0]["strike_price"])
    # and the IV solved off the wrong spot is materially wrong (or unsolvable):
    T = 31 / 365
    true_px = bs_price("call", spot_true, k_true, T, 0.35, r=DEF_RATE, q=DEF_DIV)
    iv_right = implied_vol("call", true_px, spot_true, k_true, T, r=DEF_RATE, q=DEF_DIV)
    iv_wrong = implied_vol("call", true_px, spot_adj, k_true, T, r=DEF_RATE, q=DEF_DIV)
    assert iv_right is not None and abs(iv_right - 0.35) < 1e-3, iv_right
    assert iv_wrong is None or abs(iv_wrong - 0.35) > 0.30, iv_wrong
    log("  [2] drifted name: as-traded vs adjusted spot select DIFFERENT strikes "
        "(142 vs 104) and the wrong-spot IV is unrecoverable")

    # -- 3. schema matches the old panel's column set --------------------------
    assert COLUMNS[:len(OLD_PANEL_COLUMNS)] == OLD_PANEL_COLUMNS, COLUMNS[:15]
    assert set(OLD_PANEL_COLUMNS).issubset(set(COLUMNS))
    assert len(set(COLUMNS)) == len(COLUMNS), "duplicate column"
    for c in ("spot_unadj", "spot_adjusted", "adj_factor", "moneyness_true",
              "strike_rank", "entry_volume", "entry_trades", "liquid_entry"):
        assert c in COLUMNS, c
    if OLD_PANEL.exists():
        oldcols = pl.read_parquet(OLD_PANEL).columns          # READ-ONLY
        assert oldcols == OLD_PANEL_COLUMNS, oldcols
        log("  [3] schema OK; live old panel columns match exactly (%d cols) and "
            "are a prefix of the corrected schema (%d cols)"
            % (len(oldcols), len(COLUMNS)))
    else:
        log("  [3] schema OK (old panel absent; checked against the hardcoded list)")

    # -- 4. pure helpers -------------------------------------------------------
    assert abs(years_to_expiry("2022-10-01", "2022-09-01") - 30 / 365) < 1e-12
    assert years_to_expiry("2022-09-01", "2022-09-01") == 1e-6
    bd = {}
    for i in range(0, 20):
        d = (_dt("2022-09-01") + timedelta(days=i)).isoformat()
        bd[d] = {"c": 2.0 + 0.1 * i, "h": 0, "l": 0, "o": 0, "v": 1, "n": 1}
    p15, pmx, pmn, nfwd = forward_pnl(bd, "2022-09-01", "2022-09-25", 2.0)
    assert abs(p15 - (3.5 / 2.0 - 1)) < 1e-9, p15          # 15th fwd bar = 2.0+1.5
    assert abs(pmx - (3.5 / 2.0 - 1)) < 1e-9 and abs(pmn - (2.1 / 2.0 - 1)) < 1e-9
    assert nfwd == 19, nfwd
    # < 15 forward bars -> all None, NEVER truncate-substituted with the last
    # available price (regression guard for the old panel's second defect: it was
    # built one day before commit ebca1e1b removed fwd[min(14, len(fwd)-1)]).
    short = forward_pnl(bd, "2022-09-01", "2022-09-10", 2.0)
    assert short[:3] == (None, None, None) and short[3] == 9, short
    assert forward_pnl(bd, "2022-09-01", "2022-09-25", 0.0)[:3] == (None, None, None)
    assert iv_over_rv(0.40, None) is None and iv_over_rv(None, 2.0) is None
    assert abs(iv_over_rv(0.40, 2.0) - round(0.40 / (0.02 * math.sqrt(252)), 4)) < 1e-9
    assert is_std_contract({"ticker": "O:MMM220916C00140000",
                            "shares_per_contract": 100}, "MMM") is True
    assert is_std_contract({"ticker": "O:MMM1220916C00140000",
                            "shares_per_contract": 100}, "MMM") is False
    assert is_std_contract({"ticker": "O:MMM220916C00140000",
                            "shares_per_contract": 10}, "MMM") is False
    # stable-sort tie behaviour matches the old ingest (chain order kept)
    tie = order_by_distance([{"strike_price": 105.0, "id": "b"},
                             {"strike_price": 95.0, "id": "a"}], 100.0)
    assert tie[0]["id"] == "b", tie
    log("  [4] years_to_expiry / forward_pnl / iv_over_rv / is_std_contract / "
        "tie-order OK")

    # -- 5. enrich_universe: both spots, adj_factor, fallback -------------------
    rows = [{"symbol": "MMM", "date": "2022-08-23", "overall": 72, "vol_pct": 1.5},
            {"symbol": "DEAD", "date": "2022-08-23", "overall": 71, "vol_pct": 2.0},
            {"symbol": "GONE", "date": "2022-08-23", "overall": 70, "vol_pct": 2.0}]
    enrich_universe(rows,
                    {"MMM": {"2022-08-23": 103.79}, "DEAD": {"2022-08-23": 50.0}},
                    {"MMM": {"2022-08-23": 141.75}})
    assert rows[0]["spot_unadj"] == 141.75 and rows[0]["spot_adjusted"] == 103.79
    assert abs(rows[0]["adj_factor"] - 141.75 / 103.79) < 1e-12
    assert rows[0]["spot_source"] == "yf_unadj"
    assert rows[1]["spot_unadj"] == 50.0 and rows[1]["spot_source"] == "ph_adjusted"
    assert rows[1]["adj_factor"] == 1.0
    assert rows[2]["spot_unadj"] is None and rows[2]["spot_source"] is None
    log("  [5] enrich_universe (both spots / adj_factor / delisted fallback) OK")

    # -- 6. process_one end-to-end, and the ANTI-LEAK assertion ----------------
    args = _Args()
    sig = {"symbol": "MMM", "date": "2022-08-23", "overall": 76, "vol_pct": 1.6,
           "spot_unadj": 141.75, "spot_adjusted": 103.79,
           "adj_factor": 141.75 / 103.79, "spot_source": "yf_unadj"}
    fc = _FakeClient(strikes=list(range(95, 165)))
    row = process_one(fc, sig, args)
    assert row["status"] == "kept", row["status"]
    assert row["atm_strike"] == 142.0, row["atm_strike"]      # nearest to 141.75
    assert abs(row["moneyness_true"] - 142.0 / 141.75) < 1e-12, row["moneyness_true"]
    assert row["strike_rank"] == 0 and row["dte"] == 28, row
    assert row["liquid_entry"] is True and row["entry_volume"] == 250.0
    assert row["std_contract"] is True, row["std_contract"]
    assert row["spot_unadj"] == 141.75 and row["spot_adjusted"] == 103.79
    # ANTI-LEAK: no bar was ever requested for a strike near the ADJUSTED close.
    assert all(abs(k - 103.79) > 5.0 for k in fc.bar_requests), fc.bar_requests
    assert any(abs(k - 141.75) < 1.5 for k in fc.bar_requests), fc.bar_requests
    # OTM legs anchored on the as-traded spot: 0.9*141.75=127.6, 1.1*141.75=155.9
    assert row["otm_put_strike"] == 128.0, row["otm_put_strike"]
    assert row["otm_call_strike"] == 156.0, row["otm_call_strike"]
    assert row["otm_put_iv"] is not None and row["otm_call_iv"] is not None
    assert row["skew"] is not None
    assert row["pnl15"] is not None and row["pnl_max"] is not None
    assert row["iv_rv"] is not None
    assert row["n_api_calls"] == 4, row["n_api_calls"]    # chain + atm + put + call
    for c in COLUMNS:
        assert c in row, "missing column %s" % c
    # the SAME data run with the adjusted close as the anchor picks 104 -- proof the
    # anchor is the only thing driving the difference
    sig_wrong = dict(sig, spot_unadj=103.79)
    row_wrong = process_one(_FakeClient(strikes=list(range(95, 165))), sig_wrong, args)
    assert row_wrong["atm_strike"] == 104.0, row_wrong["atm_strike"]
    assert row_wrong["atm_strike"] != row["atm_strike"]
    log("  [6] process_one kept-row OK; ANTI-LEAK: no adjusted-close strike was "
        "ever requested; OTM legs anchored on as-traded spot")

    # -- 7. miss paths ---------------------------------------------------------
    r = process_one(_FakeClient(), {**sig, "spot_unadj": None}, args)
    assert r["status"] == "miss:no_underlying" and r["n_api_calls"] == 0, r["status"]

    class _NoChain(_FakeClient):
        def list_option_contracts(self, *a, **k):
            self.calls += 1
            return []
    assert process_one(_NoChain(), sig, args)["status"] == "miss:no_chain"

    class _CallsOnly(_FakeClient):
        def list_option_contracts(self, sym, as_of, exp_gte, exp_lte, **k):
            out = _FakeClient.list_option_contracts(self, sym, as_of, exp_gte, exp_lte)
            return [c for c in out if c["contract_type"] == "call"]
    assert process_one(_CallsOnly(), sig, args)["status"] == "miss:no_chain"

    # every candidate strike dry -> no_atm_price, exactly strike_tries bar attempts
    dry_all = _FakeClient(strikes=list(range(95, 165)),
                          dry=list(range(95, 165)))
    r = process_one(dry_all, sig, args)
    assert r["status"] == "miss:no_atm_price", r["status"]
    assert r["n_api_calls"] == 1 + DEF_STRIKE_TRIES, r["n_api_calls"]

    # thin ATM strike -> falls through to the next-nearest. NOTE the candidate list
    # spans BOTH expiries in the window (faithful to the old ingest, which sorted
    # every contract in [d+20, d+45] by |strike - target| regardless of expiry), so
    # a dry 142 consumes ranks 0 and 1 (142@28d, 142@35d) and 141 lands at rank 2.
    r = process_one(_FakeClient(strikes=list(range(95, 165)), dry=[142.0]), sig, args)
    assert r["status"] == "kept" and r["strike_rank"] == 2, r
    assert r["atm_strike"] == 141.0, r["atm_strike"]       # |141-141.75| < |143-141.75|
    # single-expiry chain: the dry ATM falls straight through to rank 1
    r = process_one(_FakeClient(strikes=list(range(95, 165)), dtes=(28,),
                                dry=[142.0]), sig, args)
    assert r["status"] == "kept" and r["strike_rank"] == 1, r
    assert r["atm_strike"] == 141.0, r["atm_strike"]

    # timeframe-403 -> coverage miss, never a crash
    _pull._tf403_state["logged"] = True
    r = process_one(_FakeClient(raise_403=True), sig, args)
    assert r["status"] == "miss:timeframe_403", r["status"]

    # unsolvable IV (a print below intrinsic) -> atm_iv_unsolved
    deep = _FakeClient(strikes=[10.0], px=0.01)
    r = process_one(deep, dict(sig, spot_unadj=141.75), args)
    assert r["status"] == "miss:atm_iv_unsolved", r["status"]

    # short path -> kept, but pnl15 is null (never truncate-substituted) and
    # fwd_bars records exactly how unripe the horizon was
    r = process_one(_FakeClient(strikes=list(range(95, 165)), nbars=5), sig, args)
    assert r["status"] == "kept" and r["pnl15"] is None, r["status"]
    assert r["pnl_max"] is None and r["pnl_min"] is None and r["fwd_bars"] == 4, r
    log("  [7] miss paths OK (no_underlying / no_chain / no_atm_price / 403 / "
        "atm_iv_unsolved) + strike fallback + short-path pnl null")

    # -- 8. consolidate round-trip (None-early shape, dedupe, NaN->null) --------
    import tempfile
    import shutil
    tmp = Path(tempfile.mkdtemp(prefix="ivpanel_selftest_"))
    try:
        prog, parq = tmp / "_p.jsonl", tmp / "out.parquet"
        lines = []
        for i in range(150):                     # 150 all-None miss rows FIRST
            lines.append(json.dumps({**_base_row({"symbol": "M%d" % i,
                                                  "date": "2022-09-01",
                                                  "overall": 71, "vol_pct": None}),
                                     "status": "miss:no_underlying"}))
        lines.append(json.dumps(row, default=str))
        r2 = dict(row); r2["date"] = "2022-08-24"
        lines.append(json.dumps(r2, default=str))
        dup = dict(row); dup["overall"] = 99     # duplicate key -> last write wins
        lines.append(json.dumps(dup, default=str))
        prog.write_text("\n".join(lines) + "\n", encoding="utf-8")
        n = consolidate(prog, parq)
        assert n == 2, n                          # misses are NOT written
        df = pl.read_parquet(parq)
        assert df.columns == COLUMNS, df.columns
        assert df.filter(pl.col("date") == "2022-08-23")["overall"].to_list() == [99]
        assert df["atm_iv"].null_count() == 0
        assert df["date"].to_list() == ["2022-08-23", "2022-08-24"]   # sorted
        counts = journal_status_counts(prog)
        assert counts.get("miss:no_underlying") == 150 and counts.get("kept") == 2, counts
        # NaN is not null in polars -> the fill_nan choke point must catch it
        prog2 = tmp / "_p2.jsonl"
        nan = dict(row); nan["symbol"] = "NANX"; nan["pnl15"] = float("nan")
        prog2.write_text(json.dumps(nan, default=str) + "\n", encoding="utf-8")
        consolidate(prog2, tmp / "o2.parquet")
        assert pl.read_parquet(tmp / "o2.parquet")["pnl15"].null_count() == 1
        # empty journal -> empty typed frame with the full schema
        prog3 = tmp / "_p3.jsonl"
        prog3.write_text("", encoding="utf-8")
        assert consolidate(prog3, tmp / "o3.parquet") == 0
        assert pl.read_parquet(tmp / "o3.parquet").columns == COLUMNS
        # journal resume key is (symbol, date)
        assert load_done(prog) == {("M%d" % i, "2022-09-01") for i in range(150)} | {
            ("MMM", "2022-08-23"), ("MMM", "2022-08-24")}
        log("  [8] consolidate round-trip OK (kept-only, dedupe, NaN->null, empty, "
            "resume keys)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # -- 9. as-traded spot machinery is the SHARED one from pull.py -------------
    assert load_unadj_spots.__module__ == "pull"
    assert apply_forward_splits.__module__ == "pull"
    assert round(apply_forward_splits(["2022-08-23"], [118.52],
                                      [("2024-04-01", 1.196)])[0], 2) == 141.75
    assert _is_timeframe_403(RuntimeError(
        'Polygon 403: body={"status":"NOT_AUTHORIZED","message":'
        '"Your plan doesn\'t include this data timeframe."}'))
    assert not _is_timeframe_403(RuntimeError("Polygon 404: not found"))
    log("  [9] reusing pull.load_unadj_spots / apply_forward_splits / 403 "
        "classifier (no second spot implementation) OK")

    log("=== SELFTEST PASS ===")
    return 0


# ---------------------------------------------------------------------------
# main / CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Corrected Polygon BS-IV panel (as-traded spot throughout)")
    ap.add_argument("--min-overall", type=int, default=DEF_MIN_OVERALL,
                    help="score floor (default %d -- the OLD panel's floor, not 75)"
                         % DEF_MIN_OVERALL)
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--symbols", default="", help="comma-separated subset filter")
    ap.add_argument("--rate", type=float, default=DEF_RATE, help="risk-free rate")
    ap.add_argument("--div", type=float, default=DEF_DIV, help="continuous div yield")
    ap.add_argument("--dte-lo", type=int, default=DEF_DTE_LO)
    ap.add_argument("--dte-hi", type=int, default=DEF_DTE_HI)
    ap.add_argument("--otm-frac-put", type=float, default=DEF_OTM_PUT)
    ap.add_argument("--otm-frac-call", type=float, default=DEF_OTM_CALL)
    ap.add_argument("--strike-tries", type=int, default=DEF_STRIKE_TRIES,
                    help="nearest strikes tried per leg until one traded that day")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--flush-every", type=int, default=200)
    ap.add_argument("--limit", type=int, default=0, help="process first N pairs (smoke)")
    ap.add_argument("--refresh-spot", action="store_true",
                    help="re-fetch the as-traded spot cache from yfinance")
    ap.add_argument("--consolidate-only", action="store_true",
                    help="rebuild the parquet from the journal and exit (offline)")
    ap.add_argument("--compare-old", action="store_true",
                    help="offline old-vs-corrected report on overlapping rows")
    ap.add_argument("--selftest", action="store_true",
                    help="offline unit tests and exit (no key, no network, no DB)")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.consolidate_only:
        consolidate()
        return 0
    if args.compare_old:
        return compare_old()
    return run_pull(args)


if __name__ == "__main__":
    raise SystemExit(main())
