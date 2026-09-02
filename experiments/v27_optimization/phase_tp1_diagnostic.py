"""
Phase TP1 — per-trade TP-rate discriminator audit (v27 production)

Goal: identify which signal (year × breadth quartile × regime label × VIX bucket)
discriminates the thin-margin TP cells (2022 calls, 2023 puts) from healthier
cohorts. This audit is DETERMINISTIC — outcomes don't depend on MC randomization
because per-trade TP/SL is a pure function of (price walk, signal date, barrier).
Only same-bar TP+SL collision needs random resolution; we report 'both' as a
separate category.

Output drives Phase TP2 design:
  - If 2022 calls cluster in low-breadth quartile w/ <55% TP -> breadth-gated TP variant
  - If 2023 puts cluster in stressed regime w/ <43% TP -> put breadth-adaptive TP variant
  - Else thin margin is population-wide -> only score-stage changes can shift it

Run: python experiments/v27_optimization/phase_tp1_diagnostic.py
"""
import os
import sys
import bisect
from collections import defaultdict
from datetime import date

# Ensure we use single-process mode for deterministic audit
os.environ['MC_NO_MP'] = '1'

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc
from database.models.core import AlgorithmVersion, MarketRegime, EarningsDate, Stock
from datetime import timedelta


def regime_label_from_mult(m):
    """Derive label from regime_multiplier (bands per scoring-algorithm.md)."""
    if m is None:
        return 'unknown'
    if m < 0.78:
        return 'STRESS'
    if m < 0.88:
        return 'CAUTION'
    if m < 1.00:
        return 'NEUTRAL-'
    if m < 1.05:
        return 'HEALTHY'
    return 'BULL'


def load_regime_label_map(d_start, d_end):
    """Return {date: regime_label_string} derived from regime_multiplier."""
    rows = list(
        MarketRegime.select(MarketRegime.date, MarketRegime.regime_multiplier)
        .where(
            MarketRegime.date >= d_start - timedelta(days=60),
            MarketRegime.date <= d_end,
            MarketRegime.regime_multiplier.is_null(False),
        )
        .order_by(MarketRegime.date)
        .tuples()
    )
    return {d: regime_label_from_mult(float(m)) for d, m in rows}


def load_vix_map(d_start, d_end):
    """Return {date: vix_close}."""
    rows = list(
        MarketRegime.select(MarketRegime.date, MarketRegime.vix_close)
        .where(
            MarketRegime.date >= d_start - timedelta(days=60),
            MarketRegime.date <= d_end,
            MarketRegime.vix_close.is_null(False),
        )
        .order_by(MarketRegime.date)
        .tuples()
    )
    return {d: float(v) for d, v in rows}


def lookup_on_or_before(sorted_dates, dmap, d, default=None):
    idx = bisect.bisect_right(sorted_dates, d) - 1
    if idx >= 0:
        return dmap[sorted_dates[idx]]
    return default


def breadth_quartile(b):
    if b is None:
        return 'q?'
    if b <= 35:
        return 'q1_low'  # bottom quartile (broad weakness)
    if b <= 50:
        return 'q2_midlow'
    if b <= 65:
        return 'q3_midhi'
    return 'q4_high'


def load_earnings_lookup(d_start, d_end, sym_ids):
    """Return {(sym_id, signal_date): earn_proximity_tag} for tagging signals.

    Tag values:
      'pre1'   : earnings within [D+1, D+1] (next trading day = earnings day)
      'pre3'   : earnings within [D+1, D+3]
      'pre5'   : earnings within [D+4, D+5]
      'post3'  : earnings within [D-3, D-1] (just announced)
      'in_hold': earnings within [D+1, D+15] (within hold window, but past pre5)
      'none'   : no earnings within ±15 days
    """
    # symbol_id IS the ticker string (Stock PK = symbol)
    rows = list(
        EarningsDate.select(EarningsDate.symbol, EarningsDate.date)
        .where(
            EarningsDate.symbol.in_(list(sym_ids)),
            EarningsDate.date >= d_start - timedelta(days=20),
            EarningsDate.date <= d_end + timedelta(days=20),
        )
        .tuples()
    )
    by_sym = defaultdict(list)
    for ticker, ed in rows:
        by_sym[ticker].append(ed)
    for sid in by_sym:
        by_sym[sid].sort()
    return by_sym


def earn_tag(by_sym, sym_id, signal_date):
    eds = by_sym.get(sym_id, [])
    if not eds:
        return 'none'
    # Find nearest earnings to signal_date
    best_delta = None
    for ed in eds:
        delta = (ed - signal_date).days
        if best_delta is None or abs(delta) < abs(best_delta):
            best_delta = delta
    if best_delta is None:
        return 'none'
    # Calendar-day delta. signal_date close = entry candle.
    if best_delta == 0 or best_delta == 1:
        return 'pre1'      # earnings tomorrow or signal-day-after-close
    if 2 <= best_delta <= 3:
        return 'pre3'
    if 4 <= best_delta <= 7:
        return 'pre7'
    if 8 <= best_delta <= 21:
        return 'in_hold'   # within typical hold window (15 trd days ~21 cal)
    if -3 <= best_delta <= -1:
        return 'post3'
    if -7 <= best_delta <= -4:
        return 'post7'
    return 'none'


def vix_bucket(v):
    if v is None:
        return 'v?'
    if v < 15:
        return 'v_calm'
    if v < 22:
        return 'v_normal'
    if v < 30:
        return 'v_elevated'
    return 'v_panic'


def audit_window(label, d_start, d_end, version):
    print(f"\n{'='*80}")
    print(f"AUDIT WINDOW: {label}  ({d_start} → {d_end})")
    print('='*80)

    call_sigs = mc.load_signals(version, d_start, d_end)
    put_sigs = mc.load_put_signals(version, d_start, d_end)
    print(f"Loaded {len(call_sigs)} call signals, {len(put_sigs)} put signals")

    sym_ids_all = list(set(s.symbol_id for s in call_sigs) | set(s.symbol_id for s in put_sigs))
    ph = mc.load_price_history(sym_ids_all, d_start, d_end)
    breadth_dates, breadth_map = mc.load_breadth_map(d_start, d_end)
    regime_labels = load_regime_label_map(d_start, d_end)
    regime_label_dates = sorted(regime_labels.keys())
    vix_map = load_vix_map(d_start, d_end)
    vix_dates = sorted(vix_map.keys())

    sym_ids_present = set(s.symbol_id for s in call_sigs) | set(s.symbol_id for s in put_sigs)
    earn_lookup = load_earnings_lookup(d_start, d_end, sym_ids_present)
    print(f"Loaded earnings dates for {len(earn_lookup)} symbols")

    # Outcome computation (deterministic; reuses production breadth_adapt for stress flag)
    call_outcomes = mc.precompute_outcomes(call_sigs, ph, breadth_dates, breadth_map)
    put_outcomes = mc.precompute_put_outcomes(put_sigs, ph, breadth_dates, breadth_map)

    print(f"Computed: {len(call_outcomes)} call outcomes, {len(put_outcomes)} put outcomes")

    # Tag each outcome with its discriminator coords
    def tag_signal(sig, side):
        b = mc.breadth_on_or_before(breadth_dates, breadth_map, sig.date)
        regime = lookup_on_or_before(regime_label_dates, regime_labels, sig.date, 'unknown')
        vix = lookup_on_or_before(vix_dates, vix_map, sig.date, None)
        return {
            'year': sig.date.year,
            'side': side,
            'breadth': b,
            'breadth_q': breadth_quartile(b),
            'regime': regime,
            'vix': vix,
            'vix_b': vix_bucket(vix),
            'earn': earn_tag(earn_lookup, sig.symbol_id, sig.date),
        }

    rows = []
    for sig in call_sigs:
        out = call_outcomes.get((sig.symbol_id, sig.date))
        if out is None:
            continue
        tag = tag_signal(sig, 'call')
        tag['kind'] = out['kind']
        tag['exit_bar'] = out['exit_bar']
        rows.append(tag)
    for sig in put_sigs:
        out = put_outcomes.get((sig.symbol_id, sig.date))
        if out is None:
            continue
        tag = tag_signal(sig, 'put')
        tag['kind'] = out['kind']
        tag['exit_bar'] = out['exit_bar']
        rows.append(tag)

    # Aggregate by (year, side) baseline
    print('\n--- Per-year baseline (TP rate, "both" resolved Realistic = 50/50) ---')
    print(f"{'Year':<6} {'Side':<5} | {'N':>6} {'tp':>6} {'sl':>6} {'hard':>6} {'both':>6} | {'TP%(R)':>7} {'BE':>5} {'Margin':>7}")
    print('-' * 88)
    by_year_side = defaultdict(lambda: defaultdict(int))
    for r in rows:
        by_year_side[(r['year'], r['side'])][r['kind']] += 1
    BE = {'call': 56.3, 'put': 43.5}
    for (yr, side) in sorted(by_year_side.keys()):
        c = by_year_side[(yr, side)]
        n = sum(c.values())
        if n == 0:
            continue
        tp_realistic = (c.get('tp', 0) + 0.5 * c.get('both', 0)) / n * 100
        margin = tp_realistic - BE[side]
        flag = ' <- THIN' if margin < 3.0 else ''
        print(f"{yr:<6} {side:<5} | {n:>6} {c.get('tp',0):>6} {c.get('sl',0):>6} {c.get('hard',0):>6} {c.get('both',0):>6} | {tp_realistic:>6.1f}% {BE[side]:>5.1f} {margin:>+6.2f}pp{flag}")

    # Global earnings-window TP audit (every year, both sides) — directly informs
    # the "hold-through-earnings" hypothesis. If signals firing within N days of
    # earnings have materially different TP rates than non-earnings signals, a
    # close-before-earnings or hold-through rule could be EV-positive.
    print('\n--- Global earnings-window TP rate (5y aggregate by side × earn proximity) ---')
    print(f"{'Side':<5} {'Earn':<10} | {'N':>6} {'TP':>5} {'SL':>5} {'Hard':>5} {'Both':>5} | {'TP%(R)':>7} {'BE':>5} {'Margin':>7}")
    print('-' * 88)
    by_side_earn = defaultdict(lambda: defaultdict(int))
    for r in rows:
        by_side_earn[(r['side'], r['earn'])][r['kind']] += 1
    for side in ['call', 'put']:
        # Order: pre1, pre3, pre7, in_hold, post3, post7, none
        for et in ['pre1', 'pre3', 'pre7', 'in_hold', 'post3', 'post7', 'none']:
            c = by_side_earn[(side, et)]
            n = sum(c.values())
            if n < 30:
                continue
            tp_realistic = (c.get('tp', 0) + 0.5 * c.get('both', 0)) / n * 100
            margin = tp_realistic - BE[side]
            flag = ' <- THIN' if margin < 3.0 else (' <- STRONG' if margin > 8.0 else '')
            print(f"{side:<5} {et:<10} | {n:>6} {c.get('tp',0):>5} {c.get('sl',0):>5} {c.get('hard',0):>5} {c.get('both',0):>5} | {tp_realistic:>6.1f}% {BE[side]:>5.1f} {margin:>+6.2f}pp{flag}")
        print()

    # Discriminator audit: for THIN-MARGIN year-side cells, partition by breadth/regime/vix
    print('\n--- Discriminator audit: THIN-MARGIN cells (margin < 3pp) ---')
    thin_cells = []
    for (yr, side), c in by_year_side.items():
        n = sum(c.values())
        if n < 50:
            continue
        tp_realistic = (c.get('tp', 0) + 0.5 * c.get('both', 0)) / n * 100
        if tp_realistic - BE[side] < 3.0:
            thin_cells.append((yr, side))
    if not thin_cells:
        print("(no thin-margin cells found)")
        return rows

    for (yr, side) in thin_cells:
        cell_rows = [r for r in rows if r['year'] == yr and r['side'] == side]
        n_cell = len(cell_rows)
        be = BE[side]
        print(f"\n  ## {yr} {side.upper()} (N={n_cell}, BE={be:.1f}%)")

        # by breadth quartile
        print(f"    {'Bucket':<14} | {'N':>5} {'TP':>4} {'Hard':>5} {'Both':>5} | {'TP%(R)':>7} {'Δ vs cell':>10}")
        for axis_name, key_fn in [
            ('Breadth-Q', lambda r: r['breadth_q']),
            ('Regime',    lambda r: r['regime']),
            ('VIX',       lambda r: r['vix_b']),
            ('Earnings',  lambda r: r['earn']),
        ]:
            print(f"  -- by {axis_name} --")
            sub = defaultdict(lambda: defaultdict(int))
            for r in cell_rows:
                sub[key_fn(r)][r['kind']] += 1
            cell_tp = (sum(c2.get('tp',0)+0.5*c2.get('both',0) for c2 in sub.values()) /
                       max(1, sum(sum(c2.values()) for c2 in sub.values())) * 100)
            for k in sorted(sub.keys()):
                c2 = sub[k]
                n = sum(c2.values())
                if n == 0:
                    continue
                tp_pct = (c2.get('tp', 0) + 0.5 * c2.get('both', 0)) / n * 100
                delta = tp_pct - cell_tp
                flag = ' <-' if abs(delta) > 4 and n >= 30 else ''
                print(f"    {k:<14} | {n:>5} {c2.get('tp',0):>4} {c2.get('hard',0):>5} {c2.get('both',0):>5} | {tp_pct:>6.1f}% {delta:>+8.2f}pp{flag}")

    return rows


def main():
    version = AlgorithmVersion.get_active_scores_version()
    print(f"Using algorithm version: id={version.id} commit={getattr(version, 'git_hash', '?')}")
    # Audit 5y window (covers 2021-2026); aggregation by year happens inside.
    audit_window('5y', date(2021, 1, 1), date(2026, 4, 28), version)


if __name__ == '__main__':
    main()
