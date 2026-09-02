"""Re-aggregate the N-floor metrics using raw_<tier> (true pre-cascade signal
counts) and fill counts derived from per_day.csv. Fixes the eff_<tier>
overcounting bug where same-day fills were excluded as 're-entry blocked'.

Reads:
  experiments/n_floor_v46/per_day.csv

Writes:
  experiments/n_floor_v46/summary_v2.json
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, '..', '..'))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from backtest_cascade import (  # noqa: E402
    run_cascade_backtest, MAX_POSITIONS, TIER_ALLOC, PUT_THRESHOLD,
)
from database.models.core import AlgorithmVersion  # noqa: E402

CALL_TIERS = ['95+', '85-94', '80-84', '75-79', '70-74']
PUT_TIERS  = ['p<=15', 'p16-20', 'p21-25']
ALL_TIERS  = CALL_TIERS + PUT_TIERS


def _q(values: list, q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[int(round((len(s) - 1) * q))]


def main():
    # Re-run backtest fresh — needed for trade_log fills (per_day.csv only has open
    # state, not entries/exits). Cheap relative to any other compute.
    av = AlgorithmVersion.get_active_scores_version()
    print(f"version_id={av.id}")
    result = run_cascade_backtest(
        version_id=av.id, min_score=70.0, from_date=date(2020, 1, 1),
        initial=50000.0, verbose=False,
    )

    # Build fills-per-(date, tier)
    fills: dict = defaultdict(lambda: defaultdict(int))
    for t in result['trade_log']:
        fills[t['entry_date']][t['tier']] += 1
    for h in result.get('open_holdings', []):
        fills[h['entry_date']][h['tier']] += 1

    # Read per_day.csv
    days = []
    with open(os.path.join(_HERE, 'per_day.csv')) as f:
        for row in csv.DictReader(f):
            days.append(row)

    # Per-tier aggregates using RAW (correct) offer counts
    n_floor = {}
    n_vs_fills_buckets = {}

    call_bins = [(0, 0), (1, 1), (2, 2), (3, 4), (5, 7), (8, 12), (13, 99)]
    put_bins  = [(0, 0), (1, 2), (3, 5), (6, 10), (11, 20), (21, 99)]

    for tier in ALL_TIERS:
        bins = put_bins if tier.startswith('p') else call_bins
        bin_buckets = {f'{lo}-{hi}': {'days': 0, 'sum_fills': 0, 'sum_offered': 0}
                       for lo, hi in bins}

        offered_values = []
        fills_values = []
        offered_when_pool_open = []
        fills_when_offered = []
        days_with_fill = 0

        for d in days:
            d_obj = date.fromisoformat(d['date'])
            n_off = int(d.get(f'raw_{tier}', 0))
            n_fill = fills.get(d_obj, {}).get(tier, 0)
            offered_values.append(n_off)
            fills_values.append(n_fill)
            pool_full = (d['pool_full'] == 'True')
            cash_bound = (d['cash_bound'] == 'True')
            if not pool_full and not cash_bound:
                offered_when_pool_open.append(n_off)
            if n_off > 0:
                fills_when_offered.append(n_fill)
            if n_fill > 0:
                days_with_fill += 1

            for lo, hi in bins:
                if lo <= n_off <= hi:
                    label = f'{lo}-{hi}'
                    bin_buckets[label]['days'] += 1
                    bin_buckets[label]['sum_fills'] += n_fill
                    bin_buckets[label]['sum_offered'] += n_off
                    break

        n_total_offered = sum(offered_values)
        n_total_fills = sum(fills_values)
        n_floor[tier] = {
            'offered_total': n_total_offered,
            'offered_per_year': n_total_offered / (len(days) / 252.0) if days else 0.0,
            'offered_p50': _q(offered_values, 0.50),
            'offered_p75': _q(offered_values, 0.75),
            'offered_p90': _q(offered_values, 0.90),
            'offered_mean': (n_total_offered / len(days)) if days else 0.0,
            'fills_total': n_total_fills,
            'fills_per_year': n_total_fills / (len(days) / 252.0) if days else 0.0,
            'fills_when_offered_p50': _q(fills_when_offered, 0.50),
            'fills_when_offered_mean': (sum(fills_when_offered) / len(fills_when_offered))
                if fills_when_offered else 0.0,
            'days_with_offer': len(fills_when_offered),
            'days_with_fill': days_with_fill,
            'days_total': len(days),
            'fill_rate_overall_pct': (100.0 * n_total_fills / n_total_offered)
                if n_total_offered else 0.0,
        }

        n_vs_fills_buckets[tier] = {
            label: {
                'days': v['days'],
                'mean_fills_per_day': (v['sum_fills'] / v['days']) if v['days'] else 0.0,
                'sum_fills': v['sum_fills'],
                'sum_offered': v['sum_offered'],
                'fill_rate_pct': (100.0 * v['sum_fills'] / v['sum_offered']) if v['sum_offered'] else 0.0,
            }
            for label, v in bin_buckets.items()
        }

    # ---------- year-by-year fills + cash-deployed-days share ----------
    by_year = defaultdict(lambda: {'fills': defaultdict(int),
                                    'premium_days': defaultdict(float)})
    for t in result['trade_log']:
        held = int(t.get('hold_bars', 0))
        if held <= 0:
            continue
        year = t['entry_date'].year
        by_year[year]['fills'][t['tier']] += 1
        by_year[year]['premium_days'][t['tier']] += float(t['premium']) * held
    for h in result.get('open_holdings', []):
        eq_curve = result['equity_curve']
        td_idx = {d: i for i, (d, _) in enumerate(eq_curve)}
        e = td_idx.get(h['entry_date'])
        if e is None:
            continue
        held = len(eq_curve) - e
        year = h['entry_date'].year
        by_year[year]['fills'][h['tier']] += 1
        by_year[year]['premium_days'][h['tier']] += float(h['premium']) * held

    yearly_table = {}
    for year in sorted(by_year.keys()):
        total_fills = sum(by_year[year]['fills'].values())
        total_pd = sum(by_year[year]['premium_days'].values())
        yearly_table[year] = {
            'fills_total': total_fills,
            'fills_by_tier': {t: by_year[year]['fills'].get(t, 0) for t in ALL_TIERS},
            'pd_share_by_tier': {
                t: (by_year[year]['premium_days'].get(t, 0.0) / total_pd) if total_pd else 0.0
                for t in ALL_TIERS
            },
        }

    out = {
        'meta': {
            'version_id': av.id,
            'window_start': '2020-01-01',
            'window_end': days[-1]['date'] if days else None,
            'n_trading_days': len(days),
            'n_closed_trades': len(result['trade_log']),
            'n_open_holdings': len(result.get('open_holdings', [])),
            'tier_alloc': dict(TIER_ALLOC),
            'max_positions': MAX_POSITIONS,
        },
        'n_floor_per_tier': n_floor,
        'per_tier_n_vs_fills': n_vs_fills_buckets,
        'yearly_fills_and_share': yearly_table,
    }

    out_path = os.path.join(_HERE, 'summary_v2.json')
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f"wrote {out_path}")

    # ---------- print preview ----------
    print()
    print(f"Window: 2020-01-01 → {days[-1]['date']}  ({len(days)} trading days, "
          f"{len(days)/252.0:.1f} years)")
    print()
    print(f"{'tier':>7}  {'offered/yr':>10}  {'fills/yr':>9}  {'fill_rate%':>10}  "
          f"{'off_p50':>7}  {'off_p75':>7}  {'off_p90':>7}  {'fill@off_mean':>13}")
    for t in ALL_TIERS:
        v = n_floor[t]
        print(f"{t:>7}  {v['offered_per_year']:>10.1f}  {v['fills_per_year']:>9.1f}  "
              f"{v['fill_rate_overall_pct']:>9.1f}%  {v['offered_p50']:>7.0f}  "
              f"{v['offered_p75']:>7.0f}  {v['offered_p90']:>7.0f}  "
              f"{v['fills_when_offered_mean']:>13.2f}")

    print()
    print('Year-by-year fills (count):')
    print(f"  {'year':>5}  {'total':>6}  " + '  '.join(f'{t:>7}' for t in ALL_TIERS))
    for year, payload in yearly_table.items():
        fbt = payload['fills_by_tier']
        cells = '  '.join(f'{fbt[t]:>7}' for t in ALL_TIERS)
        print(f"  {year:>5}  {payload['fills_total']:>6}  {cells}")

    print()
    print('Year-by-year cash-deployed-days share (%):')
    print(f"  {'year':>5}  " + '  '.join(f'{t:>6}' for t in ALL_TIERS))
    for year, payload in yearly_table.items():
        pdb = payload['pd_share_by_tier']
        cells = '  '.join(f'{pdb[t]*100:>5.1f}%' for t in ALL_TIERS)
        print(f"  {year:>5}  {cells}")

    print()
    print('Per-tier N-vs-fills bin scan (binding tiers only — call/put):')
    for tier in ALL_TIERS:
        if tier == '70-74':
            continue
        print(f'  {tier}:')
        for label, v in n_vs_fills_buckets[tier].items():
            if v['days'] == 0:
                continue
            print(f"    offered={label:>5}: days={v['days']:>4}  fills={v['sum_fills']:>5}  "
                  f"mean_fills/day={v['mean_fills_per_day']:>4.2f}  fill_rate={v['fill_rate_pct']:>5.1f}%")


if __name__ == '__main__':
    main()
