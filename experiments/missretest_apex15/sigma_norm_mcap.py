"""sigma-normalization check for mcap-conditional barriers (Lead #17 / Priority #16).

The Apex TP/SL are ALREADY scaled by each stock's own 60-day realized daily vol (sigma).
A per-mcap barrier only adds value if, IN SIGMA UNITS, small/high-vol names still have a
materially different forward-path shape than large caps. If mfe15_sig / mae15_sig are flat
across mcap bins -> sigma-scaling already neutralizes mcap -> per-mcap barriers add nothing (CLOSE).
If micro-caps have systematically different sigma-normalized excursions -> structural reason exists.

Read: component_reweight v70 ledger (mfe15/mae15 are in sigma units) JOIN Stock.market_cap.
"""
import os, sys
import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
from label import add_label  # noqa: E402
from database.models.core import Stock  # noqa: E402

LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")
TP_SIG, SL_SIG = 1.092, 2.548   # apex +30% / -70%


def main():
    df = pl.read_parquet(LEDGER)
    df = add_label(df, TP_SIG, SL_SIG, 15, "apex15")
    df = df.filter(pl.col("overall") >= 75)

    # static current market cap per symbol (same source MCD uses; today's mcap as proxy)
    rows = list(Stock.select(Stock.symbol, Stock.market_cap))
    mc = {r.symbol: float(r.market_cap) for r in rows if r.market_cap}
    # detect units -> billions
    sample = np.median([v for v in mc.values() if v > 0]) if mc else 0
    div = 1e9 if sample > 1e6 else 1.0
    mcap_b = {k: v / div for k, v in mc.items()}
    df = df.with_columns(
        pl.col("symbol").replace_strict(mcap_b, default=None).alias("mcap_b"))
    df = df.filter(pl.col("mcap_b").is_not_null() & (pl.col("mcap_b") > 0))
    print("75+ CALL with mcap: N=%d (of universe; ETFs/no-mcap dropped)\n" % df.height)

    bins = [("micro <2B", 0, 2), ("small 2-10B", 2, 10), ("mid 10-50B", 10, 50),
            ("large 50-200B", 50, 200), ("xl 200B-1T", 200, 1000), ("mega 1T+", 1000, 9e9)]

    print("KEY: if mfe15_sig & mae15_sig are FLAT across rows, sigma-scaling already handles mcap")
    print("     -> per-mcap barriers add NOTHING. (raw %% move differs by mcap; sigma move is the test.)\n")
    print("%-15s %6s %7s | %9s %9s | %8s %8s | %8s" % (
        "mcap bin", "N", "apexWR", "mfe15sig", "mae15sig", "%hitTP", "%hitSL", "mfe/mae"))
    print("-" * 92)
    base_mfe = df["mfe15"].median()
    base_mae = df["mae15"].median()
    for lab, lo, hi in bins:
        sub = df.filter((pl.col("mcap_b") >= lo) & (pl.col("mcap_b") < hi))
        n = sub.height
        if n < 50:
            print("%-15s %6d  (N<50, skip)" % (lab, n)); continue
        wr = sub["apex15"].drop_nulls().mean()
        mfe = sub["mfe15"].median()
        mae = sub["mae15"].median()
        hit_tp = (sub["mfe15"] >= TP_SIG).mean()
        hit_sl = (sub["mae15"] >= SL_SIG).mean()
        print("%-15s %6d %6.1f%% | %9.2f %9.2f | %7.1f%% %7.1f%% | %8.2f" % (
            lab, n, wr * 100, mfe, mae, hit_tp * 100, hit_sl * 100, mfe / mae if mae else 0))

    # spread test: micro vs large+ on the sigma-normalized excursions
    micro = df.filter(pl.col("mcap_b") < 2)
    big = df.filter(pl.col("mcap_b") >= 50)
    print("\n=== sigma-normalized spread (the decision) ===")
    for nm, col in [("mfe15_sig", "mfe15"), ("mae15_sig", "mae15")]:
        mm, bm = micro[col].median(), big[col].median()
        print("  %-10s  micro=%.2f  large+=%.2f  ratio=%.2f  (>~1.15 => structural; ~1.0 => neutralized)"
              % (nm, mm, bm, mm / bm if bm else 0))
    mw = micro["apex15"].drop_nulls().mean(); bw = big["apex15"].drop_nulls().mean()
    print("  apex15 WR  micro=%.1f%%  large+=%.1f%%  (gap is admission/MCD turf, not barrier turf)"
          % (mw * 100, bw * 100))


if __name__ == "__main__":
    main()
