"""
Was 2024 detectable EARLY, or only obvious in hindsight?  -> the reverse-engineer crux.
Split each year H1 (Jan-Jun) vs H2 (Jul-Dec) on the key drivers (TP%, catastrophic-bag rate,
mean pnl). If 2024-H1 already looked elevated vs 2021/2023/2025-H1, there's a (weak) real-time
tell. If it only diverged in H2, the factor is exogenous and unpredictable in advance.
"""
import sys
from pathlib import Path
import polars as pl
ROOT = Path(__file__).resolve().parents[2]
TAPE = ROOT / ".cache" / "dd_ledger"
YEARS = ["2021", "2022", "2023", "2024", "2025"]

print("\n=== H1 vs H2 driver split (was the 2024 regime visible by mid-year?) ===\n")
print(" year-half | TP%   bag%(<-0.7)  meanPnl  $-wtd   entry_dd  | trades/seed")
print(" " + "-"*78)
for y in YEARS:
    f = TAPE / f"tape_{y}.parquet"
    if not f.exists(): continue
    df = pl.read_parquet(f).with_columns(
        pl.col("entry_date").str.strptime(pl.Date, "%Y-%m-%d", strict=False).alias("ed"))
    ns = df["seed"].n_unique()
    for half, lo, hi in [("H1", 1, 6), ("H2", 7, 12)]:
        d = df.filter((pl.col("ed").dt.month() >= lo) & (pl.col("ed").dt.month() <= hi))
        n = d.height
        if n == 0: continue
        tp = 100*d.filter(pl.col("outcome")=="tp").height/n
        bag = 100*d.filter(pl.col("option_pnl")<-0.7).height/n
        mp = d["option_pnl"].mean()
        dw = (d["option_pnl"]*d["premium_cost"]).sum()/d["premium_cost"].sum()
        edd = d["entry_dd"].mean()
        print(f" {y}-{half}    | {tp:4.1f}  {bag:8.1f}   {mp:+6.3f}  {dw:+6.3f}  {edd:7.3f}  | {n/ns:6.0f}")
    print()
