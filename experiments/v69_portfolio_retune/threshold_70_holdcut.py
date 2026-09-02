"""70+ (abundant) calls-only: does CUT's recycle thesis finally beat HOLD here?

CUT's only justification is capital velocity -- stop a loser early, redeploy the cash into
a FRESH signal. At sparse 85+ there was nothing to recycle into, so HOLD won. At ABUNDANT
70+ (overflow tier enabled) there's a fresh signal almost every day -- so tight CUT may
finally earn its keep. Tighter SL = faster cut = higher recycle rate (more call_trades).

Tension #1: 70-74 is lower-quality (TP drops) and the book is denser (more concurrent
leverage -> the thing that collapsed the puts book). HOLD (no stop) may over-lever and
COLLAPSE at 70+. CUT's stop caps that.
Tension #2: CUT trades more -> pays the spread more times. So CUT's velocity edge (clean at
frictionless) may erode at ~3%. Both columns shown.

calls-only, 70+ full cascade incl overflow=0.10, MaxPos 14, TP 33%, hard-sell d15.
SL in {HOLD(-1.00), CUT-27, CUT-20, CUT-15} x cost in {frictionless, ~3%}.

Run: PYTHONIOENCODING=utf-8 N_ITER=150 python -u experiments/v69_portfolio_retune/threshold_70_holdcut.py
"""
from __future__ import annotations
import os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate      # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize   # noqa: E402

WINDOWS = ['2022', '2024', '2025', 'dip', '22-now', '5y']
PUTS_OFF = dict(PUT_TIER_TOP=0.0, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0)
T70 = dict(TIER_ULTRA=0.20, TIER_TOP=0.15, TIER_MID=0.10, TIER_LOW=0.10, TIER_OVERFLOW=0.10)
COMMON = dict(TP_BASE=0.33, HOLD_DAYS=15, MAX_POSITIONS=14)

SL_LEVELS = [('HOLD(-100%)', -1.00), ('CUT -27%', -0.27), ('CUT -20%', -0.20), ('CUT -15%', -0.15)]
COSTS = [
    ('frictionless', dict(SLIP_ENTRY=0.0,   SLIP_TP=0.0,   SLIP_SL=0.0,   SLIP_HARD=0.0)),
    ('~3% rt',       dict(SLIP_ENTRY=-0.015, SLIP_TP=-0.015, SLIP_SL=-0.015, SLIP_HARD=-0.015)),
]


def main():
    n_iter = int(os.environ.get('N_ITER', '150'))
    print(f"70+ calls-only (overflow ON) HOLD vs CUT samplings on v69 | N={n_iter} | recycle thesis test\n", flush=True)
    rows = []
    for cost_label, cost in COSTS:
        for sl_label, sl in SL_LEVELS:
            params = dict(COMMON); params.update(T70); params.update(PUTS_OFF)
            params.update(cost); params['SL_BASE'] = sl
            t0 = time.time()
            print(f"running {cost_label:<13} {sl_label} ...", flush=True)
            try:
                w = run_candidate(params, n_iter, WINDOWS, tag=f"{cost_label}/{sl_label}")
            except Exception as e:
                print(f"  ERROR: {e}", flush=True); continue
            s = summarize(w)
            ct = w.get('5y', {}).get('call_tp'); ctr = w.get('5y', {}).get('call_trades')
            rows.append((cost_label, sl_label, s, ct, ctr))
            print(f"  -> 5y={s['fivey_med']}%/{s['fivey_dd']}%DD  22now={s['now_med']}%  "
                  f"coll={s['max_coll']}%  callTP={ct}%  callTrades={ctr}  ({time.time()-t0:.0f}s)", flush=True)

    print("\n========= 70+ calls-only: HOLD vs CUT (recycle rate = callTrades) =========")
    print(f"{'cost':<14}{'SL':<13}{'5y MedRet':>14}{'5y DD':>8}{'22now':>12}{'coll':>7}{'callTP':>8}{'callTrades':>12}")
    for cost_label, sl_label, s, ct, ctr in rows:
        print(f"{cost_label:<14}{sl_label:<13}{str(s['fivey_med'])+'%':>14}{str(s['fivey_dd'])+'%':>8}"
              f"{str(s['now_med'])+'%':>12}{str(s['max_coll'])+'%':>7}{str(ct)+'%':>8}{str(ctr):>12}")
    print("\nRecycle thesis holds IF at ~3% a tight CUT (more callTrades) beats HOLD on 5y compound")
    print("  without collapsing. If HOLD still wins (or only HOLD avoids collapse), recycling is a mirage")
    print("  even at abundance -- the realized losses from cutting outweigh the velocity.")


if __name__ == '__main__':
    main()
