"""W1 cohort-z pre-flight for the trend/oscillator DIVERGENCE hypothesis.

Reads the existing holdout-locked v70 universe ledger (overall 60-99, full universe,
<=2026-05-15) from experiments/component_reweight/.cache, derives win/loss on the
Stage-1 barriers, and tests whether the 'exhaustion-masked-by-saturated-trend' CALL
cohort underperforms its tier — i.e. whether a score-stage DAMPENER is justified.

Cohort (component space, no raw-RSI needed):
  DAMP = c_trend >= TR_HI  AND  c_stoch <= ST_LO    (saturated trend + overbought stoch)
Optional tightener: c_rsi <= RSI_LO (overbought raw rsi -> low rsi component).

W1 PASS for a dampener = cohort win-rate is WORSE than the rest of the same tier at
two-proportion z <= -3 on the primary barrier (option/growth-gate WR15), with the
funded HOLD barrier not contradicting.
"""
import os, sys, math
import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
import label as L  # noqa

LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")


def zprop(w1, n1, w2, n2):
    """two-proportion z for p1-p2 (cohort vs rest). negative => cohort worse."""
    if n1 < 5 or n2 < 5:
        return None, None, None
    p1, p2 = w1 / n1, w2 / n2
    pp = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
    if se == 0:
        return p1 - p2, None, (p1, p2)
    return p1 - p2, (p1 - p2) / se, (p1, p2)


def main():
    df = pl.read_parquet(LEDGER)
    # derive barriers
    df = df.with_columns([
        L.win_expr(0.901, 0.772, 15).alias("opt15"),   # growth-gate / option-aligned WR15 (PRIMARY)
        L.win_expr(1.092, 2.548, 15).alias("apex15"),  # funded APEX TP30/SL70 HOLD-to-15
        L.win_expr(0.901, 7.07, 15).alias("hold15"),   # HOLD proxy (TP-reach, wide stop)
        L.win_expr(1.414, 3.536, 15).alias("gen15"),   # dashboard generic WR15
    ])
    print(f"ledger N={df.height}  window={df['date'].min()}..{df['date'].max()}")
    assert df["date"].max() <= "2026-05-15", "HOLDOUT LEAK"

    BARRIERS = ["opt15", "apex15", "hold15", "gen15"]

    def tier(lo, hi=99):
        return (pl.col("overall") >= lo) & (pl.col("overall") <= hi)

    # ---- base rates per tier per barrier ----
    print("\n=== BASE WR by tier (resolved only) ===")
    print(f"{'tier':8} " + " ".join(f"{b:>14}" for b in BARRIERS))
    for lo, hi, nm in [(70, 74, '70-74'), (75, 79, '75-79'), (80, 84, '80-84'), (85, 99, '85+'), (75, 99, '75+ALL')]:
        sub = df.filter(tier(lo, hi))
        cells = []
        for b in BARRIERS:
            r = sub[b].drop_nulls()
            cells.append(f"{(r.mean()*100 if r.len() else 0):5.1f}%/{r.len():<6}")
        print(f"{nm:8} " + " ".join(f"{c:>14}" for c in cells))

    # ---- cohort definitions to test (DAMPENER side, within 75+) ----
    COHORTS = {
        "DAMP trend>=90 & stoch<=15":           (pl.col("c_trend") >= 90) & (pl.col("c_stoch") <= 15),
        "DAMP trend>=90 & stoch<=15 & rsi<=40": (pl.col("c_trend") >= 90) & (pl.col("c_stoch") <= 15) & (pl.col("c_rsi") <= 40),
        "DAMP trend>=95 & stoch<=10":           (pl.col("c_trend") >= 95) & (pl.col("c_stoch") <= 10),
        "DAMP trend>=90 & stoch<=20 & rsi<=45": (pl.col("c_trend") >= 90) & (pl.col("c_stoch") <= 20) & (pl.col("c_rsi") <= 45),
        # inverse sanity: trend-confirmed (stoch NOT exhausted) should be >= rest
        "CONFIRM trend>=90 & stoch>=40":        (pl.col("c_trend") >= 90) & (pl.col("c_stoch") >= 40),
    }

    for TLO in (75, 70):
        print(f"\n=========== COHORT TESTS within overall>={TLO} ===========")
        base = df.filter(tier(TLO))
        nbase = base.height
        for cname, cmask in COHORTS.items():
            coh = base.filter(cmask)
            rest = base.filter(~cmask)
            print(f"\n  [{cname}]  cohortN={coh.height} ({100*coh.height/max(nbase,1):.1f}% of {TLO}+ tier, N={nbase})")
            for b in BARRIERS:
                cw = coh[b].drop_nulls(); rw = rest[b].drop_nulls()
                if cw.len() < 5:
                    print(f"      {b:7}: cohort resolved N={cw.len()} (too small)")
                    continue
                diff, z, (p1, p2) = zprop(int(cw.sum()), cw.len(), int(rw.sum()), rw.len())
                flag = ""
                if z is not None:
                    if z <= -3: flag = "  <-- W1 PASS (dampen)"
                    elif z <= -2: flag = "  (flag, |z|<3)"
                    elif z >= 2: flag = "  (cohort BETTER!)"
                print(f"      {b:7}: cohortWR={p1*100:5.1f}% (N={cw.len():4})  restWR={p2*100:5.1f}% (N={rw.len():5})  d={diff*100:+5.1f}pp  z={ (z if z is not None else float('nan')):+5.2f}{flag}")

    # ---- gradient: WR vs c_stoch buckets within trend>=90, 75+ (is it monotone?) ----
    print("\n=== GRADIENT: opt15 WR vs c_stoch (within c_trend>=90, overall>=75) ===")
    g = df.filter(tier(75) & (pl.col("c_trend") >= 90))
    print(f"  (c_trend>=90 & 75+ subpopulation N={g.height})")
    for lo, hi in [(0, 10), (11, 20), (21, 35), (36, 50), (51, 70), (71, 100)]:
        sub = g.filter((pl.col("c_stoch") >= lo) & (pl.col("c_stoch") <= hi))
        for b in ["opt15", "apex15", "hold15"]:
            r = sub[b].drop_nulls()
            wr = f"{r.mean()*100:4.1f}% N={r.len():4}" if r.len() else "  -      "
            print(f"    stoch[{lo:3}-{hi:3}] {b}: {wr}", end="   ")
        print()

    print("\nW1 verdict rule: ship-worthy dampener needs opt15 z<=-3 AND apex15/hold15 not contradicting.")


if __name__ == "__main__":
    main()
