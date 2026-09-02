"""Filled-book occupancy sim — does the OAL strengthening booster actually improve the WR of
the POSITIONS THAT GET FILLED (the cascade-priority effect the pool analysis can't see)?

The Apex cascade fills by conviction (overall desc), MaxPos=14, same-symbol block, HOLD to
resolution (TP/SL touch or day-15). The osc-agree 75-84 cohort is +6pp apex WR; an additive
booster lifts their conviction so they fill AHEAD of non-osc signals. We replay slot occupancy
(no P&L) using each signal's resolution day (from the forward-path grids) and compare the
FILLED-book apex WR + fill count: v70 vs v70+OAL.

Under the asymmetric cost canon (untaxed wins), a higher filled-book WR = more untaxed winning
trades = real compounding benefit. If OAL does NOT raise filled-book WR here, it won't help in
the full MC either -> document null. If it does, escalate to the real APEX MC.
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
from label import UP_GRID, DN_GRID, _idx

TP_SIG, SL_SIG, WMAX = 1.092, 2.548, 15
MAXPOS = 14
THRESH = 75

df = pl.read_parquet(LEDGER)
df = df.filter(pl.col("vol_pct").is_finite() & (pl.col("vol_pct") > 0.3) & (pl.col("vol_pct") < 40))

iu, idn = _idx(UP_GRID, TP_SIG), _idx(DN_GRID, SL_SIG)
t_up = np.array([r[iu] for r in df["t_up"].to_list()], dtype=float)
t_dn = np.array([r[idn] for r in df["t_dn"].to_list()], dtype=float)
fwd = df["fwd_caldays"].to_numpy().astype(float)
# apex win = TP before SL within W; resolution day = first touch (cap WMAX), else WMAX (day-15 sell)
win = (t_up <= WMAX) & (t_up < t_dn)
res_day = np.minimum(np.minimum(np.where(t_up <= WMAX, t_up, 1e9), np.where(t_dn <= WMAX, t_dn, 1e9)), WMAX)
res_day = np.where(np.isfinite(res_day), res_day, WMAX).astype(int)
resolved = (t_up <= WMAX) | (t_dn <= WMAX) | (fwd >= WMAX)   # apex15 determined

base = pl.DataFrame({
    "symbol": df["symbol"], "date": df["date"], "overall": df["overall"],
    "c_rsi": df["c_rsi"], "c_stoch": df["c_stoch"], "c_macd": df["c_macd"], "c_trend": df["c_trend"],
    "win": win.astype(int), "res_day": res_day, "resolved": resolved,
})


def oal(overall, c_rsi, c_stoch, c_macd, c_trend, K, T, trend_gate):
    """Additive lift for osc-agree 75-84 signals (cascade-priority strengthening)."""
    lift = np.zeros(len(overall))
    cond = (overall >= 75) & (overall <= 84) & (c_rsi >= T) & (c_stoch >= T) & (c_macd >= T)
    if trend_gate is not None:
        cond = cond & (c_trend <= trend_gate)
    lift[cond] = K
    return np.minimum(100, overall + lift)


def fillbook(ov):
    """Replay slot occupancy. Returns (n_filled, filled_win_rate, filled_by_band, n_eligible)."""
    d = base.with_columns(pl.Series("ov2", ov)).filter(pl.col("ov2") >= THRESH)
    d = d.filter(pl.col("resolved"))                       # only determinable trades
    # sort by date asc, then conviction desc within date
    d = d.sort(["date", "ov2"], descending=[False, True])
    rows = d.to_dicts()
    open_free = {}          # symbol -> free date
    filled = []
    cur_date = None
    for r in rows:
        od = date.fromisoformat(r["date"])
        # free expired slots up to today
        for s in [s for s, fd in open_free.items() if fd <= od]:
            del open_free[s]
        if r["symbol"] in open_free:
            continue
        if len(open_free) >= MAXPOS:
            continue
        open_free[r["symbol"]] = od + timedelta(days=int(r["res_day"]))
        filled.append((r["win"], r["ov2"]))
    n = len(filled)
    wr = sum(w for w, _ in filled) / n if n else 0.0
    bands = {"75-79": [], "80-84": [], "85+": []}
    for w, o in filled:
        b = "75-79" if o <= 79 else ("80-84" if o <= 84 else "85+")
        bands[b].append(w)
    by = {b: (round(100 * sum(v) / len(v), 1) if v else None, len(v)) for b, v in bands.items()}
    return n, wr, by, d.height


print("=== Filled-book occupancy: v70 vs v70+OAL (Apex MaxPos=14, conviction-sorted, hold-to-resolution) ===")
b_ov = base["overall"].to_numpy().astype(float)
n0, wr0, by0, elig0 = fillbook(b_ov)
print("v70 baseline:   filled=%d  filledWR=%.2f%%  eligible=%d  bands=%s" % (n0, wr0 * 100, elig0, by0))

args = (base["c_rsi"].to_numpy(), base["c_stoch"].to_numpy(), base["c_macd"].to_numpy(),
        base["c_trend"].to_numpy())
print("\nOAL variants (K=lift, T=osc-threshold, tg=trend-gate):")
for K in (3, 5, 8):
    for T in (52, 55):
        for tg in (None, 50):
            ov2 = oal(b_ov, *args, K, T, tg)
            n, wr, by, elig = fillbook(ov2)
            print("  K=%d T=%d tg=%-4s  filled=%d(%+d)  filledWR=%.2f%% (%+.2f)  bands=%s" % (
                K, T, str(tg), n, n - n0, wr * 100, (wr - wr0) * 100, by))
