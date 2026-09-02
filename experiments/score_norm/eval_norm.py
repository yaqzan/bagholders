"""Shared eval helper for the score-normalization workflow agents.
Centralizes barrier derivation (OPTION-primary, the gate that matters) + base rates + z-test,
so every agent measures the SAME way and can't drift to the gen15 generic-barrier trap.

Usage:
    from eval_norm import load, BASE75, zprop, wr, cohort_vs_base
    df = load()                      # polars df with opt15/apex15/hold15/gen15 derived
    # df cols: symbol,date,overall,ps_z,ps_pctile,xs_pctile,n_prior,vol_pct,+ 4 barrier labels
"""
import os, sys, math
import polars as pl
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
import label as L  # noqa

LEDGER = os.path.join(_ROOT, ".cache", "score_norm", "norm_ledger_v70.parquet")

# barriers (sigma TP, sigma SL, max-hold W). opt15/apex15 are PRIMARY (what the strategy trades);
# gen15 is the wide dashboard barrier — DO NOT gate on gen15 (the SVD/v42 generic-vs-option trap).
BARRIERS = [("opt15", 0.901, 0.772, 15), ("apex15", 1.092, 2.548, 15),
            ("hold15", 0.901, 7.07, 15), ("gen15", 1.414, 3.536, 15)]

# 75+ ABSOLUTE call base rates (measured from the real 60-99 ledger, the bar a new signal must clear):
BASE75 = {"opt15": 0.475, "apex15": 0.700, "hold15": 0.789, "gen15": 0.663}
# break-evens: opt15/apex are option-aligned; a NEW signal set is NON-DILUTIVE if it MATCHES base,
# ALPHA-ADDING if it beats base at z>=3 on opt15 AND apex15, DILUTIVE if below (the v42 trap).


def load():
    df = pl.read_parquet(LEDGER)
    return df.with_columns([L.win_expr(tp, sl, w).alias(nm) for nm, tp, sl, w in BARRIERS])


def wr(series):
    r = series.drop_nulls()
    return int(r.sum()) if r.len() else 0, r.len(), (r.mean() if r.len() else None)


def zprop(w1, n1, w2, n2):
    """two-proportion z for p1-p2. positive => group1 better than group2."""
    if n1 < 5 or n2 < 5:
        return None, None
    p1, p2 = w1 / n1, w2 / n2
    pp = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
    return (p1 - p2), ((p1 - p2) / se if se else None)


def cohort_vs_base(df, mask, label=""):
    """Print a cohort's WR on every barrier vs the 75+ absolute base + two-prop z."""
    sub = df.filter(mask)
    print(f"  [{label}] N={sub.height}")
    out = {}
    for nm, *_ in BARRIERS:
        w, n, m = wr(sub[nm])
        if n < 5:
            print(f"      {nm:7}: resolvedN={n} too small"); out[nm] = None; continue
        bw = round(BASE75[nm] * n)
        d, z = zprop(w, n, bw, n)
        tag = ""
        if nm in ("opt15", "apex15") and z is not None:
            if z >= 3: tag = "  <-- ALPHA (beats base)"
            elif z >= -1.5: tag = "  (~non-dilutive)"
            elif z <= -3: tag = "  <-- DILUTIVE (v42 trap)"
        print(f"      {nm:7}: WR={m*100:5.1f}% (N={n:6}) vs base {BASE75[nm]*100:.1f}%  d={d*100:+5.1f}pp z={ (z if z is not None else float('nan')):+5.2f}{tag}")
        out[nm] = (m, n, z)
    return out


if __name__ == "__main__":
    df = load()
    print("ledger N=", df.height, "window", df["date"].min(), "..", df["date"].max())
    print("abs>=75 in ledger:", int((df["overall"] >= 75).sum()),
          " ps_z>=1.5 & abs<75:", int(((df["ps_z"] >= 1.5) & (df["overall"] < 75)).sum()))
