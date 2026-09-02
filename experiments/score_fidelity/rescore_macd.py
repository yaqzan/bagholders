"""MACD phase-smoothing A/B: faithfully re-score base v74 vs a smooth-phase MACD.

The fidelity bug (calculate_macd_score, core.py): the MOMENTUM-PHASE term (40% of the
MACD score) is a 4-branch piecewise function of (vel_norm_phase, accel_norm_phase) with a
hard ~0.6 jump in phase_raw at velocity=0 (bottoming -0.3 -> building +0.3) = ~27 phase-pts
= ~11 macd-pts from infinitesimal noise. The fix replaces the branches with a CONTINUOUS
soft-gate blend of the SAME four branch formulas (sigmoid gates on v,a; width W) — identical
away from the boundaries, smooth across them. No new signal, just removes the discontinuity.

Env: MACD_SMOOTH (0/1), MACD_PHASE_W (default 0.15), DUMP_OUT (required), STRIDE (default 3).
Read-only (ScoreSimulator, no DB writes). Dumps (symbol,date,overall,macd) for overall>=55.
"""
import os
import sys
from datetime import date

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.models.core import Stock
from simulator import ScoreSimulator

_PHASE_W = float(os.environ.get("MACD_PHASE_W", "0.15"))


def _smooth_macd_score(self, target_date, lookback_months=9, debug=False, weekly=False,
                       _ind_cache=None, _ph_cache=None):
    from database.models.technical import Indicator, WeeklyIndicator
    from datetime import timedelta
    IndicatorModel = WeeklyIndicator if weekly else Indicator
    lookback_days = int(lookback_months * 30.4)
    start_date = target_date - timedelta(weeks=lookback_days if weekly else 0,
                                         days=0 if weekly else lookback_days)
    if _ind_cache is not None:
        rows = [i for i in _ind_cache if i.date >= start_date
                and i.macd is not None and i.macd_signal is not None]
    else:
        iq = self.weekly_indicators if weekly else self.indicators
        rows = list(iq.where((IndicatorModel.date <= target_date) & (IndicatorModel.date >= start_date)
                             & (IndicatorModel.macd.is_null(False)) & (IndicatorModel.macd_signal.is_null(False)))
                    .order_by(IndicatorModel.date.desc()))
    if len(rows) < 10:
        return None
    hists = []
    for r in rows:
        if r.macd_hist is not None:
            hists.append(float(r.macd_hist))
        elif r.macd is not None and r.macd_signal is not None:
            hists.append(float(r.macd) - float(r.macd_signal))
    if len(hists) < 6:
        return None
    velocities = [hists[i] - hists[i + 1] for i in range(len(hists) - 1)]
    accelerations = [velocities[i] - velocities[i + 1] for i in range(len(velocities) - 1)]
    smooth_n = min(3, len(velocities)); avg_velocity = sum(velocities[:smooth_n]) / smooth_n
    accel_n = min(3, len(accelerations)); avg_accel = sum(accelerations[:accel_n]) / accel_n if accel_n > 0 else 0

    def normalize_tanh(value, series, sensitivity=2.0):
        if len(series) < 5:
            return 0
        s = sorted(series); q1 = s[len(s) // 4]; q3 = s[3 * len(s) // 4]; iqr = q3 - q1
        return 0 if iqr == 0 else float(np.tanh(value / iqr * sensitivity))

    vel_norm = normalize_tanh(avg_velocity, velocities, 1.5)
    velocity_score = 50 + 45 * vel_norm

    # === SMOOTH PHASE: continuous soft-gate blend of the SAME 4 branch formulas ===
    v = normalize_tanh(avg_velocity, velocities, 1.2)
    a = normalize_tanh(avg_accel, accelerations, 1.2)
    W = _PHASE_W
    gv = 0.5 * (1.0 + np.tanh(v / W))   # ~1 for v>0 (building/peaking), ~0 for v<0
    ga = 0.5 * (1.0 + np.tanh(a / W))   # ~1 for a>0
    peaking   = 0.5 + 0.5 * v + 0.3 * abs(a)      # v>0, a<0
    building  = 0.3 + 0.4 * v + 0.2 * a           # v>0, a>=0
    bottoming = -0.3 + 0.4 * abs(a) + 0.2 * abs(v) # v<=0, a>0
    declining = -0.5 - 0.3 * abs(v) - 0.2 * abs(a) # v<=0, a<=0
    phase_raw = (gv * ga * building + gv * (1 - ga) * peaking
                 + (1 - gv) * ga * bottoming + (1 - gv) * (1 - ga) * declining)
    phase_score = 50 + 45 * max(-1, min(1, phase_raw))

    hist_norm = normalize_tanh(hists[0], hists, 1.5)
    hist_score = 50 - 40 * hist_norm
    score = velocity_score * 0.35 + phase_score * 0.40 + hist_score * 0.25
    return int(max(0, min(100, round(score))))


def main():
    if os.environ.get("MACD_SMOOTH", "0") == "1":
        Stock.calculate_macd_score = _smooth_macd_score
        print(f"[patch] MACD smooth-phase ON (W={_PHASE_W})", flush=True)
    else:
        print("[patch] baseline MACD (4-branch phase)", flush=True)

    stride = int(os.environ.get("STRIDE", "3"))
    alls = [s.symbol for s in Stock.select(Stock.symbol).order_by(Stock.symbol)]
    syms = alls[::stride]
    print(f"  {len(syms)} stride-{stride} symbols", flush=True)
    sim = ScoreSimulator(symbols=syms, lookback_days=3650)
    sim.capture_inputs = True
    sim.capture_min_overall = 55
    scores = sim.simulate(since=date(2016, 1, 1))
    rows = []
    for (s, d), o in scores.items():
        if o is None or o < 55:
            continue
        inp = sim.inputs_by_key.get((s, d))
        macd = inp[0][3] if inp else None
        rows.append((s, d.isoformat() if hasattr(d, "isoformat") else str(d), int(round(o)),
                     int(macd) if macd is not None else None))
    df = pl.DataFrame({"symbol": [r[0] for r in rows], "date": [r[1] for r in rows],
                       "overall": [r[2] for r in rows], "macd": [r[3] for r in rows]})
    df.write_parquet(os.environ["DUMP_OUT"])
    print(f"\ndumped {df.height:,} rows (overall>=55) -> {os.environ['DUMP_OUT']}  | >=75: {(df['overall']>=75).sum():,}", flush=True)


if __name__ == "__main__":
    main()
