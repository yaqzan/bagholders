"""W1 for the LIFT (dip-buy) side: would promoting the trend-break + oversold-stoch
capitulation cohort to a CALL be justified on the OPTION barrier (not generic)?

Gate-aligned test (avoids the v42/SVD generic-vs-option trap):
  Compare the dip cohort's win-rate on the OPTION-aligned barriers (opt15 = growth-gate,
  apex15 = funded HOLD) against the EXISTING 75+ call population on the SAME barrier.
  LIFT is justified (W1 PASS) only if the cohort MATCHES-OR-BEATS the 75+ base
  (so it's non-dilutive / W6-preserving) at two-proportion z >= +2..+3, with a sane
  monotone gradient by stoch-depth. If it's BELOW the 75+ base on the option barrier,
  promoting it is the volume-dump trap -> NULL.
"""
import os, sys, math
import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
import label as L  # noqa

DIP = os.path.join(_ROOT, ".cache", "divergence_dampener", "ledger_v70_wide.parquet")
BASE = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")

BARRIERS = [("opt15", 0.901, 0.772, 15), ("apex15", 1.092, 2.548, 15),
            ("hold15", 0.901, 7.07, 15), ("gen15", 1.414, 3.536, 15)]


def addbars(df):
    return df.with_columns([L.win_expr(tp, sl, w).alias(nm) for nm, tp, sl, w in BARRIERS])


def zprop(w1, n1, w2, n2):
    if n1 < 5 or n2 < 5:
        return None, None, None
    p1, p2 = w1 / n1, w2 / n2
    pp = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
    return p1 - p2, ((p1 - p2) / se if se else None), (p1, p2)


def wr(series):
    r = series.drop_nulls()
    return (r.sum(), r.len(), (r.mean() if r.len() else None))


def main():
    dip = addbars(pl.read_parquet(DIP))
    print(f"DIP ledger N={dip.height} window={dip['date'].min()}..{dip['date'].max()}")
    assert dip["date"].max() <= "2026-05-15", "HOLDOUT LEAK"

    # 75+ call BASE rates measured from the REAL 60-99 ledger (w1_preflight, pre-clobber).
    # (wins, resolvedN, rate) per barrier; resolvedN=4699 across barriers in that run.
    NB = 4699
    base75 = {
        "opt15":  (round(0.475 * NB), NB, 0.475),
        "apex15": (round(0.700 * NB), NB, 0.700),
        "hold15": (round(0.789 * NB), NB, 0.789),
        "gen15":  (round(0.663 * NB), NB, 0.663),
    }
    print(f"\n=== 75+ call BASE rates (the bar to clear; from real 60-99 ledger, N={NB}) ===")
    for nm, *_ in BARRIERS:
        print(f"  {nm:7}: {base75[nm][2]*100:5.1f}%")

    print(f"\n=== DIP cohort overall-score distribution (where do they currently sit?) ===")
    ov = dip["overall"]
    print(f"  min={ov.min()} p25={ov.quantile(.25):.0f} median={ov.median():.0f} p75={ov.quantile(.75):.0f} max={ov.max()}  "
          f"%>=70:{100*(ov>=70).sum()/dip.height:.1f}  %>=60:{100*(ov>=60).sum()/dip.height:.1f}")

    # ---- whole dip cohort vs 75+ base ----
    print(f"\n=== DIP COHORT (trend<=45 & stoch>=80) vs 75+ base, on each barrier ===")
    for nm, *_ in BARRIERS:
        cs, cn, cm = wr(dip[nm])
        bs, bn, bm = base75[nm]
        if cn < 5:
            print(f"  {nm:7}: dip resolvedN={cn} too small"); continue
        diff, z, _ = zprop(int(cs), cn, int(bs), bn)
        flag = ""
        if z is not None:
            if nm in ("opt15", "apex15"):
                if z >= 3: flag = "  <-- W1 PASS (non-dilutive lift)"
                elif z >= 2: flag = "  (flag +)"
                elif z <= -3: flag = "  <-- DILUTIVE (NULL the lift)"
                elif z <= -2: flag = "  (flag -, dilutive)"
        print(f"  {nm:7}: dipWR={cm*100:5.1f}% (N={cn:5})  base75WR={bm*100:5.1f}%  d={diff*100:+5.1f}pp  z={ (z if z is not None else float('nan')):+5.2f}{flag}")

    # ---- gradient by stoch depth and trend depth ----
    print(f"\n=== GRADIENT: dip-cohort opt15/apex15 WR by stoch depth ===")
    for lo, hi in [(80, 84), (85, 89), (90, 94), (95, 100)]:
        sub = dip.filter((pl.col("c_stoch") >= lo) & (pl.col("c_stoch") <= hi))
        cells = []
        for nm in ("opt15", "apex15", "hold15"):
            s, n, m = wr(sub[nm]); cells.append(f"{nm}={m*100:4.1f}%/{n:<4}" if n else f"{nm}=  - ")
        print(f"  stoch[{lo}-{hi}] N={sub.height:5}  " + "  ".join(cells))
    print(f"\n=== GRADIENT: dip-cohort opt15/apex15 WR by trend depth (deeper break = lower trend) ===")
    for lo, hi in [(0, 15), (16, 30), (31, 45)]:
        sub = dip.filter((pl.col("c_trend") >= lo) & (pl.col("c_trend") <= hi))
        cells = []
        for nm in ("opt15", "apex15", "hold15"):
            s, n, m = wr(sub[nm]); cells.append(f"{nm}={m*100:4.1f}%/{n:<4}" if n else f"{nm}=  - ")
        print(f"  trend[{lo}-{hi}] N={sub.height:5}  " + "  ".join(cells))

    # ---- tighten: does adding raw-rsi-oversold (low c_rsi=overbought is WRONG here; we want
    #      c_rsi HIGH = raw rsi oversold) or bb<=low help? ----
    print(f"\n=== TIGHTENERS (stack conditions, opt15/apex15 vs 75+ base) ===")
    TIGHT = {
        "+ stoch>=90":                 (pl.col("c_stoch") >= 90),
        "+ stoch>=90 & rsi>=55":       (pl.col("c_stoch") >= 90) & (pl.col("c_rsi") >= 55),
        "+ stoch>=90 & bb<=40":        (pl.col("c_stoch") >= 90) & (pl.col("c_bb") <= 40),
        "+ stoch>=90 & macd<=35":      (pl.col("c_stoch") >= 90) & (pl.col("c_macd") <= 35),
        "+ trend<=30 & stoch>=88":     (pl.col("c_trend") <= 30) & (pl.col("c_stoch") >= 88),
    }
    for tname, tmask in TIGHT.items():
        sub = dip.filter(tmask)
        cells = []
        for nm in ("opt15", "apex15"):
            cs, cn, cm = wr(sub[nm]); bs, bn, bm = base75[nm]
            if cn < 5:
                cells.append(f"{nm}: N={cn} small"); continue
            diff, z, _ = zprop(int(cs), cn, int(bs), bn)
            cells.append(f"{nm}={cm*100:4.1f}%(N={cn}) z={ (z if z is not None else 0):+.2f}")
        print(f"  {tname:28} N={sub.height:5}  " + "   ".join(cells))

    print("\nW1 LIFT verdict: needs opt15 AND apex15 z>=+2..3 vs 75+ base (non-dilutive) + monotone gradient.")
    print("If dip cohort is BELOW 75+ base on the option barriers -> NULL (the generic-up signal didn't survive option exits).")


if __name__ == "__main__":
    main()
