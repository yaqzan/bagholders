"""Live-tail builder for the LIQUIDITY_FLOOR option-volume map.

Part of the LIQUIDITY_FLOOR ship-wiring pass (mechanism_registry.py; strategy
staged 2026-08-07, Core-evidence-only, enable gated on P2.B live-fill
confirmation -- see experiments/flatfile_exploitation/FF3_STAGEB_RESULTS.md
"Pre-enable requirements" item 3: "live map source design (live option_prices
trailing-30d feed replacing the static FF-3' parquet for current dates) is
part of that wiring"). This tool does the map-source design; it does NOT flip
LIQUIDITY_FLOOR on anywhere -- strategy_config.LIQUIDITY_FLOOR stays 0.0
regardless of what this builds or writes.

WHAT IT DOES

Merges the frozen FF-3' historical parquet
(.cache/experiment_data/liquidity_option_volume_30d_ff3.parquet, the Stage B'
locked map spec covering 2022-08-05..2026-07-31 -- FF3_AMENDMENT.md "Stage A
-- locked map spec") with a live tail resolved from the option_prices MySQL
table (READ-ONLY -- this tool never writes to MySQL) for signal dates AFTER
the archive's own max date.

Live-tail resolution reuses experiments/liquidity_cascade/build_option_volume.py's
fixed two-stage resolver (_resolve_real) VERBATIM -- same nearest-ATM DTE band
(20-45 calendar days), same "pick the expiry by total open_interest descending,
tie-break nearest-to-30-DTE, then nearest strike within it" logic, same
trailing-30-cal-day real contract-volume average. Reusing the identical
resolver (not a re-tuned DTE band) is deliberate: the merged series must not
have a methodology seam at the historical/live boundary, or a floor filter
reading across it would see a step-change in coverage that has nothing to do
with actual liquidity. (The ship brief's prose mentions "18-50 DTE" for the
live rows; the actual reusable resolver -- the thing explicitly named "the
fixed two-stage resolver" -- is hardcoded to 20-45 DTE. This tool reuses the
resolver as it exists rather than forking a second, differently-banded query,
since a live-only 18-50 band would silently diverge from the historical arm's
own 20-45 band and break comparability. Flagged prominently for review.)

Any live signal _resolve_real() can't chain (no contract that day) falls back
to the SAME proxy the historical builder uses: Stock.average_volume-style
30-trading-day rolling equity volume * FALLBACK_EQUITY_FRACTION (0.005),
tagged is_fallback=True -- "always tagged, never silent" per the original
design (experiments/liquidity_cascade/DESIGN.md).

Writes the canonical engine path: cache_path('liquidity_option_volume_30d')
== .cache/experiment_data/liquidity_option_volume_30d.parquet -- the path
monte_carlo.py / backtest_cascade.py's _liquidity_load() read by default
(when LIQUIDITY_MAP_FILE is unset, which is production's case).

FIRST WRITE ONLY PROVENANCE GUARD

The canonical path currently holds the 2026-07-14 P3.6 feature-build artifact
(pre-FF3', pre-live-tail -- built by build_option_volume.py, NOT this tool).
If a real (non-dry-run) write finds that file still there AND it predates
2026-08-01 (i.e. this tool has never actually run before), it is renamed to
liquidity_option_volume_30d_2026-07-14_evidence.parquet BEFORE the new merged
file is written -- so the original evidence artifact survives under its own
name instead of being silently clobbered. Once the canonical path is newer
than the cutoff (this tool has run at least once), subsequent runs overwrite
it in place with no further renaming -- provenance is preserved by the FIRST
rename only, not an ever-growing trail of them.

USAGE

  python tools/build_liquidity_map_live.py --selftest
      Fully offline: synthetic in-memory frames, a tempdir provenance-rename
      drill, no MySQL, no real file reads or writes.

  python tools/build_liquidity_map_live.py --dry-run
      Reads the REAL FF-3' parquet, queries `scores` (cheap, indexed) for the
      75+ CALL signals in the live-tail window, and prints what WOULD be
      resolved/written -- but skips every option_prices join (the MySQL-heavy
      part) and performs no rename/write. Safe to run directly, off-queue.

  python tools/build_liquidity_map_live.py [--end-date YYYY-MM-DD]
      Full build: resolves the live tail via option_prices (DB-heavy, same
      shape as build_option_volume.py's real-era loop) and writes the merged
      map. DO NOT run this directly -- submit via `trader queue submit`, and
      only at enable time (P2.B live-fill confirmation), not as part of this
      wiring pass.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)          # pin repo root FIRST -- worktree PYTHONPATH trap guard

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

import polars as pl                                                       # noqa: E402
from database.bulk_cache import cache_path                                # noqa: E402

CANONICAL_NAME = 'liquidity_option_volume_30d'
FF3_NAME = 'liquidity_option_volume_30d_ff3'
EVIDENCE_RENAME_NAME = 'liquidity_option_volume_30d_2026-07-14_evidence'
PROVENANCE_CUTOFF = date(2026, 8, 1)   # canonical file older than this = "never run for real yet"

_SCHEMA = {'symbol': pl.String, 'date': pl.Date, 'option_volume_30d': pl.Float64, 'is_fallback': pl.Boolean}


def _empty_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=_SCHEMA)


def load_historical_ff3() -> pl.DataFrame:
    """Read the frozen FF-3' historical parquet. Empty frame (not an error) if
    the file is somehow absent -- callers treat 'no historical rows' and 'no
    live rows' the same way (merge of whatever is present)."""
    path = cache_path(FF3_NAME)
    if not path.exists():
        print(f"  [WARN] {path} not found -- proceeding with zero historical rows")
        return _empty_frame()
    return pl.read_parquet(path)


def live_tail_window(hist_df: pl.DataFrame, end_date: date | None = None) -> tuple[date, date] | None:
    """(start, end) inclusive date range needing live resolution, or None if
    the historical archive already covers through end_date."""
    end_date = end_date or date.today()
    if hist_df.height == 0:
        return None
    archive_end = hist_df['date'].max()
    start = archive_end + timedelta(days=1)
    if start > end_date:
        return None
    return start, end_date


def load_live_call_signals(start: date, end: date):
    """75+ CALL signal (symbol, date, overall) rows for the active scoring
    version in [start, end] -- mirrors build_option_volume.py's
    _load_75plus_call_signals(), scoped to the (small) live-tail window so a
    plain indexed Score query suffices (no year-chunking needed)."""
    from database.models.core import AlgorithmVersion, Score
    from experiments.liquidity_cascade.build_option_volume import SIGNAL_MIN_SCORE
    version = AlgorithmVersion.get_active_scores_version()
    rows = list(
        Score.select(Score.symbol, Score.date, Score.overall)
             .where((Score.version == version)
                    & (Score.overall >= SIGNAL_MIN_SCORE)
                    & (Score.date >= start) & (Score.date <= end))
             .order_by(Score.date, Score.symbol)
             .tuples()
    )
    return rows, version


def resolve_live_tail(start: date, end: date) -> pl.DataFrame:
    """DB-heavy: per-signal option_prices resolution (real-era two-stage
    resolver) + bulk fallback proxy for misses. Mirrors
    build_option_volume.py._build()'s real/fallback split, scoped to the live
    window. NEVER called by --selftest or --dry-run."""
    from experiments.liquidity_cascade.build_option_volume import (
        _resolve_real, _build_fallback_map, FALLBACK_EQUITY_FRACTION, OPTION_COVERAGE_START,
    )

    rows, _version = load_live_call_signals(start, end)
    print(f"  {len(rows):,} 75+ call signal-days in live window {start}..{end}", flush=True)
    if not rows:
        return _empty_frame()

    out_sym, out_date, out_vol, out_fb = [], [], [], []
    misses = []
    for sym, d, _ov in rows:
        # Live-tail dates are always >= OPTION_COVERAGE_START (2025-02-10) by
        # construction (they're after the FF-3' archive's 2026-07-31 end) --
        # go straight to the real resolver, no date-gate needed here.
        assert d >= OPTION_COVERAGE_START, f"live-tail date {d} precedes option coverage start"
        v = _resolve_real(sym, d)
        if v is not None:
            out_sym.append(sym); out_date.append(d); out_vol.append(v); out_fb.append(False)
        else:
            misses.append((sym, d))
    n_real = len(out_sym)
    print(f"  live-tail real-resolved: {n_real:,}/{len(rows):,}", flush=True)

    if misses:
        fb_map = _build_fallback_map({s for s, _ in misses}, start_year=start.year - 1, end_year=end.year)
        fb_df = pl.DataFrame({"symbol": [s for s, _ in misses], "date": [d for _, d in misses]})
        joined = fb_df.join(fb_map, on=["symbol", "date"], how="left")
        n_fb = 0
        for sym, d, eqv in zip(joined["symbol"].to_list(), joined["date"].to_list(), joined["eq_vol30"].to_list()):
            if eqv is None:
                continue
            out_sym.append(sym); out_date.append(d)
            out_vol.append(float(eqv) * FALLBACK_EQUITY_FRACTION); out_fb.append(True)
            n_fb += 1
        print(f"  live-tail fallback-resolved: {n_fb:,}/{len(misses):,}", flush=True)

    return pl.DataFrame({"symbol": out_sym, "date": out_date,
                         "option_volume_30d": out_vol, "is_fallback": out_fb},
                        schema=_SCHEMA)


def merge_maps(hist_df: pl.DataFrame, live_df: pl.DataFrame) -> pl.DataFrame:
    """Concat + sort. Historical and live are date-disjoint by construction
    (live_tail_window only resolves dates strictly after the archive's max),
    so no dedup logic is needed -- but sort defensively by (symbol, date) for
    a deterministic on-disk row order regardless of input order."""
    hist_df = hist_df.cast(_SCHEMA) if hist_df.height else _empty_frame()
    live_df = live_df.cast(_SCHEMA) if live_df.height else _empty_frame()
    merged = pl.concat([hist_df, live_df], how='vertical')
    return merged.sort(['symbol', 'date'])


def apply_provenance_guard(canonical_path: Path, dry_run: bool = False) -> str | None:
    """FIRST WRITE ONLY: if canonical_path exists and predates PROVENANCE_CUTOFF,
    rename it to <dir>/liquidity_option_volume_30d_2026-07-14_evidence.parquet
    before the caller writes the new merged file. Returns the rename target
    path (as str) if a rename happened (or would happen, under dry_run), else
    None. dry_run=True performs no filesystem mutation."""
    if not canonical_path.exists():
        return None
    mtime = datetime.fromtimestamp(canonical_path.stat().st_mtime).date()
    if mtime >= PROVENANCE_CUTOFF:
        return None   # already ran for real at least once -- no further renaming
    target = canonical_path.parent / f"{EVIDENCE_RENAME_NAME}.parquet"
    if dry_run:
        return str(target)
    if target.exists():
        raise FileExistsError(
            f"provenance rename target already exists: {target} -- refusing to "
            f"overwrite; investigate before re-running"
        )
    canonical_path.rename(target)
    return str(target)


def build(end_date: date | None = None, dry_run: bool = False) -> int:
    """Full pipeline. dry_run: resolve + report everything except the
    MySQL-heavy per-signal option_prices joins and any filesystem write."""
    hist_df = load_historical_ff3()
    print(f"  historical (FF-3'): {hist_df.height:,} rows"
          + (f" ({hist_df['date'].min()}..{hist_df['date'].max()})" if hist_df.height else ""), flush=True)

    window = live_tail_window(hist_df, end_date)
    canonical_path = cache_path(CANONICAL_NAME)

    if window is None:
        print("  archive already covers through the target end date -- no live tail to resolve", flush=True)
        live_df = _empty_frame()
    else:
        start, end = window
        if dry_run:
            rows, version = load_live_call_signals(start, end)
            print(f"  [DRY-RUN] live window {start}..{end}: {len(rows):,} 75+ call "
                  f"signal-days pending resolution (version={version.git_commit[:8] if version.git_commit else version.id}) "
                  f"-- option_prices joins SKIPPED", flush=True)
            live_df = _empty_frame()
        else:
            live_df = resolve_live_tail(start, end)

    merged = merge_maps(hist_df, live_df)
    rename_target = apply_provenance_guard(canonical_path, dry_run=dry_run)
    if rename_target:
        verb = "would rename" if dry_run else "renamed"
        print(f"  [PROVENANCE] {verb} existing {canonical_path.name} -> {Path(rename_target).name}", flush=True)

    if dry_run:
        print(f"  [DRY-RUN] would write {merged.height:,} merged rows to {canonical_path} "
              f"(NOT written)", flush=True)
        return 0

    merged.write_parquet(canonical_path, compression='snappy')
    print(f"  wrote {merged.height:,} rows ({merged['date'].min()}..{merged['date'].max()}) "
          f"to {canonical_path}", flush=True)
    return 0


# ---------------------------------------------------------------------------
# Selftest -- fully offline, synthetic inputs only
# ---------------------------------------------------------------------------
def selftest() -> bool:
    ok = True

    def check(name, cond):
        nonlocal ok
        status = 'PASS' if cond else 'FAIL'
        if not cond:
            ok = False
        print(f"  [{status}] {name}")

    # 1. Resolver import sanity -- the reused two-stage resolver + its constants
    #    exist and match the documented values this tool's docstring cites.
    from experiments.liquidity_cascade.build_option_volume import (
        _resolve_real, OPTION_COVERAGE_START, FALLBACK_EQUITY_FRACTION, SIGNAL_MIN_SCORE,
    )
    check("_resolve_real is importable (the fixed two-stage resolver)", callable(_resolve_real))
    check("OPTION_COVERAGE_START == 2025-02-10", OPTION_COVERAGE_START == date(2025, 2, 10))
    check("FALLBACK_EQUITY_FRACTION == 0.005", FALLBACK_EQUITY_FRACTION == 0.005)
    check("SIGNAL_MIN_SCORE == 75 (75+ CALL universe)", SIGNAL_MIN_SCORE == 75)

    # 2. Historical-frame handling: missing file degrades to an empty typed frame,
    #    not an exception.
    empty = _empty_frame()
    check("empty frame has the canonical 4-column schema",
          list(empty.schema.keys()) == ['symbol', 'date', 'option_volume_30d', 'is_fallback'])

    # 3. live_tail_window: archive already-current -> None; archive behind -> a
    #    (start, end) pair strictly after the archive's max date.
    hist_current = pl.DataFrame({
        "symbol": ["AAPL"], "date": [date(2026, 8, 5)],
        "option_volume_30d": [1000.0], "is_fallback": [False],
    }, schema=_SCHEMA)
    check("live_tail_window is None when archive already covers end_date",
          live_tail_window(hist_current, end_date=date(2026, 8, 5)) is None)
    win = live_tail_window(hist_current, end_date=date(2026, 8, 8))
    check("live_tail_window starts the day AFTER the archive max (no overlap)",
          win == (date(2026, 8, 6), date(2026, 8, 8)))
    check("live_tail_window on an empty historical frame is None (nothing to anchor to)",
          live_tail_window(_empty_frame(), end_date=date(2026, 8, 8)) is None)

    # 4. merge_maps: disjoint historical + live frames concat cleanly, sorted,
    #    row count is additive (no silent drops/dupes).
    live_synth = pl.DataFrame({
        "symbol": ["MSFT", "AAPL"], "date": [date(2026, 8, 6), date(2026, 8, 7)],
        "option_volume_30d": [500.0, 42.0], "is_fallback": [False, True],
    }, schema=_SCHEMA)
    merged = merge_maps(hist_current, live_synth)
    check("merge_maps row count is additive (1 hist + 2 live = 3)", merged.height == 3)
    check("merge_maps output is sorted by (symbol, date)",
          merged["symbol"].to_list() == sorted(merged["symbol"].to_list()) or
          merged.sort(['symbol', 'date']).equals(merged))
    check("merge_maps preserves the is_fallback tag through the concat",
          merged.filter(pl.col("symbol") == "AAPL").filter(pl.col("date") == date(2026, 8, 7))["is_fallback"][0] == True)

    # 5. FIRST WRITE ONLY provenance guard -- isolated tempdir, never touches the
    #    real .cache/experiment_data/ canonical file.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fake_canonical = Path(td) / f"{CANONICAL_NAME}.parquet"
        _empty_frame().write_parquet(fake_canonical)
        old_ts = datetime(2026, 7, 14, 6, 21, 58).timestamp()
        os.utime(fake_canonical, (old_ts, old_ts))
        target = apply_provenance_guard(fake_canonical, dry_run=True)
        check("dry_run provenance guard reports the rename WITHOUT touching the file",
              target is not None and fake_canonical.exists())
        check("dry_run provenance guard names the exact evidence-preservation target",
              target is not None and Path(target).name == f"{EVIDENCE_RENAME_NAME}.parquet")

        target2 = apply_provenance_guard(fake_canonical, dry_run=False)
        check("real provenance guard actually renames an old (pre-cutoff) file",
              target2 is not None and not fake_canonical.exists() and Path(target2).exists())

        # Second run: canonical path now absent (renamed away) -> guard is a no-op.
        check("provenance guard no-ops once the old file is gone",
              apply_provenance_guard(fake_canonical, dry_run=False) is None)

        # A FRESH canonical file (post-cutoff mtime) must NOT be renamed on a
        # later run -- idempotent-safe repeat builds.
        fresh_canonical = Path(td) / f"{CANONICAL_NAME}_fresh.parquet"
        _empty_frame().write_parquet(fresh_canonical)
        new_ts = datetime(2026, 8, 7, 12, 0, 0).timestamp()
        os.utime(fresh_canonical, (new_ts, new_ts))
        check("provenance guard leaves a post-cutoff (already-live) file alone",
              apply_provenance_guard(fresh_canonical, dry_run=False) is None
              and fresh_canonical.exists())

    print(f"\nSELFTEST {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--selftest', action='store_true', help='offline synthetic checks, no MySQL/writes')
    ap.add_argument('--dry-run', action='store_true', help='real reads, no option_prices joins, no writes')
    ap.add_argument('--end-date', type=str, default=None, help='YYYY-MM-DD live-tail end (default: today)')
    args = ap.parse_args()

    if args.selftest:
        ok = selftest()
        sys.exit(0 if ok else 1)

    end_date = date.fromisoformat(args.end_date) if args.end_date else None
    if not args.dry_run:
        print("=" * 78)
        print("FULL BUILD -- DB-heavy (option_prices per-signal joins).")
        print("This should be running via `trader queue submit`, not a raw foreground")
        print("invocation, and only at P2.B enable time -- not as part of the wiring pass.")
        print("=" * 78)
    sys.exit(build(end_date=end_date, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
