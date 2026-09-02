"""
What did the 2024 MARKET have? — per-year regime characterization (read-only).
SPY tape + VIX + breadth + regime multiplier, by calendar year.
"""
import sys, math, statistics as st
from pathlib import Path
from datetime import date
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from database.models.technical import PriceHistory
from database.models.core import MarketRegime, MarketBreadth

YEARS = [2018, 2020, 2021, 2022, 2023, 2024, 2025]

def f(x): return float(x) if x is not None else None

# SPY daily closes
spy = list(PriceHistory.select(PriceHistory.date, PriceHistory.close, PriceHistory.high, PriceHistory.low)
           .where(PriceHistory.symbol == 'SPY').order_by(PriceHistory.date))
spy_by_year = {}
for r in spy:
    spy_by_year.setdefault(r.date.year, []).append(r)

def spy_stats(rows):
    rows = sorted(rows, key=lambda r: r.date)
    closes = [f(r.close) for r in rows]
    if len(closes) < 5: return None
    rets = [closes[i]/closes[i-1]-1 for i in range(1, len(closes))]
    total = closes[-1]/closes[0]-1
    rv = st.pstdev(rets) * math.sqrt(252) * 100
    up = 100*sum(1 for x in rets if x>0)/len(rets)
    # max intra-year drawdown (close basis)
    peak=closes[0]; mdd=0
    for c in closes:
        peak=max(peak,c); mdd=max(mdd,(peak-c)/peak)
    # longest run of trading days with running DD never exceeding 5%
    peak=closes[0]; run=0; best=0
    for c in closes:
        peak=max(peak,c); dd=(peak-c)/peak
        if dd<=0.05: run+=1; best=max(best,run)
        else: run=0
    # autocorrelation of daily returns (momentum/persistence proxy)
    if len(rets)>2:
        m=st.mean(rets); var=st.pvariance(rets) or 1e-12
        ac1=sum((rets[i]-m)*(rets[i-1]-m) for i in range(1,len(rets)))/((len(rets)-1)*var)
    else: ac1=0
    return dict(total=total*100, rv=rv, up=up, mdd=mdd*100, calm_run=best, ac1=ac1, ndays=len(closes))

# regime + breadth per year
def agg_year(model, col, yr, transform=f):
    rows = list(model.select(getattr(model, col)).where(
        (model.date >= date(yr,1,1)) & (model.date <= date(yr,12,31))))
    vals = [transform(getattr(r, col)) for r in rows]
    vals = [v for v in vals if v is not None]
    return vals

print("\n=== 2024 IT-factor: per-year MARKET REGIME characterization ===\n")
hdr = ("year | SPYret  RVol  up%  maxDD calmRun ac1  | VIXavg VIXmax <16%  | brdAvg brd>60% pAbove50 | mccAvg | regMavg")
print(hdr); print("-"*len(hdr))
for y in YEARS:
    s = spy_stats(spy_by_year.get(y, []))
    if not s: continue
    vix = agg_year(MarketRegime, 'vix_close', y)
    vix_lt16 = 100*sum(1 for v in vix if v<16)/len(vix) if vix else float('nan')
    regm = agg_year(MarketRegime, 'regime_multiplier', y)
    brd = agg_year(MarketBreadth, 'breadth_score', y)
    brd_gt60 = 100*sum(1 for v in brd if v>60)/len(brd) if brd else float('nan')
    pab = agg_year(MarketBreadth, 'pct_above_ema50', y)
    mcc = agg_year(MarketBreadth, 'mcclellan_oscillator', y)
    print(f"{y} | {s['total']:+6.1f} {s['rv']:5.1f} {s['up']:4.0f} {s['mdd']:5.1f} {s['calm_run']:6d} {s['ac1']:+5.2f} | "
          f"{st.mean(vix) if vix else float('nan'):6.1f} {max(vix) if vix else float('nan'):6.1f} {vix_lt16:4.0f}  | "
          f"{st.mean(brd) if brd else float('nan'):6.1f} {brd_gt60:6.0f} {st.mean(pab) if pab else float('nan'):8.1f} | "
          f"{st.mean(mcc) if mcc else float('nan'):6.1f} | {st.mean(regm) if regm else float('nan'):6.3f}")

print("\nlegend: RVol=annualized realized vol%, up%=fraction up-days, maxDD=max intra-year SPY drawdown%,")
print("        calmRun=longest trading-day run with running DD<=5% (trend persistence),")
print("        ac1=lag-1 daily-return autocorr (momentum), <16%=fraction of days VIX<16,")
print("        brd>60%=fraction of days breadth_score>60, pAbove50=avg %stocks above EMA50,")
print("        mccAvg=avg McClellan oscillator, regMavg=avg regime_multiplier (>1=bullish read).")
