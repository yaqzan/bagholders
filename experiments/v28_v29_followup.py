"""
Follow-up: separate pre-ern vs post-ern proximity. The original script only
detected upcoming earnings (positive proximity), but v29's V6 changes BOTH:
  - Pre-earnings (D-2, D-1, D): added a log-gradient suppression
  - Post-earnings (D+1): v28's symmetric +/-1d suppression killed volume on D+1;
    v29 (pre-only) RESTORES the volume signal on D+1.

So delta != 0 outside the "upcoming" window is concentrated on D+1 days.
"""

from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
from datetime import date, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.trader_database import DB

PERIOD_START = date(2026, 4, 1)
PERIOD_END = date(2026, 4, 30)


def load():
    csv = os.path.join(os.path.dirname(__file__), 'v28_v29_april_diff_data.csv')
    df = pd.read_csv(csv, parse_dates=['date'])
    df['date'] = df['date'].dt.date
    return df


def fetch_ern(syms):
    DB.connect(reuse_if_open=True)
    placeholders = ','.join(['%s'] * len(syms))
    sql = f'SELECT symbol, date FROM earnings_dates WHERE date BETWEEN %s AND %s AND symbol IN ({placeholders})'
    args = [PERIOD_START - timedelta(days=15), PERIOD_END + timedelta(days=15)] + list(syms)
    cur = DB.execute_sql(sql, tuple(args))
    out = defaultdict(list)
    for s, d in cur.fetchall():
        out[s].append(d)
    return out


def signed_proximity(signal_date, ern_dates, max_window=10):
    """Return signed days-to-nearest-earnings; negative = past; None = far."""
    if not ern_dates:
        return None
    nearest = min(ern_dates, key=lambda d: abs((d - signal_date).days))
    diff = (nearest - signal_date).days
    if abs(diff) > max_window:
        return None
    return diff


def main():
    df = load()
    print(f'Loaded {len(df)} pairs; {(df.delta != 0).sum()} non-identical')

    ern = fetch_ern(df['symbol'].unique().tolist())
    df['signed_prox'] = df.apply(
        lambda r: signed_proximity(r['date'], ern.get(r['symbol'], [])), axis=1)

    # Categorise by signed proximity
    def cat(p):
        if pd.isna(p):
            return 'far/none'
        p = int(p)
        if p == 0:    return 'D=0'
        if p == 1:    return 'D-1 (pre)'
        if p == 2:    return 'D-2 (pre)'
        if p == 3:    return 'D-3 (pre)'
        if p >= 4:    return f'D-{p} (pre)'
        if p == -1:   return 'D+1 (post)'
        if p == -2:   return 'D+2 (post)'
        if p == -3:   return 'D+3 (post)'
        return f'D{p} (post)'

    df['ern_cat'] = df['signed_prox'].apply(cat)

    print('\n' + '=' * 70)
    print('Delta by SIGNED earnings proximity (negative = post-earnings)')
    print('=' * 70)
    print(f'  {"category":<14} {"N":>6} {"%nz":>6} {"mean_d":>9} '
          f'{"std":>7} {"abs_p90":>8} {"interp":>30}')
    interp = {
        'D=0':        'v28=v29 (both suppress)',
        'D-1 (pre)':  'v28 suppressed; v29 partial restore',
        'D-2 (pre)':  'v28 active; v29 partial supp',
        'D-3 (pre)':  'v28 active; v29 active (no diff expected)',
        'D+1 (post)': 'v28 suppressed; v29 RESTORES vol',
        'D+2 (post)': 'v28 active; v29 active',
    }

    order = ['D-3 (pre)', 'D-2 (pre)', 'D-1 (pre)', 'D=0',
             'D+1 (post)', 'D+2 (post)', 'D+3 (post)', 'far/none']
    cats_present = [c for c in order if c in df['ern_cat'].unique()]
    for c in cats_present:
        sub = df[df['ern_cat'] == c]
        if len(sub) < 5:
            continue
        nz = (sub['delta'] != 0).mean() * 100
        print(f'  {c:<14} {len(sub):>6} {nz:>5.1f}% '
              f'{sub["delta"].mean():>+9.3f} {sub["delta"].std():>7.2f} '
              f'{sub["delta"].abs().quantile(0.9):>8.2f} {interp.get(c,""):>30}')

    # Post-ern + volume signal cross-tab
    print('\n' + '=' * 70)
    print('D+1 (post-earnings): where v29 restored volume signal v28 had killed')
    print('=' * 70)
    post1 = df[df['ern_cat'] == 'D+1 (post)']
    if len(post1) > 0:
        nz_pct = (post1['delta'] != 0).mean() * 100
        print(f'  N={len(post1)}, nonzero {nz_pct:.1f}%, mean delta {post1["delta"].mean():+.3f}')
        print(f'  By v28 volume signal:')
        for vs, sub in post1.groupby('vs_old'):
            if not vs or pd.isna(vs) or len(sub) < 3:
                continue
            nz = (sub['delta'] != 0).mean() * 100
            print(f'    {str(vs):>14} N={len(sub):>4} nz={nz:>5.1f}%  '
                  f'mean_d={sub["delta"].mean():>+7.2f}  '
                  f'abs_p90={sub["delta"].abs().quantile(0.9):>5.1f}')

    # Bayesian-bootstrap CI per signed proximity cohort
    print('\n' + '=' * 70)
    print('Bootstrap 95% CI: mean delta by signed proximity (1000 resamples)')
    print('=' * 70)
    rng = np.random.default_rng(42)
    print(f'  {"category":<14} {"N":>6} {"CI_lo":>8} {"mean":>8} {"CI_hi":>8}  sig')
    for c in cats_present:
        sub = df[df['ern_cat'] == c]
        if len(sub) < 10:
            continue
        arr = sub['delta'].to_numpy()
        idx = rng.integers(0, len(arr), size=(1000, len(arr)))
        means = arr[idx].mean(axis=1)
        lo, hi = np.percentile(means, [2.5, 97.5])
        sig = '*' if (lo > 0 or hi < 0) else ' '
        print(f'  {c:<14} {len(sub):>6} {lo:>+8.3f} {arr.mean():>+8.3f} {hi:>+8.3f}  {sig}')

    # Direction asymmetry: do the diffs at D+1 look different from D-1?
    print('\n' + '=' * 70)
    print('Asymmetry probe: D-1 (pre) vs D+1 (post) score-direction profile')
    print('=' * 70)
    for c in ['D-1 (pre)', 'D+1 (post)']:
        sub = df[df['ern_cat'] == c]
        if len(sub) == 0: continue
        nz = sub[sub['delta'] != 0]
        if len(nz) == 0: continue
        bull_v28 = (sub['ov_old'] >= 50).mean() * 100
        bull_v29 = (sub['ov_new'] >= 50).mean() * 100
        delta_when_bull = sub[sub['ov_old'] >= 50]['delta'].mean()
        delta_when_bear = sub[sub['ov_old'] <  50]['delta'].mean()
        print(f'  {c}: N={len(sub)}, %bullish v28={bull_v28:.1f}, v29={bull_v29:.1f}')
        print(f'    mean delta on v28-bullish rows: {delta_when_bull:+.3f}')
        print(f'    mean delta on v28-bearish rows: {delta_when_bear:+.3f}')

    # Where do the D+1 deltas push qualifying signals?
    print('\n' + '=' * 70)
    print('D+1 cohort: bucket transitions (post-earnings volume restored)')
    print('=' * 70)
    p1 = df[df['ern_cat'] == 'D+1 (post)']
    if len(p1):
        flipped = p1[p1['b_old'] != p1['b_new']]
        print(f'  N D+1 = {len(p1)}, bucket flips = {len(flipped)} ({len(flipped)/len(p1)*100:.1f}%)')
        if len(flipped):
            for (bo, bn), n in flipped.groupby(['b_old', 'b_new']).size().sort_values(ascending=False).items():
                if n >= 2:
                    print(f'    {bo:>6} -> {bn:<6} : {n}')


if __name__ == '__main__':
    main()
