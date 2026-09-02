"""Focused SURVIVAL sweep — find the de-leverage floor that ends the 22-now/5y collapse.

Coarse sizing alone (uniform cascade scale 0.85/0.70) does NOT fix the multi-year
collapse on honest v69 (22-now stays 90%+ DD, 100% collapse). The real levers are
MaxPos (concurrency) and put-exposure cuts. This sweep goes straight to aggressive
de-leverage x MaxPos x put-cut combos on the two HARDEST windows (22-now, 5y), at
N=80 for speed. Survivors are then validated on all 8 windows.

Writes survival_n<N>.jsonl. Ranked by: 0-collapse first, then min WorstDD, then
least-negative median return.
"""
from __future__ import annotations
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402


def cfg(cs, ps, mp, mpc, mpp, **kw):
    """cs=call cascade scale, ps=put cascade scale, mp/mpc/mpp = MaxPos total/call/put."""
    d = dict(
        TIER_ULTRA=round(0.20 * cs, 4), TIER_TOP=round(0.15 * cs, 4),
        TIER_MID=round(0.10 * cs, 4), TIER_LOW=round(0.10 * cs, 4),
        PUT_TIER_TOP=round(0.12 * ps, 4), PUT_TIER_MID=round(0.10 * ps, 4),
        PUT_TIER_LOW=round(0.08 * ps, 4),
        MAX_POSITIONS=mp, MAX_POSITIONS_CALL=mpc, MAX_POSITIONS_PUT=mpp,
    )
    d.update(kw)
    return d


# Aggressive de-leverage grid. cs/ps in {0.55,0.45,0.35}, MaxPos {10/8/4, 8/6/3, 6/5/2},
# plus put-heavy cuts (ps << cs) and DD-soft-band-tight variants.
CANDS = {
    # MOST AGGRESSIVE FIRST: full-MaxPos-14 uniform cascade cuts (0.85/0.70/0.55)
    # already shown to 100%-collapse 22-now/5y. Concurrency (MaxPos) + put-exposure
    # are the real levers. Order by likeliest-survivor first; deep MaxPos cuts run
    # faster too (fewer fills). Survivors get all-8-window validation after.
    'cs35_ps20_mp6':       cfg(0.35, 0.20, 6, 5, 3),
    'cs35_ps20_mp8_ddt':   cfg(0.35, 0.20, 8, 7, 3, DD_SOFT_BAND_LO=0.20, DD_SOFT_BAND_HI=0.40, DD_SOFT_CALL_FLOOR=0.25),
    'cs45_ps20_mp8':       cfg(0.45, 0.20, 8, 7, 3),
    'cs45_ps20_mp6':       cfg(0.45, 0.20, 6, 5, 3),
    'cs55_ps20_mp8':       cfg(0.55, 0.20, 8, 7, 3),
    'cs55_ps00_mp8':       cfg(0.55, 0.01, 8, 7, 2),   # ~calls-only probe
    'cs45_ps30_mp8_ddt':   cfg(0.45, 0.30, 8, 7, 4, DD_SOFT_BAND_LO=0.25, DD_SOFT_BAND_HI=0.45, DD_SOFT_CALL_FLOOR=0.30),
    'cs55_ps30_mp10':      cfg(0.55, 0.30, 10, 8, 5),
    'cs45_ps45_mp8':       cfg(0.45, 0.45, 8, 7, 5),
    'cs45_ps45_mp6':       cfg(0.45, 0.45, 6, 5, 4),
    'cs35_ps35_mp6':       cfg(0.35, 0.35, 6, 5, 4),
    'cs55_ps55_mp8':       cfg(0.55, 0.55, 8, 7, 5),
    'cs55_ps55_mp10':      cfg(0.55, 0.55, 10, 8, 6),
}


def rank_key(r):
    w = r['windows']
    max_coll = max(w[k]['p_collapse'] for k in w)
    max_dd = max(w[k]['worst_dd'] for k in w)
    # weighted median return (less-negative better)
    med = sum(w[k]['med_ret'] for k in w) / len(w)
    return (max_coll, max_dd, -med)


def main():
    n_iter = int(os.environ.get('N_ITER', '80'))
    windows = os.environ.get('WINDOWS', '22-now,5y').split(',')
    log_path = os.path.join(os.path.dirname(__file__), f'survival_n{n_iter}.jsonl')
    print(f'[survival] N={n_iter} windows={windows} cands={len(CANDS)} -> {log_path}', flush=True)
    rows = []
    for name, params in CANDS.items():
        t0 = time.time()
        try:
            wr = run_candidate(params, n_iter=n_iter, windows=windows, tag=f'surv_{name}')
        except Exception as e:
            print(f'  [FAIL] {name}: {e}', flush=True)
            continue
        rec = dict(name=name, params=params, windows=wr, duration_s=time.time() - t0)
        rows.append(rec)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')
        s = '  '.join(f"{k}:{wr[k]['worst_dd']:.0f}/{wr[k]['p_collapse']:.0f}c/med{wr[k]['med_ret']:+.0f}"
                      for k in windows)
        print(f"  {name:<22} [{s}]  dur={rec['duration_s']:.0f}s", flush=True)

    print('\n=== SURVIVAL RANKING (0-collapse, then min DD, then best med) ===', flush=True)
    rows.sort(key=rank_key)
    for i, r in enumerate(rows):
        w = r['windows']
        mc = max(w[k]['p_collapse'] for k in w)
        md = max(w[k]['worst_dd'] for k in w)
        s = '  '.join(f"{k}:{w[k]['worst_dd']:.0f}/{w[k]['p_collapse']:.0f}c" for k in windows)
        tag = ' <= SURVIVES' if (mc == 0.0 and md <= 75.0) else (' (0-coll)' if mc == 0.0 else '')
        print(f"{i+1:>2} {r['name']:<22} maxDD={md:5.1f}% maxColl={mc:3.0f}%  [{s}]{tag}", flush=True)


if __name__ == '__main__':
    main()
