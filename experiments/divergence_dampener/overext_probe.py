"""Complete the (b) probe: the disc ledger had pct_from_ema50/200 NULL (not stored on
historical v70 recalc rows). Recompute OVEREXTENSION directly via a single bulk JOIN
(close + ema_50 + ema_200 for the same cohort), merge into the ledger by (symbol,date),
and run the reverts-vs-continues quintile probe on the OPTION barrier.

Overextension hypothesis: a very-stretched (high pct_from_ema50) overbought call REVERTS
(lower option WR); a mildly-stretched one CONTINUES. A clean monotone top-vs-bottom |z|>=3
on opt15/apex15 = the rescue discriminator. Nothing -> the divergence thread is fully closed.
"""
import os, sys, math
import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
import label as L  # noqa
from database.trader_database import DB

DISC = os.path.join(_ROOT, ".cache", "divergence_dampener", "ledger_disc.parquet")
BARRIERS = [("opt15", 0.901, 0.772, 15), ("apex15", 1.092, 2.548, 15)]


def zprop(w1, n1, w2, n2):
    if n1 < 5 or n2 < 5:
        return None, None
    p1, p2 = w1 / n1, w2 / n2
    pp = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
    return (p1 - p2), ((p1 - p2) / se if se else None)


def wrx(s):
    r = s.drop_nulls()
    return (int(r.sum()) if r.len() else 0), r.len(), (r.mean() if r.len() else None)


def main():
    print("bulk JOIN pull (close + ema50/200) for cohort...", flush=True)
    sql = (
        "SELECT s.symbol, s.date, ph.close, i.ema_50, i.ema_200 "
        "FROM scores s "
        "JOIN price_history ph ON ph.symbol=s.symbol AND ph.date=s.date "
        "JOIN indicators i ON i.symbol=s.symbol AND i.date=s.date "
        "WHERE s.version_id=70 AND s.trend>=85 AND s.stoch<=25 AND s.overall>=70 "
        "AND s.date BETWEEN '2016-05-15' AND '2026-05-15'"
    )
    rows = DB.execute_sql(sql).fetchall()
    print("  joined rows:", len(rows), flush=True)
    pe = {}
    for sym, d, close, e50, e200 in rows:
        if close is None or e50 in (None, 0):
            continue
        c = float(close); a = float(e50)
        p50 = (c - a) / a * 100.0
        p200 = ((c - float(e200)) / float(e200) * 100.0) if e200 not in (None, 0) else None
        key = (sym, d.isoformat() if hasattr(d, "isoformat") else str(d))
        pe[key] = (round(p50, 3), (round(p200, 3) if p200 is not None else None))

    df = pl.read_parquet(DISC).with_columns([L.win_expr(tp, sl, w).alias(nm) for nm, tp, sl, w in BARRIERS])
    p50col, p200col = [], []
    for sym, d in zip(df["symbol"].to_list(), df["date"].to_list()):
        v = pe.get((sym, d))
        p50col.append(v[0] if v else None)
        p200col.append(v[1] if v else None)
    df = df.with_columns([pl.Series("pe50", p50col), pl.Series("pe200", p200col)])
    cov = df["pe50"].drop_nulls().len()
    print(f"  overextension coverage: {100*cov/df.height:.1f}% ({cov}/{df.height})")
    print(f"  pe50 distribution: min={df['pe50'].min():.1f} p25={df['pe50'].quantile(.25):.1f} "
          f"median={df['pe50'].median():.1f} p75={df['pe50'].quantile(.75):.1f} max={df['pe50'].max():.1f}")

    hits = []
    for feat in ("pe50", "pe200"):
        sub = df.filter(pl.col(feat).is_not_null())
        if sub.height < 200:
            print(f"\n{feat}: N={sub.height} too small"); continue
        qs = [sub[feat].quantile(q) for q in (0.2, 0.4, 0.6, 0.8)]
        edges = sorted(set([sub[feat].min()] + qs + [sub[feat].max()]))
        print(f"\n=== {feat} (overextension %) quintiles — opt15 / apex15 WR ===")
        buckets = []
        for bi in range(len(edges) - 1):
            lo, hi = edges[bi], edges[bi + 1]
            last = (bi == len(edges) - 2)
            m = (pl.col(feat) >= lo) & ((pl.col(feat) <= hi) if last else (pl.col(feat) < hi))
            b = sub.filter(m)
            o = wrx(b["opt15"]); a = wrx(b["apex15"])
            buckets.append((lo, hi, o, a))
            print(f"  [{lo:+6.1f}..{hi:+6.1f}%] N={o[1]:4}  opt15={o[2]*100:5.1f}%  apex15={a[2]*100:5.1f}%")
        for bname, ix in (("opt15", 2), ("apex15", 3)):
            bot = buckets[0][ix]; top = buckets[-1][ix]
            d, z = zprop(top[0], top[1], bot[0], bot[1])
            if z is not None:
                tag = "  <-- |z|>=3 CANDIDATE" if abs(z) >= 3 else ("  (|z|>=2)" if abs(z) >= 2 else "")
                print(f"    {bname} top(stretched)-vs-bottom(mild): d={d*100:+.1f}pp z={z:+.2f}{tag}")
                if abs(z) >= 3:
                    hits.append((feat, bname, d, z))

    # also: deepest-overextension tail vs rest (the 'blow-off' tail)
    print("\n=== deep-overextension tail (pe50 >= p90) vs rest, opt15 ===")
    sub = df.filter(pl.col("pe50").is_not_null())
    p90 = sub["pe50"].quantile(0.90)
    tail = sub.filter(pl.col("pe50") >= p90); rest = sub.filter(pl.col("pe50") < p90)
    for bname in ("opt15", "apex15"):
        to = wrx(tail[bname]); ro = wrx(rest[bname])
        d, z = zprop(to[0], to[1], ro[0], ro[1])
        tag = "  <-- |z|>=3" if (z is not None and abs(z) >= 3) else ""
        print(f"  pe50>={p90:.0f}% (N={to[1]}): {bname}={to[2]*100:.1f}%  vs rest {ro[2]*100:.1f}%  z={ (z if z is not None else 0):+.2f}{tag}")
        if z is not None and abs(z) >= 3:
            hits.append((f"pe50>=p90", bname, d, z))

    print("\n=== VERDICT (overextension) ===")
    if hits:
        for f, b, d, z in hits:
            print(f"  CANDIDATE {f} [{b}]: d={d*100:+.1f}pp z={z:+.2f}  -> focused Stage-1 follow-up warranted")
    else:
        print("  Overextension does NOT separate reverts-from-continues at |z|>=3 on the option barrier.")
        print("  Combined with the other null features -> the divergence thread is FULLY CLOSED.")


if __name__ == "__main__":
    main()
