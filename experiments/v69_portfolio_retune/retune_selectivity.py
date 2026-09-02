"""Selectivity probe — does dropping the marginal 75-79 call tier fix the thin-edge bleed?

Stage A showed the bleed scales with call VOLUME at a thin ~46% blended TP. But the
high-conviction tiers are far better (assess: 80+ 52%, 85+ 54.5%, 90+ 60.5% vs 75-79
~46%). Dropping 75-79 (TIER_LOW=0 -> premium<=0 -> signal skipped, no wasted slot)
should lift blended call TP solidly above BE and cut the bleed.

Tested on the B_cs45 base (calls-lean, low DD) with CURRENT barriers to ISOLATE the
selectivity effect. Sizing-up variants exploit that fewer/higher-quality trades can
carry more per-trade size.

Writes selectivity_n<N>.jsonl. Run (AFTER Stage B, to avoid CPU contention):
  PYTHONIOENCODING=utf-8 N_ITER=120 python -u experiments/v69_portfolio_retune/retune_selectivity.py
"""
from __future__ import annotations
import json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import summarize  # noqa: E402

WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']

# B_cs45 base sizing (cs=0.45). Puts ~zeroed. Stage B winner: TP_BASE=0.28 (capital
# velocity) baked into every candidate so selectivity is tested ON TOP of the winning
# barrier. (Stage B: TP0.28 took 5y -37.9% -> -1.3%; wider SL HURT, so SL stays.)
PUT0 = dict(PUT_TIER_TOP=0.0012, PUT_TIER_MID=0.001, PUT_TIER_LOW=0.0008,
            MAX_POSITIONS=8, MAX_POSITIONS_CALL=7, MAX_POSITIONS_PUT=2,
            TP_BASE=0.28)

CANDS = {
    # reference: 75+ at cs0.45
    'ref_75plus': {**PUT0, 'TIER_ULTRA': 0.09, 'TIER_TOP': 0.0675, 'TIER_MID': 0.045, 'TIER_LOW': 0.045},
    # 80+ only (drop 75-79)
    'sel80':      {**PUT0, 'TIER_ULTRA': 0.09, 'TIER_TOP': 0.0675, 'TIER_MID': 0.045, 'TIER_LOW': 0.0},
    # 85+ only (drop 75-84)
    'sel85':      {**PUT0, 'TIER_ULTRA': 0.09, 'TIER_TOP': 0.0675, 'TIER_MID': 0.0,   'TIER_LOW': 0.0},
    # 80+ only, sized UP (fewer trades -> more per-trade size)
    'sel80_up':   {**PUT0, 'TIER_ULTRA': 0.15, 'TIER_TOP': 0.11,   'TIER_MID': 0.075, 'TIER_LOW': 0.0,
                   'MAX_POSITIONS': 8, 'MAX_POSITIONS_CALL': 7, 'MAX_POSITIONS_PUT': 2},
    # 85+ only, sized UP more (rarest, highest-conviction)
    'sel85_up':   {**PUT0, 'TIER_ULTRA': 0.20, 'TIER_TOP': 0.15,   'TIER_MID': 0.0,   'TIER_LOW': 0.0,
                   'MAX_POSITIONS': 8, 'MAX_POSITIONS_CALL': 7, 'MAX_POSITIONS_PUT': 2},
    # 80+ only at full cs0.55 (more aggressive)
    'sel80_cs55': {**PUT0, 'TIER_ULTRA': 0.11, 'TIER_TOP': 0.0825, 'TIER_MID': 0.055, 'TIER_LOW': 0.0},
}


def main():
    n_iter = int(os.environ.get('N_ITER', '120'))
    out_path = os.path.join(os.path.dirname(__file__), f'selectivity_n{n_iter}.jsonl')
    results = []
    with open(out_path, 'w', encoding='utf-8') as f:
        for name, params in CANDS.items():
            t0 = time.time()
            print(f'\n[sel] {name} N={n_iter} params={params}', flush=True)
            try:
                w = run_candidate(params, n_iter, WINDOWS, tag=f'sel_{name}')
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
                  f'22now={s["now_med"]}%  5yCTP={ct}% 5yCTr={ctr}', flush=True)

    def key(r):
        s = r['summary']
        return (s['max_coll'], -s['avg_med'], s['max_dd'])
    results.sort(key=key)
    print('\n\n========== SELECTIVITY RANKING ==========')
    print(f'{"name":<14}{"coll%":>7}{"maxDD":>7}{"avgMed":>9}{"pos/8":>7}{"5yMed":>10}{"5yDD":>7}{"22nowMed":>10}{"5yCTP":>8}')
    for r in results:
        s = r['summary']; ct = r['windows'].get('5y', {}).get('call_tp')
        print(f'{r["name"]:<14}{s["max_coll"]:>7}{s["max_dd"]:>7}{s["avg_med"]:>+9.1f}'
              f'{s["pos_windows"]:>5}/8{str(s["fivey_med"]):>10}{str(s["fivey_dd"]):>7}{str(s["now_med"]):>10}{str(ct):>8}')
    print(f'\nwrote {out_path}')


if __name__ == '__main__':
    main()
