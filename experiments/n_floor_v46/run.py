"""
N-floor empirical replay on the active scoring version (provisional v46 baseline).

Runs the deterministic cascade backtest from 2020-01-01 to the latest available
date, then reconstructs per-day portfolio state to produce:

  Primary metric (a):  cash-deployed-days / year per tier
  Sanity (b):          open-slot saturation histogram

Plus per-day classification (cash-bound / signal-bound / filling) and per-tier
N-vs-throughput plateau curve. Output: per_day.csv, summary.json, FINDINGS.md.

Usage:
  PYTHONIOENCODING=utf-8 python -u experiments/n_floor_v46/run.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import date

# Make repo root importable when run as a script.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backtest_cascade import (
    run_cascade_backtest,
    MAX_POSITIONS,
    TIER_ALLOC,
    PUT_THRESHOLD,
)
from database.models.core import AlgorithmVersion

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
START_DATE = date(2020, 1, 1)
INITIAL_CAPITAL = 50_000.0

# Tier sets in cascade walk order.
CALL_TIERS = ['95+', '85-94', '80-84', '75-79', '70-74']
PUT_TIERS  = ['p<=15', 'p16-20', 'p21-25']
ALL_TIERS  = CALL_TIERS + PUT_TIERS


def _key(out_obj) -> str:
    """Tier key from a TradeOutcome (already 'p<=15' / '95+' style)."""
    return out_obj.tier


def reconstruct_per_day_state(result: dict) -> tuple[list[dict], dict]:
    """For each trading day in the window, reconstruct:
        - open position count + premium sum + cash
        - signals offered (raw, by tier)
        - signals offered (after re-entry block, by tier)
        - cash-bound / pool-full flags
    """
    trade_log = result['trade_log']
    equity_curve = result['equity_curve']
    outcomes_by_date = result['outcomes_by_date']
    open_holdings = result.get('open_holdings', [])

    if not equity_curve:
        return [], {}

    trading_days = [d for d, _ in equity_curve]
    eq_by_date = {d: e for d, e in equity_curve}
    td_idx = {d: i for i, d in enumerate(trading_days)}
    n_days = len(trading_days)

    # Per-day open position list: (tier, side, premium, symbol)
    open_pos: list[list] = [[] for _ in range(n_days)]

    # Per-tier slot-day counter (closed trades only; open holdings tracked separately
    # so we can decide whether to include the partial tail year).
    closed_slot_days: dict[str, int] = defaultdict(int)
    closed_premium_days: dict[str, float] = defaultdict(float)  # cash-deployed-days

    # CLOSED trades from trade_log
    for t in trade_log:
        e = td_idx.get(t['entry_date'])
        x = td_idx.get(t['exit_date'])
        if e is None:
            continue
        if x is None:
            x = n_days
        held = max(0, x - e)
        tier = t['tier']
        for i in range(e, x):
            open_pos[i].append((tier, t['side'], t['premium'], t['symbol']))
        closed_slot_days[tier] += held
        closed_premium_days[tier] += t['premium'] * held

    # STILL-OPEN positions at end of window
    for h in open_holdings:
        e = td_idx.get(h['entry_date'])
        if e is None:
            continue
        x = n_days
        held = x - e
        tier = h['tier']
        for i in range(e, x):
            open_pos[i].append((tier, h['side'], h['premium'], h['symbol']))
        closed_slot_days[tier] += held
        closed_premium_days[tier] += h['premium'] * held

    # Per-day reconstruction
    days = []
    for i, d in enumerate(trading_days):
        eq = eq_by_date[d]
        pos = open_pos[i]
        n_open = len(pos)
        total_prem = sum(p[2] for p in pos)
        cash = eq - total_prem
        open_syms = {p[3] for p in pos}

        offered = outcomes_by_date.get(d, [])
        # Raw and post-re-entry-block counts, per tier
        raw_by_tier = defaultdict(int)
        eff_by_tier = defaultdict(int)
        for o in offered:
            raw_by_tier[o.tier] += 1
            if o.symbol not in open_syms:
                eff_by_tier[o.tier] += 1

        # Smallest possible new-trade cost (any tier)
        min_tier_cost = min(TIER_ALLOC.values()) * eq

        days.append({
            'date': d.isoformat(),
            'equity': float(eq),
            'cash': float(cash),
            'total_open_premium': float(total_prem),
            'n_open': n_open,
            'pool_full': n_open >= MAX_POSITIONS,
            'cash_bound': cash < min_tier_cost,
            'offered_raw_total': sum(raw_by_tier.values()),
            'offered_eff_total': sum(eff_by_tier.values()),
            **{f'raw_{t}': raw_by_tier.get(t, 0) for t in ALL_TIERS},
            **{f'eff_{t}': eff_by_tier.get(t, 0) for t in ALL_TIERS},
            **{f'open_{t}': sum(1 for p in pos if p[0] == t) for t in ALL_TIERS},
        })

    aggregates = {
        'closed_slot_days_by_tier': dict(closed_slot_days),
        'closed_premium_days_by_tier': dict(closed_premium_days),
    }
    return days, aggregates


# --------------------------------------------------------------------------- #
# Aggregations
# --------------------------------------------------------------------------- #

def saturation_histogram(days: list[dict]) -> dict:
    """% of trading days in each open-slot bucket. Sanity check (b)."""
    bins = [(0, 0), (1, 3), (4, 7), (8, 13), (14, 14)]
    hist = {f'{lo}-{hi}': 0 for lo, hi in bins}
    for d in days:
        n = d['n_open']
        for lo, hi in bins:
            if lo <= n <= hi:
                hist[f'{lo}-{hi}'] += 1
                break
    total = max(1, len(days))
    return {k: {'count': v, 'pct': 100.0 * v / total} for k, v in hist.items()}


def cash_deployed_days_per_tier_year(days: list[dict]) -> dict:
    """Primary metric (a). Sums (open_premium × 1 day) per tier per year, then
    normalises to ratio of total premium-days for that year (so future
    comparisons are scale-invariant).

    Also reports total dollar premium-days per year for absolute reference.
    """
    by_year_tier = defaultdict(lambda: defaultdict(float))
    by_year_total = defaultdict(float)
    for d in days:
        year = int(d['date'][:4])
        for t in ALL_TIERS:
            # We need premium-days, not just slot-days. We didn't store per-day
            # premium-by-tier directly because it would bloat per_day.csv. Reuse
            # the count of open slots × the average premium of that tier on
            # that day from a separate pass — handled in the deeper pass below.
            pass
    return {}  # placeholder; computed in compute_premium_days_per_year_tier


def compute_premium_days_per_year_tier(result: dict, days: list[dict]) -> dict:
    """Single pass: for every closed trade and every still-open holding, add
    premium × hold-days into (year, tier) bucket. Year = entry_date.year.

    This is the cleanest definition of cash-deployed-days: each dollar held in a
    position for one trading day = 1 premium-day.
    """
    by_year_tier = defaultdict(lambda: defaultdict(float))
    by_year_total = defaultdict(float)

    for t in result['trade_log']:
        held = int(t.get('hold_bars', 0))
        if held <= 0:
            continue
        prem = float(t['premium'])
        year = int(t['entry_date'].year)
        by_year_tier[year][t['tier']] += prem * held
        by_year_total[year] += prem * held

    # Open holdings: count from entry_date to last day in equity_curve
    eq_curve = result['equity_curve']
    if eq_curve:
        last_day = eq_curve[-1][0]
        td_idx = {d: i for i, (d, _) in enumerate(eq_curve)}
        for h in result.get('open_holdings', []):
            e = td_idx.get(h['entry_date'])
            if e is None:
                continue
            held = len(eq_curve) - e
            prem = float(h['premium'])
            year = int(h['entry_date'].year)
            by_year_tier[year][h['tier']] += prem * held
            by_year_total[year] += prem * held

    out = {}
    for year, tier_dict in sorted(by_year_tier.items()):
        out[year] = {
            'total_premium_days': by_year_total[year],
            'by_tier': {t: tier_dict.get(t, 0.0) for t in ALL_TIERS},
            'by_tier_share': {
                t: (tier_dict.get(t, 0.0) / by_year_total[year]) if by_year_total[year] > 0 else 0.0
                for t in ALL_TIERS
            },
        }
    return out


def slot_occupancy_share(aggregates: dict) -> dict:
    """% of total slot-days occupied by each tier."""
    sd = aggregates['closed_slot_days_by_tier']
    total = sum(sd.values()) or 1
    return {t: {'slot_days': sd.get(t, 0), 'share_pct': 100.0 * sd.get(t, 0) / total}
            for t in ALL_TIERS}


def day_classification(days: list[dict]) -> dict:
    """Classify each trading day:
        - filling      : pool not full AND >=1 effective signal AND cash sufficient
        - cash_bound   : cash < smallest tier cost AND >=1 effective signal
        - signal_bound : pool not full AND no effective signals AND cash sufficient
        - pool_full    : pool already at MAX_POSITIONS (regardless of signals)
        - dd_breaker   : (approximated as pool empty AND no fills despite signals;
                          actual breaker state is not exposed in trade_log)
    """
    counts = {'filling': 0, 'cash_bound': 0, 'signal_bound': 0,
              'pool_full': 0, 'idle': 0}
    for d in days:
        n_open = d['n_open']
        cash_bound = d['cash_bound']
        eff = d['offered_eff_total']
        if n_open >= MAX_POSITIONS:
            counts['pool_full'] += 1
        elif cash_bound and eff > 0:
            counts['cash_bound'] += 1
        elif eff == 0:
            if n_open == 0:
                counts['idle'] += 1
            else:
                counts['signal_bound'] += 1
        else:
            counts['filling'] += 1
    total = max(1, len(days))
    return {k: {'count': v, 'pct': 100.0 * v / total} for k, v in counts.items()}


def per_tier_n_vs_fills(days: list[dict], result: dict) -> dict:
    """For each tier, group days by N-effective-offered and report:
        - days in bin
        - mean fills per day in bin
        - effective fill rate (fills / offered)
    A flat curve at high N indicates the tier is signal-surplus (cascade picks
    only some); rising curve at low N indicates the tier is binding.
    """
    # Build fills-per-day per tier from trade_log
    fills_by_day_tier: dict = defaultdict(lambda: defaultdict(int))
    for t in result['trade_log']:
        fills_by_day_tier[t['entry_date']][t['tier']] += 1
    # Also count still-open holdings (their entry_date is in-window)
    for h in result.get('open_holdings', []):
        fills_by_day_tier[h['entry_date']][h['tier']] += 1

    # Bins on N-effective-offered. Calls and puts have different scales.
    call_bins = [(0, 0), (1, 1), (2, 2), (3, 4), (5, 7), (8, 12), (13, 99)]
    put_bins  = [(0, 0), (1, 2), (3, 5), (6, 10), (11, 20), (21, 99)]

    out = {}
    for tier in ALL_TIERS:
        bins = put_bins if tier.startswith('p') else call_bins
        bin_buckets = {f'{lo}-{hi}': {'days': 0, 'sum_fills': 0, 'sum_offered': 0}
                       for lo, hi in bins}
        for d in days:
            d_obj = date.fromisoformat(d['date'])
            n_off = d.get(f'eff_{tier}', 0)
            for lo, hi in bins:
                if lo <= n_off <= hi:
                    label = f'{lo}-{hi}'
                    bin_buckets[label]['days'] += 1
                    bin_buckets[label]['sum_fills'] += fills_by_day_tier.get(d_obj, {}).get(tier, 0)
                    bin_buckets[label]['sum_offered'] += n_off
                    break
        # Convert to means
        out[tier] = {
            label: {
                'days': v['days'],
                'mean_fills_per_day': (v['sum_fills'] / v['days']) if v['days'] else 0.0,
                'mean_offered_per_day': (v['sum_offered'] / v['days']) if v['days'] else 0.0,
                'fill_rate_pct': (100.0 * v['sum_fills'] / v['sum_offered']) if v['sum_offered'] else 0.0,
            }
            for label, v in bin_buckets.items()
        }
    return out


def derive_n_floor(days: list[dict], result: dict) -> dict:
    """Per-tier provisional N/day floor.

    Definition: the floor for tier T is the empirical *median fills/day* the
    cascade actually executes for that tier across trading days where the tier
    was offered at least one effective signal AND the pool had at least one
    free slot. Below this N, the cascade can't fill its tier-T quota; above it,
    the cascade has surplus choice.

    Also reports:
      - p25, p50, p75, p90 of effective-offered-N
      - p25, p50, p75 of fills/day given non-zero offer
      - average fills/day when offered>=1 (= "what we count on")
    """
    fills_by_day_tier: dict = defaultdict(lambda: defaultdict(int))
    for t in result['trade_log']:
        fills_by_day_tier[t['entry_date']][t['tier']] += 1
    for h in result.get('open_holdings', []):
        fills_by_day_tier[h['entry_date']][h['tier']] += 1

    def _quantile(values: list, q: float) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        idx = int(round((len(s) - 1) * q))
        return s[idx]

    out = {}
    for tier in ALL_TIERS:
        offered_values = []
        fills_values = []
        offered_when_pool_open = []
        fills_when_offered = []
        for d in days:
            d_obj = date.fromisoformat(d['date'])
            n_off = d.get(f'eff_{tier}', 0)
            n_fill = fills_by_day_tier.get(d_obj, {}).get(tier, 0)
            offered_values.append(n_off)
            fills_values.append(n_fill)
            if not d['pool_full'] and not d['cash_bound']:
                offered_when_pool_open.append(n_off)
            if n_off > 0:
                fills_when_offered.append(n_fill)

        out[tier] = {
            'offered_p25': _quantile(offered_values, 0.25),
            'offered_p50': _quantile(offered_values, 0.50),
            'offered_p75': _quantile(offered_values, 0.75),
            'offered_p90': _quantile(offered_values, 0.90),
            'offered_mean': (sum(offered_values) / len(offered_values)) if offered_values else 0.0,
            'fills_when_offered_p50': _quantile(fills_when_offered, 0.50),
            'fills_when_offered_mean': (sum(fills_when_offered) / len(fills_when_offered))
                if fills_when_offered else 0.0,
            'days_with_offer': len(fills_when_offered),
            'days_total': len(days),
            'days_pool_open_pct': 100.0 * len(offered_when_pool_open) / max(1, len(days)),
            'fill_rate_when_offered_pct': (
                100.0 * sum(fills_when_offered) / max(1, sum(offered_values))
            ),
        }
    return out


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main():
    av = AlgorithmVersion.get_active_scores_version()
    print(f"Active version: id={av.id}")
    try:
        commit = av.git_commit_hash
    except Exception:
        commit = 'unknown'
    print(f"Commit hash: {commit}")
    print(f"MAX_POSITIONS = {MAX_POSITIONS}")
    print(f"PUT_THRESHOLD = {PUT_THRESHOLD}")
    print(f"TIER_ALLOC: {TIER_ALLOC}")
    print()

    print(f"Running deterministic cascade backtest from {START_DATE} ...")
    result = run_cascade_backtest(
        version_id=av.id,
        min_score=70.0,
        from_date=START_DATE,
        initial=INITIAL_CAPITAL,
        verbose=True,
    )
    if not result:
        print("No qualifying signals found.")
        return

    print()
    print("Reconstructing per-day state ...")
    days, aggregates = reconstruct_per_day_state(result)
    print(f"  {len(days):,} trading days reconstructed")
    print(f"  {len(result['trade_log']):,} closed trades")
    print(f"  {len(result.get('open_holdings', []))} open holdings at end")

    # Aggregates
    print()
    print("Computing aggregates ...")
    sat = saturation_histogram(days)
    pdt = compute_premium_days_per_year_tier(result, days)
    soc = slot_occupancy_share(aggregates)
    cls = day_classification(days)
    nvf = per_tier_n_vs_fills(days, result)
    flr = derive_n_floor(days, result)

    summary = {
        'meta': {
            'algorithm_version_id': av.id,
            'algorithm_commit': commit,
            'start_date': str(START_DATE),
            'end_date': days[-1]['date'] if days else None,
            'n_trading_days': len(days),
            'n_closed_trades': len(result['trade_log']),
            'n_open_holdings': len(result.get('open_holdings', [])),
            'initial_capital': INITIAL_CAPITAL,
            'max_positions': MAX_POSITIONS,
            'tier_alloc': dict(TIER_ALLOC),
            'put_threshold': PUT_THRESHOLD,
        },
        'saturation_histogram': sat,
        'day_classification': cls,
        'cash_deployed_days_per_year_tier': pdt,
        'slot_occupancy_share': soc,
        'per_tier_n_vs_fills': nvf,
        'n_floor_per_tier': flr,
    }

    # Persist
    json_path = os.path.join(OUT_DIR, 'summary.json')
    with open(json_path, 'w') as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"Wrote {json_path}")

    # Per-day CSV (lightweight; one row per day)
    csv_path = os.path.join(OUT_DIR, 'per_day.csv')
    if days:
        keys = list(days[0].keys())
        with open(csv_path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(days)
    print(f"Wrote {csv_path}")

    # Compact human-readable preview
    print()
    print('=' * 78)
    print('SATURATION HISTOGRAM (open-slot count distribution across all days)')
    print('=' * 78)
    for k, v in sat.items():
        print(f"  {k:>6} slots: {v['count']:>5} days ({v['pct']:5.1f}%)")

    print()
    print('=' * 78)
    print('DAY CLASSIFICATION')
    print('=' * 78)
    for k, v in cls.items():
        print(f"  {k:>14}: {v['count']:>5} days ({v['pct']:5.1f}%)")

    print()
    print('=' * 78)
    print('SLOT-OCCUPANCY SHARE BY TIER (across full window)')
    print('=' * 78)
    for t, v in soc.items():
        print(f"  {t:>7}: {v['slot_days']:>6,} slot-days ({v['share_pct']:5.1f}%)")

    print()
    print('=' * 78)
    print('CASH-DEPLOYED-DAYS / YEAR (premium $ × hold trading days)')
    print('=' * 78)
    for year, payload in pdt.items():
        print(f"\n  {year}: total ${payload['total_premium_days']:>14,.0f}")
        for t in ALL_TIERS:
            share = payload['by_tier_share'].get(t, 0.0) * 100
            amt = payload['by_tier'].get(t, 0.0)
            print(f"    {t:>7}: ${amt:>14,.0f}  ({share:5.1f}%)")

    print()
    print('=' * 78)
    print('PER-TIER N FLOOR (provisional)')
    print('=' * 78)
    print(f"  {'tier':>7}  {'off_p50':>8}  {'off_mean':>9}  {'fill@off_p50':>12}  {'fill@off_mean':>13}  {'fill_rate%':>10}")
    for t, v in flr.items():
        print(f"  {t:>7}  {v['offered_p50']:>8.1f}  {v['offered_mean']:>9.2f}  "
              f"{v['fills_when_offered_p50']:>12.1f}  {v['fills_when_offered_mean']:>13.2f}  "
              f"{v['fill_rate_when_offered_pct']:>9.1f}%")
    print()


if __name__ == '__main__':
    main()
