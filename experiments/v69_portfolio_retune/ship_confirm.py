"""Final N=500 ship-gate confirm of the chosen honest 85+ portfolio config.

Ship config (C2_cs65, 85+ only, TP0.28, puts OFF):
  TIER_ULTRA=0.13 TIER_TOP=0.0975 TIER_MID=0 TIER_LOW=0
  PUT_TIER_*=0 (puts fully off) ; MAX_POSITIONS=8/7/2 ; TP_BASE=0.28
Verifies: 0 collapse, DD sane, puts truly off (PTr~0), 7/8 windows positive.

Run: PYTHONIOENCODING=utf-8 N_ITER=500 python -u experiments/v69_portfolio_retune/ship_confirm.py
"""
from __future__ import annotations
import json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize  # noqa: E402

WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
SHIP = dict(TIER_ULTRA=0.13, TIER_TOP=0.0975, TIER_MID=0.0, TIER_LOW=0.0,
            PUT_TIER_TOP=0.0, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0,
            MAX_POSITIONS=8, MAX_POSITIONS_CALL=7, MAX_POSITIONS_PUT=2,
            TP_BASE=0.28)


def main():
    n_iter = int(os.environ.get('N_ITER', '500'))
    print(f'[ship_confirm] C2_cs65 85+ TP0.28 puts-off  N={n_iter}', flush=True)
    w = run_candidate(SHIP, n_iter, WINDOWS, tag='ship_confirm')
    s = summarize(w)
    print(f'\n{"window":>8}{"medRet":>10}{"worstDD":>9}{"coll":>7}{"CTP":>7}{"CTr":>8}{"PTr":>7}')
    for win in WINDOWS:
        d = w[win]
        print(f'{win:>8}{d["med_ret"]:>+9.0f}%{d["worst_dd"]:>8.1f}%{d["p_collapse"]:>6.1f}%'
              f'{d.get("call_tp",0):>6.1f}%{d.get("call_trades",0):>8.0f}{d.get("put_trades",0):>7.0f}')
    print(f'\nsummary: maxColl={s["max_coll"]}%  maxDD={s["max_dd"]}%  '
          f'5y={s["fivey_med"]}%/{s["fivey_dd"]}%  22now={s["now_med"]}%  pos={s["pos_windows"]}/8')
    max_ptr = max(w[win].get('put_trades', 0) for win in WINDOWS)
    print(f'puts-off check: max put_trades across windows = {max_ptr} (should be ~0)')
    json.dump({'params': SHIP, 'summary': s, 'windows': w},
              open(os.path.join(os.path.dirname(__file__), f'ship_confirm_n{n_iter}.json'), 'w'), indent=2)


if __name__ == '__main__':
    main()
