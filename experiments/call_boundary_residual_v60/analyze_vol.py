"""Phase A.3 — validate the two-sided vol-confidence thesis on v60 CALL 70-89.

Checks:
  (1) vol-tercile x tier grid (WR15 + N): is high-vol bad at every tier, and are
      low-vol 70-74 calls promotable (high WR15)?
  (2) within 75-79: high-vol cohort WR15 across WR7/WR15/WR30 (W2 consistency).
  (3) within 75-79: high-vol cohort WR15 by calendar year (W3 consistency).
  (4) low-vol 70-74 promotable sub-cohort WR15 + N, and its WR15 vs the
      75-79 baseline (would promotion add high-quality N?).

vol terciles computed on the WHOLE 70-89 population so the cut is a single
global volatility scale (what a scoring-time dampener would key on).
"""
import os, json, math
import polars as pl

TAG = os.environ.get("TAG", "5y")
df = pl.read_parquet(".cache/call_boundary_residual_v60/ledger_%s.parquet" % TAG)
df = df.filter(pl.col("win15").is_not_null() & pl.col("vol_pct").is_not_null())

# global vol terciles
vq33 = df["vol_pct"].quantile(0.33)
vq66 = df["vol_pct"].quantile(0.66)


def wr(sub, col="win15"):
    s = sub.filter(pl.col(col).is_not_null())
    return (round(s[col].mean() * 100, 2) if s.height else None), s.height


def zb(p, n, p0):
    if not n or p0 <= 0 or p0 >= 1:
        return 0.0
    return round((p - p0) / math.sqrt(p0 * (1 - p0) / n), 2)


out = {"tag": TAG, "vol_q33": round(float(vq33), 3), "vol_q66": round(float(vq66), 3)}

# (1) grid
grid = {}
for tlo, thi in [(70, 74), (75, 79), (80, 84), (85, 89)]:
    tier = df.filter((pl.col("overall") >= tlo) & (pl.col("overall") <= thi))
    base_p = tier["win15"].mean()
    row = {"tier_WR15": round(base_p * 100, 2), "tier_N": tier.height}
    for vname, expr in [("vlo", pl.col("vol_pct") <= vq33),
                        ("vmid", (pl.col("vol_pct") > vq33) & (pl.col("vol_pct") <= vq66)),
                        ("vhi", pl.col("vol_pct") > vq66)]:
        b = tier.filter(expr)
        w, n = wr(b)
        row[vname] = {"WR15": w, "N": n, "z_vs_tier": zb(b["win15"].mean(), n, base_p) if n else None}
    grid["%d_%d" % (tlo, thi)] = row
out["vol_tier_grid"] = grid

# (2) 75-79 high-vol across WR7/15/30
t = df.filter((pl.col("overall") >= 75) & (pl.col("overall") <= 79))
hv = t.filter(pl.col("vol_pct") > vq66)
out["hv_7579_multibarrier"] = {
    "N": hv.height,
    "WR7": wr(hv, "win7")[0], "WR15": wr(hv, "win15")[0], "WR30": wr(hv, "win30")[0],
    "tier_WR7": wr(t, "win7")[0], "tier_WR15": wr(t, "win15")[0], "tier_WR30": wr(t, "win30")[0],
}

# (3) by year
hv_year = {}
hv = hv.with_columns(pl.col("date").str.slice(0, 4).alias("yr"))
for yr in sorted(set(hv["yr"].to_list())):
    sub = hv.filter(pl.col("yr") == yr)
    w, n = wr(sub)
    # tier baseline that year
    ty = t.with_columns(pl.col("date").str.slice(0, 4).alias("yr")).filter(pl.col("yr") == yr)
    tw, tn = wr(ty)
    hv_year[yr] = {"hv_WR15": w, "hv_N": n, "tier_WR15": tw, "delta": round((w - tw), 2) if (w and tw) else None}
out["hv_7579_by_year"] = hv_year

# (4) promotable 70-74 low-vol + high-trend
t70 = df.filter((pl.col("overall") >= 70) & (pl.col("overall") <= 74))
base7579 = t["win15"].mean() * 100
tq = t70["trend"].quantile(0.66) if t70["trend"].null_count() < t70.height else None
promote = {}
for name, expr in [
    ("vlo", pl.col("vol_pct") <= vq33),
    ("vlo_trendhi", (pl.col("vol_pct") <= vq33) & (pl.col("trend") > (tq if tq is not None else 1e9))),
    ("vlo_72_74", (pl.col("vol_pct") <= vq33) & (pl.col("overall") >= 72)),
]:
    b = t70.filter(expr)
    w, n = wr(b)
    promote[name] = {"WR15": w, "N": n, "vs_7579_base": round(w - base7579, 2) if w else None}
out["promote_7074"] = {"base_7579_WR15": round(base7579, 2), "trend_q66": round(float(tq), 2) if tq else None, "candidates": promote}

outp = ".cache/call_boundary_residual_v60/vol_%s.json" % TAG
with open(outp, "w") as f:
    json.dump(out, f, indent=2)
print("WROTE", outp)
print(json.dumps(out, indent=2))
