"""Mirror of earn_supp_put_pretest.py — but for CALLS firing within
N trading days BEFORE earnings (signal D, earnings in (D, D+N trd])."""
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

def fwd_n_trading(d, n):
    out = d
    while n > 0:
        out += timedelta(days=1)
        if is_trading_day(out): n -= 1
    return out


def main():
    args = sys.argv[1:]
    suppress_days = int(args[-1]) if args and args[-1].isdigit() else 3

    start, end = date(2021, 1, 1), date(2026, 4, 24); label = '5y'

    ver = AlgorithmVersion.get_active_scores_version()
    print(f'Active version: {ver.git_commit[:8]}; window={label}; suppress_days={suppress_days}')

    ed_rows = list(EarningsDate.select(EarningsDate.symbol, EarningsDate.date)
                   .order_by(EarningsDate.symbol, EarningsDate.date).tuples())
    ed_map = defaultdict(list)
    for sym, d in ed_rows: ed_map[sym].append(d)

    calls = list(Score.select(Score.symbol, Score.date, Score.overall)
                 .where(Score.version==ver, Score.date>=start, Score.date<=end,
                        Score.overall>=CALL_THRESHOLD)
                 .order_by(Score.symbol, Score.date).tuples())
    print(f'Calls 70+ loaded: {len(calls)}')

    earnings_window, other = [], []
    for sym, d, ov in calls:
        sym_ed = ed_map.get(sym, [])
        if not sym_ed:
            other.append((sym, d, ov)); continue
        win_end = fwd_n_trading(d, suppress_days)
        if any(d < ed <= win_end for ed in sym_ed):
            earnings_window.append((sym, d, ov))
        else:
            other.append((sym, d, ov))
    print(f'  PRE-Earnings window: {len(earnings_window)}  Other: {len(other)}')

    syms = {s for s,_,_ in earnings_window+other}
    rows = list(PriceHistory.select(PriceHistory.symbol, PriceHistory.date,
                                    PriceHistory.close, PriceHistory.high, PriceHistory.low)
                .where(PriceHistory.symbol.in_(list(syms)))
                .order_by(PriceHistory.symbol, PriceHistory.date).tuples())
    pcache = defaultdict(list)
    for sym, d, c, h, l in rows:
        pcache[sym].append((d, float(c), float(h), float(l)))

    def bucket(ov):
        if ov >= 95: return '95+'
        if ov >= 85: return '85-94'
        if ov >= 80: return '80-84'
        if ov >= 75: return '75-79'
        return '70-74'

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

    print(f'\n{"Bucket":<8} {"PRE-Earnings WR15/WR30 (N)":>32} {"Other WR15/WR30 (N)":>32}  {"ΔWR15":>8} {"ΔWR30":>8}')
    print('-'*100)
    for b in ['95+','85-94','80-84','75-79','70-74']:
        ewr15, en15 = wr(rew.get(b, []), '15d')
        ewr30, en30 = wr(rew.get(b, []), '30d')
        owr15, on15 = wr(rot.get(b, []), '15d')
        owr30, on30 = wr(rot.get(b, []), '30d')
        e_str = f"{ewr15:.1f}/{ewr30:.1f} (N={max(en15,en30)})" if (ewr15 is not None or ewr30 is not None) else "  -"
        o_str = f"{owr15:.1f}/{owr30:.1f} (N={max(on15,on30)})" if (owr15 is not None or owr30 is not None) else "  -"
        d15 = (ewr15 - owr15) if (ewr15 is not None and owr15 is not None) else None
        d30 = (ewr30 - owr30) if (ewr30 is not None and owr30 is not None) else None
        d15s = f"{d15:+.1f}" if d15 is not None else "  -"
        d30s = f"{d30:+.1f}" if d30 is not None else "  -"
        print(f"{b:<8} {e_str:>32} {o_str:>32}  {d15s:>8} {d30s:>8}")


if __name__ == '__main__':
    main()
