"""FRESH miss mine on the live v70 Apex HOLD barrier (apex15), from scratch.

Not the pre-selected v60 families -- scan the full feature space of v70 75+ CALL signals and
rank cohorts by how much WORSE-than-base they are on apex15 (= where the misses actually concentrate
NOW). Then anatomize the losers (did they not move = vol-path, or spike-and-reverse = timing).

base apex15 WR ~70%  => miss rate ~30%. A real "miss reason" = a bin with WR << 70% at z<=-3.
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


def z_vs_rest(coh_w, coh_n, rest_w, rest_n):
    if coh_n == 0 or rest_n == 0:
        return None
    p1, p2 = coh_w / coh_n, rest_w / rest_n
    p = (coh_w + rest_w) / (coh_n + rest_n)
    se = (p * (1 - p) * (1 / coh_n + 1 / rest_n)) ** 0.5
    return (p1 - p2) / se if se else None


def main():
    df = pl.read_parquet(LEDGER)
    df = add_label(df, 1.092, 2.548, 15, "apex15")
    call = df.filter((pl.col("overall") >= 75) & pl.col("apex15").is_not_null())
    n = call.height
    base = call["apex15"].mean()
    print("v70 75+ CALL resolved: N=%d | apex15 base WR=%.1f%% (miss rate %.1f%%)\n"
          % (n, base * 100, (1 - base) * 100))

    # ---- feature bin scan ----
    def qbins(col, qs):
        vals = call[col].drop_nulls()
        edges = sorted(set(float(vals.quantile(q)) for q in qs))
        return edges

    # (feature, list-of-(label, polars-mask))
    specs = []
    for c in ["c_trend", "c_bb", "c_rsi", "c_macd", "c_stoch", "c_ta"]:
        specs.append((c, [
            ("%s<30" % c, pl.col(c) < 30),
            ("%s 30-50" % c, (pl.col(c) >= 30) & (pl.col(c) < 50)),
            ("%s 50-70" % c, (pl.col(c) >= 50) & (pl.col(c) < 70)),
            ("%s>=70" % c, pl.col(c) >= 70),
        ]))
    # sigma & volume magnitude: quantile terciles
    for c in ["vol_pct", "vol_mag", "pre_regime", "w_adj"]:
        e = qbins(c, [1/3, 2/3])
        if len(e) == 2:
            lo, hi = e
            specs.append((c, [
                ("%s lo(<%.2f)" % (c, lo), pl.col(c) < lo),
                ("%s mid" % c, (pl.col(c) >= lo) & (pl.col(c) < hi)),
                ("%s hi(>=%.2f)" % (c, hi), pl.col(c) >= hi),
            ]))
    # overall sub-band
    specs.append(("overall", [
        ("75-79", (pl.col("overall") >= 75) & (pl.col("overall") <= 79)),
        ("80-84", (pl.col("overall") >= 80) & (pl.col("overall") <= 84)),
        ("85+", pl.col("overall") >= 85),
    ]))
    # mechanism on/off flags
    for c in ["scw_dampen", "cont_lift", "mcd_dampen", "ich_call_dampen",
              "cwcf_dampen", "cwwd_dampen", "mis_stress"]:
        specs.append((c, [("%s on" % c, pl.col(c).fill_null(0).abs() > 1e-9)]))
    # volume signal categories
    vs_vals = [v for v in call["vol_signal"].unique().to_list() if v]
    specs.append(("vol_signal", [("vs=%s" % v, pl.col("vol_signal") == v) for v in vs_vals]))

    rows = []
    for feat, bins in specs:
        for label, mask in bins:
            coh = call.filter(mask)
            cn = coh.height
            if cn < 80:
                continue
            cw = int(coh["apex15"].sum())
            rest = call.filter(~mask)
            rw, rn = int(rest["apex15"].sum()), rest.height
            z = z_vs_rest(cw, cn, rw, rn)
            rows.append((label, cn, cw / cn, z))

    rows.sort(key=lambda r: (r[3] if r[3] is not None else 0))
    print("=== WORST cohorts (most negative z = where misses concentrate), N>=80 ===")
    print("%-26s %6s %7s %7s" % ("cohort", "N", "WR%", "z"))
    for label, cn, wr, z in rows[:12]:
        flag = "  <-- z<=-3 REAL" if (z is not None and z <= -3) else ""
        print("%-26s %6d %6.1f%% %+6.1f%s" % (label, cn, wr * 100, z if z else 0, flag))
    print("\n=== BEST cohorts (most positive z = where wins concentrate) ===")
    for label, cn, wr, z in rows[-6:][::-1]:
        print("%-26s %6d %6.1f%% %+6.1f" % (label, cn, wr * 100, z if z else 0))

    nreal = sum(1 for _, _, _, z in rows if z is not None and z <= -3)
    print("\n# cohorts with z<=-3 (real miss concentration): %d / %d scanned" % (nreal, len(rows)))

    # ---- loser anatomy: WHY do the 30% lose? ----
    losers = call.filter(pl.col("apex15") == 0)
    winners = call.filter(pl.col("apex15") == 1)
    print("\n=== LOSER ANATOMY (N=%d losers) ===" % losers.height)
    # apex15 TP = 1.092 sigma. how close did losers' best favorable excursion (mfe15, in sigma) get?
    for lab, lo, hi in [("never moved up (mfe<0.3sig)", -1, 0.3),
                        ("partial (0.3-0.7)", 0.3, 0.7),
                        ("close-miss (0.7-1.092=TP)", 0.7, 1.092),
                        ("reached TP but lost (mfe>=1.092)", 1.092, 99)]:
        m = (pl.col("mfe15") >= lo) & (pl.col("mfe15") < hi)
        c = losers.filter(m).height
        print("  %-34s %5d  (%4.1f%% of losers)" % (lab, c, 100 * c / max(1, losers.height)))
    print("  loser  median mfe15=%.2fsig  mae15=%.2fsig" %
          (losers["mfe15"].median(), losers["mae15"].median()))
    print("  winner median mfe15=%.2fsig  mae15=%.2fsig" %
          (winners["mfe15"].median(), winners["mae15"].median()))
    print("\n  apex15 SL = 2.548sig. losers reaching the -70%% SL (mae15>=2.548): %d (%.1f%%)" %
          (losers.filter(pl.col("mae15") >= 2.548).height,
           100 * losers.filter(pl.col("mae15") >= 2.548).height / max(1, losers.height)))


if __name__ == "__main__":
    main()
