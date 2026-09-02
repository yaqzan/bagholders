"""Robustness layer for the component verification (window everything; ensemble vs best member).

The substrate's hard lesson: pooled edges can be single-regime artifacts. This checks:
  A. PER-WINDOW stability of the headline signals (trend-deadness, macd-skill, ta-suppression,
     trend+macd anti-synergy, the 85-89 worst-band).
  B. ENSEMBLE vs BEST MEMBER: does overall's selection beat top-K-by-macd (matched count)?
     A good ensemble beats its best member; if ~equal, the machinery is a macd repackaging
     for per-trade purposes (the value is then the SELECTION/threshold, per the MC head-to-head).
Read-only. In-sample.
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
from database.models.core import AlgorithmVersion

VID = AlgorithmVersion.get_active_scores_version().id
EVMAP = {"win": +0.30, "stop": -0.70, "expire": -0.40}
SAMPLE_N = int(os.environ.get("SAMPLE_N", "300000"))


def mean_diff_t(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return (a.mean() - b.mean()) / se if se > 0 else float("nan")


def attach(df):
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(df["symbol"], df["date"])]
    results, _ = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    m = {}
    for r in results:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get("30d")
        if sw and sw.get("result") in EVMAP:
            m[(r["symbol"], dk)] = EVMAP[sw["result"]]
    ev = [m.get((s, d)) for s, d in zip(df["symbol"], df["date"])]
    return df.with_columns(
        apex_ev=pl.Series(ev, dtype=pl.Float64),
        year=pl.col("date").str.slice(0, 4),
    ).filter(pl.col("apex_ev").is_not_null())


def ev_of(df, mask):
    sub = df.filter(mask)
    return (sub.height, sub["apex_ev"].to_numpy() * 100 if sub.height else np.array([]))


def main():
    parquet = materialize_polars(f"weather_comp_v{VID}_2016", None)
    full = pl.read_parquet(parquet)
    uni = attach(full.sample(n=min(SAMPLE_N, full.height), seed=17))
    f75 = attach(full.filter(pl.col("overall") >= 75))
    years = ["2021", "2022", "2023", "2024", "2025"]

    print(f"\n{'='*78}\nA. PER-WINDOW STABILITY (apex EV, v{VID})\n{'='*78}")
    clim_by_yr = {}
    for y in ["FULL"] + years:
        d = uni if y == "FULL" else uni.filter(pl.col("year") == y)
        clim_by_yr[y] = d["apex_ev"].to_numpy().mean() * 100

    def line(label, fn):
        cells = []
        for y in ["FULL"] + years:
            d = uni if y == "FULL" else uni.filter(pl.col("year") == y)
            cells.append(fn(d, y))
        print(f"{label:>28} | " + " | ".join(cells))

    print(f"{'metric':>28} | " + " | ".join(f"{y:>10}" for y in ['FULL'] + years))
    line("climatology EV", lambda d, y: f"{clim_by_yr[y]:+9.2f}%")
    line("trend>=70 dEV vs clim", lambda d, y: f"{(ev_of(d, pl.col('trend')>=70)[1].mean()-clim_by_yr[y]):+9.2f}" if ev_of(d, pl.col('trend')>=70)[0]>30 else "       .  ")
    line("macd>=70 dEV vs clim", lambda d, y: f"{(ev_of(d, pl.col('macd')>=70)[1].mean()-clim_by_yr[y]):+9.2f}" if ev_of(d, pl.col('macd')>=70)[0]>30 else "       .  ")
    line("bb>=70 dEV vs clim", lambda d, y: f"{(ev_of(d, pl.col('bb')>=70)[1].mean()-clim_by_yr[y]):+9.2f}" if ev_of(d, pl.col('bb')>=70)[0]>30 else "       .  ")
    line("rsi>=70 dEV vs clim", lambda d, y: f"{(ev_of(d, pl.col('rsi')>=70)[1].mean()-clim_by_yr[y]):+9.2f}" if ev_of(d, pl.col('rsi')>=70)[0]>30 else "       .  ")

    def antisyn(d, y):
        nb, eb = ev_of(d, (pl.col('trend')>=70) & (pl.col('macd')>=70))
        na, ea = ev_of(d, (pl.col('macd')>=70) & (pl.col('trend')<70))
        if nb < 30 or na < 30:
            return "       .  "
        return f"{(eb.mean()-ea.mean()):+9.2f}"
    line("trend+macd vs macd-only", antisyn)

    print(f"\n  ta SUPPRESSION (multivar beta(/SD) on apex_ev, per window):")
    for y in ["FULL"] + years:
        d = uni if y == "FULL" else uni.filter(pl.col("year") == y)
        yv = d["apex_ev"].to_numpy()
        Xc = []
        for c in ["trend", "bb", "rsi", "macd", "stoch", "ta"]:
            v = d[c].to_numpy().astype(float); Xc.append((v - v.mean())/(v.std()+1e-9))
        X = np.column_stack([np.ones(len(yv))] + Xc)
        try:
            beta = np.linalg.lstsq(X, yv, rcond=None)[0]
            print(f"     {y:>6}: ta={beta[6]*100:+.2f}  macd={beta[4]*100:+.2f}  trend={beta[1]*100:+.2f}  bb={beta[2]*100:+.2f}  rsi={beta[3]*100:+.2f}  stoch={beta[5]*100:+.2f}")
        except Exception as e:
            print(f"     {y:>6}: {e}")

    print(f"\n{'='*78}\nB. ENSEMBLE vs BEST MEMBER — overall>=75 vs top-K-by-macd (matched count) ===")
    for y in ["FULL"] + years:
        d = uni if y == "FULL" else uni.filter(pl.col("year") == y)
        sc = d["overall"].to_numpy(); mc = d["macd"].to_numpy().astype(float); ev = d["apex_ev"].to_numpy()*100
        s75 = sc >= 75; K = int(s75.sum())
        if K < 30:
            print(f"  {y:>6}: K<30 skip"); continue
        thr = np.quantile(mc, 1 - K/len(mc)); topm = mc >= thr
        z = mean_diff_t(ev[s75], ev[topm])
        print(f"  {y:>6}: overall>=75 EV {ev[s75].mean():+5.2f}% (N={K:,}) | top-macd EV {ev[topm].mean():+5.2f}% (N={int(topm.sum()):,}) | ens-best {ev[s75].mean()-ev[topm].mean():+5.2f}pp t={z:+.2f}")

    print(f"\n{'='*78}\nC. FUNDED 85-89 WORST-BAND (substrate flag #2) — apex EV by tier, per window ===")
    print(f"{'tier':>8} | " + " | ".join(f"{y:>9}" for y in ['FULL'] + years))
    for lo, hi, lab in [(75,80,"75-79"),(80,85,"80-84"),(85,90,"85-89"),(90,101,"90+")]:
        cells = []
        for y in ["FULL"] + years:
            d = f75 if y == "FULL" else f75.filter(pl.col("year") == y)
            n, e = ev_of(d, (pl.col("overall")>=lo) & (pl.col("overall")<hi))
            cells.append(f"{e.mean():+6.2f}%(N{n})" if n >= 20 else "    .    ")
        print(f"{lab:>8} | " + " | ".join(f"{c:>9}" for c in cells))
    print("\nNOTE: in-sample. apex EV +0.30/-0.70/-0.40, 30d. Per-window sample sizes are smaller; read signs + stability, not precision.")


if __name__ == "__main__":
    main()
