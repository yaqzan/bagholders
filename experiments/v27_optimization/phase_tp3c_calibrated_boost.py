"""
Phase TP3C — WR-calibrated, log-smoothed earnings boost

Phase 3B revealed empirical WR15 lifts per (cohort × bucket) cell. This
phase implements a boost where MAGNITUDE is proportional to empirical lift
(log-smoothed, clamped to [0, 1]) and PROXIMITY follows the log curve.
Cells with negative lift (pre7 puts, etc.) get zero boost — no harmful
admissions.

Formula:
    proximity(d) = log(W+1-d) / log(W+1)         d ∈ [0, W]
    strength(C, B, side) = log(1 + max(0, lift)) / log(1 + lift_norm_side)
    boost_mult = 1 + MAX_BOOST × proximity × strength
    new_overall = clip(round(50 + (overall - 50) × boost_mult), 0, 100)

Key axes for sweep:
    WINDOW       ∈ {3, 5, 7}
    MAX_BOOST    ∈ {0.5, 1.0, 1.5}      (much larger than 3A — calibration
                                          requires room to span buckets)
    LIFT_NORM    ∈ {15.0, 20.0, 25.0}    (where strength saturates to 1.0)

Run: python experiments/v27_optimization/phase_tp3c_calibrated_boost.py
"""
from __future__ import annotations
import io, sys, os, math, json, time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

from experiments.fast_variant_runner import FastVariantRunner, BUY_THRESHOLDS, SELL_THRESHOLDS
from experiments.v27_optimization.phase_tp3a_earnings_boost import (
    build_earnings_lookup, compute_days_to_earnings, evaluate_baseline,
)
from experiments.v27_optimization.phase_tp3b_calibration import (
    cohort_label, call_bucket, put_bucket,
)


def load_lift_table():
    """Load (side, cohort, bucket) -> (lift_pp, n) from Phase 3B output."""
    path = ROOT / 'experiments' / 'v27_optimization' / 'phase_tp3b_lift_table.json'
    with open(path) as f:
        raw = json.load(f)
    # Rebuild with (side, cohort, bucket) tuple keys
    table = {}
    for k, v in raw.items():
        side, cohort, bucket = k.split('|')
        lift, n = v
        table[(side, cohort, bucket)] = (float(lift), int(n))
    return table


def evaluate_calibrated_boost(runner, lift_table, window, max_boost,
                              lift_norm_call, lift_norm_put,
                              call_admit=True, put_admit=False,
                              min_n=10):
    """Apply calibrated boost. min_n: ignore lift table cells with N < min_n
    (treat as no-data, strength=0).
    """
    df = runner.df

    log_w_plus_1 = math.log(window + 1)

    # Build a lookup: (side, cohort, bucket) -> strength in [0, 1]
    # log-smoothed: strength = log(1 + lift) / log(1 + lift_norm)
    strength_map_call = {}  # (cohort, bucket) -> strength
    strength_map_put  = {}
    for (side, cohort, bucket), (lift, n) in lift_table.items():
        if n < min_n:
            continue
        if lift <= 0:
            strength = 0.0
        else:
            log_norm = math.log(1 + (lift_norm_call if side == 'low' else lift_norm_put))
            strength = min(1.0, math.log(1 + lift) / log_norm)
        if side == 'low':
            strength_map_call[(cohort, bucket)] = strength
        else:
            strength_map_put[(cohort, bucket)] = strength

    # Add cohort and bucket cols on the runner df
    # Cohort
    df = df.with_columns([
        pl.when(pl.col('days_to_ern').is_null()).then(pl.lit('none'))
          .when(pl.col('days_to_ern') <= 1).then(pl.lit('pre1'))
          .when(pl.col('days_to_ern') <= 3).then(pl.lit('pre3'))
          .when(pl.col('days_to_ern') <= 7).then(pl.lit('pre7'))
          .otherwise(pl.lit('none'))
          .alias('cohort'),
    ])
    # Bucket (for boost lookup; based on ORIGINAL overall)
    df = df.with_columns([
        pl.when(pl.col('overall') >= 95).then(pl.lit('95+'))
          .when(pl.col('overall') >= 90).then(pl.lit('90-94'))
          .when(pl.col('overall') >= 85).then(pl.lit('85-89'))
          .when(pl.col('overall') >= 80).then(pl.lit('80-84'))
          .when(pl.col('overall') >= 75).then(pl.lit('75-79'))
          .when(pl.col('overall') >= 70).then(pl.lit('70-74'))
          .when(pl.col('overall') <= 5).then(pl.lit('0-5'))
          .when(pl.col('overall') <= 10).then(pl.lit('6-10'))
          .when(pl.col('overall') <= 15).then(pl.lit('11-15'))
          .when(pl.col('overall') <= 20).then(pl.lit('16-20'))
          .when(pl.col('overall') <= 25).then(pl.lit('21-25'))
          .otherwise(pl.lit('mid'))
          .alias('boost_bucket'),
    ])

    # Strength column: lookup based on (side, cohort, boost_bucket)
    # We do this by building a struct mapping. Polars: just use map_dict on tuple.
    # Workaround: convert to dataframe, lookup via join
    sm_rows = []
    for (cohort, bucket), strength in strength_map_call.items():
        sm_rows.append({'side': 'low', 'cohort': cohort, 'boost_bucket': bucket, 'strength': strength})
    for (cohort, bucket), strength in strength_map_put.items():
        sm_rows.append({'side': 'high', 'cohort': cohort, 'boost_bucket': bucket, 'strength': strength})
    sm_df = pl.DataFrame(sm_rows) if sm_rows else pl.DataFrame({'side': [], 'cohort': [], 'boost_bucket': [], 'strength': []})

    # Add side col
    df = df.with_columns(
        pl.when(pl.col('overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('side')
    )

    df = df.join(sm_df, on=['side', 'cohort', 'boost_bucket'], how='left')
    df = df.with_columns(pl.col('strength').fill_null(0.0))

    # Proximity (log curve)
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

    # Admit constraints
    call_qualifies = (pl.col('overall') >= 50) & (
        pl.lit(True) if call_admit else (pl.col('overall') >= 70)
    )
    put_qualifies = (pl.col('overall') < 50) & (
        pl.lit(True) if put_admit else (pl.col('overall') <= 25)
    )

    # boost_mult = 1 + max_boost × proximity × strength  (only when qualifies)
    boost_factor = max_boost * proximity * pl.col('strength')
    boost_mult = (
        pl.when(call_qualifies | put_qualifies)
        .then(1.0 + boost_factor)
        .otherwise(1.0)
    )

    df = df.with_columns(
        ((50.0 + (pl.col('overall') - 50.0) * boost_mult).round().clip(0, 100).cast(pl.Int64)).alias('new_overall')
    )
    return runner._aggregate_from_df(df)


PARAM_GRID = [
    # (window, max_boost, lift_norm_call, lift_norm_put, label)
    # max_boost large enough for pre1 80-84 (lift=22, strength=1.0) to span ~13 score points
    (5, 0.50, 22.3, 16.3, 'W5_B0.50'),
    (5, 0.75, 22.3, 16.3, 'W5_B0.75'),
    (5, 1.00, 22.3, 16.3, 'W5_B1.00'),
    (5, 1.50, 22.3, 16.3, 'W5_B1.50'),
    (3, 0.75, 22.3, 16.3, 'W3_B0.75'),
    (3, 1.00, 22.3, 16.3, 'W3_B1.00'),
    (3, 1.50, 22.3, 16.3, 'W3_B1.50'),
    (7, 0.75, 22.3, 16.3, 'W7_B0.75'),
    (7, 1.00, 22.3, 16.3, 'W7_B1.00'),
]


def main():
    t0 = time.time()
    runner = FastVariantRunner(version_id=None, lookback_days=1825)
    runner.load(verbose=True)
    print(f"[runner] loaded in {time.time()-t0:.1f}s", flush=True)

    t1 = time.time()
    symbols = runner.df['symbol'].unique().to_list()
    by_sym = build_earnings_lookup(symbols, runner.lookback_days)
    runner.df = compute_days_to_earnings(runner.df, by_sym, max_window=10)
    print(f"[earnings] step took {time.time()-t1:.1f}s", flush=True)

    lift_table = load_lift_table()
    print(f"[lift] loaded {len(lift_table)} cells", flush=True)

    print('\n[variant] baseline (no boost)', flush=True)
    baseline = evaluate_baseline(runner)

    rows = [('BASELINE', baseline)]
    for window, max_boost, ln_c, ln_p, label in PARAM_GRID:
        t = time.time()
        # AN mode: calls admit, puts no-admit (Phase 3A.2 finding)
        res = evaluate_calibrated_boost(
            runner, lift_table, window, max_boost, ln_c, ln_p,
            call_admit=True, put_admit=False, min_n=10
        )
        print(f"[variant] {label} ({time.time()-t:.2f}s)", flush=True)
        rows.append((label, res))

    # Print bucket-level diff table
    print('\n' + '='*140)
    print('CALIBRATED BOOST SWEEP — calls admit, puts no-admit, log-smoothed strength')
    print('='*140)

    bs = baseline['bucketed_stats']
    BUCKETS = ['95+', '90+', '85+', '80+', '75+', '70+', '<25', '<20', '<15', '<10', '<5']

    def get(stats_dict, key):
        d = stats_dict.get(key, {})
        n = d.get('sample_count', 0)
        wr15 = d.get('win_rate_15d')
        wr30 = d.get('win_rate_30d')
        return n, wr15, wr30

    print(f"\n{'Variant':<12} | " + " | ".join(f"{b:>12}" for b in BUCKETS))
    print(f"{'':12} | " + " | ".join(f"{'N      WR15':>12}" for b in BUCKETS))
    print('-' * (15 + 15 * len(BUCKETS)))

    for label, stats in rows:
        s = stats['bucketed_stats']
        cells = []
        for b in BUCKETS:
            n, wr15, _ = get(s, b)
            if label == 'BASELINE':
                cells.append(f"{n:>5}/{wr15 if wr15 is not None else 0:5.1f}".replace('  0.0', '   - '))
            else:
                nb, wb15, _ = get(bs, b)
                if wr15 is not None and wb15 is not None:
                    cells.append(f"{n-nb:>+5d}/{wr15-wb15:+5.1f}")
                else:
                    cells.append(f"{n-nb:>+5d}/  -  ")
        print(f"{label:<12} | " + " | ".join(f"{c:>12}" for c in cells))

    print(f"\n[total] {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
