"""Quick screen: test candidate put-alloc scaling functions against the observed
U-curve in put performance vs sector breadth.

Goal: validate that a continuous gradient (especially logarithmic-tail) captures
more alpha than the constant 1.0 baseline, before committing to MC-Bayesian sweep.

Method:
  - For each candidate scale function f(sec_brd):
      weighted_pnl_per_trade = pnl_pct × f(brd)
      aggregate = sum(weighted_pnl) / sum(scale)  # weighted average
  - Compare aggregate weighted pnl% across candidates
  - Higher = curve captures more alpha

Note: this is a per-trade screen, not a portfolio simulation. It tells us which
shape better aligns with the empirical U-curve. The MC-Bayesian sweep would
then validate that the per-trade alpha compounds via cascade slot displacement.
"""
from __future__ import annotations
import io, sys, pickle, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
LOCAL_CACHE = ROOT / '.cache' / 'sector_etf_screen'

# Candidate U-curve shapes (all return scale ∈ approximately [0.5, 1.5])
def baseline(brd):
    return 1.0

def vcurve_linear(brd, midpoint=75, half_width=25, floor=0.5, ceil=1.5):
    """V-shape: scale = floor + |brd - midpoint| / half_width × (ceil - floor)"""
    d = abs(brd - midpoint) / half_width
    return min(ceil, max(floor, floor + d * (ceil - floor)))

def ucurve_quadratic(brd, midpoint=75, half_width=25, floor=0.5, ceil=1.5):
    """Parabolic U: scale = floor + ((brd - midpoint)/half_width)^2 × (ceil - floor)"""
    d2 = ((brd - midpoint) / half_width) ** 2
    return min(ceil, max(floor, floor + d2 * (ceil - floor)))

def ucurve_log(brd, midpoint=75, half_width=25, floor=0.5, ceil=1.5):
    """Logarithmic-tail U: alpha accelerates at tails.
    scale = floor + log(1 + |d|×k) / log(1 + k) × (ceil - floor)
    where d = |brd - midpoint| / half_width clipped to [0,1].
    With k=10, the function curves up sharply near the extremes."""
    d = min(1.0, abs(brd - midpoint) / half_width)
    k = 10.0
    log_norm = math.log(1 + d * k) / math.log(1 + k)
    return min(ceil, max(floor, floor + log_norm * (ceil - floor)))

def ucurve_sigmoid_tails(brd, lo_thresh=20, hi_thresh=90, k=5, mid_floor=0.5, tail_ceil=1.5):
    """Sigmoid activations at each tail.
    scale = mid_floor + (tail_ceil - mid_floor) * (sigmoid_lo + sigmoid_hi)
    where sigmoid_lo = sigmoid((lo_thresh - brd) / k), peaks at low brd
          sigmoid_hi = sigmoid((brd - hi_thresh) / k), peaks at high brd
    Use clipped sum so single-tail saturation doesn't double-count."""
    sig_lo = 1 / (1 + math.exp(-(lo_thresh - brd) / k))
    sig_hi = 1 / (1 + math.exp(-(brd - hi_thresh) / k))
    activation = min(1.0, sig_lo + sig_hi)
    return mid_floor + activation * (tail_ceil - mid_floor)

def rule_b_exact(brd):
    """User's Rule B exact threshold version: 0.5 in 60-85, 1.1 at <20 or >95, else 1.0"""
    if 60 <= brd <= 85:
        return 0.5
    if brd < 20 or brd > 95:
        return 1.1
    return 1.0


CURVES = [
    ('baseline (uniform 1.0)', baseline),
    ('V-curve linear (mid=75)', lambda b: vcurve_linear(b)),
    ('U-curve quadratic',       lambda b: ucurve_quadratic(b)),
    ('U-curve LOG (k=10)',      lambda b: ucurve_log(b)),
    ('U-curve sigmoid-tails',   lambda b: ucurve_sigmoid_tails(b)),
    ('Rule B exact thresholds', rule_b_exact),
]


def main():
    print('=' * 76)
    print('Put U-curve allocation screen — alpha capture by candidate shape')
    print('=' * 76)

    with open(LOCAL_CACHE / 'v44_backtest_result.pkl', 'rb') as f:
        result = pickle.load(f)
    trade_log = result['trade_log']

    sec_brd = pl.read_csv(LOCAL_CACHE / 'sector_breadth_daily_2020plus.csv')

    rows = []
    for t in trade_log:
        ed = t.get('entry_date')
        ed_iso = ed.isoformat() if hasattr(ed, 'isoformat') else str(ed)
        side_raw = t.get('side', 'call')
        side = 'CALL' if side_raw in ('call', 'low') else 'PUT'
        rows.append({
            'date': ed_iso,
            'side': side,
            'tp': 1 if (t.get('outcome') or '').upper() == 'TP' else 0,
            'pnl_pct': float(t.get('pnl_pct') or 0),
        })
    df = pl.DataFrame(rows).join(
        sec_brd.with_columns(pl.col('date').cast(pl.Utf8)), on='date', how='left'
    ).filter(pl.col('sec_brd_ema50').is_not_null())

    puts = df.filter(pl.col('side') == 'PUT')
    print(f'\n[puts] N={len(puts):,}, mean pnl%={puts["pnl_pct"].mean()*100:+.3f}%, '
          f'TP={puts["tp"].mean()*100:.2f}%')

    # Print scale value at key breadth points for each curve (sanity check shapes)
    print('\n  Curve shape values at breadth checkpoints:')
    print(f'  {"curve":<32s}', end='')
    for b in [5, 15, 25, 50, 75, 85, 95, 100]:
        print(f'{"brd="+str(b):>8s}', end=' ')
    print()
    for name, fn in CURVES:
        print(f'  {name:<32s}', end='')
        for b in [5, 15, 25, 50, 75, 85, 95, 100]:
            v = fn(b)
            print(f'{v:>8.3f}', end=' ')
        print()

    # Apply each curve to puts and compute weighted aggregate
    print('\n  Per-curve aggregate metrics (puts only):')
    print(f'  {"curve":<32s} {"sum(scale)":>12s} {"sum(scale×pnl%)":>16s} {"weighted_pnl%":>14s} '
          f'{"Δ vs baseline":>14s}')
    print(f'  {"-"*32} {"-"*12} {"-"*16} {"-"*14} {"-"*14}')

    baseline_weighted = None
    for name, fn in CURVES:
        scales = np.array([fn(b) for b in puts['sec_brd_ema50'].to_numpy()])
        pnls = puts['pnl_pct'].to_numpy()
        sum_s = scales.sum()
        sum_sp = (scales * pnls).sum()
        weighted = sum_sp / sum_s if sum_s > 0 else 0
        if baseline_weighted is None:
            baseline_weighted = weighted
            delta_str = ''
        else:
            delta = (weighted - baseline_weighted) * 100
            delta_str = f'{delta:>+10.3f}pp'
        print(f'  {name:<32s} {sum_s:>12.0f} {sum_sp*100:>15.1f}%  {weighted*100:>+13.3f}%  {delta_str:>14s}')

    # Check by breadth band: what scale does each curve apply, and what's the band's pnl%
    print('\n  Per-band put performance and scale assignment:')
    print(f'  {"band":<10s} {"N":>5s} {"raw_pnl%":>9s} {"baseline":>9s} {"V-lin":>7s} '
          f'{"U-quad":>7s} {"U-LOG":>7s} {"U-sig":>7s} {"RuleB":>7s}')
    for label, lo, hi in [('<10', 0, 10), ('10-15', 10, 15), ('15-20', 15, 20),
                          ('20-30', 20, 30), ('30-50', 30, 50), ('50-70', 50, 70),
                          ('70-85', 70, 85), ('85-95', 85, 95), ('>95', 95, 999)]:
        sub = puts.filter((pl.col('sec_brd_ema50') > lo) & (pl.col('sec_brd_ema50') <= hi))
        if len(sub) == 0: continue
        mean_pnl = sub['pnl_pct'].mean() * 100
        # Scale at band midpoint
        midbrd = (lo + hi) / 2 if hi < 999 else 97.5
        scales_str = ''
        for name, fn in CURVES:
            scales_str += f'{fn(midbrd):>7.2f} '
        print(f'  {label:<10s} {len(sub):>5d} {mean_pnl:>+8.2f}% {scales_str.rstrip()}')

    # Show "lift" — how much more alpha we capture per band by overweighting it
    print('\n  Per-band weighted contribution under U-curve LOG vs baseline:')
    print(f'  (positive = curve is correctly putting more weight on alpha-positive bands)')
    print(f'  {"band":<10s} {"N":>5s} {"raw_pnl%":>9s} {"scale":>6s} {"lift_per_trade":>14s}')

    log_curve = lambda b: ucurve_log(b)
    base_curve = baseline
    for label, lo, hi in [('<10', 0, 10), ('10-15', 10, 15), ('15-20', 15, 20),
                          ('20-30', 20, 30), ('30-50', 30, 50), ('50-70', 50, 70),
                          ('70-85', 70, 85), ('85-95', 85, 95), ('>95', 95, 999)]:
        sub = puts.filter((pl.col('sec_brd_ema50') > lo) & (pl.col('sec_brd_ema50') <= hi))
        if len(sub) == 0: continue
        mean_pnl = sub['pnl_pct'].mean()
        midbrd = (lo + hi) / 2 if hi < 999 else 97.5
        log_scale = log_curve(midbrd)
        # Lift per trade = mean_pnl × (log_scale - 1)  → how much more (or less) we capture
        lift = mean_pnl * (log_scale - 1) * 100
        sign = '+' if lift > 0 else ''
        print(f'  {label:<10s} {len(sub):>5d} {mean_pnl*100:>+8.2f}% '
              f'{log_scale:>6.2f} {sign}{lift:>+12.2f}pp')


if __name__ == '__main__':
    main()
