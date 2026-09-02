"""Ceiling sensitivity: how much does PRACTICAL_CAPITAL_CEILING dampen long-term growth?

The ceiling caps the deployable base: alloc_base = min(portfolio_value, CEILING). Below it,
sizing compounds; above it, dollar positions go flat -> growth bends from exponential to ~linear.

KEY: DD% and collapse are SCALE-INVARIANT (a % of equity) -> they are ~identical at every
ceiling. The ONLY thing the ceiling moves is MedRet (the compounding headline). So the backtest
is liquidity-blind and will ALWAYS prefer a higher ceiling -- the ceiling is a real-world
liquidity + lifecycle judgment, NOT a backtest optimum. This run quantifies the *cost* of each
ceiling so the human can pick the point where the ~3% spread model stops being honest.

Config = the Apex CALL winner (75+ SL-70/TP30 / 50% exposure / MaxPos14 / HOLD-d15 / calls-only / ~3% spread).
Run: PYTHONIOENCODING=utf-8 ALGORITHM_VERSION_PIN=c70d16d22 N_ITER=200 python -u \
       experiments/v69_portfolio_retune/ceiling_curve.py
"""
from __future__ import annotations
import os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate      # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize   # noqa: E402

WINDOWS = ['2020', '5y', '10y']   # 2020 = DD-invariance sanity; 5y/10y = the growth curve

APEX = dict(
    TP_BASE=0.30, TP_STRESS=0.30, SL_BASE=-0.70, SL_STRESS=-0.70,
    HOLD_DAYS=15, MAX_POSITIONS=14, MAX_POSITIONS_CALL=14,
    TIER_ULTRA=0.20, TIER_TOP=0.15, TIER_MID=0.10, TIER_LOW=0.10, TIER_OVERFLOW=0.0,
    PUT_TIER_TOP=0.0, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0, PUT_PREMIUM_CAP=0.0,
    GROSS_PREMIUM_CAP=0.50, CALL_PREMIUM_CAP=0.50,
    SLIP_ENTRY=-0.015, SLIP_TP=-0.015, SLIP_SL=-0.015, SLIP_HARD=-0.015,
)

CEILINGS = [
    ('$250k',     250_000),
    ('$500k',     500_000),
    ('$1M',     1_000_000),
    ('$2M',     2_000_000),
    ('$5M',     5_000_000),
    ('uncapped', 1_000_000_000_000),   # 1e12 = inert for a $50k start
]


def main():
    n_iter = int(os.environ.get('N_ITER', '200'))
    print(f"Ceiling curve | Apex 75+ SL-70/TP30 50%-exp HOLD-d15 calls-only ~3% spread | v70 10y | N={n_iter}\n", flush=True)
    rows = []
    for name, ceil in CEILINGS:
        params = {**APEX, 'PRACTICAL_CAPITAL_CEILING': ceil}
        t0 = time.time()
        print(f"running ceiling={name} ...", flush=True)
        try:
            w = run_candidate(params, n_iter, WINDOWS, tag=f'ceil_{name}')
        except Exception as e:
            print(f"  ERROR {name}: {e}", flush=True); continue
        s = summarize(w)
        rows.append((name, w, s))
        w5 = w.get('5y', {}); w10 = w.get('10y', {}); w20 = w.get('2020', {})
        print(f"  -> 10y={w10.get('med_ret')}%/{w10.get('worst_dd')}%DD  5y={w5.get('med_ret')}%/{w5.get('worst_dd')}%DD  "
              f"2020DD={w20.get('worst_dd')}%  maxColl={s['max_coll']}%  ({time.time()-t0:.0f}s)", flush=True)

    print("\n===== CEILING vs GROWTH (DD% should be ~flat; MedRet rises with ceiling) =====")
    print(f"{'ceiling':>10}{'5y MedRet':>18}{'10y MedRet':>20}{'10y DD':>9}{'2020 DD':>9}{'maxColl':>9}")
    for name, w, s in rows:
        w5 = w.get('5y', {}); w10 = w.get('10y', {}); w20 = w.get('2020', {})
        print(f"{name:>10}{str(w5.get('med_ret'))+'%':>18}{str(w10.get('med_ret'))+'%':>20}"
              f"{str(w10.get('worst_dd'))+'%':>9}{str(w20.get('worst_dd'))+'%':>9}{str(s['max_coll'])+'%':>9}")
    print("\nRead: DD% flat across ceilings => the ceiling is a pure growth-vs-realism dial, not a risk knob.")
    print("Pick the highest ceiling where the ~3% blended spread still holds on the signal name-mix")
    print("(binding = the ULTRA-tier 0.20*base position on the rarer/illiquid 95+ names).")


if __name__ == '__main__':
    main()
