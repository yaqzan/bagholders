"""W1 for IV/skew: does an options-IMPLIED signal predict the ACTUAL option P&L (the thing the
realized-vol barrier can't see)? Outcome = reconstructed Apex trade P&L (TP+30/SL-70/HOLD-15) from
the captured option path, plus raw HOLD-15 P&L. Features: iv_rv (variance risk premium), skew, atm_iv.

Real signal (probe-worthy) = a feature's top-vs-bottom quintile differs in mean P&L at |t|>=3 (Welch),
STABLE across the pre/post-selloff split (so it's not just the Feb-2025 regime). Continuous mean-P&L
(not win-rate) for power at this N (~2.5k). Honest about the 1.25y / regime-concentration limits.
"""
import os, sys, math
import numpy as np
import polars as pl

LED = os.path.join(os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")),
                   ".cache", "iv_skew", "iv_ledger.parquet")


def apex_pnl(pmax, pmin, p15):
    if pmin is None or pmax is None or p15 is None:
        return None
    if pmin <= -0.70:      # SL-first (conservative)
        return -0.70
    if pmax >= 0.30:       # TP
        return 0.30
    return max(p15, -0.70)


def welch(a, b):
    a = np.array([x for x in a if x is not None]); b = np.array([x for x in b if x is not None])
    if len(a) < 5 or len(b) < 5:
        return None, None, (None, None)
    m1, m2 = a.mean(), b.mean()
    s1, s2 = a.var(ddof=1), b.var(ddof=1)
    se = math.sqrt(s1/len(a) + s2/len(b))
    return (m1 - m2), ((m1 - m2)/se if se else None), (m1, m2)


def feat_test(df, feat, ycol, label):
    sub = df.filter(pl.col(feat).is_not_null() & pl.col(ycol).is_not_null())
    if sub.height < 100:
        print(f"  {feat} vs {label}: N={sub.height} too small"); return
    qs = [sub[feat].quantile(q) for q in (0.2, 0.4, 0.6, 0.8)]
    edges = sorted(set([sub[feat].min()] + qs + [sub[feat].max()]))
    print(f"  {feat} -> {label} (N={sub.height}):")
    buckets = []
    for bi in range(len(edges)-1):
        lo, hi = edges[bi], edges[bi+1]; last = bi == len(edges)-2
        m = (pl.col(feat) >= lo) & ((pl.col(feat) <= hi) if last else (pl.col(feat) < hi))
        b = sub.filter(m)
        y = b[ycol].to_list()
        yv = np.array([x for x in y if x is not None])
        win = 100*np.mean(yv > 0) if len(yv) else float('nan')
        buckets.append(y)
        print(f"      {feat}[{lo:+.2f},{hi:+.2f}] N={b.height:4}  mean={np.mean(yv)*100:+5.1f}%  win%={win:4.0f}")
    d, t, (m1, m2) = welch(buckets[-1], buckets[0])
    if t is not None:
        tag = "  <-- |t|>=3 SIGNAL" if abs(t) >= 3 else ("  (|t|>=2)" if abs(t) >= 2 else "")
        print(f"        top-vs-bottom mean Δ={d*100:+.1f}pp  Welch t={t:+.2f}{tag}")


def main():
    df = pl.read_parquet(LED)
    df = df.with_columns(
        pl.struct(["pnl_max", "pnl_min", "pnl15"]).map_elements(
            lambda r: apex_pnl(r["pnl_max"], r["pnl_min"], r["pnl15"]), return_dtype=pl.Float64).alias("apex_pnl"))
    print(f"iv_ledger N={df.height} window={df['date'].min()}..{df['date'].max()}")
    print(f"coverage: iv_rv={df['iv_rv'].drop_nulls().len()} skew={df['skew'].drop_nulls().len()} pnl15={df['pnl15'].drop_nulls().len()}")
    print(f"baseline: mean HOLD-15 pnl={df['pnl15'].mean()*100:+.1f}%  mean apex pnl={df['apex_pnl'].mean()*100:+.1f}%  apex win%={100*(df['apex_pnl']>0).mean():.0f}")
    print(f"iv_rv: p10={df['iv_rv'].quantile(.1):.2f} median={df['iv_rv'].median():.2f} p90={df['iv_rv'].quantile(.9):.2f}  (1.0 = IV==RV)")

    for feat in ("iv_rv", "skew", "atm_iv"):
        print(f"\n########## {feat} ##########")
        feat_test(df, feat, "pnl15", "HOLD-15 P&L")
        feat_test(df, feat, "apex_pnl", "Apex P&L (TP30/SL70)")

    print("\n########## PERIOD STABILITY (iv_rv -> apex_pnl, pre vs post 2025-05-01) ##########")
    for lab, m in [("pre (selloff era)", pl.col("date") < "2025-05-01"), ("post (recovery)", pl.col("date") >= "2025-05-01")]:
        sub = df.filter(m)
        print(f"  {lab} N={sub.height}:")
        feat_test(sub, "iv_rv", "apex_pnl", "Apex P&L")

    print("\nVERDICT: real probe signal if a feature's top-vs-bottom |t|>=3 on apex_pnl AND holds same-sign pre & post.")


if __name__ == "__main__":
    main()
