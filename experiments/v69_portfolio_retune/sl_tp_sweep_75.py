"""Phase 1b: SL x TP at 75+ calls-only -- does a tight-but-wide stop tame 75+'s 89% DD?

75+ HOLD (SL=100%) ran 85-89% DD over full history (the user's +250%/5mo was a no-crash
window). The open question: does a wide-but-finite stop (70-85%) pull 75+'s drawdown down
toward 85+'s 32% while keeping 75+'s far richer signal count (more compounding)? If yes,
75+ could beat 85+ at a survivable DD. Same grid/cost/objective as the 85+ sweep so the
two frontiers compare head-to-head.

Every candidate at ~3% round-trip spread; win = highest 5y cost-adjusted MedRet subject to
collapse=0, DD tiebreaker. Single SL/TP across regimes (regime-split is phase 3). hard-sell d15.

Run: PYTHONIOENCODING=utf-8 N_ITER=150 python -u experiments/v69_portfolio_retune/sl_tp_sweep_75.py
"""
from __future__ import annotations
import os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate      # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize   # noqa: E402

WINDOWS = ['2020', '2022', 'dip', '22-now', '5y', '10y']   # v70 10y incl COVID-2020 collapse test

# 75+ FULL cascade (mid/low ON, overflow off), calls-only, hard-sell d15, ~3% spread
BASE = dict(
    HOLD_DAYS=15, MAX_POSITIONS=14,
    TIER_ULTRA=0.20, TIER_TOP=0.15, TIER_MID=0.10, TIER_LOW=0.10, TIER_OVERFLOW=0.0,   # 75+
    PUT_TIER_TOP=0.0, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0,
    SLIP_ENTRY=-0.015, SLIP_TP=-0.015, SLIP_SL=-0.015, SLIP_HARD=-0.015,
)

SL_GRID = [-0.70, -0.85, -1.00]   # return-optimal region (wide); tight SL doesn't tame 75+ DD (density-driven)
TP_GRID = [0.30, 0.40]            # TP30 won at 85+; 0.40 as backup


def main():
    n_iter = int(os.environ.get('N_ITER', '150'))
    print(f"Phase-1b SL x TP sweep | 75+ calls-only | ~3% spread | N={n_iter}\n", flush=True)
    rows = []
    for sl in SL_GRID:
        for tp in TP_GRID:
            params = dict(BASE); params['SL_BASE'] = sl; params['SL_STRESS'] = sl
            params['TP_BASE'] = tp; params['TP_STRESS'] = tp
            t0 = time.time()
            tag = f"75+SL{int(sl*100)}/TP{int(tp*100)}"
            print(f"running {tag} ...", flush=True)
            try:
                w = run_candidate(params, n_iter, WINDOWS, tag=tag)
            except Exception as e:
                print(f"  ERROR {tag}: {e}", flush=True); continue
            s = summarize(w)
            w10 = w.get('10y', {}); w20 = w.get('2020', {})
            ct = w.get('5y', {}).get('call_tp')
            rows.append((sl, tp, s, w10, w20, ct))
            print(f"  -> 10y={w10.get('med_ret')}%/{w10.get('worst_dd')}%DD  "
                  f"2020={w20.get('med_ret')}%/{w20.get('worst_dd')}%DD/coll{w20.get('p_collapse')}%  "
                  f"5y={s['fivey_med']}%  maxColl={s['max_coll']}%  callTP={ct}%  ({time.time()-t0:.0f}s)", flush=True)

    print("\n===== Phase-1b SL x TP @ 75+ calls-only, ~3% spread, v70 10y (incl 2020 COVID) =====")
    print(f"{'SL':>6}{'TP':>6}{'10y MedRet':>15}{'10yDD':>7}{'2020DD':>8}{'2020coll':>9}"
          f"{'5y MedRet':>15}{'maxColl':>9}{'callTP':>8}")
    for sl, tp, s, w10, w20, ct in rows:
        print(f"{int(sl*100):>5}%{int(tp*100):>5}%{str(w10.get('med_ret'))+'%':>15}{str(w10.get('worst_dd'))+'%':>7}"
              f"{str(w20.get('worst_dd'))+'%':>8}{str(w20.get('p_collapse'))+'%':>9}"
              f"{str(s['fivey_med'])+'%':>15}{str(s['max_coll'])+'%':>9}{str(ct)+'%':>8}")

    survivors = [r for r in rows if (r[2]['max_coll'] or 0) == 0]
    def _num(x):
        try: return float(x)
        except Exception: return float('-inf')
    survivors.sort(key=lambda r: _num(r[3].get('med_ret')), reverse=True)
    print("\n--- 75+ Apex pick: ranked by 10y MedRet, collapse=0 across ALL windows incl 2020 ---")
    for sl, tp, s, w10, w20, ct in survivors[:6]:
        print(f"  SL {int(sl*100)}% / TP {int(tp*100)}%:  10y {w10.get('med_ret')}% @ {w10.get('worst_dd')}% DD"
              f"  | 2020 DD {w20.get('worst_dd')}% coll {w20.get('p_collapse')}%  | 5y {s['fivey_med']}%")
    print("\nApex = max 10y MedRet at maxColl=0. 2020 DD/coll is the COVID crash-survival gate.")


if __name__ == '__main__':
    main()
