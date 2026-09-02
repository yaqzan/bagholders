"""
Phase TP3A — log-curve earnings meta-score boost (cheap per-trade screen)

Score-stage modification: amplify (overall - 50) by a log-curve multiplier
when signal date is within WINDOW trading days of an upcoming earnings.

    proximity = log(WINDOW + 1 - d) / log(WINDOW + 1)
    mult      = 1 + max_boost * proximity        (= 1 at d=WINDOW, 1+max at d=0)
    new       = clamp(round(50 + (overall - 50) * mult), 0, 100)

Bayesian-style grid sweep on (WINDOW, MAX_BOOST). Asymmetric put/call ratio
optional. Per-trade screen via FastVariantRunner — reuses production scores
(post-regime, post-dampener) and applies the boost as the final transform,
then re-aggregates barrier outcomes per bucket.

Run: python experiments/v27_optimization/phase_tp3a_earnings_boost.py

Output: WR15 / WR30 / N / Cap30 per bucket, vs production baseline.
"""
from __future__ import annotations
import io, sys, math, time, json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import polars as pl

from experiments.fast_variant_runner import FastVariantRunner, BUY_THRESHOLDS, SELL_THRESHOLDS, PERIODS
from database.models.core import EarningsDate, AlgorithmVersion


def build_earnings_lookup(symbols, lookback_days):
    """Return polars DF with columns (symbol, signal_date, days_to_earnings).

    days_to_earnings is the smallest non-negative calendar-day delta to next
    earnings within (signal_date, signal_date + 30 calendar days]. NULL if no
    earnings in window. Computed for every signal date the runner has scores
    for; we'll join later.
    """
    cutoff = (date.today() - timedelta(days=lookback_days + 30)).isoformat()
    rows = list(
        EarningsDate.select(EarningsDate.symbol, EarningsDate.date)
        .where(
            EarningsDate.symbol.in_(list(symbols)),
            EarningsDate.date >= cutoff,
        )
        .tuples()
    )
    by_sym = defaultdict(list)
    for sym, d in rows:
        by_sym[sym].append(d)
    for sym in by_sym:
        by_sym[sym].sort()
    return by_sym


def compute_days_to_earnings(scores_df, by_sym, max_window=10):
    """Add `days_to_ern` column (NULL if no upcoming earnings within max_window
    calendar days from signal date).
    """
    sym_col = scores_df['symbol'].to_list()
    date_col = scores_df['date'].to_list()
    days_to_ern = []
    for sym, d_str in zip(sym_col, date_col):
        # date column is iso string (per the runner cache build)
        if isinstance(d_str, str):
            try:
                d = date.fromisoformat(d_str)
            except ValueError:
                days_to_ern.append(None)
                continue
        else:
            d = d_str
        eds = by_sym.get(sym, [])
        # Find smallest non-negative delta in window
        # Linear scan ok since each stock has ~20 earnings over 5y
        best = None
        for ed in eds:
            delta = (ed - d).days
            if 0 <= delta <= max_window:
                if best is None or delta < best:
                    best = delta
        days_to_ern.append(best)
    return scores_df.with_columns(pl.Series('days_to_ern', days_to_ern, dtype=pl.Int64))


def evaluate_earnings_boost(runner, window, max_boost, put_ratio=1.0,
                            call_admit=True, put_admit=True):
    """Apply log-curve boost to runner.df, return aggregated stats.

    runner.df must already have `days_to_ern` column (built once, reused).

    call_admit=False: only boost calls already in qualifying range (overall >= 70).
    put_admit=False:  only boost puts already in qualifying range (overall <= 25).
    """
    df = runner.df

    # Log-curve proximity: log(WINDOW + 1 - d) / log(WINDOW + 1)
    log_w_plus_1 = math.log(window + 1)

    is_in_window = (
        pl.col('days_to_ern').is_not_null()
        & (pl.col('days_to_ern') <= window)
        & (pl.col('days_to_ern') >= 0)
    )
    proximity = (
        pl.when(is_in_window)
        .then((window + 1 - pl.col('days_to_ern')).log() / log_w_plus_1)
        .otherwise(0.0)
    )

    call_boost = max_boost
    put_boost = max_boost * put_ratio

    # Only apply boost when the signal qualifies under (admit) constraints
    call_qualifies = (pl.col('overall') >= 50) & (
        pl.lit(True) if call_admit else (pl.col('overall') >= 70)
    )
    put_qualifies = (pl.col('overall') < 50) & (
        pl.lit(True) if put_admit else (pl.col('overall') <= 25)
    )

    boost = (
        pl.when(call_qualifies)
        .then(1.0 + call_boost * proximity)
        .when(put_qualifies)
        .then(1.0 + put_boost * proximity)
        .otherwise(1.0)
    )

    df = df.with_columns(
        ((50.0 + (pl.col('overall') - 50.0) * boost).round().clip(0, 100).cast(pl.Int64)).alias('new_overall')
    )
    return runner._aggregate_from_df(df)


# Baseline = no boost (apply mult=1.0 ⇒ new_overall == overall)
def evaluate_baseline(runner):
    df = runner.df.with_columns(pl.col('overall').alias('new_overall'))
    return runner._aggregate_from_df(df)


def fmt_bucket_row(stats, key):
    s = stats.get(key, {})
    n = s.get('sample_count', 0)
    if n == 0:
        return f"{key:>5} | {0:>6} | {'-':>5}  {'-':>5}  {'-':>5}"
    wr15 = s.get('win_rate_15d')
    wr30 = s.get('win_rate_30d')
    cap30 = s.get('capture_ratio_30d')
    wr15_s = f"{wr15:5.1f}" if wr15 is not None else "  -  "
    wr30_s = f"{wr30:5.1f}" if wr30 is not None else "  -  "
    cap_s = f"{cap30:+.2f}" if cap30 is not None else "  -  "
    return f"{key:>5} | {n:>6} | {wr15_s}  {wr30_s}  {cap_s}"


def diff_table(baseline, variants_results, variant_labels):
    """Print bucket × variant grid: WR15 / WR30 / N for baseline + each variant."""
    keys = [f'{t}+' for t in BUY_THRESHOLDS] + [f'<{t}' for t in SELL_THRESHOLDS]
    # Header
    cols = ['baseline'] + variant_labels
    print(f"\n{'Bucket':>6} | {'metric':<7} |  " + "  ".join(f"{c:>20}" for c in cols))
    print('-' * (15 + 22 * len(cols)))
    for k in keys:
        b = baseline['bucketed_stats'].get(k, {})
        b_n = b.get('sample_count', 0)
        for metric in ['WR15', 'WR30', 'N', 'Cap30']:
            row = [k if metric == 'WR15' else '', metric]
            cell_b = ''
            if metric == 'WR15':
                v = b.get('win_rate_15d')
                cell_b = f"{v:5.1f}" if v is not None else '   -'
            elif metric == 'WR30':
                v = b.get('win_rate_30d')
                cell_b = f"{v:5.1f}" if v is not None else '   -'
            elif metric == 'N':
                cell_b = f"{b_n}"
            elif metric == 'Cap30':
                v = b.get('capture_ratio_30d')
                cell_b = f"{v:+.2f}" if v is not None else '   -'
            cells = [f"{cell_b:>20}"]
            for vr in variants_results:
                vv = vr['bucketed_stats'].get(k, {})
                v_n = vv.get('sample_count', 0)
                if metric == 'WR15':
                    v = vv.get('win_rate_15d')
                    bv = b.get('win_rate_15d')
                    if v is None:
                        cell = '   -'
                    elif bv is None:
                        cell = f"{v:5.1f}"
                    else:
                        cell = f"{v:5.1f} ({v-bv:+5.1f})"
                elif metric == 'WR30':
                    v = vv.get('win_rate_30d')
                    bv = b.get('win_rate_30d')
                    if v is None:
                        cell = '   -'
                    elif bv is None:
                        cell = f"{v:5.1f}"
                    else:
                        cell = f"{v:5.1f} ({v-bv:+5.1f})"
                elif metric == 'N':
                    if b_n > 0:
                        cell = f"{v_n} ({v_n-b_n:+d})"
                    else:
                        cell = f"{v_n}"
                elif metric == 'Cap30':
                    v = vv.get('capture_ratio_30d')
                    bv = b.get('capture_ratio_30d')
                    if v is None:
                        cell = '   -'
                    elif bv is None:
                        cell = f"{v:+.2f}"
                    else:
                        cell = f"{v:+.2f} ({v-bv:+.2f})"
                cells.append(f"{cell:>20}")
            print(f"{row[0]:>6} | {row[1]:<7} |  " + "  ".join(cells))
        print()


def main():
    t0 = time.time()
    runner = FastVariantRunner(version_id=None, lookback_days=1825)
    runner.load(verbose=True)
    print(f"[runner] loaded in {time.time()-t0:.1f}s", flush=True)

    # Build earnings lookup once
    t1 = time.time()
    symbols = runner.df['symbol'].unique().to_list()
    print(f"[earnings] loading earnings for {len(symbols)} symbols ...", flush=True)
    by_sym = build_earnings_lookup(symbols, runner.lookback_days)
    print(f"[earnings] {len(by_sym)} symbols have earnings, {sum(len(v) for v in by_sym.values())} dates total", flush=True)

    # Compute days_to_ern column ONCE for max possible window (use 10 to cover all sweep values)
    print(f"[earnings] computing days_to_ern column ...", flush=True)
    runner.df = compute_days_to_earnings(runner.df, by_sym, max_window=10)
    n_in_win = runner.df.filter(pl.col('days_to_ern').is_not_null()).height
    print(f"[earnings] {n_in_win:,} of {runner.df.height:,} score rows are within 10d of earnings", flush=True)
    print(f"[earnings] step took {time.time()-t1:.1f}s", flush=True)

    # ── Baseline (no boost) ──────────────────────────────────────────────
    print('\n[variant] baseline (no boost)', flush=True)
    baseline = evaluate_baseline(runner)

    # ── Variant grid ─────────────────────────────────────────────────────
    variants = []
    labels = []
    for window in [3, 5, 7]:
        for max_boost in [0.05, 0.10, 0.15]:
            for put_ratio in [1.0, 1.2]:
                label = f"W{window}_B{int(max_boost*100):02d}_P{int(put_ratio*10):02d}"
                t = time.time()
                res = evaluate_earnings_boost(runner, window, max_boost, put_ratio)
                dt = time.time() - t
                print(f"[variant] {label} ({dt:.2f}s)", flush=True)
                variants.append(res)
                labels.append(label)

    # ── Diff table ────────────────────────────────────────────────────────
    print('\n' + '='*100)
    print(f"EARNINGS BOOST SWEEP — {len(variants)} variants vs baseline")
    print('='*100)

    # Print top-half: highest-N gainers
    # Easier: print summary metric per variant
    print(f"\n{'Variant':<22} | {'70+ N':>7} {'70+ WR15':>9} | {'75+ N':>7} {'75+ WR15':>9} | "
          f"{'<25 N':>7} {'<25 WR15':>9} | {'<15 N':>7} {'<15 WR15':>9} | {'<5 N':>7} {'<5 WR15':>9}")
    print('-' * 120)
    bs = baseline['bucketed_stats']
    def _fmt(s_, key):
        s = s_.get(key, {})
        n = s.get('sample_count', 0)
        wr = s.get('win_rate_15d')
        return n, wr
    n70b, wr70b = _fmt(bs, '70+'); n75b, wr75b = _fmt(bs, '75+')
    n25b, wr25b = _fmt(bs, '<25');  n15b, wr15b = _fmt(bs, '<15');  n5b, wr5b = _fmt(bs, '<5')
    def _row(label, stats_):
        s = stats_['bucketed_stats']
        n70, wr70 = _fmt(s, '70+'); n75, wr75 = _fmt(s, '75+')
        n25, wr25 = _fmt(s, '<25');  n15, wr15 = _fmt(s, '<15');  n5, wr5 = _fmt(s, '<5')
        def f1(n, wr, nb, wrb):
            wrs = f"{wr:5.1f}" if wr is not None else '   -'
            wrd = f"({wr-wrb:+4.1f})" if (wr is not None and wrb is not None) else '       '
            return f"{n:>7d}", f"{wrs}{wrd}"
        return (
            label,
            *f1(n70, wr70, n70b, wr70b), *f1(n75, wr75, n75b, wr75b),
            *f1(n25, wr25, n25b, wr25b), *f1(n15, wr15, n15b, wr15b),
            *f1(n5, wr5, n5b, wr5b),
        )
    rb = _row('BASELINE', baseline)
    print(f"{rb[0]:<22} | {rb[1]:>7} {rb[2]:>13} | {rb[3]:>7} {rb[4]:>13} | {rb[5]:>7} {rb[6]:>13} | {rb[7]:>7} {rb[8]:>13} | {rb[9]:>7} {rb[10]:>13}")
    for label, res in zip(labels, variants):
        r = _row(label, res)
        print(f"{r[0]:<22} | {r[1]:>7} {r[2]:>13} | {r[3]:>7} {r[4]:>13} | {r[5]:>7} {r[6]:>13} | {r[7]:>7} {r[8]:>13} | {r[9]:>7} {r[10]:>13}")

    print(f"\n[total] {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
