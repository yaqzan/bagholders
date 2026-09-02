"""Diag 4 — does the HONEST (v69) weekly still carry incremental conditional edge?

The user's hypothesis: the weekly holds edge "when it swings a certain way / tail end of
a multi-week cycle" — so maybe the look-ahead tail can be sampled out and the edge derived.

Test: for v69 honest signals (75+/85+), pull the honest blended weekly adjustment (w_adj
from weight_info — v69's point-in-time value, NO look-ahead) and split forward outcomes by
it. If strong-confirming-weekly cohorts have materially higher honest 15d forward WR than
weak-weekly, the honest weekly has INCREMENTAL edge worth extracting better. If flat, the
weekly is already fully baked into the score and there's nothing left to derive.

Also reports the look-ahead decomposition: the recalc's "extra" weekly info = the first
~3 days of the forward window (Wed-Fri of the signal week), which for a fast-resolving
option ≈ seeing early trade resolution. That is WHY it was worth ~12pp and why it can't be
recovered without predicting those days (= the price-prediction problem, ~51% ceiling).

Run: PYTHONIOENCODING=utf-8 python -u experiments/v69_portfolio_retune/diag4_weekly_conditional.py
"""
from __future__ import annotations
import os, sys, json, math, statistics, bisect
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from database.models.core import AlgorithmVersion, Score  # noqa: E402
from database.models.technical import PriceHistory  # noqa: E402

HOLD = 15
db = Score._meta.database
av = AlgorithmVersion.get_active_scores_version()


def load_ph(symbols):
    out = {}
    syms = list(symbols)
    for i in range(0, len(syms), 200):
        chunk = syms[i:i+200]
        ph = ','.join(['%s'] * len(chunk))
        for sym, d, c in db.execute_sql(
            f"SELECT symbol,date,close FROM price_history WHERE symbol IN ({ph}) ORDER BY symbol,date", tuple(chunk)):
            out.setdefault(sym, ([], []))
            out[sym][0].append(d); out[sym][1].append(float(c))
    return out


def fwd(arr, d, bars):
    dates, closes = arr
    i = bisect.bisect_left(dates, d)
    if i >= len(dates) or dates[i] != d: return None
    j = i + bars
    if j >= len(closes): return None
    return closes[j] / closes[i] - 1.0


def tstat(xs):
    if len(xs) < 2: return 0.0
    m = statistics.mean(xs); sd = statistics.pstdev(xs)
    return m / (sd / math.sqrt(len(xs))) if sd else 0.0


def probe(thresh):
    rows = db.execute_sql(
        "SELECT symbol,date,weight_info FROM scores "
        "WHERE version_id=%s AND overall>=%s AND date>='2021-01-01' ORDER BY date", (av.id, thresh)).fetchall()
    syms = set(r[0] for r in rows)
    ph = load_ph(syms | {'SPY'}); spy = ph.get('SPY')
    recs = []
    for sym, d, wij in rows:
        arr = ph.get(sym)
        if not arr: continue
        f = fwd(arr, d, HOLD); sf = fwd(spy, d, HOLD)
        if f is None or sf is None: continue
        wadj = None
        if wij:
            try:
                wi = json.loads(wij)
                wadj = wi.get('w_adj', wi.get('wadj'))
            except Exception:
                pass
        recs.append((sym, d, f - sf, wadj))  # market-adj excess + honest weekly
    exc_all = [r[2] for r in recs]
    print(f"\n===== {thresh}+ honest signals: N={len(recs)} =====")
    print(f"  overall market-adj 15d excess: {statistics.mean(exc_all)*100:+.2f}%  t={tstat(exc_all):.2f}")
    wr = [r for r in recs if r[3] is not None]
    if len(wr) < 30:
        print("  (insufficient w_adj coverage)"); return
    wr.sort(key=lambda r: r[3])
    t = len(wr) // 3
    lo, mid, hi = wr[:t], wr[t:2*t], wr[2*t:]
    def stat(g, name):
        ex = [r[2] for r in g]
        wadjs = [r[3] for r in g]
        print(f"  {name:<22} N={len(g):>4}  w_adj~[{min(wadjs):+.0f},{max(wadjs):+.0f}]  "
              f"mean-adj-excess={statistics.mean(ex)*100:+.2f}%  t={tstat(ex):.2f}  "
              f"hit={sum(1 for x in ex if x>0)/len(ex)*100:.0f}%")
    stat(lo, "weak/bearish weekly")
    stat(mid, "neutral weekly")
    stat(hi, "strong-confirming weekly")
    # correlation w_adj vs excess (does honest weekly swing predict forward?)
    xs = [r[3] for r in wr]; ys = [r[2] for r in wr]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((a-mx)*(b-my) for a, b in zip(xs, ys)) / len(xs)
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    corr = cov/(sx*sy) if sx and sy else 0.0
    print(f"  corr(honest w_adj, fwd excess) = {corr:+.3f}  "
          f"({'weekly swing PREDICTS forward (edge to extract)' if abs(corr)>0.1 else 'NO incremental honest weekly edge — already baked into score'})")


def main():
    print(f"active honest version: v{av.id}")
    print("Testing: does the HONEST weekly (no look-ahead) swing predict forward outcome,")
    print("incrementally, within high-score cohorts? (positive corr => edge left to extract)")
    for th in (75, 85):
        probe(th)


if __name__ == '__main__':
    main()
