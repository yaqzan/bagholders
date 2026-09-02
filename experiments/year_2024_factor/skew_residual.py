"""
DECISIVE TEST: does DIRECT option-chain skew add per-trade signal BEYOND the shipped
semivol_r (SVR)?  If subsumed -> SVR already got it.  If residual -> real unshipped edge.
Uses the existing 10y proxy_ledger (has opt_skew + semivol_r + ret_skew + fwd option pnl).
"""
import sys
from pathlib import Path
import numpy as np, polars as pl
ROOT = Path(__file__).resolve().parents[2]
LED = ROOT / ".cache" / "iv_skew" / "proxy_ledger.parquet"

df = pl.read_parquet(LED)
print(f"proxy_ledger: {df.shape}")
for c in ["opt_skew", "semivol_r", "ret_skew", "pnl15", "overall"]:
    nn = df[c].is_not_null().sum() if c in df.columns else "MISSING"
    print(f"  {c}: non-null {nn}")

# covered subset: direct option-skew available (the ~1.3y option window) + targets present
d = df.filter(
    pl.col("opt_skew").is_not_null() & pl.col("semivol_r").is_not_null()
    & pl.col("ret_skew").is_not_null() & pl.col("pnl15").is_not_null()
    & (pl.col("overall") >= 75)              # the tradable Apex cohort
)
print(f"\ncovered 75+ subset (opt_skew & semivol_r & pnl15 present): N={d.height}")
if d.height < 100:
    # fall back to 70+ if 75+ too thin on the covered window
    d = df.filter(
        pl.col("opt_skew").is_not_null() & pl.col("semivol_r").is_not_null()
        & pl.col("ret_skew").is_not_null() & pl.col("pnl15").is_not_null()
        & (pl.col("overall") >= 70))
    print(f"  -> too thin; using 70+ : N={d.height}")

def z(a):
    a = np.asarray(a, float); m = np.nanmean(a); s = np.nanstd(a) or 1.0
    return (a - m) / s

y = np.asarray(d["pnl15"], float)
osk = z(d["opt_skew"]); svr = z(d["semivol_r"]); rsk = z(d["ret_skew"])

def ols_t(X, y):
    """return betas and t-stats; X already has intercept col."""
    XtX = X.T @ X
    beta = np.linalg.solve(XtX, X.T @ y)
    resid = y - X @ beta
    dof = len(y) - X.shape[1]
    s2 = (resid @ resid) / dof
    cov = s2 * np.linalg.inv(XtX)
    se = np.sqrt(np.diag(cov))
    return beta, beta / se

N = len(y)
one = np.ones(N)
print(f"\n=== univariate (option fwd P&L pnl15 ~ feature), N={N} ===")
for name, x in [("opt_skew", osk), ("semivol_r", svr), ("ret_skew", rsk)]:
    b, t = ols_t(np.c_[one, x], y); print(f"  {name:10s}: beta={b[1]:+.4f}  t={t[1]:+.2f}")

print(f"\n=== PARTIAL: opt_skew controlling for semivol_r (the decisive test) ===")
b, t = ols_t(np.c_[one, svr, osk], y)
print(f"  semivol_r| t={t[1]:+.2f}    opt_skew|semivol_r  t={t[2]:+.2f}   (beta={b[2]:+.4f})")
print(f"  -> if opt_skew|semivol_r t is small (<~2), SVR subsumes it; if large, real residual.")

print(f"\n=== PARTIAL: opt_skew controlling for semivol_r AND ret_skew ===")
b, t = ols_t(np.c_[one, svr, rsk, osk], y)
print(f"  svr t={t[1]:+.2f}  ret_skew t={t[2]:+.2f}  opt_skew| both t={t[3]:+.2f}")

# reverse: does semivol_r add beyond opt_skew? (who dominates)
b, t = ols_t(np.c_[one, osk, svr], y)
print(f"\n=== reverse: semivol_r | opt_skew  t={t[2]:+.2f}  (opt_skew| t={t[1]:+.2f}) ===")

# correlation
print(f"\ncorr(opt_skew, semivol_r) = {np.corrcoef(osk, svr)[0,1]:+.3f}")
print(f"corr(opt_skew, ret_skew)  = {np.corrcoef(osk, rsk)[0,1]:+.3f}")

# win-rate of opt_skew WITHIN semivol_r terciles (does direct-skew still sort inside SVR bins?)
print(f"\n=== win-rate(pnl15>0) by opt_skew tercile, WITHIN semivol_r tercile ===")
dd = d.with_columns(
    (pl.col("pnl15") > 0).alias("win"),
    pl.col("semivol_r").qcut(3, labels=["svr_lo","svr_mid","svr_hi"]).alias("svr_b"),
    pl.col("opt_skew").qcut(3, labels=["osk_lo","osk_mid","osk_hi"]).alias("osk_b"),
)
tab = (dd.group_by(["svr_b","osk_b"]).agg(pl.col("win").mean().alias("wr"), pl.len().alias("n"))
         .sort(["svr_b","osk_b"]))
for r in tab.iter_rows(named=True):
    print(f"  {r['svr_b']:7s} x {r['osk_b']:7s}: WR={100*r['wr']:5.1f}%  N={r['n']}")
