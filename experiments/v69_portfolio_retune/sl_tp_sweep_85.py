"""Phase 1 Stage-3 sweep: SL x TP at the 85+ calls-only HOLD survivor, REALISTIC cost.

85+ calls-only HOLD (SL=100%) survives at ~3% spread (+370% / 32% DD / 0 collapse). User's
hypothesis: a stop close-but-not-100% caps the crash tail (rides losers to -SL%, not to zero)
while keeping HOLD's winner-recovery -- and a different TP may beat 33%. Find the static
SL/TP optimum BEFORE layering allocation (phase 2) and a smooth regime function (phase 3).

Every candidate scored at ~3% round-trip spread with collapse in the objective. Win = highest
5y cost-adjusted MedRet subject to collapse=0; DD is the tiebreaker (house DD-primary rule).
Single SL/TP across calm+stress (SL_BASE=SL_STRESS, TP_BASE=TP_STRESS) -- regime-split is phase 3.

Coarse N=150 to find the basin; drill the winner at N=300+ next. calls-only, 85+, hard-sell d15.

Run: PYTHONIOENCODING=utf-8 N_ITER=150 python -u experiments/v69_portfolio_retune/sl_tp_sweep_85.py
"""
from __future__ import annotations
import os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate      # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize   # noqa: E402

WINDOWS = ['2022', '2024', '2025', 'dip', '22-now', '5y']

# 85+ calls-only, puts off, hard-sell d15, ~3% round-trip spread (the decision cost)
BASE = dict(
    HOLD_DAYS=15, MAX_POSITIONS=14,
    TIER_ULTRA=0.13, TIER_TOP=0.0975, TIER_MID=0.0, TIER_LOW=0.0,   # 85+ only
    PUT_TIER_TOP=0.0, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0,           # puts off
    SLIP_ENTRY=-0.015, SLIP_TP=-0.015, SLIP_SL=-0.015, SLIP_HARD=-0.015,  # ~3% round-trip
)

SL_GRID = [-0.55, -0.70, -0.85, -1.00]   # tighter -> wider; brackets user's -0.75 and the HOLD extreme
TP_GRID = [0.30, 0.40, 0.50]             # shipped 0.33 region -> let-winners-run


def main():
    n_iter = int(os.environ.get('N_ITER', '150'))
    print(f"Phase-1 SL x TP sweep | 85+ calls-only HOLD | ~3% spread | N={n_iter}\n", flush=True)
    rows = []
    for sl in SL_GRID:
        for tp in TP_GRID:
            params = dict(BASE); params['SL_BASE'] = sl; params['SL_STRESS'] = sl
            params['TP_BASE'] = tp; params['TP_STRESS'] = tp
            t0 = time.time()
            tag = f"SL{int(sl*100)}/TP{int(tp*100)}"
            print(f"running {tag} ...", flush=True)
            try:
                w = run_candidate(params, n_iter, WINDOWS, tag=tag)
            except Exception as e:
                print(f"  ERROR {tag}: {e}", flush=True); continue
            s = summarize(w)
            ct = w.get('5y', {}).get('call_tp')
            rows.append((sl, tp, s, ct))
            print(f"  -> 5y={s['fivey_med']}%/{s['fivey_dd']}%DD  22now={s['now_med']}%  "
                  f"coll={s['max_coll']}%  callTP={ct}%  ({time.time()-t0:.0f}s)", flush=True)

    print("\n========= Phase-1 SL x TP @ 85+ calls-only, ~3% spread =========")
    print(f"{'SL':>6}{'TP':>6}{'5y MedRet':>14}{'5y DD':>8}{'22now':>12}{'collapse':>10}{'callTP':>8}")
    for sl, tp, s, ct in rows:
        print(f"{int(sl*100):>5}%{int(tp*100):>5}%{str(s['fivey_med'])+'%':>14}{str(s['fivey_dd'])+'%':>8}"
              f"{str(s['now_med'])+'%':>12}{str(s['max_coll'])+'%':>10}{str(ct)+'%':>8}")

    # rank by 5y cost-adjusted MedRet among collapse==0
    survivors = [(sl, tp, s, ct) for (sl, tp, s, ct) in rows if (s['max_coll'] or 0) == 0]
    def _num(x):
        try: return float(x)
        except Exception: return float('-inf')
    survivors.sort(key=lambda r: _num(r[2]['fivey_med']), reverse=True)
    print("\n--- ranked by 5y cost-adjusted MedRet (collapse=0 only) ---")
    for sl, tp, s, ct in survivors[:6]:
        print(f"  SL {int(sl*100)}% / TP {int(tp*100)}%:  5y {s['fivey_med']}% @ {s['fivey_dd']}% DD  | 22now {s['now_med']}%")
    print("\nNext: drill the top SL/TP at N=300+, then phase 2 (allocation), then phase 3 (smooth regime fn).")


if __name__ == '__main__':
    main()
