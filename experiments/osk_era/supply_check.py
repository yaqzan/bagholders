"""Phase C OSK era-layer -- supply reality check (the growth-gate trap killer).

From rs_ledger.parquet + MAIN's gex_build_report.json coverage_map -- NOT re-querying
MySQL (this script is intentionally light/fast; the heavy per-row chain truth comes
from build_osk_ledger.py's queued job, which hits MySQL directly) -- computes what
fraction of 70+ and 75+ signal-ROWS in 2025-02-01..2026-06-15 are "option-covered",
per score bucket (70-74 / 75-79 / 80+) and per quarter. This is the REAL supply number
Stage-1 W-gates must use -- no FALLBACK_COVERAGE-style rosy defaults
(reference_growth_gate_supply_fallback.md).

Coverage definition (literal task spec: "chain exists that day per the coverage
map"): a signal (symbol, date) is SPAN-COVERED iff symbol appears in
gex_build_report.json's coverage_map AND min_date <= date <= max_date for that symbol.

HONESTY CAVEAT (why this script reports a SECOND, density-adjusted number): the
coverage_map's per-symbol record is (min_date, max_date, n_dates) -- n_dates counts
days with SOME option data in that span; it is not a full per-date list. Across the
723 probed symbols, median span is ~441 calendar days but median n_dates is only 239
-- roughly half the days in a "covered" span actually have a chain. Simple
span-membership therefore OVERSTATES true daily coverage. This script reports both:
  (a) SPAN coverage    -- the literal spec, an optimistic upper bound.
  (b) DENSITY-ADJUSTED  -- span-covered rows scaled by that symbol's own
                           n_dates/(span_days+1) ratio -- a more realistic estimate
                           assuming roughly uniform density within the span.
Neither is verified ground truth. build_osk_ledger.py's queued job performs the real
per-(symbol,date) MySQL chain query (ATM call, DTE 20-45, OI>0, iv sane) for every
signal row -- ITS kept-vs-requested rate is the actual ground truth; cross-check this
script's two estimates against that once the queued job completes.

WORKTREE DISCIPLINE: runs only from Trader-exp-osk-era; reads MAIN's rs_ledger.parquet
and gex_build_report.json by absolute path (read-only); writes to this worktree's
.cache/osk_era/ only. No MySQL connection is opened by this script at all.

Usage (light -- runs directly in the foreground, no queue needed):
    python experiments/osk_era/supply_check.py
"""
import json
import os
import sys
from datetime import date as _date

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_WORKTREE_MARKER = "Trader-exp-osk-era"
assert _WORKTREE_MARKER in _ROOT, (
    f"supply_check.py must run from the {_WORKTREE_MARKER} worktree root, resolved _ROOT={_ROOT}"
)
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import polars as pl  # noqa: E402

import experiments._holdout as _holdout_mod  # noqa: E402
assert os.path.abspath(_holdout_mod.__file__).lower().startswith(_ROOT.lower()), (
    f"experiments._holdout resolved OUTSIDE the worktree root ({_ROOT}): {_holdout_mod.__file__}"
)
from experiments._holdout import assert_no_holdout_leak  # noqa: E402

MAIN_ROOT = r"C:\Development\Trader"
LED = os.path.join(MAIN_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")
GEX_REPORT = os.path.join(MAIN_ROOT, ".cache", "experiment_data", "gex_build_report.json")

WIN_START = "2025-02-01"
WIN_END = "2026-06-15"   # == CALIBRATION_CUTOFF_DATE

OUT_DIR = os.path.join(_ROOT, ".cache", "osk_era")


def score_bucket(overall: int) -> str:
    if overall >= 80:
        return "80+"
    if overall >= 75:
        return "75-79"
    if overall >= 70:
        return "70-74"
    return "<70"


def quarter_of(d: str) -> str:
    y, m = int(d[0:4]), int(d[5:7])
    q = (m - 1) // 3 + 1
    return f"{y}-Q{q}"


def load_coverage_map() -> dict:
    with open(GEX_REPORT, "r", encoding="utf-8") as f:
        report = json.load(f)
    cov = {}
    for rec in report.get("coverage_map", []):
        sym = rec["symbol"]
        mn = _date.fromisoformat(rec["min_date"])
        mx = _date.fromisoformat(rec["max_date"])
        n_dates = rec.get("n_dates", 0)
        span_days = max((mx - mn).days, 0) + 1
        density = n_dates / span_days if span_days > 0 else 0.0
        cov[sym] = {"min_date": mn, "max_date": mx, "n_dates": n_dates,
                    "span_days": span_days, "density": min(density, 1.0)}
    return cov, report


def span_covered(cov: dict, sym: str, d: _date) -> tuple:
    """Returns (span_hit: bool, density: float). density=0.0 if symbol absent."""
    rec = cov.get(sym)
    if rec is None:
        return False, 0.0
    hit = rec["min_date"] <= d <= rec["max_date"]
    return hit, (rec["density"] if hit else 0.0)


def summarize(df: pl.DataFrame, group_col: str) -> list:
    """Per-group N/covered/pct (span) + density-weighted-expected pct."""
    out = []
    for key, sub in df.group_by(group_col, maintain_order=True):
        key = key[0] if isinstance(key, tuple) else key
        n = sub.height
        n_span = int(sub["span_hit"].sum())
        exp_density_n = float(sub["density"].sum())  # sum of per-row hit probabilities
        out.append({
            group_col: key,
            "n_total": n,
            "n_span_covered": n_span,
            "span_coverage_pct": round(100.0 * n_span / n, 1) if n else 0.0,
            "density_adjusted_expected_pct": round(100.0 * exp_density_n / n, 1) if n else 0.0,
        })
    return out


def main() -> None:
    led = pl.read_parquet(LED)
    win = led.filter(
        (pl.col("date") >= WIN_START) & (pl.col("date") <= WIN_END) & (pl.col("overall") >= 70)
    )
    assert_no_holdout_leak(win, context="osk_era supply_check")

    cov, gex_report = load_coverage_map()

    syms = win["symbol"].to_list()
    dates = win["date"].to_list()
    overalls = win["overall"].to_list()

    span_hits, densities, buckets, quarters = [], [], [], []
    for sym, d, ov in zip(syms, dates, overalls):
        dd = _date.fromisoformat(d)
        hit, dens = span_covered(cov, sym, dd)
        span_hits.append(hit)
        densities.append(dens)
        buckets.append(score_bucket(ov))
        quarters.append(quarter_of(d))

    df = win.with_columns([
        pl.Series("span_hit", span_hits),
        pl.Series("density", densities),
        pl.Series("bucket", buckets),
        pl.Series("quarter", quarters),
    ])

    by_bucket = summarize(df, "bucket")
    # overall 70+ / 75+ aggregate rows
    df_75plus = df.filter(pl.col("overall") >= 75)
    n70 = df.height
    n70_span = int(df["span_hit"].sum())
    n70_dens = float(df["density"].sum())
    n75 = df_75plus.height
    n75_span = int(df_75plus["span_hit"].sum())
    n75_dens = float(df_75plus["density"].sum())
    aggregates = [
        {"cohort": "ALL 70+", "n_total": n70, "n_span_covered": n70_span,
         "span_coverage_pct": round(100.0 * n70_span / n70, 1) if n70 else 0.0,
         "density_adjusted_expected_pct": round(100.0 * n70_dens / n70, 1) if n70 else 0.0},
        {"cohort": "ALL 75+", "n_total": n75, "n_span_covered": n75_span,
         "span_coverage_pct": round(100.0 * n75_span / n75, 1) if n75 else 0.0,
         "density_adjusted_expected_pct": round(100.0 * n75_dens / n75, 1) if n75 else 0.0},
    ]

    by_quarter = summarize(df, "quarter")
    by_quarter_75 = summarize(df_75plus, "quarter")

    # --- Ground-truth cross-check (reads a CACHED parquet only -- still no MySQL
    # here). If build_osk_ledger.py's queued job has finished, its "kept" rows are
    # the REAL per-(symbol,date) MySQL-verified chain hits (ATM 20-45DTE call found).
    # Joining that back onto this window's full universe gives the actual hit-rate
    # to compare against the two coverage_map-only estimates above.
    ledger_path = os.path.join(OUT_DIR, "osk_era_ledger.parquet")
    ground_truth = None
    if os.path.exists(ledger_path):
        led_built = pl.read_parquet(ledger_path).select(
            ["symbol", "date", "skew", "pnl15"]
        ).rename({"skew": "_gt_skew", "pnl15": "_gt_pnl15"})
        joined = df.join(led_built, on=["symbol", "date"], how="left")
        joined = joined.with_columns([
            pl.col("_gt_skew").is_not_null().alias("gt_chain_hit"),
            pl.col("_gt_pnl15").is_not_null().alias("gt_ripe"),
        ])

        def gt_summarize(sub: pl.DataFrame) -> dict:
            n = sub.height
            hit = int(sub["gt_chain_hit"].sum())
            ripe = int(sub["gt_ripe"].sum())
            return {
                "n_total": n, "n_chain_hit": hit,
                "chain_hit_pct": round(100.0 * hit / n, 1) if n else 0.0,
                "n_ripe_pnl15": ripe,
                "ripe_pct_of_total": round(100.0 * ripe / n, 1) if n else 0.0,
            }

        gt_by_bucket = []
        for key, sub in joined.group_by("bucket", maintain_order=True):
            key = key[0] if isinstance(key, tuple) else key
            gt_by_bucket.append({"bucket": key, **gt_summarize(sub)})
        gt_aggregates = {
            "ALL 70+": gt_summarize(joined),
            "ALL 75+": gt_summarize(joined.filter(pl.col("overall") >= 75)),
        }
        ground_truth = {
            "source": ledger_path,
            "note": (
                "REAL per-(symbol,date) MySQL chain query result (build_osk_ledger.py "
                "task, queued separately) -- this is the actual ground truth, not an "
                "estimate. chain_hit_pct is directly comparable to span_coverage_pct "
                "and density_adjusted_expected_pct above; ripe_pct_of_total additionally "
                "requires >=15 real trading-day forward price rows (see build_osk_ledger.py "
                "docstring -- a stricter bar than mere chain existence)."
            ),
            "aggregates_70_75": gt_aggregates,
            "by_bucket": gt_by_bucket,
        }
        print("\n*** GROUND TRUTH available (build_osk_ledger.py already completed) ***")
        for coh, r in gt_aggregates.items():
            print(f"  {coh:8s}  N={r['n_total']:5d}  chain_hit={r['n_chain_hit']:5d} "
                  f"({r['chain_hit_pct']:5.1f}%)  ripe(pnl15)={r['n_ripe_pnl15']:5d} "
                  f"({r['ripe_pct_of_total']:5.1f}% of total)")

    # bucket x quarter cross-tab (70+ only, all three discrete buckets)
    cross = []
    for (q, b), sub in df.group_by(["quarter", "bucket"], maintain_order=True):
        n = sub.height
        n_span = int(sub["span_hit"].sum())
        cross.append({
            "quarter": q, "bucket": b, "n_total": n, "n_span_covered": n_span,
            "span_coverage_pct": round(100.0 * n_span / n, 1) if n else 0.0,
        })

    result = {
        "window_start": WIN_START,
        "window_end": WIN_END,
        "coverage_map_source": GEX_REPORT,
        "coverage_map_n_symbols": len(cov),
        "coverage_map_probe_summary": gex_report.get("coverage_summary"),
        "aggregates_70_75": aggregates,
        "by_bucket_70plus_window": by_bucket,
        "by_quarter_70plus": by_quarter,
        "by_quarter_75plus": by_quarter_75,
        "bucket_x_quarter_70plus": cross,
        "ground_truth_crosscheck": ground_truth,
        "caveat": (
            "span_coverage_pct = literal task spec (date within symbol's tracked "
            "min/max chain span) -- an OPTIMISTIC upper bound. "
            "density_adjusted_expected_pct = span_coverage_pct's rows re-weighted by "
            "each symbol's own (n_dates/span_days) density -- a more realistic estimate. "
            "NEITHER is verified per-day ground truth; cross-check against "
            "build_osk_ledger.py's queued real-MySQL kept-rate once it completes."
        ),
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "supply_check.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    print("=== OSK era-layer supply reality check ===")
    print(f"window: {WIN_START}..{WIN_END}  coverage_map symbols: {len(cov)}")
    print()
    print("AGGREGATES (70+ / 75+):")
    for r in aggregates:
        print(f"  {r['cohort']:8s}  N={r['n_total']:5d}  span_covered={r['n_span_covered']:5d} "
              f"({r['span_coverage_pct']:5.1f}%)  density_adj~{r['density_adjusted_expected_pct']:5.1f}%")
    print()
    print("BY SCORE BUCKET (70+ window):")
    for r in by_bucket:
        print(f"  {r['bucket']:8s}  N={r['n_total']:5d}  span_covered={r['n_span_covered']:5d} "
              f"({r['span_coverage_pct']:5.1f}%)  density_adj~{r['density_adjusted_expected_pct']:5.1f}%")
    print()
    print("BY QUARTER (70+ window):")
    for r in by_quarter:
        print(f"  {r['quarter']:8s}  N={r['n_total']:5d}  span_covered={r['n_span_covered']:5d} "
              f"({r['span_coverage_pct']:5.1f}%)  density_adj~{r['density_adjusted_expected_pct']:5.1f}%")
    print()
    print("BY QUARTER (75+ window):")
    for r in by_quarter_75:
        print(f"  {r['quarter']:8s}  N={r['n_total']:5d}  span_covered={r['n_span_covered']:5d} "
              f"({r['span_coverage_pct']:5.1f}%)")
    print()
    print("DONE ->", out_path)


if __name__ == "__main__":
    main()
