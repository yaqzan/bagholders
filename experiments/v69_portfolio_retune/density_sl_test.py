"""Density x stop-loss test (user hypothesis) — FRICTIONLESS (= TFSA + mid-fills).

User's insight: cutting a loser early only pays if you can RECYCLE the cash into a fresh
signal. At sparse 85+ (~1/week) there's nothing to recycle into -> HOLD should beat CUT.
At dense 75+/puts -> CUT (recycling) should beat HOLD. Test the 2x2 + puts backfill.

Frictionless (SLIP=0 default = mid-fills, no tax) on honest v69 scores, at the user's
TP=+33%. CUT = SL -27% (early stop, recycle). HOLD = SL -90% (effectively never; ride to
TP or the day-15 hard-sell). All else current engine (dead-hold, regime, etc.).

Writes density_sl_n<N>.jsonl. Run:
  PYTHONIOENCODING=utf-8 N_ITER=150 python -u experiments/v69_portfolio_retune/density_sl_test.py
"""
from __future__ import annotations
import json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize  # noqa: E402

WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']

T85 = dict(TIER_ULTRA=0.13, TIER_TOP=0.0975, TIER_MID=0.0, TIER_LOW=0.0)
T75 = dict(TIER_ULTRA=0.20, TIER_TOP=0.15, TIER_MID=0.10, TIER_LOW=0.10)
PUTS_OFF = dict(PUT_TIER_TOP=0.0, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0)
PUTS_ON = dict(PUT_TIER_TOP=0.12, PUT_TIER_MID=0.10, PUT_TIER_LOW=0.08)
MP_SPARSE = dict(MAX_POSITIONS=8, MAX_POSITIONS_CALL=7, MAX_POSITIONS_PUT=2)
MP_DENSE = dict(MAX_POSITIONS=14, MAX_POSITIONS_CALL=12, MAX_POSITIONS_PUT=8)

def cfg(tiers, puts, mp, sl):
    d = dict(TP_BASE=0.33, SL_BASE=sl)   # SLIP defaults to 0 = frictionless
    d.update(tiers); d.update(puts); d.update(mp)
    return d

CANDS = {
    'A_85_CUT':    cfg(T85, PUTS_OFF, MP_SPARSE, -0.27),   # 85+ early-stop (retune pick)
    'B_85_HOLD':   cfg(T85, PUTS_OFF, MP_SPARSE, -0.90),   # 85+ ride it (user: should beat CUT)
    'C_75_CUT':    cfg(T75, PUTS_OFF, MP_DENSE, -0.27),    # 75+ early-stop (recycle)
    'D_75_HOLD':   cfg(T75, PUTS_OFF, MP_DENSE, -0.90),    # 75+ ride it
    'E_75p_CUT':   cfg(T75, PUTS_ON, MP_DENSE, -0.27),     # 75+ & puts, early-stop
    'F_75p_HOLD':  cfg(T75, PUTS_ON, MP_DENSE, -0.90),     # 75+ & puts, ride it
}


def main():
    n_iter = int(os.environ.get('N_ITER', '150'))
    out = os.path.join(os.path.dirname(__file__), f'density_sl_n{n_iter}.jsonl')
    rows = []
    with open(out, 'w', encoding='utf-8') as f:
        for name, params in CANDS.items():
            t0 = time.time()
            print(f'\n[density_sl] {name} N={n_iter} (frictionless, TP=33%)  {params}', flush=True)
            try:
                w = run_candidate(params, n_iter, WINDOWS, tag=name)
            except Exception as e:
                print(f'  ERROR {name}: {e}', flush=True); continue
            s = summarize(w)
            rec = dict(name=name, params=params, summary=s, windows=w, dur_s=round(time.time()-t0, 1))
            f.write(json.dumps(rec) + '\n'); f.flush(); rows.append(rec)
            ct = w.get('5y', {}).get('call_tp'); pt = w.get('5y', {}).get('put_tp')
            print(f'  -> coll={s["max_coll"]}%  maxDD={s["max_dd"]}%  pos={s["pos_windows"]}/8  '
                  f'5y={s["fivey_med"]}%/{s["fivey_dd"]}%  22now={s["now_med"]}%  5yCTP={ct}% PTP={pt}%', flush=True)
    print('\n========== DENSITY x SL (frictionless, honest v69, TP=33%) ==========')
    print(f'{"config":<14}{"5yMed":>12}{"5yDD":>7}{"22nowMed":>12}{"maxColl":>9}{"pos/8":>7}')
    for r in rows:
        s = r['summary']
        print(f'{r["name"]:<14}{str(s["fivey_med"]):>12}{str(s["fivey_dd"]):>7}{str(s["now_med"]):>12}{s["max_coll"]:>8}%{s["pos_windows"]:>5}/8')
    print('\nUser hypothesis check: B_85_HOLD > A_85_CUT (sparse->hold) ? and C_75_CUT > D_75_HOLD (dense->cut) ?')
    print(f'wrote {out}')


if __name__ == '__main__':
    main()
