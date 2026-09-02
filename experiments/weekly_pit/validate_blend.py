"""Validate the weekly-transition blend wired into the production scoring path.

No DB writes.  Re-scores a sample (>=150 symbols, 5y) with ScoreSimulator (which
now applies the blend), computes the score-INVARIANT 30dte_opt option-TP outcome
(opt15) per signal date via the assess barrier walk, and contrasts against the
stored v60 state-3 look-ahead ledger (.cache/rqc_v60/ledger_5y.parquet).

Five reports:
  1. LOOK-AHEAD removed (PRIMARY): blended 75-79 cohort optTP15 by day-of-week
     must be ~FLAT (no Monday spike).  Contrast vs v60 state-3 (Mon >> Fri).
  2. HONEST-WR-clears-BE: blended optTP15 by tier (75-79/80-84/85-89) vs ~45% BE,
     and vs v60 state-3.
  3. STABILITY / fakeout fix: Fri-of-W vs Mon-of-W+1 mean |Δoverall| for the same
     symbols, blend vs v60.
  4. BLEND behavior sanity: weekly_transition_t ~0.2 Mon / 1.0 Fri; wadj_partial /
     wadj_completed populate.
  5. BLAST radius: mean |Δoverall| vs v60 and 75+ count change.

Run: PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python experiments/weekly_pit/validate_blend.py [--syms N]
"""
import os, sys, json, time
from collections import defaultdict
from datetime import date, timedelta

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import polars as pl
import numpy as np

LEDGER = os.path.join(ROOT, ".cache", "rqc_v60", "ledger_5y.parquet")
CUTOFF = "2026-05-15"            # CALIBRATION_CUTOFF_DATE
START = "2021-05-15"            # 5y
LOOKBACK_DAYS = 1825

DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def _wilson_lo(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n
    return (p + z*z/(2*n) - z*((p*(1-p)+z*z/(4*n))/n) ** 0.5) / (1 + z*z/n)


def main():
    n_syms = 200
    if "--syms" in sys.argv:
        n_syms = int(sys.argv[sys.argv.index("--syms") + 1])

    t0 = time.time()
    led = pl.read_parquet(LEDGER)
    led_syms = led["symbol"].unique().sort().to_list()
    # Sample symbols from the ledger universe (ensures opt15 overlap exists).
    syms = led_syms[:n_syms]
    print(f"[validate] {len(syms)} symbols (from {len(led_syms)} ledger symbols), "
          f"5y window {START}..{CUTOFF}", flush=True)

    # ── 1. Re-score with the blend (ScoreSimulator) ────────────────────────
    from simulator import ScoreSimulator
    sim = ScoreSimulator(symbols=syms, lookback_days=LOOKBACK_DAYS)
    sim.capture_weight_info = True
    t1 = time.time()
    blended = sim.simulate(since=date.fromisoformat(START))   # {(sym,date)->overall}
    wi_by_key = sim.weight_info_by_key
    print(f"[validate] blended {len(blended)} (sym,date) scores in {time.time()-t1:.0f}s "
          f"(load+score)", flush=True)

    # Filter to holdout-locked window and tradable call range for the cohort tests.
    cutoff_d = date.fromisoformat(CUTOFF)
    start_d = date.fromisoformat(START)

    # ── 2. Compute score-INVARIANT opt15 (30dte_opt) for the blended call
    #       universe via the assess barrier walk. opt15 depends ONLY on price +
    #       signal date, so it is the honest option-TP outcome for whatever dates
    #       the BLEND qualifies (incl. blend-promoted dates absent from the v60
    #       state-3 ledger). ────────────────────────────────────────────────
    import assess_scores as A
    from database.barrier_cache import BARRIER_SETS
    from collections import namedtuple
    OPT = BARRIER_SETS["30dte_opt"]
    Peak = namedtuple("Peak", ["symbol_id", "date", "overall"])

    # Build call peaks for the blended-qualifying universe (overall>=70, calls).
    blend_call_peaks = [
        Peak(sym, d, ov)
        for (sym, d), ov in blended.items()
        if ov is not None and ov >= 70 and start_d <= d <= cutoff_d
    ]
    print(f"[validate] blended call peaks (>=70, in window): {len(blend_call_peaks)}", flush=True)

    # Walk option barriers for the blended universe.
    A.SWING_K_LOW, A.SWING_M_LOW = OPT["k_low"], OPT["m_low"]
    A.SWING_K_HIGH, A.SWING_M_HIGH = OPT["k_high"], OPT["m_high"]
    try:
        t2 = time.time()
        res = A._forward_walk_subset(blend_call_peaks)
        print(f"[validate] option barrier walk (blended): {len(res)} in {time.time()-t2:.0f}s",
              flush=True)
    finally:
        A.SWING_K_LOW, A.SWING_M_LOW = 2.0, 5.0
        A.SWING_K_HIGH, A.SWING_M_HIGH = 1.0, 2.0

    def _opt15(r):
        s = r.get("swing", {}).get("15d")
        if s is None or s.get("result") is None:
            return None
        return 1 if s["result"] == "win" else 0

    # blended rows: (sym, date, overall, dow, opt15)
    brows = []
    for r in res:
        o15 = _opt15(r)
        if o15 is None:
            continue
        d = r["date"]
        brows.append((r["symbol"], d, int(r["score"]), d.weekday(), o15))
    bdf = pl.DataFrame(brows, schema=["symbol", "date", "overall", "dow", "opt15"],
                       orient="row")
    # persist for fast offline re-analysis (no re-score).
    _bpath = os.path.join(ROOT, ".cache", "weekly_pit", "blended_optwalk.parquet")
    bdf.with_columns(pl.col("date").cast(pl.Utf8)).write_parquet(_bpath)
    print(f"[validate] wrote {_bpath}", flush=True)

    # v60 ledger restricted to the same symbol sample + window (state-3, look-ahead).
    vdf = led.filter(pl.col("symbol").is_in(syms)).with_columns(
        pl.col("date").str.to_date().alias("d"))
    vdf = vdf.filter((pl.col("d") >= start_d) & (pl.col("d") <= cutoff_d))
    vdf = vdf.with_columns(pl.col("d").dt.weekday().alias("dow1"))  # polars: Mon=1
    vdf = vdf.with_columns((pl.col("dow1") - 1).alias("dow"))       # -> Mon=0

    print("\n" + "=" * 78)
    print("REPORT 1 — LOOK-AHEAD REMOVED (PRIMARY): optTP15 by day-of-week")
    print("  Honest scorer => FLAT across dow. Look-ahead (v60 state-3) => Mon spike.")
    print("=" * 78)

    def _dow_table(df, score_col, opt_col, lo, hi, label):
        sub = df.filter((pl.col(score_col) >= lo) & (pl.col(score_col) <= hi))
        rows = []
        for wd in range(5):
            s = sub.filter(pl.col("dow") == wd)
            n = s.height
            k = int(s[opt_col].sum()) if n else 0
            tp = (k / n * 100) if n else float("nan")
            rows.append((wd, n, tp))
        ns = [r[1] for r in rows]
        tps = [r[2] for r in rows if r[1] > 0]
        spread = (max(tps) - min(tps)) if tps else float("nan")
        mon = rows[0][2]
        fri = rows[4][2]
        print(f"\n  {label}  (N={sub.height})")
        print("   " + "  ".join(f"{DOW_NAMES[r[0]]}:{r[2]:5.1f}%(n={r[1]})" for r in rows))
        print(f"   max-min spread = {spread:.1f}pp | Mon-Fri = {mon-fri:+.1f}pp")
        return spread, mon - fri

    print("\n  --- 75-79 cohort (each scorer's OWN cohort) ---")
    b_spread, b_mf = _dow_table(bdf, "overall", "opt15", 75, 79, "BLENDED (honest, state-1+2)")
    v_spread, v_mf = _dow_table(vdf, "overall", "opt15", 75, 79, "v60 STATE-3 (look-ahead)")

    print("\n  --- 75+ cohort (each scorer's OWN cohort; larger N) ---")
    b75_spread, b75_mf = _dow_table(bdf, "overall", "opt15", 75, 200, "BLENDED 75+ (honest)")
    v75_spread, v75_mf = _dow_table(vdf, "overall", "opt15", 75, 200, "v60 STATE-3 75+ (look-ahead)")

    # APPLES-TO-APPLES: same (sym,date) universe = the v60 state-3 75+ cohort.
    # Compare state-3's optTP15-by-dow (from ledger) vs the SAME dates' optTP15
    # under the blend's opt15 (score-invariant per date, so identical outcome) —
    # the informative contrast is whether the blend KEEPS these as >=75 (cohort
    # retention) and how the EARLY-WEEK inflation differs. Here we report the
    # blend's qualification rate by dow on the state-3 75+ cohort: if look-ahead
    # inflated early-week entries, the blend should DROP more of them on Mon/Tue.
    led75 = vdf.filter((pl.col("overall") >= 75) & (pl.col("overall") <= 200)) \
               .select(["symbol", "d", "dow"]).rename({"d": "date"})
    bkeys = {(r["symbol"], r["date"]): r["overall"] for r in
             bdf.with_columns(pl.col("date").cast(pl.Date)).iter_rows(named=True)} \
        if bdf.height else {}
    # build retention + opt15 by dow over the state-3 75+ cohort
    print("\n  --- look-ahead isolation: on the v60 state-3 75+ cohort, what the "
          "blend does by dow ---")
    rows = []
    for wd in range(5):
        sub = led75.filter(pl.col("dow") == wd)
        n = sub.height
        kept = 0
        for r in sub.iter_rows(named=True):
            bo = bkeys.get((r["symbol"], r["date"]))
            if bo is not None and bo >= 75:
                kept += 1
        rows.append((wd, n, kept))
    print("   state-3 75+ cohort, blend-retained-as-75+ by dow:")
    print("   " + "  ".join(
        f"{DOW_NAMES[r[0]]}:{(r[2]/r[1]*100 if r[1] else float('nan')):4.0f}%kept(n={r[1]})"
        for r in rows))
    print("   (look-ahead inflates EARLY-week entries; honest blend should retain "
          "FEWER on Mon/Tue than Fri)")

    print("\n" + "=" * 78)
    print("REPORT 2 — HONEST WR CLEARS BREAK-EVEN: optTP15 by tier")
    print("  Call BE ~45.0% (base TP=33/SL=27). Honest numbers expected LOWER than")
    print("  v60 state-3 (which is inflated by look-ahead).")
    print("=" * 78)
    print(f"\n  {'tier':<8}{'BLENDED N':>11}{'BLENDED TP%':>13}{'wilsonLo':>10}"
          f"{'  ':>2}{'v60 N':>8}{'v60 TP%':>9}")
    for lo, hi, nm in [(75, 79, "75-79"), (80, 84, "80-84"), (85, 89, "85-89"),
                       (75, 200, "75+"), (70, 74, "70-74")]:
        bs = bdf.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        bn, bk = bs.height, (int(bs["opt15"].sum()) if bs.height else 0)
        btp = (bk / bn * 100) if bn else float("nan")
        blo = _wilson_lo(bk, bn) * 100 if bn else float("nan")
        vs = vdf.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        vn, vk = vs.height, (int(vs["opt15"].sum()) if vs.height else 0)
        vtp = (vk / vn * 100) if vn else float("nan")
        flag = "  <-- BELOW BE" if (bn and btp < 45.0 and lo >= 75) else ""
        print(f"  {nm:<8}{bn:>11}{btp:>12.1f}%{blo:>9.1f}%{'  ':>2}{vn:>8}{vtp:>8.1f}%{flag}")

    print("\n" + "=" * 78)
    print("REPORT 3 — STABILITY / FAKEOUT FIX: Fri-of-W vs Mon-of-W+1 |Δoverall|")
    print("  Blend ramps the weekly cliff over the week => smaller Mon-boundary jump.")
    print("=" * 78)

    def _mon_boundary_jump(score_map):
        """score_map: {(sym,date)->overall}. For each symbol, pair the last
        trading day of week W with the first trading day of week W+1 and measure
        |Δoverall|. Returns (n_pairs, mean|Δ|, p90|Δ|)."""
        by_sym = defaultdict(dict)
        for (sym, d), ov in score_map.items():
            if ov is None or not (start_d <= d <= cutoff_d):
                continue
            by_sym[sym][d] = ov
        deltas = []
        for sym, dmap in by_sym.items():
            ds = sorted(dmap)
            # group by week
            wk_last = {}   # week_start -> (last_date)
            wk_first = {}
            for d in ds:
                wk = d - timedelta(days=d.weekday())
                if wk not in wk_first:
                    wk_first[wk] = d
                wk_last[wk] = d
            wks = sorted(wk_first)
            for i in range(len(wks) - 1):
                w0, w1 = wks[i], wks[i + 1]
                if (w1 - w0).days > 10:   # skip gaps > ~1.4 weeks (missing data)
                    continue
                a = dmap[wk_last[w0]]
                b = dmap[wk_first[w1]]
                deltas.append(abs(b - a))
        if not deltas:
            return 0, float("nan"), float("nan")
        arr = np.array(deltas, dtype=float)
        return len(arr), float(arr.mean()), float(np.percentile(arr, 90))

    # v60 state-3 scores for the same universe (from ledger, but ledger is only
    # >=70 — so the Mon-boundary jump must be measured on a FULL re-score under
    # state-3. We approximate v60 state-3 from the blended sim by re-deriving it
    # is not possible here; instead we measure the blend's own boundary jump and
    # compare to the look-ahead jump reconstructed from the ledger's qualifying
    # rows. For an apples-to-apples FULL comparison we re-score state-3 below.
    bn, bmean, bp90 = _mon_boundary_jump(blended)
    print(f"\n  BLENDED  Mon-boundary |Δoverall|:  pairs={bn}  mean={bmean:.2f}  p90={bp90:.2f}")

    # Re-score the SAME universe under v60 state-3 (look-ahead) to get a full,
    # apples-to-apples Mon-boundary jump. We do this by toggling the blend OFF
    # via an env flag the simulator honors (no pit kwargs => completed-only is
    # state-1, NOT state-3). To get true state-3 we instead read the stored DB
    # state-3 scores for the same (sym,date) over the window.
    print("\n  (reading stored v60 state-3 scores from DB for apples-to-apples jump)", flush=True)
    from database.trader_database import DB
    syms_in = ",".join("'%s'" % s.replace("'", "''") for s in syms)
    sql = (f"SELECT symbol, date, overall FROM scores WHERE version_id=60 "
           f"AND date BETWEEN '{START}' AND '{CUTOFF}' AND symbol IN ({syms_in})")
    v60_map = {}
    for sym, d, ov in DB.execute_sql(sql).fetchall():
        if ov is not None:
            v60_map[(sym, d)] = int(ov)
    vn2, vmean, vp90 = _mon_boundary_jump(v60_map)
    print(f"  v60 STATE-3 Mon-boundary |Δoverall|: pairs={vn2}  mean={vmean:.2f}  p90={vp90:.2f}")
    print(f"  => blend reduces mean Mon-boundary jump by {vmean - bmean:+.2f}pp "
          f"({(bmean/vmean-1)*100:+.0f}%)" if vmean else "")

    print("\n" + "=" * 78)
    print("REPORT 4 — BLEND BEHAVIOR SANITY (weight_info trace)")
    print("=" * 78)
    # t by dow + populate rates from captured weight_info.
    t_by_dow = defaultdict(list)
    n_with_partial = n_with_completed = n_total = 0
    gaps = []
    for (sym, d), wi in wi_by_key.items():
        if not (start_d <= d <= cutoff_d):
            continue
        n_total += 1
        tt = wi.get("weekly_transition_t")
        if tt is not None:
            t_by_dow[d.weekday()].append(float(tt))
        if wi.get("wadj_partial") is not None:
            n_with_partial += 1
        if wi.get("wadj_completed") is not None:
            n_with_completed += 1
        g = wi.get("weekly_adj_gap")
        if g is not None:
            gaps.append(abs(float(g)))
    print(f"\n  weekly_transition_t by day-of-week (target: Mon~0.2 .. Fri~1.0):")
    for wd in range(5):
        v = t_by_dow[wd]
        if v:
            print(f"    {DOW_NAMES[wd]}: mean_t={sum(v)/len(v):.3f}  (n={len(v)})")
    print(f"\n  rows with weekly weekly_detail: {n_total}")
    print(f"  wadj_completed populated: {n_with_completed} ({n_with_completed/max(1,n_total)*100:.1f}%)")
    print(f"  wadj_partial   populated: {n_with_partial} ({n_with_partial/max(1,n_total)*100:.1f}%)")
    if gaps:
        ga = np.array(gaps)
        print(f"  |wadj_partial - wadj_completed| gap: mean={ga.mean():.2f} "
              f"p50={np.percentile(ga,50):.2f} p90={np.percentile(ga,90):.2f} max={ga.max():.2f}")

    print("\n" + "=" * 78)
    print("REPORT 5 — BLAST RADIUS (blended vs v60 state-3, same (sym,date))")
    print("=" * 78)
    # Join blended vs v60 on (sym,date) over window.
    diffs = []
    b75 = v75 = 0
    common = 0
    for (sym, d), bov in blended.items():
        if bov is None or not (start_d <= d <= cutoff_d):
            continue
        vov = v60_map.get((sym, d))
        if vov is None:
            # blended-only (rare; both should exist) — count tier only
            if bov >= 75:
                b75 += 1
            continue
        common += 1
        diffs.append(abs(bov - vov))
        if bov >= 75:
            b75 += 1
        if vov >= 75:
            v75 += 1
    if diffs:
        da = np.array(diffs, dtype=float)
        print(f"\n  common (sym,date): {common}")
        print(f"  mean |Δoverall| (blend vs v60 state-3): {da.mean():.2f}")
        print(f"  p50={np.percentile(da,50):.1f}  p90={np.percentile(da,90):.1f}  "
              f"p99={np.percentile(da,99):.1f}  max={da.max():.0f}")
        print(f"  fraction with |Δ|>=1: {(da>=1).mean()*100:.1f}%  |Δ|>=5: {(da>=5).mean()*100:.1f}%")
    print(f"\n  75+ call count — blended: {b75}  |  v60 state-3: {v75}  "
          f"(Δ {b75-v75:+d}, {(b75/max(1,v75)-1)*100:+.1f}%)")

    # ── GO/NO-GO summary ───────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"  [1] look-ahead: blended 75-79 dow spread={b_spread:.1f}pp (Mon-Fri {b_mf:+.1f}pp) "
          f"vs v60 spread={v_spread:.1f}pp (Mon-Fri {v_mf:+.1f}pp)")
    print(f"  [3] stability: blend Mon-boundary mean |Δ|={bmean:.2f} vs v60 {vmean:.2f}")
    print(f"  total runtime {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
