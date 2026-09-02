"""Adversarial verification of the SKEW signal (t=+4.28 on apex_pnl full-sample).
IV/RV looked similar then sign-flipped across regimes — so REFUTE skew the same way:
 1. Stability: skew->apex_pnl by quarter + pre/post 2025-05-01. Same sign everywhere?
 2. Confound: is skew just a proxy for the stock's recent return (oversold bounce) or overall/vol?
    Control by splitting within recent-return / overall / vol buckets (join stock_r20 from rs_ledger).
 3. Concentration: drop top-5 symbols; by-symbol contribution.
 4. Direction sanity: low-skew (call-euphoria) vs high-skew (cheap call) win-rates per period.
Real signal survives if skew->apex_pnl stays same-sign (high-skew better) across periods AND within
recent-return buckets (i.e. it adds beyond oversold).
"""
import os, sys, math
import numpy as np
import polars as pl

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
IV = os.path.join(_ROOT, ".cache", "iv_skew", "iv_ledger.parquet")
RS = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")


def apex_pnl(pmax, pmin, p15):
    if pmin is None or pmax is None or p15 is None: return None
    if pmin <= -0.70: return -0.70
    if pmax >= 0.30: return 0.30
    return max(p15, -0.70)


def welch(a, b):
    a = np.array([x for x in a if x is not None]); b = np.array([x for x in b if x is not None])
    if len(a) < 5 or len(b) < 5: return None, None
    se = math.sqrt(a.var(ddof=1)/len(a) + b.var(ddof=1)/len(b))
    return (a.mean()-b.mean()), ((a.mean()-b.mean())/se if se else None)


def hilo(df, feat, y, frac=0.4):
    sub = df.filter(pl.col(feat).is_not_null() & pl.col(y).is_not_null())
    if sub.height < 40: return None
    qlo, qhi = sub[feat].quantile(frac), sub[feat].quantile(1-frac)
    lo = sub.filter(pl.col(feat) <= qlo)[y].to_list()
    hi = sub.filter(pl.col(feat) >= qhi)[y].to_list()
    d, t = welch(hi, lo)
    hv = np.array([x for x in hi if x is not None]); lv = np.array([x for x in lo if x is not None])
    return (d, t, np.mean(hv), np.mean(lv), 100*np.mean(hv>0), 100*np.mean(lv>0), len(hv), len(lv))


def show(tag, r):
    if r is None: print(f"  {tag}: too small"); return
    d, t, mh, ml, wh, wl, nh, nl = r
    flag = "  <-- |t|>=3" if (t is not None and abs(t) >= 3) else ("  (|t|>=2)" if (t is not None and abs(t) >= 2) else "")
    print(f"  {tag:30} hiSkew apex={mh*100:+5.1f}%(win{wh:3.0f}%,N{nh}) vs loSkew {ml*100:+5.1f}%(win{wl:3.0f}%,N{nl})  Δ={d*100:+5.1f} t={ (t if t is not None else 0):+.2f}{flag}")


def main():
    df = pl.read_parquet(IV).with_columns(
        pl.struct(["pnl_max", "pnl_min", "pnl15"]).map_elements(
            lambda r: apex_pnl(r["pnl_max"], r["pnl_min"], r["pnl15"]), return_dtype=pl.Float64).alias("apex_pnl"))
    # join stock_r20 (recent own momentum) from rs_ledger
    rs = pl.read_parquet(RS).select(["symbol", "date", "stock_r20"])
    df = df.join(rs, on=["symbol", "date"], how="left")
    print(f"N={df.height}  full-sample skew->apex_pnl:")
    show("FULL", hilo(df, "skew", "apex_pnl"))

    print("\n1. STABILITY by period:")
    for lab, m in [("pre  <2025-05", pl.col("date") < "2025-05-01"),
                   ("post >=2025-05", pl.col("date") >= "2025-05-01")]:
        show(lab, hilo(df.filter(m), "skew", "apex_pnl"))
    print("   by quarter:")
    for q, (a, b) in [("2025Q2", ("2025-04-01", "2025-07-01")), ("2025Q3", ("2025-07-01", "2025-10-01")),
                      ("2025Q4", ("2025-10-01", "2026-01-01")), ("2026Q1+", ("2026-01-01", "2026-06-01"))]:
        show(q, hilo(df.filter((pl.col("date") >= a) & (pl.col("date") < b)), "skew", "apex_pnl"))

    print("\n2. CONFOUND — does skew add BEYOND recent return (oversold) / overall / vol?")
    print("   within stock_r20 (recent return) terciles:")
    d2 = df.filter(pl.col("stock_r20").is_not_null())
    for lab, lo, hi in [("r20 low(down)", -9, d2["stock_r20"].quantile(.33)),
                        ("r20 mid", d2["stock_r20"].quantile(.33), d2["stock_r20"].quantile(.66)),
                        ("r20 high(up)", d2["stock_r20"].quantile(.66), 9)]:
        show(lab, hilo(d2.filter((pl.col("stock_r20") >= lo) & (pl.col("stock_r20") < hi)), "skew", "apex_pnl"))
    print("   within overall band:")
    for lab, lo, hi in [("overall 70-74", 70, 74), ("overall 75+", 75, 99)]:
        show(lab, hilo(df.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi)), "skew", "apex_pnl"))
    # correlation of skew with the confounders
    sk = df.filter(pl.col("skew").is_not_null() & pl.col("stock_r20").is_not_null())
    cr = np.corrcoef(sk["skew"].to_list(), sk["stock_r20"].to_list())[0, 1]
    print(f"   corr(skew, stock_r20)={cr:+.3f}  corr(skew, vol_pct)={np.corrcoef(df.filter(pl.col('skew').is_not_null())['skew'].to_list(), df.filter(pl.col('skew').is_not_null())['vol_pct'].to_list())[0,1]:+.3f}")

    print("\n3. CONCENTRATION — drop top-5 contributing symbols:")
    top = df.group_by("symbol").agg(pl.len().alias("n")).sort("n", descending=True).head(5)["symbol"].to_list()
    print(f"   top-5: {top}")
    show("minus top-5 syms", hilo(df.filter(~pl.col("symbol").is_in(top)), "skew", "apex_pnl"))

    print("\nVERDICT: skew is real if it stays same-sign (high-skew better) across periods AND within recent-return buckets.")


if __name__ == "__main__":
    main()
