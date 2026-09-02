"""The decisive test: does calls-only HOLD 85+ (the surviving config) beat the spread?

Decomposition showed calls-only HOLD 85+ = +656% 5y / 30% DD / 0% collapse frictionless --
the first non-collapsing, non-fantasy-DD result. It trades sparsely (85+ only) and holds
(low turnover), so it pays the ~6% options spread FEW times. Prior "options dead at spread"
was measured on the dense CUT+puts book; this config never got tested. THIS is the make-or-break.

calls-only, SL=100% (no early stop), day-15 hard-sell, 85+, puts off, across cost levels.
  frictionless  = the +656% headline
  ~3% round-trip = the user's realistic mid-fill execution (prior break-even ~3%)
  ~6% round-trip = conservative incl. illiquid names

Run: PYTHONIOENCODING=utf-8 N_ITER=150 python -u experiments/v69_portfolio_retune/calls_only_cost.py
"""
from __future__ import annotations
import os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate      # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize   # noqa: E402

WINDOWS = ['2022', '2024', '2025', 'dip', '22-now', '5y']

BASE = dict(
    TP_BASE=0.33, SL_BASE=-1.00, HOLD_DAYS=15, MAX_POSITIONS=14,
    TIER_ULTRA=0.13, TIER_TOP=0.0975, TIER_MID=0.0, TIER_LOW=0.0,   # 85+ only
    PUT_TIER_TOP=0.0, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0,           # puts OFF
)
COSTS = [
    ('frictionless',   dict(SLIP_ENTRY=0.0,    SLIP_TP=0.0,    SLIP_SL=0.0,    SLIP_HARD=0.0)),
    ('~3% round-trip', dict(SLIP_ENTRY=-0.015, SLIP_TP=-0.015, SLIP_SL=-0.015, SLIP_HARD=-0.015)),
    ('~6% round-trip', dict(SLIP_ENTRY=-0.03,  SLIP_TP=-0.03,  SLIP_SL=-0.03,  SLIP_HARD=-0.03)),
]


def main():
    n_iter = int(os.environ.get('N_ITER', '150'))
    print(f"CALLS-ONLY HOLD 85+ (SL=100%, hard-sell d15, puts off) on v69 | N={n_iter} | vs spread\n", flush=True)
    rows = []
    for label, cost in COSTS:
        params = dict(BASE); params.update(cost)
        t0 = time.time()
        print(f"running {label} ...", flush=True)
        try:
            w = run_candidate(params, n_iter, WINDOWS, tag=label)
        except Exception as e:
            print(f"  ERROR {label}: {e}", flush=True); continue
        s = summarize(w)
        ct = w.get('5y', {}).get('call_tp')
        rows.append((label, s, ct))
        print(f"  -> 5y={s['fivey_med']}%/{s['fivey_dd']}%DD  22now={s['now_med']}%  "
              f"coll={s['max_coll']}%  5yCallTP={ct}%  ({time.time()-t0:.0f}s)", flush=True)

    print("\n========= calls-only HOLD 85+ on v69 — return vs options spread =========")
    print(f"{'cost level':<16}{'5y MedRet':>14}{'5y DD':>8}{'22now':>14}{'collapse':>10}{'callTP':>8}")
    for label, s, ct in rows:
        print(f"{label:<16}{str(s['fivey_med'])+'%':>14}{str(s['fivey_dd'])+'%':>8}"
              f"{str(s['now_med'])+'%':>14}{str(s['max_coll'])+'%':>10}{str(ct)+'%':>8}")
    print("\nIf 5y stays strongly positive at ~3% (your execution) with collapse 0 and DD ~30%:")
    print("  this is a real, survivable, leveraged-momentum sleeve. If it craters at 3-6%: frictionless-only.")


if __name__ == '__main__':
    main()
