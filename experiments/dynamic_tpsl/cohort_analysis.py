"""Cohort z-score: does daily_cash_demand predict TP/SL/bars-to-resolution?

Hypothesis: on high-cash-demand days, faster TP/SL resolution (and/or different
optimal TP/SL) because there are more attractive alternatives the parked capital
could be deployed against. If true, a dynamic TP/SL anchored on daily_cash_demand
beats a static one.

Cohort variable:
  daily_cash_demand_t = sum(tier_alloc_i for i in qualifying signals on date t)

This represents "expected fraction of portfolio the cascade WANTS to deploy
today" — proxies opportunity cost of capital tied up in losers.

Splits:
  Q1 (lowest demand) ... Q5 (highest demand) per side.

Per cohort metrics:
  - n
  - TP_rate
  - avg_exit_bars (across all firings, lower = faster resolution)
  - avg_exit_bars_winners
  - avg_exit_bars_losers
  - avg_exit_return (side-adjusted close-to-close %)
  - z vs population (mean TP_rate)

Also computes a partial-correlation check vs BREADTH_THRESHOLD=40 to see
if cash_demand adds information OVER the existing breadth-adaptive switch.
"""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polars as pl
import numpy as np
from database.bulk_cache import cache_path
from experiments._holdout import assert_no_holdout_leak

DEMAND_QS = [0.0, 0.20, 0.40, 0.60, 0.80, 1.0]
DEMAND_LABELS = ['Q1', 'Q2', 'Q3', 'Q4', 'Q5']


def _z_diff(p1, n1, p_pop, n_pop):
    """z-score of cohort proportion p1 vs population proportion p_pop."""
    if n1 == 0 or p_pop is None or n_pop == 0:
        return float('nan')
    se = (p_pop * (1 - p_pop) / n1) ** 0.5
    if se == 0: return float('nan')
    return (p1 - p_pop) / se


def main():
    path = cache_path('dynamic_tpsl_v46_5y')
    df = pl.read_parquet(path)
    assert_no_holdout_leak(df, context='dynamic_tpsl cohort_analysis')
    print(f"Loaded {len(df):,} peaks from {path.name}")

    # Daily cash demand per (date) — sum of tier_alloc across all qualifying signals on that date
    demand = df.group_by('date').agg(
        daily_cash_demand=pl.col('tier_alloc').sum(),
        daily_signal_count=pl.len(),
    )
    print(f"Distinct days: {len(demand):,}")
    print(f"\nDaily cash demand distribution:")
    print(demand['daily_cash_demand'].describe())

    # Join demand back per signal
    df = df.join(demand, on='date', how='left')

    # Split by side
    for side, side_label in [('low', 'CALL (75+)'), ('high', 'PUT (<=25)')]:
        sd = df.filter((pl.col('side') == side) & pl.col('result').is_not_null())
        if len(sd) == 0:
            continue
        # quantile boundaries on this side's signals' demand
        qs = sd['daily_cash_demand'].quantile
        bounds = [sd['daily_cash_demand'].quantile(q) for q in DEMAND_QS]
        # ensure unique
        sd = sd.with_columns(
            qbin=pl.col('daily_cash_demand').map_batches(
                lambda s: pl.Series([
                    next((i for i, b in enumerate(bounds[1:]) if v <= b), len(bounds)-2)
                    for v in s.to_list()
                ], dtype=pl.Int32)
            )
        )

        print(f"\n{'='*84}")
        print(f"{side_label} — cohort split by daily_cash_demand quantile (5y v46)")
        print(f"{'='*84}")

        p_pop = float(sd['result'].mean())
        n_pop = len(sd)
        avg_bars_pop = float(sd['exit_bars'].mean())

        # Header
        print(f"\nPopulation: N={n_pop:,}  TP_rate={p_pop*100:.2f}%  avg_exit_bars={avg_bars_pop:.2f}")
        print(f"\n{'Cohort':<8} {'demand_bnd':<14} {'N':>6} {'TP%':>7} {'z_TP':>7} "
              f"{'avg_bars':>9} {'bars_W':>7} {'bars_L':>7} {'avg_ret%':>9}")
        print('-' * 84)

        for i, qlabel in enumerate(DEMAND_LABELS):
            sub = sd.filter(pl.col('qbin') == i)
            if len(sub) == 0:
                continue
            demand_low = bounds[i]
            demand_high = bounds[i+1]
            n = len(sub)
            tp_rate = float(sub['result'].mean()) if n else float('nan')
            avg_bars = float(sub['exit_bars'].mean()) if n else float('nan')
            avg_bars_w = float(sub.filter(pl.col('result') == 1)['exit_bars'].mean() or float('nan'))
            avg_bars_l = float(sub.filter(pl.col('result') == 0)['exit_bars'].mean() or float('nan'))
            avg_ret = float(sub['exit_return'].mean()) if n else float('nan')
            z = _z_diff(tp_rate, n, p_pop, n_pop)
            print(f"{qlabel:<8} {demand_low:.2f}-{demand_high:.2f}  {n:>6,} "
                  f"{tp_rate*100:>6.2f}% {z:>+7.2f} {avg_bars:>9.2f} "
                  f"{avg_bars_w:>7.2f} {avg_bars_l:>7.2f} {avg_ret:>+9.2f}")

        # Direct test: extreme cohorts (Q5 vs Q1)
        q1 = sd.filter(pl.col('qbin') == 0)
        q5 = sd.filter(pl.col('qbin') == 4)
        if len(q1) > 0 and len(q5) > 0:
            p1 = float(q1['result'].mean()); n1 = len(q1)
            p5 = float(q5['result'].mean()); n5 = len(q5)
            # Pooled z for two-prop diff
            p_p = (p1*n1 + p5*n5) / (n1 + n5)
            se_diff = (p_p*(1-p_p)*(1/n1 + 1/n5))**0.5
            z_q1q5 = (p5 - p1) / se_diff if se_diff > 0 else float('nan')
            bars_q1 = float(q1['exit_bars'].mean())
            bars_q5 = float(q5['exit_bars'].mean())
            print(f"\n  Q5 vs Q1: ΔTP_rate={(p5-p1)*100:+.2f}pp  z={z_q1q5:+.2f}  "
                  f"Δavg_bars={bars_q5-bars_q1:+.2f}")

    # ==================================================================
    # Marginal-info check vs breadth: split by breadth_score within Q5 vs Q1
    # ==================================================================
    print(f"\n\n{'='*84}")
    print("MARGINAL INFO vs breadth: cash_demand split AFTER controlling for breadth")
    print(f"{'='*84}")

    # Pull breadth_score per date
    from market_breadth import MarketBreadth
    breadth_rows = list(MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
                        .where(MarketBreadth.date >= '2021-01-01')
                        .where(MarketBreadth.date <= '2026-05-15')
                        .dicts())
    bdf = pl.DataFrame({
        'date': [r['date'].isoformat() for r in breadth_rows],
        'breadth_score': [float(r['breadth_score']) if r['breadth_score'] is not None else None
                          for r in breadth_rows],
    })
    df = df.join(bdf, on='date', how='left')

    # 2x2: low_breadth (≤40) × low_demand vs high_demand
    for side, side_label in [('low', 'CALL'), ('high', 'PUT')]:
        sd = df.filter((pl.col('side') == side) & pl.col('result').is_not_null() &
                        pl.col('breadth_score').is_not_null())
        if len(sd) == 0: continue
        # split into 4 cells
        median_demand = sd['daily_cash_demand'].median()
        cells = [
            ('breadth>40 & demand<med', sd.filter((pl.col('breadth_score') > 40) & (pl.col('daily_cash_demand') < median_demand))),
            ('breadth>40 & demand>=med', sd.filter((pl.col('breadth_score') > 40) & (pl.col('daily_cash_demand') >= median_demand))),
            ('breadth<=40 & demand<med', sd.filter((pl.col('breadth_score') <= 40) & (pl.col('daily_cash_demand') < median_demand))),
            ('breadth<=40 & demand>=med', sd.filter((pl.col('breadth_score') <= 40) & (pl.col('daily_cash_demand') >= median_demand))),
        ]
        print(f"\n{side_label}:")
        for name, sub in cells:
            n = len(sub)
            if n == 0:
                print(f"  {name:>30}: N=0")
                continue
            tp = float(sub['result'].mean()) * 100
            bars = float(sub['exit_bars'].mean())
            ret = float(sub['exit_return'].mean())
            print(f"  {name:>30}: N={n:>5,}  TP={tp:>5.2f}%  bars={bars:>4.2f}  ret={ret:>+5.2f}%")


if __name__ == '__main__':
    main()
