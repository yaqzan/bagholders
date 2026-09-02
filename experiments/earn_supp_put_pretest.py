"""
Pre-test: verify the earnings-window put cohort actually underperforms
the broader put population by enough to justify suppression.

The known-issues doc reports puts 16-25 PRE-earnings (signal 1-3 trading days
BEFORE earnings) have WR_30d 62-63%, below 66.7% random. But that measurement
uses POST-earnings entry. The live-strategy entry is signal_date+1 (often
INTO earnings). The relevant question for the strategy is:

  When a put fires on date D, and earnings is in (D, D+3 trading days],
  what's the barrier-touch WR if I enter on D+1 (the standard cascade entry)?

If WR is meaningfully below the population average, suppress them.

Methodology mirrors design_d_put_flip.py: standard barrier-touch from
signal date close.
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

PUT_THRESHOLD = 25

def fwd_n_trading(d, n):
    out = d
    while n > 0:
        out += timedelta(days=1)
        if is_trading_day(out): n -= 1
    return out


def main():
    args = sys.argv[1:]
    if '5y' in args:
        start, end = date(2021, 1, 1), date(2026, 4, 24); label = '5y'
    elif '3y' in args:
        start, end = date(2023, 4, 24), date(2026, 4, 24); label = '3y'
    else:
        # Default: 5y
        start, end = date(2021, 1, 1), date(2026, 4, 24); label = '5y'

    suppress_days = int(sys.argv[-1]) if sys.argv[-1].isdigit() else 3

    ver = AlgorithmVersion.get_active_scores_version()
    print(f'Active version: {ver.git_commit[:8]}')
    print(f'Window: {label} ({start} → {end}); suppress_days={suppress_days}')

    ed_rows = list(EarningsDate.select(EarningsDate.symbol, EarningsDate.date)
                   .order_by(EarningsDate.symbol, EarningsDate.date).tuples())
    ed_map = defaultdict(list)
    for sym, d in ed_rows: ed_map[sym].append(d)

    puts = list(Score.select(Score.symbol, Score.date, Score.overall)
                .where(Score.version==ver, Score.date>=start, Score.date<=end,
                       Score.overall<=PUT_THRESHOLD)
                .order_by(Score.symbol, Score.date).tuples())
    print(f'Puts loaded: {len(puts)}')

    earnings_window = []  # signals with earnings in (D, D+suppress_days trd]
    other = []
    for sym, d, ov in puts:
        sym_ed = ed_map.get(sym, [])
        if not sym_ed:
            other.append((sym, d, ov)); continue
        win_end = fwd_n_trading(d, suppress_days)
        if any(d < ed <= win_end for ed in sym_ed):
            earnings_window.append((sym, d, ov))
        else:
            other.append((sym, d, ov))
    print(f'  Earnings-window: {len(earnings_window)}')
    print(f'  Other:           {len(other)}')

    syms = {s for s,_,_ in (earnings_window + other)}
    print(f'Loading prices for {len(syms)} symbols...')
    rows = list(PriceHistory.select(PriceHistory.symbol, PriceHistory.date,
                                    PriceHistory.close, PriceHistory.high, PriceHistory.low)
                .where(PriceHistory.symbol.in_(list(syms)))
                .order_by(PriceHistory.symbol, PriceHistory.date).tuples())
    pcache = defaultdict(list)
    for sym, d, c, h, l in rows:
        pcache[sym].append((d, float(c), float(h), float(l)))

    def bucket(ov):
        if ov <= 5: return '<=5'
        if ov <= 10: return '6-10'
        if ov <= 15: return '11-15'
        if ov <= 20: return '16-20'
        return '21-25'

    def run(sigs):
        out = defaultdict(list)
        for sym, d, ov in sigs:
            rs = pcache.get(sym, [])
            if not rs: continue
            di = {r[0]: i for i, r in enumerate(rs)}
            base = di.get(d)
            if base is None: continue
            dates  = [r[0] for r in rs]
            closes = [r[1] for r in rs]
            highs  = [r[2] for r in rs]
            lows   = [r[3] for r in rs]
            side = _peak_side(ov)
            vol = _realized_vol_pct(closes, base)
            sw = _swing_walk(closes, base, side, vol, highs=highs, lows=lows, dates=dates)
            if sw is None: continue
            out[bucket(ov)].append(sw)
        return out

    rew = run(earnings_window)
    rot = run(other)

    def wr(swings, p):
        per = [s[p] for s in swings if p in s]
        if len(per) < 8: return None, len(per)
        wins = sum(1 for r in per if r['result'] == 'win')
        return wins/len(per)*100, len(per)

    print(f'\n{"Bucket":<8} {"EarningsWindow WR15/WR30 (N)":>34} {"Other WR15/WR30 (N)":>30}  {"ΔWR15":>8} {"ΔWR30":>8}')
    print('-'*100)
    for b in ['<=5','6-10','11-15','16-20','21-25']:
        ewr15, en15 = wr(rew.get(b, []), '15d')
        ewr30, en30 = wr(rew.get(b, []), '30d')
        owr15, on15 = wr(rot.get(b, []), '15d')
        owr30, on30 = wr(rot.get(b, []), '30d')
        e_str = f"{ewr15:.1f}/{ewr30:.1f} (N={max(en15,en30)})" if (ewr15 is not None or ewr30 is not None) else "  -        "
        o_str = f"{owr15:.1f}/{owr30:.1f} (N={max(on15,on30)})" if (owr15 is not None or owr30 is not None) else "  -        "
        d15 = (ewr15 - owr15) if (ewr15 is not None and owr15 is not None) else None
        d30 = (ewr30 - owr30) if (ewr30 is not None and owr30 is not None) else None
        d15s = f"{d15:+.1f}" if d15 is not None else "  -"
        d30s = f"{d30:+.1f}" if d30 is not None else "  -"
        print(f"{b:<8} {e_str:>34} {o_str:>30}  {d15s:>8} {d30s:>8}")

    # Also: aggregate total cohorts
    a_e = []; a_o = []
    for v in rew.values(): a_e.extend(v)
    for v in rot.values(): a_o.extend(v)
    def aggwr(swings, p):
        per = [s[p] for s in swings if p in s]
        wins = sum(1 for r in per if r['result'] == 'win')
        return wins/len(per)*100 if per else 0, len(per)
    ewr15a, en15a = aggwr(a_e, '15d'); ewr30a, en30a = aggwr(a_e, '30d')
    owr15a, on15a = aggwr(a_o, '15d'); owr30a, on30a = aggwr(a_o, '30d')
    print(f"\nAGG     {f'{ewr15a:.1f}/{ewr30a:.1f} (N={max(en15a,en30a)})':>34} {f'{owr15a:.1f}/{owr30a:.1f} (N={max(on15a,on30a)})':>30}  {ewr15a-owr15a:+8.1f} {ewr30a-owr30a:+8.1f}")


if __name__ == '__main__':
    main()
