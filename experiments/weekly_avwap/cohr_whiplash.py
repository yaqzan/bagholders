"""COHR-class 1-bar whiplash test (Priority #7's load-bearing question).

Quantifies day-over-day score volatility specifically on Monday refreshes
(Friday close → Monday close), comparing v43 production scores to the same
peaks under Phase E (or Phase G winner) Ichimoku dampener.

Hypothesis: weekly composite refresh on Monday produces large |Δoverall|
jumps in v43 (the COHR 86→52 case). Ichimoku dampener — being driven by a
SLOW indicator (last completed weekly bar at scoring_date - 7d) — should
NOT inherit the same Monday refresh volatility for peaks where Ichimoku fires.

Methodology:
  1. Load v43 parquet with overall + indicator features
  2. For each (symbol, date) pair where date is Monday and prior Friday exists,
     compute |Δoverall_v43| = |overall(Mon) - overall(Fri)|
  3. Apply Ichimoku dampener to recompute overall_new for Mon and Fri
  4. Compute |Δoverall_new| = |overall_new(Mon) - overall_new(Fri)|
  5. Compare distributions: Mon Δ vs Tue-Fri Δ; v43 vs new

Pass criteria:
  - On affected cohort (peaks where Ichimoku fires): mean |Δoverall_new(Mon)|
    is lower than mean |Δoverall_v43(Mon)| (any reduction = pass)
  - Tue-Fri unaffected (the dampener doesn't introduce new churn elsewhere)
"""
from __future__ import annotations

import io
import math
import sys
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.cache' / 'weekly_avwap'

# Phase H Rank #3 ship config (production defaults)
CONFIG = {
    'GATE_CALL_LO':     int(sys.argv[1]) if len(sys.argv) > 1 else 69,
    'GATE_CALL_HI':     int(sys.argv[2]) if len(sys.argv) > 2 else 90,
    'K_CALL':           float(sys.argv[3]) if len(sys.argv) > 3 else 0.359,
    'KIJ_SAT_CALL':     float(sys.argv[4]) if len(sys.argv) > 4 else 18.4,
    'LIFT_TARGET_CALL': float(sys.argv[5]) if len(sys.argv) > 5 else 63.8,
    'GATE_PUT_LO':      10,
    'GATE_PUT_HI':      27,
    'K_PUT':            0.278,
    'KIJ_SAT_PUT':      8.8,
    'LIFT_TARGET_PUT':  33.4,
    'RAMP_SHAPE':       'linear',
    'K_CALL_POWER':     2.68,  # Phase H asymmetric-K
}


def apply_ichimoku(df: pl.DataFrame, p: dict, score_col: str = 'overall') -> pl.DataFrame:
    """Apply Ichimoku dampener; supports both Phase E (uniform K) and Phase G (power-law K)."""
    if p['RAMP_SHAPE'] == 'log':
        ramp = lambda x, sat: ((x.clip(0.0, None) + 1).log() / math.log(1 + sat)).clip(0.0, 1.0)
    else:
        ramp = lambda x, sat: (x.clip(0.0, None) / sat).clip(0.0, 1.0)

    ind_dist = (-pl.col('price_vs_kijun_pct')).clip(0.0, None)

    # Call side — power-law if K_CALL_POWER != 1.0
    if abs(p['K_CALL_POWER'] - 1.0) > 0.01:
        score_range = max(1, p['GATE_CALL_HI'] - p['GATE_CALL_LO'])
        score_norm = ((pl.col(score_col) - p['GATE_CALL_LO']) / score_range).clip(0.0, None)
        k_eff = p['K_CALL'] * score_norm.pow(p['K_CALL_POWER'])
        ind_grad = ramp(ind_dist, p['KIJ_SAT_CALL'])
        call_d_full = -k_eff * ind_grad * (pl.col(score_col) - p['LIFT_TARGET_CALL'])
        call_d = pl.when(pl.col(score_col) >= p['GATE_CALL_LO']).then(call_d_full).otherwise(0.0)
    else:
        call_sg = ramp(pl.col(score_col) - p['GATE_CALL_LO'],
                       p['GATE_CALL_HI'] - p['GATE_CALL_LO'])
        call_ig = ramp(ind_dist, p['KIJ_SAT_CALL'])
        call_d = -p['K_CALL'] * call_sg * call_ig * (pl.col(score_col) - p['LIFT_TARGET_CALL'])

    # Put side
    put_sg = ramp(p['GATE_PUT_HI'] - pl.col(score_col),
                  p['GATE_PUT_HI'] - p['GATE_PUT_LO'])
    put_ig = ramp(ind_dist, p['KIJ_SAT_PUT'])
    put_d = p['K_PUT'] * put_sg * put_ig * (p['LIFT_TARGET_PUT'] - pl.col(score_col))

    return df.with_columns(
        (pl.col(score_col) + call_d.fill_null(0) + put_d.fill_null(0))
        .clip(0.0, 100.0)
        .alias('overall_new')
    )


def main():
    pq = CACHE / 'calls_v43_1825d_min0.parquet'
    df = pl.read_parquet(pq)
    df = df.with_columns(
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct')
    )
    df = df.filter(pl.col('price_vs_kijun_pct').is_not_null()
                   & pl.col('price_vs_kijun_pct').is_finite())

    print(f'[load] v43 parquet: {len(df):,} rows', flush=True)
    print(f'[config] CALL gate={CONFIG["GATE_CALL_LO"]}-{CONFIG["GATE_CALL_HI"]} '
          f'K={CONFIG["K_CALL"]:.3f} POWER={CONFIG["K_CALL_POWER"]:.2f} '
          f'sat={CONFIG["KIJ_SAT_CALL"]:.1f} target={CONFIG["LIFT_TARGET_CALL"]:.1f}', flush=True)

    # Apply Ichimoku to v43 scores → get overall_new
    df = apply_ichimoku(df, CONFIG, score_col='overall')

    # Add day-of-week
    df = df.with_columns(
        pl.col('date').str.to_date().dt.weekday().alias('dow')   # 1=Mon ... 5=Fri
    )

    # Sort and compute prior-trading-day score per symbol
    df = df.sort(['symbol', 'date']).with_columns([
        pl.col('overall').shift(1).over('symbol').alias('overall_prev'),
        pl.col('overall_new').shift(1).over('symbol').alias('overall_new_prev'),
        pl.col('date').shift(1).over('symbol').alias('date_prev'),
    ])

    # Filter: rows with a valid prior, and the prior is at most 5 calendar days back
    # (so we capture Friday→Monday correctly even though calendar gap is 3 days)
    df = df.with_columns(
        ((pl.col('date').str.to_date() - pl.col('date_prev').str.to_date())
         .dt.total_days())
        .alias('cal_gap')
    )
    df = df.filter(
        pl.col('overall_prev').is_not_null()
        & pl.col('overall_new_prev').is_not_null()
        & (pl.col('cal_gap') >= 1)
        & (pl.col('cal_gap') <= 5)
    )

    df = df.with_columns([
        (pl.col('overall') - pl.col('overall_prev')).abs().alias('abs_d_v43'),
        (pl.col('overall_new') - pl.col('overall_new_prev')).abs().alias('abs_d_new'),
    ])
    print(f'[filter] {len(df):,} day-pairs with valid priors', flush=True)

    # ------------------------------------------------------------------
    # Mon vs Tue-Fri churn comparison
    # ------------------------------------------------------------------
    print('\n' + '=' * 90)
    print('Day-of-week churn comparison: |Δoverall(t) - overall(t-1)|')
    print('  v43 = current production (no Ichimoku)')
    print('  new = v43 with Phase E/G Ichimoku dampener applied additively')
    print('=' * 90)

    print(f'\n{"day":<10s} {"N":>9s} {"v43 mean |Δ|":>14s} {"new mean |Δ|":>14s} '
          f'{"Δ change":>10s} {"v43 p99":>10s} {"new p99":>10s}')
    print('-' * 80)

    # Polars weekday: 1=Mon, 5=Fri
    for label, dow in [('Mon', 1), ('Tue', 2), ('Wed', 3), ('Thu', 4), ('Fri', 5)]:
        sub = df.filter(pl.col('dow') == dow)
        if len(sub) < 100:
            continue
        m_v43 = float(sub['abs_d_v43'].mean())
        m_new = float(sub['abs_d_new'].mean())
        p99_v43 = float(sub['abs_d_v43'].quantile(0.99))
        p99_new = float(sub['abs_d_new'].quantile(0.99))
        delta = m_new - m_v43
        print(f'{label:<10s} {len(sub):>9,} {m_v43:>13.3f}  {m_new:>13.3f}  '
              f'{delta:>+9.3f}  {p99_v43:>9.2f}  {p99_new:>9.2f}')

    # ------------------------------------------------------------------
    # Cohort analysis — focus on peaks where Ichimoku actually fires
    # ------------------------------------------------------------------
    print('\n' + '=' * 90)
    print('Cohort: peaks where Ichimoku changes score (overall != overall_new)')
    print('=' * 90)

    affected = df.filter(pl.col('overall') != pl.col('overall_new'))
    print(f'  N affected day-pairs: {len(affected):,}  '
          f'({len(affected)/len(df)*100:.2f}% of total)')

    if len(affected) >= 100:
        for label, dow in [('Mon', 1), ('Tue-Fri', None)]:
            if dow is None:
                sub = affected.filter((pl.col('dow') >= 2) & (pl.col('dow') <= 5))
            else:
                sub = affected.filter(pl.col('dow') == dow)
            if len(sub) < 30:
                continue
            m_v43 = float(sub['abs_d_v43'].mean())
            m_new = float(sub['abs_d_new'].mean())
            print(f'  {label:<10s}  N={len(sub):>5,}  '
                  f'v43 mean |Δ|={m_v43:.2f}  new mean |Δ|={m_new:.2f}  '
                  f'reduction={m_v43-m_new:+.2f}pp')

    # ------------------------------------------------------------------
    # Verdict
    # ------------------------------------------------------------------
    print('\n' + '=' * 90)
    print('VERDICT')
    print('=' * 90)
    mon_v43 = float(df.filter(pl.col('dow') == 1)['abs_d_v43'].mean())
    mon_new = float(df.filter(pl.col('dow') == 1)['abs_d_new'].mean())
    tuefri_v43 = float(df.filter((pl.col('dow') >= 2) & (pl.col('dow') <= 5))['abs_d_v43'].mean())
    tuefri_new = float(df.filter((pl.col('dow') >= 2) & (pl.col('dow') <= 5))['abs_d_new'].mean())

    mon_red = mon_v43 - mon_new
    tuefri_red = tuefri_v43 - tuefri_new

    print(f'\n  Monday churn:      v43 {mon_v43:.3f}  →  new {mon_new:.3f}  ({mon_red:+.3f}pp)')
    print(f'  Tue-Fri churn:     v43 {tuefri_v43:.3f}  →  new {tuefri_new:.3f}  ({tuefri_red:+.3f}pp)')
    print(f'  Mon/Tue-Fri ratio: v43 {mon_v43/tuefri_v43:.2f}x  →  new {mon_new/tuefri_new:.2f}x')

    if mon_red > 0 and abs(tuefri_red) < 0.1:
        print('\n  ✓ PASS: Monday churn reduced; Tue-Fri unaffected.')
    elif mon_red > 0:
        print('\n  ~ PARTIAL: Monday churn reduced; Tue-Fri also changed.')
    else:
        print('\n  ✗ FAIL: Monday churn not improved.')


if __name__ == '__main__':
    main()
