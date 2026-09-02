"""Stage B — TP/SL barrier lever on the honest v69 substrate (the user's explicit ask).

Two arms:
  (1) CALL-barrier sweep on the Stage-A sizing winner (calls-lean base). The honest
      call edge is thin (~46-52%); the 27% base SL may be too tight (false stops on
      a thin edge) OR too loose. Sweep SL_BASE / TP_BASE / BREADTH_THRESHOLD.
  (2) PUT-RESCUE sweep on a put-inclusive base. Honest put TP at 5y is 25-36% (below
      the 36.4% BE at TP=35/SL=-20). Can a wider PUT_TP (puts need bigger moves) or a
      different PUT_SL lift puts above BE so they earn their slot instead of being cut?
      (Doc says "never widen PUT_SL beyond -20%" — but that was on INFLATED data;
      re-test honestly, flag the result.)

Auto-picks the Stage-A base from stageA_n*.jsonl (best avg_med among 0-collapse configs).
Writes stageB_n<N>.jsonl. Run:
  PYTHONIOENCODING=utf-8 N_ITER=120 python -u experiments/v69_portfolio_retune/retune_stageB.py
"""
from __future__ import annotations
import glob, json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402
from experiments.v69_portfolio_retune.retune_stageA import cfg, summarize  # noqa: E402

WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']


def pick_base():
    """Best 0-collapse, most-profitable calls-lean base from Stage A."""
    files = sorted(glob.glob(os.path.join(os.path.dirname(__file__), 'stageA_n*.jsonl')))
    best = None
    for fn in files:
        for line in open(fn):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            s = r['summary']
            if s['max_coll'] > 0:
                continue
            if best is None or s['avg_med'] > best['summary']['avg_med']:
                best = r
    return best


def main():
    n_iter = int(os.environ.get('N_ITER', '120'))
    # Stage A: B_cs45 had best 22-now (+42.8%) + lowest 5y DD (66.6%) among survivors.
    # Sizing doesn't change per-trade TP rate, so the barrier sweep is base-independent
    # for RANKING; use the clean low-DD base for interpretable absolute numbers.
    base = cfg(0.45, 0.01, 8, 7, 2)
    base_name = 'B_cs45_ps00'
    print(f'[stageB] CALL-arm base = {base_name}  {base}', flush=True)

    # Put-inclusive base for the put-rescue arm (same call sizing, restore a put sleeve)
    put_base = dict(base)
    put_base.update(PUT_TIER_TOP=round(0.12 * 0.30, 4), PUT_TIER_MID=round(0.10 * 0.30, 4),
                    PUT_TIER_LOW=round(0.08 * 0.30, 4), MAX_POSITIONS=base.get('MAX_POSITIONS', 8),
                    MAX_POSITIONS_PUT=4)

    CANDS = {}
    # --- ARM 1: call barriers on the calls-lean base ---
    CANDS['call_base_curbar'] = dict(base)
    CANDS['call_sl-0.30'] = {**base, 'SL_BASE': -0.30}            # wider stop, fewer false stops
    CANDS['call_sl-0.33'] = {**base, 'SL_BASE': -0.33}
    CANDS['call_sl-0.24'] = {**base, 'SL_BASE': -0.24}            # tighter, faster recycle
    CANDS['call_sl-0.22'] = {**base, 'SL_BASE': -0.22}           # very tight, fastest recycle
    CANDS['call_tp0.28'] = {**base, 'TP_BASE': 0.28}             # lower TP, higher hit rate
    CANDS['call_tp0.30'] = {**base, 'TP_BASE': 0.30}             # faster fills on thin edge
    CANDS['call_tp0.30_sl-0.22'] = {**base, 'TP_BASE': 0.30, 'SL_BASE': -0.22}  # tight both
    CANDS['call_tp0.40_sl-0.33'] = {**base, 'TP_BASE': 0.40, 'SL_BASE': -0.33}  # let winners run
    CANDS['call_brth30'] = {**base, 'BREADTH_THRESHOLD': 30}     # stress band fires less
    # --- ARM 2: put rescue on a put-inclusive base ---
    CANDS['put_base_curbar'] = dict(put_base)
    CANDS['put_tp0.45'] = {**put_base, 'PUT_TP': 0.45}           # puts need bigger moves
    CANDS['put_tp0.50_sl-0.25'] = {**put_base, 'PUT_TP': 0.50, 'PUT_SL': -0.25}
    CANDS['put_tp0.40_sl-0.18'] = {**put_base, 'PUT_TP': 0.40, 'PUT_SL': -0.18}  # tighter BE

    out_path = os.path.join(os.path.dirname(__file__), f'stageB_n{n_iter}.jsonl')
    results = []
    with open(out_path, 'w', encoding='utf-8') as f:
        for name, params in CANDS.items():
            t0 = time.time()
            print(f'\n[stageB] {name} N={n_iter} params={params}', flush=True)
            try:
                w = run_candidate(params, n_iter, WINDOWS, tag=name)
            except Exception as e:
                print(f'  ERROR {name}: {e}', flush=True)
                continue
            s = summarize(w)
            rec = dict(name=name, params=params, summary=s, windows=w, dur_s=round(time.time() - t0, 1))
            f.write(json.dumps(rec) + '\n'); f.flush()
            results.append(rec)
            ct = w.get('5y', {}).get('call_tp'); pt = w.get('5y', {}).get('put_tp')
            print(f'  -> coll={s["max_coll"]}%  maxDD={s["max_dd"]}%  avgMed={s["avg_med"]:+.1f}%  '
                  f'pos={s["pos_windows"]}/8  5y={s["fivey_med"]}%/{s["fivey_dd"]}%  '
                  f'5yCTP={ct}% 5yPTP={pt}%', flush=True)

    def key(r):
        s = r['summary']
        return (s['max_coll'], -s['avg_med'], s['max_dd'])
    results.sort(key=key)
    print('\n\n========== STAGE B RANKING ==========')
    print(f'{"name":<22}{"coll%":>7}{"maxDD":>7}{"avgMed":>9}{"pos/8":>7}{"5yMed":>10}{"5yDD":>7}')
    for r in results:
        s = r['summary']
        print(f'{r["name"]:<22}{s["max_coll"]:>7}{s["max_dd"]:>7}{s["avg_med"]:>+9.1f}'
              f'{s["pos_windows"]:>5}/8{str(s["fivey_med"]):>10}{str(s["fivey_dd"]):>7}')
    print(f'\nwrote {out_path}')


if __name__ == '__main__':
    main()
