"""Stage A — profitability-first sizing/put-scale/concurrency screen on honest v69.

B's survival sweep established: current params COLLAPSE (coll=100% 22-now/5y); the
only positive probe was cs55_ps00_mp8 (+30% 22-now / 58% DD). This validates the
profitable basin across ALL 8 windows and explores around it.

User priority: PROFITABILITY first (baseline loses money), then survivability.
Rank: 0-collapse (hard floor) -> max avg median return -> min worst DD.

Writes stageA_n<N>.jsonl. Run:
  PYTHONIOENCODING=utf-8 N_ITER=100 python -u experiments/v69_portfolio_retune/retune_stageA.py
"""
from __future__ import annotations
import json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402

WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']


def cfg(cs, ps, mp, mpc, mpp, **kw):
    d = dict(
        TIER_ULTRA=round(0.20 * cs, 4), TIER_TOP=round(0.15 * cs, 4),
        TIER_MID=round(0.10 * cs, 4), TIER_LOW=round(0.10 * cs, 4),
        PUT_TIER_TOP=round(0.12 * ps, 4), PUT_TIER_MID=round(0.10 * ps, 4),
        PUT_TIER_LOW=round(0.08 * ps, 4),
        MAX_POSITIONS=mp, MAX_POSITIONS_CALL=mpc, MAX_POSITIONS_PUT=mpp,
    )
    d.update(kw)
    return d


# Profitable basin = downsized calls + (near-)zero puts + lower concurrency.
# Explore call-scale, put-scale, and MaxPos around B's cs55_ps00_mp8 winner.
CANDS = {
    'ref_cur':        {},                         # current params (expected collapse)
    'A_cs55_ps00':    cfg(0.55, 0.01, 8, 7, 2),   # B's winner
    'B_cs45_ps00':    cfg(0.45, 0.01, 8, 7, 2),   # lighter calls
    'C_cs65_ps00':    cfg(0.65, 0.01, 10, 9, 2),  # heavier calls, more compounding
    'D_cs55_ps10':    cfg(0.55, 0.10, 8, 6, 3),   # tiny put sleeve (hedge in bear?)
    'E_cs55_ps00_mp6':cfg(0.55, 0.01, 6, 5, 2),   # tighter concurrency
    'F_cs75_ps00':    cfg(0.75, 0.01, 10, 9, 2),  # push call sizing (calls are +EV)
    'G_cs45_ps10':    cfg(0.45, 0.10, 8, 6, 3),   # light calls + tiny puts
}


def summarize(w):
    labels = list(w.keys())
    max_coll = max(w[k]['p_collapse'] for k in labels)
    max_dd = max(w[k]['worst_dd'] for k in labels)
    avg_med = sum(w[k]['med_ret'] for k in labels) / len(labels)
    # profitability fraction: windows with positive median
    pos = sum(1 for k in labels if w[k]['med_ret'] > 0)
    # key windows
    fivey = w.get('5y', {})
    now = w.get('22-now', {})
    return dict(max_coll=max_coll, max_dd=max_dd, avg_med=avg_med, pos_windows=pos,
                fivey_med=fivey.get('med_ret'), fivey_dd=fivey.get('worst_dd'),
                now_med=now.get('med_ret'), now_dd=now.get('worst_dd'))


def main():
    n_iter = int(os.environ.get('N_ITER', '100'))
    out_path = os.path.join(os.path.dirname(__file__), f'stageA_n{n_iter}.jsonl')
    results = []
    with open(out_path, 'w', encoding='utf-8') as f:
        for name, params in CANDS.items():
            t0 = time.time()
            print(f'\n[stageA] {name} N={n_iter} params={params}', flush=True)
            try:
                w = run_candidate(params, n_iter, WINDOWS, tag=name)
            except Exception as e:
                print(f'  ERROR {name}: {e}', flush=True)
                continue
            s = summarize(w)
            dur = time.time() - t0
            rec = dict(name=name, params=params, summary=s, windows=w, dur_s=round(dur, 1))
            f.write(json.dumps(rec) + '\n'); f.flush()
            results.append(rec)
            print(f'  -> coll={s["max_coll"]}%  maxDD={s["max_dd"]}%  avgMed={s["avg_med"]:+.1f}%  '
                  f'pos={s["pos_windows"]}/8  5y={s["fivey_med"]}%/{s["fivey_dd"]}%  '
                  f'22now={s["now_med"]}%/{s["now_dd"]}%  ({dur:.0f}s)', flush=True)

    # rank: survivable first (0 collapse), then profitability, then DD
    def key(r):
        s = r['summary']
        return (s['max_coll'], -s['avg_med'], s['max_dd'])
    results.sort(key=key)
    print('\n\n========== STAGE A RANKING (survivable -> profitable -> low DD) ==========')
    print(f'{"name":<18}{"coll%":>7}{"maxDD":>7}{"avgMed":>9}{"pos/8":>7}{"5yMed":>10}{"5yDD":>7}{"22nowMed":>10}')
    for r in results:
        s = r['summary']
        print(f'{r["name"]:<18}{s["max_coll"]:>7}{s["max_dd"]:>7}{s["avg_med"]:>+9.1f}'
              f'{s["pos_windows"]:>5}/8{str(s["fivey_med"]):>10}{str(s["fivey_dd"]):>7}{str(s["now_med"]):>10}')
    print(f'\nwrote {out_path}')


if __name__ == '__main__':
    main()
