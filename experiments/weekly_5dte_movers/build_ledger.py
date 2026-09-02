"""
build_ledger.py -- weekly_5dte_movers, Stage A (BUILD_BRIEF.md).

Binding spec: PREREG.md (LOCKED) + BUILD_BRIEF.md (implementation detail). Read those
first if you are debugging this file.

For every in-scope trading week (Mon 2022-08-01 .. expiry <= 2026-06-12, ~202 weeks),
builds one ledger row per (contract, entry_date) where entry_date is a Monday or Tuesday
of that week and the contract's OCC-parsed expiry equals that week's expiry_day (last
session of the week -- Friday normally, Thursday on a holiday-Friday week). Outcome =
max(high) strictly after entry_date through expiry_day ("theoretical max growth", the
owner's explicit framing since Friday itself is 0-DTE territory).

Substrate: B:/polygon_derived/contract_day_index/_bydate/<date>.parquet -- one parquet
per session with ALL underlyings (built by experiments/flatfile_exploitation/build_index.py
phase 1). This is the correct access pattern for a per-WEEK scan (need every underlying on
a handful of specific dates), as opposed to the underlying=<U>/year=<Y>/ hive layout (built
for the opposite access pattern: one underlying's full history). Schema introspected
2026-08-17 directly off a hive partition (BUILD_REPORT.md "Schema introspection"): date,
ticker, underlying, expiry, cp, strike, adjusted, volume, open, close, high, low,
window_start, transactions -- matches BUILD_BRIEF's expected schema exactly (tape columns
present), so the raw-gzip fallback was NOT needed.

Calendar: `ff_common.list_session_dates('day_aggs_v1')` (archive filesystem walk) is the
sole calendar source -- no MySQL in Stage A (PREREG "Compute plan").

Two `root`/`underlying` columns: the PREREG ledger-column list names both `underlying` and
`root` as separate columns, but `ff_common.add_opra_columns` (which built the index) only
ever produces ONE parsed root string (called `underlying` in the index; this is the RAW OCC
root, e.g. "AMD1" for a non-standard-deliverable series -- never resolved to a "true"
underlying, out of scope per ff_common's own docstring). There is no second, different
"root" value available anywhere in this codebase. Conservative interpretation (documented
in BUILD_REPORT.md OPEN QUESTIONS): `root` is emitted as an exact alias of `underlying`.

`strike_thousandths`: dropped by the contract_day_index build (only float `strike` was
kept). Recomputed here as round(strike * 1000) -- exact, since strike was originally
derived as strike_thousandths / 1000.0 with no lossy intermediate step.

CLI:
    python build_ledger.py --smoke                # 4 named weeks, isolated _smoke/ output
    python build_ledger.py --year 2024             # one expiry-year chunk (real output)
    python build_ledger.py --full                  # all in-scope years, sequential, resumable

--smoke is the ONLY mode this build session is allowed to run (BUILD_BRIEF hard rule).
--year/--full are implemented for the orchestrator's later queued run and are not executed
here; they are exercised structurally (imports, schema) by --selftest-import via
smoke_test.py, not by an actual invocation.
"""
from __future__ import annotations

import argparse
import csv
import json
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

import polars as pl  # noqa: E402

import ff_common  # noqa: E402
assert "flatfile_exploitation" in ff_common.__file__.replace("\\", "/"), \
    f"ff_common imported from unexpected location: {ff_common.__file__}"

# ---------------------------------------------------------------- constants

FIRST_MONDAY = date(2022, 8, 1)
LAST_EXPIRY = date(2026, 6, 12)   # PREREG-locked window end (last full week before holdout)

INDEX_ROOT = ff_common.DERIVED_ROOT / "contract_day_index"
BYDATE_DIR = INDEX_ROOT / "_bydate"

OUT_ROOT = ff_common.DERIVED_ROOT / "weekly_5dte_movers"
LEDGER_DIR = OUT_ROOT / "ledger"
LEDGER_SMOKE_DIR = LEDGER_DIR / "_smoke"
COUNTERS_CSV = LEDGER_DIR / "week_counters.csv"
SMOKE_COUNTERS_CSV = LEDGER_SMOKE_DIR / "week_counters.csv"
SMOKE_LEDGER_PARQUET = LEDGER_SMOKE_DIR / "ledger_smoke.parquet"

STATE_PATH = Path(_ROOT) / ".horizon" / "weekly-5dte-movers" / "state.json"

# The 4 smoke weeks, named by their EXPIRY DAY (BUILD_BRIEF "Smoke scope"):
#   2024-05-24 NVDA earnings week (2024-05-22)  -- expect NVDA calls in top growth_mult
#   2023-07-28 quiet control week
#   2024-03-28 Good Friday week (holiday Fri -> Thu expiry)
#   2025-01-24 MLK Monday week (2025-01-20 holiday -> zero Mon entries)
SMOKE_EXPIRY_ANCHORS = [date(2024, 5, 24), date(2023, 7, 28), date(2024, 3, 28), date(2025, 1, 24)]

LEDGER_SCHEMA: dict[str, pl.DataType] = {
    "ticker": pl.Utf8,
    "underlying": pl.Utf8,
    "root": pl.Utf8,
    "adjusted": pl.Boolean,
    "cp": pl.Utf8,
    "strike_thousandths": pl.Int64,
    "strike": pl.Float64,
    "expiry": pl.Date,
    "week_monday": pl.Date,
    "expiry_day": pl.Date,
    "is_monthly_opex": pl.Boolean,
    "entry_date": pl.Date,
    "entry_dow": pl.Utf8,
    "entry_open": pl.Float64,
    "entry_close": pl.Float64,
    "entry_high": pl.Float64,
    "entry_low": pl.Float64,
    "entry_volume": pl.Int64,
    "entry_transactions": pl.Int64,
    "entry_dollar_vol": pl.Float64,
    "hl_range_pct": pl.Float64,
    "close_vs_open_pct": pl.Float64,
    "dte_calendar": pl.Int64,
    "dte_trading": pl.Int64,
    "n_later_prints": pl.Int64,
    "max_future_high": pl.Float64,
    "max_high_date": pl.Date,
    "close_at_expiry": pl.Float64,
    "growth_mult": pl.Float64,
    "no_later_print": pl.Int64,
}
LEDGER_COLUMNS = list(LEDGER_SCHEMA.keys())


def log(msg) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- pure calendar helpers
# (reused by build_features.py and smoke_test.py -- keep these dependency-free)

def week_monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def third_friday(year: int, month: int) -> date:
    """Pure calendar math -- the 3rd Friday of a given (year, month), regardless of
    whether it is a trading holiday."""
    d = date(year, month, 1)
    first_friday = d + timedelta(days=(4 - d.weekday()) % 7)
    return first_friday + timedelta(days=14)


def is_monthly_opex_week(week_monday: date, expiry_day: date) -> bool:
    """True iff this trading week's Mon-Fri span contains the calendar 3rd Friday of
    its month (BUILD_BRIEF "OPEX"). Checks both week_monday's and expiry_day's month in
    case a week ever straddles a month boundary (3rd Fridays are always mid-month, so in
    practice this never fires for both months at once)."""
    for month_ref in {week_monday, expiry_day}:
        tf = third_friday(month_ref.year, month_ref.month)
        if week_monday_of(tf) == week_monday:
            return True
    return False


def group_sessions_into_weeks(session_dates: list) -> dict:
    """Map week_monday -> sorted list of session dates within that ISO (Mon-Fri) week."""
    weeks: dict = {}
    for d in session_dates:
        wm = week_monday_of(d)
        weeks.setdefault(wm, []).append(d)
    for wm in weeks:
        weeks[wm].sort()
    return weeks


def load_in_scope_weeks(cutoff: date = LAST_EXPIRY) -> dict:
    """Authoritative week map for the whole experiment: week_monday -> session list,
    restricted to weeks whose TRUE expiry_day (last session of that week, using the
    FULL un-truncated archive calendar) is <= cutoff.

    Deliberately does NOT simply filter session_dates <= cutoff and then group (which
    the brief's literal Stage A step 1 wording suggests) -- that ordering only gives the
    same answer if cutoff lands exactly on a week boundary (Friday), which it does here
    by design (2026-06-12 is the Friday immediately before the 2026-06-15 Monday holdout
    line). Grouping from the FULL calendar first and filtering completed weeks second is
    the same result but is also correct if that assumption were ever violated, so it is
    the implementation used. A defensive assertion below still confirms no truncation.
    """
    all_sessions = ff_common.list_session_dates("day_aggs_v1")
    all_weeks = group_sessions_into_weeks(all_sessions)
    in_scope = {wm: sess for wm, sess in all_weeks.items() if sess[-1] <= cutoff}

    # Defensive cross-check against the brief's literal "filter-then-group" reading.
    filtered_sessions = [d for d in all_sessions if d <= cutoff]
    filtered_weeks = group_sessions_into_weeks(filtered_sessions)
    if in_scope:
        last_wm = max(in_scope)
        assert filtered_weeks.get(last_wm) == in_scope[last_wm], (
            f"cutoff {cutoff} does not land on a week boundary -- filter-then-group "
            f"({filtered_weeks.get(last_wm)}) disagrees with group-then-filter "
            f"({in_scope[last_wm]}) for the boundary week {last_wm}. PREREG assumes "
            f"these agree; re-examine the cutoff choice before proceeding."
        )
    return in_scope


# ---------------------------------------------------------------- IO helpers

def _atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.write_parquet(tmp, compression="snappy")
    os.replace(tmp, path)


def _atomic_write_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="ascii", errors="replace") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def _write_counters_csv(path: Path, new_rows: list) -> None:
    """Idempotent merge-by-week_monday write: read existing rows (if any), overlay
    new_rows keyed by week_monday, sort, write atomically."""
    existing: dict = {}
    if path.exists():
        with open(path, "r", encoding="ascii", newline="") as f:
            for row in csv.DictReader(f):
                existing[row["week_monday"]] = row
    for row in new_rows:
        existing[row["week_monday"]] = {k: str(v) for k, v in row.items()}
    ordered = sorted(existing.values(), key=lambda r: r["week_monday"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fieldnames = ["week_monday", "expiry_day", "is_monthly_opex", "entries", "contracts",
                  "excluded_nonstandard_expiry", "zero_close_drops"]
    with open(tmp, "w", encoding="ascii", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in ordered:
            w.writerow({k: row.get(k, "") for k in fieldnames})
    os.replace(tmp, path)


def _empty_ledger_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=LEDGER_SCHEMA)


# ---------------------------------------------------------------- core per-week logic

def read_week_tape(week_sessions: list, bydate_dir: Path = BYDATE_DIR) -> pl.DataFrame:
    frames = []
    for d in week_sessions:
        p = bydate_dir / f"{d.isoformat()}.parquet"
        if not p.exists():
            raise FileNotFoundError(f"missing _bydate partition for session {d}: {p}")
        frames.append(pl.read_parquet(p))
    return pl.concat(frames, how="vertical") if frames else pl.DataFrame()


def process_week(week_monday: date, week_sessions: list, bydate_dir: Path = BYDATE_DIR):
    """Returns (ledger_df, counters_dict) for one trading week.

    ledger_df has exactly LEDGER_COLUMNS/LEDGER_SCHEMA, one row per (contract,
    entry_date) that survives the Stage A filters (PREREG "Entry events" / BUILD_BRIEF
    Stage A steps 2-5).
    """
    expiry_day = week_sessions[-1]
    opex = is_monthly_opex_week(week_monday, expiry_day)
    week_span = set(week_sessions)

    week_df = read_week_tape(week_sessions, bydate_dir)

    cu = week_df.filter(pl.col("expiry") == expiry_day)
    same_week_other = week_df.filter(
        (pl.col("expiry") != expiry_day) & (pl.col("expiry").is_in(list(week_span)))
    )
    excluded_nonstandard_expiry = same_week_other.height
    n_contracts = int(cu["ticker"].n_unique()) if cu.height else 0

    entry_days = [d for d in week_sessions if d.weekday() in (0, 1)]  # Mon=0, Tue=1

    zero_close_drops = 0
    ledger_week = _empty_ledger_frame()

    if entry_days and cu.height:
        candidates = cu.filter(pl.col("date").is_in(entry_days) & (pl.col("volume") > 0))
        zero_close_drops = candidates.filter(pl.col("close") <= 0).height
        entries = candidates.filter(pl.col("close") > 0)

        if entries.height:
            entries = entries.rename({
                "date": "entry_date", "open": "entry_open", "close": "entry_close",
                "high": "entry_high", "low": "entry_low", "volume": "entry_volume",
                "transactions": "entry_transactions",
            })

            fwd = (
                entries.select(["ticker", "entry_date"])
                .join(cu.select(["ticker", "date", "close", "high"]), on="ticker", how="left")
                .filter(pl.col("date") > pl.col("entry_date"))
            )

            if fwd.height:
                agg = (
                    fwd.sort(["ticker", "entry_date", "high", "date"],
                             descending=[False, False, True, False])
                    .group_by(["ticker", "entry_date"], maintain_order=True)
                    .agg([
                        pl.first("date").alias("max_high_date"),
                        pl.first("high").alias("max_future_high"),
                        pl.len().alias("n_later_prints"),
                    ])
                )
                close_exp = (
                    fwd.filter(pl.col("date") == expiry_day)
                    .select(["ticker", "entry_date", "close"])
                    .rename({"close": "close_at_expiry"})
                    .unique(subset=["ticker", "entry_date"], keep="first")
                )
            else:
                agg = pl.DataFrame(schema={"ticker": pl.Utf8, "entry_date": pl.Date,
                                            "max_high_date": pl.Date, "max_future_high": pl.Float64,
                                            "n_later_prints": pl.Int64})
                close_exp = pl.DataFrame(schema={"ticker": pl.Utf8, "entry_date": pl.Date,
                                                  "close_at_expiry": pl.Float64})

            ledger_week = entries.join(agg, on=["ticker", "entry_date"], how="left")
            ledger_week = ledger_week.join(close_exp, on=["ticker", "entry_date"], how="left")

            ledger_week = ledger_week.with_columns(pl.col("n_later_prints").fill_null(0))
            ledger_week = ledger_week.with_columns(
                (pl.col("n_later_prints") == 0).cast(pl.Int64).alias("no_later_print")
            )
            ledger_week = ledger_week.with_columns(
                pl.when(pl.col("no_later_print") == 1)
                .then(None)
                .otherwise(pl.col("max_future_high") / pl.col("entry_close"))
                .alias("growth_mult")
            )
            ledger_week = ledger_week.with_columns([
                pl.lit(week_monday).alias("week_monday"),
                pl.lit(expiry_day).alias("expiry_day"),
                pl.lit(opex).alias("is_monthly_opex"),
                pl.col("underlying").alias("root"),
                ((pl.col("strike") * 1000).round(0).cast(pl.Int64)).alias("strike_thousandths"),
                ((pl.col("entry_high") - pl.col("entry_low")) / pl.col("entry_close") * 100.0)
                .alias("hl_range_pct"),
                pl.when(pl.col("entry_open") > 0)
                .then((pl.col("entry_close") - pl.col("entry_open")) / pl.col("entry_open") * 100.0)
                .otherwise(None)
                .alias("close_vs_open_pct"),
                (pl.col("entry_close") * pl.col("entry_volume") * 100.0).alias("entry_dollar_vol"),
            ])

            dow_map = {d: ("Mon" if d.weekday() == 0 else "Tue") for d in entry_days}
            dte_trading_map = {d: sum(1 for s in week_sessions if s > d) for d in entry_days}
            dte_cal_map = {d: (expiry_day - d).days for d in entry_days}

            entry_date_list = ledger_week["entry_date"].to_list()
            ledger_week = ledger_week.with_columns([
                pl.Series("entry_dow", [dow_map[d] for d in entry_date_list], dtype=pl.Utf8),
                pl.Series("dte_trading", [dte_trading_map[d] for d in entry_date_list], dtype=pl.Int64),
                pl.Series("dte_calendar", [dte_cal_map[d] for d in entry_date_list], dtype=pl.Int64),
            ])

            ledger_week = ledger_week.select([
                pl.col(c).cast(t) for c, t in LEDGER_SCHEMA.items()
            ])

    counters = {
        "week_monday": week_monday.isoformat(),
        "expiry_day": expiry_day.isoformat(),
        "is_monthly_opex": opex,
        "entries": ledger_week.height,
        "contracts": n_contracts,
        "excluded_nonstandard_expiry": excluded_nonstandard_expiry,
        "zero_close_drops": zero_close_drops,
    }
    return ledger_week, counters


# ---------------------------------------------------------------- smoke driver

def run_smoke(verbose: bool = True) -> dict:
    """Builds exactly the 4 named smoke weeks into an isolated _smoke sidecar. Never
    touches the real ledger/, week_counters.csv, or .horizon state.json -- mirrors
    experiments/flatfile_exploitation/build_index.py's --smoke isolation pattern."""
    t0 = time.time()
    all_weeks = load_in_scope_weeks()

    # Find the week whose expiry_day matches each named anchor.
    by_expiry = {sess[-1]: wm for wm, sess in all_weeks.items()}
    target_weeks = []
    for anchor in SMOKE_EXPIRY_ANCHORS:
        wm = by_expiry.get(anchor)
        if wm is None:
            raise AssertionError(f"smoke anchor expiry_day {anchor} not found as any week's "
                                  f"last session in the archive calendar")
        target_weeks.append((wm, all_weeks[wm]))

    per_week = []
    frames = []
    for wm, sess in target_weeks:
        ledger_week, counters = process_week(wm, sess)
        frames.append(ledger_week)
        per_week.append(counters)
        if verbose:
            log(f"  SMOKE week {wm} (expiry {counters['expiry_day']}, "
                f"opex={counters['is_monthly_opex']}): entries={counters['entries']} "
                f"contracts={counters['contracts']} "
                f"excluded_nonstandard_expiry={counters['excluded_nonstandard_expiry']} "
                f"zero_close_drops={counters['zero_close_drops']}")

    combined = pl.concat(frames, how="vertical") if frames else _empty_ledger_frame()
    _atomic_write_parquet(combined, SMOKE_LEDGER_PARQUET)
    _write_counters_csv(SMOKE_COUNTERS_CSV, per_week)

    elapsed = time.time() - t0
    report = {
        "weeks": [c["week_monday"] for c in per_week],
        "per_week": per_week,
        "total_rows": combined.height,
        "ledger_path": str(SMOKE_LEDGER_PARQUET),
        "counters_path": str(SMOKE_COUNTERS_CSV),
        "elapsed_seconds": round(elapsed, 2),
    }
    if verbose:
        log(f"SMOKE DONE: {combined.height} ledger rows across {len(per_week)} weeks in "
            f"{elapsed:.2f}s -- {SMOKE_LEDGER_PARQUET}")
    return report


# ---------------------------------------------------------------- year / full drivers
# (implemented for the orchestrator's later queued run -- NOT executed by this build
# session; --smoke is the only mode exercised here per BUILD_BRIEF hard rule.)

def _update_state_cursor(year: int, year_report: dict) -> None:
    """Atomic read-modify-write on .horizon/weekly-5dte-movers/state.json. Touches ONLY
    cursor/updated/done/counters -- never phase or config (orchestrator-owned)."""
    state = {}
    if STATE_PATH.exists():
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
    state.setdefault("done", [])
    if year not in state["done"]:
        state["done"].append(year)
        state["done"].sort()
    state["cursor"] = year
    state.setdefault("counters", {})
    state["counters"][f"stageA_year_{year}"] = year_report
    state["updated"] = date.today().isoformat()
    tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)
    os.replace(tmp, STATE_PATH)


def run_year(year: int, verbose: bool = True) -> dict:
    t0 = time.time()
    all_weeks = load_in_scope_weeks()
    weeks_this_year = sorted(
        (wm, sess) for wm, sess in all_weeks.items() if sess[-1].year == year
    )
    if not weeks_this_year:
        log(f"no in-scope weeks with expiry_day.year == {year}; nothing to do")
        return {"year": year, "weeks": 0, "rows": 0}

    per_week = []
    frames = []
    for wm, sess in weeks_this_year:
        ledger_week, counters = process_week(wm, sess)
        frames.append(ledger_week)
        per_week.append(counters)
        if verbose:
            log(f"  [{year}] week {wm}: entries={counters['entries']} "
                f"contracts={counters['contracts']}")

    combined = pl.concat(frames, how="vertical") if frames else _empty_ledger_frame()
    out_path = LEDGER_DIR / f"ledger_{year}.parquet"
    _atomic_write_parquet(combined, out_path)
    _write_counters_csv(COUNTERS_CSV, per_week)

    elapsed = time.time() - t0
    report = {
        "year": year, "weeks": len(weeks_this_year), "rows": combined.height,
        "elapsed_seconds": round(elapsed, 1), "out_path": str(out_path),
    }
    _update_state_cursor(year, report)
    if verbose:
        log(f"YEAR {year} DONE: {combined.height} rows across {len(weeks_this_year)} weeks "
            f"in {elapsed:.1f}s -- {out_path}")
    return report


def run_full(verbose: bool = True) -> list:
    all_weeks = load_in_scope_weeks()
    years = sorted({sess[-1].year for sess in all_weeks.values()})
    reports = []
    for y in years:
        reports.append(run_year(y, verbose=verbose))
    return reports


# ---------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--smoke", action="store_true", help="4 named weeks only, isolated _smoke output")
    ap.add_argument("--year", type=int, help="process one expiry year (real ledger output)")
    ap.add_argument("--full", action="store_true", help="process all in-scope years sequentially")
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
