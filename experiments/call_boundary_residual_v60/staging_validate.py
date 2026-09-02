"""Staging validation — confirm VCBW reproduces the fast-sim on the REAL scoring
path (ScoreSimulator), per the v42 'validate on the real path' mandate.

Runs ScoreSimulator twice on a symbol sample (VCBW off, then on), isolates the
incremental VCBW delta per (sym,date), and checks it matches the closed-form
VCBW formula keyed on the scorer's own vol_pct. Also joins to the ledger's
win15 outcomes to show the 75+ WR15/N direction holds on the sim path.
"""
import os, sys, json, datetime as dt
sys.path.insert(0, os.path.abspath(os.getcwd()))  # ensure worktree-root imports
import polars as pl
import database.utils.scoring as S
from strategy_config import SCORING as CFG
from simulator import ScoreSimulator

# Param values from the single source of truth (robust to module-instance issues)
P = {k: getattr(CFG, "VCBW_" + k) for k in
     ["VL_LO", "VL_HI", "KL", "TL", "VD_LO", "VD_HI", "KD", "TD"]}


def _set_vcbw(flag):
    # toggle on every loaded scoring module instance (path-quirk proof)
    for m in list(sys.modules.values()):
        if m is not None and hasattr(m, "VCBW_ENABLED") and getattr(m, "__name__", "").endswith("scoring"):
            try:
                m.VCBW_ENABLED = flag
            except Exception:
                pass

CUTOFF = dt.date(2026, 5, 15)
SINCE = dt.date(2021, 5, 15)

led = pl.read_parquet(".cache/call_boundary_residual_v60/ledger_5y.parquet")
led = led.filter(pl.col("win15").is_not_null() & pl.col("vol_pct").is_not_null())
# sample symbols present in the ledger (cap for speed)
counts = led.group_by("symbol").len().sort("len", descending=True)
NS = int(os.environ.get("NSYM", "60"))
syms = counts["symbol"].to_list()[:NS]
win15 = {(r["symbol"], r["date"]): r["win15"] for r in led.select(["symbol", "date", "win15"]).to_dicts()}

print("loading ScoreSimulator for %d symbols ..." % len(syms))
sim = ScoreSimulator(syms, lookback_days=1825)

_set_vcbw(False)
off = sim.simulate(since=SINCE)
_set_vcbw(True)
on = sim.simulate(since=SINCE)


def vcbw_formula(o, v):
    if v is None or v <= 0:
        return 0.0
    if 70 <= o <= 74 and P["VL_HI"] > P["VL_LO"]:
        st = max(0.0, min(1.0, (P["VL_HI"] - v) / (P["VL_HI"] - P["VL_LO"])))
        return P["KL"] * st * (P["TL"] - o) if st > 0 else 0.0
    if 75 <= o <= 79 and P["VD_HI"] > P["VD_LO"]:
        wk = max(0.0, min(1.0, (v - P["VD_LO"]) / (P["VD_HI"] - P["VD_LO"])))
        return -P["KD"] * wk * (o - P["TD"]) if wk > 0 else 0.0
    return 0.0


# vol lookup from ledger by (sym,date)
volmap = {(r["symbol"], r["date"]): r["vol_pct"] for r in led.select(["symbol", "date", "vol_pct"]).to_dicts()}

checked = mism = fired = 0
examples = []
for k, o_off in off.items():
    sym, d = k
    diso = d.isoformat() if hasattr(d, "isoformat") else str(d)
    if d > CUTOFF:
        continue
    o_on = on.get(k)
    if o_on is None:
        continue
    if not (70 <= o_off <= 79):
        # outside VCBW band -> must be unchanged
        if o_on != o_off:
            mism += 1
        continue
    v = volmap.get((sym, diso))
    pred = int(max(0, min(100, round(o_off + vcbw_formula(o_off, v)))))
    checked += 1
    if o_on != o_off:
        fired += 1
    if o_on != pred:
        mism += 1
        if len(examples) < 6:
            examples.append({"sym": sym, "date": diso, "off": o_off, "on": o_on, "pred": pred, "vol": v})

# aggregate 75+ WR15/N on the sim path (off vs on), joined to ledger win15
def agg(scores):
    wins = n = 0
    for k, o in scores.items():
        sym, d = k
        diso = d.isoformat() if hasattr(d, "isoformat") else str(d)
        if d > CUTOFF or o < 75:
            continue
        w = win15.get((sym, diso))
        if w is None:
            continue
        n += 1; wins += w
    return (round(wins / n * 100, 2) if n else None), n

off_wr, off_n = agg(off)
on_wr, on_n = agg(on)
out = {"symbols": len(syms), "cohort_checked": checked, "vcbw_fired": fired,
       "delta_mismatches": mism, "examples": examples,
       "sim_ge75_off": {"WR15": off_wr, "N": off_n},
       "sim_ge75_on": {"WR15": on_wr, "N": on_n},
       "dWR": round(on_wr - off_wr, 2) if (on_wr and off_wr) else None,
       "dN": on_n - off_n}
with open(".cache/call_boundary_residual_v60/staging_validate.json", "w") as f:
    json.dump(out, f, indent=2)
print("RESULT " + json.dumps(out))
