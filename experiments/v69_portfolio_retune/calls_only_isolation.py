"""Decompose the 100% collapse of SL=100% calls+puts HOLD: PUTS or no-STOP?

calls+puts SL=100% HOLD collapsed 100% in MC at every cost level. Isolate the cause,
frictionless (best case) so any collapse is structural, not cost:

  B  calls-only, SL=100% HOLD, 75+   <- the EXACT deterministic +10,150% config. Does it
                                        survive 150 random orderings, or was that one lucky path?
  C  calls-only, SL=100% HOLD, 85+   <- sparser/survivable threshold, still no stop
  D  calls-only, SL=27%  CUT,  85+   <- stop restored = the reference (should be survivable)

Read:
  B/C collapse too  -> the missing STOP is fatal even without puts; HOLD-no-stop is dead.
  B/C survive (low coll, DD<~80) -> the PUTS were the killer; calls-only HOLD is a real strategy.

Run: PYTHONIOENCODING=utf-8 N_ITER=150 python -u experiments/v69_portfolio_retune/calls_only_isolation.py
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
T75 = dict(TIER_ULTRA=0.20, TIER_TOP=0.15, TIER_MID=0.10, TIER_LOW=0.10)
T85 = dict(TIER_ULTRA=0.13, TIER_TOP=0.0975, TIER_MID=0.0, TIER_LOW=0.0)   # mid/low=0 => 85+ only
COMMON = dict(TP_BASE=0.33, HOLD_DAYS=15, MAX_POSITIONS=14)                # frictionless (SLIP defaults 0)

VARIANTS = [
    ('B calls-only HOLD 75+', {**COMMON, **T75, **PUTS_OFF, 'SL_BASE': -1.00}),
    ('C calls-only HOLD 85+', {**COMMON, **T85, **PUTS_OFF, 'SL_BASE': -1.00}),
    ('D calls-only CUT  85+', {**COMMON, **T85, **PUTS_OFF, 'SL_BASE': -0.27}),
]


def main():
    n_iter = int(os.environ.get('N_ITER', '150'))
    print(f"Calls-only collapse decomposition on v69 | N={n_iter} | frictionless | TP 33%, hard-sell d15\n", flush=True)
    rows = []
    for name, params in VARIANTS:
        t0 = time.time()
        print(f"running {name} ...", flush=True)
        try:
            w = run_candidate(params, n_iter, WINDOWS, tag=name)
        except Exception as e:
            print(f"  ERROR {name}: {e}", flush=True); continue
        s = summarize(w)
        ct = w.get('5y', {}).get('call_tp')
        rows.append((name, s, ct))
        print(f"  -> 5y={s['fivey_med']}%/{s['fivey_dd']}%DD  22now={s['now_med']}%  "
              f"coll={s['max_coll']}%  5yCallTP={ct}%  ({time.time()-t0:.0f}s)", flush=True)

    print("\n========= calls-only collapse decomposition (frictionless, v69) =========")
    print(f"{'variant':<24}{'5y MedRet':>14}{'5y DD':>8}{'22now':>14}{'collapse':>10}{'callTP':>8}")
    for name, s, ct in rows:
        print(f"{name:<24}{str(s['fivey_med'])+'%':>14}{str(s['fivey_dd'])+'%':>8}"
              f"{str(s['now_med'])+'%':>14}{str(s['max_coll'])+'%':>10}{str(ct)+'%':>8}")
    print("\nIf B/C show collapse ~0 and DD < ~80%: PUTS were the killer, calls-only HOLD is real.")
    print("If B/C still collapse: the missing STOP is fatal; HOLD needs a brake regardless of puts.")


if __name__ == '__main__':
    main()
