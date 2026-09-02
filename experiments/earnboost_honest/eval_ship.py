"""Validate the exact v70 ship config against the holdout-locked call ledger,
mirroring scoring.py's real boost math (log-proximity, WINDOW=7, MAX=0.55,
strength=min(1,log(1+lift)/log(1+NORM_CALL))). Reports tradable-tier numbers
+ vs OFF and vs current v34. (Outcomes fixed; boost only re-buckets.)
"""
from __future__ import annotations
import io, sys, os, math, json
from pathlib import Path
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parents[2]
import polars as pl
NORM = 14.0; MAX = 0.55


def bkt(pb):
    if pb >= 95: return '95+'
    if pb >= 90: return '90-94'
    if pb >= 85: return '85-89'
    if pb >= 80: return '80-84'
    if pb >= 75: return '75-79'
    return '70-74'


def cohort(d, W):
    if d is None or d < 1 or d > W: return None
    if d <= 1: return 'pre1'
    if d <= 3: return 'pre3'
    return 'pre7'


def smap_from(tbl):
    out = {}
    for k, (lift, n) in tbl.items():
        if k.split('|')[0] != 'low': continue
        if n < 10 or lift <= 0: continue
        out[k] = min(1.0, math.log(1 + lift) / math.log(1 + NORM))
    return out


def boosted(pb, d, smap, W):
    c = cohort(d, W)
    if c is None: return pb
    st = smap.get(f"low|{c}|{bkt(pb)}", 0.0)
    if st <= 0: return pb
    prox = math.log(W + 1 - d) / math.log(W + 1)
    return int(max(0, min(100, round(50 + (pb - 50) * (1 + MAX * prox * st)))))


def main():
    df = pl.read_parquet(ROOT / '.cache' / 'earnboost_honest' / 'call_ledger_v69_holdout.parquet')
    rows = list(zip(df['pre_boost'].to_list(), df['days_to_ern'].to_list(),
                    df['cohort'].to_list(), df['w15o'].to_list()))
    v70 = json.load(open(ROOT / 'experiments' / 'earnboost_honest' / 'lift_table_v70.json'))
    v34 = json.load(open(ROOT / 'experiments' / 'v34_calibration' / 'lift_table_v34.json'))

    def run(name, smap, W):
        post = []; admit = []; aff = []
        for pb, d, cf, wo in rows:
            bs = pb if smap is None else boosted(pb, d, smap, W)
            if bs != pb and wo is not None: aff.append(wo)
            if pb < 75 <= bs and wo is not None: admit.append(wo)
            post.append((bs, wo))
        def wr(lst):
            lst = [v for v in lst if v is not None]
            return (100.0 * sum(lst) / len(lst), len(lst)) if lst else (None, 0)
        o75 = [wo for bs, wo in post if bs >= 75 and wo is not None]
        o80 = [wo for bs, wo in post if bs >= 80 and wo is not None]
        o85 = [wo for bs, wo in post if bs >= 85 and wo is not None]
        return {'aff': wr(aff), 'admit': wr(admit),
                '75': (100.0*sum(o75)/len(o75), len(o75)),
                '80': (100.0*sum(o80)/len(o80), len(o80)),
                '85': (100.0*sum(o85)/len(o85), len(o85))}

    cfgs = [('OFF', None, 5),
            ('CURRENT v34 (W5)', smap_from(v34), 5),
            ('SHIP v70 (both-barrier, W7)', smap_from(v70), 7)]
    print("Exact ship-config validation (tradable / opt WR15, call ledger, holdout-locked):\n")
    print(f"{'config':<30} | {'aff N':>5} {'aff WRo':>7} | {'admit N':>7} {'adWRo':>6} | "
          f"{'75+ N':>6} {'75+WRo':>7} | {'80+ N':>6} {'80+WRo':>7} | {'85+ N':>6} {'85+WRo':>7}")
    print("-" * 118)
    for name, smap, W in cfgs:
        r = run(name, smap, W)
        def f(t):
            wr, n = t; return f"{n:>6} {('%5.1f%%'%wr) if wr is not None else '   -':>7}"
        aw, an = r['aff']; dw, dn = r['admit']
        print(f"{name:<30} | {an:>5} {('%5.1f%%'%aw) if aw is not None else '  -':>7} | "
              f"{dn:>7} {('%5.1f%%'%dw) if dw is not None else '  -':>6} | {f(r['75'])} | {f(r['80'])} | {f(r['85'])}")


if __name__ == '__main__':
    main()
