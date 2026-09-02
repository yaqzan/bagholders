"""N=300 confirm of the Apex CALL config before writing the live canonical config.

Two runs at N=300 (vs the N=150 sweeps):
  1. Apex uncapped ($25M inert ceiling) -- validates the call config reproduces the
     +15,323% / 10y / 0-collapse result at higher N (the validation).
  2. Apex + $250k ceiling -- the REAL shipping number (caps the deployable base at realistic
     options liquidity; rescales the headline DOWN, collapse/DD ~unchanged).

Config = 75+ SL-70/TP30 / 50% exposure / MaxPos 14 / HOLD-to-day-15 / calls-only / ~3% spread.
Run: PYTHONIOENCODING=utf-8 ALGORITHM_VERSION_PIN=c70d16d22 N_ITER=300 python -u \
       experiments/v69_portfolio_retune/n300_confirm.py
"""
from __future__ import annotations
import os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate      # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize   # noqa: E402

WINDOWS = ['2020', '2022', '22-now', '5y', '10y']

APEX = dict(
    TP_BASE=0.30, TP_STRESS=0.30, SL_BASE=-0.70, SL_STRESS=-0.70,
    HOLD_DAYS=15, MAX_POSITIONS=14, MAX_POSITIONS_CALL=14,
    TIER_ULTRA=0.20, TIER_TOP=0.15, TIER_MID=0.10, TIER_LOW=0.10, TIER_OVERFLOW=0.0,
    PUT_TIER_TOP=0.0, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0, PUT_PREMIUM_CAP=0.0,
    GROSS_PREMIUM_CAP=0.50, CALL_PREMIUM_CAP=0.50,
    SLIP_ENTRY=-0.015, SLIP_TP=-0.015, SLIP_SL=-0.015, SLIP_HARD=-0.015,
)

CONFIGS = [
    ('Apex uncapped (validate)', dict(APEX)),
    ('Apex + $250k (SHIP)', {**APEX, 'PRACTICAL_CAPITAL_CEILING': 250000}),
]


def main():
    n_iter = int(os.environ.get('N_ITER', '300'))
    print(f"N=300 confirm | Apex 75+ SL-70/TP30 50%-exposure HOLD-d15 calls-only ~3% spread | v70 10y\n", flush=True)
    for name, params in CONFIGS:
        t0 = time.time()
        print(f"running {name} ...", flush=True)
        try:
            w = run_candidate(params, n_iter, WINDOWS, tag=name)
        except Exception as e:
            print(f"  ERROR {name}: {e}", flush=True); continue
        s = summarize(w)
        print(f"\n=== {name} (N={n_iter}) ===")
        print(f"{'window':>8}{'MedRet':>16}{'WorstDD':>9}{'collapse':>10}")
        for win in WINDOWS:
            d = w.get(win, {})
            print(f"{win:>8}{str(d.get('med_ret'))+'%':>16}{str(d.get('worst_dd'))+'%':>9}{str(d.get('p_collapse'))+'%':>10}")
        ct = w.get('5y', {}).get('call_tp')
        print(f"  maxColl across windows = {s['max_coll']}%   5y callTP = {ct}%   ({time.time()-t0:.0f}s)\n", flush=True)

    print("PASS if: maxColl=0 on every window incl 2020-COVID; 10y/5y MedRet strongly positive;")
    print("  uncapped ~ the N=150 +15,323% (validates); $250k-capped = the realistic shipping headline.")


if __name__ == '__main__':
    main()
