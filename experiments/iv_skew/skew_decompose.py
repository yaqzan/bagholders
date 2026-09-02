"""Decompose the skew edge: is it (a) a PREMIUM effect (cheap-side pays less; invisible to a
realized-vol-premium MC) or (b) also an UNDERLYING-DIRECTION effect (the stock itself moves better;
visible on the barrier and in any MC)?

Join iv_ledger (skew, ACTUAL option pnl) to rs_ledger (barrier-agnostic t_up/t_dn -> opt15/apex15
underlying barriers). Compare skew's predictive t on:
  - opt15 / apex15  = UNDERLYING barrier-touch (premium-BLIND; what the standard MC effectively sees)
  - actual apex_pnl = ACTUAL option P&L (premium-AWARE; where we found t=+4)
If skew predicts actual-pnl strongly but the underlying barrier weakly -> PREMIUM-ONLY -> the standard
10y MC gate (realized-vol premium) CANNOT validate it; need an IV-aware MC. If skew also moves the
underlying barrier -> a price-proxy + standard MC is viable.
"""
import os, sys, math
import numpy as np
import polars as pl
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
import label as L  # noqa

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


def zprop(w1, n1, w2, n2):
    if n1 < 5 or n2 < 5: return None, None
    p1, p2 = w1/n1, w2/n2; pp = (w1+w2)/(n1+n2); se = math.sqrt(pp*(1-pp)*(1/n1+1/n2))
    return (p1-p2), ((p1-p2)/se if se else None)


def hilo_binary(df, feat, y, frac=0.4):
    sub = df.filter(pl.col(feat).is_not_null() & pl.col(y).is_not_null())
    if sub.height < 40: return None
    qlo, qhi = sub[feat].quantile(frac), sub[feat].quantile(1-frac)
    lo = sub.filter(pl.col(feat) <= qlo)[y]; hi = sub.filter(pl.col(feat) >= qhi)[y]
    lw, ln = lo.sum(), lo.len(); hw, hn = hi.sum(), hi.len()
    d, z = zprop(int(hw), ln_hi := hn, int(lw), ln)
    return d, z, 100*hw/hn, 100*lw/ln, hn, ln


def hilo_cont(df, feat, y, frac=0.4):
    sub = df.filter(pl.col(feat).is_not_null() & pl.col(y).is_not_null())
    if sub.height < 40: return None
    qlo, qhi = sub[feat].quantile(frac), sub[feat].quantile(1-frac)
    lo = sub.filter(pl.col(feat) <= qlo)[y].to_list(); hi = sub.filter(pl.col(feat) >= qhi)[y].to_list()
    d, t = welch(hi, lo)
    return d, t


def main():
    iv = pl.read_parquet(IV).with_columns(
        pl.struct(["pnl_max", "pnl_min", "pnl15"]).map_elements(
            lambda r: apex_pnl(r["pnl_max"], r["pnl_min"], r["pnl15"]), return_dtype=pl.Float64).alias("apex_pnl"))
    rs = pl.read_parquet(RS).with_columns([
        L.win_expr(0.901, 0.772, 15).alias("opt15"), L.win_expr(1.092, 2.548, 15).alias("apex15")])
    df = iv.join(rs.select(["symbol", "date", "opt15", "apex15"]), on=["symbol", "date"], how="left")
    print(f"joined N={df.height}  opt15 cov={df['opt15'].drop_nulls().len()}")

    print("\n=== skew -> UNDERLYING barrier (premium-BLIND; what the standard MC sees) ===")
    for y in ("opt15", "apex15"):
        r = hilo_binary(df, "skew", y)
        if r:
            d, z, hw, lw, hn, ln = r
            print(f"  skew -> {y:7} (binary WR): hiSkew {hw:.1f}% vs loSkew {lw:.1f}%  Δ={d*100:+.1f}pp z={ (z if z is not None else 0):+.2f}")

    print("\n=== skew -> ACTUAL option P&L (premium-AWARE; where the edge lives) ===")
    for y in ("pnl15", "apex_pnl"):
        d, t = hilo_cont(df, "skew", y)
        print(f"  skew -> {y:9} (mean): Δ={d*100:+.1f}pp Welch t={ (t if t is not None else 0):+.2f}")

    print("\n=== DIAGNOSIS ===")
    print("If underlying-barrier z is weak (|z|<2) but actual-pnl t is strong (>=3) -> PREMIUM-ONLY edge:")
    print("  the realized-vol-premium MC is blind to it; full-history proxy+standard-MC is NOT a valid gate.")
    print("  -> validate via IV-aware MC on the 1.3y window (or accept per-trade evidence).")
    print("If underlying barrier also moves with skew -> proxy + standard MC is viable on full history.")


if __name__ == "__main__":
    main()
