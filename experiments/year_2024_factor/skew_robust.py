"""Bulletproof the staged direct-skew residual on the TRADABLE 75+ cohort:
 (1) sign-stability across quarters (the test that killed iv_rv),
 (2) orthogonality to the price score (overall) and recent return,
 (3) it's the OPTION skew, not semivol_r, that carries it (already shown; reconfirm win-rate quintile).
Win-rate (binary) is the clean metric for the noisy option P&L.
"""
import sys
from pathlib import Path
import numpy as np, polars as pl
ROOT = Path(__file__).resolve().parents[2]
df = pl.read_parquet(ROOT / ".cache" / "iv_skew" / "proxy_ledger.parquet")

def z(a): a=np.asarray(a,float); return (a-np.nanmean(a))/(np.nanstd(a) or 1.0)
def ols_t(X,y):
    b=np.linalg.solve(X.T@X,X.T@y); r=y-X@b; s2=(r@r)/(len(y)-X.shape[1])
    return b, b/np.sqrt(np.diag(s2*np.linalg.inv(X.T@X)))

d = df.filter(pl.col("opt_skew").is_not_null() & pl.col("pnl15").is_not_null() & (pl.col("overall")>=75))
d = d.with_columns((pl.col("pnl15")>0).cast(pl.Float64).alias("win"),
                   pl.col("date").str.slice(0,7).alias("ym"))
print(f"75+ covered: N={d.height}  date range {d['date'].min()} -> {d['date'].max()}  base WR={100*d['win'].mean():.1f}%")

# (1) sign stability by quarter
print("\n=== (1) sign-stability: opt_skew->win univariate t, by quarter ===")
d = d.with_columns(
    (pl.col("date").str.slice(0,4) + "Q" +
     ((pl.col("date").str.slice(5,2).cast(pl.Int32)-1)//3 + 1).cast(pl.Utf8)).alias("q"))
for q in sorted(d["q"].unique().to_list()):
    s = d.filter(pl.col("q")==q)
    if s.height < 30:
        print(f"  {q}: N={s.height} (thin)"); continue
    y=np.asarray(s["win"],float); b,t=ols_t(np.c_[np.ones(len(y)), z(s["opt_skew"])], y)
    print(f"  {q}: N={s.height:4}  t={t[1]:+.2f}  WR={100*y.mean():.0f}%")

# (2) orthogonality to overall + recent return (does opt_skew add within score/return controls?)
print("\n=== (2) orthogonality (75+ win ~ controls + opt_skew) ===")
y=np.asarray(d["win"],float); one=np.ones(len(y))
osk=z(d["opt_skew"]); ov=z(d["overall"]); r20=z(d["stock_r20"]); r60=z(d["stock_r60"])
b,t=ols_t(np.c_[one,ov,osk],y);            print(f"  +overall:           opt_skew t={t[2]:+.2f}")
b,t=ols_t(np.c_[one,r20,r60,osk],y);       print(f"  +stock_r20,r60:     opt_skew t={t[3]:+.2f}")
b,t=ols_t(np.c_[one,ov,r20,r60,osk],y);    print(f"  +overall,r20,r60:   opt_skew t={t[4]:+.2f}")
# within recent-return terciles (is it just an oversold-bounce proxy?)
dd=d.with_columns(pl.col("stock_r20").qcut(3,labels=["dn","mid","up"]).alias("rb"))
print("  within r20 terciles:")
for rb in ["dn","mid","up"]:
    s=dd.filter(pl.col("rb")==rb); y=np.asarray(s["win"],float)
    b,t=ols_t(np.c_[np.ones(len(y)),z(s["opt_skew"])],y)
    print(f"    r20={rb}: N={s.height:4} opt_skew t={t[1]:+.2f}")

# (3) win-rate quintile by opt_skew, and the cheap-side capacity at a few cuts
print("\n=== (3) opt_skew win-rate quintile (75+) + filter capacity ===")
q=d.with_columns(pl.col("opt_skew").qcut(5,labels=[str(i) for i in range(5)]).alias("qb"))
for r in q.group_by("qb").agg(pl.col("win").mean().alias("wr"),pl.len().alias("n")).sort("qb").iter_rows(named=True):
    print(f"  Q{r['qb']}: WR={100*r['wr']:.0f}%  N={r['n']}")
for cut in (0.2,0.33,0.5):
    thr=d["opt_skew"].quantile(cut); k=np.asarray(d["opt_skew"],float)>thr
    y=np.asarray(d["win"],float)
    print(f"  drop bottom-{int(cut*100)}%: WR {100*y.mean():.1f}->{100*y[k].mean():.1f}%  kept {k.sum()}/{len(y)} ({100*k.mean():.0f}%)")
