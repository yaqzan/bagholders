"""Spread-skill KILL-TEST — the PRE-MC gate for the component-spread -> Stage-3
position-sizing-confidence idea (weatherization roadmap item #6, ensemble-sizing).

Round 2 (`spread_skill_apex.py`) found ONE band clearing |t|>=2 on the live apex
predictand: 75-79 low-spread beats high-spread by +1.76pp mean apex EV (Welch
t=+2.05, N=5,272 each). Pooled t=+1.60 (weak); 80-84 INVERTS; 85+/90+ underpowered.

Before that single-band edge earns a T1-T7 Monte Carlo, it must survive TWO
kill-tests that the DQT/sector/score-norm nulls failed:

  KILL-TEST 1 — PER-YEAR SIGN STABILITY (single-regime trap).
    On the 75-79 band, compute the low-vs-high spread-tercile mean-apex-EV gap
    (lo - hi) for EACH year 2021..2025 separately (+ Welch t + N per year).
    REQUIRE the gap POSITIVE (low-spread better) in >= 4 of 5 years.
    <=3 positive => single-regime artifact (e.g. a 2024-only risk-shaper) => FAIL.

  KILL-TEST 2 — ORTHOGONALITY TO `overall` (score-relabel trap).
    (a) corr(spread, overall) within 75-79: high |corr| => spread is a score proxy.
    (b) WITHIN each fixed integer score (75,76,77,78,79), is low-spread STILL
        higher-EV than high-spread? Pool the within-bucket lo-vs-hi comparisons
        (sign-consistency + pooled Welch t on the residualized halves).
        If the edge vanishes when score is held fixed, it's a re-label of the
        score (the score-norm WHAT-NOT-TO-DO trap) => FAIL.

BOTH must PASS for a SHIP-TO-MC verdict. The documented prior is AGAINST passing.

DATA SHORTCUT (verified, identical to round 2): reads the EXISTING v73 component
parquet 'ensemble_spread_v73_calls_2016'. v74 == v73 at the pre_boost checkpoint
bit-exact; component spread is on the PRE-pre_boost component columns
(trend/bb/rsi/macd/stoch), identical across v73/v74. The 30dte_apex barrier_set is
version-agnostic (price-path keyed). In-sample only (data ends 2026-06-12 < cutoff
2026-06-15). Read-only: no re-score, no MC, no recalculate, no writes.
"""
import os
import sys
import math
from collections import namedtuple

import numpy as np
import polars as pl

# Pin imports to THIS checkout (avoid the worktree-PYTHONPATH trap; harmless on main).
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
assert os.path.abspath(__file__).startswith(_ROOT), "running from an unexpected checkout"

from database.bulk_cache import materialize_polars, chunked_query_by_year
from database.barrier_cache import peaks_to_swing_results

# --- constants (identical to spread_skill_apex.py) ---
BARRIER = "30dte_apex"
PERIOD = "30d"
EV = {"win": +0.30, "stop": -0.70, "expire": -0.40}
MEMBERS = ["trend", "bb", "rsi", "macd", "stoch"]
DATA_NAME = "ensemble_spread_v73_calls_2016"
DATA_VID = 73
START_YEAR, END_YEAR = 2016, 2027

# the band Round 2 flagged as the ONLY |t|>=2 cohort
KILL_BAND = (75, 79)
KILL_YEARS = [2021, 2022, 2023, 2024, 2025]


def _build():
    """Fallback builder — only runs if the v73 parquet is somehow absent."""
    rows = chunked_query_by_year(
        "SELECT symbol, date, overall, trend, bb, rsi, macd, stoch, technical_alignment "
        "FROM scores WHERE version_id={vid} AND overall>=70 "
        "AND date BETWEEN '{y_start}' AND '{y_end}'",
        start_year=START_YEAR, end_year=END_YEAR, vid=DATA_VID,
    )
    return pl.DataFrame(
        {
            "symbol":  [r[0] for r in rows],
            "date":    [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
            "overall": [int(r[2]) for r in rows],
            "trend":   [float(r[3]) for r in rows],
            "bb":      [float(r[4]) for r in rows],
            "rsi":     [float(r[5]) for r in rows],
            "macd":    [float(r[6]) for r in rows],
            "stoch":   [float(r[7]) for r in rows],
            "ta":      [float(r[8]) for r in rows],
        }
    )


def welch_t(a, b):
    """Welch's unequal-variance two-sample t-test on means(a) vs means(b).
    Returns (t, mean_a - mean_b). Positive t => mean(a) > mean(b)."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return float("nan"), (a.mean() - b.mean()) if (na and nb) else float("nan")
    ma, mb = a.mean(), b.mean()
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return float("nan"), ma - mb
    return (ma - mb) / se, ma - mb


def load_df():
    parquet = materialize_polars(DATA_NAME, _build)
    df = pl.read_parquet(parquet)
    n_raw = df.height

    try:
        from experiments._holdout import pre_cutoff_filter, cutoff_iso
        df = pre_cutoff_filter(df)
        cutoff = cutoff_iso()
    except Exception:
        cutoff = "(holdout helper unavailable)"
    n_insample = df.height

    # component spread (5-member, population variance — identical to round 1/2)
    mean5 = sum(pl.col(c) for c in MEMBERS) / 5.0
    var5 = sum((pl.col(c) - mean5) ** 2 for c in MEMBERS) / 5.0
    df = df.with_columns(spread=var5.sqrt())

    # apex barrier outcome -> option EV via the version-agnostic cache
    from datetime import date as _date
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [
        Peak(s, _date.fromisoformat(d), int(o))
        for s, d, o in zip(df["symbol"], df["date"], df["overall"])
    ]
    results, skipped = peaks_to_swing_results(peaks, verbose=False, barrier_set=BARRIER)
    ev_map = {}
    for r in results:
        dk = r["date"]
        dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get(PERIOD)
        if sw and sw.get("result") in EV:
            ev_map[(r["symbol"], dk)] = EV[sw["result"]]

    df = df.with_columns(
        ev=pl.struct(["symbol", "date"]).map_elements(
            lambda x: ev_map.get((x["symbol"], x["date"]), float("nan")),
            return_dtype=pl.Float64,
        )
    ).filter(pl.col("ev").is_not_nan())
    # parse year from the ISO date string for the per-year split
    df = df.with_columns(year=pl.col("date").str.slice(0, 4).cast(pl.Int32))
    return df, n_raw, n_insample, n_raw - skipped, df.height, cutoff


def main():
    df, n_raw, n_insample, _, n_usable, cutoff = load_df()
    print("\n" + "=" * 78)
    print("SPREAD-SKILL KILL-TEST  (weatherization #6 ensemble-sizing PRE-MC gate)")
    print("=" * 78)
    print(f"barrier={BARRIER}  predictand={PERIOD}  EV={EV}")
    print(f"data: {DATA_NAME} (v73==v74 pre_boost; spread on pre-pre_boost comps => identical)")
    print(f"rows: {n_raw:,} pulled, {n_insample:,} in-sample (<= {cutoff}), "
          f"{n_usable:,} usable (spread+apexEV)")
    print(f"kill band = {KILL_BAND[0]}-{KILL_BAND[1]} (the only |t|>=2 cohort in Round 2)\n")

    lo_band, hi_band = KILL_BAND
    band = df.filter((pl.col("overall") >= lo_band) & (pl.col("overall") <= hi_band))
    sc = band["overall"].to_numpy()
    sp = band["spread"].to_numpy()
    ev = band["ev"].to_numpy()
    yr = band["year"].to_numpy()
    print(f"75-79 band total N = {band.height:,}, base mean apex EV = {ev.mean()*100:+.2f}%\n")

    # ============================================================== KILL-TEST 1
    # Per-year sign stability. Terciles are recomputed PER YEAR (the within-year
    # spread distribution is what a per-year operator would actually see); this is
    # the stricter, honest reading. (We also report a pooled-tercile cross-check.)
    print("-" * 78)
    print("KILL-TEST 1 — PER-YEAR SIGN STABILITY of the 75-79 lo-vs-hi spread EV gap")
    print("-" * 78)
    print(f"{'year':>6} | {'N':>6} | {'baseEV':>8} | {'loSpr EV/N':>14} | "
          f"{'hiSpr EV/N':>14} | {'lo-hi pp':>9} | {'Welch t':>8} | sign")
    pos_years = 0
    valid_years = 0
    per_year = []
    for y in KILL_YEARS:
        m = yr == y
        if m.sum() < 60:
            print(f"{y:>6} | {int(m.sum()):>6} |   (band N<60 — underpowered, skip)")
            continue
        valid_years += 1
        spv, evv = sp[m], ev[m]
        q1, q2 = np.quantile(spv, [1 / 3, 2 / 3])
        loT = spv <= q1
        hiT = spv > q2
        lo_ev, hi_ev = evv[loT], evv[hiT]
        t, diff = welch_t(lo_ev, hi_ev)
        gap = (lo_ev.mean() - hi_ev.mean()) * 100
        sign = "POS" if gap > 0 else "neg"
        if gap > 0:
            pos_years += 1
        print(f"{y:>6} | {int(m.sum()):>6} | {evv.mean()*100:7.2f}% | "
              f"{lo_ev.mean()*100:7.2f}% {len(lo_ev):>5} | "
              f"{hi_ev.mean()*100:7.2f}% {len(hi_ev):>5} | {gap:+8.2f} | {t:+7.2f} | {sign}")
        per_year.append((y, gap, t, len(lo_ev), len(hi_ev)))

    print(f"\n  PER-YEAR (own-year terciles): {pos_years}/{valid_years} years "
          f"low-spread better (require >= 4/5).")

    # cross-check: pooled-tercile cuts (global q1/q2), then per-year gap inside them
    q1g, q2g = np.quantile(sp, [1 / 3, 2 / 3])
    print(f"\n  cross-check (GLOBAL terciles q1={q1g:.3f} q2={q2g:.3f}, gap per year):")
    pos_years_g = 0
    valid_g = 0
    for y in KILL_YEARS:
        m = yr == y
        if m.sum() < 60:
            continue
        spv, evv = sp[m], ev[m]
        loT = spv <= q1g
        hiT = spv > q2g
        if loT.sum() < 10 or hiT.sum() < 10:
            print(f"    {y}: tercile N too small ({int(loT.sum())}/{int(hiT.sum())})")
            continue
        valid_g += 1
        lo_ev, hi_ev = evv[loT], evv[hiT]
        t, _ = welch_t(lo_ev, hi_ev)
        gap = (lo_ev.mean() - hi_ev.mean()) * 100
        if gap > 0:
            pos_years_g += 1
        print(f"    {y}: lo-hi {gap:+6.2f}pp  t={t:+5.2f}  "
              f"(N lo={int(loT.sum())} hi={int(hiT.sum())})")
    print(f"  GLOBAL-tercile sign-stability: {pos_years_g}/{valid_g} years positive.")

    kt1_pass = pos_years >= 4
    print(f"\n  >>> KILL-TEST 1 verdict: {'PASS' if kt1_pass else 'FAIL'} "
          f"({pos_years}/{valid_years} own-year, {pos_years_g}/{valid_g} global-tercile)\n")

    # ============================================================== KILL-TEST 2
    print("-" * 78)
    print("KILL-TEST 2 — ORTHOGONALITY TO `overall` within 75-79")
    print("-" * 78)
    # (a) correlation of spread with score inside the band
    if len(sc) > 2 and sp.std() > 0 and sc.std() > 0:
        corr = float(np.corrcoef(sp, sc.astype(float))[0, 1])
    else:
        corr = float("nan")
    print(f"(a) corr(spread, overall) within 75-79 = {corr:+.3f}  "
          f"(|corr|>=~0.5 => spread is largely a score proxy)\n")

    # (b) within each fixed integer score, low-spread vs high-spread EV
    print("(b) WITHIN fixed integer score (held constant), lo-vs-hi spread EV:")
    print(f"{'score':>6} | {'N':>6} | {'baseEV':>8} | {'loSpr EV/N':>14} | "
          f"{'hiSpr EV/N':>14} | {'lo-hi pp':>9} | {'Welch t':>8} | sign")
    agg_lo, agg_hi = [], []
    buckets_pos = 0
    buckets_valid = 0
    for s in range(lo_band, hi_band + 1):
        m = sc == s
        if m.sum() < 60:
            print(f"{s:>6} | {int(m.sum()):>6} |   (N<60 — underpowered, skip)")
            continue
        spv, evv = sp[m], ev[m]
        q1, q2 = np.quantile(spv, [1 / 3, 2 / 3])
        loT = spv <= q1
        hiT = spv > q2
        if loT.sum() < 10 or hiT.sum() < 10:
            print(f"{s:>6} | {int(m.sum()):>6} |   (tercile N too small)")
            continue
        buckets_valid += 1
        lo_ev, hi_ev = evv[loT], evv[hiT]
        t, _ = welch_t(lo_ev, hi_ev)
        gap = (lo_ev.mean() - hi_ev.mean()) * 100
        sign = "POS" if gap > 0 else "neg"
        if gap > 0:
            buckets_pos += 1
        print(f"{s:>6} | {int(m.sum()):>6} | {evv.mean()*100:7.2f}% | "
              f"{lo_ev.mean()*100:7.2f}% {len(lo_ev):>5} | "
              f"{hi_ev.mean()*100:7.2f}% {len(hi_ev):>5} | {gap:+8.2f} | {t:+7.2f} | {sign}")
        agg_lo.append(lo_ev)
        agg_hi.append(hi_ev)

    if agg_lo:
        pl_lo = np.concatenate(agg_lo)
        pl_hi = np.concatenate(agg_hi)
        pt, _ = welch_t(pl_lo, pl_hi)
        pooled_gap = (pl_lo.mean() - pl_hi.mean()) * 100
        print(f"{'POOLED':>6} | {'':>6} | {'':>8} | "
              f"{pl_lo.mean()*100:7.2f}% {len(pl_lo):>5} | "
              f"{pl_hi.mean()*100:7.2f}% {len(pl_hi):>5} | {pooled_gap:+8.2f} | {pt:+7.2f} |")
    else:
        pt, pooled_gap = float("nan"), float("nan")

    print(f"\n  within-fixed-score: {buckets_pos}/{buckets_valid} integer buckets "
          f"low-spread better; pooled within-bucket gap {pooled_gap:+.2f}pp, t={pt:+.2f}")
    # orthogonal if the edge survives at fixed score (sign + |t| meaningful) AND
    # spread is not just a score proxy
    kt2_pass = (
        not math.isnan(pt)
        and pt >= 1.5
        and pooled_gap > 0
        and abs(corr) < 0.5
        and buckets_pos >= max(1, (buckets_valid + 1) // 2)
    )
    print(f"\n  >>> KILL-TEST 2 verdict: {'PASS' if kt2_pass else 'FAIL'} "
          f"(corr {corr:+.3f}, within-bucket pooled t {pt:+.2f}, "
          f"{buckets_pos}/{buckets_valid} buckets positive)\n")

    # ====================================================================== VERDICT
    print("=" * 78)
    if kt1_pass and kt2_pass:
        print("FINAL VERDICT: SHIP-TO-MC")
        print("  Both kill-tests PASS: the 75-79 low-spread EV edge is sign-stable")
        print("  across years AND carries info orthogonal to `overall`.")
        print("  Proposed narrow Stage-3 mechanism (to be confirmed by T1-T7 N=500x8):")
        print("    a 75-79-ONLY component-spread alloc tilt — DOWN-WEIGHT ONLY")
        print("    (haircut high-spread/disagreement 75-79 calls toward a floor;")
        print("    never up-size) to stay collapse-safe. Still in-sample only.")
    else:
        print("FINAL VERDICT: SKIP-NULL")
        failed = []
        if not kt1_pass:
            failed.append("KILL-TEST 1 (per-year sign stability) — single-regime artifact")
        if not kt2_pass:
            failed.append("KILL-TEST 2 (orthogonality) — score-relabel artifact"
                          if abs(corr) >= 0.5 or (not math.isnan(pt) and pt < 1.5)
                          else "KILL-TEST 2 (orthogonality)")
        for f in failed:
            print(f"  FAILED: {f}")
        print("  File alongside the DQT / sector / score-norm nulls.")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
