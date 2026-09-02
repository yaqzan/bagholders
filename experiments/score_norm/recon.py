"""Recon for the per-stock score-normalization hypothesis.
1. Per-stock signal-supply distribution (v70, 5y): how uneven is 75+ / <=25 supply across stocks?
   Quantify the 'some stocks 0 signals, some many' phenomenon the user describes.
2. Per-stock overall-score distribution stats (mean/std/min/max) for a sample — shows how
   differently scaled each stock's score is (the thing a per-stock z/percentile would normalize).
Single lightweight GROUP BY aggregates (version_id,date indexed) — seconds, no forward walk.
"""
import os, sys
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, _ROOT)
from database.trader_database import DB
from experiments._holdout import CUTOFF
from collections import Counter

CUT = CUTOFF.isoformat()
START = "2021-05-15"  # ~5y to cutoff

print(f"window {START}..{CUT}  (v70)")

# per-stock supply
sql = (
    "SELECT symbol, COUNT(*) ndays, SUM(overall>=75) c75, SUM(overall>=70) c70, "
    "SUM(overall<=25) p25, AVG(overall) avg_ov, STD(overall) std_ov, "
    "MIN(overall) mn, MAX(overall) mx "
    "FROM scores WHERE version_id=70 AND date BETWEEN '%s' AND '%s' GROUP BY symbol"
    % (START, CUT)
)
rows = DB.execute_sql(sql).fetchall()
print(f"stocks: {len(rows)}")

# distribution of 75+ call supply
c75 = sorted([int(r[2] or 0) for r in rows])
c70 = sorted([int(r[3] or 0) for r in rows])
p25 = sorted([int(r[4] or 0) for r in rows])
def bucketize(vals, edges):
    c = Counter()
    for v in vals:
        for e in edges:
            if v <= e:
                c[e] += 1; break
        else:
            c['>'] += 1
    return c

print("\n=== per-stock 75+ CALL signal supply over ~5y ===")
edges = [0, 2, 5, 10, 25, 50, 100, 250]
b = bucketize(c75, edges)
for e in edges:
    print(f"  <= {e:>4} signals : {b.get(e,0):>4} stocks")
print(f"  >  250 signals : {b.get('>',0):>4} stocks")
n = len(c75)
print(f"  ZERO 75+ signals: {sum(1 for v in c75 if v==0)} stocks ({100*sum(1 for v in c75 if v==0)/n:.0f}%)")
print(f"  median 75+/stock: {c75[n//2]}  p90: {c75[9*n//10]}  max: {c75[-1]}  total: {sum(c75)}")

print("\n=== per-stock <=25 PUT signal supply over ~5y ===")
bp = bucketize(p25, edges)
print(f"  ZERO <=25 signals: {sum(1 for v in p25 if v==0)} stocks ({100*sum(1 for v in p25 if v==0)/n:.0f}%)")
print(f"  median <=25/stock: {p25[n//2]}  p90: {p25[9*n//10]}  max: {p25[-1]}  total: {sum(p25)}")

# how different are per-stock score distributions (mean/std spread)?
avgs = sorted([float(r[5]) for r in rows if r[5] is not None])
stds = sorted([float(r[6]) for r in rows if r[6] is not None])
print("\n=== per-stock overall-score distribution spread (the thing normalization would equalize) ===")
print(f"  per-stock MEAN overall: p10={avgs[len(avgs)//10]:.1f} median={avgs[len(avgs)//2]:.1f} p90={avgs[9*len(avgs)//10]:.1f}")
print(f"  per-stock STD  overall: p10={stds[len(stds)//10]:.1f} median={stds[len(stds)//2]:.1f} p90={stds[9*len(stds)//10]:.1f}")
print(f"  -> stocks with low mean+std rarely cross the absolute 75 line; high mean+std cross constantly.")

# the extreme cases
rows_s = sorted(rows, key=lambda r: int(r[2] or 0))
print("\n  5 LOWEST-supply stocks (75+ count, mean, std, max):")
for r in rows_s[:5]:
    print(f"    {r[0]:8} c75={int(r[2] or 0):3} avg={float(r[5]):.1f} std={float(r[6] or 0):.1f} max={int(r[8])}")
print("  5 HIGHEST-supply stocks:")
for r in rows_s[-5:]:
    print(f"    {r[0]:8} c75={int(r[2] or 0):4} avg={float(r[5]):.1f} std={float(r[6] or 0):.1f} max={int(r[8])}")
