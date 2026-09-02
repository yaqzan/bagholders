"""build_exclusions.py -- liquidity-floor exclusion set for floor_mc_2026_08
(PREREG.md commit 5a9b4949, section 3 "Floor variables + PIT justification"
and "Coverage rules"). Build step 1 (see module docstring in floorMC_run.py
for step 2, the MC runner that consumes this file's output).

REPORT-ONLY FILE BUILD. Pure polars. No MySQL, no monte_carlo import, no
task queue (mirrors experiments/liquidity_floor_2026_08/build_floor_sweep.py's
own "No MySQL, no monte_carlo import, no task queue" framing -- same order of
magnitude of work: one ~4,936-row parquet scan, seconds not minutes, so this
runs foreground like that script did, never via trader queue submit).

======================================================================
JOIN-KEY NAMING TRAP -- READ BEFORE TOUCHING THIS FILE
======================================================================
B:\\polygon_derived\\ledger_v2\\ledger.parquet has TWO different "ticker-shaped"
string columns (verified live, 2026-08-11, schema recon):

  - `symbol` : the UNDERLYING STOCK ticker (e.g. "AAPL"). ONE row per
               (symbol, entry_date) SIGNAL -- signal_id is literally
               f"{symbol}|{date.isoformat()}" (build_ledger_v2.py:312).
               THIS is what monte_carlo.py's own signal keys use: Stock.symbol
               is Stock's primary key (database/models/core.py:3582
               `symbol = CharField(primary_key=True)`), and Score.symbol is a
               DeferredForeignKey onto that PK column -- so accessing
               `sig.symbol_id` in monte_carlo.py returns this exact ticker
               STRING directly (verified live: `Score.select(...).symbol_id`
               returned e.g. 'A', a str, not an int -- the well-known "peewee
               FK-per-row trap": .symbol_id skips the lazy Stock join and
               reads the raw stored FK value, which for a Stock FK already
               *is* the ticker).
  - `ticker` : the OPTION CONTRACT id (OCC-style, e.g.
               "O:VRTX220902C00290000") -- ONLY populated when status=='kept'
               (null for all 533 non-kept rows, verified). This is a
               DIFFERENT entity (one specific chosen contract), never the
               right column to join against a monte_carlo signal.

PREREG section 3 writes the join key as "(ticker, entry_date)" -- read here
as colloquial English for "the stock ticker", NOT literally
`ledger.parquet`'s own `ticker` column. This matches
experiments/liquidity_floor_2026_08/build_floor_sweep.py's OWN join, which
uses `symbol`+`entry_date` against signal_liquidity.parquet (its
`load_base()`), and that same script's "duplicate (ticker, entry_date)"
data-quality-check name, which is really testing signal-identity uniqueness
(confirmed here: 0 duplicate (symbol, entry_date) pairs across all 4,936 rows
of any status -- so (symbol, entry_date) is a safe, unique join key; the
literal `ticker` column would also be unique but is null for 533/4936 rows
and is the wrong entity regardless).

This script's OUTPUT column is named `ticker` (matching the PREREG's + the
build brief's literal requested schema "(ticker, entry_date, in_ledger,
pass_A1, pass_A2)"), but its VALUES are sourced from ledger.parquet's
`symbol` column. Never confer this with ledger.parquet's own `ticker` column.

======================================================================
in_ledger DEFINITION
======================================================================
in_ledger := (status == 'kept'). PREREG's coverage rule frames the "no ledger
row" population as "~533 of 4,936 in-archive" -- this matches EXACTLY
4936 (full ledger.parquet row count, every status) minus 4403
(status=='kept' count), confirmed live 2026-08-11:
  kept=4403, no_atm_in_dte_window=223, no_chain=222, no_forward_path=16,
  no_underlying=37, too_few_path_days=35  (sum of non-kept = 533).
Only status=='kept' rows carry BOTH a usable entry_premium AND entry_volume
(no_forward_path/too_few_path_days keep a raw entry_premium from the best
candidate tried but their entry_volume is null; the other three non-kept
statuses null out both) -- so status=='kept' is both PREREG-consistent (exact
533 match) and the only column with a well-defined premium+volume pair.

pass_A1 := in_ledger AND entry_premium >= 0.25 AND entry_volume >= 5   (PREREG arm A1)
pass_A2 := in_ledger AND entry_premium >= 0.50 AND entry_volume >= 10  (PREREG arm A2)
Both fill_null(False) defensively (Kleene AND already yields False whenever
in_ledger=False regardless of null premium/volume, verified live -- the
fill_null is belt-and-suspenders only, not load-bearing for real ledger data).

======================================================================
Output
======================================================================
experiments/floor_mc_2026_08/out/floor_exclusions.parquet
    columns: ticker (str, sourced from ledger `symbol`), entry_date (date),
             in_ledger (bool), pass_A1 (bool), pass_A2 (bool)
    One row per ledger.parquet row (4,936 rows, ALL statuses).

Any (ticker, entry_date) key ABSENT from this file entirely (e.g. a 70-74
overflow-tier call signal -- ledger_v2 was built from a SCORE_MIN=75-only
signal snapshot, experiments/flatfile_exploitation/ff_signals.py:94, so it
never covers overflow-tier signals at all; or any signal outside the
2022-08-05..2026-07-31 archive span) is NOT a row here. floorMC_run.py's A1/A2
pass-sets are built from pass_A1==True / pass_A2==True rows only, so an
absent key is automatically excluded from both (same FAIL-by-construction
effect as an explicit in_ledger=False row, matching the coverage rule's
"can't verify liquidity -> don't trade it" clause) -- floorMC_run.py's
identity arm handles "absent entirely" as a special auto-PASS case itself
(union with the live ctx keyset), never this file.

Usage:
    python experiments/floor_mc_2026_08/driver/build_exclusions.py
"""
from __future__ import annotations

import os

import polars as pl

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))          # .../floor_mc_2026_08/driver
_EXP_DIR = os.path.dirname(_THIS_DIR)                            # .../floor_mc_2026_08
_EXPERIMENTS_DIR = os.path.dirname(_EXP_DIR)
_REPO_ROOT = os.path.dirname(_EXPERIMENTS_DIR)
assert os.path.isfile(os.path.join(_REPO_ROOT, 'monte_carlo.py')), (
    f"repo root resolution failed: {_REPO_ROOT!r} (computed from __file__={__file__!r})")

LEDGER_PATH = r"B:\polygon_derived\ledger_v2\ledger.parquet"
OUT_PATH = os.path.join(_EXP_DIR, 'out', 'floor_exclusions.parquet')

A1_PREMIUM, A1_VOLUME = 0.25, 5     # PREREG section 3, arm A1
A2_PREMIUM, A2_VOLUME = 0.50, 10    # PREREG section 3, arm A2


def log(msg):
    print(msg, flush=True)


def build() -> pl.DataFrame:
    if not os.path.isfile(LEDGER_PATH):
        raise SystemExit(f"[STOP] ledger not found: {LEDGER_PATH!r} -- cannot build floor exclusions")

    df = pl.read_parquet(LEDGER_PATH)
    log(f"[load] {LEDGER_PATH}: {df.height} rows")
    log(f"[load] status distribution:\n{df.group_by('status').agg(pl.len()).sort('status')}")

    # Precondition this whole join strategy depends on: (symbol, entry_date)
    # must be unique, or a downstream dict-from-zip in floorMC_run.py would
    # silently pick one row and drop the other.
    dupe = (df.group_by(["symbol", "entry_date"]).agg(pl.len().alias("n"))
            .filter(pl.col("n") > 1))
    if dupe.height:
        raise SystemExit(f"[STOP] {dupe.height} duplicate (symbol, entry_date) rows in "
                         f"ledger.parquet -- join key is not unique, refusing to silently pick one:\n{dupe}")
    log(f"[check] (symbol, entry_date) uniqueness OK: {df.height} rows, "
        f"{df.select(['symbol', 'entry_date']).n_unique()} distinct keys")

    out = (
        df.select([
            pl.col("symbol").alias("ticker"),
            pl.col("entry_date"),
            (pl.col("status") == "kept").alias("in_ledger"),
            pl.col("entry_premium"),
            pl.col("entry_volume"),
            pl.col("status"),
        ])
        .with_columns([
            (pl.col("in_ledger")
             & (pl.col("entry_premium") >= A1_PREMIUM)
             & (pl.col("entry_volume") >= A1_VOLUME)).fill_null(False).alias("pass_A1"),
            (pl.col("in_ledger")
             & (pl.col("entry_premium") >= A2_PREMIUM)
             & (pl.col("entry_volume") >= A2_VOLUME)).fill_null(False).alias("pass_A2"),
        ])
    )

    n_total = out.height
    n_in_ledger = int(out["in_ledger"].sum())
    n_pass_a1 = int(out["pass_A1"].sum())
    n_pass_a2 = int(out["pass_A2"].sum())
    log(f"[summary] n_total={n_total}  in_ledger={n_in_ledger} ({100.0 * n_in_ledger / n_total:.2f}%)  "
        f"pass_A1={n_pass_a1} ({100.0 * n_pass_a1 / n_total:.2f}%)  "
        f"pass_A2={n_pass_a2} ({100.0 * n_pass_a2 / n_total:.2f}%)")
    log(f"[summary] entry_date range: {out['entry_date'].min()} .. {out['entry_date'].max()}")
    log("[summary] by status:")
    log(str(out.group_by("status").agg(
        pl.len().alias("n"),
        pl.col("pass_A1").sum().alias("n_pass_A1"),
        pl.col("pass_A2").sum().alias("n_pass_A2"),
    ).sort("status")))

    # sanity: pass_A2 subset of pass_A1 subset of in_ledger (thresholds nest)
    bad_nest_a1 = out.filter(pl.col("pass_A1") & ~pl.col("in_ledger")).height
    bad_nest_a2 = out.filter(pl.col("pass_A2") & ~pl.col("pass_A1")).height
    if bad_nest_a1 or bad_nest_a2:
        raise SystemExit(f"[STOP] nesting invariant violated: pass_A1-not-in_ledger={bad_nest_a1} "
                         f"pass_A2-not-pass_A1={bad_nest_a2}")
    log("[check] nesting invariant OK: pass_A2 subset pass_A1 subset in_ledger")

    final = out.select(["ticker", "entry_date", "in_ledger", "pass_A1", "pass_A2"])
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp = OUT_PATH + ".tmp"
    final.write_parquet(tmp)
    os.replace(tmp, OUT_PATH)
    log(f"[write] {OUT_PATH} ({final.height} rows, columns={final.columns})")
    return final


if __name__ == "__main__":
    build()
