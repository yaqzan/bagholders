"""Cleaner cut: does direct opt_skew add to WIN-RATE beyond semivol_r, on 70+ vs 75+?
Win-rate (binary) is far less noisy than the reconstructed option P&L."""
import sys
from pathlib import Path
import numpy as np, polars as pl
ROOT = Path(__file__).resolve().parents[2]
df = pl.read_parquet(ROOT / ".cache" / "iv_skew" / "proxy_ledger.parquet")

def z(a):
    a = np.asarray(a, float); return (a - np.nanmean(a)) / (np.nanstd(a) or 1.0)
def ols_t(X, y):
    beta = np.linalg.solve(X.T@X, X.T@y); r = y - X@beta
    s2 = (r@r)/(len(y)-X.shape[1]); se = np.sqrt(np.diag(s2*np.linalg.inv(X.T@X)))
    return beta, beta/se

for thr in (70, 75):
    d = df.filter(
        pl.col("opt_skew").is_not_null() & pl.col("semivol_r").is_not_null()
        & pl.col("pnl15").is_not_null() & (pl.col("overall") >= thr))
    y = (np.asarray(d["pnl15"], float) > 0).astype(float)   # WIN
    osk, svr = z(d["opt_skew"]), z(d["semivol_r"]); one = np.ones(len(y))
    bu, tu = ols_t(np.c_[one, osk], y)
    bp, tp = ols_t(np.c_[one, svr, osk], y)
    print(f"\n=== {thr}+  N={len(y)}  base WR={100*y.mean():.1f}% ===")
    print(f"  opt_skew univariate (win):   t={tu[1]:+.2f}")
    print(f"  opt_skew | semivol_r (win):  t={tp[2]:+.2f}   semivol_r| t={tp[1]:+.2f}")
    # quintile WR by opt_skew
    q = d.with_columns((pl.col("pnl15")>0).alias("w"),
                       pl.col("opt_skew").qcut(5, labels=[str(i) for i in range(5)]).alias("qb"))
    tab = q.group_by("qb").agg(pl.col("w").mean().alias("wr"), pl.len().alias("n")).sort("qb")
    s = "  ".join(f"Q{r['qb']}:{100*r['wr']:.0f}%(n{r['n']})" for r in tab.iter_rows(named=True))
    print(f"  opt_skew WR by quintile: {s}")

# How tradable is the direct-skew filter on 75+? i.e. if we drop the worst opt_skew
# quintile on 75+ covered, how many signals remain (capacity), and the WR lift.
d = df.filter(pl.col("opt_skew").is_not_null() & pl.col("pnl15").is_not_null() & (pl.col("overall")>=75))
y = (np.asarray(d["pnl15"],float)>0).astype(float)
thr_q = d["opt_skew"].quantile(0.2)
keep = np.asarray(d["opt_skew"],float) > thr_q
print(f"\n=== capacity: 75+ covered N={len(y)} over ~1.3y; drop bottom-20% opt_skew ===")
print(f"  base WR {100*y.mean():.1f}%  ->  filtered WR {100*y[keep].mean():.1f}%  "
      f"(kept {keep.sum()}/{len(y)} = {100*keep.mean():.0f}%)")
print(f"  signals/yr on 75+ covered ~ {len(y)/1.3:.0f}; the cheap-side subset is the tradable count.")
