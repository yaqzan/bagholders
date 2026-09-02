"""Integration test for entry_filter.

Pulls all V3 peaks from the last 1095 days, evaluates each one through
entry_filter.evaluate(), and computes the realized EV / p_win on the
matching path-dependent option cell. Compares against the expected EV from
the OOS validation runs (LOW=+21.6%, HIGH=+7.0%).

Also spot-checks 5 LOW and 5 HIGH peaks by re-evaluating them with
features=None to confirm the live feature compute path matches the cached
bulk-feature path.

Run:
    python -m experiments.test_entry_filter
"""
import sys
from collections import defaultdict
from datetime import timedelta

from database.models.core import AlgorithmVersion
from experiments.probe_maturity_acceleration import (
    _bulk_features_for_symbol,
    _payout_cell,
    _sanitize_split_corruption,
)
import entry_filter


# Tolerances vs the validation-run EV. The bulk-feature replay should
# reproduce the validation EV exactly because it uses the same code path.
EXPECTED_LOW_EV  = 20.1   # full 1095d, sanitized (matches "full" row of validate-low)
EXPECTED_HIGH_EV = 6.5    # full 1095d, sanitized (matches "full" row of validate-high)
TOLERANCE_PP = 0.3        # tight: integration test should reproduce exactly


def main():
    from assess_scores import extract_peaks

    days = 1095
    if '--days' in sys.argv:
        days = int(sys.argv[sys.argv.index('--days') + 1])

    v3 = AlgorithmVersion.get_or_none(AlgorithmVersion.id == 3)
    if not v3:
        print('V3 (id=3) not found')
        return 1

    print(f'Pulling V3 peaks for last {days}d ...')
    peaks = extract_peaks(lookback_days=days, version=v3)
    print(f'  {len(peaks)} total peaks')

    by_sym = defaultdict(list)
    for p in peaks:
        if p.overall >= 70:
            by_sym[str(p.symbol_id)].append((p.date, 'high', p.overall))
        elif p.overall <= 25:
            by_sym[str(p.symbol_id)].append((p.date, 'low', p.overall))

    n_high = sum(1 for s in by_sym.values() for x in s if x[1] == 'high')
    n_low  = sum(1 for s in by_sym.values() for x in s if x[1] == 'low')
    print(f'  {n_high} HIGH peaks, {n_low} LOW peaks across {len(by_sym)} symbols')

    rows = []
    syms = sorted(by_sym.keys())
    for i, sym in enumerate(syms, 1):
        if i % 50 == 0:
            print(f'    [{i}/{len(syms)}]')
        feats = _bulk_features_for_symbol(sym, by_sym[sym])
        for d, f in feats.items():
            rows.append(f)
    rows = _sanitize_split_corruption(rows)
    print(f'  After sanitize: {len(rows)} rows')

    # Evaluate every row through entry_filter using cached features
    # (the row dict is the same shape entry_filter expects)
    long_hits = []
    short_hits = []
    sample_long_for_live = []
    sample_short_for_live = []

    for r in rows:
        symbol = r['symbol']
        date = r['date']
        score = r['score']
        feats = {
            'ext': r.get('ext'),
            'macdh': r.get('macdh'),
            'vol_ratio': r.get('vol_ratio'),
            'ret_20d_prior': r.get('ret_20d_prior'),
        }
        tradeable, reason, side = entry_filter.evaluate(symbol, date, score, features=feats)
        if not tradeable:
            continue
        if side == 'long':
            long_hits.append(r)
            if len(sample_long_for_live) < 5:
                sample_long_for_live.append((symbol, date, score, r))
        elif side == 'short':
            short_hits.append(r)
            if len(sample_short_for_live) < 5:
                sample_short_for_live.append((symbol, date, score, r))

    # ── Realized EV via the matching cell ──
    print()
    print('=' * 92)
    print('INTEGRATION TEST: entry_filter realized EV vs validation EV')
    print('=' * 92)

    print()
    print(f'  LONG hits (LOW gate):  {len(long_hits)}')
    cell_low = _payout_cell(long_hits, 2.0, 5.0, 30, 'low')
    if cell_low is None:
        print('    no path data')
        return 1
    print(f'    cell K=2.0/M=5.0/W=30')
    print(f'    N={cell_low["n"]}  p_win={cell_low["p_win"]*100:.1f}%  '
          f'win_pnl={cell_low["mean_win"]:+.1f}%  stop_pnl={cell_low["mean_stop"]:+.1f}%  '
          f'EV={cell_low["ev"]:+.1f}%')
    print(f'    expected (validate-low full): EV~{EXPECTED_LOW_EV:+.1f}%')
    delta_low = abs(cell_low['ev'] - EXPECTED_LOW_EV)
    if delta_low <= TOLERANCE_PP:
        print(f'    [PASS] within {TOLERANCE_PP}pp tolerance (delta={delta_low:.2f}pp)')
    else:
        print(f'    [FAIL] outside tolerance (delta={delta_low:.2f}pp > {TOLERANCE_PP}pp)')

    print()
    print(f'  SHORT hits (HIGH gate): {len(short_hits)}')
    cell_high = _payout_cell(short_hits, 1.0, 2.0, 42, 'high')
    if cell_high is None:
        print('    no path data')
        return 1
    print(f'    cell K=1.0/M=2.0/W=42')
    print(f'    N={cell_high["n"]}  p_win={cell_high["p_win"]*100:.1f}%  '
          f'win_pnl={cell_high["mean_win"]:+.1f}%  stop_pnl={cell_high["mean_stop"]:+.1f}%  '
          f'EV={cell_high["ev"]:+.1f}%')
    print(f'    expected (validate-high full): EV~{EXPECTED_HIGH_EV:+.1f}%')
    delta_high = abs(cell_high['ev'] - EXPECTED_HIGH_EV)
    if delta_high <= TOLERANCE_PP:
        print(f'    [PASS] within {TOLERANCE_PP}pp tolerance (delta={delta_high:.2f}pp)')
    else:
        print(f'    [FAIL] outside tolerance (delta={delta_high:.2f}pp > {TOLERANCE_PP}pp)')

    # ── Spot-check live feature compute matches cached features ──
    print()
    print('=' * 92)
    print('SPOT CHECK: live feature compute (features=None) matches cached')
    print('=' * 92)
    mismatches = 0
    checks = 0
    for label, sample in [('LONG', sample_long_for_live), ('SHORT', sample_short_for_live)]:
        for symbol, date, score, cached_row in sample:
            cached_feats = {
                'ext': cached_row.get('ext'),
                'macdh': cached_row.get('macdh'),
                'vol_ratio': cached_row.get('vol_ratio'),
                'ret_20d_prior': cached_row.get('ret_20d_prior'),
            }
            t_cached, _, _ = entry_filter.evaluate(symbol, date, score, features=cached_feats)
            t_live, _, _   = entry_filter.evaluate(symbol, date, score, features=None)
            checks += 1
            tag = '[OK]' if t_cached == t_live else '[MISMATCH]'
            if t_cached != t_live:
                mismatches += 1
            print(f'  {tag} {label} {symbol} {date} score={score}  '
                  f'cached={t_cached}  live={t_live}')
    print()
    if mismatches == 0:
        print(f'  [PASS] all {checks} live/cached spot checks agree')
    else:
        print(f'  [FAIL] {mismatches}/{checks} mismatches')

    # Final
    print()
    if delta_low <= TOLERANCE_PP and delta_high <= TOLERANCE_PP and mismatches == 0:
        print('OVERALL: PASS')
        return 0
    print('OVERALL: FAIL')
    return 1


if __name__ == '__main__':
    sys.exit(main())
