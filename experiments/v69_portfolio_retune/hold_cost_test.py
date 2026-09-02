"""Does the SL=100% (HOLD) calls+puts config survive realistic options spread?

User is running v69 with SL=100% on BOTH sides and seeing "great results". SL=100% =
the option must lose 100% before stopping = it ~never stops = HOLD (ride to TP / day-15
hard-sell). HOLD beats CUT frictionless -- already known. The OPEN question: HOLD trades
LESS often than CUT (longer holds, fewer round-trips over the decade), so it may pay the
~6% options spread fewer times and SURVIVE the cost hurdle the CUT/shipped config FAILED.
The prior "options dead at 6% spread" verdict was measured on CUT, never on HOLD.

This runs the HOLD calls+puts config at three cost levels and shows where it lands.
  frictionless = the user's headline (unrealistic)
  ~3% round-trip = optimistic mid-fills on liquid names (prior break-even was ~3%)
  ~6% round-trip = realistic incl. illiquid names (prior CUT verdict: 5y -45%)

Config assumed: 75+ full cascade + puts on, MaxPos 14, TP 33%/35%, SL 100% both sides.
(adjust BASE if your live setup differs)

Run: PYTHONIOENCODING=utf-8 N_ITER=150 python -u experiments/v69_portfolio_retune/hold_cost_test.py
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
    TP_BASE=0.33, SL_BASE=-1.00,            # calls: TP 33%, SL 100% => never stops
    PUT_TP=0.35, PUT_SL=-1.00,              # puts:  TP 35%, SL 100% => never stops
    HOLD_DAYS=30,                           # HOLD TO EXPIRY (no day-15 hard-sell) -- "hold til it dies"
    TIER_ULTRA=0.20, TIER_TOP=0.15, TIER_MID=0.10, TIER_LOW=0.10,   # 75+ full cascade
    PUT_TIER_TOP=0.12, PUT_TIER_MID=0.10, PUT_TIER_LOW=0.08,        # puts ON
    MAX_POSITIONS=14,
)

COSTS = [
    ('frictionless',      dict(SLIP_ENTRY=0.0,   SLIP_TP=0.0,   SLIP_SL=0.0,   SLIP_HARD=0.0)),
    ('~3% round-trip',    dict(SLIP_ENTRY=-0.015, SLIP_TP=-0.015, SLIP_SL=-0.015, SLIP_HARD=-0.015)),
    ('~6% round-trip',    dict(SLIP_ENTRY=-0.03,  SLIP_TP=-0.03,  SLIP_SL=-0.03,  SLIP_HARD=-0.03)),
]


def main():
    n_iter = int(os.environ.get('N_ITER', '150'))
    print(f"HOLD-TO-EXPIRY (SL=100%, NO day-15 hard-sell) calls+puts on v69 | N={n_iter} | 75+ cascade, MaxPos 14, TP 33/35\n", flush=True)
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
        ct = w.get('5y', {}).get('call_tp'); pt = w.get('5y', {}).get('put_tp')
        rows.append((label, s, ct, pt))
        print(f"  -> 5y={s['fivey_med']}%/{s['fivey_dd']}%DD  22now={s['now_med']}%  "
              f"coll={s['max_coll']}%  5yCTP={ct}% PTP={pt}%  ({time.time()-t0:.0f}s)", flush=True)

    print("\n========= HOLD calls+puts on v69 — return vs realistic options spread =========")
    print(f"{'cost level':<16}{'5y MedRet':>16}{'5y DD':>8}{'22now MedRet':>16}{'maxColl':>9}{'callTP':>8}{'putTP':>8}")
    for label, s, ct, pt in rows:
        print(f"{label:<16}{str(s['fivey_med'])+'%':>16}{str(s['fivey_dd'])+'%':>8}"
              f"{str(s['now_med'])+'%':>16}{str(s['max_coll'])+'%':>9}{str(ct)+'%':>8}{str(pt)+'%':>8}")
    print("\nVerdict: if 5y stays strongly positive at ~6% round-trip with collapse low, HOLD genuinely")
    print("  survives the spread the CUT config died to (real finding). If 5y craters/ collapses at")
    print("  3-6%, the 'great results' were the frictionless headline again. DD is the other gate.")


if __name__ == '__main__':
    main()
