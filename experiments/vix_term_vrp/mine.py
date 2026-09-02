"""N5 (NEW_LEADS): forward-vol-expectation cohort probe — VIX term structure,
VVIX, variance risk premium (VRP). The last untested regime-signal family.

The VIX-momentum family (weekly-MACD/velocity/accel) is freshly NULL (G28); the
regime multiplier already encodes VIX LEVEL + velocity. This probes a DIFFERENT
family: FORWARD vol expectation — VIX9D/VIX3M term-structure slope (backwardation
= near-term fear > medium-term), VVIX (vol-of-vol), VRP (implied^2 - realized^2).

Decisive test (G28): a forward-vol signal must add WITHIN VIX-level bands
(orthogonal to level), not just correlate with level. So every cohort is mined
both pooled AND within calm/norm/elev VIX bands. z>=3 regime-stable => real lead;
else close the corner (expectation LOW — all prior regime A/Bs null).

Read-only. In-sample. apex EV +0.30/-0.70/-0.40, 30d. funded 75+ CALL book.
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
from database.models.core import AlgorithmVersion, MarketRegime

VID = AlgorithmVersion.get_active_scores_version().id
EVMAP = {"win": +0.30, "stop": -0.70, "expire": -0.40}
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vix_term_cache.parquet")


def two_prop_z(x1, n1, x2, n2):
    if not n1 or not n2:
        return float("nan")
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (p1 - p2) / se if se > 0 else float("nan")


def build_vol_features():
    """Fetch VIX9D/VIX3M/VVIX/^VIX/SPY (yfinance), cache, build date->features."""
    if os.path.exists(CACHE):
        return pl.read_parquet(CACHE)
    import yfinance as yf
    import pandas as pd
    def col(t):
        d = yf.download(t, start="2015-06-01", end="2026-06-15", progress=False, auto_adjust=False)
        s = d["Close"]
        if hasattr(s, "columns"):
            s = s.iloc[:, 0]
        return s
    vix9d, vix3m, vvix, vix = col("^VIX9D"), col("^VIX3M"), col("^VVIX"), col("^VIX")
    spy = col("SPY")
    rv21 = (spy.pct_change().rolling(21).std() * math.sqrt(252) * 100.0)  # annualized %
    df = pd.DataFrame({"vix9d": vix9d, "vix3m": vix3m, "vvix": vvix, "vix": vix, "rv21": rv21}).dropna(subset=["vix9d", "vix3m"])
    df = df.reset_index().rename(columns={"Date": "date", "index": "date"})
    out = pl.DataFrame({
        "date": [d.date().isoformat() for d in df["date"]],
        "vix9d": df["vix9d"].astype(float).to_list(),
        "vix3m": df["vix3m"].astype(float).to_list(),
        "vvix": df["vvix"].astype(float).to_list(),
        "vix": df["vix"].astype(float).to_list(),
        "rv21": df["rv21"].astype(float).to_list(),
    }).with_columns(
        ts_ratio=pl.col("vix9d") / pl.col("vix3m"),                 # >1 backwardation (near-term fear)
        vrp=pl.col("vix") ** 2 - pl.col("rv21") ** 2,               # >0 vol premium; <0 stress
    )
    out.write_parquet(CACHE)
    return out


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
    resv = [m.get((s, d)) for s, d in zip(df["symbol"], df["date"])]
    return df.with_columns(
        apex_ev=pl.Series([EVMAP[r] if r else None for r in resv], dtype=pl.Float64),
        apex_win=pl.Series([(1 if r == "win" else 0) if r else None for r in resv], dtype=pl.Int64),
        yr=pl.col("date").str.slice(0, 4),
    ).filter(pl.col("apex_ev").is_not_null()), sk


def terc_report(df, feat, label):
    v = df[feat].to_numpy()
    valid = ~np.isnan(v)
    d = df.filter(pl.Series(valid))
    v = v[valid]
    qs = np.quantile(v, [1/3, 2/3])
    print(f"\n  [{label}] terciles on {feat} (cut {qs[0]:.3f}, {qs[1]:.3f}):")
    print(f"    {'tercile':>8} | {'N':>6} {'EV':>7} {'WR':>6} | {'zWin vs rest':>12}")
    masks = {"low": v <= qs[0], "mid": (v > qs[0]) & (v <= qs[1]), "high": v > qs[1]}
    for name, mk in masks.items():
        sub = d.filter(pl.Series(mk)); rest = d.filter(pl.Series(~mk))
        if sub.height < 30:
            print(f"    {name:>8} | N={sub.height} thin"); continue
        z = two_prop_z(int(sub["apex_win"].sum()), sub.height, int(rest["apex_win"].sum()), rest.height)
        print(f"    {name:>8} | {sub.height:>6,} {sub['apex_ev'].mean()*100:+6.2f}% {sub['apex_win'].mean()*100:5.1f}% | {z:+12.2f}")


def main():
    vol = build_vol_features()
    print(f"vol features: {vol.height:,} dates  {vol['date'][0]}..{vol['date'][-1]}")
    parquet = materialize_polars(f"weather_comp_v{VID}_2016", None)
    f75 = pl.read_parquet(parquet).filter(pl.col("overall") >= 75).select(["symbol", "date"])
    f75 = f75.join(vol, on="date", how="inner")
    f75, sk = attach(f75)
    print(f"funded 75+ apex-matched w/ vol features: {f75.height:,} ({sk:,} uncached)")
    base_ev = f75["apex_ev"].mean() * 100
    print(f"baseline 75+ apex-EV = {base_ev:+.2f}%  WR={f75['apex_win'].mean()*100:.1f}%  N={f75.height:,}")

    # VIX-level bands (control: the multiplier already encodes level)
    f75 = f75.with_columns(vixb=pl.when(pl.col("vix") < 16).then(pl.lit("calm<16"))
                           .when(pl.col("vix") < 22).then(pl.lit("norm16-22"))
                           .when(pl.col("vix") < 28).then(pl.lit("elev22-28"))
                           .otherwise(pl.lit("panic>=28")))

    print("\n=== POOLED terciles (does any forward-vol state move apex-EV?) ===")
    for feat in ["ts_ratio", "vrp", "vvix"]:
        terc_report(f75, feat, "pooled")

    print("\n=== CONTROL FOR VIX LEVEL (the decisive test, G28) ===")
    print("  a real signal must survive within a level band (z stays strong, same sign).")
    for band in ["calm<16", "norm16-22", "elev22-28"]:
        sub = f75.filter(pl.col("vixb") == band)
        if sub.height < 200:
            print(f"\n  -- {band}: N={sub.height} thin --"); continue
        print(f"\n  -- within {band} (N={sub.height:,}) --")
        for feat in ["ts_ratio", "vrp"]:
            terc_report(sub, feat, band)

    # backwardation flag (a discrete state often cited): VIX9D > VIX3M
    print("\n=== backwardation flag (vix9d > vix3m = near-term fear), per VIX band ===")
    for band in ["ALL", "calm<16", "norm16-22", "elev22-28"]:
        sub = f75 if band == "ALL" else f75.filter(pl.col("vixb") == band)
        bw = sub.filter(pl.col("ts_ratio") > 1.0); ct = sub.filter(pl.col("ts_ratio") <= 1.0)
        if bw.height < 30 or ct.height < 30:
            print(f"  {band:>10}: thin (bw={bw.height} ct={ct.height})"); continue
        z = two_prop_z(int(bw["apex_win"].sum()), bw.height, int(ct["apex_win"].sum()), ct.height)
        print(f"  {band:>10}: backwardation EV {bw['apex_ev'].mean()*100:+5.2f}%(N{bw.height:,}) | "
              f"contango EV {ct['apex_ev'].mean()*100:+5.2f}%(N{ct.height:,}) | bw-ct zWin {z:+.2f}")

    print("\nNOTE: in-sample; v1 apex EV map. A pooled hit that DISSOLVES within VIX bands = it was")
    print("just VIX level (already encoded) -> NULL. Need z>=3 surviving a level band to be real.")


if __name__ == "__main__":
    main()
