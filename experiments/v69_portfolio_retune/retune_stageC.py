"""Stage C — ship-gate the 85+-only honest strategy. Sizing sweep at N=300 (then N=500).

Selectivity probe was decisive: the honest edge lives ENTIRELY in 85+ (TP 52.2% > 49.1%
BE at TP0.28/SL0.27); 80-84 (48.3%) and 75-79 drag below BE. Trading 85+ only:
  sel85 (cs0.45): 5y +86% / DD 25% / 7-8 windows positive
  sel85_up (full): 5y +219% / DD 48.5%
Both vs current params (collapse, 92% DD). 85+ only = ~274 trades/5y, so N>=300 needed
to confirm the low-N medians. This sweeps the return/DD trade-off via 85+ sizing.

Base: TP_BASE=0.28 (Stage B call-velocity winner), SL unchanged (-0.27; wider HURT),
puts ~0 (unrescuable), MID=LOW=0 (85+ only).

Writes stageC_n<N>.jsonl. Run:
  PYTHONIOENCODING=utf-8 N_ITER=300 python -u experiments/v69_portfolio_retune/retune_stageC.py
"""
from __future__ import annotations
import json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize  # noqa: E402

WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']

# 85+ only: MID (80-84) = LOW (75-79) = 0. ULTRA=95+, TOP=85-94.
def s85(ultra, top, mp=8, mpc=7, **kw):
    d = dict(TIER_ULTRA=ultra, TIER_TOP=top, TIER_MID=0.0, TIER_LOW=0.0,
             PUT_TIER_TOP=0.0012, PUT_TIER_MID=0.001, PUT_TIER_LOW=0.0008,
             MAX_POSITIONS=mp, MAX_POSITIONS_CALL=mpc, MAX_POSITIONS_PUT=2,
             TP_BASE=0.28)
    d.update(kw)
    return d

CANDS = {
    # baseline (current params) omitted: already quantified collapse @ Stage A N=100
    # (5y DD 84.1%, coll 100%). 85+ configs stand on absolute merit (0 collapse, <50% DD).
    'C1_cs45':       s85(0.09, 0.0675),           # safest  (5y +86% / DD 25% @ N=120)
    'C2_cs65':       s85(0.13, 0.0975),           # intermediate
    'C3_cs85':       s85(0.17, 0.1275),
    'C4_full':       s85(0.20, 0.15),             # aggressive (5y +219% / DD 48.5% @ N=120)
    'C5_ultra_tilt': s85(0.25, 0.15),             # push the rare 95+ tier
    'C6_full_mp6':   s85(0.20, 0.15, mp=6, mpc=6),# tighter concurrency for DD
    'C7_plus80sm':   {**s85(0.20, 0.15), 'TIER_MID': 0.04},  # small 80-84 back — does it still drag?
}


def main():
    n_iter = int(os.environ.get('N_ITER', '300'))
    out_path = os.path.join(os.path.dirname(__file__), f'stageC_n{n_iter}.jsonl')
    results = []
    with open(out_path, 'w', encoding='utf-8') as f:
        for name, params in CANDS.items():
            t0 = time.time()
            print(f'\n[stageC] {name} N={n_iter} params={params}', flush=True)
            try:
                w = run_candidate(params, n_iter, WINDOWS, tag=name)
            except Exception as e:
                print(f'  ERROR {name}: {e}', flush=True)
                continue
            s = summarize(w)
            rec = dict(name=name, params=params, summary=s, windows=w, dur_s=round(time.time() - t0, 1))
            f.write(json.dumps(rec) + '\n'); f.flush()
            results.append(rec)
            ct = w.get('5y', {}).get('call_tp'); ctr = w.get('5y', {}).get('call_trades')
            print(f'  -> coll={s["max_coll"]}%  maxDD={s["max_dd"]}%  avgMed={s["avg_med"]:+.1f}%  '
                  f'pos={s["pos_windows"]}/8  5y={s["fivey_med"]}%/{s["fivey_dd"]}%  '
                  f'22now={s["now_med"]}%  5yCTP={ct}% CTr={ctr}', flush=True)

    def key(r):
        s = r['summary']
        return (s['max_coll'], -s['avg_med'], s['max_dd'])
    results.sort(key=key)
    print('\n\n========== STAGE C RANKING (N={}) =========='.format(n_iter))
    print(f'{"name":<15}{"coll%":>7}{"maxDD":>7}{"avgMed":>9}{"pos/8":>7}{"5yMed":>10}{"5yDD":>7}{"22nowMed":>10}')
    for r in results:
        s = r['summary']
        print(f'{r["name"]:<15}{s["max_coll"]:>7}{s["max_dd"]:>7}{s["avg_med"]:>+9.1f}'
              f'{s["pos_windows"]:>5}/8{str(s["fivey_med"]):>10}{str(s["fivey_dd"]):>7}{str(s["now_med"]):>10}')
    print(f'\nwrote {out_path}')


if __name__ == '__main__':
    main()
