"""Phase D: stability quantification — does Ichimoku produce less week-over-week
churn than wadj?

The COHR case (Priority #7): a Monday weekly composite refresh pushed wadj from
+11.9 to −16.2 in a single recompute, driving overall from 86 to 52. The slow-
indicator hypothesis is that Ichimoku features (computed from completed weekly
bars, anchored to discrete events) should NOT produce that magnitude of churn.

Test: for each (symbol, date) peak, look up the same symbol's peak 5 trading
days earlier. Compute the indicator delta. Compare distributions:

  |Δwadj|              vs  |Δcloud_position|, |Δkijun_pct|
  Monday churn         vs  Tue-Fri churn (per Priority #7 finding)
  Score-zone breakdown (puts ≤25, neutral, calls ≥75)

Three possible outcomes:
  1. Ichimoku << wadj on churn  → ship is justified for stability AND alpha
  2. Ichimoku ≈ wadj on churn   → ship still produces alpha, doesn't fix whiplash
  3. Ichimoku >> wadj on churn  → don't ship, signal is unstable

Side effect: caches wadj for the 916K-peak min0 parquet to .cache/weekly_avwap/
for Phase C reuse.

Usage:
    python experiments/weekly_avwap/stability.py [parquet_name]
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.cache' / 'weekly_avwap'

PARQUET = sys.argv[1] if len(sys.argv) > 1 else 'calls_v39_1825d_min0.parquet'
WADJ_CACHE = CACHE / 'wadj_v39_1825d_min0.parquet'  # reusable for Phase C


# ---------------------------------------------------------------------------
# Wadj extraction (reusable cache)
# ---------------------------------------------------------------------------

def load_wadj_full_universe(av_id: int) -> pl.DataFrame:
    """Load w_adj from Score.weight_info for all peaks at this version.
    Cached to disk for Phase C reuse."""
    if WADJ_CACHE.exists():
        df = pl.read_parquet(WADJ_CACHE)
        print(f'[wadj] loaded from cache: {len(df):,} rows', flush=True)
        return df

    from database.trader_database import DB
    print(f'[wadj] extracting from DB (no cache, ~3 min)...', flush=True)
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT symbol, date, weight_info
        FROM scores
        WHERE version_id = {av_id}
          AND weight_info IS NOT NULL
          AND weight_info != ''
    """)
    syms, dates, wadjs, wcfs = [], [], [], []
    for sym, dt, wi in cur.fetchall():
        try:
            d = json.loads(wi) if isinstance(wi, str) else wi
            syms.append(sym)
            dates.append(dt.isoformat() if hasattr(dt, 'isoformat') else str(dt))
            wadjs.append(float(d.get('w_adj')) if d.get('w_adj') is not None else None)
            # wcf_lift only present when v27 fired — used for v27 reversal in Phase C
            wcfs.append(float(d.get('wcf_lift')) if d.get('wcf_lift') is not None else None)
        except Exception:
            continue

    df = pl.DataFrame({
        'symbol':   syms,
        'date':     dates,
        'w_adj':    wadjs,
        'wcf_lift': wcfs,
    })
    CACHE.mkdir(parents=True, exist_ok=True)
    df.write_parquet(WADJ_CACHE)
    print(f'[wadj] extracted {len(df):,} rows in {time.time()-t0:.1f}s, '
          f'cached → {WADJ_CACHE.name}', flush=True)
    return df


# ---------------------------------------------------------------------------
# Stability analysis
# ---------------------------------------------------------------------------

def compute_5bar_deltas(df: pl.DataFrame, columns: list[str]) -> pl.DataFrame:
    """For each (symbol, date), look up the same symbol 5 trading days earlier
    via shift(5).over('symbol'). Compute |Δ| for each requested column.

    Note: shift(5) over rows assumes consecutive trading days in the parquet.
    Gaps (holidays, missed scoring) cause minor under/over-counting but the
    aggregate distribution is what we care about.
    """
    df = df.sort(['symbol', 'date'])
    for c in columns:
        df = df.with_columns(
            pl.col(c).shift(5).over('symbol').alias(f'{c}_t5')
        )
        df = df.with_columns(
            (pl.col(c) - pl.col(f'{c}_t5')).abs().alias(f'abs_d_{c}')
        )
    return df


def main():
    # 1. Load the full-universe parquet (916K peaks, scores 0-100)
    pq = CACHE / PARQUET
    df = pl.read_parquet(pq)
    print(f'[load] parquet: {len(df):,} rows', flush=True)

    # Drop inf rows
    df = df.with_columns(
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct')
    )

    # 2. Attach wadj (cached load)
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    wadj_df = load_wadj_full_universe(av.id)
    df = df.join(wadj_df, on=['symbol', 'date'], how='left')
    n_with_wadj = df['w_adj'].drop_nulls().len()
    print(f'[joined] {n_with_wadj:,} of {len(df):,} have w_adj', flush=True)

    # 3. Compute 5-trading-day deltas on indicators
    print('[compute] 5-bar deltas...', flush=True)
    indicators = ['w_adj', 'cloud_position', 'price_vs_kijun_pct', 'overall']
    df = compute_5bar_deltas(df, indicators)

    # 4. Day-of-week
    df = df.with_columns(
        pl.col('date').str.to_date().dt.weekday().alias('dow')   # 1=Mon..7=Sun
    )

    # 5. Score zone (puts/neutral/calls)
    df = df.with_columns(
        pl.when(pl.col('overall') <= 25).then(pl.lit('put'))
          .when(pl.col('overall') >= 75).then(pl.lit('call'))
          .when((pl.col('overall') >= 70) & (pl.col('overall') < 75)).then(pl.lit('70-74'))
          .otherwise(pl.lit('neutral'))
          .alias('zone')
    )

    # ============================================================================
    # OVERALL CHURN (Mon vs Tue-Fri, all zones)
    # ============================================================================
    print('\n' + '=' * 95)
    print('PART 1 — Overall churn distribution by indicator')
    print('=' * 95)

    valid = df.filter(
        pl.col('abs_d_w_adj').is_not_null()
        & pl.col('abs_d_cloud_position').is_not_null()
        & pl.col('abs_d_price_vs_kijun_pct').is_not_null()
        & pl.col('abs_d_overall').is_not_null()
    )
    print(f'\nN={len(valid):,} (rows with both t and t-5 indicators present)\n')

    print(f'{"indicator":<25s} {"mean":>9s} {"median":>9s} {"p75":>9s} {"p90":>9s} '
          f'{"p99":>9s} {"max":>9s}')
    print('-' * 80)
    for ind in indicators:
        col = f'abs_d_{ind}'
        s = valid[col]
        print(
            f'{ind:<25s} '
            f'{s.mean():>9.3f} '
            f'{s.median():>9.3f} '
            f'{s.quantile(0.75):>9.3f} '
            f'{s.quantile(0.90):>9.3f} '
            f'{s.quantile(0.99):>9.3f} '
            f'{s.max():>9.3f}'
        )

    # ============================================================================
    # MONDAY VS TUE-FRI (Priority #7's load-bearing finding)
    # ============================================================================
    print('\n' + '=' * 95)
    print('PART 2 — Monday vs Tue-Fri churn (per Priority #7)')
    print('=' * 95)
    print('\n  Hypothesis: wadj should spike on Monday (weekly composite refresh).')
    print('  Slow indicators (cloud, kijun) should NOT show a Monday spike.\n')

    # Polars weekday(): 1=Mon..5=Fri
    mon = valid.filter(pl.col('dow') == 1)
    tue_fri = valid.filter((pl.col('dow') >= 2) & (pl.col('dow') <= 5))

    print(f'  Mon N    = {len(mon):,}')
    print(f'  Tue-Fri N = {len(tue_fri):,}\n')

    print(f'{"indicator":<25s} {"Mon mean":>10s} {"Tue-Fri mean":>13s} {"Mon/T-F ratio":>15s}')
    print('-' * 70)
    ratios = {}
    for ind in indicators:
        col = f'abs_d_{ind}'
        m = mon[col].mean()
        tf = tue_fri[col].mean()
        ratio = m / tf if tf > 0 else float('nan')
        ratios[ind] = ratio
        print(f'{ind:<25s} {m:>10.3f} {tf:>13.3f} {ratio:>14.3f}x')

    # ============================================================================
    # NORMALIZED COMPARISON (signals on different scales — use coefficient of variation)
    # ============================================================================
    print('\n' + '=' * 95)
    print('PART 3 — Normalized churn (mean |Δ| / std(indicator))')
    print('=' * 95)
    print('\n  Indicators are on different scales — normalize by their stdev to compare.')
    print('  Lower normalized churn = more stable indicator.\n')

    print(f'{"indicator":<25s} {"std":>9s} {"mean |Δ|":>10s} {"normalized":>11s}')
    print('-' * 60)
    norm_churn = {}
    for ind in indicators:
        if ind == 'overall':
            continue  # overall is downstream of all indicators
        col = f'abs_d_{ind}'
        std = valid[ind].std()
        mean_d = valid[col].mean()
        norm = mean_d / std if std > 0 else float('nan')
        norm_churn[ind] = norm
        print(f'{ind:<25s} {std:>9.3f} {mean_d:>10.3f} {norm:>11.3f}')

    # ============================================================================
    # ZONE-CONDITIONAL CHURN (do indicators behave differently in put/call zones?)
    # ============================================================================
    print('\n' + '=' * 95)
    print('PART 4 — Churn by score zone')
    print('=' * 95)
    print('\n  Whiplash matters most where dampener boundaries sit. Wadj boundary\n'
          '  is at -17 (v27 WCF gate); Ichimoku boundary is at cloud=0.\n')

    print(f'{"zone":<10s} {"N":>9s} '
          f'{"|Δwadj|":>10s} {"|Δcloud|":>10s} {"|Δkijun|":>10s} {"|Δoverall|":>11s}')
    print('-' * 70)
    for zone in ['put', '70-74', 'call', 'neutral']:
        sub = valid.filter(pl.col('zone') == zone)
        if len(sub) < 100:
            continue
        wa = sub['abs_d_w_adj'].mean()
        cl = sub['abs_d_cloud_position'].mean()
        kj = sub['abs_d_price_vs_kijun_pct'].mean()
        ov = sub['abs_d_overall'].mean()
        print(f'{zone:<10s} {len(sub):>9,} {wa:>10.3f} {cl:>10.3f} {kj:>10.3f} {ov:>10.3f}')

    # ============================================================================
    # VERDICT
    # ============================================================================
    print('\n' + '=' * 95)
    print('VERDICT')
    print('=' * 95)
    wadj_mon_ratio  = ratios['w_adj']
    cloud_mon_ratio = ratios['cloud_position']
    kijun_mon_ratio = ratios['price_vs_kijun_pct']
    score_mon_ratio = ratios['overall']

    wadj_norm  = norm_churn['w_adj']
    cloud_norm = norm_churn['cloud_position']
    kijun_norm = norm_churn['price_vs_kijun_pct']

    print(f'\n  Monday/Tue-Fri ratio (whiplash concentration):')
    print(f'    wadj:           {wadj_mon_ratio:.3f}x  '
          f'{"(>1.2 confirms Priority #7 spike)" if wadj_mon_ratio > 1.2 else "(no notable spike)"}')
    print(f'    cloud_position: {cloud_mon_ratio:.3f}x')
    print(f'    kijun_pct:      {kijun_mon_ratio:.3f}x')
    print(f'    overall score:  {score_mon_ratio:.3f}x')
    print()
    print(f'  Normalized churn (mean |Δ| / std):')
    print(f'    wadj:           {wadj_norm:.3f}')
    print(f'    cloud_position: {cloud_norm:.3f}  '
          f'(ratio vs wadj: {cloud_norm/wadj_norm:.2f}x)')
    print(f'    kijun_pct:      {kijun_norm:.3f}  '
          f'(ratio vs wadj: {kijun_norm/wadj_norm:.2f}x)')

    print('\n  Decision:')
    if cloud_norm < wadj_norm * 0.7 and kijun_norm < wadj_norm * 0.7:
        print('    ✓ STABILITY WIN: both Ichimoku features are >30% more stable than wadj.')
        print('    → Phase C ship is justified for STABILITY + alpha. Proceed.')
    elif cloud_norm < wadj_norm and kijun_norm < wadj_norm:
        print('    ~ MARGINAL: Ichimoku features are modestly more stable than wadj.')
        print('    → Phase C ship still produces alpha; partial whiplash improvement.')
    elif cloud_norm > wadj_norm * 1.2 or kijun_norm > wadj_norm * 1.2:
        print('    ✗ STABILITY LOSS: Ichimoku features are LESS stable than wadj.')
        print('    → DO NOT proceed to Phase C. Signal is unstable.')
    else:
        print('    ~ NEUTRAL: Ichimoku features are comparable to wadj on stability.')
        print('    → Phase C ship justified for ALPHA but not for whiplash fix.')

    if cloud_mon_ratio < wadj_mon_ratio - 0.1 or kijun_mon_ratio < wadj_mon_ratio - 0.1:
        print(f'\n  Mon-spike: Ichimoku does NOT inherit wadj\'s Monday whiplash.')
    elif cloud_mon_ratio > wadj_mon_ratio:
        print(f'\n  Mon-spike: Ichimoku has SIMILAR or WORSE Monday churn pattern.')


if __name__ == '__main__':
    main()
