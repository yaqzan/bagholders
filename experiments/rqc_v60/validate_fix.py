"""Validate the weekly look-ahead fix via the simulator (no DB writes).

Re-scores a sample of ledger symbols with the FIXED weekly (last-completed-week)
and compares to stored v60 (look-ahead current-week):
  - blast radius (|Δoverall|, % crossing the 75 boundary)
  - honest 75-79 / 75+ optTP (must still clear call BE ~45%)
  - dow-flatness of the fixed 75-79 cohort (look-ahead removed)
opt15 (30dte_opt) is score-invariant, taken from the ledger.
"""
import os, sys, json
from datetime import date
import polars as pl

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

from simulator import ScoreSimulator

LED = ".cache/rqc_v60/ledger_5y.parquet"
N_SYMS = int(os.environ.get("N_SYMS", "150"))


def main():
    led = pl.read_parquet(LED)
    syms = led["symbol"].unique().to_list()
    syms = sorted(syms)[:N_SYMS]
    print(f"re-scoring {len(syms)} symbols (5y) with FIXED weekly ...", flush=True)
    sim = ScoreSimulator(syms, lookback_days=1825)
    fixed = sim.simulate()   # {(symbol, date): overall}
    print("fixed scores:", len(fixed), flush=True)

    # build comparison frame on the ledger subset (v60 [70,89] peaks w/ opt15)
    sub = led.filter(pl.col("symbol").is_in(syms) & pl.col("opt15").is_not_null())
    recs = []
    for r in sub.iter_rows(named=True):
        d = date.fromisoformat(r["date"])
        fv = fixed.get((r["symbol"], d))
        if fv is None:
            continue
        recs.append({"v60": int(r["overall"]), "fix": int(fv), "opt15": r["opt15"],
                     "dow": d.weekday()})
    df = pl.DataFrame(recs)
    n = df.height
    print(f"\ncompared {n} v60 [70,89] peaks")
    dlt = (df["fix"] - df["v60"])
    print("blast radius: mean|Δ|=%.2f  |Δ|>=3: %.1f%%  |Δ|>=5: %.1f%%" % (
        dlt.abs().mean(), (dlt.abs() >= 3).mean() * 100, (dlt.abs() >= 5).mean() * 100))
    # boundary churn within this v60 [70,89] subset
    v75 = (df["v60"] >= 75).sum(); f75 = (df["fix"] >= 75).sum()
    cross_up = ((df["v60"] < 75) & (df["fix"] >= 75)).sum()
    cross_dn = ((df["v60"] >= 75) & (df["fix"] < 75)).sum()
    print(f"75+ count: v60={v75}  fixed={f75}  (cross_up={cross_up} cross_dn={cross_dn})")

    def tp(mask):
        s = df.filter(mask)
        return (round(s["opt15"].mean() * 100, 2) if s.height else None, s.height)

    print("\noptTP15 by tier (v60 stored vs fixed re-score; opt15 score-invariant):")
    for lo, hi, nm in [(75, 79, "75-79"), (80, 89, "80-89"), (75, 100, "75+")]:
        v = tp((pl.col("v60") >= lo) & (pl.col("v60") <= hi))
        f = tp((pl.col("fix") >= lo) & (pl.col("fix") <= hi))
        print(f"  {nm:6s}: v60 optTP={v[0]} (N={v[1]})  |  fixed optTP={f[0]} (N={f[1]})  [call BE=45]")

    print("\nFIXED 75-79 cohort optTP by dow (look-ahead removed => should be flat):")
    f7579 = df.filter((pl.col("fix") >= 75) & (pl.col("fix") <= 79))
    for d in range(5):
        s = f7579.filter(pl.col("dow") == d)
        print(f"  dow={d} N={s.height:4d} optTP={round(s['opt15'].mean()*100,2) if s.height else None}")
    print("  (contrast v60 stored 75-79 by dow:)")
    v7579 = df.filter((pl.col("v60") >= 75) & (pl.col("v60") <= 79))
    for d in [0, 4]:
        s = v7579.filter(pl.col("dow") == d)
        print(f"  dow={d} N={s.height:4d} optTP={round(s['opt15'].mean()*100,2) if s.height else None}")


if __name__ == "__main__":
    main()
