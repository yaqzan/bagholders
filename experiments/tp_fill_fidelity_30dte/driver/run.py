"""run.py -- TP-fill fidelity, 30-DTE (experiments/tp_fill_fidelity_30dte).

PREREG-LOCKED measurement (see ../TASK.md, ../PREREG.md, ../BRIEF_BUILDER.md --
read those FIRST; this file implements them, it does not restate the spec).
Single entrypoint: --selftest | --smoke N | --full | --analyze
Plus one contingency action: --fetch-ohlc-fallback (PREREG Sec1 fallback pull,
queue-submitted only, never run directly outside `trader queue submit`).

Measured object: does the 30-DTE engine's own TP declaration (compute_trade_
outcome's intrabar sigma-barrier touch) correspond to a REAL option contract
print at/above the modeled barrier premium, on the day it declares?

======================================================================
SCHEMA GATE STATUS (2026-08-10 builder run) -- READ BEFORE TOUCHING P1/P2
======================================================================
PREREG Sec1's underlying-OHLC gate FAILED on first recon: the primary cached
parquet (.cache/flatfile_exploitation/underlying_ohlc_2022_2026.parquet) has
schema {symbol,date,close,high,low} -- NO 'open' column (verified against the
real file on disk; also verified in its builder, ff_signals.py:170-176, which
never SELECTs PriceHistory.open). PREREG Sec1 requires "full OHLC" as an
unconditional AND-gate before the file may be used; this fails it regardless
of whether the 3 present columns match MySQL (they do, by construction -- see
check_underlying_ohlc_schema / spot_audit_underlying below and the builder
report). Per PREREG Sec1 + BRIEF_BUILDER.md Sec2 + hard rules, this STOPS the
pipeline before any real P1 execution: the fallback bulk pull
(symbol/date/open/high/low/close, 2022-04-01..2026-08-08, ledger symbols) is
queued via `--fetch-ohlc-fallback`, never run inline. See the builder's
report-back for the queued task id. P1/P2 code below is fully implemented and
--selftest-verified against synthetic bars; it has NOT yet run against real
data. Re-run `python run.py --smoke 25` once the fallback parquet lands at
.cache/tp_fill_fidelity_30dte/underlying_ohlc.parquet -- OHLC_CANDIDATES below
already prefers it automatically, no code change needed.

A second, independent, narrower gap: `ledger_v2/paths/year=*/part.parquet`
(the REAL option contract path data) also has no 'open' column (schema:
signal_id,date,close,high,low,volume,transactions -- verified against
build_ledger_v2.py's PATH_COLUMNS + the real files on disk). This blocks ONLY
PREREG Sec3's gap-open decomposition (open_mult, gap-open share gamma) --
every OTHER Sec3/Sec4 metric (fill/miss/no-print/shortfall/overshoot(high-
basis)/close-basis/never-fill/days-late) is unaffected. gap_open_decomposition()
below is implemented and selftest-verified on synthetic opens; it is simply
never fed a real one today. See KNOWN_SCHEMA_GAPS and the report-back for the
full interpretation note -- this is flagged for orchestrator adjudication, not
silently patched.
======================================================================
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

# ---------------------------------------------------------------------------
# sys.path pin -- strict while-remove-then-insert(0) idiom (traps.md "Worktree
# PYTHONPATH trap"; this repo's own convention, mirrored from build_liquidity_
# map.py / build_minute_fidelity.py). This experiment runs from the MAIN
# checkout (T9) -- the post-import assert on mc.__file__ re-verifies this at
# monte_carlo-import time too.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)
assert os.path.isfile(os.path.join(_ROOT, "CLAUDE.md")), \
    f"sys.path pin landed on {_ROOT!r}, expected the Trader repo root"

# mc_patch.py is imported BY PATH (BRIEF_BUILDER.md Sec "Implementation
# order" #4) -- it lives in a sibling experiment, not a package we own.
_MC_PATCH_DIR = os.path.join(_ROOT, "experiments", "tpsl_refine_2026_08", "driver")
if _MC_PATCH_DIR not in sys.path:
    sys.path.insert(0, _MC_PATCH_DIR)
import mc_patch  # noqa: E402  (set_tpsl, atomic_write_json, pct)

import polars as pl  # noqa: E402

EXP_DIR = Path(_ROOT) / "experiments" / "tp_fill_fidelity_30dte"
OUT_DIR = EXP_DIR / "out"
LOG_DIR = EXP_DIR / "logs"
STATE_DIR = EXP_DIR / "driver" / "state"

# ---------------------------------------------------------------------------
# Locked input paths (PREREG Sec1). All read-only; never written by this file
# except the ONE fallback artifact under CACHE_ROOT (PREREG's own fallback
# path) and everything under OUT_DIR/STATE_DIR/LOG_DIR (this experiment).
# ---------------------------------------------------------------------------
SIGNALS_PARQUET = Path(_ROOT) / ".cache" / "flatfile_exploitation" / "signals_v74_2022_2026.parquet"
LEDGER_PARQUET = Path("B:/polygon_derived/ledger_v2/ledger.parquet")
PATHS_GLOB = str(Path("B:/polygon_derived/ledger_v2/paths") / "year=*" / "part.parquet")
LIQUIDITY_PARQUET = Path("B:/polygon_derived/liquidity_map/signal_liquidity.parquet")
BINDINGS_JSON = Path("B:/polygon_derived/minute_fidelity/bindings.json")

OHLC_PRIMARY = Path(_ROOT) / ".cache" / "flatfile_exploitation" / "underlying_ohlc_2022_2026.parquet"
CACHE_ROOT = Path(_ROOT) / ".cache" / "tp_fill_fidelity_30dte"
OHLC_FALLBACK = CACHE_ROOT / "underlying_ohlc.parquet"
# Preference order: fallback (once it exists -- full OHLC, wider date range)
# beats primary (missing 'open'). Single place to fix if a 3rd candidate ever
# shows up.
OHLC_CANDIDATES = [OHLC_FALLBACK, OHLC_PRIMARY]

FALLBACK_PULL_START = date(2022, 4, 1)
FALLBACK_PULL_END = date(2026, 8, 8)

# PREREG Sec9 AMENDMENT A1 (commit 3a4b36fd): contract OPEN's sole source.
# Partitioned underlying=SYMBOL/year=YYYY/part.parquet -- the same FF-0 hive
# build_ledger_v2.py's PartitionCache reads (that IS this artifact's
# canonical reader; mirrored here read-only).
CONTRACT_DAY_INDEX_ROOT = Path("B:/polygon_derived/contract_day_index")
# A1: "index high/close must equal paths high/close; mismatch share ... >1% = STOP"
INTEGRITY_MISMATCH_TOL = 0.005
INTEGRITY_MISMATCH_STOP_PCT = 1.0

# PREREG Sec2 arms: (tp, sl). ARM-30 = incumbent/production. ARM-15 = leading
# tpsl_refine Phase-A candidate cell.
ARMS = {
    "arm30": {"tp": 0.30, "sl": -0.70, "label": "ARM-30 (incumbent TP+30/SL-70)"},
    "arm15": {"tp": 0.15, "sl": -0.90, "label": "ARM-15 (candidate TP+15/SL-90)"},
}
ARM_ORDER = ["arm30", "arm15"]  # PREREG Sec2: "Run ARM-30 fully, then ARM-15" -- never interleave (T8/set_tpsl statefulness)

DTE_TIGHT = (25, 38)          # PREREG Sec4 matched-filter band (FF-1 precedent)
SMOKE_DEFAULT_N = 25
YEARS = (2022, 2023, 2024, 2025, 2026)
QUANTILE_MIN_N = 30            # PREREG Sec4: "Cells with N < 30: suppress quantiles, print N only"
N_FLOOR_MATCHED_POOLED = 500   # PREREG Sec6.5


def log(msg) -> None:
    print(msg, flush=True)


# ===========================================================================
# COLMAP -- PREREG's semantic names -> actual on-disk column names. Single
# place to fix (BRIEF_BUILDER.md Sec "Implementation order" #1). Every name on
# the right was verified against the REAL files 2026-08-10 (see the module
# docstring's "SCHEMA GATE STATUS" + colmap_presence_check() below, which
# re-verifies this list against the real files every --selftest run).
# ===========================================================================
COLMAP = {
    "signals": {
        "symbol": "symbol", "signal_date": "date", "overall": "overall",
        "version_id": "version_id", "close_adj": "close", "close_unadj": "close_unadj",
        "sigma": "sigma",
    },
    "ledger": {
        "signal_id": "signal_id", "symbol": "symbol", "signal_date": "entry_date",
        "entry_premium_real": "entry_premium", "spot_unadj": "entry_spot_unadj",
        "sigma": "sigma", "moneyness": "moneyness", "status": "status",
        "liquid_entry": "liquid_entry", "dte_actual": "dte_actual",
        "contract_ticker": "ticker", "expiry": "expiry", "walk_end": "walk_end",
        "path_end_reason": "path_end_reason",
        # tp_touch_date / sl_touch_date EXIST but are BANNED from use (T2 --
        # FF-1's accepted close-only deviation; never the engine's own
        # high-basis convention). Listed here only so colmap_presence_check
        # documents that the ban is a deliberate omission, not an oversight.
        "_BANNED_tp_touch_date": "tp_touch_date", "_BANNED_sl_touch_date": "sl_touch_date",
    },
    "paths": {
        "signal_id": "signal_id", "touch_date": "date", "contract_close": "close",
        "contract_high": "high", "contract_low": "low",
        # "contract_open": MISSING -- RESOLVED by PREREG Sec9 A1 via
        # contract_day_index (below), never re-added here (paths itself still
        # lacks the column; A1 sources 'open' from a different asset).
    },
    "underlying_ohlc": {
        "symbol": "symbol", "date": "date", "close": "close", "high": "high", "low": "low",
        "open": "open",   # present in OHLC_FALLBACK; MISSING in OHLC_PRIMARY (GAP A)
    },
    "liquidity": {
        "symbol": "symbol", "date": "date", "metric": "opt_vol_30d_atm",
    },
    "contract_day_index": {
        # A1: SOLE source of the contract 'open', joined on (ticker, date==T).
        "contract_ticker": "ticker", "touch_date": "date", "contract_open": "open",
        "contract_high": "high", "contract_close": "close", "underlying": "underlying",
    },
}

# Columns intentionally absent from a given real file, keyed "<asset>.<col>".
# colmap_presence_check() treats these as expected-missing (not a failure) but
# still reports them loudly. See module docstring "SCHEMA GATE STATUS".
KNOWN_SCHEMA_GAPS = {
    "underlying_ohlc_primary.open": (
        "underlying_ohlc_2022_2026.parquet has no 'open' column (schema: "
        "symbol,date,close,high,low). PREREG Sec1's full-OHLC gate FAILS on "
        "this file -- triggers the pre-planned fallback bulk MySQL pull "
        "(--fetch-ohlc-fallback). Not a COLMAP rename -- the column does not "
        "exist under any name in this file."
    ),
    "paths.open": (
        "ledger_v2/paths/year=*/part.parquet has no 'open' column (schema: "
        "signal_id,date,close,high,low,volume,transactions -- "
        "build_ledger_v2.py's PATH_COLUMNS never included it). RESOLVED by "
        "PREREG Sec9 AMENDMENT A1 (commit 3a4b36fd, 2026-08-10): "
        "contract_day_index (FF-0) is the SOLE source of contract 'open', "
        "joined on (ticker, date==T) via ContractOpenIndex. Ledger paths "
        "remain primary for entry premium/high/close; an integrity cross-"
        "check (integrity_cross_check()) enforces index high/close == paths "
        "high/close on every joined row, >1% mismatch share = STOP "
        "(compute_tripwires 'A1 integrity cross-check' + dose_accounting.md)."
    ),
}


# ===========================================================================
# Small IO helpers
# ===========================================================================
def atomic_write_json(obj, path) -> None:
    mc_patch.atomic_write_json(str(path), obj)


def atomic_write_text(text: str, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def atomic_write_parquet(df: pl.DataFrame, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.write_parquet(tmp, compression="snappy")
    os.replace(tmp, path)


# ===========================================================================
# PURE helpers -- offline-testable (--selftest), no B:/MySQL dependency.
# ===========================================================================
def wilson_ci(successes: int, n: int, z: float = 1.96):
    """Wilson score 95% CI. Returns (lo, hi) or (None, None) if n<=0."""
    if n <= 0:
        return (None, None)
    phat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = phat + z2 / (2 * n)
    margin = z * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n))
    lo = (center - margin) / denom
    hi = (center + margin) / denom
    return (max(0.0, lo), min(1.0, hi))


def safe_quantiles(values, min_n: int = QUANTILE_MIN_N, qs=(0.25, 0.5, 0.75, 0.90)):
    """PREREG Sec4: 'Cells with N < 30: suppress quantiles, print N only.'
    `values` need not be pre-sorted. Uses mc_patch.pct (nearest-rank,
    mirrors monte_carlo.py's own _pct) for consistency with the engine's own
    percentile convention -- reused, not reinvented."""
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    out = {"n": n, "suppressed": n < min_n}
    for q in qs:
        key = f"p{int(round(q * 100))}"
        out[key] = None if n < min_n else mc_patch.pct(vals, q)
    return out


def deadline_for(signal_date, hold_cal_days: int = 27):
    """PREREG Sec2/Sec3: signal_date + HOLD_CAL_DAYS calendar days (raw
    calendar arithmetic -- NOT trading-day aware; weekends/holidays just fall
    where they fall, mirroring monte_carlo.py's own `_deadline` computation
    mc:2478)."""
    return signal_date + timedelta(days=hold_cal_days)


def liquidity_tier_for(value, edges):
    """PREREG Sec1: FF-3'/FF-4 liquidity tier, t1 (least liquid) .. t5 (most).
    `edges` = 4 ascending cut points read live from bindings.json
    (6_gap_through_sl.liquidity_tier_edges). None if value is None."""
    if value is None:
        return None
    if value <= edges[0]:
        return "t1"
    if value <= edges[1]:
        return "t2"
    if value <= edges[2]:
        return "t3"
    if value <= edges[3]:
        return "t4"
    return "t5"


def classify_join(T, B, expiry, walk_end, path_end_reason, path_by_date, last_path_date):
    """PREREG Sec3 primary classification for one declared kind in
    {'tp','both'} event, on the touch date T. Returns (bucket, reason):
      bucket in {'fill','miss_traded_below','miss_no_print','unjoinable'}.
    UNJOINABLE reasons (PREREG Sec3, all three named cases):
      - "contract expired before T"          (T > expiry)
      - "T beyond ledger walk window"        (T > walk_end)
      - "path ended before T (no_more_bars)" (path_end_reason marker, T5/T10)
      - "no path rows for this contract"     (defensive; structurally should
        not occur for a status=='kept' row, which requires >=2 forward rows)
    Check ORDER matters: walk_end = min(expiry, entry_date+45cal, FF-1's
    WALK_MAX_CAL_DAYS) by construction, so walk_end <= expiry ALWAYS --
    `T > expiry` therefore always implies `T > walk_end` but not vice versa.
    Checking expiry FIRST reports the more specific/informative reason (the
    contract genuinely expired) whenever it applies; checking walk_end
    first would make the expiry branch permanently unreachable dead code
    (caught this via --selftest, see LESSONS.md-style note: a synthetic
    case with walk_end==expiry always hit the walk_end branch until this
    was reordered).
    """
    if not path_by_date:
        return "unjoinable", "no path rows for this contract"
    if T > expiry:
        return "unjoinable", "contract expired before T"
    if T > walk_end:
        return "unjoinable", "T beyond ledger walk window"
    if last_path_date is not None and T > last_path_date and path_end_reason == "no_more_bars":
        return "unjoinable", "path ended before T (no_more_bars)"
    row = path_by_date.get(T)
    if row is None:
        return "miss_no_print", None          # T5: day_aggs traded-days-only, never forward-fill
    high = row.get("high")
    if high is not None and high >= B:
        return "fill", None
    return "miss_traded_below", None


def timing_walk(T, deadline, B, path_rows_sorted):
    """PREREG Sec3 timing secondary: first date in [T,deadline] with
    high>=B. `path_rows_sorted`: list of (date,high) tuples for one
    signal_id, sorted ascending by date (any range -- filtered here).
    Returns (bucket, days_late): bucket in {'same_day','late','never_fill'};
    days_late is None unless bucket=='late'. Running out of real path rows
    before finding a fill (path-goes-silent, LESSONS.md 2026-08-10) resolves
    to 'never_fill' by construction -- no special-casing needed.

    UNITS: days_late = (d - T).days on two real datetime.date objects (both
    T and every path row date are genuine calendar dates, never a trading-
    bar index) -- so this is CALENDAR days, matching PREREG's own deadline
    arithmetic (deadline_for = signal_date + timedelta(calendar days)) and
    the requirement that days_late and the deadline live in the same units.
    A weekend/holiday gap between two consecutive traded days is counted in
    full here (e.g. Friday fill -> following Monday fill = 3 calendar days,
    not 1 trading day) -- deliberately, since the 27-calendar-day hard-sell
    deadline is itself calendar-denominated."""
    for d, h in path_rows_sorted:
        if d < T:
            continue
        if d > deadline:
            break
        if h is not None and h >= B:
            return ("same_day", None) if d == T else ("late", (d - T).days)
    return "never_fill", None


def gap_open_decomposition(open_price, B, entry_premium_real):
    """PREREG Sec3 gap-open decomposition: gap-open fill iff open>=B at T.
    Returns (is_gap_open, open_mult) or (None, None) if open_price is
    unavailable (GAP B -- see KNOWN_SCHEMA_GAPS['paths.open']). Implemented +
    selftest-verified now so wiring a real 'open' later is a one-line change
    at the call site, not a redesign."""
    if open_price is None or not entry_premium_real:
        return None, None
    open_mult = open_price / entry_premium_real
    return (open_price >= B), open_mult


def split_primary_and_both(declarations):
    """PREREG Sec3: primary event = kind=='tp'; kind=='both' tabulated
    separately, excluded from primary rates; 'sl'/'hard'/'prem' are non-
    events (census only); None-kind = engine None-return (census only,
    T10). Returns (primary, both, census_counts) where census_counts is a
    dict of kind/none_reason -> count over the FULL input (nothing dropped)."""
    primary, both = [], []
    census = {}
    for d in declarations:
        k = d.get("kind")
        label = k if k is not None else f"none:{d.get('none_reason')}"
        census[label] = census.get(label, 0) + 1
        if k == "tp":
            primary.append(d)
        elif k == "both":
            both.append(d)
    return primary, both, census


def _classify_none_reason(dates, closes, signal_date, hold_cal_days, realized_vol_fn):
    """Diagnostic re-derivation of WHY mc.compute_trade_outcome returned None
    (T10) -- mirrors its own None-branches (mc:2417-2454) without duplicating
    the barrier walk. Never used to alter the engine's own return value."""
    try:
        base_idx = dates.index(signal_date)
    except ValueError:
        return "signal_date_not_in_bars"
    vol = realized_vol_fn(closes, base_idx)
    if vol is None or vol <= 0:
        return "vol_none_or_nonpositive"
    dl = deadline_for(dates[base_idx], hold_cal_days)
    end_idx = base_idx + 1
    while end_idx < len(dates) and dates[end_idx] <= dl:
        end_idx += 1
    if end_idx <= base_idx + 1:
        return "window_too_short"
    return "unknown_reason"  # should not occur if compute_trade_outcome actually returned None


def build_sym_bars(dates_l, closes_l, highs_l, lows_l, opens_l):
    """Tuple ORDER the engine expects: (date, close, high, low, open)
    (mc:2418-2422, PREREG Sec2). Pure zip -- kept as a named function so the
    order is asserted in exactly one place."""
    return list(zip(dates_l, closes_l, highs_l, lows_l, opens_l))


def _compare_ohlc_row(check_cols, parquet_vals: dict, db_vals: dict, tol: float = 0.005):
    """PURE (offline-testable). PREREG Sec9 A1 spot-audit comparator, factored
    out of spot_audit_underlying() so the T1 close/close_unadj-mixup detector
    is selftest-able without a live MySQL connection. `db_vals` may carry
    'close_unadj' alongside the checked columns -- when the 'close' column
    mismatches but the parquet value matches close_unadj instead (within
    tol), that POSITIVELY IDENTIFIES the exact failure mode PREREG Sec9
    names ("grabbing close_unadj instead of the engine's adjusted close"),
    flagged as close_unadj_bug_suspected rather than a generic mismatch.
    Returns {'status': 'match'|'MISMATCH', 'mismatches': [(col,parquet_v,
    db_v),...], 'close_unadj_bug_suspected': bool}."""
    mismatches = []
    close_unadj_bug = False
    for c in check_cols:
        dbv = db_vals.get(c)
        pv = parquet_vals.get(c)
        bad = dbv is None or pv is None or abs(dbv - pv) > tol
        if bad:
            mismatches.append((c, pv, dbv))
            if c == "close":
                cu = db_vals.get("close_unadj")
                if cu is not None and pv is not None and abs(cu - pv) <= tol:
                    close_unadj_bug = True
    return {"status": "match" if not mismatches else "MISMATCH", "mismatches": mismatches,
            "close_unadj_bug_suspected": close_unadj_bug}


def integrity_cross_check(paths_row: dict, index_row: dict, tol: float = INTEGRITY_MISMATCH_TOL):
    """PURE (offline-testable). PREREG Sec9 A1: on a (paths_row, index_row)
    pair that BOTH exist for the same (ticker, date==T), index high/close
    must equal paths high/close (both parquets are FF-0-derived from the same
    underlying day_aggs, extracted by two independent pipelines -- a
    mismatch signals a join or extraction bug, not real-world noise). Each
    row is a dict with at least 'high'/'close' keys. Returns (is_match: bool,
    detail: dict|None) -- detail populated ONLY on mismatch (for
    dose_accounting.md's capped mismatch sample)."""
    h_ok = (paths_row.get("high") is not None and index_row.get("high") is not None
            and abs(paths_row["high"] - index_row["high"]) <= tol)
    c_ok = (paths_row.get("close") is not None and index_row.get("close") is not None
            and abs(paths_row["close"] - index_row["close"]) <= tol)
    if h_ok and c_ok:
        return True, None
    return False, {"paths_high": paths_row.get("high"), "index_high": index_row.get("high"),
                    "paths_close": paths_row.get("close"), "index_close": index_row.get("close")}


class ContractOpenIndex:
    """PREREG Sec9 AMENDMENT A1: FF-0 `contract_day_index` as the SOLE source
    of the contract 'open' column, joined on (contract ticker, touch date T).
    Partitioned `underlying=SYMBOL/year=YYYY/part.parquet` -- the identical
    hive layout build_ledger_v2.py's own PartitionCache reads (that IS this
    artifact's canonical reader; mirrored here, read-only, never written).
    Cached per (underlying,year) so repeat lookups for the same underlying/
    year cost one parquet read total. `_cache` is directly injectable for
    --selftest (mirrors build_ledger_v2.py's own PartitionCache._cache
    seeding pattern -- no real B:/ dependency needed to test the lookup
    logic)."""

    def __init__(self, root=CONTRACT_DAY_INDEX_ROOT):
        self.root = root
        self._cache: dict = {}   # (underlying, year) -> {(ticker,date): {'open','high','close'}} | None
        self.n_files_read = 0

    def _partition(self, underlying, year):
        key = (underlying, year)
        if key not in self._cache:
            p = self.root / f"underlying={underlying}" / f"year={year}" / "part.parquet"
            if p.exists():
                df = pl.read_parquet(p, columns=["ticker", "date", "open", "high", "close"])
                idx = {}
                for t, d, o, h, c in zip(df["ticker"].to_list(), df["date"].to_list(),
                                          df["open"].to_list(), df["high"].to_list(), df["close"].to_list()):
                    idx[(t, d)] = {"open": o, "high": h, "close": c}
                self._cache[key] = idx
                self.n_files_read += 1
            else:
                self._cache[key] = None
        return self._cache[key]

    def lookup(self, underlying, ticker, touch_date):
        """Returns {'open','high','close'} or None -- no partition for
        (underlying,year), or no row for this exact (ticker,date)."""
        part = self._partition(underlying, touch_date.year)
        if part is None:
            return None
        return part.get((ticker, touch_date))


# ===========================================================================
# Schema recon (read-only) -- BRIEF_BUILDER.md Sec "Implementation order" #1
# ===========================================================================
_RECON_ASSETS = [
    ("signals", SIGNALS_PARQUET),
    ("ledger", LEDGER_PARQUET),
    ("underlying_ohlc_primary", OHLC_PRIMARY),
    ("liquidity", LIQUIDITY_PARQUET),
]


def _schema_of(path) -> "list[str] | None":
    path = Path(path)
    if not path.exists():
        return None
    return list(pl.scan_parquet(path).collect_schema().names())


def recon_schemas(sample_rows: int = 3) -> dict:
    """Prints schema + N sample rows for every PREREG Sec1 asset, plus ONE
    paths year partition and FF-4 bindings.json. Read-only, foreground.
    Returns {asset: [columns]} for colmap_presence_check()."""
    out = {}
    for name, path in _RECON_ASSETS:
        log(f"=== {name}: {path} ===")
        path = Path(path)
        if not path.exists():
            log("  MISSING")
            out[name] = None
            continue
        df = pl.read_parquet(path)
        log(f"  shape={df.shape}")
        log(f"  schema={df.schema}")
        log(str(df.head(sample_rows)))
        out[name] = list(df.schema.names())

    paths_files = sorted(glob.glob(PATHS_GLOB))
    log(f"=== paths (one year partition of {len(paths_files)}): {paths_files[:1]} ===")
    if paths_files:
        df = pl.read_parquet(paths_files[0])
        log(f"  shape={df.shape}")
        log(f"  schema={df.schema}")
        log(str(df.head(sample_rows)))
        out["paths"] = list(df.schema.names())
    else:
        out["paths"] = None

    cdi_files = sorted(glob.glob(str(CONTRACT_DAY_INDEX_ROOT / "underlying=*" / "year=*" / "part.parquet")))
    log(f"=== contract_day_index (one partition of {len(cdi_files)}): {cdi_files[:1]} ===")
    if cdi_files:
        df = pl.read_parquet(cdi_files[0])
        log(f"  shape={df.shape}")
        log(f"  schema={df.schema}")
        log(str(df.head(sample_rows)))
        out["contract_day_index"] = list(df.schema.names())
    else:
        out["contract_day_index"] = None

    log(f"=== FF-4 bindings.json: {BINDINGS_JSON} ===")
    if BINDINGS_JSON.exists():
        with open(BINDINGS_JSON, encoding="utf-8") as f:
            b = json.load(f)
        log(f"  keys={list(b.keys())}")
        log(f"  6_gap_through_sl={b.get('6_gap_through_sl')}")
        out["bindings"] = b
    else:
        out["bindings"] = None
    return out


def colmap_presence_check(real_schemas: dict):
    """Verifies every COLMAP target column exists in its real file's schema,
    except columns explicitly listed in KNOWN_SCHEMA_GAPS. Returns list of
    issue strings (empty = clean)."""
    issues = []
    asset_to_schema_key = {
        "signals": "signals", "ledger": "ledger", "paths": "paths",
        "underlying_ohlc": "underlying_ohlc_primary", "liquidity": "liquidity",
        "contract_day_index": "contract_day_index",
    }
    for asset, mapping in COLMAP.items():
        schema_key = asset_to_schema_key[asset]
        cols = real_schemas.get(schema_key)
        if cols is None:
            issues.append(f"{asset}: real file schema unavailable (missing on disk?)")
            continue
        for semantic, actual in mapping.items():
            if semantic.startswith("_BANNED_"):
                continue  # documented-omission entries, not presence checks
            gap_key_a = f"{schema_key}.{actual}"
            gap_key_b = f"{asset}.{actual}"
            if actual not in cols:
                if gap_key_a in KNOWN_SCHEMA_GAPS or gap_key_b in KNOWN_SCHEMA_GAPS:
                    continue  # expected, documented gap
                issues.append(f"{asset}.{semantic} -> {actual!r} NOT FOUND in {schema_key} "
                               f"(columns={cols})")
    return issues


# ===========================================================================
# Underlying OHLC gate (PREREG Sec1) + spot audit + fallback pull
# ===========================================================================
def check_underlying_ohlc_schema(path):
    """PREREG Sec1 gate: full OHLC (open+high+low+close, plus symbol+date)
    present. Returns (passed: bool, reason: str, columns: list|None)."""
    path = Path(path)
    if not path.exists():
        return False, f"file not found: {path}", None
    cols = _schema_of(path)
    required = {"symbol", "date", "open", "high", "low", "close"}
    missing = sorted(required - set(cols))
    if missing:
        return False, f"missing columns: {missing} (schema={cols})", cols
    return True, "schema OK (full OHLC present)", cols


def resolve_ohlc_path():
    """Walks OHLC_CANDIDATES in preference order (fallback before primary --
    see OHLC_CANDIDATES comment). Returns (path, passed, reason, columns) for
    the FIRST candidate that passes the gate; if none pass, returns the
    first EXISTING candidate's failing result (so the report names a real
    file, not nothing); if no candidate exists at all, returns
    (None, False, "no candidate file exists", None)."""
    first_existing_failure = None
    for cand in OHLC_CANDIDATES:
        if not cand.exists():
            continue
        ok, reason, cols = check_underlying_ohlc_schema(cand)
        if ok:
            return cand, True, reason, cols
        if first_existing_failure is None:
            first_existing_failure = (cand, False, reason, cols)
    if first_existing_failure is not None:
        return first_existing_failure
    return None, False, "no candidate file exists", None


def spot_audit_underlying(ohlc_df: pl.DataFrame, symbols: list, n_symbols: int = 5,
                            n_dates: int = 3, tol: float = 0.005):
    """PREREG Sec1 spot audit (scope-shifted to the fallback parquet by Sec9
    A1): N symbols x M dates vs MySQL price_history, ENGINE convention
    (`.close`, never `.close_unadj`). Read-only, defensive (T11) -- never
    retries on failure (zombie-query trap), returns an error string instead.
    Checks whatever columns ohlc_df actually has. Comparison logic lives in
    the pure _compare_ohlc_row() (selftest-able without MySQL); this
    function is just the IO boundary (pick rows, fetch DB rows, delegate)."""
    from database.models.technical import PriceHistory  # lazy: only this fn touches MySQL

    check_cols = [c for c in ("close", "high", "low", "open") if c in ohlc_df.columns]
    picks = []
    for sym in symbols[:n_symbols]:
        sub = ohlc_df.filter(pl.col("symbol") == sym).sort("date")
        if sub.height == 0:
            continue
        idxs = sorted(set(max(0, min(sub.height - 1, i))
                           for i in (0, sub.height // 2, sub.height - 1)))[:n_dates]
        for i in idxs:
            row = {"symbol": sym, "date": sub["date"][i]}
            for c in check_cols:
                row[c] = sub[c][i]
            picks.append(row)

    results = []
    try:
        for p in picks:
            dbrow = (PriceHistory
                     .select(PriceHistory.close, PriceHistory.high, PriceHistory.low,
                             PriceHistory.open, PriceHistory.close_unadj)
                     .where(PriceHistory.symbol == p["symbol"], PriceHistory.date == p["date"])
                     .dicts().first())
            if dbrow is None:
                results.append({"symbol": p["symbol"], "date": str(p["date"]), "status": "no_db_row",
                                 "mismatches": [], "close_unadj_bug_suspected": False})
                continue
            db_vals = {c: (float(dbrow[c]) if dbrow.get(c) is not None else None) for c in check_cols}
            db_vals["close_unadj"] = float(dbrow["close_unadj"]) if dbrow.get("close_unadj") is not None else None
            parquet_vals = {c: p[c] for c in check_cols}
            cmp = _compare_ohlc_row(check_cols, parquet_vals, db_vals, tol)
            results.append({"symbol": p["symbol"], "date": str(p["date"]), **cmp})
    except Exception as e:  # noqa: BLE001 -- defensive DB read, never a retry loop (T11)
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "results": results,
                "checked_columns": check_cols}

    n_mismatch = sum(1 for r in results if r["status"] != "match")
    any_close_unadj_bug = any(r.get("close_unadj_bug_suspected") for r in results)
    return {"ok": n_mismatch == 0, "error": None, "results": results,
            "checked_columns": check_cols, "n_checked": len(results), "n_mismatch": n_mismatch,
            "close_unadj_bug_suspected": any_close_unadj_bug}


def _fallback_audit_cache_key(path):
    """Cache-invalidation key for the fallback-parquet spot-audit verdict --
    (path, mtime, size), so a re-pulled fallback parquet forces a fresh
    audit rather than trusting a stale cached verdict for a different file."""
    st = os.stat(path)
    return {"path": str(path), "mtime": st.st_mtime, "size": st.st_size}


def ensure_fallback_spot_audit(ohlc_path, cached_gate_state: dict):
    """PREREG Sec9 A1: the Sec1 5x3 MySQL value audit runs against the NEW
    fallback parquet (guards the PULL CODE -- the named failure mode is
    grabbing close_unadj instead of the engine's adjusted close), ONCE,
    foreground, verdict cached to driver/state/ohlc_gate.json so a later
    --full (queued, DB-free) never needs to touch MySQL itself. Returns the
    (possibly cached) audit verdict dict, or None if `ohlc_path` is not the
    fallback candidate (the primary was already established gate-failing
    and is never audited)."""
    if Path(ohlc_path) != OHLC_FALLBACK:
        return None
    cache_key = _fallback_audit_cache_key(ohlc_path)
    cached = (cached_gate_state or {}).get("fallback_spot_audit")
    if cached and cached.get("cache_key") == cache_key:
        log("fallback spot-audit: using cached verdict (file unchanged since last audit)")
        return cached
    log("fallback spot-audit: running (first time for this file, or file changed since last audit)...")
    ledger_kept = load_ledger_kept()
    symbols = sorted(ledger_kept["symbol"].unique().to_list())
    ohlc_df = pl.read_parquet(ohlc_path)
    audit = spot_audit_underlying(ohlc_df, symbols)
    audit["cache_key"] = cache_key
    audit["audited_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return audit


def fetch_ohlc_fallback(out_path=OHLC_FALLBACK, start=FALLBACK_PULL_START, end=FALLBACK_PULL_END,
                         ledger_path=LEDGER_PARQUET):
    """PREREG Sec1 fallback: ONE bulk pull of symbol/date/open/high/low/close
    from MySQL price_history (ADJUSTED convention -- .close, never
    .close_unadj -- the engine's own convention) for every symbol appearing in
    ledger_v2/ledger.parquet, chunked by YEAR (never one giant query -- tight
    client read_timeout, traps.md zombie-query cascade). `--db heavy` queue
    job ONLY -- never run directly outside `trader queue submit` (T13)."""
    from database.models.technical import PriceHistory  # lazy: only this fn touches MySQL

    ledger = pl.read_parquet(ledger_path)
    symbols = sorted(ledger["symbol"].unique().to_list())
    log(f"fallback OHLC pull: {len(symbols)} ledger symbols, {start}..{end}")

    out_symbol, out_date = [], []
    out_open, out_high, out_low, out_close = [], [], [], []
    for yr in range(start.year, end.year + 1):
        lo = max(start, date(yr, 1, 1))
        hi = min(end, date(yr, 12, 31))
        if lo > hi:
            continue
        t0 = time.time()
        rows = list(
            PriceHistory.select(PriceHistory.symbol, PriceHistory.date, PriceHistory.open,
                                 PriceHistory.high, PriceHistory.low, PriceHistory.close)
            .where(PriceHistory.symbol.in_(symbols), PriceHistory.date >= lo, PriceHistory.date <= hi)
            .order_by(PriceHistory.symbol, PriceHistory.date)
            .dicts()
        )
        log(f"  chunk {yr} [{lo}..{hi}]: {len(rows)} rows ({time.time() - t0:.1f}s)")
        for r in rows:
            out_symbol.append(r["symbol"]); out_date.append(r["date"])
            out_open.append(float(r["open"]) if r["open"] is not None else None)
            out_high.append(float(r["high"]) if r["high"] is not None else None)
            out_low.append(float(r["low"]) if r["low"] is not None else None)
            out_close.append(float(r["close"]) if r["close"] is not None else None)

    df = pl.DataFrame(
        {"symbol": out_symbol, "date": out_date, "open": out_open,
         "high": out_high, "low": out_low, "close": out_close},
        schema={"symbol": pl.String, "date": pl.Date, "open": pl.Float64,
                "high": pl.Float64, "low": pl.Float64, "close": pl.Float64},
    )
    # T3: an all-None float column infers Null dtype -- cast BEFORE fill_nan.
    for c in ("open", "high", "low", "close"):
        df = df.with_columns(pl.col(c).cast(pl.Float64, strict=False))
    df = df.with_columns([pl.col(c).fill_nan(None) for c in ("open", "high", "low", "close")])
    df = df.sort(["symbol", "date"])
    atomic_write_parquet(df, out_path)
    log(f"wrote {out_path} ({df.height} rows, {df['symbol'].n_unique()} symbols)")
    return df


# ===========================================================================
# monte_carlo import + PREREG Sec2 runtime asserts
# ===========================================================================
def apply_prereg_env_pins():
    """PREREG Sec2 EXACT pin list -- deliberately narrower than
    mc_patch.apply_frozen_pins() (which also sets MC_RETURN_PATHS/MC_WORKERS,
    scoped to the full simulate() pipeline this measurement never calls).
    Must run before `import monte_carlo`. Idempotent."""
    os.environ["MC_NO_DB_PERSIST"] = "1"
    os.environ["LIQUIDITY_FLOOR"] = "0.0"
    os.environ["PYTHONIOENCODING"] = "utf-8"


def import_mc_with_asserts():
    """PREREG Sec2: env pins BEFORE `import monte_carlo`, then the full
    runtime-assert list (hard-fail). Returns the mc module."""
    apply_prereg_env_pins()
    import monte_carlo as mc  # noqa: E402 -- intentionally deferred import

    assert os.path.abspath(mc.__file__).lower().startswith(_ROOT.lower()), \
        f"monte_carlo imported from {mc.__file__!r}, expected under {_ROOT!r} (T9 worktree/PYTHONPATH ghost guard)"
    assert mc.CALENDAR_HOLD is True, f"mc.CALENDAR_HOLD={mc.CALENDAR_HOLD!r}, expected True"
    assert mc.HOLD_CAL_DAYS == 27, f"mc.HOLD_CAL_DAYS={mc.HOLD_CAL_DAYS!r}, expected 27"
    assert mc.NOMINAL_CAL_DTE == 30, f"mc.NOMINAL_CAL_DTE={mc.NOMINAL_CAL_DTE!r}, expected 30"
    assert not mc.NEXT_OPEN_ANCHOR, f"mc.NEXT_OPEN_ANCHOR={mc.NEXT_OPEN_ANCHOR!r}, expected falsy"
    assert not mc.TSL_ENABLED, f"mc.TSL_ENABLED={mc.TSL_ENABLED!r}, expected falsy"
    assert mc.PREM_STOP_LOSS >= 0, f"mc.PREM_STOP_LOSS={mc.PREM_STOP_LOSS!r}, expected >=0 (disabled)"
    return mc


def set_arm(mc, arm_name):
    """Applies one arm's TP/SL via mc_patch.set_tpsl (flat stress: tp_stress/
    sl_stress default to tp/sl) and re-checks the post-set_tpsl PREREG Sec2
    assert (flat stress -> TP_SIGMA_BASE==TP_SIGMA_STRESS)."""
    cell = ARMS[arm_name]
    mc_patch.set_tpsl(mc, cell["tp"], cell["sl"])
    assert mc.TP_SIGMA_BASE == mc.TP_SIGMA_STRESS, "flat-stress assert failed post set_tpsl (TP)"
    assert mc.SL_SIGMA_BASE == mc.SL_SIGMA_STRESS, "flat-stress assert failed post set_tpsl (SL)"


# ===========================================================================
# P1 -- declarations (PREREG Sec2)
# ===========================================================================
def load_ledger_kept(path=LEDGER_PARQUET) -> pl.DataFrame:
    df = pl.read_parquet(path)
    return df.filter(pl.col("status") == "kept").sort(["entry_date", "symbol"])


def ledger_row_dicts(kept_df: pl.DataFrame) -> list:
    """COLMAP-renamed ledger rows, one dict per kept signal."""
    cols = ["signal_id", "symbol", "entry_date", "entry_premium", "entry_spot_unadj",
            "dte_actual", "expiry", "walk_end", "path_end_reason", "liquid_entry", "ticker"]
    out = []
    for r in kept_df.select(cols).to_dicts():
        out.append({
            "signal_id": r["signal_id"], "symbol": r["symbol"],
            "signal_date": r["entry_date"],
            "entry_premium_real": r["entry_premium"],
            "spot_unadj": r["entry_spot_unadj"],
            "dte_actual": r["dte_actual"], "expiry": r["expiry"],
            "walk_end": r["walk_end"], "path_end_reason": r["path_end_reason"],
            "liquid_entry": r["liquid_entry"], "contract_ticker": r["ticker"],
        })
    return out


def load_ohlc_map(path, symbols=None) -> dict:
    """Returns dict[symbol] -> sorted (date,close,high,low,open) tuple list.
    Requires 'open' present -- caller must pass the Sec1 gate first."""
    df = pl.read_parquet(path)
    if symbols is not None:
        df = df.filter(pl.col("symbol").is_in(list(symbols)))
    df = df.sort(["symbol", "date"])
    out = {}
    for sym, g in df.group_by("symbol", maintain_order=True):
        sym = sym[0] if isinstance(sym, tuple) else sym
        out[sym] = build_sym_bars(g["date"].to_list(), g["close"].to_list(),
                                   g["high"].to_list(), g["low"].to_list(), g["open"].to_list())
    return out


def declare_arm(mc, ledger_rows: list, ohlc_map: dict, arm_name: str) -> list:
    """PREREG Sec2. `ledger_rows`: COLMAP-renamed dicts (ledger_row_dicts()).
    `ohlc_map`: dict[symbol] -> sym_bars. Returns one declaration dict per
    input row -- None-returns get kind=None + none_reason (T10, never
    dropped)."""
    set_arm(mc, arm_name)
    out = []
    for row in ledger_rows:
        symbol, signal_date = row["symbol"], row["signal_date"]
        rec = {"arm": arm_name, "symbol": symbol, "signal_date": signal_date,
               "signal_id": row["signal_id"]}
        bars = ohlc_map.get(symbol)
        if not bars:
            rec.update(kind=None, none_reason="symbol_missing_from_ohlc", T=None)
            out.append(rec)
            continue
        result = mc.compute_trade_outcome(bars, signal_date, stressed=False, trail=False, symbol=symbol)
        if result is None:
            dates = [b[0] for b in bars]
            closes = [b[1] for b in bars]
            reason = _classify_none_reason(dates, closes, signal_date, mc.HOLD_CAL_DAYS, mc.realized_vol)
            rec.update(kind=None, none_reason=reason, T=None)
            out.append(rec)
            continue
        dates = [b[0] for b in bars]
        base_idx = dates.index(signal_date)
        T = dates[base_idx + result["exit_bar"]]
        rec.update(
            kind=result["kind"], none_reason=None, exit_bar=result["exit_bar"], T=T,
            vol=result["vol"], premium_pct=result["premium_pct"], entry=result["entry"],
            fire_tp_level=result["fire_tp_level"], fire_sl_level=result["fire_sl_level"],
            fire_open=result["fire_open"], fire_close=result["fire_close"],
            fire_high=result["fire_high"], fire_low=result["fire_low"],
            cal_held=result["cal_held"],
        )
        out.append(rec)
    return out


# ===========================================================================
# P2 -- join + metrics (PREREG Sec3-Sec4)
# ===========================================================================
def load_paths_index(paths_glob=PATHS_GLOB, signal_ids=None):
    """Returns (by_date, sorted_list, last_date):
      by_date[signal_id][date]     -> {'close','high','low'}
      sorted_list[signal_id]       -> [(date,high), ...] ascending
      last_date[signal_id]         -> max date or None
    `signal_ids`, if given, restricts the read (smoke-scale convenience)."""
    files = sorted(glob.glob(paths_glob))
    if not files:
        return {}, {}, {}
    df = pl.concat([pl.read_parquet(f) for f in files])
    if signal_ids is not None:
        df = df.filter(pl.col("signal_id").is_in(list(signal_ids)))
    df = df.sort(["signal_id", "date"])
    by_date, sorted_list = {}, {}
    for sid, g in df.group_by("signal_id", maintain_order=True):
        sid = sid[0] if isinstance(sid, tuple) else sid
        dd, lst = {}, []
        for d, c, h, l in zip(g["date"].to_list(), g["close"].to_list(),
                               g["high"].to_list(), g["low"].to_list()):
            dd[d] = {"close": c, "high": h, "low": l}
            lst.append((d, h))
        by_date[sid] = dd
        sorted_list[sid] = lst
    last_date = {sid: (lst[-1][0] if lst else None) for sid, lst in sorted_list.items()}
    return by_date, sorted_list, last_date


def load_liquidity_map(path=LIQUIDITY_PARQUET) -> dict:
    """Returns dict[(symbol,date)] -> opt_vol_30d_atm."""
    if not Path(path).exists():
        return {}
    df = pl.read_parquet(path)
    return {(r["symbol"], r["date"]): r["opt_vol_30d_atm"] for r in
            df.select(["symbol", "date", "opt_vol_30d_atm"]).to_dicts()}


def load_tier_edges(path=BINDINGS_JSON):
    """PREREG Sec1: FF-4 tier edges, read LIVE from bindings.json (not
    hardcoded). Returns (edges: list[4 floats], source: str)."""
    with open(path, encoding="utf-8") as f:
        b = json.load(f)
    edges = b["6_gap_through_sl"]["liquidity_tier_edges"]
    return list(edges), str(path)


def join_and_classify(declarations: list, ledger_by_sid: dict, paths_by_date: dict,
                        paths_sorted: dict, last_path_date: dict, liquidity_map: dict,
                        tier_edges, hold_cal_days=27, contract_open_index=None):
    """PREREG Sec3 (+ Sec9 AMENDMENT A1 for contract open + the integrity
    cross-check). Returns (events: list[dict], both_rows: list[dict],
    integrity: dict). `events` = one row per declared kind=='tp' input
    (joinable or not -- UNJOINABLE rows ARE included, per Sec3 'fully
    counted'). kind=='sl'/'hard'/'prem'/None are NOT represented here
    (census-only, see split_primary_and_both / dose accounting).
    `contract_open_index`: a ContractOpenIndex (or None to skip A1 entirely
    -- gap_open/open_mult stay None, no integrity check runs; used by
    --selftest cases that don't need it)."""
    events, both_rows = [], []
    integrity = {"n_compared": 0, "n_mismatch": 0, "mismatches": []}
    for d in declarations:
        kind = d.get("kind")
        if kind not in ("tp", "both"):
            continue
        lrow = ledger_by_sid.get(d["signal_id"])
        if lrow is None:
            continue  # structurally unreachable (declarations are built FROM kept ledger rows)
        dte_actual = lrow["dte_actual"]
        dte_bucket = "matched" if (dte_actual is not None and DTE_TIGHT[0] <= dte_actual <= DTE_TIGHT[1]) else "other"
        entry_year = d["signal_date"].year
        liq_val = liquidity_map.get((d["symbol"], d["signal_date"]))
        tier = liquidity_tier_for(liq_val, tier_edges) if liq_val is not None else None
        entry_premium_real = lrow["entry_premium_real"]
        arm_tp = ARMS[d["arm"]]["tp"]
        B_mult = 1.0 + arm_tp
        B = (B_mult * entry_premium_real) if (entry_premium_real and entry_premium_real > 0) else None

        base = dict(arm=d["arm"], symbol=d["symbol"], signal_date=d["signal_date"],
                    signal_id=d["signal_id"], kind=kind, T=d["T"],
                    contract_ticker=lrow["contract_ticker"],
                    dte_actual=dte_actual, dte_bucket=dte_bucket, entry_year=entry_year,
                    tier=tier, entry_premium_real=entry_premium_real, B_mult=B_mult, B=B,
                    vol=d.get("vol"), fire_tp_level=d.get("fire_tp_level"))

        if kind == "both":
            both_rows.append(base)
            continue

        if B is None:
            base.update(bucket="unjoinable", unjoinable_reason="entry_premium_real not positive",
                        high_mult=None, close_mult=None, close_basis_fill=None,
                        shortfall=None, overshoot=None, timing_bucket=None, days_late=None,
                        gap_open=None, open_mult=None)
            events.append(base)
            continue

        by_date = paths_by_date.get(d["signal_id"], {})
        sorted_list = paths_sorted.get(d["signal_id"], [])
        last_dt = last_path_date.get(d["signal_id"])
        bucket, reason = classify_join(d["T"], B, lrow["expiry"], lrow["walk_end"],
                                        lrow["path_end_reason"], by_date, last_dt)
        base["bucket"] = bucket
        base["unjoinable_reason"] = reason

        high_mult = close_mult = close_basis_fill = shortfall = overshoot = None
        timing_bucket = days_late = None
        gap_open = open_mult = None  # A1: populated below, fills only, when contract_open_index is supplied
        if bucket != "unjoinable":
            row = by_date.get(d["T"])
            if row is not None:
                high_mult = (row["high"] / entry_premium_real) if row["high"] is not None else None
                close_mult = (row["close"] / entry_premium_real) if row["close"] is not None else None
                close_basis_fill = (close_mult is not None) and (close_mult >= B_mult)

                if contract_open_index is not None:
                    idx_row = contract_open_index.lookup(d["symbol"], lrow["contract_ticker"], d["T"])
                    if idx_row is not None:
                        # A1 integrity cross-check: run on EVERY joinable row with
                        # both a paths row and an index row (not just fills) --
                        # maximizes N for the >1%-mismatch STOP decision, which is
                        # a data-integrity question independent of PREREG's
                        # "on fills" scoping for the gap-open OUTPUT below.
                        integrity["n_compared"] += 1
                        is_match, detail = integrity_cross_check(row, idx_row)
                        if not is_match:
                            integrity["n_mismatch"] += 1
                            if len(integrity["mismatches"]) < 20:
                                integrity["mismatches"].append({
                                    "signal_id": d["signal_id"], "arm": d["arm"],
                                    "ticker": lrow["contract_ticker"], "T": str(d["T"]), **detail})
                        if bucket == "fill":
                            gap_open, open_mult = gap_open_decomposition(idx_row["open"], B, entry_premium_real)

            if bucket == "fill" and high_mult is not None:
                overshoot = high_mult - B_mult
            if bucket == "miss_traded_below" and high_mult is not None:
                shortfall = B_mult - high_mult
            dl = deadline_for(d["signal_date"], hold_cal_days)
            timing_bucket, days_late = timing_walk(d["T"], dl, B, sorted_list)

        base.update(high_mult=high_mult, close_mult=close_mult, close_basis_fill=close_basis_fill,
                    shortfall=shortfall, overshoot=overshoot, timing_bucket=timing_bucket,
                    days_late=days_late, gap_open=gap_open, open_mult=open_mult)
        events.append(base)
    integrity["mismatch_share"] = (integrity["n_mismatch"] / integrity["n_compared"]
                                    if integrity["n_compared"] else None)
    return events, both_rows, integrity


_EVENTS_SCHEMA = {
    "arm": pl.String, "symbol": pl.String, "signal_date": pl.Date, "signal_id": pl.String,
    "kind": pl.String, "T": pl.Date, "contract_ticker": pl.String,
    "dte_actual": pl.Int64, "dte_bucket": pl.String, "entry_year": pl.Int64,
    "tier": pl.String, "entry_premium_real": pl.Float64, "B_mult": pl.Float64, "B": pl.Float64,
    "vol": pl.Float64, "fire_tp_level": pl.Float64,
    "bucket": pl.String, "unjoinable_reason": pl.String,
    "high_mult": pl.Float64, "close_mult": pl.Float64, "close_basis_fill": pl.Boolean,
    "shortfall": pl.Float64, "overshoot": pl.Float64,
    "timing_bucket": pl.String, "days_late": pl.Int64,
    "gap_open": pl.Boolean, "open_mult": pl.Float64,
}


def events_to_df(events: list) -> pl.DataFrame:
    if not events:
        return pl.DataFrame(schema=_EVENTS_SCHEMA)
    df = pl.DataFrame(events, infer_schema_length=None)
    for c, dt in _EVENTS_SCHEMA.items():
        if c not in df.columns:
            df = df.with_columns(pl.lit(None).alias(c))
    float_cols = [c for c, dt in _EVENTS_SCHEMA.items() if dt == pl.Float64]
    df = df.with_columns([pl.col(c).cast(pl.Float64, strict=False).fill_nan(None) for c in float_cols])  # T3
    return df.select(list(_EVENTS_SCHEMA)).with_columns(
        [pl.col(c).cast(dt, strict=False) for c, dt in _EVENTS_SCHEMA.items()])


_DECL_SCHEMA = {
    "arm": pl.String, "symbol": pl.String, "signal_date": pl.Date, "signal_id": pl.String,
    "kind": pl.String, "none_reason": pl.String, "exit_bar": pl.Int64, "T": pl.Date,
    "vol": pl.Float64, "premium_pct": pl.Float64, "entry": pl.Float64,
    "fire_tp_level": pl.Float64, "fire_sl_level": pl.Float64,
    "fire_open": pl.Float64, "fire_close": pl.Float64, "fire_high": pl.Float64, "fire_low": pl.Float64,
    "cal_held": pl.Int64,
}


def declarations_to_df(decls: list) -> pl.DataFrame:
    if not decls:
        return pl.DataFrame(schema=_DECL_SCHEMA)
    df = pl.DataFrame(decls, infer_schema_length=None)
    for c in _DECL_SCHEMA:
        if c not in df.columns:
            df = df.with_columns(pl.lit(None).alias(c))
    float_cols = [c for c, dt in _DECL_SCHEMA.items() if dt == pl.Float64]
    df = df.with_columns([pl.col(c).cast(pl.Float64, strict=False).fill_nan(None) for c in float_cols])
    return df.select(list(_DECL_SCHEMA)).with_columns(
        [pl.col(c).cast(dt, strict=False) for c, dt in _DECL_SCHEMA.items()])


# ===========================================================================
# Cell metrics (PREREG Sec4) + slices
# ===========================================================================
def compute_cell_metrics(df: pl.DataFrame, both_df: pl.DataFrame) -> dict:
    n_declared = df.height
    joinable = df.filter(pl.col("bucket") != "unjoinable")
    n_joinable = joinable.height
    coverage_pct = (100.0 * n_joinable / n_declared) if n_declared else None

    fills = joinable.filter(pl.col("bucket") == "fill")
    trbelow = joinable.filter(pl.col("bucket") == "miss_traded_below")
    noprint = joinable.filter(pl.col("bucket") == "miss_no_print")
    n_fill, n_trbelow, n_noprint = fills.height, trbelow.height, noprint.height
    n_miss = n_trbelow + n_noprint

    fill_rate = (n_fill / n_joinable) if n_joinable else None
    fill_ci = wilson_ci(n_fill, n_joinable) if n_joinable else (None, None)
    miss_rate = (n_miss / n_joinable) if n_joinable else None
    no_print_share = (n_noprint / n_joinable) if n_joinable else None

    close_eval = joinable.filter(pl.col("close_basis_fill").is_not_null())
    n_close_eval = close_eval.height
    close_fill_n = close_eval.filter(pl.col("close_basis_fill")).height
    close_fill_rate = (close_fill_n / n_close_eval) if n_close_eval else None

    shortfall_q = safe_quantiles(trbelow["shortfall"].drop_nulls().to_list())
    overshoot_q = safe_quantiles(fills["overshoot"].drop_nulls().to_list())

    timing = joinable.filter(pl.col("timing_bucket").is_not_null())
    n_timing = timing.height
    never_n = timing.filter(pl.col("timing_bucket") == "never_fill").height
    never_fill_rate = (never_n / n_timing) if n_timing else None
    same_day_n = timing.filter(pl.col("timing_bucket") == "same_day").height
    late = timing.filter(pl.col("timing_bucket") == "late")
    days_late_q = safe_quantiles(late["days_late"].drop_nulls().to_list())

    both_n = both_df.height if both_df is not None else 0
    both_rate = (both_n / (n_declared + both_n)) if (n_declared + both_n) else None

    # PREREG Sec9 A1: gap-open share gamma + (open_mult - B_mult) quantiles on
    # gap-open fills. "Available" is determined per-cell from the data itself
    # (n_gap_eval>0), not a global flag -- FF-0 index coverage can vary by
    # (ticker,date), so a cell can legitimately have zero evaluable rows even
    # when contract_open_index was supplied.
    gap_eval = fills.filter(pl.col("gap_open").is_not_null())
    n_gap_eval = gap_eval.height
    gap_open_n = gap_eval.filter(pl.col("gap_open")).height
    gap_open_share = (gap_open_n / n_gap_eval) if n_gap_eval else None
    gap_open_fills = gap_eval.filter(pl.col("gap_open"))
    open_minus_b_vals = []
    if gap_open_fills.height:
        oms = gap_open_fills["open_mult"].to_list()
        bms = gap_open_fills["B_mult"].to_list()
        open_minus_b_vals = [o - b for o, b in zip(oms, bms) if o is not None and b is not None]
    open_minus_b_q = safe_quantiles(open_minus_b_vals)

    return {
        "n_declared": n_declared, "n_joinable": n_joinable, "coverage_pct": coverage_pct,
        "n_fill": n_fill, "fill_rate": fill_rate, "fill_ci_lo": fill_ci[0], "fill_ci_hi": fill_ci[1],
        "n_miss": n_miss, "miss_rate": miss_rate,
        "n_traded_below": n_trbelow, "n_no_print": n_noprint, "no_print_share": no_print_share,
        "n_close_eval": n_close_eval, "close_fill_rate": close_fill_rate,
        "both_n": both_n, "both_rate": both_rate,
        "shortfall": shortfall_q, "overshoot": overshoot_q,
        "n_timing": n_timing, "same_day_n": same_day_n,
        "never_fill_n": never_n, "never_fill_rate": never_fill_rate,
        "days_late": days_late_q,
        "gap_open_share": gap_open_share, "n_gap_eval": n_gap_eval, "n_gap_open": gap_open_n,
        "open_minus_b": open_minus_b_q,
        "gap_open_note": None if n_gap_eval else "no contract-open data evaluable for this cell "
                          "(A1 index coverage gap, or contract_open_index not supplied)",
    }


def iter_slices(events_df: pl.DataFrame, both_df: pl.DataFrame):
    """Yields ((dte_label, slice_type, slice_value), sub_events, sub_both)
    for the full PREREG Sec4 cell set: {all_kept, matched_25_38} x
    {pooled, tier t1..t5, year 2022..2026}."""
    for dte_label, pred in (("all_kept", None), ("matched_25_38", pl.col("dte_bucket") == "matched")):
        base = events_df if pred is None else events_df.filter(pred)
        bboth = both_df if pred is None else both_df.filter(pred)
        yield (dte_label, "pooled", "pooled"), base, bboth
        for t in ("t1", "t2", "t3", "t4", "t5"):
            yield (dte_label, "tier", t), base.filter(pl.col("tier") == t), bboth.filter(pl.col("tier") == t)
        for y in YEARS:
            yield (dte_label, "year", str(y)), base.filter(pl.col("entry_year") == y), bboth.filter(pl.col("entry_year") == y)


def build_cells(events_by_arm: dict, both_by_arm: dict) -> list:
    cells = []
    for arm in ARM_ORDER:
        edf = events_by_arm.get(arm, events_to_df([]))
        bdf = both_by_arm.get(arm, events_to_df([]))
        for (dte_label, slice_type, slice_value), sub_e, sub_b in iter_slices(edf, bdf):
            m = compute_cell_metrics(sub_e, sub_b)
            m.update(arm=arm, dte_filter=dte_label, slice_type=slice_type, slice_value=slice_value)
            cells.append(m)
    return cells


# ===========================================================================
# Tripwires (PREREG Sec6)
# ===========================================================================
def compute_tripwires(events_by_arm: dict, decl_by_arm: dict, integrity_by_arm: dict = None) -> list:
    lines = []
    # Sec9 A1: contract_day_index vs paths high/close integrity cross-check,
    # >1% mismatch share = STOP (data-integrity gate, checked ahead of the
    # numbered Sec6 tripwires since a join/extraction bug here would corrupt
    # everything below it).
    if integrity_by_arm:
        total_compared = sum(i["n_compared"] for i in integrity_by_arm.values())
        total_mismatch = sum(i["n_mismatch"] for i in integrity_by_arm.values())
        share_pct = (100.0 * total_mismatch / total_compared) if total_compared else None
        if total_compared == 0:
            lines.append("A1 integrity cross-check [pooled, both arms]: N=0 -- UNEVALUATED")
        else:
            status = "STOP (>1% mismatch -- investigate before results are read, PREREG Sec9 A1)" \
                if share_pct > INTEGRITY_MISMATCH_STOP_PCT else "PASS"
            lines.append(f"A1 integrity cross-check [pooled, both arms]: compared={total_compared} "
                          f"mismatch={total_mismatch} share={share_pct:.2f}% "
                          f"(threshold >{INTEGRITY_MISMATCH_STOP_PCT}%) -- {status}")
    # 6.1 coverage >=70% of declared 'tp' events, per arm (pooled, all-kept)
    for arm in ARM_ORDER:
        edf = events_by_arm.get(arm, events_to_df([]))
        n_decl = edf.height
        n_join = edf.filter(pl.col("bucket") != "unjoinable").height
        cov = (100.0 * n_join / n_decl) if n_decl else None
        status = "UNEVALUATED (N=0)" if n_decl == 0 else ("PASS" if cov >= 70.0 else "FAIL")
        lines.append(f"6.1 coverage [{arm}]: joinable={n_join}/{n_decl} "
                      f"({'%.1f%%' % cov if cov is not None else 'NA'}) -- {status}")

    # 6.2 Link-1 band sanity: ARM-30 matched-filter+liquid real high-mult
    # median on fill days within [1.20,1.50]. "liquid" = ledger's liquid_entry
    # (PREREG doesn't redefine it here; reuses FF-1's own flag) -- not yet
    # threaded into events_df in this implementation (liquid_entry is a
    # ledger-row property, joinable via signal_id if needed); reported over
    # ALL matched-filter fills as a documented approximation when the liquid-
    # only cut isn't available, flagged explicitly.
    edf30 = events_by_arm.get("arm30", events_to_df([]))
    matched_fill30 = edf30.filter((pl.col("dte_bucket") == "matched") & (pl.col("bucket") == "fill"))
    n2 = matched_fill30.height
    if n2 >= 30:
        med = float(matched_fill30["high_mult"].median())
        inside = 1.20 <= med <= 1.50
        lines.append(f"6.2 Link-1 band sanity [arm30, matched-filter, ALL liquidity (liquid-only cut "
                      f"not applied -- see note)]: N={n2} median high_mult={med:.3f} band=[1.20,1.50] -- "
                      f"{'PASS' if inside else 'FAIL'}")
    else:
        lines.append(f"6.2 Link-1 band sanity [arm30]: N={n2} < 30 -- UNEVALUATED")

    # 6.3 arm divergence: ARM-15 declares strictly more 'tp' events than ARM-30
    d30 = decl_by_arm.get("arm30", [])
    d15 = decl_by_arm.get("arm15", [])
    n_tp30 = sum(1 for d in d30 if d.get("kind") == "tp")
    n_tp15 = sum(1 for d in d15 if d.get("kind") == "tp")
    status = "PASS" if n_tp15 > n_tp30 else "FAIL (set_tpsl silent no-op guard -- T8/LESSONS.md)"
    lines.append(f"6.3 arm divergence: tp(ARM-15)={n_tp15} vs tp(ARM-30)={n_tp30} -- {status}")

    # 6.4 smoke worked example -- printed separately in smoke_report.md (not a PASS/FAIL line)
    lines.append("6.4 smoke worked example: see smoke_report.md (hand-audited by orchestrator, not auto-graded)")

    # 6.5 N floor: matched-filter pooled events >= 500 per arm expected
    for arm in ARM_ORDER:
        edf = events_by_arm.get(arm, events_to_df([]))
        n = edf.filter(pl.col("dte_bucket") == "matched").height
        status = "OK" if n >= N_FLOOR_MATCHED_POOLED else f"UNDERPOWERED (<{N_FLOOR_MATCHED_POOLED} -- pooled-only, no per-tier calibration claimed)"
        lines.append(f"6.5 N floor [{arm}, matched-filter pooled]: N={n} (expected >={N_FLOOR_MATCHED_POOLED}) -- {status}")
    return lines


# ===========================================================================
# Table / doc builders
# ===========================================================================
def _fmt(v, nd=3):
    if v is None:
        return "NA"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def write_bindings_echo(tier_edges, tier_edges_source, out_path=OUT_DIR / "bindings_echo.json"):
    obj = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tier_edges": tier_edges, "tier_edges_source": tier_edges_source,
        "tier_labels": ["t1", "t2", "t3", "t4", "t5"],
        "tier_convention": "t1=least liquid .. t5=most (PREREG Sec1)",
        "env_pins": {"MC_NO_DB_PERSIST": "1", "LIQUIDITY_FLOOR": "0.0", "PYTHONIOENCODING": "utf-8"},
        "runtime_asserts": [
            "mc.__file__ under repo root", "mc.CALENDAR_HOLD is True", "mc.HOLD_CAL_DAYS == 27",
            "mc.NOMINAL_CAL_DTE == 30", "mc.NEXT_OPEN_ANCHOR falsy", "mc.TSL_ENABLED falsy",
            "mc.PREM_STOP_LOSS >= 0", "post-set_tpsl: TP_SIGMA_BASE == TP_SIGMA_STRESS",
        ],
        "arms": ARMS, "hold_cal_days": 27, "dte_tight_band": list(DTE_TIGHT),
        "quantile_min_n": QUANTILE_MIN_N, "n_floor_matched_pooled": N_FLOOR_MATCHED_POOLED,
        "known_schema_gaps": KNOWN_SCHEMA_GAPS,
        "days_late_units": "calendar_days",
        "days_late_units_note": "(d - T).days on real datetime.date objects (T and every path-row "
            "date are calendar dates, never a trading-bar index) -- matches PREREG's calendar-day "
            "deadline arithmetic (deadline = signal_date + HOLD_CAL_DAYS calendar days); a weekend/"
            "holiday gap between two traded days counts in full (e.g. Fri->Mon = 3, not 1 trading day).",
    }
    atomic_write_json(obj, out_path)
    return obj


def write_dose_accounting(census_by_arm: dict, cells: list, out_path=OUT_DIR / "dose_accounting.md",
                            integrity_by_arm: dict = None):
    lines = ["# Dose accounting -- TP-fill fidelity 30-DTE", ""]
    for arm in ARM_ORDER:
        census = census_by_arm.get(arm, {})
        n_in = sum(census.values())
        lines.append(f"## {ARMS[arm]['label']}")
        lines.append(f"- Step 1: kept ledger signals fed to P1: N={n_in}")
        lines.append("- Step 2: FULL declaration kind census (tp/sl/hard/both/prem/None-reasons, PREREG Sec9 A1 "
                      "clarification -- incl. engine None-returns T10, fully counted, never dropped):")
        for k, v in sorted(census.items()):
            lines.append(f"    - {k}: {v}")
        n_tp = census.get("tp", 0)
        n_both = census.get("both", 0)
        lines.append(f"- Step 3: primary events (kind=='tp'): N={n_tp}")
        lines.append(f"- Step 4: 'both'-kind (tabulated separately, excluded from primary rates): N={n_both}")
        pooled = [c for c in cells if c["arm"] == arm and c["dte_filter"] == "all_kept" and c["slice_type"] == "pooled"]
        if pooled:
            m = pooled[0]
            lines.append(f"- Step 5: join outcome on primary events (all-kept, pooled): "
                          f"joinable={m['n_joinable']}/{m['n_declared']} ({_fmt(m['coverage_pct'],1)}%), "
                          f"fill={m['n_fill']}, traded_below={m['n_traded_below']}, no_print={m['n_no_print']}")
        integ = (integrity_by_arm or {}).get(arm)
        if integ:
            share_pct = None if integ["mismatch_share"] is None else integ["mismatch_share"] * 100.0
            status = ("UNEVALUATED (N=0)" if integ["n_compared"] == 0
                       else ("STOP" if share_pct > INTEGRITY_MISMATCH_STOP_PCT else "OK"))
            lines.append(f"- Step 6 (PREREG Sec9 A1 integrity cross-check, contract_day_index vs paths "
                          f"high/close on joinable rows): compared={integ['n_compared']} "
                          f"mismatch={integ['n_mismatch']} share={_fmt(share_pct,2)}% "
                          f"(STOP threshold >{INTEGRITY_MISMATCH_STOP_PCT}%) -- {status}")
            if integ["mismatches"]:
                lines.append(f"    - sample mismatches (cap 20, {len(integ['mismatches'])} shown):")
                for mm in integ["mismatches"]:
                    lines.append(f"      - {mm['signal_id']} ({mm['ticker']} @ {mm['T']}): "
                                  f"paths high/close={mm['paths_high']}/{mm['paths_close']} vs "
                                  f"index high/close={mm['index_high']}/{mm['index_close']}")
        lines.append("")
    atomic_write_text("\n".join(lines), out_path)


def write_optimism_table(cells: list, md_path=OUT_DIR / "tp_fill_optimism_by_tier.md",
                           csv_path=OUT_DIR / "tp_fill_optimism_by_tier.csv", tripwires=None):
    md_path = Path(md_path)
    csv_path = Path(csv_path)
    cols = ["arm", "dte_filter", "slice_type", "slice_value", "n_declared", "n_joinable", "coverage_pct",
            "n_fill", "fill_rate", "fill_ci_lo", "fill_ci_hi", "n_miss", "miss_rate", "n_no_print",
            "no_print_share", "n_close_eval", "close_fill_rate", "both_n", "both_rate",
            "shortfall_p25", "shortfall_p50", "shortfall_p75", "shortfall_p90", "shortfall_n",
            "overshoot_p25", "overshoot_p50", "overshoot_p75", "overshoot_p90", "overshoot_n",
            "n_timing", "same_day_n", "never_fill_n", "never_fill_rate",
            "days_late_p25", "days_late_p50", "days_late_p75", "days_late_p90", "days_late_n",
            "gap_open_share", "n_gap_eval", "n_gap_open",
            "open_minus_b_p25", "open_minus_b_p50", "open_minus_b_p75", "open_minus_b_p90", "open_minus_b_n"]
    csv_rows = []
    for c in cells:
        row = {k: c.get(k) for k in ["arm", "dte_filter", "slice_type", "slice_value", "n_declared",
                                       "n_joinable", "coverage_pct", "n_fill", "fill_rate", "fill_ci_lo",
                                       "fill_ci_hi", "n_miss", "miss_rate", "n_no_print", "no_print_share",
                                       "n_close_eval", "close_fill_rate", "both_n", "both_rate"]}
        sq, oq, dq, ombq = c["shortfall"], c["overshoot"], c["days_late"], c["open_minus_b"]
        for prefix, q in (("shortfall", sq), ("overshoot", oq), ("days_late", dq), ("open_minus_b", ombq)):
            for k in ("p25", "p50", "p75", "p90"):
                row[f"{prefix}_{k}"] = q.get(k)
            row[f"{prefix}_n"] = q.get("n")
        row["n_timing"] = c["n_timing"]; row["same_day_n"] = c["same_day_n"]
        row["never_fill_n"] = c["never_fill_n"]; row["never_fill_rate"] = c["never_fill_rate"]
        row["gap_open_share"] = c["gap_open_share"]; row["n_gap_eval"] = c["n_gap_eval"]
        row["n_gap_open"] = c["n_gap_open"]
        csv_rows.append(row)

    csv_df = pl.DataFrame(csv_rows, infer_schema_length=None) if csv_rows else pl.DataFrame(schema={c: pl.Utf8 for c in cols})
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = csv_path.with_name(csv_path.name + ".tmp")
    csv_df.write_csv(tmp)
    os.replace(tmp, csv_path)

    lines = []
    if tripwires:
        lines.append("# TP-fill optimism by tier -- 30-DTE (PREREG-locked measurement)")
        lines.append("")
        lines.append("## Tripwires (PREREG Sec6) -- checked BEFORE results are read as findings")
        lines.extend(f"- {t}" for t in tripwires)
        lines.append("")
    lines.append("## Headline: matched-filter (DTE 25-38) x {pooled, tier t1..t5} x arm (PREREG Sec4 'HEADLINE table')")
    lines.append("")
    lines.append("| arm | slice | N decl | N join | cov% | fill% | fill 95%CI | miss% | no-print% | "
                  "close-fill% | both N | shortfall p50 | overshoot p50 | never-fill% | gap-open gamma "
                  "(N eval) | open-B p50 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in cells:
        if c["dte_filter"] != "matched_25_38" or c["slice_type"] not in ("pooled", "tier"):
            continue
        ci = f"[{_fmt(c['fill_ci_lo'])},{_fmt(c['fill_ci_hi'])}]" if c["n_joinable"] else "NA"
        gamma_str = f"{_fmt(c['gap_open_share'],3)} (N={c['n_gap_eval']})"
        lines.append(f"| {c['arm']} | {c['slice_value']} | {c['n_declared']} | {c['n_joinable']} | "
                      f"{_fmt(c['coverage_pct'],1)} | {_fmt(c['fill_rate'],3)} | {ci} | "
                      f"{_fmt(c['miss_rate'],3)} | {_fmt(c['no_print_share'],3)} | "
                      f"{_fmt(c['close_fill_rate'],3)} | {c['both_n']} | "
                      f"{_fmt(c['shortfall']['p50'])} | {_fmt(c['overshoot']['p50'])} | "
                      f"{_fmt(c['never_fill_rate'],3)} | {gamma_str} | {_fmt(c['open_minus_b']['p50'])} |")
    lines.append("")
    lines.append("## Secondary: all-kept (no DTE filter) x {pooled, tier t1..t5} x arm")
    lines.append("")
    lines.append("| arm | slice | N decl | N join | cov% | fill% | miss% | no-print% | close-fill% | both N |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for c in cells:
        if c["dte_filter"] != "all_kept" or c["slice_type"] not in ("pooled", "tier"):
            continue
        lines.append(f"| {c['arm']} | {c['slice_value']} | {c['n_declared']} | {c['n_joinable']} | "
                      f"{_fmt(c['coverage_pct'],1)} | {_fmt(c['fill_rate'],3)} | {_fmt(c['miss_rate'],3)} | "
                      f"{_fmt(c['no_print_share'],3)} | {_fmt(c['close_fill_rate'],3)} | {c['both_n']} |")
    lines.append("")
    lines.append("## Year breakdown (matched-filter x year x arm)")
    lines.append("")
    lines.append("| arm | year | N decl | N join | cov% | fill% | miss% | no-print% |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for c in cells:
        if c["dte_filter"] != "matched_25_38" or c["slice_type"] != "year":
            continue
        lines.append(f"| {c['arm']} | {c['slice_value']} | {c['n_declared']} | {c['n_joinable']} | "
                      f"{_fmt(c['coverage_pct'],1)} | {_fmt(c['fill_rate'],3)} | {_fmt(c['miss_rate'],3)} | "
                      f"{_fmt(c['no_print_share'],3)} |")
    lines.append("")
    lines.append("Footer notes (verbatim, PREREG-required):")
    lines.append("- day-agg highs may include non-RTH prints; FF-4 minute overlap cross-check available as follow-up. (T6) "
                  "This covers contract_day_index's opens identically (same day_aggs provenance, PREREG Sec9 A1).")
    lines.append("- 2022 and 2026 year slices are PARTIAL archive coverage (2022-08 start, 2026-07/08 end) -- not full calendar years.")
    lines.append("- gap-open decomposition (open_mult, gamma) sourced from contract_day_index per PREREG Sec9 AMENDMENT A1 "
                  "(joined on ticker+T; integrity cross-check vs paths high/close in dose_accounting.md). "
                  "'N gap eval' = joinable fills with a resolvable index row; cells below that show gamma=NA had zero index coverage.")
    lines.append("- days_late UNITS: CALENDAR days, not trading days -- (d-T).days on real calendar dates, matching "
                  "PREREG's own calendar-day deadline arithmetic (echoed machine-readably in bindings_echo.json's "
                  "days_late_units). A weekend/holiday gap between two traded days counts in full (Fri->Mon = 3, not 1).")
    lines.append("- quantiles suppressed (N-only) below N=30 per PREREG Sec4.")
    atomic_write_text("\n".join(lines), md_path)


def write_knob_calibration_draft(cells: list, out_path=OUT_DIR / "knob_calibration_draft.md"):
    """PREREG Sec5 numbers only -- NO verdicts (mechanical rounding to 0.05,
    exact value alongside)."""
    lines = ["# Knob calibration draft -- numbers only, no verdicts (PREREG Sec5)", ""]
    for arm in ARM_ORDER:
        pooled = [c for c in cells if c["arm"] == arm and c["dte_filter"] == "matched_25_38"
                  and c["slice_type"] == "pooled"]
        tiers = [c for c in cells if c["arm"] == arm and c["dte_filter"] == "matched_25_38"
                 and c["slice_type"] == "tier"]
        lines.append(f"## {ARMS[arm]['label']}")
        if pooled:
            m = pooled[0]
            econ = m["never_fill_rate"]
            bound = m["miss_rate"]
            econ_r = None if econ is None else round(econ / 0.05) * 0.05
            bound_r = None if bound is None else round(bound / 0.05) * 0.05
            lines.append(f"- TP_FILL_MISS_P (economic calibration = pooled NEVER-FILL rate): "
                          f"exact={_fmt(econ,4)} rounded={_fmt(econ_r,2)} (N_timing={m['n_timing']})")
            lines.append(f"- TP_FILL_MISS_P (pessimism bound = pooled same-day miss rate): "
                          f"exact={_fmt(bound,4)} rounded={_fmt(bound_r,2)} (N_joinable={m['n_joinable']})")
            lines.append(f"  - graded against tpsl_refine's locked probe 0.10: "
                          f"{'bound > 0.10 -> FLAG probe understates' if (bound is not None and bound > 0.10) else 'bound <= 0.10 or UNEVALUATED'}")
            default_credit = None
            if m["overshoot"]["p50"] is not None:
                default_credit = m["overshoot"]["p50"] / 2.0
            lines.append(f"- Overshoot-credit optimism, default-arm term E[(high_mult-B_mult)/2 | fill]: "
                          f"{_fmt(default_credit,4)} (median-based proxy for mean; N_fill={m['n_fill']})")
            gamma = m["gap_open_share"]
            gamma_r = None if gamma is None else round(gamma / 0.05) * 0.05
            lines.append(f"- Gap-open share gamma (PREREG Sec9 A1, contract_day_index): "
                          f"exact={_fmt(gamma,4)} rounded={_fmt(gamma_r,2)} "
                          f"(N_gap_eval={m['n_gap_eval']}, N_gap_open={m['n_gap_open']})"
                          + ("" if gamma is not None else f"  [{m['gap_open_note']}]"))
            real_credit = None
            if gamma is not None and m["open_minus_b"]["p50"] is not None:
                real_credit = gamma * m["open_minus_b"]["p50"]
            lines.append(f"- Real limit-mechanics credit gamma x E[open_mult-B_mult | gap-open fill]: "
                          f"{_fmt(real_credit,4)} (median-based proxy for mean)")
            ratio = None
            if default_credit is not None and real_credit not in (None, 0):
                ratio = default_credit / real_credit
            lines.append(f"- Overshoot-credit optimism ratio (default/real): {_fmt(ratio,3)}")
            if gamma is None:
                rec = "CANNOT be mechanically determined -- no gap-open data evaluable in this cell"
            else:
                rec = "TP_FILL_GAP_AWARE" if gamma >= 0.03 else "TP_FILL_AT_BARRIER"
            lines.append(f"- Fill-price arm recommendation (PREREG Sec5 mechanical rule, "
                          f"gamma>=3%->GAP_AWARE else AT_BARRIER): {rec}")
        else:
            lines.append("- (no pooled cell -- 0 declared events in this slice)")
        lines.append("- Per-tier (matched-filter):")
        for c in tiers:
            lines.append(f"    - {c['slice_value']}: never_fill_rate={_fmt(c['never_fill_rate'],4)} "
                          f"(N_timing={c['n_timing']}), same_day_miss_rate={_fmt(c['miss_rate'],4)} "
                          f"(N_joinable={c['n_joinable']})")
        lines.append("")
    atomic_write_text("\n".join(lines), out_path)


# ===========================================================================
# Pipeline runner (shared by --smoke and --full)
# ===========================================================================
def run_pipeline(n=None, label="smoke"):
    """Returns a result dict; never raises on the expected gate-failure path
    (that's a normal, reportable outcome, not a bug)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ohlc_path, gate_ok, gate_reason, gate_cols = resolve_ohlc_path()
    gate_state = {"checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "path": str(ohlc_path),
                  "passed": gate_ok, "reason": gate_reason, "columns": gate_cols,
                  "candidates_tried": [str(p) for p in OHLC_CANDIDATES]}

    result = {"label": label, "n": n}

    if not gate_ok:
        atomic_write_json(gate_state, STATE_DIR / "ohlc_gate.json")
        result["gate"] = gate_state
        log(f"[{label}] OHLC GATE FAILED: {gate_reason}")
        log("STOPPING before P1 (PREREG Sec1 fallback required). See module docstring "
            "'SCHEMA GATE STATUS' and run --fetch-ohlc-fallback (queued only).")
        result["status"] = "BLOCKED_ON_OHLC_GATE"
        return result

    # PREREG Sec9 A1: the 5x3 MySQL value audit now runs (once, foreground,
    # cached) against the FALLBACK parquet specifically -- it guards the pull
    # CODE, not the (already-established-good) primary schema. Load the
    # previously-cached gate_state (if any) first so ensure_fallback_spot_
    # audit can reuse an unexpired verdict instead of re-touching MySQL.
    prior_gate_path = STATE_DIR / "ohlc_gate.json"
    prior_gate_state = {}
    if prior_gate_path.exists():
        try:
            with open(prior_gate_path, encoding="utf-8") as f:
                prior_gate_state = json.load(f)
        except Exception:  # noqa: BLE001 -- a corrupt/stale cache file just forces a fresh audit
            prior_gate_state = {}
    audit = ensure_fallback_spot_audit(ohlc_path, prior_gate_state)
    if audit is not None:
        gate_state["fallback_spot_audit"] = audit
        log(f"[{label}] fallback spot-audit: ok={audit.get('ok')} "
            f"n_checked={audit.get('n_checked')} n_mismatch={audit.get('n_mismatch')} "
            f"close_unadj_bug_suspected={audit.get('close_unadj_bug_suspected')}")
    atomic_write_json(gate_state, prior_gate_path)
    result["gate"] = gate_state

    ledger_kept = load_ledger_kept()
    if n is not None:
        ledger_kept = ledger_kept.head(n)
    ledger_rows = ledger_row_dicts(ledger_kept)
    ledger_by_sid = {r["signal_id"]: r for r in ledger_rows}
    symbols = sorted({r["symbol"] for r in ledger_rows})
    signal_ids = [r["signal_id"] for r in ledger_rows]

    log(f"[{label}] {len(ledger_rows)} kept ledger signals, {len(symbols)} symbols")
    ohlc_map = load_ohlc_map(ohlc_path, symbols=symbols)
    paths_by_date, paths_sorted, last_path_date = load_paths_index(signal_ids=signal_ids)
    liquidity_map = load_liquidity_map()
    tier_edges, tier_edges_source = load_tier_edges()
    contract_open_index = ContractOpenIndex()   # PREREG Sec9 A1; shared cache across both arms

    mc = import_mc_with_asserts()

    decl_by_arm, events_by_arm, both_by_arm, census_by_arm, integrity_by_arm = {}, {}, {}, {}, {}
    for arm in ARM_ORDER:  # never interleave (T8/mc_patch statefulness)
        decls = declare_arm(mc, ledger_rows, ohlc_map, arm)
        primary, both, census = split_primary_and_both(decls)
        decl_by_arm[arm] = decls
        census_by_arm[arm] = census
        events, both_rows, integrity = join_and_classify(
            decls, ledger_by_sid, paths_by_date, paths_sorted, last_path_date, liquidity_map,
            tier_edges, hold_cal_days=mc.HOLD_CAL_DAYS, contract_open_index=contract_open_index)
        events_by_arm[arm] = events_to_df(events)
        both_by_arm[arm] = events_to_df(both_rows)
        integrity_by_arm[arm] = integrity
        atomic_write_parquet(declarations_to_df(decls), OUT_DIR / f"declarations_{arm}.parquet")
        atomic_write_parquet(events_by_arm[arm], OUT_DIR / f"events_{arm}.parquet")
        log(f"[{label}] {arm}: declared={len(decls)} census={census} "
            f"events(tp)={len(events)} both={len(both_rows)} "
            f"A1_integrity(compared={integrity['n_compared']},mismatch={integrity['n_mismatch']})")

    cells = build_cells(events_by_arm, both_by_arm)
    tripwires = compute_tripwires(events_by_arm, decl_by_arm, integrity_by_arm=integrity_by_arm)
    for t in tripwires:
        log(f"  {t}")
    write_bindings_echo(tier_edges, tier_edges_source)
    write_dose_accounting(census_by_arm, cells, integrity_by_arm=integrity_by_arm)
    write_optimism_table(cells, tripwires=tripwires)
    write_knob_calibration_draft(cells)

    total_compared = sum(i["n_compared"] for i in integrity_by_arm.values())
    total_mismatch = sum(i["n_mismatch"] for i in integrity_by_arm.values())
    integrity_stop = (total_compared > 0
                       and (100.0 * total_mismatch / total_compared) > INTEGRITY_MISMATCH_STOP_PCT)
    status = "INTEGRITY_MISMATCH_STOP" if integrity_stop else "OK"
    if integrity_stop:
        log(f"[{label}] STOP: A1 integrity cross-check mismatch share "
            f"{100.0*total_mismatch/total_compared:.2f}% exceeds {INTEGRITY_MISMATCH_STOP_PCT}% -- "
            "investigate before results are read (PREREG Sec9 A1).")

    result.update(status=status, decl_by_arm=decl_by_arm, events_by_arm=events_by_arm,
                   both_by_arm=both_by_arm, census_by_arm=census_by_arm, cells=cells,
                   tripwires=tripwires, ledger_rows=ledger_rows, integrity_by_arm=integrity_by_arm)
    return result


def _pick_worked_examples(events_by_arm: dict):
    """PREREG Sec6.4, orchestrator round-2(b): prefer ONE fill + ONE miss
    (traded-below or no-print) example, searched across arms in ARM_ORDER.
    Returns {'fill': dict|None, 'miss': dict|None}."""
    out = {"fill": None, "miss": None}
    for arm in ARM_ORDER:
        edf = events_by_arm.get(arm, events_to_df([]))
        if out["fill"] is None:
            f = edf.filter(pl.col("bucket") == "fill")
            if f.height:
                out["fill"] = f.to_dicts()[0]
        if out["miss"] is None:
            m = edf.filter(pl.col("bucket").is_in(["miss_traded_below", "miss_no_print"]))
            if m.height:
                out["miss"] = m.to_dicts()[0]
        if out["fill"] is not None and out["miss"] is not None:
            break
    return out


def _synthetic_worked_example():
    """Used only when the real OHLC gate blocks real P1 execution -- proves
    the P1->P2->classification wiring end-to-end on a hand-auditable
    synthetic case, clearly labeled as NOT real data. Mirrors the exact
    proven construction from --selftest's sym_bars_order_hand_computed_tp_
    level (base_idx=60, oscillating pre-signal closes so realized_vol>0 --
    VOL_LOOKBACK=60 means base_idx must be >=60 or realized_vol short-
    circuits to None)."""
    apply_prereg_env_pins()
    import monte_carlo as mc
    set_arm(mc, "arm30")
    d0 = date(2024, 3, 1)
    n = 70
    dates = [d0 + timedelta(days=i) for i in range(n)]
    closes = [100.0] * n
    for i in range(1, 61):
        closes[i] = closes[i - 1] * (1.01 if i % 2 == 0 else 0.99)
    base_idx = 60
    signal_date = dates[base_idx]
    entry_price = closes[base_idx]
    for i in range(base_idx + 1, n):
        closes[i] = entry_price
    highs = [c * 1.0001 for c in closes]
    lows = [c * 0.9999 for c in closes]
    opens = list(closes)
    vol = mc.realized_vol(closes, base_idx)
    tp_level_hand = entry_price * (1 + mc.TP_SIGMA_BASE * vol / 100)
    highs[base_idx + 5] = tp_level_hand + 1.0   # clean TP touch, 5 bars out
    bars = build_sym_bars(dates, closes, highs, lows, opens)
    result = mc.compute_trade_outcome(bars, signal_date, stressed=False, trail=False, symbol="SYN")
    assert result is not None and result["kind"] == "tp", \
        f"synthetic worked example setup failed to produce a 'tp' declaration: {result!r}"
    T = dates[base_idx + result["exit_bar"]]
    entry_premium_real = 3.00   # hand-picked stand-in real contract entry premium
    B_mult = 1.30                # == 1+TP for ARM-30
    B = B_mult * entry_premium_real
    real_high_on_T = 4.10        # hand-picked synthetic contract print, >= B=3.90 -> FILL
    return {
        "synthetic": True, "symbol": "SYN", "signal_date": str(signal_date),
        "vol": result["vol"], "tp_level": result["fire_tp_level"],
        "touch_date": str(T), "contract_ticker": "O:SYN240501C00100000",
        "entry_premium_real": entry_premium_real, "B": B,
        "real_high_on_T": real_high_on_T, "real_close_on_T": 4.05,
        "classification": "fill" if real_high_on_T >= B else "miss_traded_below",
    }


def run_smoke(n=SMOKE_DEFAULT_N):
    result = run_pipeline(n=n, label="smoke")
    lines = [f"# Smoke report -- TP-fill fidelity 30-DTE (N={n})", "",
             f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}", "",
             f"OHLC gate: passed={result['gate']['passed']} path={result['gate']['path']} "
             f"reason={result['gate']['reason']}", ""]

    if result["status"] == "BLOCKED_ON_OHLC_GATE":
        lines.append("## STATUS: BLOCKED on PREREG Sec1 OHLC gate")
        lines.append("")
        lines.append("Real P1/P2 execution did not run. See run.py module docstring "
                      "'SCHEMA GATE STATUS'. Fallback pull must be queued via "
                      "`--fetch-ohlc-fallback` (--db heavy --window off_market).")
        lines.append("")
        ledger_kept = load_ledger_kept().head(n)
        lines.append(f"## Census of the {ledger_kept.height} selected smoke signals "
                      "(first N kept ledger rows by (signal_date,symbol) -- pre-engine, "
                      "still meaningful without OHLC data)")
        lines.append("")
        lines.append("| symbol | signal_date | dte_actual | entry_premium | ticker |")
        lines.append("|---|---|---|---|---|")
        for r in ledger_kept.select(["symbol", "entry_date", "dte_actual", "entry_premium", "ticker"]).to_dicts():
            lines.append(f"| {r['symbol']} | {r['entry_date']} | {r['dte_actual']} | "
                          f"{r['entry_premium']:.3f} | {r['ticker']} |")
        lines.append("")
        lines.append("## SYNTHETIC worked example (NOT real data -- proves P1->P2 wiring only; "
                      "PREREG Sec6.4 needs a REAL worked example once the gate passes)")
        lines.append("")
        ex = _synthetic_worked_example()
        for k, v in ex.items():
            lines.append(f"- {k}: {v}")
    else:
        status_banner = ("STOP -- A1 integrity cross-check mismatch share exceeds "
                          f"{INTEGRITY_MISMATCH_STOP_PCT}% (see below)"
                          if result["status"] == "INTEGRITY_MISMATCH_STOP" else "OK")
        lines.append(f"## STATUS: {status_banner} -- real P1/P2 executed")
        lines.append("")
        audit = result["gate"].get("fallback_spot_audit")
        lines.append("## Spot audit (PREREG Sec9 A1: 5x3 vs MySQL price_history, adjusted close, "
                      "on the fallback parquet)")
        if audit:
            lines.append(f"- ok={audit.get('ok')} n_checked={audit.get('n_checked')} "
                          f"n_mismatch={audit.get('n_mismatch')} "
                          f"close_unadj_bug_suspected={audit.get('close_unadj_bug_suspected')} "
                          f"error={audit.get('error')}")
            for r in audit.get("results", []):
                lines.append(f"    - {r}")
        else:
            lines.append("- (not run -- resolved OHLC path was not the fallback candidate)")
        lines.append("")
        lines.append("## A1 integrity cross-check (contract_day_index vs paths high/close)")
        for arm in ARM_ORDER:
            integ = result["integrity_by_arm"].get(arm, {})
            share = integ.get("mismatch_share")
            lines.append(f"- {arm}: compared={integ.get('n_compared')} mismatch={integ.get('n_mismatch')} "
                          f"share={_fmt(None if share is None else share*100, 2)}%")
        lines.append("")
        for arm in ARM_ORDER:
            census = result["census_by_arm"][arm]
            lines.append(f"### {ARMS[arm]['label']} declaration census (N={n})")
            for k, v in sorted(census.items()):
                lines.append(f"- {k}: {v}")
            lines.append("")
        lines.append("## Tripwires (N=25 -- 6.3/6.5 are FULL-RUN checks; a FAIL/UNDERPOWERED reading here "
                      "is EXPECTED at this N and is not itself a smoke failure)")
        lines.extend(f"- {t}" for t in result["tripwires"])
        lines.append("")
        examples = _pick_worked_examples(result["events_by_arm"])
        lines.append("## Worked examples (PREREG Sec6.4 fields, real data)")
        for label_, ex in examples.items():
            lines.append(f"### {label_.upper()}")
            if ex:
                for k in ("arm", "symbol", "signal_date", "vol", "fire_tp_level", "T", "contract_ticker",
                          "entry_premium_real", "B", "bucket", "unjoinable_reason", "high_mult", "close_mult",
                          "gap_open", "open_mult", "timing_bucket", "days_late"):
                    lines.append(f"- {k}: {ex.get(k)}")
            else:
                lines.append(f"- (no {label_} event in this N={n} smoke sample -- plausible at this N, not a failure)")

    atomic_write_text("\n".join(lines), OUT_DIR / "smoke_report.md")
    atomic_write_json({"n": n, "status": result["status"], "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                       STATE_DIR / "smoke.json")
    log(f"wrote {OUT_DIR / 'smoke_report.md'}")
    return result


def run_full():
    result = run_pipeline(n=None, label="full")
    atomic_write_json({"status": result["status"], "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                       STATE_DIR / "full.json")
    return result


# ===========================================================================
# --selftest (>=12 offline/synthetic tests, BRIEF_BUILDER.md Sec "Implementation order" #3)
# ===========================================================================
def selftest() -> int:
    tests = []

    def t(name):
        def deco(fn):
            tests.append((name, fn))
            return fn
        return deco

    # -- 1. COLMAP presence check against the real files --------------------
    @t("colmap_presence_check_vs_real_files")
    def _1():
        real = {}
        for name, path in _RECON_ASSETS:
            real[name] = _schema_of(path)
        pf = sorted(glob.glob(PATHS_GLOB))
        real["paths"] = _schema_of(pf[0]) if pf else None
        cdi = sorted(glob.glob(str(CONTRACT_DAY_INDEX_ROOT / "underlying=*" / "year=*" / "part.parquet")))
        real["contract_day_index"] = _schema_of(cdi[0]) if cdi else None
        issues = colmap_presence_check(real)
        assert issues == [], f"unexpected COLMAP presence issues: {issues}"

    # -- 2/3. sym_bars tuple ORDER fed to a synthetic compute_trade_outcome,
    #         hand-computed tp_level / sl_level -----------------------------
    @t("sym_bars_order_hand_computed_tp_level")
    def _2():
        apply_prereg_env_pins()
        import monte_carlo as mc
        set_arm(mc, "arm30")   # TP=0.30, SL=-0.70
        d0 = date(2023, 1, 2)
        n = 70
        dates = [d0 + timedelta(days=i) for i in range(n)]
        entry = 50.0
        closes = [entry] * n
        # give realized_vol nonzero variance over the 60-bar lookback so vol>0
        for i in range(1, 61):
            closes[i] = closes[i - 1] * (1.01 if i % 2 == 0 else 0.99)
        base_idx = 60
        signal_date = dates[base_idx]
        entry_price = closes[base_idx]
        for i in range(base_idx + 1, n):
            closes[i] = entry_price
        highs = [c * 1.0001 for c in closes]
        lows = [c * 0.9999 for c in closes]
        opens = list(closes)
        # force a clean TP touch on bar base_idx+5 by hand, using the engine's
        # OWN sigma formula so the "hand-computed" level matches exactly
        vol = mc.realized_vol(closes, base_idx)
        assert vol is not None and vol > 0, "selftest setup: expected positive vol"
        tp_level_hand = entry_price * (1 + mc.TP_SIGMA_BASE * vol / 100)
        highs[base_idx + 5] = tp_level_hand + 1.0
        bars = build_sym_bars(dates, closes, highs, lows, opens)
        assert bars[0] == (dates[0], closes[0], highs[0], lows[0], opens[0]), "tuple order mismatch"
        result = mc.compute_trade_outcome(bars, signal_date, stressed=False, trail=False, symbol="SYN")
        assert result is not None, "expected a declaration, got None"
        assert abs(result["fire_tp_level"] - tp_level_hand) < 1e-6, \
            f"tp_level {result['fire_tp_level']} != hand-computed {tp_level_hand}"
        assert result["kind"] == "tp", f"expected kind=='tp', got {result['kind']!r}"
        assert result["exit_bar"] == 5, f"expected exit_bar==5, got {result['exit_bar']}"

    @t("sym_bars_order_hand_computed_sl_level")
    def _3():
        apply_prereg_env_pins()
        import monte_carlo as mc
        set_arm(mc, "arm30")
        d0 = date(2023, 2, 1)
        n = 70
        dates = [d0 + timedelta(days=i) for i in range(n)]
        entry = 80.0
        closes = [entry] * n
        for i in range(1, 61):
            closes[i] = closes[i - 1] * (1.01 if i % 2 == 0 else 0.99)
        base_idx = 60
        signal_date = dates[base_idx]
        entry_price = closes[base_idx]
        for i in range(base_idx + 1, n):
            closes[i] = entry_price
        highs = [c * 1.0001 for c in closes]
        lows = [c * 0.9999 for c in closes]
        opens = list(closes)
        vol = mc.realized_vol(closes, base_idx)
        sl_level_hand = entry_price * (1 - mc.SL_SIGMA_BASE * vol / 100)
        lows[base_idx + 3] = sl_level_hand - 1.0
        bars = build_sym_bars(dates, closes, highs, lows, opens)
        result = mc.compute_trade_outcome(bars, signal_date, stressed=False, trail=False, symbol="SYN")
        assert result is not None
        assert abs(result["fire_sl_level"] - sl_level_hand) < 1e-6
        assert result["kind"] == "sl", f"expected kind=='sl', got {result['kind']!r}"
        assert result["exit_bar"] == 3

    # -- 4. set_tpsl arm divergence: TP15 touches a bar TP30 does not --------
    @t("set_tpsl_arm_divergence_tp15_vs_tp30")
    def _4():
        apply_prereg_env_pins()
        import monte_carlo as mc
        d0 = date(2023, 3, 1)
        n = 70
        dates = [d0 + timedelta(days=i) for i in range(n)]
        entry = 40.0
        closes = [entry] * n
        for i in range(1, 61):
            closes[i] = closes[i - 1] * (1.01 if i % 2 == 0 else 0.99)
        base_idx = 60
        signal_date = dates[base_idx]
        entry_price = closes[base_idx]
        for i in range(base_idx + 1, n):
            closes[i] = entry_price
        highs = [c * 1.0001 for c in closes]
        lows = [c * 0.9999 for c in closes]
        opens = list(closes)

        set_arm(mc, "arm30")
        vol = mc.realized_vol(closes, base_idx)
        tp30_level = entry_price * (1 + mc.TP_SIGMA_BASE * vol / 100)
        set_arm(mc, "arm15")
        tp15_level = entry_price * (1 + mc.TP_SIGMA_BASE * vol / 100)
        assert tp15_level < tp30_level, "TP15 barrier should sit below TP30 barrier"
        # bar high strictly between the two levels -> TP15 touches, TP30 doesn't
        mid_high = (tp15_level + tp30_level) / 2.0
        highs2 = list(highs)
        highs2[base_idx + 4] = mid_high
        bars = build_sym_bars(dates, closes, highs2, lows, opens)

        set_arm(mc, "arm30")
        r30 = mc.compute_trade_outcome(bars, signal_date, stressed=False, trail=False, symbol="SYN")
        set_arm(mc, "arm15")
        r15 = mc.compute_trade_outcome(bars, signal_date, stressed=False, trail=False, symbol="SYN")
        assert r30["kind"] != "tp", f"ARM-30 should NOT touch TP on this bar, got {r30['kind']!r}"
        assert r15["kind"] == "tp", f"ARM-15 SHOULD touch TP on this bar, got {r15['kind']!r}"

    # -- 5. engine None-return reasons (T10), 3 sub-cases ---------------------
    @t("none_return_signal_date_not_in_bars")
    def _5a():
        apply_prereg_env_pins()
        import monte_carlo as mc
        dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(10)]
        closes = [10.0] * 10
        result = mc.compute_trade_outcome(
            build_sym_bars(dates, closes, closes, closes, closes),
            date(2023, 5, 1), stressed=False, trail=False, symbol="SYN")
        assert result is None
        reason = _classify_none_reason(dates, closes, date(2023, 5, 1), 27, mc.realized_vol)
        assert reason == "signal_date_not_in_bars", reason

    @t("none_return_window_too_short")
    def _5b():
        apply_prereg_env_pins()
        import monte_carlo as mc
        n = 65
        dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(n)]
        closes = [10.0] * n
        for i in range(1, 61):
            closes[i] = closes[i - 1] * (1.01 if i % 2 == 0 else 0.99)
        signal_date = dates[-1]   # last bar -- no room for even 1 forward bar
        result = mc.compute_trade_outcome(
            build_sym_bars(dates, closes, closes, closes, closes),
            signal_date, stressed=False, trail=False, symbol="SYN")
        assert result is None
        reason = _classify_none_reason(dates, closes, signal_date, mc.HOLD_CAL_DAYS, mc.realized_vol)
        assert reason == "window_too_short", reason

    @t("none_return_vol_nonpositive")
    def _5c():
        apply_prereg_env_pins()
        import monte_carlo as mc
        n = 70
        dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(n)]
        closes = [10.0] * n   # perfectly flat -> realized_vol == 0
        base_idx = 60
        result = mc.compute_trade_outcome(
            build_sym_bars(dates, closes, closes, closes, closes),
            dates[base_idx], stressed=False, trail=False, symbol="SYN")
        assert result is None
        reason = _classify_none_reason(dates, closes, dates[base_idx], mc.HOLD_CAL_DAYS, mc.realized_vol)
        assert reason == "vol_none_or_nonpositive", reason

    # -- 6/7/8. join classifier: FILL / traded-below / no-print --------------
    @t("join_classify_fill")
    def _6():
        T = date(2024, 1, 10)
        expiry = date(2024, 2, 1)
        walk_end = date(2024, 2, 1)
        path_by_date = {T: {"close": 5.0, "high": 5.0, "low": 3.0}}
        bucket, reason = classify_join(T, 3.9, expiry, walk_end, "expiry", path_by_date, T)
        assert bucket == "fill" and reason is None, (bucket, reason)

    @t("join_classify_traded_below")
    def _7():
        T = date(2024, 1, 10)
        path_by_date = {T: {"close": 3.0, "high": 3.5, "low": 2.9}}
        bucket, reason = classify_join(T, 3.9, date(2024, 2, 1), date(2024, 2, 1), "expiry", path_by_date, T)
        assert bucket == "miss_traded_below" and reason is None, (bucket, reason)

    @t("join_classify_no_print")
    def _8():
        T = date(2024, 1, 10)
        later = date(2024, 1, 15)
        path_by_date = {later: {"close": 3.0, "high": 3.0, "low": 3.0}}   # T itself absent, but contract still alive past T
        bucket, reason = classify_join(T, 3.9, date(2024, 2, 1), date(2024, 2, 1), "expiry", path_by_date, later)
        assert bucket == "miss_no_print" and reason is None, (bucket, reason)

    # -- 9/10/11. join classifier: unjoinable (3 reasons) ---------------------
    @t("join_classify_unjoinable_expired")
    def _9():
        T = date(2024, 3, 1)
        expiry = date(2024, 2, 15)   # T is after expiry
        walk_end = date(2024, 2, 15)
        path_by_date = {date(2024, 2, 10): {"close": 3.0, "high": 3.0, "low": 3.0}}
        bucket, reason = classify_join(T, 3.9, expiry, walk_end, "expiry", path_by_date, date(2024, 2, 10))
        assert bucket == "unjoinable" and "expired" in reason, (bucket, reason)

    @t("join_classify_unjoinable_walk_end")
    def _10():
        T = date(2024, 3, 10)
        expiry = date(2024, 4, 1)     # not expired
        walk_end = date(2024, 3, 1)   # but past the walk window (short-DTE case)
        path_by_date = {date(2024, 2, 20): {"close": 3.0, "high": 3.0, "low": 3.0}}
        bucket, reason = classify_join(T, 3.9, expiry, walk_end, "walk_limit", path_by_date, date(2024, 2, 20))
        assert bucket == "unjoinable" and "walk window" in reason, (bucket, reason)

    @t("join_classify_unjoinable_path_silent")
    def _11():
        T = date(2024, 2, 20)
        expiry = date(2024, 3, 1)
        walk_end = date(2024, 3, 1)
        last = date(2024, 2, 5)   # path went silent well before T, well before walk_end
        path_by_date = {date(2024, 2, 5): {"close": 3.0, "high": 3.0, "low": 3.0}}
        bucket, reason = classify_join(T, 3.9, expiry, walk_end, "no_more_bars", path_by_date, last)
        assert bucket == "unjoinable" and "no_more_bars" in reason, (bucket, reason)

    # -- 12/13/14. timing walk: same-day / late / never-fill ------------------
    @t("timing_walk_same_day")
    def _12():
        T = date(2024, 1, 10)
        dl = deadline_for(T, 27)
        rows = [(T, 4.0)]
        bucket, days_late = timing_walk(T, dl, 3.9, rows)
        assert bucket == "same_day" and days_late is None, (bucket, days_late)

    @t("timing_walk_late_fill")
    def _13():
        T = date(2024, 1, 10)
        dl = deadline_for(T, 27)
        rows = [(T, 3.0), (T + timedelta(days=1), 3.2), (T + timedelta(days=4), 4.5)]
        bucket, days_late = timing_walk(T, dl, 3.9, rows)
        assert bucket == "late" and days_late == 4, (bucket, days_late)

    @t("timing_walk_never_fill")
    def _14():
        T = date(2024, 1, 10)
        dl = deadline_for(T, 27)
        rows = [(T, 3.0), (T + timedelta(days=1), 3.1)]   # path goes silent, never reaches B by deadline
        bucket, days_late = timing_walk(T, dl, 3.9, rows)
        assert bucket == "never_fill" and days_late is None, (bucket, days_late)

    # -- 15. Wilson CI vs a known published value -----------------------------
    @t("wilson_ci_known_value_50_of_100")
    def _15():
        lo, hi = wilson_ci(50, 100)
        assert abs(lo - 0.4038) < 0.01, lo
        assert abs(hi - 0.5962) < 0.01, hi
        lo0, hi0 = wilson_ci(0, 0)
        assert lo0 is None and hi0 is None

    # -- 16. nearest-rank percentile vs known values (mc_patch.pct reused) ----
    @t("nearest_rank_percentile_known_values")
    def _16():
        assert mc_patch.pct([1, 2, 3, 4, 5], 0.5) == 3
        assert mc_patch.pct([1, 2, 3, 4], 0.75) == 3
        assert mc_patch.pct([], 0.5) is None
        q = safe_quantiles([1, 2, 3, 4, 5], min_n=3)
        assert q["n"] == 5 and not q["suppressed"] and q["p50"] == 3, q

    # -- 17. deadline arithmetic, weekend-spanning case ------------------------
    @t("deadline_arithmetic_weekend_spanning")
    def _17():
        # 2024-01-05 is a Friday; +27 calendar days crosses 4 weekends
        assert deadline_for(date(2024, 1, 5), 27) == date(2024, 2, 1)
        assert date(2024, 1, 5).weekday() == 4  # sanity: Friday

    # -- 18. 'both'-kind exclusion from primary --------------------------------
    @t("both_kind_excluded_from_primary")
    def _18():
        decls = [
            {"kind": "tp", "none_reason": None}, {"kind": "both", "none_reason": None},
            {"kind": "sl", "none_reason": None}, {"kind": None, "none_reason": "vol_none_or_nonpositive"},
        ]
        primary, both, census = split_primary_and_both(decls)
        assert len(primary) == 1 and primary[0]["kind"] == "tp"
        assert len(both) == 1 and both[0]["kind"] == "both"
        assert census == {"tp": 1, "both": 1, "sl": 1, "none:vol_none_or_nonpositive": 1}, census

    # -- 19. N<30 quantile suppression -----------------------------------------
    @t("n_below_30_quantile_suppression")
    def _19():
        small = safe_quantiles(list(range(29)))
        assert small["suppressed"] is True and small["p50"] is None and small["n"] == 29, small
        big = safe_quantiles(list(range(30)))
        assert big["suppressed"] is False and big["p50"] is not None and big["n"] == 30, big

    # -- 20. liquidity tier edges boundary inclusivity --------------------------
    @t("liquidity_tier_edges_boundaries")
    def _20():
        edges = [320, 1191, 3486, 14524]
        assert liquidity_tier_for(None, edges) is None
        assert liquidity_tier_for(320, edges) == "t1"
        assert liquidity_tier_for(320.0001, edges) == "t2"
        assert liquidity_tier_for(1191, edges) == "t2"
        assert liquidity_tier_for(3486, edges) == "t3"
        assert liquidity_tier_for(14524, edges) == "t4"
        assert liquidity_tier_for(14525, edges) == "t5"

    # -- 21. import order + runtime asserts (T9) --------------------------------
    @t("import_order_and_runtime_asserts")
    def _21():
        mc = import_mc_with_asserts()
        assert os.path.abspath(mc.__file__).lower().startswith(_ROOT.lower())
        set_arm(mc, "arm30")
        assert mc.TP_SIGMA_BASE == mc.TP_SIGMA_STRESS
        set_arm(mc, "arm15")
        assert mc.TP_SIGMA_BASE == mc.TP_SIGMA_STRESS

    # -- 22. gap-open decomposition logic ---------------------------------
    @t("gap_open_decomposition_logic")
    def _22():
        is_gap, om = gap_open_decomposition(None, 3.9, 3.0)
        assert is_gap is None and om is None
        is_gap, om = gap_open_decomposition(4.2, 3.9, 3.0)
        assert is_gap is True and abs(om - 1.4) < 1e-9, (is_gap, om)
        is_gap, om = gap_open_decomposition(3.5, 3.9, 3.0)
        assert is_gap is False and abs(om - (3.5 / 3.0)) < 1e-9, (is_gap, om)

    # -- 23/24. ContractOpenIndex (PREREG Sec9 A1): present / absent cases ----
    # Cache injected directly (mirrors build_ledger_v2.py's own PartitionCache
    # selftest pattern) -- no real B:/ dependency.
    @t("contract_open_index_present_in_index")
    def _23():
        idx = ContractOpenIndex()
        d1, d2 = date(2023, 1, 10), date(2023, 1, 11)
        idx._cache[("AAA", 2023)] = {
            ("O:AAA230210C00100000", d1): {"open": 3.05, "high": 3.20, "close": 3.10},
            ("O:AAA230210C00100000", d2): {"open": 3.15, "high": 3.40, "close": 3.30},
        }
        row = idx.lookup("AAA", "O:AAA230210C00100000", d1)
        assert row == {"open": 3.05, "high": 3.20, "close": 3.10}, row
        row2 = idx.lookup("AAA", "O:AAA230210C00100000", d2)
        assert row2["open"] == 3.15, row2
        assert idx.n_files_read == 0, "injected cache must not trigger a real parquet read"

    @t("contract_open_index_absent_cases")
    def _24():
        idx = ContractOpenIndex()
        # (a) partition present, but no row for this exact (ticker,date)
        idx._cache[("BBB", 2023)] = {("O:BBB230210C00050000", date(2023, 1, 10)): {"open": 1.0, "high": 1.1, "close": 1.05}}
        assert idx.lookup("BBB", "O:BBB230210C00050000", date(2023, 1, 11)) is None   # wrong date
        assert idx.lookup("BBB", "O:BBB230210C00099000", date(2023, 1, 10)) is None   # wrong ticker
        # (b) partition itself absent (no underlying=X/year=Y at all -- injected as None,
        # mirroring PartitionCache.get()'s own "file doesn't exist -> None" convention)
        idx._cache[("CCC", 2023)] = None
        assert idx.lookup("CCC", "O:CCC230210C00050000", date(2023, 1, 10)) is None
        # (c) year genuinely never touched -> lazily resolves via _partition (real B:/ path,
        # nonexistent underlying -> file-not-found -> None, no crash)
        assert idx.lookup("ZZZ_NONEXISTENT_TICKER_PROBE", "O:ZZZ990101C00001000", date(1999, 1, 1)) is None

    # -- 25. integrity_cross_check: match / mismatch detection (PREREG Sec9 A1) --
    @t("integrity_cross_check_match_and_mismatch")
    def _25():
        is_match, detail = integrity_cross_check({"high": 5.30, "close": 5.10}, {"high": 5.30, "close": 5.10})
        assert is_match is True and detail is None
        is_match, detail = integrity_cross_check({"high": 5.30, "close": 5.10}, {"high": 5.3001, "close": 5.1001})
        assert is_match is True, "within tolerance must still match"
        is_match, detail = integrity_cross_check({"high": 5.30, "close": 5.10}, {"high": 6.00, "close": 5.10})
        assert is_match is False and detail["paths_high"] == 5.30 and detail["index_high"] == 6.00, detail
        is_match, detail = integrity_cross_check({"high": 5.30, "close": None}, {"high": 5.30, "close": 5.10})
        assert is_match is False, "a None on either side must not silently pass"

    # -- 26. _compare_ohlc_row: close/close_unadj mixup detection (PREREG Sec9 A1) --
    @t("compare_ohlc_row_close_unadj_bug_detection")
    def _26():
        check_cols = ["close", "high", "low"]
        # (a) clean match
        r = _compare_ohlc_row(check_cols, {"close": 100.0, "high": 101.0, "low": 99.0},
                               {"close": 100.0, "high": 101.0, "low": 99.0, "close_unadj": 250.0})
        assert r["status"] == "match" and r["close_unadj_bug_suspected"] is False, r
        # (b) THE named failure mode: parquet's 'close' column actually holds close_unadj
        r = _compare_ohlc_row(check_cols, {"close": 250.0, "high": 101.0, "low": 99.0},
                               {"close": 100.0, "high": 101.0, "low": 99.0, "close_unadj": 250.0})
        assert r["status"] == "MISMATCH" and r["close_unadj_bug_suspected"] is True, r
        # (c) a generic mismatch that does NOT match close_unadj either -- must not false-positive
        r = _compare_ohlc_row(check_cols, {"close": 77.0, "high": 101.0, "low": 99.0},
                               {"close": 100.0, "high": 101.0, "low": 99.0, "close_unadj": 250.0})
        assert r["status"] == "MISMATCH" and r["close_unadj_bug_suspected"] is False, r

    # -- 27. join_and_classify wires A1 open-join + integrity check end-to-end --
    @t("join_and_classify_a1_wiring_end_to_end")
    def _27():
        T = date(2024, 1, 10)
        ledger_by_sid = {"AAA|2024-01-05": {"dte_actual": 30, "expiry": date(2024, 2, 4),
                                             "walk_end": date(2024, 2, 4), "path_end_reason": "expiry",
                                             "entry_premium_real": 3.0, "contract_ticker": "O:AAA240204C00100000"}}
        decls = [{"arm": "arm30", "symbol": "AAA", "signal_date": date(2024, 1, 5),
                  "signal_id": "AAA|2024-01-05", "kind": "tp", "T": T, "vol": 2.0, "fire_tp_level": 103.0}]
        paths_by_date = {"AAA|2024-01-05": {T: {"close": 3.95, "high": 4.10, "low": 3.80}}}
        paths_sorted = {"AAA|2024-01-05": [(T, 4.10)]}
        last_path_date = {"AAA|2024-01-05": T}
        idx = ContractOpenIndex()
        idx._cache[("AAA", 2024)] = {("O:AAA240204C00100000", T): {"open": 4.00, "high": 4.10, "close": 3.95}}
        events, both_rows, integrity = join_and_classify(
            decls, ledger_by_sid, paths_by_date, paths_sorted, last_path_date, {}, [320, 1191, 3486, 14524],
            hold_cal_days=27, contract_open_index=idx)
        assert len(events) == 1, events
        e = events[0]
        assert e["bucket"] == "fill", e
        assert e["gap_open"] is True and abs(e["open_mult"] - 4.00 / 3.0) < 1e-9, e   # open=4.00 >= B=3.90
        assert integrity["n_compared"] == 1 and integrity["n_mismatch"] == 0, integrity
        # now corrupt the index's high so the cross-check must catch it
        idx2 = ContractOpenIndex()
        idx2._cache[("AAA", 2024)] = {("O:AAA240204C00100000", T): {"open": 4.00, "high": 9.99, "close": 3.95}}
        events2, _, integrity2 = join_and_classify(
            decls, ledger_by_sid, paths_by_date, paths_sorted, last_path_date, {}, [320, 1191, 3486, 14524],
            hold_cal_days=27, contract_open_index=idx2)
        assert integrity2["n_compared"] == 1 and integrity2["n_mismatch"] == 1, integrity2
        assert integrity2["mismatches"][0]["index_high"] == 9.99, integrity2

    log("=== run.py --selftest ===")
    n_pass = n_fail = 0
    failures = []
    for name, fn in tests:
        try:
            fn()
            n_pass += 1
            log(f"  [PASS] {name}")
        except Exception as e:  # noqa: BLE001 -- collect every failure, don't stop at first
            n_fail += 1
            failures.append((name, f"{type(e).__name__}: {e}"))
            log(f"  [FAIL] {name}: {type(e).__name__}: {e}")
    log(f"=== {n_pass}/{len(tests)} PASS, {n_fail} FAIL ===")
    atomic_write_json({"n_pass": n_pass, "n_fail": n_fail, "n_total": len(tests),
                       "failures": failures, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                       STATE_DIR / "selftest.json")
    return 0 if n_fail == 0 else 1


# ===========================================================================
# CLI
# ===========================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true", help=">=12 offline/synthetic tests")
    ap.add_argument("--smoke", nargs="?", const=SMOKE_DEFAULT_N, type=int, default=None,
                     metavar="N", help=f"end-to-end on first N kept ledger signals (default {SMOKE_DEFAULT_N})")
    ap.add_argument("--full", action="store_true", help="end-to-end on all kept ledger signals (queue only, T13)")
    ap.add_argument("--analyze", action="store_true", help="rebuild tables/tripwires from existing out/*.parquet")
    ap.add_argument("--fetch-ohlc-fallback", action="store_true",
                     help="PREREG Sec1 fallback bulk pull (--db heavy queue job only, never run directly)")
    ap.add_argument("--recon", action="store_true", help="schema recon only (diagnostic)")
    args = ap.parse_args()

    if args.recon:
        recon_schemas()
        return 0
    if args.selftest:
        return selftest()
    if args.fetch_ohlc_fallback:
        fetch_ohlc_fallback()
        return 0
    if args.smoke is not None:
        result = run_smoke(n=args.smoke)
        # BLOCKED_ON_OHLC_GATE is a clean, expected stop (exit 0, not an error).
        # INTEGRITY_MISMATCH_STOP is a genuine STOP condition (PREREG Sec9 A1,
        # >1% mismatch) -- non-zero so the caller/queue notices.
        return 0 if result["status"] in ("OK", "BLOCKED_ON_OHLC_GATE") else 1
    if args.full:
        result = run_full()
        return 0 if result["status"] == "OK" else 1
    if args.analyze:
        log("--analyze (standalone) not yet needed -- --full runs the identical table-build "
            "at its end (BRIEF_BUILDER.md Sec Implementation order #8). Run --full instead.")
        return 2
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
