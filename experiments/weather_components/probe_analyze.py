"""TA-suppressor probe decomposition: does retiring TA (W_TA=0) accrete the funded book?

Compares two ScoreSimulator re-score arms on the SAME symbol sample (apples-to-apples,
controls sim-vs-stored drift):
  base = v74 as-is ; zero = W_TA_BASE=0 W_TA_SLOPE=0 (TA retired).
For the >=75 traded set on the 30dte_apex payoff: supply Δ, and ADDED/REMOVED/COMMON apex-EV,
pooled + per-window. A retirement EARNS its keep iff REMOVED EV << COMMON EV (it drops the worse
signals) AND supply is roughly held; it's a reweight-trap if supply collapses.

Usage: probe_analyze.py [base.parquet] [zero.parquet]
"""
import os
import sys
from collections import namedtuple
from datetime import date as _date

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.barrier_cache import peaks_to_swing_results

EV = {"win": +0.30, "stop": -0.70, "expire": -0.40}
BASE = sys.argv[1] if len(sys.argv) > 1 else ".cache/weather_components/ta_base.parquet"
ZERO = sys.argv[2] if len(sys.argv) > 2 else ".cache/weather_components/ta_zero.parquet"


def evmap(df):
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(df["symbol"], df["date"])]
    res, _ = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    m = {}
    for r in res:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get("30d")
        if sw and sw.get("result") in EV:
            m[(r["symbol"], dk)] = EV[sw["result"]]
    return m


def evs(df, m):
    a = [m[(s, d)] for s, d in zip(df["symbol"], df["date"]) if (s, d) in m]
    return (len(a), (np.mean(a) * 100 if a else float("nan")))


def main():
    b = pl.read_parquet(BASE).select(["symbol", "date", "overall"]).rename({"overall": "ovb"})
    z = pl.read_parquet(ZERO).select(["symbol", "date", "overall"]).rename({"overall": "ovz"})
    df = b.join(z, on=["symbol", "date"], how="full", coalesce=True).with_columns(
        ovb=pl.col("ovb").fill_null(0), ovz=pl.col("ovz").fill_null(0),
        yr=pl.col("date").str.slice(0, 4),
    )
    print(f"base rows {b.height:,} | zero rows {z.height:,} | union {df.height:,}")
    print(f"base >=75 supply {df.filter(pl.col('ovb')>=75).height:,} | "
          f"zero >=75 supply {df.filter(pl.col('ovz')>=75).height:,} "
          f"(Δ {(df.filter(pl.col('ovz')>=75).height/max(1,df.filter(pl.col('ovb')>=75).height)-1)*100:+.1f}%)")
    rel = df.filter((pl.col("ovb") >= 75) | (pl.col("ovz") >= 75))
    m = evmap(rel)
    print(f"apex-matched {len(m):,}/{rel.height:,}\n")

    def block(d, tag):
        common = d.filter((pl.col("ovb") >= 75) & (pl.col("ovz") >= 75))
        added = d.filter((pl.col("ovz") >= 75) & (pl.col("ovb") < 75))
        removed = d.filter((pl.col("ovb") >= 75) & (pl.col("ovz") < 75))
        nc, ec = evs(common, m); na, ea = evs(added, m); nr, er = evs(removed, m)
        verdict = ""
        if nr and nc:
            verdict = "  ACCRETIVE (removed worse than common)" if er < ec - 0.3 else (
                "  DILUTIVE (removed >= common -> dropping good ones)" if er > ec + 0.3 else "  ~neutral removal")
        print(f"  [{tag:>5}] COMMON N={nc:,} EV={ec:+.2f}% | REMOVED N={nr:,} EV={er:+.2f}% | "
              f"ADDED N={na:,} EV={ea:+.2f}%{verdict}")

    block(rel, "FULL")
    for y in ["2021", "2022", "2023", "2024", "2025"]:
        block(rel.filter(pl.col("yr") == y), y)
    print("\nREAD: ACCRETIVE + supply ~held => TA-retire is a live v75-lean candidate (-> full W/apex gates).")
    print("      supply collapse OR removed>=common => reweight-trap / dilutive => STAGE-null. In-sample.")


if __name__ == "__main__":
    main()
