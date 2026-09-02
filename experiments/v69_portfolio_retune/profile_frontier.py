"""Apex / Core / Sentinel frontier on the v70 HOLD substrate (puts-off, calls-only).

All three profiles share the SAME validated HOLD core (TP30 / SL-70 / SLIP-0.015 / HOLD-d15 /
MaxPos14 / calls-only / ~3% spread). They differ ONLY on the two dials the risk-budget ethos
allows: EXPOSURE (gross/call premium cap) and SELECTIVITY (threshold via zeroing low tiers).
NOT on cranking allocation (the v70 down-check proved over-deployment deepens DD).

  Apex     = 75+ full cascade (20/15/10/10), 50% exposure, UNCAPPED      -> explosive (validated)
  Core     = 75+ full cascade,               40% exposure, $2M ceiling   -> balanced
  Sentinel = 85+-only (mid/low=0),           30% exposure, $1M ceiling   -> preservation

This produces the return-vs-DD frontier at collapse=0. Each profile picks its point.
Run: PYTHONIOENCODING=utf-8 ALGORITHM_VERSION_PIN=c70d16d22 N_ITER=250 python -u \
       experiments/v69_portfolio_retune/profile_frontier.py
"""
from __future__ import annotations
import os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate      # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize   # noqa: E402

WINDOWS = ['2020', '2022', '5y', '10y']

# Shared HOLD core (the validated Apex call engine). Puts OFF on every profile.
CORE = dict(
    TP_BASE=0.30, TP_STRESS=0.30, SL_BASE=-0.70, SL_STRESS=-0.70,
    HOLD_DAYS=15, MAX_POSITIONS=14, MAX_POSITIONS_CALL=14,
    TIER_OVERFLOW=0.0,
    PUT_TIER_TOP=0.0, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0, PUT_PREMIUM_CAP=0.0,
    SLIP_ENTRY=-0.015, SLIP_TP=-0.015, SLIP_SL=-0.015, SLIP_HARD=-0.015,
)

# (label, exposure cap, ceiling, tier_ultra/top/mid/low) — selectivity via zeroing mid/low.
# ceiling=0 => UNCAPPED (Apex max-compounding). Core/Sentinel apply realistic base caps.
PROFILES = [
    ('Apex      75+ exp50 uncapped', 0.50,         0,  (0.20, 0.15, 0.10, 0.10)),
    ('Core      75+ exp40 $2M',      0.40, 2_000_000,  (0.20, 0.15, 0.10, 0.10)),
    ('Core-45   75+ exp45 $2M',      0.45, 2_000_000,  (0.20, 0.15, 0.10, 0.10)),
    ('Sentinel  85+ exp30 $1M',      0.30, 1_000_000,  (0.20, 0.15, 0.00, 0.00)),
    ('Sent-lite 85+ exp30 $1M',      0.30, 1_000_000,  (0.13, 0.0975, 0.00, 0.00)),
]


def main():
    n_iter = int(os.environ.get('N_ITER', '250'))
    print(f"Profile frontier | HOLD core (SL-70/TP30/~3% spread) | puts-off | v70 10y | N={n_iter}\n", flush=True)
    rows = []
    for label, exp, ceil, (tu, tt, tm, tl) in PROFILES:
        params = dict(CORE)
        params.update(GROSS_PREMIUM_CAP=exp, CALL_PREMIUM_CAP=exp,
                      PRACTICAL_CAPITAL_CEILING=ceil,
                      TIER_ULTRA=tu, TIER_TOP=tt, TIER_MID=tm, TIER_LOW=tl)
        t0 = time.time()
        print(f"running {label} ...", flush=True)
        try:
            w = run_candidate(params, n_iter, WINDOWS, tag=label)
        except Exception as e:
            print(f"  ERROR {label}: {e}", flush=True); continue
        s = summarize(w)
        rows.append((label, w, s))
        w10 = w.get('10y', {}); w5 = w.get('5y', {}); w20 = w.get('2020', {}); w22 = w.get('2022', {})
        print(f"  -> 10y={w10.get('med_ret')}%/{w10.get('worst_dd')}%DD  5y={w5.get('med_ret')}%/{w5.get('worst_dd')}%DD  "
              f"2020DD={w20.get('worst_dd')}%  2022DD={w22.get('worst_dd')}%  maxColl={s['max_coll']}%  ({time.time()-t0:.0f}s)", flush=True)

    print("\n===== APEX / CORE / SENTINEL FRONTIER (collapse must be 0 on all) =====")
    print(f"{'profile':<26}{'10y MedRet':>14}{'10y DD':>8}{'5y MedRet':>14}{'5y DD':>7}{'2020DD':>8}{'2022DD':>8}{'maxColl':>9}")
    for label, w, s in rows:
        w10 = w.get('10y', {}); w5 = w.get('5y', {}); w20 = w.get('2020', {}); w22 = w.get('2022', {})
        print(f"{label:<26}{str(w10.get('med_ret'))+'%':>14}{str(w10.get('worst_dd'))+'%':>8}"
              f"{str(w5.get('med_ret'))+'%':>14}{str(w5.get('worst_dd'))+'%':>7}"
              f"{str(w20.get('worst_dd'))+'%':>8}{str(w22.get('worst_dd'))+'%':>8}{str(s['max_coll'])+'%':>9}")
    print("\nPick: Apex = max survivable return; Core = moderate DD; Sentinel = lowest DD. All collapse=0.")


if __name__ == '__main__':
    main()
