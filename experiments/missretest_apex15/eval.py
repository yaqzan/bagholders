"""Re-mine the MISS_CANDIDATES.md harmful cohorts on the LIVE v70 30-DTE Apex HOLD barrier.

Context: the v60 miss-analysis sweep that produced alpha_mining/MISS_CANDIDATES.md
binned harmful "tradeable" cohorts on a GENERIC barrier. The live strategy is now the
v70 Apex 30-DTE HOLD (TP+30%/SL-70%, day-15 hard-sell) = apex15 = (1.092sigma, 2.548sigma, 15d).
A cohort that looked harmful on the generic barrier may dissolve on the funded HOLD barrier
(the documented generic-vs-option trap). This script answers: which miss families STILL flag
genuinely-worse signals when measured on apex15?

Substrate: .cache/component_reweight/ledger_v70_5y.parquet  (v70 scores, 10y, barrier-agnostic
forward-path t_up/t_dn grids + weight_info traces). Read-only; no DB, no recompute.

Direction: harmful cohort = cohort WR < rest WR  => NEGATIVE two-proportion z.
A family "survives on apex15" if cohort z <= -3 on apex15 (genuinely worse -> damp-able).
If it only shows on gen15 but flattens on apex15 -> generic-vs-option trap (NOT a live residual).
"""
import os, sys
import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
from label import add_label  # noqa: E402

LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")


def two_prop_z(w1, n1, w2, n2):
    """z for (p1 - p2); negative => group1 worse. None if degenerate."""
    if n1 == 0 or n2 == 0:
        return None
    p1, p2 = w1 / n1, w2 / n2
    p = (w1 + w2) / (n1 + n2)
    se = (p * (1 - p) * (1 / n1 + 1 / n2)) ** 0.5
    if se == 0:
        return None
    return (p1 - p2) / se


def wr(series):
    s = series.drop_nulls()
    n = s.len()
    return (int(s.sum()), n, (s.sum() / n if n else None))


def main():
    df = pl.read_parquet(LEDGER)
    # funded HOLD barrier (PRIMARY) + diagnostics
    df = add_label(df, 1.092, 2.548, 15, "apex15")   # live 30-DTE Apex HOLD (TP30/SL70/15d)
    df = add_label(df, 0.901, 0.772, 15, "opt15")    # tighter option-aligned (growth-gate)
    df = add_label(df, 1.414, 3.536, 15, "gen15")    # dashboard generic WR15 (what miss-sweep ~used)

    call = df.filter(pl.col("overall") >= 75)
    print("v70 75+ CALL universe: N=%d (resolved varies by barrier)" % call.height)
    print("date span: %s -> %s\n" % (df["date"].min(), df["date"].max()))

    BARS = ["apex15", "opt15", "gen15"]
    base = {}
    for b in BARS:
        w, n, r = wr(call[b])
        base[b] = (w, n, r)
    print("75+ BASE WR:  " + "  ".join(
        "%s=%.1f%% (N=%d)" % (b, base[b][2] * 100, base[b][1]) for b in BARS) + "\n")

    # cohort defs within the 75+ CALL universe. Bin cutpoints reconstructed from the
    # v60 sweep's intent (exact cutpoints not in artifact) -> magnitudes approximate, SIGN/z is the read.
    sig = call["vol_pct"]
    vm = call["vol_mag"].fill_null(0.0)
    sig_q70, sig_q90 = sig.quantile(0.70), sig.quantile(0.90)
    vm_pos = vm.filter(vm > 0)
    vm_q66 = vm_pos.quantile(0.66) if vm_pos.len() else 1e9

    cohorts = [
        ("#1 score 75-79", (pl.col("overall") >= 75) & (pl.col("overall") <= 79)),
        ("#2 cont_lift on", pl.col("cont_lift").fill_null(0) > 0),
        ("#4 scw_dampen on", pl.col("scw_dampen").fill_null(0) > 0),
        ("#4 stoch_low (<30)", pl.col("c_stoch") < 30),
        ("#5 trend_mid [40,60]", (pl.col("c_trend") >= 40) & (pl.col("c_trend") <= 60)),
        ("#5 macd_mid [40,60]", (pl.col("c_macd") >= 40) & (pl.col("c_macd") <= 60)),
        ("#6 sigma_wide (>p70)", pl.col("vol_pct") > sig_q70),
        ("#6 sigma_vwide (>p90)", pl.col("vol_pct") > sig_q90),
        ("#7 vmag_hi (>p66,>0)", (pl.col("vol_mag").fill_null(0) > vm_q66)),
    ]

    hdr = "%-22s %6s | " % ("cohort", "N") + " | ".join(
        "%-26s" % ("%s coh/rest  z" % b) for b in BARS)
    print(hdr)
    print("-" * len(hdr))
    survivors = []
    for name, mask in cohorts:
        coh = call.filter(mask)
        rest = call.filter(~mask)
        cells = []
        apex_z = None
        gen_z = None
        for b in BARS:
            cw, cn, cr = wr(coh[b])
            rw, rn, rr = wr(rest[b])
            z = two_prop_z(cw, cn, rw, rn)
            if b == "apex15":
                apex_z = z
            if b == "gen15":
                gen_z = z
            cr_s = "%.1f" % (cr * 100) if cr is not None else "  - "
            rr_s = "%.1f" % (rr * 100) if rr is not None else "  - "
            z_s = "%+5.1f" % z if z is not None else "  -  "
            cells.append("%5s/%-5s %s" % (cr_s, rr_s, z_s))
        ncoh = wr(coh["apex15"])[1]
        print("%-22s %6d | " % (name, ncoh) + " | ".join("%-26s" % c for c in cells))
        # survives = genuinely worse on the FUNDED apex15 barrier
        if apex_z is not None and apex_z <= -3:
            survivors.append((name, apex_z, gen_z))

    print("\n=== VERDICT (apex15 = live 30-DTE Apex HOLD) ===")
    if survivors:
        print("SURVIVES on apex15 (cohort genuinely worse, z<=-3 -> live damp-able residual):")
        for n, az, gz in survivors:
            tag = " [also gen15]" if (gz is not None and gz <= -3) else " [apex-only!]"
            print("  %-22s apex15 z=%+.1f  gen15 z=%s%s" % (
                n, az, ("%+.1f" % gz if gz is not None else "n/a"), tag))
    else:
        print("  NONE clear z<=-3 on apex15. Cohorts that flag on gen15 but flatten on apex15")
        print("  = generic-vs-option trap: NOT a residual the live HOLD strategy is exposed to.")
    print("\nOut of scope here: #3 regime (needs regime-mult/state capture, not in ledger),")
    print("  #8 ichimoku-put (puts OFF in all v70 profiles), #9/#10 weekly (look-ahead blocked).")


if __name__ == "__main__":
    main()
