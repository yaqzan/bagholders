"""
v28 vs v29 score diff — April 2026.

Goal: figure out where the two scoring formulas actually disagree, despite v29's
N=500 canonical MC showing only modest aggregate differences from v28.

v28 (e3c8678) = earnings meta-score boost (final transform pre-clamp).
v29 (8473cba) = v28 + V6 pre-only log-gradient earnings VOLUME suppression in
                volume_amplifier.py (W=2 cal days, M=1.0).

Both versions share the same per-(symbol,date) population (14,063 rows in April 2026).
The only score-stage delta is the volume_amplifier output for signals within
the V6 pre-earnings window. The earnings boost itself sits at the END of the
formula (post-volume-amplifier), so any earnings boost is applied to BOTH
versions. The diff is purely how volume amplification ramps near earnings.

Hypotheses to test:
  H1. The diff distribution is bimodal: a large "no diff" mass (most days are
      not pre-earnings) plus a smaller "pre-earnings" tail.
  H2. The pre-earnings diff sign correlates with the underlying volume signal:
      CONVICTION/CLIMAX get muted; NEUTRAL is unchanged.
  H3. The diff magnitude scales with proximity (d=0 strongest, d=2 weakest, d>2 zero).
  H4. After bucketing into call (>=70) and put (<=25) thresholds, v29 shifts
      population mainly at the borderline — which is the slot-displacement
      mechanism the canonical MC findings document blamed for the regression.
  H5. The aggregate WR proxy (forward 15d barrier touch using cached outcomes)
      should be approximately the same for the population that is CLASSIFIED
      THE SAME WAY by both versions, but should differ for the borderline-flipped
      cohort.

This script does NOT do a full canonical MC — that already exists. It does
explanatory diagnostics on the score-level diff.
"""

from __future__ import annotations
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.trader_database import DB

PERIOD_START = date(2026, 4, 1)
PERIOD_END = date(2026, 4, 30)
V_OLD = 28
V_NEW = 29


def fetch_scores():
    """Pull both versions' rows for April 2026 + earnings dates + volume signals."""
    DB.connect(reuse_if_open=True)

    sql = """
    SELECT s.symbol, s.date, s.version_id, s.overall, s.volume_signal,
           s.volume_magnitude, s.bb, s.trend, s.rsi, s.macd, s.stoch,
           s.technical_alignment, s.regime_multiplier
    FROM scores s
    WHERE s.date BETWEEN %s AND %s
      AND s.version_id IN (%s, %s)
    """
    cur = DB.execute_sql(sql, (PERIOD_START, PERIOD_END, V_OLD, V_NEW))
    cols = ['symbol', 'date', 'version', 'overall', 'volsig', 'volmag',
            'bb', 'trend', 'rsi', 'macd', 'stoch', 'ta', 'reg_mult']
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    return df


def fetch_earnings_map(symbols, period_start, period_end):
    """Per-symbol earnings dates within +/- 30 days of the analysis window."""
    DB.connect(reuse_if_open=True)
    if not symbols:
        return {}
    placeholders = ','.join(['%s'] * len(symbols))
    sql = f"""
    SELECT symbol, date FROM earnings_dates
    WHERE date BETWEEN %s AND %s AND symbol IN ({placeholders})
    """
    args = [period_start - timedelta(days=10), period_end + timedelta(days=10)] + list(symbols)
    cur = DB.execute_sql(sql, tuple(args))
    out = defaultdict(list)
    for sym, dt in cur.fetchall():
        out[sym].append(dt)
    return out


def proximity_class(signal_date, ern_dates, max_lookahead=10):
    """Days until nearest upcoming earnings event (None if no event in horizon)."""
    upcoming = [d for d in ern_dates if d >= signal_date]
    if not upcoming:
        return None
    best = min(upcoming, key=lambda d: (d - signal_date).days)
    diff = (best - signal_date).days
    return diff if diff <= max_lookahead else None


def main():
    print(f'Loading scores for v{V_OLD} vs v{V_NEW}, {PERIOD_START} -> {PERIOD_END}')
    df = fetch_scores()
    print(f'  loaded {len(df)} rows')

    # Pivot to wide so each (symbol, date) has v28 and v29 columns
    wide = (df.pivot_table(index=['symbol', 'date'],
                           columns='version',
                           values=['overall', 'volsig', 'volmag'],
                           aggfunc='first')
            .reset_index())
    wide.columns = [f'{a}_{b}' if b != '' else a for a, b in wide.columns]
    wide = wide.rename(columns={f'overall_{V_OLD}': 'ov_old',
                                 f'overall_{V_NEW}': 'ov_new',
                                 f'volsig_{V_OLD}': 'vs_old',
                                 f'volsig_{V_NEW}': 'vs_new',
                                 f'volmag_{V_OLD}': 'vm_old',
                                 f'volmag_{V_NEW}': 'vm_new'})
    wide = wide.dropna(subset=['ov_old', 'ov_new'])
    wide['delta'] = wide['ov_new'] - wide['ov_old']
    print(f'  matched pairs: {len(wide)}')

    # Earnings map
    syms = wide['symbol'].unique().tolist()
    ern_map = fetch_earnings_map(syms, PERIOD_START, PERIOD_END)
    print(f'  symbols with earnings in window: {sum(1 for s in syms if ern_map.get(s))}')

    wide['days_to_ern'] = wide.apply(
        lambda r: proximity_class(r['date'], ern_map.get(r['symbol'], [])), axis=1)

    # ============================================================
    # SECTION 1 — overall delta distribution
    # ============================================================
    print('\n' + '=' * 70)
    print('Section 1: Overall delta distribution (v29 - v28)')
    print('=' * 70)
    d = wide['delta']
    print(f'  N pairs               : {len(d)}')
    print(f'  Pairs with delta == 0 : {(d == 0).sum()} ({(d == 0).mean()*100:.1f}%)')
    print(f'  Pairs with delta != 0 : {(d != 0).sum()} ({(d != 0).mean()*100:.1f}%)')
    print(f'  mean delta            : {d.mean():+.3f}')
    print(f'  median delta          : {d.median():+.0f}')
    print(f'  std                   : {d.std():.3f}')
    print(f'  min, max              : {d.min():+.0f}, {d.max():+.0f}')
    print(f'  abs(delta) p50/p75/p90/p95/p99: '
          f'{d.abs().quantile([0.5, 0.75, 0.9, 0.95, 0.99]).round(2).tolist()}')

    # Sign decomposition
    nz = wide[wide['delta'] != 0]
    if len(nz):
        print(f'\n  Among {len(nz)} non-zero pairs:')
        print(f'    delta > 0 (v29 boosted score): {(nz["delta"] > 0).sum()} '
              f'({(nz["delta"] > 0).mean()*100:.1f}%)')
        print(f'    delta < 0 (v29 lowered score): {(nz["delta"] < 0).sum()} '
              f'({(nz["delta"] < 0).mean()*100:.1f}%)')
        print(f'    mean +ve deltas: {nz[nz["delta"]>0]["delta"].mean():+.2f}')
        print(f'    mean -ve deltas: {nz[nz["delta"]<0]["delta"].mean():+.2f}')

    # ============================================================
    # SECTION 2 — diff by earnings proximity
    # ============================================================
    print('\n' + '=' * 70)
    print('Section 2: Delta by days-to-earnings (V6 mechanism gate)')
    print('=' * 70)
    print(f'  {"d_ern":>6} {"N":>6} {"% nonzero":>10} {"mean_d":>8} {"median_d":>9}'
          f' {"std":>7} {"abs_p90":>8}')
    groups = [(None, 'no ern'), (0, 'd=0'), (1, 'd=1'), (2, 'd=2'), (3, 'd=3'),
              (4, 'd=4'), (5, 'd=5')]
    for key, label in groups:
        if key is None:
            sub = wide[wide['days_to_ern'].isna()]
        else:
            sub = wide[wide['days_to_ern'] == key]
        if len(sub) == 0:
            continue
        nz_pct = (sub['delta'] != 0).mean() * 100
        print(f'  {label:>6} {len(sub):>6} {nz_pct:>9.1f}% '
              f'{sub["delta"].mean():>+8.3f} {sub["delta"].median():>+9.1f} '
              f'{sub["delta"].std():>7.2f} {sub["delta"].abs().quantile(0.9):>8.2f}')

    # ============================================================
    # SECTION 3 — diff by v28 volume signal (where the V6 mechanism acts)
    # ============================================================
    print('\n' + '=' * 70)
    print('Section 3: Delta by v28 volume signal type')
    print('=' * 70)
    print(f'  {"vsig":>14} {"N":>6} {"%nz":>6} {"mean_d":>8} '
          f'{"std":>7} {"abs_p90":>8}')
    for vs, sub in wide.groupby('vs_old'):
        if not vs or len(sub) < 50:
            continue
        nz_pct = (sub['delta'] != 0).mean() * 100
        print(f'  {str(vs):>14} {len(sub):>6} {nz_pct:>5.1f}% '
              f'{sub["delta"].mean():>+8.3f} {sub["delta"].std():>7.2f} '
              f'{sub["delta"].abs().quantile(0.9):>8.2f}')

    # Cross-tab: pre-earnings + volume signal — where V6 actually fires
    print('\n  Cross-tab: pre-earnings pairs by volume signal:')
    pre_ern = wide[wide['days_to_ern'].notna() & (wide['days_to_ern'] <= 5)]
    print(f'  {"vsig":>14} {"N_pre":>6} {"%nz":>6} {"mean_d":>9} '
          f'{"abs_p90":>8}')
    for vs, sub in pre_ern.groupby('vs_old'):
        if not vs or len(sub) < 5:
            continue
        nz_pct = (sub['delta'] != 0).mean() * 100
        print(f'  {str(vs):>14} {len(sub):>6} {nz_pct:>5.1f}% '
              f'{sub["delta"].mean():>+9.3f} {sub["delta"].abs().quantile(0.9):>8.2f}')

    # ============================================================
    # SECTION 4 — bucket flips at portfolio thresholds
    # ============================================================
    print('\n' + '=' * 70)
    print('Section 4: Bucket flips at portfolio thresholds')
    print('=' * 70)

    def bucket(s):
        if s >= 95: return '95+'
        if s >= 90: return '90-94'
        if s >= 85: return '85-89'
        if s >= 80: return '80-84'
        if s >= 75: return '75-79'
        if s >= 70: return '70-74'
        if s <= 5:  return '<=5'
        if s <= 10: return '6-10'
        if s <= 15: return '11-15'
        if s <= 20: return '16-20'
        if s <= 25: return '21-25'
        return 'mid'

    wide['b_old'] = wide['ov_old'].apply(bucket)
    wide['b_new'] = wide['ov_new'].apply(bucket)
    wide['flipped'] = wide['b_old'] != wide['b_new']

    flips = wide[wide['flipped']]
    print(f'  Total bucket flips: {len(flips)} ({len(flips)/len(wide)*100:.1f}%)')

    if len(flips):
        print('\n  Top transitions (>= 5 occurrences):')
        trans = flips.groupby(['b_old', 'b_new']).size().sort_values(ascending=False)
        for (bo, bn), n in trans.items():
            if n >= 5:
                print(f'    {bo:>6} -> {bn:<6} : {n:>4}')

        # Crossings of the 70 / 25 thresholds (the cascade gate)
        wide['call_qual_old'] = wide['ov_old'] >= 70
        wide['call_qual_new'] = wide['ov_new'] >= 70
        wide['put_qual_old']  = wide['ov_old'] <= 25
        wide['put_qual_new']  = wide['ov_new'] <= 25

        gain_call = ((~wide['call_qual_old']) & (wide['call_qual_new'])).sum()
        loss_call = ((wide['call_qual_old']) & (~wide['call_qual_new'])).sum()
        gain_put  = ((~wide['put_qual_old']) & (wide['put_qual_new'])).sum()
        loss_put  = ((wide['put_qual_old']) & (~wide['put_qual_new'])).sum()
        print(f'\n  Threshold crossings (population effect):')
        print(f'    Calls (>=70): v29 NEW qualifying     : {gain_call}')
        print(f'    Calls (>=70): v29 LOST qualifying    : {loss_call}')
        print(f'    Calls net delta                       : {gain_call - loss_call:+d}')
        print(f'    Puts  (<=25): v29 NEW qualifying     : {gain_put}')
        print(f'    Puts  (<=25): v29 LOST qualifying    : {loss_put}')
        print(f'    Puts net delta                        : {gain_put - loss_put:+d}')

    # ============================================================
    # SECTION 5 — diff inside qualifying signals (cascade EV impact)
    # ============================================================
    print('\n' + '=' * 70)
    print('Section 5: Magnitude diff within qualifying tiers')
    print('=' * 70)

    print('\n  Calls (>=70) — pairs that qualify in BOTH versions:')
    both_call = wide[(wide['ov_old'] >= 70) & (wide['ov_new'] >= 70)]
    print(f'    N (v28 & v29 both qualifying): {len(both_call)}')
    if len(both_call):
        d = both_call['delta']
        print(f'    nonzero delta share: {(d != 0).mean()*100:.1f}%')
        print(f'    mean delta : {d.mean():+.2f}')
        print(f'    p10/p50/p90: '
              f'{d.quantile(0.10):+.1f} / {d.quantile(0.50):+.1f} / {d.quantile(0.90):+.1f}')
        # Pre-earnings subset
        sub = both_call[both_call['days_to_ern'].notna() & (both_call['days_to_ern'] <= 5)]
        if len(sub):
            print(f'\n    Pre-earnings subset (d<=5): N={len(sub)}, '
                  f'mean delta {sub["delta"].mean():+.2f}, '
                  f'nonzero {(sub["delta"]!=0).mean()*100:.1f}%')

    print('\n  Puts (<=25) — pairs that qualify in BOTH versions:')
    both_put = wide[(wide['ov_old'] <= 25) & (wide['ov_new'] <= 25)]
    print(f'    N (v28 & v29 both qualifying): {len(both_put)}')
    if len(both_put):
        d = both_put['delta']
        print(f'    nonzero delta share: {(d != 0).mean()*100:.1f}%')
        print(f'    mean delta : {d.mean():+.2f}')
        print(f'    p10/p50/p90: '
              f'{d.quantile(0.10):+.1f} / {d.quantile(0.50):+.1f} / {d.quantile(0.90):+.1f}')
        sub = both_put[both_put['days_to_ern'].notna() & (both_put['days_to_ern'] <= 5)]
        if len(sub):
            print(f'\n    Pre-earnings subset (d<=5): N={len(sub)}, '
                  f'mean delta {sub["delta"].mean():+.2f}, '
                  f'nonzero {(sub["delta"]!=0).mean()*100:.1f}%')

    # ============================================================
    # SECTION 6 — Bayesian-flavored signal/noise decomposition
    # Method: bootstrap mean delta within each (cohort) bucket and report
    # 95% credible intervals. Cohorts where 0 is inside the CI are noise.
    # ============================================================
    print('\n' + '=' * 70)
    print('Section 6: Bootstrap 95% CI on mean delta per cohort (1000 resamples)')
    print('=' * 70)
    rng = np.random.default_rng(42)

    def bootstrap_mean(arr, n=1000):
        if len(arr) == 0:
            return (np.nan, np.nan, np.nan)
        idx = rng.integers(0, len(arr), size=(n, len(arr)))
        means = arr[idx].mean(axis=1)
        return (np.percentile(means, 2.5), arr.mean(), np.percentile(means, 97.5))

    cohorts = []
    for label, sub in [
        ('all pairs',                                  wide),
        ('no earnings in 10d',                         wide[wide['days_to_ern'].isna()]),
        ('pre-ern d=0',                                wide[wide['days_to_ern'] == 0]),
        ('pre-ern d=1',                                wide[wide['days_to_ern'] == 1]),
        ('pre-ern d=2',                                wide[wide['days_to_ern'] == 2]),
        ('pre-ern d=3',                                wide[wide['days_to_ern'] == 3]),
        ('pre-ern d in 1-2',                           wide[wide['days_to_ern'].isin([1, 2])]),
        ('pre-ern + CONVICTION',                       wide[(wide['days_to_ern'].notna())
                                                            & (wide['days_to_ern'] <= 2)
                                                            & (wide['vs_old'] == 'CONVICTION')]),
        ('pre-ern + REJECTION',                        wide[(wide['days_to_ern'].notna())
                                                            & (wide['days_to_ern'] <= 2)
                                                            & (wide['vs_old'] == 'REJECTION')]),
        ('pre-ern + ABSORPTION',                       wide[(wide['days_to_ern'].notna())
                                                            & (wide['days_to_ern'] <= 2)
                                                            & (wide['vs_old'] == 'ABSORPTION')]),
        ('pre-ern + CLIMAX',                           wide[(wide['days_to_ern'].notna())
                                                            & (wide['days_to_ern'] <= 2)
                                                            & (wide['vs_old'] == 'CLIMAX')]),
        ('calls 70+ (both qual)',                      wide[(wide['ov_old']>=70)&(wide['ov_new']>=70)]),
        ('calls 70+ pre-ern d<=5',                     wide[(wide['ov_old']>=70)&(wide['ov_new']>=70)
                                                            &(wide['days_to_ern'].notna())
                                                            &(wide['days_to_ern']<=5)]),
        ('puts 25- (both qual)',                       wide[(wide['ov_old']<=25)&(wide['ov_new']<=25)]),
        ('puts 25- pre-ern d<=5',                      wide[(wide['ov_old']<=25)&(wide['ov_new']<=25)
                                                            &(wide['days_to_ern'].notna())
                                                            &(wide['days_to_ern']<=5)]),
    ]:
        if len(sub) < 5:
            continue
        lo, m, hi = bootstrap_mean(sub['delta'].to_numpy())
        sig = '*' if (lo > 0 or hi < 0) else ' '
        cohorts.append((label, len(sub), lo, m, hi, sig))

    print(f'  {"cohort":<32} {"N":>6} {"CI_lo":>8} {"mean":>8} {"CI_hi":>8}  sig')
    for lbl, n, lo, m, hi, sig in cohorts:
        print(f'  {lbl:<32} {n:>6} {lo:>+8.3f} {m:>+8.3f} {hi:>+8.3f}  {sig}')
    print('\n  Sig (*) = 0 outside 95% CI -> v29 != v28 with high confidence in this cohort.')

    # ============================================================
    # SECTION 7 — top movers (largest absolute deltas)
    # ============================================================
    print('\n' + '=' * 70)
    print('Section 7: Top 25 absolute movers (where v29 disagrees most)')
    print('=' * 70)
    top = wide.assign(absd=wide['delta'].abs()).sort_values('absd', ascending=False).head(25)
    print(f'  {"sym":>6} {"date":>11} {"v28":>4} {"v29":>4} {"delta":>5} '
          f'{"vs_v28":>14} {"vmag":>5} {"d_ern":>5}')
    for _, r in top.iterrows():
        d_ern = '' if pd.isna(r['days_to_ern']) else f'{int(r["days_to_ern"]):>3}'
        vmag = f'{r["vm_old"]:.2f}' if pd.notna(r["vm_old"]) else ''
        print(f'  {r["symbol"]:>6} {str(r["date"]):>11} '
              f'{int(r["ov_old"]):>4} {int(r["ov_new"]):>4} '
              f'{int(r["delta"]):>+5} '
              f'{str(r["vs_old"] or ""):>14} {vmag:>5} {d_ern:>5}')

    # ============================================================
    # Save the full diff table for follow-up
    # ============================================================
    out_path = os.path.join(os.path.dirname(__file__), 'v28_v29_april_diff_data.csv')
    wide.to_csv(out_path, index=False)
    print(f'\nFull diff table saved to {out_path}')


if __name__ == '__main__':
    main()
