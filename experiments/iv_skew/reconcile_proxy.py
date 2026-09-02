"""Reconcile the 10y price-skew proxy to REAL option skew, and test it full-history.
Binary verdict:
 1. Does the proxy TRACK real option skew on the 1.3y overlap? corr(ret_skew, opt_skew),
    corr(semivol_r, opt_skew). Need a meaningful positive corr to be a stand-in.
 2. Does the proxy reproduce the SAME P&L direction (high proxy -> better calls) on the overlap?
 3. FULL 10y: does the proxy predict the underlying barrier (opt15/apex15)? (the directional residual)
If corr is near zero -> price-proxy does NOT capture option skew -> skew is options-data-locked
(park until coverage deepens). If corr is solid AND directional residual shows on 10y -> a 10y feature.
"""
import os, sys, math
import numpy as np
import polars as pl
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
import label as L  # noqa
LED = os.path.join(_ROOT, ".cache", "iv_skew", "proxy_ledger.parquet")


def apex_pnl(pmax, pmin, p15):
    if pmin is None or pmax is None or p15 is None: return None
    if pmin <= -0.70: return -0.70
    if pmax >= 0.30: return 0.30
    return max(p15, -0.70)


def zprop(w1, n1, w2, n2):
    if n1 < 5 or n2 < 5: return None, None
    p1, p2 = w1/n1, w2/n2; pp = (w1+w2)/(n1+n2); se = math.sqrt(pp*(1-pp)*(1/n1+1/n2))
    return (p1-p2), ((p1-p2)/se if se else None)


def welch(a, b):
    a = np.array([x for x in a if x is not None]); b = np.array([x for x in b if x is not None])
    if len(a) < 5 or len(b) < 5: return None, None
    se = math.sqrt(a.var(ddof=1)/len(a) + b.var(ddof=1)/len(b))
    return (a.mean()-b.mean()), ((a.mean()-b.mean())/se if se else None)


def main():
    df = pl.read_parquet(LED).with_columns([
        L.win_expr(0.901, 0.772, 15).alias("opt15"), L.win_expr(1.092, 2.548, 15).alias("apex15"),
        pl.struct(["pnl_max", "pnl_min", "pnl15"]).map_elements(
            lambda r: apex_pnl(r["pnl_max"], r["pnl_min"], r["pnl15"]), return_dtype=pl.Float64).alias("apex_pnl"),
    ])
    print(f"N={df.height}  proxy cov ret_skew={df['ret_skew'].drop_nulls().len()}")

    print("\n=== 1. Does the price-proxy TRACK real option skew? (overlap) ===")
    ov = df.filter(pl.col("opt_skew").is_not_null() & pl.col("ret_skew").is_not_null())
    print(f"  overlap N={ov.height}")
    for p in ("ret_skew", "semivol_r"):
        o2 = ov.filter(pl.col(p).is_not_null())
        if o2.height < 30: continue
        c = np.corrcoef(o2[p].to_list(), o2["opt_skew"].to_list())[0, 1]
        print(f"  corr({p:10}, opt_skew) = {c:+.3f}  (N={o2.height})")

    print("\n=== 2. Same P&L direction on overlap? (high proxy -> better calls, like real skew) ===")
    for p in ("ret_skew", "semivol_r", "opt_skew"):
        sub = ov.filter(pl.col(p).is_not_null() & pl.col("apex_pnl").is_not_null())
        if sub.height < 40: continue
        ql, qh = sub[p].quantile(0.4), sub[p].quantile(0.6)
        lo = sub.filter(pl.col(p) <= ql)["apex_pnl"].to_list(); hi = sub.filter(pl.col(p) >= qh)["apex_pnl"].to_list()
        d, t = welch(hi, lo)
        print(f"  {p:10} hi-vs-lo apex_pnl: Δ={d*100:+.1f}pp t={ (t if t is not None else 0):+.2f}")

    print("\n=== 3. FULL 10y: proxy -> underlying barrier (directional residual) ===")
    for p in ("ret_skew", "semivol_r"):
        sub = df.filter(pl.col(p).is_not_null())
        ql, qh = sub[p].quantile(0.4), sub[p].quantile(0.6)
        for y in ("opt15", "apex15"):
            lo = sub.filter(pl.col(p) <= ql)[y]; hi = sub.filter(pl.col(p) >= qh)[y]
            d, z = zprop(int(hi.sum()), hi.len(), int(lo.sum()), lo.len())
            print(f"  {p:10} -> {y:7} (10y, N={sub.height}): hi {100*hi.mean():.1f}% vs lo {100*lo.mean():.1f}%  Δ={d*100:+.1f}pp z={ (z if z is not None else 0):+.2f}")

    print("\n=== VERDICT ===")
    print("  proxy is a usable 10y stand-in ONLY if corr(proxy, opt_skew) is solidly + AND same P&L sign")
    print("  AND the 10y directional residual shows (z>=2-3). Near-zero corr -> skew is options-data-locked.")


if __name__ == "__main__":
    main()
