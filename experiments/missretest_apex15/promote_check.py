"""70-74 PROMOTION/admission check on the live v70 Apex HOLD barrier (apex15).

The damp side is closed (no 75+ cohort is identifiably bad). This is the upside side: are there
70-74 CALL sub-cohorts whose apex15 WR reaches the TRADEABLE bar (75-79 tier ~= 69.8% / 75+ base
70.0%) at meaningful N, with a clean discriminator so they could be lifted SELECTIVELY (gradient
admission) rather than promoting the whole band?

Bars: 75+ base and 75-79 tier WR on apex15. A 70-74 cohort 'deserves admission' if apex15 WR >= bar
at z>=+2 vs the 70-74-rest (discriminator exists) AND not-worse-than the 75+ population.

CAVEATS (do not skip): (1) holdout window already excluded at ledger build; (2) v61 history -- 70-74
promoters looked +7pp in isolation but degraded 80+ WR15 ~2.8pp + made broad score deltas when wired;
(3) a real ship needs the full Stage-1 W1-W6 gate + active-DB validation, not this screen.
Read-only over .cache/component_reweight/ledger_v70_5y.parquet.
"""
import os, sys
import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
from label import add_label  # noqa: E402

LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")


def zp(w1, n1, w2, n2):
    if not n1 or not n2:
        return None
    p1, p2 = w1 / n1, w2 / n2
    p = (w1 + w2) / (n1 + n2)
    se = (p * (1 - p) * (1 / n1 + 1 / n2)) ** 0.5
    return (p1 - p2) / se if se else None


def main():
    df = pl.read_parquet(LEDGER)
    df = add_label(df, 1.092, 2.548, 15, "apex15").filter(pl.col("apex15").is_not_null())
    tradeable = df.filter(pl.col("overall") >= 75)
    tier7579 = df.filter((pl.col("overall") >= 75) & (pl.col("overall") <= 79))
    band = df.filter((pl.col("overall") >= 70) & (pl.col("overall") <= 74))
    bar75 = tradeable["apex15"].mean()
    bar7579 = tier7579["apex15"].mean()
    bw, bn = int(band["apex15"].sum()), band.height
    print("apex15 bars:  75+ base=%.1f%% (N=%d)  |  75-79 tier=%.1f%% (N=%d)" % (
        bar75 * 100, tradeable.height, bar7579 * 100, tier7579.height))
    print("70-74 BAND baseline: %.1f%% (N=%d)  <- the overflow tier\n" % (bw / bn * 100, bn))

    # feature cohorts within 70-74
    specs = []
    for c in ["c_trend", "c_bb", "c_rsi", "c_macd", "c_stoch", "c_ta"]:
        specs += [("%s<30" % c, pl.col(c) < 30), ("%s 30-50" % c, (pl.col(c) >= 30) & (pl.col(c) < 50)),
                  ("%s 50-70" % c, (pl.col(c) >= 50) & (pl.col(c) < 70)), ("%s>=70" % c, pl.col(c) >= 70)]
    for c in ["vol_pct", "vol_mag", "pre_regime", "w_adj"]:
        v = band[c].drop_nulls()
        lo, hi = float(v.quantile(1/3)), float(v.quantile(2/3))
        specs += [("%s lo" % c, pl.col(c) < lo), ("%s hi" % c, pl.col(c) >= hi)]
    for c in ["cont_lift", "scw_dampen", "mcd_dampen", "ich_call_dampen", "cwcf_dampen", "mis_stress"]:
        specs.append(("%s on" % c, pl.col(c).fill_null(0).abs() > 1e-9))
    vs_vals = [v for v in band["vol_signal"].unique().to_list() if v]
    specs += [("vs=%s" % v, pl.col("vol_signal") == v) for v in vs_vals]

    rows = []
    for label, mask in specs:
        coh = band.filter(mask)
        cn = coh.height
        if cn < 80:
            continue
        cw = int(coh["apex15"].sum())
        rest = band.filter(~mask)
        z_band = zp(cw, cn, int(rest["apex15"].sum()), rest.height)   # discriminator within 70-74
        z_vs75 = zp(cw, cn, int(tradeable["apex15"].sum()), tradeable.height)  # vs tradeable pop
        rows.append((label, cn, cw / cn, z_band, z_vs75))

    rows.sort(key=lambda r: -r[2])   # best WR first
    print("Top 70-74 cohorts by apex15 WR (N>=80):")
    print("%-22s %6s %7s %10s %10s  %s" % ("cohort", "N", "WR%", "z_vs_band", "z_vs_75+", "verdict"))
    print("-" * 78)
    promotable = []
    for label, cn, wr, zb, z75 in rows[:14]:
        # 'promotable' = reaches the tradeable bar AND has a clean discriminator vs the band
        ok = (wr >= bar7579) and (zb is not None and zb >= 2.0)
        verdict = "PROMOTABLE" if ok else ("at-bar" if wr >= bar7579 else "")
        if ok:
            promotable.append((label, cn, wr, zb, z75))
        print("%-22s %6d %6.1f%% %+9.1f %+9.1f  %s" % (
            label, cn, wr * 100, zb if zb else 0, z75 if z75 else 0, verdict))

    print("\n=== VERDICT (70-74 admission side) ===")
    if promotable:
        print("Candidate 70-74 cohorts at tradeable quality WITH a clean within-band discriminator:")
        for label, cn, wr, zb, z75 in promotable:
            print("  %-22s N=%d  apex15=%.1f%%  z_vs_band=%+.1f  z_vs_75+=%+.1f" % (
                label, cn, wr * 100, zb, z75))
        print("\n  -> candidates ONLY. Next: confirm discriminator is not a holdout/selection artifact,")
        print("     then full Stage-1 W1-W6 + active-DB (v61 lesson: watch 80+ WR15 + score-delta spread).")
    else:
        print("  NONE reach the 75-79 tier bar (%.1f%%) with a clean within-band discriminator (z>=+2)."
              % (bar7579 * 100))
        print("  The 70-74 band is uniformly sub-tradeable in feature space -> no selective-lift target.")
        print("  (Consistent with: directional score has ~no edge; 70-74 already traded as flat overflow.)")


if __name__ == "__main__":
    main()
