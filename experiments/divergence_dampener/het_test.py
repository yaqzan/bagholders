"""Per-stock-TYPE heterogeneity test (the LEGITIMATE form of 'adjust per stock').
The user's instinct: maybe the universe-average divergence z~0 hides a real effect that's
strong for NVDA-LIKE (high realized-vol / strong-trend) names and washed out by the rest.

Test: bucket the divergence cohorts by realized vol (vol_pct, the generic stock-type axis we
have) and re-measure option-barrier WR vs the 75+ base WITHIN each bucket. If high-vol names
show the dip cohort BEATING base (or the fade cohort clearly BELOW), that's a real per-type
discriminator a generic (vol-conditioned) knob could capture. If flat across vol -> even the
legitimate per-type version is null.
"""
import os, sys, math
import polars as pl
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
import label as L  # noqa

DIP = os.path.join(_ROOT, ".cache", "divergence_dampener", "ledger_v70_wide.parquet")   # lift cohort
DISC = os.path.join(_ROOT, ".cache", "divergence_dampener", "ledger_disc.parquet")        # fade cohort
BASE75 = {"opt15": 0.475, "apex15": 0.700}


def zprop(w1, n1, w2, n2):
    if n1 < 5 or n2 < 5: return None, None
    p1, p2 = w1/n1, w2/n2
    pp = (w1+w2)/(n1+n2); se = math.sqrt(pp*(1-pp)*(1/n1+1/n2))
    return p1-p2, ((p1-p2)/se if se else None)


def addb(df):
    return df.with_columns([L.win_expr(0.901,0.772,15).alias("opt15"),
                            L.win_expr(1.092,2.548,15).alias("apex15")])


def by_vol(df, label, compare='base'):
    df = addb(df)
    v = df["vol_pct"]
    edges = [v.min()] + [v.quantile(q) for q in (0.2,0.4,0.6,0.8)] + [v.max()]
    edges = sorted(set(round(e,4) for e in edges))
    print(f"\n=== {label}: option WR by realized-vol bucket (vol_pct = 60d daily vol %) ===")
    print(f"  (compare vs {'75+ base' if compare=='base' else 'whole-cohort'} : opt15 {BASE75['opt15']*100:.1f}% / apex15 {BASE75['apex15']*100:.1f}%)")
    for bi in range(len(edges)-1):
        lo, hi = edges[bi], edges[bi+1]; last = bi==len(edges)-2
        m = (pl.col("vol_pct")>=lo) & ((pl.col("vol_pct")<=hi) if last else (pl.col("vol_pct")<hi))
        b = df.filter(m)
        cells=[]
        for nm in ("opt15","apex15"):
            r=b[nm].drop_nulls()
            if r.len()<5: cells.append(f"{nm}=-"); continue
            wr=r.mean()
            d,z = zprop(int(r.sum()), r.len(), round(BASE75[nm]*r.len()), r.len())
            cells.append(f"{nm}={wr*100:4.1f}% (vs base z={z:+.1f})")
        # vol_pct is already 60d daily vol in % (s*100). NVDA's own daily vol ~3-5%.
        flag = "  <-- NVDA's vol level (~4%/d)" if (lo <= 4.0 <= hi) else ""
        print(f"  vol[{lo:5.2f}-{hi:6.2f}%/d] N={b.height:6}  " + "  ".join(cells) + flag)


dip = pl.read_parquet(DIP)
disc = pl.read_parquet(DISC)
print(f"DIP(lift) N={dip.height}   DISC(fade) N={disc.height}")
by_vol(dip,  "DIP / lift cohort (trend<=45 & stoch>=80) — want HIGH-vol to BEAT base")
by_vol(disc, "FADE cohort (trend>=85 & stoch<=25) — want HIGH-vol to be clearly BELOW base")
print("\nVERDICT: if the high-vol (NVDA-like) bucket doesn't diverge from base, a vol-conditioned")
print("(generic per-type) knob has nothing to capture either -> per-stock-type also null.")
