"""Phase B+: Joint AVWAP × Ichimoku cohort + WR30 cross-check.

Two questions answered here, building on profile.py:

  Q1) Does the slow-indicator signal strengthen at WR30?
      Slow indicators (Ichimoku, 50W SMA, AVWAP) are inherently slower-acting
      than RSI/MACD.  The signal might need >15d to express.  Loads
      30dte_opt at w=30 (option-aligned) AND 30dte_generic at w=30 (dashboard's
      K=2σ/M=5σ barrier) for both metrics.

  Q2) Does AVWAP add information conditional on Ichimoku?
      Phase B showed AVWAP is a null signal MARGINALLY (0.7pp at 75+).
      But maybe the COMBINATION (e.g., bullish Ichimoku + above AVWAP =
      "confirmed strong" vs bullish Ichimoku + below AVWAP = "weak rally")
      separates cohorts that the marginals miss.

Output goes to stdout — no parquet write.  Reads existing parquet built by
build_features.py.

Usage:
    python experiments/weekly_avwap/joint_profile.py [parquet_name]
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

PARQUET = sys.argv[1] if len(sys.argv) > 1 else 'calls_v39_1825d.parquet'

GATE_PP = 5.0


def _wr_z(n: int, k: int, p_baseline: float) -> tuple[float, float]:
    if n == 0:
        return float('nan'), 0.0
    p = k / n
    se = math.sqrt(p_baseline * (1 - p_baseline) / n) if 0 < p_baseline < 1 else 0
    z = (p - p_baseline) / se if se > 0 else 0.0
    return p * 100, z


def load_wr30_barriers(barrier_set: str) -> pl.DataFrame:
    """Load WR30 outcomes from barrier_cache (option-aligned or generic)."""
    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        df = pl.read_database(
            f"""SELECT symbol, date, result AS opt_result_30
                FROM barrier_outcomes
                WHERE side = 'low'
                  AND barrier_set = '{barrier_set}'
                  AND w_days = 30""",
            connection=conn,
        )
    finally:
        conn.close()
    return df


def cohort_wr(df: pl.DataFrame, result_col: str) -> tuple[float, int]:
    """Return (wr_pct, n_resolved) for a sub-DataFrame."""
    sub = df.filter(pl.col(result_col).is_not_null())
    n = len(sub)
    if n == 0:
        return float('nan'), 0
    wr = float(sub[result_col].mean()) * 100
    return wr, n


def main():
    pq = CACHE / PARQUET
    if not pq.exists():
        print(f'[ERROR] parquet not found: {pq}', flush=True)
        return

    df = pl.read_parquet(pq)

    # ------------------------------------------------------------------
    # Drop the inf rows (data quality artifacts on w52_low_pct = 0 stocks)
    # ------------------------------------------------------------------
    df = df.with_columns([
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct'),
    ])

    # ------------------------------------------------------------------
    # Attach WR30 outcomes (option-aligned 30dte_opt and dashboard-generic)
    # ------------------------------------------------------------------
    print(f'[load] WR30 from barrier_cache...', flush=True)
    wr30_opt = load_wr30_barriers('30dte_opt').rename({'opt_result_30': 'opt_result_30'})
    wr30_gen = load_wr30_barriers('30dte_generic').rename({'opt_result_30': 'gen_result_30'})

    df = df.join(wr30_opt, on=['symbol', 'date'], how='left')
    df = df.join(wr30_gen, on=['symbol', 'date'], how='left')

    print(f'[joined] {len(df):,} peaks  '
          f'(WR15 resolved: {df["opt_result_15"].drop_nulls().len():,}, '
          f'WR30_opt: {df["opt_result_30"].drop_nulls().len():,}, '
          f'WR30_gen: {df["gen_result_30"].drop_nulls().len():,})',
          flush=True)

    # ============================================================================
    # PART 1: WR30 cross-check at the 75+ tier — does the signal strengthen?
    # ============================================================================
    print('\n' + '=' * 100)
    print('PART 1 — WR15 vs WR30 (option-aligned) vs WR30 (generic K=2σ)')
    print('Tier: 75+ calls  |  metric in each row: % win, spread = high tercile − low tercile')
    print('=' * 100)

    tier = df.filter(pl.col('overall') >= 75)

    print(
        f'\n{"feature":<22s}  '
        f'{"low N":>6s}  {"low WR15":>9s} {"WR30op":>7s} {"WR30gn":>7s}  '
        f'{"high N":>6s}  {"hi WR15":>8s} {"WR30op":>7s} {"WR30gn":>7s}  '
        f'{"sp15":>6s} {"sp30op":>7s} {"sp30gn":>7s}'
    )
    print('-' * 110)

    for feature in [
        'avwap_pct', 'price_vs_kijun_pct', 'price_vs_tenkan_pct',
        'price_vs_span_b_pct', 'sma50w_pct', 'w52_high_pct', 'w52_low_pct',
    ]:
        sub = tier.filter(
            pl.col(feature).is_not_null()
            & pl.col(feature).is_finite()
        )
        if len(sub) < 50:
            print(f'{feature:<22s}  SKIP (N={len(sub)})')
            continue
        q33 = sub[feature].quantile(0.333)
        q67 = sub[feature].quantile(0.667)
        low  = sub.filter(pl.col(feature) <= q33)
        high = sub.filter(pl.col(feature) > q67)

        wr15_lo, n15_lo = cohort_wr(low, 'opt_result_15')
        wr15_hi, n15_hi = cohort_wr(high, 'opt_result_15')
        wr30op_lo, _ = cohort_wr(low, 'opt_result_30')
        wr30op_hi, _ = cohort_wr(high, 'opt_result_30')
        wr30gn_lo, _ = cohort_wr(low, 'gen_result_30')
        wr30gn_hi, _ = cohort_wr(high, 'gen_result_30')

        sp15  = wr15_hi - wr15_lo
        sp30o = wr30op_hi - wr30op_lo
        sp30g = wr30gn_hi - wr30gn_lo

        print(
            f'{feature:<22s}  '
            f'{n15_lo:>6,}  {wr15_lo:>8.2f}% {wr30op_lo:>6.2f}% {wr30gn_lo:>6.2f}%  '
            f'{n15_hi:>6,}  {wr15_hi:>7.2f}% {wr30op_hi:>6.2f}% {wr30gn_hi:>6.2f}%  '
            f'{sp15:>+5.2f} {sp30o:>+6.2f} {sp30g:>+6.2f}'
        )

    # Categorical
    print()
    for feature in ['tenkan_kijun_dir', 'cloud_position']:
        sub = tier.filter(pl.col(feature).is_not_null())
        if len(sub) < 50:
            continue
        for val in sorted(sub[feature].unique().drop_nulls().to_list()):
            grp = sub.filter(pl.col(feature) == val)
            wr15, n15 = cohort_wr(grp, 'opt_result_15')
            wr30op, _ = cohort_wr(grp, 'opt_result_30')
            wr30gn, _ = cohort_wr(grp, 'gen_result_30')
            print(
                f'{feature:<22s}  v={val:+d}  N={n15:>5,}  '
                f'WR15={wr15:5.2f}%  WR30(opt)={wr30op:5.2f}%  WR30(gen)={wr30gn:5.2f}%'
            )

    # ============================================================================
    # PART 2: Joint AVWAP × Ichimoku cohorts on 75+
    # ============================================================================
    print('\n' + '=' * 100)
    print('PART 2 — Joint cohort: AVWAP × tenkan_kijun_dir on 75+ calls')
    print('Question: does AVWAP add information conditional on Ichimoku state?')
    print('=' * 100)

    base = df.filter(
        (pl.col('overall') >= 75)
        & pl.col('avwap_pct').is_not_null()
        & pl.col('avwap_pct').is_finite()
        & pl.col('tenkan_kijun_dir').is_not_null()
    )
    print(f'\nbase 75+ N = {len(base):,}  (with both AVWAP and tenkan_kijun_dir non-null)')

    # AVWAP split: above/below VWAP anchor (positive = price above VWAP since earnings)
    base = base.with_columns(
        pl.when(pl.col('avwap_pct') > 0).then(pl.lit('above'))
          .otherwise(pl.lit('below')).alias('avwap_side')
    )

    print(
        f'\n{"Ichimoku":<10s} {"AVWAP":<8s}  '
        f'{"N":>6s}  {"WR15":>7s}  {"WR30op":>8s}  {"WR30gn":>8s}'
    )
    print('-' * 70)
    for ich_val in [1, 0, -1]:
        for avwap_side in ['above', 'below']:
            grp = base.filter(
                (pl.col('tenkan_kijun_dir') == ich_val)
                & (pl.col('avwap_side') == avwap_side)
            )
            n = len(grp)
            if n < 30:
                print(f'{ich_val:>+8d}    {avwap_side:<8s}  N={n:>5,}  (< 30, skip)')
                continue
            wr15, _ = cohort_wr(grp, 'opt_result_15')
            wr30op, _ = cohort_wr(grp, 'opt_result_30')
            wr30gn, _ = cohort_wr(grp, 'gen_result_30')
            print(f'{ich_val:>+8d}    {avwap_side:<8s}  '
                  f'{n:>6,}  {wr15:>6.2f}%  {wr30op:>7.2f}%  {wr30gn:>7.2f}%')

    # ============================================================================
    # PART 3: Joint cloud_position × AVWAP on 75+
    # ============================================================================
    print('\n' + '=' * 100)
    print('PART 3 — Joint cohort: AVWAP × cloud_position on 75+ calls')
    print('=' * 100)

    base2 = df.filter(
        (pl.col('overall') >= 75)
        & pl.col('avwap_pct').is_not_null()
        & pl.col('avwap_pct').is_finite()
        & pl.col('cloud_position').is_not_null()
    )

    base2 = base2.with_columns(
        pl.when(pl.col('avwap_pct') > 0).then(pl.lit('above'))
          .otherwise(pl.lit('below')).alias('avwap_side')
    )

    print(
        f'\n{"Cloud":<10s} {"AVWAP":<8s}  '
        f'{"N":>6s}  {"WR15":>7s}  {"WR30op":>8s}  {"WR30gn":>8s}'
    )
    print('-' * 70)
    for c_val in [1, 0, -1]:
        for avwap_side in ['above', 'below']:
            grp = base2.filter(
                (pl.col('cloud_position') == c_val)
                & (pl.col('avwap_side') == avwap_side)
            )
            n = len(grp)
            if n < 30:
                print(f'{c_val:>+8d}    {avwap_side:<8s}  N={n:>5,}  (< 30, skip)')
                continue
            wr15, _ = cohort_wr(grp, 'opt_result_15')
            wr30op, _ = cohort_wr(grp, 'opt_result_30')
            wr30gn, _ = cohort_wr(grp, 'gen_result_30')
            print(f'{c_val:>+8d}    {avwap_side:<8s}  '
                  f'{n:>6,}  {wr15:>6.2f}%  {wr30op:>7.2f}%  {wr30gn:>7.2f}%')

    # ============================================================================
    # PART 4: Composite Ichimoku score (combine all 5 Ichimoku features)
    # ============================================================================
    print('\n' + '=' * 100)
    print('PART 4 — Composite Ichimoku score (all 5 features summed)')
    print('Each peak gets a score in {−5..+5}: sum of signs of (kijun, tenkan, span_b,')
    print('tenkan_kijun_dir, cloud_position) — bullish for each ≥0 component.')
    print('=' * 100)

    base3 = df.filter(
        (pl.col('overall') >= 75)
        & pl.col('price_vs_kijun_pct').is_not_null()
        & pl.col('price_vs_tenkan_pct').is_not_null()
        & pl.col('price_vs_span_b_pct').is_not_null()
        & pl.col('tenkan_kijun_dir').is_not_null()
        & pl.col('cloud_position').is_not_null()
    )
    base3 = base3.with_columns([
        # Each component: +1 if bullish (above indicator / +1 dir), 0 if neutral, -1 bearish
        pl.when(pl.col('price_vs_kijun_pct') > 0).then(1)
          .when(pl.col('price_vs_kijun_pct') < 0).then(-1)
          .otherwise(0).alias('s_kijun'),
        pl.when(pl.col('price_vs_tenkan_pct') > 0).then(1)
          .when(pl.col('price_vs_tenkan_pct') < 0).then(-1)
          .otherwise(0).alias('s_tenkan'),
        pl.when(pl.col('price_vs_span_b_pct') > 0).then(1)
          .when(pl.col('price_vs_span_b_pct') < 0).then(-1)
          .otherwise(0).alias('s_spanb'),
    ])
    base3 = base3.with_columns(
        (pl.col('s_kijun') + pl.col('s_tenkan') + pl.col('s_spanb')
         + pl.col('tenkan_kijun_dir').cast(pl.Int32)
         + pl.col('cloud_position').cast(pl.Int32)).alias('ich_score')
    )

    print(f'\nbase 75+ N = {len(base3):,}  (with all 5 Ichimoku features non-null)')
    print(f'\n{"ich_score":>10s}  {"N":>6s}  {"WR15":>7s}  {"WR30op":>8s}  {"WR30gn":>8s}')
    print('-' * 55)
    for s in sorted(base3['ich_score'].unique().drop_nulls().to_list()):
        grp = base3.filter(pl.col('ich_score') == s)
        n = len(grp)
        wr15, _ = cohort_wr(grp, 'opt_result_15')
        wr30op, _ = cohort_wr(grp, 'opt_result_30')
        wr30gn, _ = cohort_wr(grp, 'gen_result_30')
        print(f'{s:>10d}  {n:>6,}  {wr15:>6.2f}%  {wr30op:>7.2f}%  {wr30gn:>7.2f}%')

    # Spread between fully-bullish (+5) and fully-bearish (−5)
    full_bull = base3.filter(pl.col('ich_score') == 5)
    full_bear = base3.filter(pl.col('ich_score') == -5)
    if len(full_bull) > 30 and len(full_bear) > 30:
        wr15_bu, _ = cohort_wr(full_bull, 'opt_result_15')
        wr15_be, _ = cohort_wr(full_bear, 'opt_result_15')
        wr30op_bu, _ = cohort_wr(full_bull, 'opt_result_30')
        wr30op_be, _ = cohort_wr(full_bear, 'opt_result_30')
        print(
            f'\nFull-bull (+5) vs Full-bear (-5) spread:\n'
            f'  WR15:     {wr15_bu - wr15_be:+5.2f}pp  (N={len(full_bull):,} / {len(full_bear):,})\n'
            f'  WR30(op): {wr30op_bu - wr30op_be:+5.2f}pp\n'
        )


if __name__ == '__main__':
    main()
