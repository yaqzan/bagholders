"""W1 cohort-z pre-flight for the Stoch-overbought CONVICTION amplification cap (VSG).

Hypothesis: CONVICTION-amplified, momentum-overbought (low stoch SCORE) 75+ CALLs do not
earn the volume boost — at their boosted score they underperform. If true, capping the
amplification removes empty inflation (and the ATI-class intraday CONVICTION<->THIN_AIR
whiplash) without costing per-trade edge.

Read-only on the barrier-agnostic 10y v70 ledger (holdout-locked at 2026-05-15).
GATE: targeted cohort (CONVICTION x overbought) WR15 below comparison at z>=+3.
"""
import os, sys, math
os.environ.setdefault("PYTHONIOENCODING", "utf-8"); os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for p in (_ROOT, os.path.join(_ROOT, "experiments", "component_reweight")):
    if p not in sys.path:
        sys.path.insert(0, p)
import polars as pl
from label import add_label
from experiments._holdout import assert_no_holdout_leak

LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")
BARRIERS = [("apex15", 1.092, 2.548, 15), ("opt15", 0.901, 0.772, 15),
            ("gen15", 1.414, 3.536, 15)]


def ztest(w1, n1, w2, n2):
    """z for (p2 - p1): positive => group1 WORSE (lower WR) than group2."""
    if n1 == 0 or n2 == 0:
        return None
    p1, p2 = w1 / n1, w2 / n2
    pool = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2))
    return None if se == 0 else (p2 - p1) / se


def wr(df, col):
    s = df[col].drop_nulls()
    return (s.sum(), s.len(), round(100 * s.mean(), 1) if s.len() else None)


def main():
    df = pl.read_parquet(LEDGER)
    assert_no_holdout_leak(df, context="vsg_w1")
    for name, tp, sl, w in BARRIERS:
        df = add_label(df, tp, sl, w, name)
    df = df.with_columns(pl.col("date").str.slice(0, 4).cast(pl.Int32).alias("yr"))
    print("ledger window:", df["date"].min(), "->", df["date"].max(), "N=", df.height)

    for window_lbl, yr_min in [("10y", 0), ("5y", 2021), ("3y", 2023)]:
        d = df.filter((pl.col("overall") >= 75) & (pl.col("yr") >= yr_min))
        conv = d.filter(pl.col("vol_signal") == "CONVICTION")
        print("\n================ %s  (75+ calls, N=%d ; CONVICTION N=%d) ================"
              % (window_lbl, d.height, conv.height))
        for lo in (10, 15, 20):
            ob = conv.filter(pl.col("c_stoch") <= lo)
            nonob = conv.filter(pl.col("c_stoch") > lo)
            thin_ob = d.filter((pl.col("vol_signal") == "THIN_AIR") & (pl.col("c_stoch") <= lo))
            print("  --- overbought def: c_stoch <= %d  (CONV-OB N=%d, CONV-nonOB N=%d, THINAIR-OB N=%d) ---"
                  % (lo, ob.height, nonob.height, thin_ob.height))
            for name, *_ in BARRIERS:
                w_ob, n_ob, p_ob = wr(ob, name)
                w_no, n_no, p_no = wr(nonob, name)
                w_th, n_th, p_th = wr(thin_ob, name)
                z = ztest(w_ob, n_ob, w_no, n_no)  # +z => CONV-OB worse than CONV-nonOB
                zt = ztest(w_ob, n_ob, w_th, n_th)  # +z => CONV-OB worse than THINAIR-OB
                print("      %-7s  CONV-OB=%s%% (N=%d) | CONV-nonOB=%s%% (N=%d) z=%s | THINAIR-OB=%s%% (N=%d) z(vs thin)=%s"
                      % (name, p_ob, n_ob, p_no, n_no,
                         ("%.2f" % z if z is not None else "na"),
                         p_th, n_th, ("%.2f" % zt if zt is not None else "na")))


if __name__ == "__main__":
    main()
