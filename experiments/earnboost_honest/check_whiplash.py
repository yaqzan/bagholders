"""Whiplash check: per-day EARN_BOOST curve for a sample pre_boost score, under
current production (v34 table, W=5, log-proximity) vs the honest both-barrier
table (W=7). Shows day-to-day boosted-score deltas as a stock approaches earnings
— a large day-over-day jump = the COHR/VICR whiplash class (open HIGH priority).
"""
from __future__ import annotations
import io, sys, os, math, json
from pathlib import Path
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parents[2]
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


def strength(tbl, c, b):
    v = tbl.get(f"low|{c}|{b}")
    if not v: return 0.0
    lift, n = v
    if n < 10 or lift <= 0: return 0.0
    return min(1.0, math.log(1 + lift) / math.log(1 + NORM))


def boosted(pb, d, tbl, W, prox_shape):
    c = cohort(d, W)
    if c is None: return pb
    st = strength(tbl, c, bkt(pb))
    if st <= 0: return pb
    if prox_shape == 'log':
        prox = math.log(W + 1 - d) / math.log(W + 1)
    else:
        prox = 1.0
    return int(max(0, min(100, round(50 + (pb - 50) * (1 + MAX * prox * st)))))


def main():
    v34 = json.load(open(ROOT / 'experiments' / 'v34_calibration' / 'lift_table_v34.json'))
    honest = json.load(open(ROOT / 'experiments' / 'earnboost_honest' / 'lift_table_v69_calls_opt.json'))
    # both-barrier gate already implies opt>0; the opt table's lifts are the opt magnitudes.
    # But honest_calls_opt has cells even where generic<0; for both-barrier we'd intersect.
    gen = json.load(open(ROOT / 'experiments' / 'earnboost_honest' / 'lift_table_v69_calls_generic.json'))
    both = {k: honest[k] for k in honest if k in gen and honest[k][0] > 0 and gen[k][0] > 0}

    configs = [
        ('v34 production (W5, log-prox)', v34, 5, 'log'),
        ('honest both-barrier (W7, log-prox)', both, 7, 'log'),
        ('honest both-barrier (W7, FLAT-prox)', both, 7, 'flat'),
    ]
    for pb in (82, 78, 72):
        print(f"\n=== pre_boost = {pb} ({bkt(pb)}) — boosted score by days-to-earnings ===")
        print(f"{'config':<40} | " + " | ".join(f"d{d}" for d in range(8, 0, -1)) + " | max|Δday|")
        for name, tbl, W, ps in configs:
            curve = {d: boosted(pb, d, tbl, W, ps) for d in range(8, 0, -1)}
            # day-over-day deltas as stock moves 8->1 (approaching earnings)
            seq = [curve[d] for d in range(8, 0, -1)]
            deltas = [abs(seq[i + 1] - seq[i]) for i in range(len(seq) - 1)]
            # also the exit cliff (d1 boosted -> d0 reaction day, boost gone -> back to pb)
            maxd = max(deltas + [abs(pb - seq[-1])]) if seq else 0
            cells = " | ".join(f"{curve[d]:>2}" for d in range(8, 0, -1))
            print(f"{name:<40} | {cells} | {maxd:>2}")
    print("\nmax|Δday| = largest day-over-day boosted-score jump as the stock approaches earnings")
    print("(includes the d1->reaction-day drop-off). Larger = more whiplash.")


if __name__ == '__main__':
    main()
