"""
Post-earnings call WR — does the documented edge hold for signals
fired 0-3 trading days AFTER the announcement?

The existing earnings_signal_winrate.py measures the PRE-earnings cohort
(signal 1-3 trading days BEFORE earnings, entry post-earnings). For the
LIVE strategy we care about the POST-earnings cohort (signal 0-3 trading
days AFTER earnings, entry next bar) since that's what the cascade allocator
sees in real time.

Methodology mirrors assess_scores._swing_walk:
  K=2.0σ target, M=5.0σ stop (calls), period scaling sqrt(W/30)
  σ = 60-day realized daily stdev at signal close.
  Win = entry_close × (1 + K·σ·√(W/30)) touched intraday before stop.
"""
from __future__ import annotations
import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.models.core import Score, EarningsDate, AlgorithmVersion
from database.models.technical import PriceHistory
from database.utils.trading_calendar import is_trading_day
from assess_scores import _peak_side, _realized_vol_pct, _swing_walk

CALL_THRESHOLD = 70

def back_n_trading(d, n):
    out = d
    while n > 0:
        out -= timedelta(days=1)
        if is_trading_day(out): n -= 1
    return out


def main():
    args = sys.argv[1:]
    if '5y' in args:
        start, end = date(2021, 1, 1), date(2026, 4, 24)
        label = '5y'
    elif '2024' in args:
        start, end = date(2024, 1, 1), date(2024, 12, 31); label = '2024'
    elif '2022' in args:
        start, end = date(2022, 1, 1), date(2022, 12, 31); label = '2022'
    else:
        start, end = date(2023, 4, 24), date(2026, 4, 24); label = '3y'

    ver = AlgorithmVersion.get_active_scores_version()
    print(f'Active version: {ver.git_commit[:8]} ({ver.git_message[:60]})')
    print(f'Window: {label} ({start} → {end})')

    # Build per-symbol earnings ordered map
    ed_rows = list(EarningsDate.select(EarningsDate.symbol, EarningsDate.date)
                   .order_by(EarningsDate.symbol, EarningsDate.date).tuples())
    ed_map = defaultdict(list)
    for sym, d in ed_rows:
        ed_map[sym].append(d)
    print(f'Earnings rows total: {len(ed_rows)} across {len(ed_map)} symbols')

    # Calls 70+ in window
    calls = list(Score.select(Score.symbol, Score.date, Score.overall)
                 .where(Score.version==ver, Score.date>=start, Score.date<=end,
                        Score.overall>=CALL_THRESHOLD)
                 .order_by(Score.symbol, Score.date).tuples())
    print(f'Calls 70+: {len(calls)}')

    # Tag each: is signal_date within 0..3 trading days AFTER an earnings event?
    tagged_post = []  # earnings-proximal POST: signal in [ed_date+1trd, ed_date+3trd]
    untagged    = []
    for sym, d, ov in calls:
        # Get the most recent earnings <= d
        sym_ed = ed_map.get(sym, [])
        if not sym_ed:
            untagged.append((sym, d, ov)); continue
        # Find the latest ed ≤ d
        latest = None
        for ed in sym_ed:
            if ed > d: break
            latest = ed
        if latest is None:
            untagged.append((sym, d, ov)); continue
        # Count trading days from latest earnings to signal_date
        days_back = 0
        cur = d
        while cur > latest and days_back < 5:
            cur -= timedelta(days=1)
            if is_trading_day(cur):
                days_back += 1
        if 1 <= days_back <= 3:
            tagged_post.append((sym, d, ov, days_back))
        else:
            untagged.append((sym, d, ov))

    print(f'Tagged POST-earnings (0-3 trading days after): {len(tagged_post)}')
    print(f'Untagged: {len(untagged)}')

    # Bucket break
    def bucket(ov):
        if ov >= 95: return '95+'
        if ov >= 85: return '85-94'
        if ov >= 80: return '80-84'
        if ov >= 75: return '75-79'
        return '70-74'

    print(f'\nDistribution by bucket:')
    print(f"  {'bucket':<8} {'POST-ern N':>12} {'Untagged N':>12}")
    bd_post = defaultdict(int); bd_un = defaultdict(int)
    for _, _, ov, _ in tagged_post: bd_post[bucket(ov)] += 1
    for _, _, ov in untagged:        bd_un[bucket(ov)]   += 1
    for b in ['95+','85-94','80-84','75-79','70-74']:
        print(f"  {b:<8} {bd_post[b]:>12} {bd_un[b]:>12}")

    # Now load price data and run barrier-touch on each
    sym_set = set(s for s,_,_,_ in tagged_post) | set(s for s,_,_ in untagged)
    print(f'\nLoading price history for {len(sym_set)} symbols...')
    rows = list(PriceHistory.select(PriceHistory.symbol, PriceHistory.date,
                                    PriceHistory.close, PriceHistory.high, PriceHistory.low)
               .where(PriceHistory.symbol.in_(list(sym_set)))
               .order_by(PriceHistory.symbol, PriceHistory.date).tuples())
    pcache = defaultdict(list)
    for sym, d, c, h, l in rows:
        pcache[sym].append((d, float(c), float(h), float(l)))

    def run_barrier_set(signals):
        # signals: list of (sym, d, ov, *)
        results = defaultdict(list)
        for tup in signals:
            sym, d, ov = tup[0], tup[1], tup[2]
            rows = pcache.get(sym, [])
            if not rows: continue
            di = {r[0]: i for i, r in enumerate(rows)}
            base = di.get(d)
            if base is None: continue
            dates  = [r[0] for r in rows]
            closes = [r[1] for r in rows]
            highs  = [r[2] for r in rows]
            lows   = [r[3] for r in rows]
            side = _peak_side(ov)
            vol = _realized_vol_pct(closes, base)
            sw = _swing_walk(closes, base, side, vol, highs=highs, lows=lows, dates=dates)
            if sw is None: continue
            b = bucket(ov)
            results[b].append(sw)
        return results

    rp = run_barrier_set(tagged_post)
    ru = run_barrier_set(untagged)

    def wr(swings, p):
        per = [s[p] for s in swings if p in s]
        if len(per) < 10: return None, len(per)
        wins = sum(1 for r in per if r['result'] == 'win')
        return wins/len(per)*100, len(per)

    print(f'\n{"Bucket":<8} {"POST-ern WR15/WR30 (N)":>32} {"Untagged WR15/WR30 (N)":>32}  {"ΔWR15":>8} {"ΔWR30":>8}')
    print('-'*100)
    for b in ['95+','85-94','80-84','75-79','70-74']:
        pwr15, pn15 = wr(rp.get(b, []), '15d')
        pwr30, pn30 = wr(rp.get(b, []), '30d')
        uwr15, un15 = wr(ru.get(b, []), '15d')
        uwr30, un30 = wr(ru.get(b, []), '30d')
        if pwr15 is None and pwr30 is None:
            p_str = '   -        '
        else:
            p_str = f"{pwr15:.1f}/{pwr30:.1f} (N={max(pn15,pn30)})"
        if uwr15 is None and uwr30 is None:
            u_str = '   -        '
        else:
            u_str = f"{uwr15:.1f}/{uwr30:.1f} (N={max(un15,un30)})"
        d15 = (pwr15 - uwr15) if (pwr15 is not None and uwr15 is not None) else None
        d30 = (pwr30 - uwr30) if (pwr30 is not None and uwr30 is not None) else None
        d15s = f"{d15:+.1f}" if d15 is not None else "  -"
        d30s = f"{d30:+.1f}" if d30 is not None else "  -"
        print(f"{b:<8} {p_str:>32} {u_str:>32}  {d15s:>8} {d30s:>8}")


if __name__ == '__main__':
    main()
