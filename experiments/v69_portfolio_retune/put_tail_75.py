"""Phase 3: the put tail done right — does a slot-filler/CUT put sleeve EARN its slots?

Per MASTER_FINDINGS OPEN GOALS: puts are NOT dead; the catastrophic result was un-gated
HOLD-puts at full alloc. Here puts are: small slot-filler allocation, CUT (real stop, the
mirror of calls), put-specific TP/SL, and a small put-side premium cap. The win condition is
HEDGE VALUE: does adding puts cut the CRASH-window drawdown (2020-COVID, 2022-bear) -- when
the calls are bleeding -- enough to justify the slots, while keeping the 10y return and 0 collapse?

Calls = the Apex winner (75+ SL-70/TP30, call cap CALL_CAP). Puts added on top (gross headroom).
Run: PYTHONIOENCODING=utf-8 ALGORITHM_VERSION_PIN=c70d16d22 N_ITER=150 python -u \
       experiments/v69_portfolio_retune/put_tail_75.py
"""
from __future__ import annotations
import os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate      # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize   # noqa: E402

WINDOWS = ['2020', '2022', 'dip', '22-now', '5y', '10y']

CALL_CAP = 0.50   # Apex exposure peak from down-check (50% > 65% via capital velocity)

# Calls = the 75+ Apex winner. gross headroom above call cap lets puts add on top w/o stealing call exposure.
CALLBASE = dict(
    TP_BASE=0.30, TP_STRESS=0.30, SL_BASE=-0.70, SL_STRESS=-0.70,
    HOLD_DAYS=15, MAX_POSITIONS=14,
    TIER_ULTRA=0.20, TIER_TOP=0.15, TIER_MID=0.10, TIER_LOW=0.10, TIER_OVERFLOW=0.0,
    SLIP_ENTRY=-0.015, SLIP_TP=-0.015, SLIP_SL=-0.015, SLIP_HARD=-0.015,
    GROSS_PREMIUM_CAP=CALL_CAP, CALL_PREMIUM_CAP=CALL_CAP,   # gross=call: puts fill IDLE capacity only, NO added leverage
)

VARIANTS = [
    ('puts OFF (baseline)',
     dict(PUT_TIER_TOP=0.0, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0, PUT_PREMIUM_CAP=0.0)),
    ('puts slot CUT-20 TP35',
     dict(PUT_TIER_TOP=0.05, PUT_TIER_MID=0.05, PUT_TIER_LOW=0.05,
          PUT_TP=0.35, PUT_SL=-0.20, PUT_PREMIUM_CAP=0.20, MAX_POSITIONS_PUT=4)),
    ('puts mod CUT-25 TP45',
     dict(PUT_TIER_TOP=0.08, PUT_TIER_MID=0.08, PUT_TIER_LOW=0.08,
          PUT_TP=0.45, PUT_SL=-0.25, PUT_PREMIUM_CAP=0.30, MAX_POSITIONS_PUT=6)),
]


def main():
    n_iter = int(os.environ.get('N_ITER', '150'))
    print(f"Phase-3 put tail (slot-filler/CUT) @ 75+ Apex calls | ~3% spread | v70 10y | N={n_iter}\n", flush=True)
    rows = []
    for name, pv in VARIANTS:
        params = dict(CALLBASE); params.update(pv)
        t0 = time.time()
        print(f"running {name} ...", flush=True)
        try:
            w = run_candidate(params, n_iter, WINDOWS, tag=name)
        except Exception as e:
            print(f"  ERROR {name}: {e}", flush=True); continue
        s = summarize(w)
        w10 = w.get('10y', {}); w20 = w.get('2020', {}); w22 = w.get('2022', {})
        ct = w.get('5y', {}).get('call_tp'); pt = w.get('5y', {}).get('put_tp')
        rows.append((name, s, w10, w20, w22, ct, pt))
        print(f"  -> 10y={w10.get('med_ret')}%/{w10.get('worst_dd')}%DD  "
              f"2020DD={w20.get('worst_dd')}%  2022DD={w22.get('worst_dd')}%  "
              f"maxColl={s['max_coll']}%  callTP={ct}% putTP={pt}%  ({time.time()-t0:.0f}s)", flush=True)

    print("\n===== Phase-3 put tail: does it CUT the crash drawdown? (v70 10y) =====")
    print(f"{'variant':<22}{'10y MedRet':>14}{'10yDD':>7}{'2020DD':>8}{'2022DD':>8}{'maxColl':>9}{'callTP':>8}{'putTP':>8}")
    base20 = base22 = None
    for name, s, w10, w20, w22, ct, pt in rows:
        if name.startswith('puts OFF'):
            base20 = _f(w20.get('worst_dd')); base22 = _f(w22.get('worst_dd'))
        print(f"{name:<22}{str(w10.get('med_ret'))+'%':>14}{str(w10.get('worst_dd'))+'%':>7}"
              f"{str(w20.get('worst_dd'))+'%':>8}{str(w22.get('worst_dd'))+'%':>8}"
              f"{str(s['max_coll'])+'%':>9}{str(ct)+'%':>8}{str(pt)+'%':>8}")

    print("\n--- HEDGE VERDICT (put-on crash DD vs puts-OFF baseline) ---")
    for name, s, w10, w20, w22, ct, pt in rows:
        if name.startswith('puts OFF'):
            continue
        d20 = (_f(w20.get('worst_dd')) - base20) if base20 is not None else None
        d22 = (_f(w22.get('worst_dd')) - base22) if base22 is not None else None
        verdict = 'EARNS slots' if (d20 is not None and d20 < -1 and (s['max_coll'] or 0) == 0) else 'no / costs DD'
        print(f"  {name}: 2020 DD {d20:+.1f}pp  2022 DD {d22:+.1f}pp  10y {w10.get('med_ret')}%  coll {s['max_coll']}%  -> {verdict}")
    print("\nPuts EARN their slots if they cut 2020/2022 DD materially at 0 collapse w/o gutting 10y return.")


def _f(x):
    try: return float(x)
    except Exception: return float('nan')


if __name__ == '__main__':
    main()
