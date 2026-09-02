"""Decisive look-ahead test (apples-to-apples, full 753-symbol ledger).

The v60 state-3 ledger (.cache/rqc_v60/ledger_5y.parquet) is a LOOK-AHEAD scorer:
for an early-week (Mon) signal it reads the COMPLETE Mon-Fri WeeklyScore, i.e. the
rest-of-week price action that determines the outcome. This shows as a Monday
optTP15 spike that decays to Friday (where the 'complete week' ~= the real partial
week, so minimal look-ahead).

This test re-scores the EXACT (symbol,date) of the state-3 75+ cohort with the
NEW BLEND via the single-row production path (Score.calculate_overall_score, which
now applies the weekly-transition blend), and compares optTP15-by-day-of-week on
the IDENTICAL universe:

  * state-3 optTP15 by dow (from ledger)  — the look-ahead curve
  * blend score's tier + optTP15 by dow   — should be FLAT (look-ahead removed)

opt15 is score-INVARIANT per (sym,date), so the outcome is the same; the test is
whether the BLEND keeps Monday entries honest (de-inflates the Monday spike via
the t=0.2 lean on the stale-but-honest completed week).

Run: PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python experiments/weekly_pit/lookahead_paired.py [--min 75]
"""
import os, sys, json, time
from collections import defaultdict
from datetime import date

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import polars as pl

LEDGER = os.path.join(ROOT, ".cache", "rqc_v60", "ledger_5y.parquet")
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def main():
    mn = 75
    if "--min" in sys.argv:
        mn = int(sys.argv[sys.argv.index("--min") + 1])

    led = pl.read_parquet(LEDGER).with_columns(pl.col("date").str.to_date().alias("d"))
    coh = led.filter(pl.col("overall") >= mn)
    print(f"state-3 {mn}+ cohort: {coh.height} rows, "
          f"{coh['symbol'].n_unique()} symbols", flush=True)

    from database.models.core import Stock, Score, AlgorithmVersion
    import assess_scores as A
    from database.barrier_cache import BARRIER_SETS
    from collections import namedtuple
    OPT = BARRIER_SETS["30dte_opt"]
    Peak = namedtuple("Peak", ["symbol_id", "date", "overall"])

    # Re-score each cohort (sym,date) with the blend by loading the EXISTING v60
    # Score row (components already populated) and calling calculate_overall_score
    # — which now applies the weekly-transition blend via the single-row prod path.
    t0 = time.time()
    rescored = {}    # (sym, date_iso) -> blended overall
    coh_keys = [(r["symbol"], r["d"]) for r in coh.iter_rows(named=True)]
    by_sym = defaultdict(list)
    for sym, d in coh_keys:
        by_sym[sym].append(d)
    n_done = 0
    n_sym = 0
    for sym, dates in by_sym.items():
        rows = list(
            Score.select()
            .where((Score.symbol == sym) & (Score.version_id == 60)
                   & (Score.date.in_(dates)))
        )
        for sc in rows:
            if None in (sc.trend, sc.bb, sc.rsi, sc.macd, sc.stoch,
                        sc.technical_alignment):
                continue
            try:
                ov = sc.calculate_overall_score()
            except Exception:
                ov = None
            if ov is not None:
                rescored[(sym, sc.date.isoformat())] = int(ov)
                n_done += 1
        n_sym += 1
        if n_sym % 75 == 0:
            print(f"  rescored {n_sym}/{len(by_sym)} symbols, {n_done} dates "
                  f"({time.time()-t0:.0f}s)", flush=True)
    print(f"rescored {n_done} cohort dates in {time.time()-t0:.0f}s", flush=True)

    # opt15 (score-invariant) for the cohort via option barrier walk.
    peaks = [Peak(r["symbol"], r["d"], int(r["overall"])) for r in coh.iter_rows(named=True)]
    A.SWING_K_LOW, A.SWING_M_LOW = OPT["k_low"], OPT["m_low"]
    A.SWING_K_HIGH, A.SWING_M_HIGH = OPT["k_high"], OPT["m_high"]
    try:
        res = A._forward_walk_subset(peaks)
    finally:
        A.SWING_K_LOW, A.SWING_M_LOW = 2.0, 5.0
        A.SWING_K_HIGH, A.SWING_M_HIGH = 1.0, 2.0
    opt15 = {}
    for r in res:
        s = r.get("swing", {}).get("15d")
        if s and s.get("result") is not None:
            opt15[(r["symbol"], r["date"].isoformat()
                   if hasattr(r["date"], "isoformat") else str(r["date"]))] = \
                1 if s["result"] == "win" else 0

    # Build paired table.
    rows = []
    for r in coh.iter_rows(named=True):
        key = (r["symbol"], r["d"].isoformat())
        o15 = opt15.get(key)
        if o15 is None:
            continue
        rows.append({
            "dow": r["d"].weekday(),
            "state3": int(r["overall"]),
            "blend": rescored.get(key),        # None if rescore failed
            "opt15": o15,
        })
    pdf = pl.DataFrame(rows)

    def _dow(df, mask_col_lo, label, score_col):
        print(f"\n  {label} (cohort defined by state-3 {mn}+; score={score_col})")
        cells = []
        tps = []
        for wd in range(5):
            s = df.filter(pl.col("dow") == wd)
            n = s.height
            k = int(s["opt15"].sum()) if n else 0
            tp = k / n * 100 if n else float("nan")
            cells.append(f"{DOW[wd]}:{tp:5.1f}%(n={n})")
            if n > 0:
                tps.append(tp)
        spread = max(tps) - min(tps) if tps else float("nan")
        mon = df.filter(pl.col("dow") == 0)
        fri = df.filter(pl.col("dow") == 4)
        mtp = int(mon["opt15"].sum())/mon.height*100 if mon.height else 0
        ftp = int(fri["opt15"].sum())/fri.height*100 if fri.height else 0
        print("   " + "  ".join(cells))
        print(f"   spread={spread:.1f}pp  Mon-Fri={mtp-ftp:+.1f}pp")
        return spread, mtp - ftp

    print("\n" + "=" * 78)
    print(f"LOOK-AHEAD ISOLATION on the IDENTICAL state-3 {mn}+ cohort")
    print("  (a) state-3 optTP15 by dow = the look-ahead curve (Mon spike).")
    print("  (b) same dates, but KEPT only where the BLEND also scores >=%d," % mn)
    print("      i.e. the blend's honest cohort — Monday look-ahead entries the")
    print("      blend rejects drop out, flattening the curve.")
    print("=" * 78)

    # (a) full state-3 cohort, opt15 by dow
    s3_spread, s3_mf = _dow(pdf, "state3", "(a) STATE-3 look-ahead cohort", "state3")
    # (b) restrict to blend>=mn (blend's honest retained cohort)
    keep = pdf.filter(pl.col("blend").is_not_null() & (pl.col("blend") >= mn))
    b_spread, b_mf = _dow(keep, "blend", "(b) BLEND-honest retained cohort", "blend")

    # retention by dow
    print("\n  blend retention of state-3 cohort by dow (look-ahead inflates EARLY "
          "week => fewer kept on Mon/Tue):")
    for wd in range(5):
        sub = pdf.filter((pl.col("dow") == wd) & pl.col("blend").is_not_null())
        n = sub.height
        kept = sub.filter(pl.col("blend") >= mn).height
        print(f"   {DOW[wd]}: {kept}/{n} = {(kept/n*100 if n else float('nan')):.0f}% kept")

    print("\n" + "=" * 78)
    print("VERDICT")
    print(f"  state-3 Mon-Fri spread = {s3_mf:+.1f}pp (look-ahead Monday inflation)")
    print(f"  blend   Mon-Fri spread = {b_mf:+.1f}pp (should be ~0 / no Mon inflation)")
    print("=" * 78)


if __name__ == "__main__":
    main()
