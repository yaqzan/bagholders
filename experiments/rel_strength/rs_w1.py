"""W1 for relative strength vs SPY: does RS add OPTION-barrier info BEYOND overall, and beyond the
stock's OWN momentum (so we're testing the RELATIVE part, not re-discovering price momentum)?

Real discriminator (ship-worthy) = within 75+, the top-RS group beats the bottom-RS group on opt15 AND
apex15 at |z|>=3, AND it survives controlling for stock_r20 (own momentum). Direction (leaders-continue
vs laggards-revert) is whatever the data says. If flat -> RS is redundant -> null.
"""
import os, sys, math
import polars as pl
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
import label as L  # noqa

LED = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")
BARS = [("opt15", 0.901, 0.772, 15), ("apex15", 1.092, 2.548, 15), ("hold15", 0.901, 7.07, 15), ("gen15", 1.414, 3.536, 15)]


def zprop(w1, n1, w2, n2):
    if n1 < 5 or n2 < 5: return None, None
    p1, p2 = w1/n1, w2/n2
    pp = (w1+w2)/(n1+n2); se = math.sqrt(pp*(1-pp)*(1/n1+1/n2))
    return p1-p2, ((p1-p2)/se if se else None)


def wr(s):
    r = s.drop_nulls(); return (int(r.sum()) if r.len() else 0, r.len(), (r.mean() if r.len() else None))


def quintile_test(df, feat, tier_label, bars=("opt15", "apex15")):
    sub = df.filter(pl.col(feat).is_not_null())
    if sub.height < 200:
        print(f"  [{tier_label}] {feat}: N={sub.height} too small"); return
    qs = [sub[feat].quantile(q) for q in (0.2, 0.4, 0.6, 0.8)]
    edges = sorted(set([sub[feat].min()] + qs + [sub[feat].max()]))
    print(f"  [{tier_label}] {feat} quintiles (N={sub.height}):")
    buckets = []
    for bi in range(len(edges)-1):
        lo, hi = edges[bi], edges[bi+1]; last = bi == len(edges)-2
        m = (pl.col(feat) >= lo) & ((pl.col(feat) <= hi) if last else (pl.col(feat) < hi))
        b = sub.filter(m)
        cells = []; vals = {}
        for nm in bars:
            w, n, mn = wr(b[nm]); vals[nm] = (w, n, mn)
            cells.append(f"{nm}={mn*100:4.1f}%" if mn is not None else f"{nm}=-")
        buckets.append(vals)
        print(f"      {feat}[{lo:+.3f},{hi:+.3f}] N={b.height:5}  " + "  ".join(cells))
    for nm in bars:
        bot = buckets[0][nm]; top = buckets[-1][nm]
        if bot[1] >= 5 and top[1] >= 5:
            d, z = zprop(top[0], top[1], bot[0], bot[1])
            tag = "  <-- |z|>=3 DISCRIMINATOR" if (z is not None and abs(z) >= 3) else ("  (|z|>=2)" if (z is not None and abs(z) >= 2) else "")
            print(f"        {nm} top-vs-bottom: d={d*100:+.1f}pp z={z:+.2f}{tag}")


def main():
    df = pl.read_parquet(LED).with_columns([L.win_expr(tp, sl, w).alias(nm) for nm, tp, sl, w in BARS])
    cov = df["rs20"].drop_nulls().len()
    print(f"rs_ledger N={df.height} rs20-cov={100*cov/df.height:.1f}% window={df['date'].min()}..{df['date'].max()}")
    assert df["date"].max() <= "2026-05-15", "HOLDOUT LEAK"

    print("\n########## A. Does RS split winners WITHIN the call pool? ##########")
    for tlo, tname in [(75, "75+"), (70, "70-74"), (80, "80+")]:
        thi = 74 if tname == "70-74" else 99
        tier = df.filter((pl.col("overall") >= tlo) & (pl.col("overall") <= thi))
        for feat in ("rs20", "rs60"):
            quintile_test(tier, feat, tname)
        print()

    print("########## B. Isolate the RELATIVE part: control for own momentum (stock_r20) within 75+ ##########")
    t75 = df.filter(pl.col("overall") >= 75).filter(pl.col("stock_r20").is_not_null() & pl.col("rs20").is_not_null())
    # own-momentum quartiles, then rs20 high/low within each
    mq = [t75["stock_r20"].quantile(q) for q in (0.25, 0.5, 0.75)]
    medges = [t75["stock_r20"].min()] + mq + [t75["stock_r20"].max()]
    for i in range(4):
        lo, hi = medges[i], medges[i+1]; last = i == 3
        band = t75.filter((pl.col("stock_r20") >= lo) & ((pl.col("stock_r20") <= hi) if last else (pl.col("stock_r20") < hi)))
        rsmed = band["rs20"].median()
        hi_rs = band.filter(pl.col("rs20") >= rsmed); lo_rs = band.filter(pl.col("rs20") < rsmed)
        ho = wr(hi_rs["opt15"]); lo_ = wr(lo_rs["opt15"]); ha = wr(hi_rs["apex15"]); la = wr(lo_rs["apex15"])
        if ho[1] >= 5 and lo_[1] >= 5:
            d, z = zprop(ho[0], ho[1], lo_[0], lo_[1]); _, za = zprop(ha[0], ha[1], la[0], la[1])
            print(f"  own_mom[{lo:+.2f},{hi:+.2f}] N={band.height:4}: hiRS opt15={ho[2]*100:4.1f}% vs loRS {lo_[2]*100:4.1f}%  d={d*100:+.1f}pp z(opt15)={z:+.2f} z(apex15)={ (za if za is not None else 0):+.2f}")
    print("  -> if rs20 adds nothing within a fixed own-momentum band, RS is just own-momentum (already in score via trend).")

    print("\n########## C. Direction + skeptic ##########")
    t75 = df.filter(pl.col("overall") >= 75)
    for nm in ("opt15", "apex15"):
        for lo, hi, lab in [(-9, -0.03, "laggard rs20<-3%"), (-0.03, 0.03, "neutral"), (0.03, 9, "leader rs20>+3%")]:
            b = t75.filter((pl.col("rs20") >= lo) & (pl.col("rs20") < hi))
            w, n, m = wr(b[nm])
            if n >= 20:
                print(f"  {nm} {lab:18} N={n:4} WR={m*100:4.1f}%")
    print("by year (rs20 top-vs-bottom tercile within 75+, opt15):")
    for y in ('2018', '2020', '2022', '2024'):
        yb = t75.filter(pl.col("date").str.starts_with(y)).filter(pl.col("rs20").is_not_null())
        if yb.height < 60: continue
        t = yb["rs20"].quantile(0.66); bq = yb["rs20"].quantile(0.33)
        hi = yb.filter(pl.col("rs20") >= t); lo = yb.filter(pl.col("rs20") <= bq)
        ho = wr(hi["opt15"]); lo_ = wr(lo["opt15"])
        if ho[1] >= 10 and lo_[1] >= 10:
            d, z = zprop(ho[0], ho[1], lo_[0], lo_[1])
            print(f"  {y}: hiRS {ho[2]*100:.1f}% vs loRS {lo_[2]*100:.1f}%  z={z:+.2f}")

    print("\n########## VERDICT ##########")
    print("Real discriminator if A shows |z|>=3 on opt15 AND apex15 within 75+, AND B survives own-momentum control.")


if __name__ == "__main__":
    main()
