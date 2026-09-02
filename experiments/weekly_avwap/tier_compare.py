"""Cross-tier Ichimoku signal comparison (response to "does the gate matter?").

For each tier slice (50-59, 60-69, 70-74, 75-79, 80-84, 85+), report the
WR15/WR30(opt) tercile spread on price_vs_kijun_pct and the categorical
spread on cloud_position / tenkan_kijun_dir.

This answers: does the Ichimoku signal direction and magnitude generalize
below 75, or is it conviction-tier-dependent?

Three possible outcomes:
  1. Same direction, similar magnitude → score-range gate is NOT load-bearing;
     a continuous gradient mechanism is justified.
  2. Same direction, weaker/null magnitude → signal applies but is
     conviction-dependent; gate kept but with more confidence.
  3. Inverted direction at sub-75 → the gate IS load-bearing (signal
     specifically captures high-conviction-but-weak-weekly setups);
     keep the dampener tightly gated.

Usage:
    python experiments/weekly_avwap/tier_compare.py [parquet_name]
"""
from __future__ import annotations

import io
import math
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.cache' / 'weekly_avwap'
BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'

PARQUET = sys.argv[1] if len(sys.argv) > 1 else 'calls_v39_1825d_min50.parquet'

# Tier slices: (label, lo, hi)  inclusive lo, exclusive hi (except 85+ which is 85-100)
TIERS = [
    ('50-59', 50, 60),
    ('60-69', 60, 70),
    ('70-74', 70, 75),
    ('75-79', 75, 80),
    ('80-84', 80, 85),
    ('85+',   85, 101),
]


def cohort_wr(df: pl.DataFrame, col: str) -> tuple[float, int]:
    sub = df.filter(pl.col(col).is_not_null())
    n = len(sub)
    if n == 0:
        return float('nan'), 0
    return float(sub[col].mean()) * 100, n


def main():
    pq = CACHE / PARQUET
    if not pq.exists():
        print(f'[ERROR] parquet not found: {pq}', flush=True)
        return

    df = pl.read_parquet(pq)
    print(f'[load] parquet: {pq.name} → {len(df):,} rows', flush=True)

    # Drop the inf rows on w52_low_pct
    df = df.with_columns(
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct')
    )

    # Attach WR30(opt) — same load pattern as joint_profile.py
    print('[load] WR30(opt) from barrier_cache...', flush=True)
    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        wr30 = pl.read_database(
            """SELECT symbol, date, result AS opt_result_30
               FROM barrier_outcomes
               WHERE side='low' AND barrier_set='30dte_opt' AND w_days=30""",
            connection=conn,
        )
    finally:
        conn.close()
    df = df.join(wr30, on=['symbol', 'date'], how='left')
    print(f'[joined] WR30: {df["opt_result_30"].drop_nulls().len():,} resolved', flush=True)

    # ============================================================================
    # Per-tier table — tercile spread on price_vs_kijun_pct + categoricals
    # ============================================================================
    print('\n' + '=' * 110)
    print('Per-tier WR15 / WR30(opt) cohort spreads — does the Ichimoku signal generalize?')
    print('=' * 110)

    print(
        f'\n{"Tier":<8s} {"N":>7s} {"baseWR15":>9s} {"baseWR30":>9s}  '
        f'{"kijun spread (low→hi)":<26s}  '
        f'{"tk_dir(-1 vs +1)":<22s}  '
        f'{"cloud(-1 vs +1)":<22s}'
    )
    print('-' * 110)

    summary: list[dict] = []

    for label, lo, hi in TIERS:
        tier = df.filter((pl.col('overall') >= lo) & (pl.col('overall') < hi))
        if len(tier) < 50:
            print(f'{label:<8s} N={len(tier):>5,}  (insufficient, skip)')
            continue

        base15, _ = cohort_wr(tier, 'opt_result_15')
        base30, _ = cohort_wr(tier, 'opt_result_30')

        # Kijun terciles
        kij = tier.filter(
            pl.col('price_vs_kijun_pct').is_not_null()
            & pl.col('price_vs_kijun_pct').is_finite()
        )
        kij_sp15 = kij_sp30 = float('nan')
        if len(kij) >= 90:
            q33 = kij['price_vs_kijun_pct'].quantile(0.333)
            q67 = kij['price_vs_kijun_pct'].quantile(0.667)
            low = kij.filter(pl.col('price_vs_kijun_pct') <= q33)
            high = kij.filter(pl.col('price_vs_kijun_pct') > q67)
            wr15_lo, _ = cohort_wr(low, 'opt_result_15')
            wr15_hi, _ = cohort_wr(high, 'opt_result_15')
            wr30_lo, _ = cohort_wr(low, 'opt_result_30')
            wr30_hi, _ = cohort_wr(high, 'opt_result_30')
            kij_sp15 = wr15_hi - wr15_lo
            kij_sp30 = wr30_hi - wr30_lo

        # tenkan_kijun_dir: v=-1 vs v=+1
        tk_neg = tier.filter(pl.col('tenkan_kijun_dir') == -1)
        tk_pos = tier.filter(pl.col('tenkan_kijun_dir') == 1)
        tk_sp15 = tk_sp30 = float('nan')
        tk_neg_n = len(tk_neg)
        if tk_neg_n >= 30 and len(tk_pos) >= 30:
            wr15_neg, _ = cohort_wr(tk_neg, 'opt_result_15')
            wr15_pos, _ = cohort_wr(tk_pos, 'opt_result_15')
            wr30_neg, _ = cohort_wr(tk_neg, 'opt_result_30')
            wr30_pos, _ = cohort_wr(tk_pos, 'opt_result_30')
            tk_sp15 = wr15_pos - wr15_neg
            tk_sp30 = wr30_pos - wr30_neg

        # cloud_position: v=-1 vs v=+1
        cl_neg = tier.filter(pl.col('cloud_position') == -1)
        cl_pos = tier.filter(pl.col('cloud_position') == 1)
        cl_sp15 = cl_sp30 = float('nan')
        cl_neg_n = len(cl_neg)
        if cl_neg_n >= 30 and len(cl_pos) >= 30:
            wr15_neg, _ = cohort_wr(cl_neg, 'opt_result_15')
            wr15_pos, _ = cohort_wr(cl_pos, 'opt_result_15')
            wr30_neg, _ = cohort_wr(cl_neg, 'opt_result_30')
            wr30_pos, _ = cohort_wr(cl_pos, 'opt_result_30')
            cl_sp15 = wr15_pos - wr15_neg
            cl_sp30 = wr30_pos - wr30_neg

        print(
            f'{label:<8s} {len(tier):>7,} '
            f'{base15:>8.2f}% {base30:>8.2f}%  '
            f'k15={kij_sp15:>+5.2f} k30={kij_sp30:>+5.2f}  | '
            f'd15={tk_sp15:>+5.2f} d30={tk_sp30:>+5.2f} '
            f'(N-={tk_neg_n:>4,})  '
            f'c15={cl_sp15:>+5.2f} c30={cl_sp30:>+5.2f} '
            f'(N-={cl_neg_n:>4,})'
        )

        summary.append({
            'tier': label, 'n': len(tier),
            'base15': base15, 'base30': base30,
            'kij15': kij_sp15, 'kij30': kij_sp30,
            'tk15': tk_sp15, 'tk30': tk_sp30,
            'cl15': cl_sp15, 'cl30': cl_sp30,
        })

    # ============================================================================
    # Interpretation rules
    # ============================================================================
    print('\n' + '=' * 110)
    print('INTERPRETATION:')
    print('=' * 110)
    print(
        '  k15/k30 = price_vs_kijun_pct WR15/WR30 spread (high tercile − low tercile)\n'
        '  d15/d30 = tenkan_kijun_dir  WR15/WR30 spread (v=+1 cohort − v=−1 cohort)\n'
        '  c15/c30 = cloud_position    WR15/WR30 spread (v=+1 cohort − v=−1 cohort)\n'
        '  N-      = size of the bearish-Ichimoku sub-cohort within the tier\n'
        '\n'
        '  Direction signs:\n'
        '    Positive spread → bullish-Ichimoku state outperforms (signal direction matches 75+)\n'
        '    Negative spread → INVERSION — bearish-Ichimoku outperforms in this tier\n'
        '    |spread| < 1pp → null signal (keep gate, signal does not generalize)\n'
        '    |spread| 2-4pp → weak signal (gate optional, gradient effect modest)\n'
        '    |spread| ≥ 5pp → strong signal (gradient mechanism justified)\n'
    )

    # Auto-flag direction inversions
    print('Direction check across tiers (kijun WR15 spread):')
    signs = []
    for r in summary:
        sign = '+' if r['kij15'] > 0 else ('-' if r['kij15'] < 0 else '0')
        signs.append(sign)
        print(f'  {r["tier"]:<8s}  kij_sp15 = {r["kij15"]:+5.2f}pp  ({sign})')

    if all(s == '+' for s in signs if s != '0'):
        verdict = 'CONSISTENT — bullish-Ichimoku outperforms across ALL tiers'
    elif all(s == '-' for s in signs if s != '0'):
        verdict = 'CONSISTENT INVERSION — bearish-Ichimoku outperforms across ALL tiers (unexpected)'
    else:
        verdict = 'MIXED — direction differs by tier (gate is load-bearing)'
    print(f'\n  → {verdict}')


if __name__ == '__main__':
    main()
