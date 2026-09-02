"""
Decisive mechanism test for a SECTOR-CONCENTRATION exposure cap:
 (A) reconstruct SAME-SECTOR concurrent open positions at each trade's entry (sampled seeds);
 (B) is high same-sector-concurrency a low-EV / high-bag cohort BEYOND total concurrency?
     (G22: TOTAL concurrency DD is already captured by MAX_POS+levers — a sector cap only adds
      value if SAME-SECTOR crowding predicts losses beyond total crowding.)
 (C) exit-date (synchronized-death) clustering: do bags DIE in the same weeks (correlated crash)?
If sector_concur adds nothing beyond total_concur -> sector cap is redundant -> NULL.
"""
import sys
from pathlib import Path
import numpy as np, polars as pl
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from database.models.core import Stock
TAPE = ROOT / ".cache" / "dd_ledger"
sec = {s.symbol: (s.sector or "none") for s in Stock.select(Stock.symbol, Stock.sector)}
YEARS = ["2021", "2023", "2025", "2024"]          # off-years + 2024 contrast
N_SEEDS = 20

def ols_t(X, y):
    b = np.linalg.solve(X.T@X, X.T@y); r = y - X@b
    s2 = (r@r)/(len(y)-X.shape[1]); se = np.sqrt(np.diag(s2*np.linalg.inv(X.T@X)))
    return b, b/se
def z(a): a=np.asarray(a,float); return (a-np.nanmean(a))/(np.nanstd(a) or 1.0)

def same_sector_concur(seed_df):
    """for each trade, count OTHER open same-sector positions at its entry (entry_o<=entry<exit_o)."""
    rows = seed_df.select(["ed","xd","sector"]).to_dicts()
    out = []
    for i, t in enumerate(rows):
        e = t["ed"]; s = t["sector"]
        c = 0
        for j, o in enumerate(rows):
            if j == i: continue
            if o["sector"] == s and o["ed"] <= e < o["xd"]:
                c += 1
        out.append(c)
    return out

print("\n=== (A)/(B) same-sector concurrency: is it a low-EV/high-bag cohort beyond TOTAL concurrency? ===\n")
for y in YEARS:
    df = pl.read_parquet(TAPE / f"tape_{y}.parquet").with_columns(
        pl.col("sym_id").replace_strict(sec, default="none").alias("sector"),
        pl.col("entry_date").str.strptime(pl.Date,"%Y-%m-%d",strict=False).alias("ed"),
        pl.col("exit_date").str.strptime(pl.Date,"%Y-%m-%d",strict=False).alias("xd"))
    seeds = df["seed"].unique().to_list()[:N_SEEDS]
    parts = []
    for sd in seeds:
        s = df.filter(pl.col("seed")==sd).sort("ed")
        ssc = same_sector_concur(s)
        parts.append(s.with_columns(pl.Series("ssc", ssc)))
    d = pl.concat(parts)
    y_bag = (d["outcome"]=="dh_expiry").cast(pl.Float64).to_numpy()
    pnl = d["option_pnl"].to_numpy()
    tot = d["entry_open_calls"].to_numpy().astype(float)
    ssc = d["ssc"].to_numpy().astype(float)
    # correlation of sector_concur with total_concur (how redundant)
    cc = np.corrcoef(ssc, tot)[0,1]
    # partial: does ssc predict bag-rate beyond total concurrency?
    one = np.ones(len(y_bag))
    _, t_tot = ols_t(np.c_[one, z(tot)], y_bag)
    _, t_both = ols_t(np.c_[one, z(tot), z(ssc)], y_bag)
    # mean pnl by ssc bucket
    dd = d.with_columns(pl.Series("ssc", ssc))
    buck = dd.with_columns(
        pl.when(pl.col("ssc")<=1).then(pl.lit("0-1"))
         .when(pl.col("ssc")<=3).then(pl.lit("2-3"))
         .when(pl.col("ssc")<=5).then(pl.lit("4-5")).otherwise(pl.lit("6+")).alias("b"))
    tab = buck.group_by("b").agg(pl.col("option_pnl").mean().alias("mpnl"),
                                 (pl.col("outcome")=="dh_expiry").mean().alias("bagrate"),
                                 pl.len().alias("n")).sort("b")
    print(f"{y}: N={len(y_bag)} ({N_SEEDS} seeds)  corr(ssc,total)={cc:+.2f}  "
          f"bag~total t={t_tot[1]:+.1f}  bag~ssc|total t={t_both[2]:+.1f}")
    for r in tab.iter_rows(named=True):
        print(f"     ssc {r['b']:>3}: mpnl={r['mpnl']:+.3f}  bagrate={100*r['bagrate']:.1f}%  n={r['n']}")
    print()

print("=== (C) synchronized-death: do bags EXIT (die) in the same weeks? (1 seed/year) ===")
for y in YEARS:
    df = pl.read_parquet(TAPE / f"tape_{y}.parquet").with_columns(
        pl.col("exit_date").str.strptime(pl.Date,"%Y-%m-%d",strict=False).alias("xd"))
    one = df.filter((pl.col("seed")==df["seed"][0]) & (pl.col("outcome")=="dh_expiry"))
    one = one.with_columns(pl.col("xd").dt.strftime("%Y-%W").alias("wk"))
    wk = one.group_by("wk").len().sort("len",descending=True); nb=one.height
    if nb<10: print(f"  {y}: few bags"); continue
    print(f"  {y}: bags={nb}  exit-weeks={wk.height}  worst-exit-week={100*wk['len'][0]/nb:.0f}%  top3={100*wk['len'].head(3).sum()/nb:.0f}%")
