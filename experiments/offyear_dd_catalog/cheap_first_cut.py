"""
CHEAP decisive first cut (G23: cheap test before building anything).
Off-year (2021/2023/2025/2018) drawdown driver = the −90% dead-hold-EXPIRY bags.
Two questions that gate the whole sector-clustering hypothesis:
 (1) Do the bags CONCENTRATE by sector BEYOND the book's (tech-heavy) baseline mix?
 (2) Do the bags CLUSTER TEMPORALLY (same entry/exit weeks = a correlated crash)?
If both NO -> sector-clustering is a non-starter, stop (don't build concurrency).
Pure tape mining + Stock.sector join. NO concurrency reconstruction yet.
"""
import sys
from pathlib import Path
import numpy as np, polars as pl
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from database.models.core import Stock

TAPE = ROOT / ".cache" / "dd_ledger"
sec = {s.symbol: (s.sector or "none") for s in Stock.select(Stock.symbol, Stock.sector)}
YEARS = ["2021", "2022", "2023", "2024", "2025"]

def load(y):
    df = pl.read_parquet(TAPE / f"tape_{y}.parquet")
    return df.with_columns(
        pl.col("sym_id").replace_strict(sec, default="none").alias("sector"),
        pl.col("entry_date").str.strptime(pl.Date, "%Y-%m-%d", strict=False).alias("ed"),
        pl.col("exit_date").str.strptime(pl.Date, "%Y-%m-%d", strict=False).alias("xd"),
    )

def herf(shares):  # Herfindahl concentration (0..1, higher=concentrated)
    return float(sum(s*s for s in shares))

print("\n=== (1) sector concentration: dh_expiry BAGS vs ALL trades (per year) ===")
print("    bag = dh_expiry (the −90% drawdown drivers). Δshare = bag_share − book_share.\n")
for y in YEARS:
    df = load(y)
    allc = df.group_by("sector").len().rename({"len":"n_all"})
    bags = df.filter(pl.col("outcome")=="dh_expiry").group_by("sector").len().rename({"len":"n_bag"})
    j = allc.join(bags, on="sector", how="left").fill_null(0)
    tot_all = j["n_all"].sum(); tot_bag = j["n_bag"].sum()
    j = j.with_columns((pl.col("n_all")/tot_all).alias("book_sh"),
                       (pl.col("n_bag")/max(tot_bag,1)).alias("bag_sh"))
    j = j.with_columns((pl.col("bag_sh")-pl.col("book_sh")).alias("d")).sort("d",descending=True)
    H_book = herf(j["book_sh"].to_list()); H_bag = herf(j["bag_sh"].to_list())
    top = "  ".join(f"{r['sector'][:10]}:{100*r['bag_sh']:.0f}%(Δ{100*r['d']:+.0f})"
                    for r in j.head(3).iter_rows(named=True))
    print(f"  {y}: bags={tot_bag:5}  Herf book={H_book:.3f} -> bag={H_bag:.3f}  | top bag-sectors: {top}")

print("\n  (if bag Herf >> book Herf, the losses concentrate in a sector beyond its book share)")

print("\n=== (2) temporal clustering: are the bags' ENTRY dates clustered (correlated crash)? ===")
print("    metric: of the bags (one representative seed), what % entered in the single worst week,")
print("    and top-3 weeks' share. High = a few entry-weeks seeded most of the crash.\n")
for y in YEARS:
    df = load(y)
    one = df.filter((pl.col("seed")==df["seed"][0]) & (pl.col("outcome")=="dh_expiry"))
    if one.height < 10:
        print(f"  {y}: too few bags ({one.height})"); continue
    one = one.with_columns(pl.col("ed").dt.strftime("%Y-%W").alias("wk"))
    wk = one.group_by("wk").len().sort("len",descending=True)
    nb = one.height
    top1 = 100*wk["len"][0]/nb; top3 = 100*wk["len"].head(3).sum()/nb
    nwk = wk.height
    print(f"  {y}: bags(1seed)={nb:4}  entry-weeks={nwk:3}  worst-week={top1:.0f}%  top3-weeks={top3:.0f}%")

print("\n  baseline for comparison: if bags spread evenly over ~50 weeks, worst-week ~ a few %.")
print("  high worst-week/top3 share => bags entered in a burst => correlated-crash signature.")
