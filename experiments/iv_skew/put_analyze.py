"""Put-side skew analysis. Does skew predict PUT P&L, and in which direction?
 LOW skew -> better puts  => "buy the cheap side" (put cheap when call_iv>=put_iv) => GENERAL edge.
 HIGH skew -> better puts => directional sentiment (puts expensive=fear=bearish continuation).
The call side found HIGH skew -> better CALLS (call cheap). Cheap-side symmetry predicts the OPPOSITE
sign on puts. Sentiment predicts the SAME sign. This distinguishes the mechanism.
"""
import os, sys, math
import numpy as np
import polars as pl
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
LED = os.path.join(_ROOT, ".cache", "iv_skew", "put_ledger.parquet")


def pnl_recon(pmax, pmin, p15):
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
    return d, t, np.mean(hv), np.mean(lv), 100*np.mean(hv > 0), 100*np.mean(lv > 0), len(hv), len(lv)


def show(tag, r):
    if r is None: print(f"  {tag:22}: too small"); return
    d, t, mh, ml, wh, wl, nh, nl = r
    print(f"  {tag:22} hiSkew put={mh*100:+5.1f}%(win{wh:3.0f}%) vs loSkew {ml*100:+5.1f}%(win{wl:3.0f}%)  Δ={d*100:+5.1f} t={ (t if t is not None else 0):+.2f}")


def main():
    df = pl.read_parquet(LED).with_columns(
        pl.struct(["pnl_max", "pnl_min", "pnl15"]).map_elements(
            lambda r: pnl_recon(r["pnl_max"], r["pnl_min"], r["pnl15"]), return_dtype=pl.Float64).alias("put_pnl"))
    print(f"put_ledger N={df.height} window={df['date'].min()}..{df['date'].max()}")
    print(f"baseline put: mean HOLD-15={df['pnl15'].mean()*100:+.1f}% mean recon={df['put_pnl'].mean()*100:+.1f}% win%={100*(df['put_pnl']>0).mean():.0f}")
    print("\nskew -> PUT P&L:")
    show("HOLD-15", hilo(df, "skew", "pnl15"))
    show("recon (TP30/SL70)", hilo(df, "skew", "put_pnl"))
    print("\nperiod stability:")
    for lab, m in [("pre <2025-05", pl.col("date") < "2025-05-01"), ("post >=2025-05", pl.col("date") >= "2025-05-01")]:
        show(lab, hilo(df.filter(m), "skew", "put_pnl"))
    print("\nINTERPRETATION:")
    print("  Δ < 0 (LOW skew better puts)  => CHEAP-SIDE edge (symmetric with calls) — strongest result.")
    print("  Δ > 0 (HIGH skew better puts) => SAME sign as calls => directional/sentiment, not cheap-side.")
    print("  flat => skew is call-specific.")


if __name__ == "__main__":
    main()
