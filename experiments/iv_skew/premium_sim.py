"""Premium-aware portfolio sim on the 1.3y covered window using ACTUAL option P&L.
Does filtering out euphoric (low-skew) calls improve the real equity curve / DD?

Baseline = all 75+ calls (and 70+). Filtered = drop bottom-X% skew (the expensive/crowded calls).
Each signal's realized P&L = apex_pnl (TP+30/SL-70/HOLD-15 reconstructed from the actual option path).
Portfolio = equal-weight the month's signals -> monthly P&L -> compound -> equity curve + maxDD.
(Monthly equal-weight avoids overlapping-position timing artifacts; robust for a probe.)
Reports per-trade mean/win/tail + monthly-compound total return + maxDD, baseline vs filtered,
sweeping the skew cut. Honest: 1.3y, premium-aware, ~real.
"""
import os, sys
import numpy as np
import polars as pl
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
IV = os.path.join(_ROOT, ".cache", "iv_skew", "iv_ledger.parquet")


def apex_pnl(pmax, pmin, p15):
    if pmin is None or pmax is None or p15 is None: return None
    if pmin <= -0.70: return -0.70
    if pmax >= 0.30: return 0.30
    return max(p15, -0.70)


def maxdd(curve):
    peak = -1e9; dd = 0.0
    for v in curve:
        peak = max(peak, v)
        dd = min(dd, (v - peak) / peak if peak > 0 else 0.0)
    return dd


def sim(df, f=0.10):
    """monthly equal-weight book: each month, mean apex_pnl of its signals; deploy fraction f of
    equity spread across that month's trades (approx via mean*f compounding). Returns (total_ret, maxDD, N)."""
    d = df.filter(pl.col("apex_pnl").is_not_null())
    if d.height < 20:
        return None
    d = d.with_columns(pl.col("date").str.slice(0, 7).alias("ym"))
    months = d.group_by("ym").agg([pl.col("apex_pnl").mean().alias("m"), pl.len().alias("n")]).sort("ym")
    eq = 1.0; curve = [1.0]
    for m, n in zip(months["m"].to_list(), months["n"].to_list()):
        # deploy fraction f of equity equally across n trades; book return that month = f * mean_pnl
        eq *= (1.0 + f * m)
        curve.append(eq)
    return (eq - 1.0), maxdd(curve), d.height, float(d["apex_pnl"].mean()), 100*float((d["apex_pnl"] > 0).mean())


def main():
    df = pl.read_parquet(IV).with_columns(
        pl.struct(["pnl_max", "pnl_min", "pnl15"]).map_elements(
            lambda r: apex_pnl(r["pnl_max"], r["pnl_min"], r["pnl15"]), return_dtype=pl.Float64).alias("apex_pnl"))
    for tlo, tname in [(75, "75+"), (70, "70+")]:
        tier = df.filter(pl.col("overall") >= tlo)
        print(f"\n########## {tname} calls (N={tier.height}) — premium-aware monthly-compound book (f=0.10) ##########")
        base = sim(tier)
        print(f"  {'BASELINE (all)':28} ret={base[0]*100:+8.1f}%  maxDD={base[1]*100:5.1f}%  N={base[2]:4}  perTrade={base[3]*100:+.1f}% win={base[4]:.0f}%")
        skewcol = tier.filter(pl.col("skew").is_not_null())
        for q, lab in [(0.20, "drop bottom-20% skew"), (0.33, "drop bottom-33% skew"),
                       (0.50, "drop bottom-50% skew"), (0.0, "keep skew>=0 only")]:
            if q == 0.0:
                filt = skewcol.filter(pl.col("skew") >= 0)
            else:
                cut = skewcol["skew"].quantile(q)
                filt = skewcol.filter(pl.col("skew") >= cut)
            r = sim(filt)
            if r:
                dvs = r[0]*100 - base[0]*100
                print(f"  {lab:28} ret={r[0]*100:+8.1f}%  maxDD={r[1]*100:5.1f}%  N={r[2]:4}  perTrade={r[3]*100:+.1f}% win={r[4]:.0f}%   Δret={dvs:+.1f}pp ΔDD={ (r[1]-base[1])*100:+.1f}pp")
    print("\nNOTE: skew filter is portfolio-stage (entry), score frozen. 1.3y/premium-aware. Watch the N drop")
    print("(capacity): the filter must improve return/DD WITHOUT cutting so many signals the book starves.")


if __name__ == "__main__":
    main()
