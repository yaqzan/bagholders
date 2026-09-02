"""Thread 2 — 15DTE/WR7 vs 30DTE/WR15 under the NEW wide SL (TP30/SL70).

The barrier-agnostic forward-path ledger lets me derive the per-trade outcome for ANY option
(premium-mult, TP%, SL%, hold window) on the SAME v70 75+ signals, decomposed TP / SL / expire:
  underlying_move = pct% * 2 * premium_mult * sigma   (DELTA=0.5)
  30DTE premium_mult=1.82 -> TP30=+1.092sigma  SL70=-2.548sigma  ; WR15 window=15
  15DTE premium_mult=1.29 -> TP30=+0.774sigma  SL70=-1.806sigma  ; WR7  window=7

Key questions:
  (a) Is 15DTE/WR7 per-trade WR really ~= or > 30DTE/WR15? (user's claim)
  (b) WHY is 15DTE astronomically worse at portfolio scale despite similar WR? -> decompose the
      LOSS composition: 15DTE's tighter SL (1.806 vs 2.548 sigma) + shorter window should produce
      MORE full -70% SL hits (vs cheaper day-N expires) => worse per-trade EV even at equal TP-WR.
EV(premium) = tp_rate*0.30 + sl_rate*(-0.70) + expire_rate*hard   (hard: 30DTE day15 -0.40, 15DTE day7 -0.45)
"""
import os, sys
import numpy as np
import polars as pl
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")
from label import UP_GRID, DN_GRID, _idx

df = pl.read_parquet(LEDGER)
df = df.filter(pl.col("vol_pct").is_finite() & (pl.col("vol_pct") > 0.3) & (pl.col("vol_pct") < 40))
T_UP = np.array(df["t_up"].to_list(), dtype=np.int16)   # (N, len(UP_GRID))
T_DN = np.array(df["t_dn"].to_list(), dtype=np.int16)
FWD = df["fwd_caldays"].to_numpy().astype(int)
OV = df["overall"].to_numpy()


def decompose(tp_sig, sl_sig, W, hard):
    iu, idn = _idx(UP_GRID, tp_sig), _idx(DN_GRID, sl_sig)
    ttp = T_UP[:, iu].astype(int)
    tsl = T_DN[:, idn].astype(int)
    tp = (ttp <= W) & (ttp < tsl)
    sl = (tsl <= W) & (tsl <= ttp)
    determined = tp | sl | (FWD >= W)
    expire = determined & ~tp & ~sl
    return tp, sl, expire, determined


def report(name, tp_sig, sl_sig, W, sl_loss, hard, mask):
    tp, sl, expire, det = decompose(tp_sig, sl_sig, W, hard)
    m = mask & det
    n = int(m.sum())
    if n == 0:
        print("  %-22s (no resolved)" % name); return
    tpr = tp[m].mean(); slr = sl[m].mean(); exr = expire[m].mean()
    ev = tpr * 0.30 + slr * sl_loss + exr * hard      # sl_loss = the config's SL premium % (e.g. -1.00 at SL100)
    # capital-velocity proxy: EV per trading-DAY held (faster recycle = more trades/yr).
    # mean resolution day among resolved (cap W); rough.
    print("  %-22s N=%5d  TP=%.1f%% SL=%.1f%%(@%.0f%%) exp=%.1f%%  EV/trade=%+.4f  EV/day~%+.5f" % (
        name, n, tpr * 100, slr * 100, sl_loss * 100, exr * 100, ev, ev / W))
    return dict(n=n, tp=tpr, sl=slr, ex=exr, ev=ev)


#       name                 TP_sig  SL_sig   W  sl_loss hard
CFGS = [
    ("30DTE WR15 (Apex)", 1.092, 2.548, 15, -0.70, -0.40),
    ("30DTE WR7  SL70",   1.092, 2.548, 7,  -0.70, -0.45),
    ("15DTE WR7  SL70",   0.774, 1.806, 7,  -0.70, -0.45),
    ("15DTE WR7  SL85",   0.774, 2.193, 7,  -0.85, -0.45),   # SL-85% = 2.193sigma
    ("15DTE WR7  SL100",  0.774, 2.580, 7,  -1.00, -0.45),   # SL-100% = 2.58sigma (total-loss on stop)
    ("15DTE WR15 SL100",  0.774, 2.580, 15, -1.00, -0.45),
]
for lo, hi, nm in [(75, 99, "75+ POOL"), (70, 74, "70-74 (overflow band)"),
                   (75, 79, "75-79"), (85, 99, "85+")]:
    mask = (OV >= lo) & (OV <= hi)
    print("\n=== %s (N=%d) ===" % (nm, int(mask.sum())))
    for name, tps, sls, W, sll, hard in CFGS:
        report(name, tps, sls, W, sll, hard, mask)
print("\n=== PER-YEAR: is there a window where 15DTE/WR7 beats 30DTE/WR15 on EV/day? ===")
yr = np.array([int(d[:4]) for d in df["date"].to_list()])
p75 = OV >= 75
for y in range(2016, 2027):
    m = p75 & (yr == y)
    if m.sum() < 50:
        continue
    r30 = report.__wrapped__ if False else None
    # 30DTE WR15
    tp, sl, ex, det = decompose(1.092, 2.548, 15, -0.40)
    mm = m & det; n30 = int(mm.sum())
    ev30 = tp[mm].mean()*0.30 + sl[mm].mean()*(-0.70) + ex[mm].mean()*(-0.40)
    # 15DTE WR7 SL100 (best 15DTE)
    tp2, sl2, ex2, det2 = decompose(0.774, 2.580, 7, -0.45)
    m2 = m & det2; n15 = int(m2.sum())
    ev15 = tp2[m2].mean()*0.30 + sl2[m2].mean()*(-1.00) + ex2[m2].mean()*(-0.45)
    flag = "  <-- 15DTE WINS" if (ev15/7) > (ev30/15) else ""
    print("  %d  N~%5d  30DTE EV/day=%+.5f   15DTE/WR7/SL100 EV/day=%+.5f%s" % (
        y, n30, ev30/15, ev15/7, flag))
print("\nNote: EV is PER-TRADE premium (untaxed wins, asymmetric canon). Portfolio compounding")
print("(velocity, theta, bounded-fill gap on smaller premium, forced-exit spread) is the MC's job.")
