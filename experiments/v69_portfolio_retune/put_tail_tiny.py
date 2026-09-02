"""EXPANDED put-fill research (user standing goal): <15 puts ONLY, as a TINY remainder sliver.

The broader put-tail (put_tail_75.py: all <25 puts at 5-8% alloc, put_max 4-6) COLLAPSED 100% —
puts consumed the cash buffer that is the survival mechanism in weak tape. The user's refined
hypothesis: shrink the sleeve hard — ONLY the deepest <15 puts (put_top only; mid/low OFF), a
TINY put_cap (5-10%) and put_max 1-2, filling ONLY idle capacity within the existing 50% gross
(NO added leverage). Question: does a sliver that barely touches the buffer HEDGE the crash
windows (2020-COVID, 2022-bear, dip) without collapsing or gutting the 10y return?

Win condition: cuts 2020/2022/dip worst-DD materially at 0 collapse, 10y return ~preserved.
Kill condition: still collapses (puts truly dead even as a sliver) OR no DD help.

Calls = the shipped Apex (uncapped, 75+, HOLD SL-70/TP30, ~3% spread). Puts added in idle slots.
Run: PYTHONIOENCODING=utf-8 ALGORITHM_VERSION_PIN=c70d16d22 N_ITER=200 python -u \
       experiments/v69_portfolio_retune/put_tail_tiny.py
"""
from __future__ import annotations
import os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate      # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize   # noqa: E402

WINDOWS = ['2020', '2022', 'dip', '5y', '10y']   # crash windows = where puts hedge if anywhere

# Apex calls (uncapped, 75+, HOLD). gross=call=0.50 => puts fill ONLY idle capacity, no added leverage.
CALLBASE = dict(
    TP_BASE=0.30, TP_STRESS=0.30, SL_BASE=-0.70, SL_STRESS=-0.70,
    HOLD_DAYS=15, MAX_POSITIONS=14, MAX_POSITIONS_CALL=14,
    TIER_ULTRA=0.20, TIER_TOP=0.15, TIER_MID=0.10, TIER_LOW=0.10, TIER_OVERFLOW=0.0,
    SLIP_ENTRY=-0.015, SLIP_TP=-0.015, SLIP_SL=-0.015, SLIP_HARD=-0.015,
    GROSS_PREMIUM_CAP=0.50, CALL_PREMIUM_CAP=0.50,
    PRACTICAL_CAPITAL_CEILING=0,   # uncapped Apex base
)

# <15 ONLY = put_top set, put_mid=put_low=0. Tiny cap + 1-2 slots so puts barely touch the buffer.
VARIANTS = [
    ('puts OFF (Apex base)',
     dict(PUT_TIER_TOP=0.0, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0, PUT_PREMIUM_CAP=0.0, MAX_POSITIONS_PUT=0)),
    ('<15 sliver cap5 max1 CUT',
     dict(PUT_TIER_TOP=0.05, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0,
          PUT_TP=0.35, PUT_SL=-0.20, PUT_PREMIUM_CAP=0.05, MAX_POSITIONS_PUT=1)),
    ('<15 small cap10 max2 CUT',
     dict(PUT_TIER_TOP=0.08, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0,
          PUT_TP=0.35, PUT_SL=-0.20, PUT_PREMIUM_CAP=0.10, MAX_POSITIONS_PUT=2)),
    ('<15 sliver cap5 max1 HOLD',
     dict(PUT_TIER_TOP=0.05, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0,
          PUT_TP=0.35, PUT_SL=-0.70, PUT_PREMIUM_CAP=0.05, MAX_POSITIONS_PUT=1)),
]


def main():
    n_iter = int(os.environ.get('N_ITER', '200'))
    print(f"EXPANDED put tail: <15 ONLY, tiny sliver in idle capacity | Apex uncapped | v70 10y | N={n_iter}\n", flush=True)
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
        w10 = w.get('10y', {}); w20 = w.get('2020', {}); w22 = w.get('2022', {}); wd = w.get('dip', {})
        ct = w.get('5y', {}).get('call_tp'); pt = w.get('5y', {}).get('put_tp')
        rows.append((name, s, w10, w20, w22, wd, ct, pt))
        print(f"  -> 10y={w10.get('med_ret')}%/{w10.get('worst_dd')}%DD  2020DD={w20.get('worst_dd')}%  "
              f"2022DD={w22.get('worst_dd')}%  dipDD={wd.get('worst_dd')}%  maxColl={s['max_coll']}%  "
              f"callTP={ct}% putTP={pt}%  ({time.time()-t0:.0f}s)", flush=True)

    print("\n===== <15 sliver hedge test (does a TINY deep-put sleeve cut crash DD w/o collapse?) =====")
    print(f"{'variant':<28}{'10y MedRet':>13}{'10yDD':>7}{'2020DD':>8}{'2022DD':>8}{'dipDD':>7}{'maxColl':>9}{'putTP':>7}")
    b = {}
    for name, s, w10, w20, w22, wd, ct, pt in rows:
        if name.startswith('puts OFF'):
            b = {'20': _f(w20.get('worst_dd')), '22': _f(w22.get('worst_dd')), 'dip': _f(wd.get('worst_dd')), '10y': _f(w10.get('med_ret'))}
        print(f"{name:<28}{str(w10.get('med_ret'))+'%':>13}{str(w10.get('worst_dd'))+'%':>7}"
              f"{str(w20.get('worst_dd'))+'%':>8}{str(w22.get('worst_dd'))+'%':>8}{str(wd.get('worst_dd'))+'%':>7}"
              f"{str(s['max_coll'])+'%':>9}{str(pt)+'%':>7}")
    print("\n--- HEDGE VERDICT vs Apex baseline (need crash-DD down, 0 collapse, 10y not gutted) ---")
    for name, s, w10, w20, w22, wd, ct, pt in rows:
        if name.startswith('puts OFF') or not b:
            continue
        d20 = _f(w20.get('worst_dd')) - b['20']; d22 = _f(w22.get('worst_dd')) - b['22']; dd = _f(wd.get('worst_dd')) - b['dip']
        coll = s['max_coll'] or 0
        helps = (d20 < -1 or d22 < -1 or dd < -1) and coll == 0
        verdict = 'EARNS slots' if helps else ('COLLAPSE' if coll > 0 else 'no DD help')
        print(f"  {name}: 2020 {d20:+.1f}pp  2022 {d22:+.1f}pp  dip {dd:+.1f}pp  coll {coll}%  -> {verdict}")
    print("\nIf even a cap5/max1 sliver collapses or fails to cut crash DD: puts are dead even as tail-fill (close the goal).")


def _f(x):
    try: return float(x)
    except Exception: return float('nan')


if __name__ == '__main__':
    main()
