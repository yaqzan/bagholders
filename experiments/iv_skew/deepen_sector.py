"""Deepen step 1 (cheap, no build): is the skew signal just spec small-caps / 2025 meme-unwind?
Break skew->apex_pnl down by mcap and sector. If it survives in mid/large-caps and across sectors,
the meme-unwind worry is largely dead. If it's only micro/small-cap spec names, it's fragile.
Also: skew as a RATIO and ATM-normalized (cheap robustness on existing columns).
"""
import os, sys, math
import numpy as np
import polars as pl
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, _ROOT)
from database.trader_database import DB

IV = os.path.join(_ROOT, ".cache", "iv_skew", "iv_ledger.parquet")


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
    lo = sub.filter(pl.col(feat) <= qlo)[y].to_list(); hi = sub.filter(pl.col(feat) >= qhi)[y].to_list()
    d, t = welch(hi, lo)
    hv = np.array([x for x in hi if x is not None]); lv = np.array([x for x in lo if x is not None])
    return (d, t, 100*np.mean(hv>0), 100*np.mean(lv>0), len(hv), len(lv))


def show(tag, r):
    if r is None: print(f"  {tag:26}: too small"); return
    d, t, wh, wl, nh, nl = r
    flag = "  <-- t>=3" if (t is not None and t >= 3) else ("  t>=2" if (t is not None and t >= 2) else ("  FLIPS!" if (t is not None and t <= -2) else ""))
    print(f"  {tag:26} Δ={d*100:+5.1f}pp t={ (t if t is not None else 0):+.2f}  hiWin{wh:3.0f}% loWin{wl:3.0f}% (N{nh}/{nl}){flag}")


def main():
    df = pl.read_parquet(IV).with_columns(
        pl.struct(["pnl_max", "pnl_min", "pnl15"]).map_elements(
            lambda r: apex_pnl(r["pnl_max"], r["pnl_min"], r["pnl15"]), return_dtype=pl.Float64).alias("apex_pnl"))
    syms = df["symbol"].unique().to_list()
    rows = DB.execute_sql("SELECT symbol, sector, market_cap FROM stocks WHERE symbol IN (%s)"
                          % ",".join("'%s'" % s for s in syms)).fetchall()
    sec = {r[0]: (r[1] or "Unknown") for r in rows}
    mc = {r[0]: (float(r[2]) if r[2] is not None else None) for r in rows}
    df = df.with_columns([
        pl.col("symbol").replace_strict(sec, default="Unknown").alias("sector"),
        pl.col("symbol").map_elements(lambda s: mc.get(s), return_dtype=pl.Float64).alias("mcap"),
    ])

    print("=== FULL skew->apex_pnl ===")
    show("FULL", hilo(df, "skew", "apex_pnl"))

    print("\n=== by MCAP bucket (is it just micro/small spec?) ===")
    for lab, lo, hi in [("micro <2B", 0, 2e9), ("small 2-10B", 2e9, 1e10), ("mid 10-50B", 1e10, 5e10), ("large 50B+", 5e10, 1e15)]:
        show(lab, hilo(df.filter((pl.col("mcap") >= lo) & (pl.col("mcap") < hi)), "skew", "apex_pnl"))
    show("mcap >= 10B (mid+large)", hilo(df.filter(pl.col("mcap") >= 1e10), "skew", "apex_pnl"))

    print("\n=== by SECTOR (top by N) ===")
    secn = df.group_by("sector").agg(pl.len().alias("n")).sort("n", descending=True).head(8)
    for s in secn["sector"].to_list():
        show(s[:24], hilo(df.filter(pl.col("sector") == s), "skew", "apex_pnl"))

    print("\n=== robustness: skew RATIO + ATM-normalized skew ===")
    df = df.with_columns([
        (pl.col("skew") / pl.col("atm_iv")).alias("skew_norm"),
    ])
    show("skew (diff, baseline)", hilo(df, "skew", "apex_pnl"))
    show("skew_norm (skew/atm_iv)", hilo(df, "skew_norm", "apex_pnl"))

    print("\nVERDICT: meme-unwind worry dies if skew survives (t>=2, same sign) in mid/large-cap AND >=3 sectors.")


if __name__ == "__main__":
    main()
