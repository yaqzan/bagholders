"""Thread 1 x Overflow synergy — does WEEKLY-MATURITY filtering improve the OVERFLOW book?

Finding (weekly_mine.py): strong prior-completed-week weekly (wadj_completed HIGH tercile) => ~4-5pp
LOWER apex WR, honest (dow-stable, persists Friday), consistent across [70,84]. Pool base 70.0% ==
apex break-even (SL70/TP30). So the high-weekly cohort is the sub-break-even tail.

Tension: the book is supply-STARVED (idle), so overflow (promote 70-74) helps by FILLING idle slots.
But weekly-maturity wants to REMOVE the high-weekly losers (cuts supply). Net is the MC's job — BUT
the necessary condition is: a weekly-FILTERED overflow must have higher filled-book WR than plain
overflow at comparable fill count. If filtering barely moves filled-WR, the weekly edge is the same
supply-for-noise null as the component reweight. If it clearly lifts filled-WR, escalate to the APEX
MC for the real test (does filtering the sub-BE losers raise the COLLAPSE-safe alloc ceiling?).

Occupancy only (no P&L): MaxPos=14, conviction-sort (overall desc within date), same-symbol block,
hold-to-resolution (apex TP/SL touch or day-15). Filled-WR = untaxed-win rate of filled trades.
"""
import os, sys
from datetime import date, timedelta
import polars as pl
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")
from label import UP_GRID, DN_GRID, _idx  # noqa: E402

TP_SIG, SL_SIG, WMAX = 1.092, 2.548, 15
MAXPOS = 14

df = pl.read_parquet(LEDGER)
df = df.filter(pl.col("vol_pct").is_finite() & (pl.col("vol_pct") > 0.3) & (pl.col("vol_pct") < 40))
iu, idn = _idx(UP_GRID, TP_SIG), _idx(DN_GRID, SL_SIG)
t_up = np.array([r[iu] for r in df["t_up"].to_list()], dtype=float)
t_dn = np.array([r[idn] for r in df["t_dn"].to_list()], dtype=float)
fwd = df["fwd_caldays"].to_numpy().astype(float)
win = (t_up <= WMAX) & (t_up < t_dn)
res_day = np.minimum(np.minimum(np.where(t_up <= WMAX, t_up, 1e9), np.where(t_dn <= WMAX, t_dn, 1e9)), WMAX)
res_day = np.where(np.isfinite(res_day), res_day, WMAX).astype(int)
resolved = (t_up <= WMAX) | (t_dn <= WMAX) | (fwd >= WMAX)

base = pl.DataFrame({
    "symbol": df["symbol"], "date": df["date"], "overall": df["overall"],
    "w_adj": df["w_adj"], "wadj_completed": df["wadj_completed"],
    "win": win.astype(int), "res_day": res_day, "resolved": resolved,
})
b_ov = base["overall"].to_numpy().astype(float)
b_wadj = base["w_adj"].to_numpy().astype(float)
b_wcomp = base["wadj_completed"].to_numpy().astype(float)

# tercile cut for "mature weekly" (computed on the [70,84] tradeable+overflow universe, resolved)
u = base.filter((pl.col("overall") >= 70) & (pl.col("overall") <= 84) & pl.col("resolved"))
WADJ_HI = u["w_adj"].quantile(0.6667)
WCOMP_HI = u["wadj_completed"].quantile(0.6667)
print("mature-weekly top-tercile cuts (on [70,84] resolved):  w_adj>%.2f   wadj_completed>%.2f"
      % (WADJ_HI, WCOMP_HI))


def fillbook(ov, thresh, drop_mask=None):
    """Replay slot occupancy on eligible (ov>=thresh & not dropped). Returns (n, wr, bands, elig)."""
    d = base.with_columns(pl.Series("ov2", ov))
    if drop_mask is not None:
        d = d.with_columns(pl.Series("drop", drop_mask))
        d = d.filter(~pl.col("drop"))
    d = d.filter((pl.col("ov2") >= thresh) & pl.col("resolved"))
    d = d.sort(["date", "ov2"], descending=[False, True])
    rows = d.to_dicts()
    open_free, filled = {}, []
    for r in rows:
        od = date.fromisoformat(r["date"])
        for s in [s for s, fd in open_free.items() if fd <= od]:
            del open_free[s]
        if r["symbol"] in open_free or len(open_free) >= MAXPOS:
            continue
        open_free[r["symbol"]] = od + timedelta(days=int(r["res_day"]))
        filled.append((r["win"], r["ov2"], r["overall"]))
    n = len(filled)
    wr = sum(w for w, _, _ in filled) / n if n else 0.0
    # split filled by original-overall band (primary 75+ vs promoted 70-74)
    prim = [w for w, _, o0 in filled if o0 >= 75]
    ovf = [w for w, _, o0 in filled if o0 < 75]
    pr = (round(100 * sum(prim) / len(prim), 1), len(prim)) if prim else (None, 0)
    ov_ = (round(100 * sum(ovf) / len(ovf), 1), len(ovf)) if ovf else (None, 0)
    return n, wr, pr, ov_, d.height


def wmd(ov, feat, FLOOR, K, HI, LOband=70, HIband=84):
    """Smooth weekly-maturity dampener: lower score of high-weekly calls in [LOband,HIband].
       weakness ramps 0->1 as feat goes median(approx HI*0.5)->HI ; overall -= K*weakness*(overall-FLOOR)."""
    out = ov.copy()
    cond = (ov >= LOband) & (ov <= HIband)
    LO = HI * 0.4
    weak = np.clip((feat - LO) / (HI - LO + 1e-9), 0.0, 1.0)
    out[cond] = ov[cond] - K * weak[cond] * (ov[cond] - FLOOR)
    return out


print("\n=== Filled-book occupancy (Apex MaxPos=14, hold-to-resolution) ===")
n, wr, pr, ov_, el = fillbook(b_ov, 75)
print("A  75+ only            filled=%d  WR=%.2f%%  prim75+=%s  ovf=%s  elig=%d" % (n, wr*100, pr, ov_, el))
nB, wrB, prB, ovB, elB = fillbook(b_ov, 70)
print("B  +overflow (plain)   filled=%d  WR=%.2f%%  prim75+=%s  ovf70-74=%s  elig=%d"
      % (nB, wrB*100, prB, ovB, elB))

# C: overflow but HARD-DROP the mature-weekly 70-74 (top tercile by wadj_completed / w_adj)
for fnm, feat, cut in [("wadj_completed", b_wcomp, WCOMP_HI), ("w_adj", b_wadj, WADJ_HI)]:
    drop = (b_ov >= 70) & (b_ov <= 74) & (feat > cut)
    n, wr, pr, ov_, el = fillbook(b_ov, 70, drop_mask=drop)
    print("C  +ovf, DROP mature 70-74 (%s>%.1f)  filled=%d(%+d)  WR=%.2f%%(%+.2f)  ovf=%s"
          % (fnm, cut, n, n-nB, wr*100, (wr-wrB)*100, ov_))

# D: smooth WMD dampener across [70,84] (demotes mature high-weekly down the cascade / out)
print("\n  smooth WMD dampener over [70,84] (feat=wadj_completed), then +overflow @ thresh70:")
for K in (0.3, 0.5, 0.8):
    for FLOOR in (55, 62):
        ov2 = wmd(b_ov, b_wcomp, FLOOR, K, WCOMP_HI * 1.0)
        n, wr, pr, ov_, el = fillbook(ov2, 70)
        print("  D  K=%.1f FLOOR=%d   filled=%d(%+d)  WR=%.2f%%(%+.2f)  prim=%s ovf=%s"
              % (K, FLOOR, n, n-nB, wr*100, (wr-wrB)*100, pr, ov_))

print("\nRead: does any C/D lift filled-WR over B (plain overflow) at acceptable fill count? If the")
print("lift is <~0.5pp it's supply-for-noise (null, like component reweight). If clear, the real test")
print("is the APEX MC: does weekly-filtered overflow raise the COLLAPSE-safe alloc ceiling > 0.04?")
