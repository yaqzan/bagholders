# Test of the viral claim: "Buy stocks when VIX is 30 (45+), sell when VIX is 14, repeat.
# Undefeated, ensures you outperform the S&P 500 year after year."
# Literal state-machine backtest on MarketRegime (vix_close, spy_close), closes only, no look-ahead.
import sys, math
from collections import defaultdict

from database.models.core import MarketRegime

rows = list(MarketRegime
            .select(MarketRegime.date, MarketRegime.vix_close, MarketRegime.spy_close)
            .where(MarketRegime.vix_close.is_null(False) & MarketRegime.spy_close.is_null(False))
            .order_by(MarketRegime.date)
            .tuples())
dates = [r[0] for r in rows]
vix = [float(r[1]) for r in rows]
spy = [float(r[2]) for r in rows]
n = len(rows)
print(f"rows={n}  span={dates[0]} .. {dates[-1]}")
print(f"VIX min={min(vix):.2f} max={max(vix):.2f}   SPY first={spy[0]:.2f} last={spy[-1]:.2f}")

years_span = (dates[-1] - dates[0]).days / 365.25
bh_mult = spy[-1] / spy[0]
bh_cagr = bh_mult ** (1 / years_span) - 1

def max_dd(equity):
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return mdd

def run(buy_thr, sell_thr, cash_yield=0.0):
    daily_cash = (1 + cash_yield) ** (1 / 252) - 1
    eq = 1.0
    in_mkt = False
    entry_px = entry_dt = None
    equity = []
    cycles = []
    days_in = 0
    for i in range(n):
        if in_mkt:
            days_in += 1
        else:
            eq *= (1 + daily_cash)
        if in_mkt:
            cur = eq * spy[i] / entry_px
        else:
            cur = eq
        equity.append(cur)
        if not in_mkt and vix[i] >= buy_thr:
            in_mkt, entry_px, entry_dt = True, spy[i], dates[i]
        elif in_mkt and vix[i] <= sell_thr:
            r = spy[i] / entry_px - 1
            eq *= (1 + r)
            cycles.append((entry_dt, dates[i], r, (dates[i] - entry_dt).days))
            in_mkt = False
    open_pos = None
    if in_mkt:
        r = spy[-1] / entry_px - 1
        open_pos = (entry_dt, r, (dates[-1] - entry_dt).days)
        eq *= (1 + r)
    mult = eq
    cagr = mult ** (1 / years_span) - 1
    mdd = max_dd(equity)
    tim = days_in / n

    # annual returns strategy vs SPY
    yr_eq = defaultdict(list)
    for d, v, s in zip(dates, equity, spy):
        yr_eq[d.year].append((v, s))
    ann = []
    prev_v = prev_s = None
    for y in sorted(yr_eq):
        v_end, s_end = yr_eq[y][-1]
        if prev_v is not None:
            ann.append((y, v_end / prev_v - 1, s_end / prev_s - 1))
        prev_v, prev_s = v_end, s_end
    losses = [a for a in ann if a[1] < a[2]]
    return dict(mult=mult, cagr=cagr, mdd=mdd, tim=tim, cycles=cycles,
                open_pos=open_pos, ann=ann, n_lose_years=len(losses), equity=equity)

for buy_thr in (30.0, 45.0):
    for cy, tag in ((0.0, "cash 0%"), (0.03, "cash 3%/yr")):
        res = run(buy_thr, 14.0, cy)
        print(f"\n=== BUY VIX>={buy_thr:.0f} / SELL VIX<=14  ({tag}) ===")
        print(f"  multiple x{res['mult']:.2f}  CAGR {res['cagr']*100:.2f}%  maxDD {res['mdd']*100:.1f}%  time-in-mkt {res['tim']*100:.1f}%")
        print(f"  vs BUY&HOLD: x{bh_mult:.2f}  CAGR {bh_cagr*100:.2f}%")
        print(f"  years underperforming SPY: {res['n_lose_years']} of {len(res['ann'])}")
        if cy == 0.0:
            print(f"  completed cycles: {len(res['cycles'])}  (neg: {sum(1 for c in res['cycles'] if c[2] < 0)})")
            for c in res['cycles']:
                print(f"    {c[0]} -> {c[1]}  {c[2]*100:+7.1f}%  ({c[3]}d)")
            if res['open_pos']:
                e, r, dd_ = res['open_pos']
                print(f"    OPEN since {e}: {r*100:+.1f}% ({dd_}d)")
            # worst underperformance years
            worst = sorted(res['ann'], key=lambda a: a[1]-a[2])[:6]
            print("  worst years vs SPY (strat vs spy):")
            for y, s, b in worst:
                print(f"    {y}: {s*100:+6.1f}% vs {b*100:+6.1f}%")

# buy&hold maxDD on same span for fairness
print(f"\nBUY&HOLD maxDD {max_dd(spy)*100:.1f}%  (same close series)")
