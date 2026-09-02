"""
Trend-duration / trend-score impact analysis.

Two questions:
  1. How long do TREND-component runs last? Medians + means for:
       - Uptrend run = consecutive trading days where TREND >= 75
       - Downtrend run = consecutive trading days where TREND <= 25
  2. Does the TREND value at signal time (or day-number within the run)
     predict accuracy of the overall signal?

Uses the ACTIVE algorithm version (get_active_scores_version). No DB writes.

Usage:
    python -m experiments.trend_duration_analysis           # 5y
    python -m experiments.trend_duration_analysis 3y
    python -m experiments.trend_duration_analysis 1y
"""
from __future__ import annotations
import sys
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, median

from database.models.core import Score, AlgorithmVersion
from database.models.technical import PriceHistory
from assess_scores import (
    _peak_to_result, _peak_side, _swing_walk, _realized_vol_pct,
    PERIODS, RANDOM_WIN_RATE_CALL, RANDOM_WIN_RATE_PUT,
)

UP_THRESH = 75      # TREND >= this starts an uptrend run
DOWN_THRESH = 25    # TREND <= this starts a downtrend run
CALL_OVERALL = 75   # qualifying overall score for call signal
PUT_OVERALL = 25    # qualifying overall score for put signal


def _percentiles(values, ps=(10, 25, 50, 75, 90, 95, 99)):
    if not values:
        return {}
    s = sorted(values)
    n = len(s)
    out = {}
    for p in ps:
        idx = min(n - 1, max(0, int(round((p / 100.0) * (n - 1)))))
        out[p] = s[idx]
    return out


def parse_lookback():
    days = 1825
    if len(sys.argv) > 1:
        tok = sys.argv[1].lower().strip()
        if tok.endswith('y'):
            days = int(float(tok[:-1]) * 365)
        elif tok.endswith('d'):
            days = int(tok[:-1])
        else:
            days = int(tok)
    return days


def load_scores(version, cutoff):
    """Return dict[symbol_id] -> list of (date, overall, trend) sorted ascending."""
    print(f"Loading scores from {cutoff} onward for version {version.git_commit} (v{version.id})...")
    q = (Score
         .select(Score.symbol, Score.date, Score.overall, Score.trend)
         .where(Score.date >= cutoff,
                Score.version == version,
                Score.overall.is_null(False),
                Score.trend.is_null(False))
         .order_by(Score.symbol, Score.date)
         .tuples())
    by_sym = defaultdict(list)
    for sym_id, d, overall, trend in q:
        by_sym[sym_id].append((d, int(overall), int(trend)))
    total = sum(len(v) for v in by_sym.values())
    print(f"  loaded {total:,} score rows across {len(by_sym):,} symbols")
    return by_sym


def find_trend_runs(rows_by_sym):
    """Walk each symbol's score list, identify runs where TREND >= UP_THRESH
    (uptrends) or TREND <= DOWN_THRESH (downtrends).

    A run is counted as *completed* when the TREND crosses back out. Runs that
    extend to the end of the data are marked `completed=False` and excluded
    from the median/mean (right-censored).

    Returns two lists of dicts: uptrend_runs, downtrend_runs, each with:
        symbol_id, start_date, end_date, duration_days (trading-day count),
        max_trend (for uptrends) or min_trend (for downtrends), completed.
    """
    uptrends, downtrends = [], []
    for sym_id, rows in rows_by_sym.items():
        cur_up = None
        cur_dn = None
        for i, (d, _overall, trend) in enumerate(rows):
            # Uptrend tracking
            if trend >= UP_THRESH:
                if cur_up is None:
                    cur_up = {'start': d, 'end': d, 'n': 1, 'max': trend}
                else:
                    cur_up['end'] = d
                    cur_up['n'] += 1
                    if trend > cur_up['max']:
                        cur_up['max'] = trend
            else:
                if cur_up is not None:
                    uptrends.append({
                        'symbol_id': sym_id,
                        'start_date': cur_up['start'],
                        'end_date': cur_up['end'],
                        'duration_days': cur_up['n'],
                        'max_trend': cur_up['max'],
                        'completed': True,
                    })
                    cur_up = None
            # Downtrend tracking
            if trend <= DOWN_THRESH:
                if cur_dn is None:
                    cur_dn = {'start': d, 'end': d, 'n': 1, 'min': trend}
                else:
                    cur_dn['end'] = d
                    cur_dn['n'] += 1
                    if trend < cur_dn['min']:
                        cur_dn['min'] = trend
            else:
                if cur_dn is not None:
                    downtrends.append({
                        'symbol_id': sym_id,
                        'start_date': cur_dn['start'],
                        'end_date': cur_dn['end'],
                        'duration_days': cur_dn['n'],
                        'min_trend': cur_dn['min'],
                        'completed': True,
                    })
                    cur_dn = None
        # End-of-series: mark open runs as right-censored
        if cur_up is not None:
            uptrends.append({
                'symbol_id': sym_id, 'start_date': cur_up['start'], 'end_date': cur_up['end'],
                'duration_days': cur_up['n'], 'max_trend': cur_up['max'], 'completed': False,
            })
        if cur_dn is not None:
            downtrends.append({
                'symbol_id': sym_id, 'start_date': cur_dn['start'], 'end_date': cur_dn['end'],
                'duration_days': cur_dn['n'], 'min_trend': cur_dn['min'], 'completed': False,
            })
    return uptrends, downtrends


def print_duration_stats(label, runs):
    completed = [r for r in runs if r['completed']]
    open_ = [r for r in runs if not r['completed']]
    durations = [r['duration_days'] for r in completed]
    print(f"\n{label}  (n={len(completed):,} completed, {len(open_):,} right-censored at load time)")
    if not durations:
        print("  (no completed runs)")
        return
    pct = _percentiles(durations)
    print(f"  mean   = {mean(durations):.2f} trading days")
    print(f"  median = {median(durations):.1f}")
    print(f"  p10 / p25 / p50 / p75 / p90 / p95 / p99 = "
          f"{pct[10]} / {pct[25]} / {pct[50]} / {pct[75]} / {pct[90]} / {pct[95]} / {pct[99]}")
    short = sum(1 for d in durations if d <= 3)
    mid = sum(1 for d in durations if 4 <= d <= 10)
    long_ = sum(1 for d in durations if 11 <= d <= 30)
    extra = sum(1 for d in durations if d > 30)
    tot = len(durations)
    print(f"  duration buckets: ≤3d={short:,} ({100*short/tot:.1f}%)  "
          f"4-10d={mid:,} ({100*mid/tot:.1f}%)  "
          f"11-30d={long_:,} ({100*long_/tot:.1f}%)  "
          f">30d={extra:,} ({100*extra/tot:.1f}%)")


def extract_signals(rows_by_sym):
    """Return list of signals (call or put) that qualify overall, annotated
    with the day-number within their current trend run and the trend value.

    Day-number within run: for calls (overall >= CALL_OVERALL) we look at the
    active uptrend run (TREND >= UP_THRESH). day=1 means the first day the
    trend entered the run. If no active uptrend run, day=0 (signal fired
    without structural uptrend — still recorded). Same logic for puts with
    downtrend runs.
    """
    signals = []
    for sym_id, rows in rows_by_sym.items():
        up_start_idx = None  # index in rows where current uptrend run started
        dn_start_idx = None
        for i, (d, overall, trend) in enumerate(rows):
            # update run tracking
            if trend >= UP_THRESH:
                if up_start_idx is None:
                    up_start_idx = i
            else:
                up_start_idx = None
            if trend <= DOWN_THRESH:
                if dn_start_idx is None:
                    dn_start_idx = i
            else:
                dn_start_idx = None

            if overall >= CALL_OVERALL:
                day_in_run = (i - up_start_idx + 1) if up_start_idx is not None else 0
                signals.append({
                    'symbol_id': sym_id, 'date': d, 'overall': overall,
                    'trend': trend, 'side': 'call',
                    'day_in_run': day_in_run,
                })
            elif overall <= PUT_OVERALL:
                day_in_run = (i - dn_start_idx + 1) if dn_start_idx is not None else 0
                signals.append({
                    'symbol_id': sym_id, 'date': d, 'overall': overall,
                    'trend': trend, 'side': 'put',
                    'day_in_run': day_in_run,
                })
    return signals


def run_swing_walks(signals):
    """Bulk-load PriceHistory per symbol, compute swing outcome per signal.
    Returns same signals annotated with 'win_15d', 'win_30d', 'win_60d' (or None)
    and 'swing_ok' = True/False."""
    sym_ids = sorted({s['symbol_id'] for s in signals})
    print(f"\nLoading PriceHistory for {len(sym_ids):,} symbols...")
    price_cache = {}
    for sym in sym_ids:
        rows = list(
            PriceHistory.select(PriceHistory.date, PriceHistory.close,
                                PriceHistory.high, PriceHistory.low)
            .where(PriceHistory.symbol == sym)
            .order_by(PriceHistory.date)
            .tuples()
        )
        price_cache[sym] = [(d, float(c), float(h), float(l)) for d, c, h, l in rows]

    out = []
    for sig in signals:
        rows = price_cache.get(sig['symbol_id'], [])
        # Build a lightweight peak-like object expected by _peak_to_result
        class _P: pass
        p = _P()
        p.symbol_id = sig['symbol_id']
        p.date = sig['date']
        p.overall = sig['overall']
        res = _peak_to_result(p, rows)
        if res is None:
            continue
        swing = res['swing']
        new = dict(sig)
        for label in ('7d', '15d', '30d', '60d', '90d'):
            s = swing.get(label)
            if s is None:
                new[f'win_{label}'] = None
            else:
                new[f'win_{label}'] = 1 if s['result'] == 'win' else 0
        out.append(new)
    return out


def _win_rate(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return None, 0
    return 100 * sum(vals) / len(vals), len(vals)


def _fmt_rate(rows, key):
    wr, n = _win_rate(rows, key)
    if wr is None:
        return f"  --  (n=0)"
    return f"{wr:5.1f}%  (n={n:,})"


def print_wr_by_trend_bucket(signals, side_filter, label):
    rows = [s for s in signals if s['side'] == side_filter]
    print(f"\n{label} — WR by TREND component bucket  (signal pool n={len(rows):,})")
    print(f"  Random-walk baseline: calls {RANDOM_WIN_RATE_CALL:.1f}%, puts {RANDOM_WIN_RATE_PUT:.1f}%")
    # For calls (side=call, overall>=75): trend bucket from 0..100
    # For puts (side=put, overall<=25): same, but puts are expected to have LOW trend
    if side_filter == 'call':
        buckets = [('TREND  0-24', 0, 24), ('TREND 25-49', 25, 49),
                   ('TREND 50-74', 50, 74), ('TREND 75-89', 75, 89),
                   ('TREND 90-100', 90, 100)]
    else:
        buckets = [('TREND  0-10', 0, 10), ('TREND 11-24', 11, 24),
                   ('TREND 25-49', 25, 49), ('TREND 50-74', 50, 74),
                   ('TREND 75-100', 75, 100)]

    print(f"  {'bucket':<15} {'WR15':>14} {'WR30':>14} {'WR60':>14}")
    for bl, lo, hi in buckets:
        sub = [r for r in rows if lo <= r['trend'] <= hi]
        wr15 = _fmt_rate(sub, 'win_15d')
        wr30 = _fmt_rate(sub, 'win_30d')
        wr60 = _fmt_rate(sub, 'win_60d')
        print(f"  {bl:<15} {wr15:>14} {wr30:>14} {wr60:>14}")


def print_wr_by_day_in_run(signals, side_filter, label):
    rows = [s for s in signals if s['side'] == side_filter]
    print(f"\n{label} — WR by day-number within active TREND run  (n={len(rows):,})")
    buckets = [
        ('no active run', 0, 0),
        ('day 1',         1, 1),
        ('day 2',         2, 2),
        ('days 3-5',      3, 5),
        ('days 6-10',     6, 10),
        ('days 11-20',    11, 20),
        ('days 21-40',    21, 40),
        ('days 41-80',    41, 80),
        ('days 81+',      81, 10_000),
    ]
    print(f"  {'bucket':<16} {'WR15':>14} {'WR30':>14} {'WR60':>14}")
    for bl, lo, hi in buckets:
        sub = [r for r in rows if lo <= r['day_in_run'] <= hi]
        wr15 = _fmt_rate(sub, 'win_15d')
        wr30 = _fmt_rate(sub, 'win_30d')
        wr60 = _fmt_rate(sub, 'win_60d')
        print(f"  {bl:<16} {wr15:>14} {wr30:>14} {wr60:>14}")


def print_overall_bucket_interaction(signals, side_filter, label):
    """Cross-tab: for each overall-score bucket (75+/80+/85+/90+ or <25/<15/<5),
    slice by day-in-run short vs long to see if structural trend age matters
    for HIGH-conviction signals too."""
    rows = [s for s in signals if s['side'] == side_filter]
    print(f"\n{label} — overall bucket × trend-run age interaction  (n={len(rows):,})")
    if side_filter == 'call':
        obuckets = [('95+', 95, 100), ('90+', 90, 100), ('85+', 85, 100),
                    ('80+', 80, 100), ('75+', 75, 100)]
    else:
        obuckets = [('<=5', 0, 5), ('<=10', 0, 10), ('<=15', 0, 15),
                    ('<=20', 0, 20), ('<=25', 0, 25)]
    age_buckets = [('early (1-5d)', 1, 5), ('mid (6-20d)', 6, 20), ('late (21+d)', 21, 10_000)]
    print(f"  {'overall':<8} {'age':<14} {'WR15':>14} {'WR30':>14}")
    for ol, lo, hi in obuckets:
        for al, alo, ahi in age_buckets:
            sub = [r for r in rows if lo <= r['overall'] <= hi and alo <= r['day_in_run'] <= ahi]
            wr15 = _fmt_rate(sub, 'win_15d')
            wr30 = _fmt_rate(sub, 'win_30d')
            print(f"  {ol:<8} {al:<14} {wr15:>14} {wr30:>14}")


def main():
    days = parse_lookback()
    cutoff = date.today() - timedelta(days=days)
    version = AlgorithmVersion.get_active_scores_version()
    if version is None:
        print("No active algorithm version."); return

    rows_by_sym = load_scores(version, cutoff)

    # ---- Q1: trend durations -------------------------------------------------
    uptrends, downtrends = find_trend_runs(rows_by_sym)
    print("\n" + "=" * 80)
    print(f"  TREND RUN DURATIONS  (active version v{version.id} = {version.git_commit})")
    print(f"  Window: last {days} calendar days ({cutoff} to today)")
    print("=" * 80)
    print_duration_stats(f"UPTREND  runs (TREND >= {UP_THRESH})",   uptrends)
    print_duration_stats(f"DOWNTREND runs (TREND <= {DOWN_THRESH})", downtrends)

    # ---- Q2: signal WR by trend context -------------------------------------
    signals = extract_signals(rows_by_sym)
    call_n = sum(1 for s in signals if s['side'] == 'call')
    put_n  = sum(1 for s in signals if s['side'] == 'put')
    print(f"\nExtracted {len(signals):,} signals  (calls: {call_n:,},  puts: {put_n:,})")

    sig_with_swing = run_swing_walks(signals)
    print(f"  {len(sig_with_swing):,} signals have valid swing outcomes")

    print("\n" + "=" * 80)
    print("  ACCURACY BY TREND VALUE AT SIGNAL TIME")
    print("=" * 80)
    print_wr_by_trend_bucket(sig_with_swing, 'call', "CALLS  (overall >= 75)")
    print_wr_by_trend_bucket(sig_with_swing, 'put',  "PUTS   (overall <= 25)")

    print("\n" + "=" * 80)
    print("  ACCURACY BY TREND-RUN AGE (hypothesis: older trends break down)")
    print("=" * 80)
    print_wr_by_day_in_run(sig_with_swing, 'call', "CALLS  — days into active uptrend (TREND >= 75) at entry")
    print_wr_by_day_in_run(sig_with_swing, 'put',  "PUTS   — days into active downtrend (TREND <= 25) at entry")

    print("\n" + "=" * 80)
    print("  OVERALL-BUCKET × TREND-AGE INTERACTION")
    print("=" * 80)
    print_overall_bucket_interaction(sig_with_swing, 'call', "CALLS")
    print_overall_bucket_interaction(sig_with_swing, 'put',  "PUTS")


if __name__ == '__main__':
    main()
