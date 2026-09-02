"""Diagnostics 1 & 3 (+ liquidity) — is the honest 85+ signal real alpha or momentum beta?

D1 (factor attribution): for every 85+ call signal (v69), the underlying stock's 15-
trading-day forward return. Compare to SPY same-window (market-adjusted excess) and to
the stock's own trailing 6m momentum. If the forward edge survives market-adjustment AND
isn't explained by how-high-momentum the names are -> real stock-selection alpha. If the
names are just high-momentum and the edge vanishes market-adjusted -> momentum beta you
could buy in MTUM for free.

D3 (2022 robustness): per-symbol P&L concentration of 2022's 74 signals. Broad (many
winners, low Herfindahl) = signal; 3 lucky names = regime luck.

Liquidity: market-cap / avg-volume profile of the 85+ names -> what option spread is
realistic (feeds D2 interpretation).

Run: PYTHONIOENCODING=utf-8 python -u experiments/v69_portfolio_retune/diag1_3_signal.py
"""
from __future__ import annotations
import os, sys, math, statistics, datetime
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from database.models.core import AlgorithmVersion, Stock  # noqa: E402
from database.models.technical import PriceHistory  # noqa: E402

HOLD = 15  # trading bars (matches 30 DTE hard-sell)
MOM = 126  # ~6m trailing momentum
db = PriceHistory._meta.database
av = AlgorithmVersion.get_active_scores_version()


def fetch(sql, *a):
    return db.execute_sql(sql, a).fetchall()


def load_ph(symbols):
    """{sym: ([dates], [closes])} sorted ascending."""
    out = {}
    syms = list(symbols)
    for i in range(0, len(syms), 200):
        chunk = syms[i:i+200]
        ph = ','.join(['%s'] * len(chunk))
        rows = fetch(f"SELECT symbol,date,close FROM price_history WHERE symbol IN ({ph}) ORDER BY symbol,date", *chunk)
        for sym, d, c in rows:
            out.setdefault(sym, ([], []))
            out[sym][0].append(d); out[sym][1].append(float(c))
    return out


def fwd_ret(arr, d, bars):
    dates, closes = arr
    import bisect
    i = bisect.bisect_left(dates, d)
    if i >= len(dates) or dates[i] != d:
        # nearest on/after
        if i >= len(dates): return None
    j = i + bars
    if j >= len(closes): return None
    return closes[j] / closes[i] - 1.0


def trail_ret(arr, d, bars):
    dates, closes = arr
    import bisect
    i = bisect.bisect_left(dates, d)
    if i >= len(dates) or i - bars < 0: return None
    return closes[i] / closes[i - bars] - 1.0


def main():
    sigs = fetch("SELECT symbol,date,overall FROM scores WHERE version_id=%s AND overall>=85 ORDER BY date", av.id)
    print(f"v{av.id} 85+ signals: {len(sigs)}  ({len(set(s[0] for s in sigs))} distinct symbols)")
    syms = set(s[0] for s in sigs)
    ph = load_ph(syms | {'SPY'})
    spy = ph.get('SPY')

    recs = []  # (sym, date, fwd15, spy_fwd15, excess, mom6m)
    for sym, d, ov in sigs:
        arr = ph.get(sym)
        if not arr: continue
        f = fwd_ret(arr, d, HOLD)
        if f is None: continue
        sf = fwd_ret(spy, d, HOLD)
        m = trail_ret(arr, d, MOM)
        recs.append((sym, d, f, sf, (f - sf) if sf is not None else None, m))

    # ---------- D1: factor attribution ----------
    fwds = [r[2] for r in recs]
    exc = [r[4] for r in recs if r[4] is not None]
    moms = [r[5] for r in recs if r[5] is not None]
    n = len(fwds)
    def tstat(xs):
        if len(xs) < 2: return 0.0
        m = statistics.mean(xs); sd = statistics.pstdev(xs)
        return m / (sd / math.sqrt(len(xs))) if sd else 0.0
    print("\n========== D1: SIGNAL FACTOR ATTRIBUTION (15-bar fwd) ==========")
    print(f"  N resolved trades: {n}")
    print(f"  mean RAW 15d fwd return:        {statistics.mean(fwds)*100:+.2f}%  (hit-rate {sum(1 for x in fwds if x>0)/n*100:.1f}%)")
    print(f"  mean MARKET-ADJ (vs SPY) excess:{statistics.mean(exc)*100:+.2f}%  t={tstat(exc):.2f}  (N={len(exc)})")
    print(f"  mean trailing-6m momentum of names: {statistics.mean(moms)*100:+.1f}%  (these ARE momentum names if >>0)")
    # does forward edge correlate with how-high momentum the name is? (is excess just momentum?)
    pairs = [(r[5], r[4]) for r in recs if r[5] is not None and r[4] is not None]
    if len(pairs) > 10:
        mx = statistics.mean([p[0] for p in pairs]); my = statistics.mean([p[1] for p in pairs])
        cov = sum((p[0]-mx)*(p[1]-my) for p in pairs)/len(pairs)
        sx = statistics.pstdev([p[0] for p in pairs]); sy = statistics.pstdev([p[1] for p in pairs])
        corr = cov/(sx*sy) if sx and sy else 0.0
        print(f"  corr(name momentum, fwd excess): {corr:+.3f}  (near 0 => edge NOT explained by momentum level)")
    # split excess by momentum tercile
    pm = sorted([r for r in recs if r[5] is not None and r[4] is not None], key=lambda r: r[5])
    if len(pm) >= 9:
        t = len(pm)//3
        lo = pm[:t]; hi = pm[-t:]
        print(f"  excess by name-momentum tercile: LOW-mom {statistics.mean([r[4] for r in lo])*100:+.2f}%  "
              f"HIGH-mom {statistics.mean([r[4] for r in hi])*100:+.2f}%")

    # ---------- D3: 2022 concentration ----------
    r22 = [r for r in recs if r[1].year == 2022]
    print("\n========== D3: 2022 ROBUSTNESS (signal vs luck) ==========")
    print(f"  2022 resolved trades: {len(r22)}  ({len(set(r[0] for r in r22))} symbols)")
    if r22:
        by_sym = defaultdict(float); cnt = defaultdict(int)
        for r in r22:
            by_sym[r[0]] += r[2]; cnt[r[0]] += 1   # sum of fwd returns as P&L proxy
        tot = sum(by_sym.values())
        ranked = sorted(by_sym.items(), key=lambda kv: kv[1], reverse=True)
        winners = sum(1 for v in by_sym.values() if v > 0)
        print(f"  mean 2022 fwd return: {statistics.mean([r[2] for r in r22])*100:+.2f}%  "
              f"market-adj: {statistics.mean([r[4] for r in r22 if r[4] is not None])*100:+.2f}%")
        print(f"  symbols net-positive: {winners}/{len(by_sym)}")
        if tot > 0:
            hhi = sum((v/tot)**2 for v in by_sym.values() if v > 0)
            top3 = sum(v for _, v in ranked[:3]) / tot * 100
            print(f"  top-3 symbols = {top3:.0f}% of positive P&L  |  Herfindahl(pos)={hhi:.3f}  "
                  f"({'CONCENTRATED/luck' if top3 > 60 else 'BROAD/signal'})")
        print(f"  top contributors: " + ", ".join(f"{s}({v*100:+.0f}%,n{cnt[s]})" for s, v in ranked[:6]))

    # ---------- Liquidity profile ----------
    print("\n========== LIQUIDITY PROFILE of 85+ names (-> realistic option spread) ==========")
    mcaps = {}
    for sym in syms:
        st = Stock.get_or_none(Stock.symbol == sym)
        if st: mcaps[sym] = (float(st.market_cap) if st.market_cap else None,
                             float(st.avg_volume) if getattr(st, 'avg_volume', None) else None)
    # weight by signal frequency
    freq = defaultdict(int)
    for s in sigs: freq[s[0]] += 1
    bins = {'mega >200B': 0, 'large 50-200B': 0, 'mid 10-50B': 0, 'small 2-10B': 0, 'micro <2B': 0, 'unknown': 0}
    for sym, f in freq.items():
        mc = mcaps.get(sym, (None, None))[0]
        if mc is None: bins['unknown'] += f
        else:
            b = mc / 1e9
            if b > 200: bins['mega >200B'] += f
            elif b > 50: bins['large 50-200B'] += f
            elif b > 10: bins['mid 10-50B'] += f
            elif b > 2: bins['small 2-10B'] += f
            else: bins['micro <2B'] += f
    tot_sig = sum(bins.values())
    for k, v in bins.items():
        print(f"  {k:<14}: {v:>4} signals ({v/tot_sig*100:.0f}%)")
    illiq = (bins['small 2-10B'] + bins['micro <2B']) / tot_sig * 100
    print(f"  => {illiq:.0f}% of 85+ signals are small/micro-cap (wide ~10-15% option spreads)")


if __name__ == '__main__':
    main()
