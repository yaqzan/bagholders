"""Phase 2: Apex exposure sweep — how hard can 75+ deploy before COLLAPSE (not just DD)?

75+ SL-70/TP30 = +12,848% 10y / 83% DD / 0 collapse, but throttled by the Sentinel
call-65% premium cap. Apex relaxes it. Sweep the call/gross premium cap upward; find the
MAX 10y cost-adjusted return that still holds collapse=0 across ALL windows incl 2020-COVID.
Higher cap = more deployment = more explosive AND more DD/collapse. Apex deploys at the
highest cap that stays the right side of the collapse line; above it is ruin, not aggression.

calls-only, 75+, SL-70/TP30, hard-sell d15, ~3% spread, v70 10y.
Run: PYTHONIOENCODING=utf-8 ALGORITHM_VERSION_PIN=c70d16d22 N_ITER=150 python -u \
       experiments/v69_portfolio_retune/exposure_sweep_75.py
"""
from __future__ import annotations
import os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate      # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize   # noqa: E402

WINDOWS = ['2020', '2022', 'dip', '22-now', '5y', '10y']

BASE = dict(
    TP_BASE=0.30, TP_STRESS=0.30, SL_BASE=-0.70, SL_STRESS=-0.70,   # 75+ Apex winner
    HOLD_DAYS=15, MAX_POSITIONS=14,
    TIER_ULTRA=0.20, TIER_TOP=0.15, TIER_MID=0.10, TIER_LOW=0.10, TIER_OVERFLOW=0.0,
    PUT_TIER_TOP=0.0, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0,
    SLIP_ENTRY=-0.015, SLIP_TP=-0.015, SLIP_SL=-0.015, SLIP_HARD=-0.015,
    PUT_PREMIUM_CAP=0.0,
)

# 65% beat 100%/off (over-deployment hurts); peak may be LOWER -> down-check the capital-velocity peak
CAPS = [0.40, 0.50, 0.55, 0.65]


def main():
    n_iter = int(os.environ.get('N_ITER', '150'))
    print(f"Phase-2 Apex exposure sweep | 75+ SL-70/TP30 | ~3% spread | v70 10y | N={n_iter}\n", flush=True)
    rows = []
    for cap in CAPS:
        params = dict(BASE); params['GROSS_PREMIUM_CAP'] = cap; params['CALL_PREMIUM_CAP'] = cap
        lbl = 'OFF' if cap == 0 else f'{int(cap*100)}%'
        t0 = time.time()
        print(f"running cap={lbl} ...", flush=True)
        try:
            w = run_candidate(params, n_iter, WINDOWS, tag=f'cap{lbl}')
        except Exception as e:
            print(f"  ERROR cap={lbl}: {e}", flush=True); continue
        s = summarize(w); w10 = w.get('10y', {}); w20 = w.get('2020', {})
        ct = w.get('5y', {}).get('call_tp')
        rows.append((lbl, s, w10, w20, ct))
        print(f"  -> 10y={w10.get('med_ret')}%/{w10.get('worst_dd')}%DD  "
              f"2020={w20.get('med_ret')}%/{w20.get('worst_dd')}%DD/coll{w20.get('p_collapse')}%  "
              f"5y={s['fivey_med']}%  maxColl={s['max_coll']}%  callTP={ct}%  ({time.time()-t0:.0f}s)", flush=True)

    print("\n===== Phase-2 Apex exposure (premium cap) @ 75+ SL-70/TP30, v70 10y =====")
    print(f"{'cap':>6}{'10y MedRet':>15}{'10yDD':>7}{'2020DD':>8}{'2020coll':>9}{'5y MedRet':>15}{'maxColl':>9}{'callTP':>8}")
    for lbl, s, w10, w20, ct in rows:
        print(f"{lbl:>6}{str(w10.get('med_ret'))+'%':>15}{str(w10.get('worst_dd'))+'%':>7}"
              f"{str(w20.get('worst_dd'))+'%':>8}{str(w20.get('p_collapse'))+'%':>9}"
              f"{str(s['fivey_med'])+'%':>15}{str(s['max_coll'])+'%':>9}{str(ct)+'%':>8}")

    survivors = [r for r in rows if (r[1]['max_coll'] or 0) == 0]
    def _num(x):
        try: return float(x)
        except Exception: return float('-inf')
    survivors.sort(key=lambda r: _num(r[2].get('med_ret')), reverse=True)
    print("\n--- Apex exposure pick: max 10y MedRet at maxColl=0 (the explosiveness ceiling) ---")
    for lbl, s, w10, w20, ct in survivors[:5]:
        print(f"  cap {lbl}:  10y {w10.get('med_ret')}% @ {w10.get('worst_dd')}% DD  "
              f"| 2020 coll {w20.get('p_collapse')}%  | 5y {s['fivey_med']}%")
    print("\nApex deploys at the highest cap holding collapse=0. Above it = ruin, not aggression.")


if __name__ == '__main__':
    main()
