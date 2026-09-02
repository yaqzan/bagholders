"""Diagnostic 2 — does the honest 85+ edge survive realistic options bid-ask spread?

The MC assumes $0 slippage (Wealthsimple $0 commissions) but IGNORES the option
bid-ask spread you cross on entry/exit. 85+ signals often fire on illiquid small/mid
caps where ATM spreads run 5-15% of premium. Model a round-trip spread S as crossing
half on entry + half on exit (SLIP_ENTRY=SLIP_TP=SLIP_SL=SLIP_HARD = -S/2), so
NET_TP = 0.28 - S, NET_SL = -0.27 - S.

Per-trade gross EV is thin (~+1.7% premium at 52% TP / TP0.28-SL0.27), so the
hypothesis is that even a modest spread flips it negative. Run on C4_full (aggressive
85+ winner) across all 8 windows, N=200.

Writes diag2_cost_n<N>.jsonl. Run:
  PYTHONIOENCODING=utf-8 N_ITER=200 python -u experiments/v69_portfolio_retune/diag2_cost.py
"""
from __future__ import annotations
import json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize  # noqa: E402

WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']

# C4_full: 85+ only, aggressive sizing, TP0.28
BASE = dict(TIER_ULTRA=0.20, TIER_TOP=0.15, TIER_MID=0.0, TIER_LOW=0.0,
            PUT_TIER_TOP=0.0012, PUT_TIER_MID=0.001, PUT_TIER_LOW=0.0008,
            MAX_POSITIONS=8, MAX_POSITIONS_CALL=7, MAX_POSITIONS_PUT=2,
            TP_BASE=0.28)

def with_spread(S):
    half = round(-S / 2.0, 4)
    return {**BASE, 'SLIP_ENTRY': half, 'SLIP_TP': half, 'SLIP_SL': half, 'SLIP_HARD': half}

CANDS = {
    'spread_0pct':   with_spread(0.0),    # baseline (matches C4_full)
    'spread_2.5pct': with_spread(0.025),  # liquid large-cap
    'spread_5pct':   with_spread(0.05),   # mid-cap
    'spread_7.5pct': with_spread(0.075),
    'spread_10pct':  with_spread(0.10),   # illiquid small-cap
}


def main():
    n_iter = int(os.environ.get('N_ITER', '200'))
    out_path = os.path.join(os.path.dirname(__file__), f'diag2_cost_n{n_iter}.jsonl')
    rows = []
    with open(out_path, 'w', encoding='utf-8') as f:
        for name, params in CANDS.items():
            t0 = time.time()
            print(f'\n[diag2] {name} N={n_iter}  net_tp={0.28+params["SLIP_ENTRY"]+params["SLIP_TP"]:+.3f} '
                  f'net_sl={-0.27+params["SLIP_ENTRY"]+params["SLIP_SL"]:+.3f}', flush=True)
            try:
                w = run_candidate(params, n_iter, WINDOWS, tag=name)
            except Exception as e:
                print(f'  ERROR {name}: {e}', flush=True)
                continue
            s = summarize(w)
            rec = dict(name=name, params=params, summary=s, windows=w, dur_s=round(time.time() - t0, 1))
            f.write(json.dumps(rec) + '\n'); f.flush()
            rows.append(rec)
            print(f'  -> coll={s["max_coll"]}%  maxDD={s["max_dd"]}%  5y={s["fivey_med"]}%  '
                  f'22now={s["now_med"]}%  pos={s["pos_windows"]}/8', flush=True)
    print('\n========== DIAG 2: COST SENSITIVITY (C4_full 85+) ==========')
    print(f'{"round-trip spread":<18}{"5yMed":>10}{"22nowMed":>10}{"maxColl":>9}{"pos/8":>7}')
    for r in rows:
        s = r['summary']
        print(f'{r["name"]:<18}{str(s["fivey_med"]):>10}{str(s["now_med"]):>10}{s["max_coll"]:>8}%{s["pos_windows"]:>5}/8')
    print(f'\nwrote {out_path}')


if __name__ == '__main__':
    main()
