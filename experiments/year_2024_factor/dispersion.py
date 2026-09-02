"""
Is 2024's edge INDEX-level (beta) or SINGLE-STOCK (dispersion/momentum-persistence)?
2023 ~= 2024 at the index level but 97x apart at the strategy level -> the differentiator
is sub-index. Quantify it: per-year, how concentrated is the strategy's pnl across names,
and which momentum leaders drove it. Pure tape mining (one seed -> dedup names).
"""
import sys
from pathlib import Path
import polars as pl
ROOT = Path(__file__).resolve().parents[2]
TAPE = ROOT / ".cache" / "dd_ledger"
YEARS = ["2021", "2022", "2023", "2024", "2025"]

print("\n=== 2024 IT-factor: single-stock dispersion of the 75+ cohort (per-symbol pnl) ===\n")
print("Per year: collapse to one seed, aggregate realized option_pnl by symbol.")
print("'follow-through' = mean pnl across the names the scorer actually picked.\n")
hdr = "year | n_names  meanPnl  | top10 names by total realized pnl-contribution (sym:meanPnl xN)"
print(hdr); print("-"*100)
for y in YEARS:
    f = TAPE / f"tape_{y}.parquet"
    if not f.exists(): continue
    df = pl.read_parquet(f)
    one = df.filter(pl.col("seed") == df["seed"][0])          # single seed = one realization
    g = (one.group_by("sym_id")
            .agg(pl.len().alias("n"), pl.col("option_pnl").mean().alias("mpnl"),
                 pl.col("option_pnl").sum().alias("tot"))
            .sort("tot", descending=True))
    n_names = g.height
    mean_all = one["option_pnl"].mean()
    top = g.head(10)
    names = "  ".join(f"{r['sym_id']}:{r['mpnl']:+.2f}x{r['n']}" for r in top.iter_rows(named=True))
    print(f"{y} | {n_names:6d}  {mean_all:+6.3f}  | {names}")

print("\n=== concentration: how much of total positive pnl comes from the top names? ===\n")
print(" year |  top1%   top5%   top10%  of total realized pnl  (higher = narrower/concentrated) | gini")
print(" " + "-"*95)
for y in YEARS:
    f = TAPE / f"tape_{y}.parquet"
    if not f.exists(): continue
    df = pl.read_parquet(f)
    # use ALL seeds for a stable per-symbol total
    g = (df.group_by("sym_id").agg(pl.col("option_pnl").sum().alias("tot"))
           .sort("tot", descending=True))
    tots = g["tot"].to_list()
    pos = [t for t in tots if t > 0]; total = sum(pos)
    n = len(pos)
    def share(frac):
        k = max(1, int(n*frac)); return 100*sum(pos[:k])/total if total else 0
    # gini of positive contributions
    s = sorted(pos); cum = 0; areas = 0
    for i, v in enumerate(s, 1): cum += v; areas += cum
    gini = (n + 1 - 2*areas/total)/n if total else 0
    print(f" {y}  | {share(0.01):6.1f} {share(0.05):6.1f} {share(0.10):7.1f}                                        | {gini:.3f}")
