"""Does trading the 70-74 band actually ADD filled +EV trades? (the overflow question, re-opened
under the asymmetric cost canon + Apex's high-DD risk budget).

70-74 apex WR ~68.1% ~= 75-79's 69.8% (same bucket). At Apex TP30/SL70 with untaxed wins, ~68%
is positive-EV once losses include day-15 expires (not all -70%). The only question the pool
analysis left open: do 70-74 signals FILL idle slots (book is full ~68% of the time on average,
but signals CLUSTER -> quiet days have empty slots), and at what realized WR when they do?

Occupancy replay (MaxPos=14, conviction-sorted desc, same-symbol block, hold-to-resolution from
the forward-path grids). No P&L/DD here -> that's the MC's job; this is the supply/WR gate.
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

TP_SIG, SL_SIG, WMAX, MAXPOS = 1.092, 2.548, 15, 14

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
    "c_rsi": df["c_rsi"], "c_stoch": df["c_stoch"], "c_macd": df["c_macd"], "c_ta": df["c_ta"],
    "win": win.astype(int), "res_day": res_day, "resolved": resolved,
})
TA_HI = float(base.filter((pl.col("overall") >= 70) & (pl.col("overall") <= 74))["c_ta"].quantile(0.67))


def fillbook(elig_expr, label):
    d = base.filter(elig_expr & pl.col("resolved")).sort(["date", "overall"], descending=[False, True])
    rows = d.to_dicts()
    open_free = {}
    filled = []
    for r in rows:
        od = date.fromisoformat(r["date"])
        for s in [s for s, fd in open_free.items() if fd <= od]:
            del open_free[s]
        if r["symbol"] in open_free or len(open_free) >= MAXPOS:
            continue
        open_free[r["symbol"]] = od + timedelta(days=int(r["res_day"]))
        filled.append((r["win"], r["overall"]))
    n = len(filled)
    wr = sum(w for w, _ in filled) / n if n else 0.0
    n7074 = sum(1 for w, o in filled if o <= 74)
    wr7074 = (sum(w for w, o in filled if o <= 74) / n7074) if n7074 else None
    n75 = n - n7074
    wr75 = (sum(w for w, o in filled if o >= 75) / n75) if n75 else None
    print("%-28s filled=%5d  WR=%.2f%% | of which 70-74: n=%4d WR=%s ; 75+: n=%5d WR=%.2f%%"
          % (label, n, wr * 100, n7074, ("%.2f%%" % (wr7074 * 100) if wr7074 else "n/a"),
             n75, (wr75 * 100 if wr75 else 0)))
    return n, wr


print("=== Does enabling 70-74 add filled +EV trades? (MaxPos=14 occupancy, 10y) ===")
o = pl.col("overall")
fillbook(o >= 75, "baseline (75+ only)")
fillbook(o >= 70, "include ALL 70-74")
fillbook((o >= 75) | ((o >= 70) & (o <= 74) & (pl.col("c_ta") >= TA_HI)), "include 70-74 TA-hi only")
osc = (pl.col("c_rsi") >= 52) & (pl.col("c_stoch") >= 52) & (pl.col("c_macd") >= 52)
fillbook((o >= 75) | ((o >= 70) & (o <= 74) & osc), "include 70-74 osc-aligned only")
fillbook(o >= 72, "include 72-74 (milder)")
