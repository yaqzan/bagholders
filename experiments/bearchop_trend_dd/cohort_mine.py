"""Phase 2a (cheap precondition, NO MC): is the trend-dominant 75+ cohort a
LOW-EV target in the sustained bear/chop band (ex-panic) on the funded apex payoff?

Hypothesis (Stage-3): a regime-gated call-alloc dampener on the over-extended
trend-confirm 75+ cohort in bear/chop (elevated VIX 22-28 and/or weak breadth,
EXCLUDING acute panic where mean-reversion wins) cuts DD without cutting
bull/neutral participation. Grounded in weather_components: TREND is the
regime-harmful driver (dEV -2.42/-1.81 in 2022/2023); RSI is bear-robust.

This script tests the PRECONDITION before any MC:
  (1) baseline 75+ apex-EV by VIX band + year (reference);
  (2) the trend-dominant cohorts' absolute apex-EV vs band-rest, by regime band;
  (3) per-window (the G26 reversal-trap check: bad in bear/chop AND not-good-in-bull?);
  (4) confirm panic (VIX>=28) trend-confirm is flat/positive (mean-reversion -> do NOT trim).

Read-only. In-sample (data end < holdout 2026-06-15). apex EV +0.30/-0.70/-0.40, 30d.
"""
import os
import sys
import math
from collections import namedtuple
from datetime import date as _date

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.bulk_cache import materialize_polars
from database.barrier_cache import peaks_to_swing_results
from database.models.core import AlgorithmVersion, MarketRegime, MarketBreadth

VID = AlgorithmVersion.get_active_scores_version().id
EVMAP = {"win": +0.30, "stop": -0.70, "expire": -0.40}


def two_prop_z(x1, n1, x2, n2):
    if not n1 or not n2:
        return float("nan")
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (p1 - p2) / se if se > 0 else float("nan")


def mean_diff_t(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return (a.mean() - b.mean()) / se if se > 0 else float("nan")


def date_maps():
    vix, brd, rmult = {}, {}, {}
    for r in MarketRegime.select(MarketRegime.date, MarketRegime.vix_close,
                                 MarketRegime.regime_multiplier).where(
            MarketRegime.date.is_null(False)):
        d = r.date.isoformat() if hasattr(r.date, "isoformat") else str(r.date)
        if r.vix_close is not None:
            vix[d] = float(r.vix_close)
        if r.regime_multiplier is not None:
            rmult[d] = float(r.regime_multiplier)
    for r in MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score).where(
            MarketBreadth.breadth_score.is_null(False)):
        d = r.date.isoformat() if hasattr(r.date, "isoformat") else str(r.date)
        brd[d] = float(r.breadth_score)
    return vix, brd, rmult


def vix_band(v):
    if v != v:
        return None
    if v < 16:
        return "calm<16"
    if v < 22:
        return "norm16-22"
    if v < 28:
        return "elev22-28"
    return "panic>=28"


def attach(df):
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(df["symbol"], df["date"])]
    res, sk = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    m = {}
    for r in res:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get("30d")
        if sw and sw.get("result") in EVMAP:
            m[(r["symbol"], dk)] = sw["result"]
    vix, brd, rmult = date_maps()
    resv = [m.get((s, d)) for s, d in zip(df["symbol"], df["date"])]
    df = df.with_columns(
        apex_res=pl.Series(resv, dtype=pl.Utf8),
        apex_ev=pl.Series([EVMAP[r] if r is not None else None for r in resv], dtype=pl.Float64),
        apex_win=pl.Series([(1 if r == "win" else 0) if r is not None else None for r in resv], dtype=pl.Int64),
        vix=pl.Series([vix.get(d, float("nan")) for d in df["date"]], dtype=pl.Float64),
        brd=pl.Series([brd.get(d, float("nan")) for d in df["date"]], dtype=pl.Float64),
        rmult=pl.Series([rmult.get(d, float("nan")) for d in df["date"]], dtype=pl.Float64),
        vixb=pl.Series([vix_band(vix.get(d, float("nan"))) for d in df["date"]], dtype=pl.Utf8),
        yr=pl.col("date").str.slice(0, 4),
    ).filter(pl.col("apex_res").is_not_null())
    return df, sk


def stat(df, mask):
    s = df.filter(mask)
    if s.height == 0:
        return 0, float("nan"), float("nan"), np.array([]), 0
    ev = s["apex_ev"].to_numpy()
    wins = int(s["apex_win"].sum())
    return s.height, s["apex_win"].mean() * 100, ev.mean() * 100, ev, wins


def main():
    parquet = materialize_polars(f"weather_comp_v{VID}_2016", None)
    full = pl.read_parquet(parquet)
    f75, sk = attach(full.filter(pl.col("overall") >= 75))
    print(f"\n{'='*92}\nBEAR/CHOP TREND-DD precondition mine  (v{VID}, 30dte_apex CALL payoff, funded >=75)\n{'='*92}")
    print(f"funded >=75 apex-matched: {f75.height:,}  ({sk:,} uncached)")

    # ---------- (1) baseline reference ----------
    n, wr, evb, _, _ = stat(f75, pl.lit(True))
    print(f"\n=== (1) BASELINE 75+ reference ===")
    print(f"  ALL 75+:  N={n:,}  WR={wr:.1f}%  EV={evb:+.2f}%")
    print("  by VIX band:")
    for b in ["calm<16", "norm16-22", "elev22-28", "panic>=28"]:
        n2, wr2, ev2, _, _ = stat(f75, pl.col("vixb") == b)
        print(f"    {b:>10}: N={n2:>6,}  WR={wr2:5.1f}%  EV={ev2:+6.2f}%")
    print("  by year:")
    for y in ["2016","2017","2018","2019","2020","2021","2022","2023","2024","2025","2026"]:
        n2, wr2, ev2, _, _ = stat(f75, pl.col("yr") == y)
        if n2 >= 50:
            print(f"    {y}: N={n2:>6,}  WR={wr2:5.1f}%  EV={ev2:+6.2f}%")

    # ---------- (2) cohort EV vs band-rest, by regime band ----------
    # cohort definitions (the over-extended trend-confirm tail)
    cohorts = {
        "trend>=80":            (pl.col("trend") >= 80),
        "trend>=70":            (pl.col("trend") >= 70),
        "trend&macd both>=70":  (pl.col("trend") >= 70) & (pl.col("macd") >= 70),
        "trend>=80 & rsi<50":   (pl.col("trend") >= 80) & (pl.col("rsi") < 50),  # up but no mean-rev support
        "trend>=80 & macd>=70": (pl.col("trend") >= 80) & (pl.col("macd") >= 70),
    }
    # regime bands (the candidate bear/chop gates, EX-panic where noted)
    bands = {
        "ALL":                  pl.lit(True),
        "elev22-28":            (pl.col("vixb") == "elev22-28"),
        "weak-brd<40":          (pl.col("brd") < 40),
        "elev|weakbrd ex-panic": ((pl.col("vixb") == "elev22-28") | (pl.col("brd") < 40)) & (pl.col("vix") < 28),
        "lowmult<0.92 ex-panic": (pl.col("rmult") < 0.92) & (pl.col("vix") < 28),
        "panic>=28":            (pl.col("vix") >= 28),
    }
    print(f"\n=== (2) cohort apex-EV vs band-REST (within each regime band) ===")
    print("  z = two-prop on WIN (cohort vs rest-of-75+-in-band). want cohort EV << rest AND z<=-2 to have a target.")
    for cname, cmask in cohorts.items():
        print(f"\n  cohort [{cname}]:")
        print(f"    {'band':>22} | {'N_coh':>6} {'EVcoh':>7} {'WRcoh':>6} | {'N_rest':>6} {'EVrest':>7} {'WRrest':>6} | {'dEV':>6} {'zWin':>6}")
        for bname, bmask in bands.items():
            nc, wrc, evc, _, wc = stat(f75, bmask & cmask)
            nr, wrr, evr, _, wr_ = stat(f75, bmask & ~cmask)
            if nc < 30 or nr < 30:
                print(f"    {bname:>22} | N_coh={nc:<5} (thin)")
                continue
            z = two_prop_z(wc, nc, wr_, nr)
            print(f"    {bname:>22} | {nc:>6,} {evc:+6.2f}% {wrc:5.1f}% | {nr:>6,} {evr:+6.2f}% {wrr:5.1f}% | {evc-evr:+6.2f} {z:+6.2f}")

    # ---------- (3) per-window crash-robustness (the G26 reversal-trap check) ----------
    print(f"\n=== (3) PER-WINDOW: cohort [trend&macd both>=70] in [elev|weakbrd ex-panic] vs band-rest ===")
    print("  trimming is a Pareto candidate ONLY if cohort is bad in bear/chop AND NOT-good in bull (else G26 reversal-trap).")
    coh = (pl.col("trend") >= 70) & (pl.col("macd") >= 70)
    band = ((pl.col("vixb") == "elev22-28") | (pl.col("brd") < 40)) & (pl.col("vix") < 28)
    print(f"    {'year':>6} | {'N_coh':>6} {'EVcoh':>7} | {'N_rest':>6} {'EVrest':>7} | {'dEV':>7}")
    for y in ["2016","2017","2018","2019","2021","2022","2023","2024","2025","2026"]:
        ym = pl.col("yr") == y
        nc, _, evc, _, _ = stat(f75, ym & band & coh)
        nr, _, evr, _, _ = stat(f75, ym & band & ~coh)
        if nc < 15:
            print(f"    {y:>6} | N_coh={nc:<5} (thin)")
            continue
        print(f"    {y:>6} | {nc:>6,} {evc:+6.2f}% | {nr:>6,} {evr:+6.2f}% | {evc-evr:+7.2f}")

    # ---------- (4) high-trend variant per-window (the diagnostic's component signal, mapped) ----------
    print(f"\n=== (4) PER-WINDOW: cohort [trend>=80] in [elev|weakbrd ex-panic] vs band-rest ===")
    coh2 = (pl.col("trend") >= 80)
    print(f"    {'year':>6} | {'N_coh':>6} {'EVcoh':>7} | {'N_rest':>6} {'EVrest':>7} | {'dEV':>7}")
    for y in ["2016","2017","2018","2019","2021","2022","2023","2024","2025","2026"]:
        ym = pl.col("yr") == y
        nc, _, evc, _, _ = stat(f75, ym & band & coh2)
        nr, _, evr, _, _ = stat(f75, ym & band & ~coh2)
        if nc < 15:
            print(f"    {y:>6} | N_coh={nc:<5} (thin)")
            continue
        print(f"    {y:>6} | {nc:>6,} {evc:+6.2f}% | {nr:>6,} {evr:+6.2f}% | {evc-evr:+7.2f}")

    print("\nNOTE: in-sample; v1 apex EV map (no dead-hold/theta). Directional precondition only;")
    print("portfolio DD + orthogonality come from the MC tape (Phase 2b).")


if __name__ == "__main__":
    main()
