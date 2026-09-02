"""(b) Discriminator probe: within the overbought-in-uptrend CALL cohort (trend>=85 & stoch<=25
& overall>=70 — the dampener-failed cohort), does ANY orthogonal feature split 'reverts'
(option loss) from 'continues' (option win) on the OPTION barrier (opt15/apex15)?

If a feature shows a MONOTONE quintile gradient with top-vs-bottom |z|>=3 on the option barrier,
it's a candidate 'reverts-vs-continues' discriminator -> a real rescue for the divergence family.
If nothing clears, the cohort is genuinely unseparable -> close the whole thread.

Candidates (orthogonal to the trend/stoch components that define the cohort):
  pct_ema50, pct_ema200 (OVEREXTENSION), bb_position, regime_composite, score_vel7,
  vol_mag, w_adj (weekly confirm), c_macd, c_bb.  Plus vol_signal categories.
"""
import os, sys, math
import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
import label as L  # noqa

DISC = os.path.join(_ROOT, ".cache", "divergence_dampener", "ledger_disc.parquet")
BARRIERS = [("opt15", 0.901, 0.772, 15), ("apex15", 1.092, 2.548, 15), ("hold15", 0.901, 7.07, 15)]
# 75+ overall base (real ledger) for reference
BASE75 = {"opt15": 0.475, "apex15": 0.700, "hold15": 0.789}


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
    df = pl.read_parquet(DISC).with_columns([L.win_expr(tp, sl, w).alias(nm) for nm, tp, sl, w in BARRIERS])
    print(f"cohort N={df.height} window={df['date'].min()}..{df['date'].max()}")
    assert df["date"].max() <= "2026-05-15", "HOLDOUT LEAK"

    print("\n=== cohort base WR (vs 75+ overall base) ===")
    for nm, *_ in BARRIERS:
        _, n, m = wrx(df[nm])
        print(f"  {nm:7}: cohortWR={m*100:5.1f}% (resolvedN={n})  [75+ base {BASE75[nm]*100:.1f}%]")

    NUM = ["pct_ema50", "pct_ema200", "bb_position", "regime_composite", "score_vel7",
           "vol_mag", "w_adj", "c_macd", "c_bb"]
    print("\n=== feature coverage (non-null %) ===")
    for f in NUM:
        nn = df[f].drop_nulls().len()
        print(f"  {f:18}: {100*nn/df.height:5.1f}% non-null (N={nn})")

    print("\n=== QUINTILE gradient per feature (opt15 / apex15 WR), top-vs-bottom z ===")
    hits = []
    for f in NUM:
        sub = df.filter(pl.col(f).is_not_null())
        if sub.height < 200:
            print(f"  {f:18}: N={sub.height} too small"); continue
        # quintile edges
        qs = [sub[f].quantile(q) for q in (0.2, 0.4, 0.6, 0.8)]
        edges = sorted(set([sub[f].min()] + qs + [sub[f].max()]))
        if len(edges) < 3:
            print(f"  {f:18}: degenerate distribution"); continue
        line = f"  {f:18}: "
        buckets = []
        for bi in range(len(edges) - 1):
            lo, hi = edges[bi], edges[bi + 1]
            last = (bi == len(edges) - 2)
            m = (pl.col(f) >= lo) & ((pl.col(f) <= hi) if last else (pl.col(f) < hi))
            b = sub.filter(m)
            o = wrx(b["opt15"]); a = wrx(b["apex15"])
            buckets.append((lo, hi, o, a))
            line += f"[{lo:+.1f}..{hi:+.1f}] o={o[2]*100:.0f}%/a={a[2]*100:.0f}%(N={o[1]})  " if o[2] is not None else f"[{lo:.1f}..{hi:.1f}] -  "
        print(line)
        # top vs bottom on opt15 and apex15
        for bi_name, ix in (("opt15", 2), ("apex15", 3)):
            bot = buckets[0][ix]; top = buckets[-1][ix]
            if bot[1] >= 5 and top[1] >= 5:
                d, z = zprop(top[0], top[1], bot[0], bot[1])
                tag = ""
                if z is not None and abs(z) >= 3:
                    tag = "  <-- |z|>=3 candidate"
                    hits.append((f, bi_name, d, z))
                if z is not None and abs(z) >= 2:
                    print(f"        {bi_name} top-vs-bottom: d={d*100:+.1f}pp z={z:+.2f}{tag}")

    print("\n=== vol_signal categories (opt15 WR) ===")
    for cat in df["vol_signal"].drop_nulls().unique().to_list():
        b = df.filter(pl.col("vol_signal") == cat)
        o = wrx(b["opt15"]); rest = df.filter(pl.col("vol_signal") != cat)
        ro = wrx(rest["opt15"])
        if o[1] >= 5 and ro[1] >= 5:
            d, z = zprop(o[0], o[1], ro[0], ro[1])
            tag = "  <-- |z|>=3" if (z is not None and abs(z) >= 3) else ""
            print(f"  {cat:14} opt15={o[2]*100:5.1f}% (N={o[1]:5})  vs rest {ro[2]*100:.1f}%  z={ (z if z is not None else 0):+.2f}{tag}")
            if z is not None and abs(z) >= 3:
                hits.append((f"vol_signal={cat}", "opt15", d, z))

    print("\n=== VERDICT ===")
    if hits:
        print("  Candidate discriminator(s) found (|z|>=3 on an option barrier):")
        for f, b, d, z in hits:
            print(f"    {f} [{b}]: d={d*100:+.1f}pp z={z:+.2f}")
        print("  -> worth a focused Stage-1 cohort-z + monotonicity check before any mechanism.")
    else:
        print("  NO feature splits the overbought-in-uptrend cohort by |z|>=3 on the option barrier.")
        print("  -> the cohort is genuinely unseparable by these orthogonal features. Close the thread.")


if __name__ == "__main__":
    main()
