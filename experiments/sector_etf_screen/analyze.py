"""Analysis: SPY backdrop cohort + static-sector baseline + sector ETF
time-varying cohort + ETF-as-breadth correlation, all on v43.

Phase 0  — SPY % above EMA50 cohort across all signals (market backdrop)
Phase 0.5 — Static-sector-label baseline (validates user's intuition that
            sector separation has any predictive variance at v43)
Phase 1  — Sector ETF cohort: per-stock-sector ETF state vs outcome
Phase 1b — Counter-trend cohort: signals that fight the sector ETF
Phase 2  — ETF-as-breadth basket correlation with production breadth_score

Outputs: cohort tables to stdout + structured CSVs in .cache/sector_etf_screen/
"""
from __future__ import annotations
import io, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LOCAL_CACHE = ROOT / '.cache' / 'sector_etf_screen'
COHORT_PQ = LOCAL_CACHE / 'cohort_v43_etf.parquet'

# Cumulative tier groupings for cohort rollups
CALL_TIERS = ['70+', '75+', '80+', '85+', '90+']
PUT_TIERS = ['<=25', '<=20', '<=15', '<=10', '<=5']


def tier_filter(df: pl.DataFrame, side: str, tier: str) -> pl.DataFrame:
    if side == 'CALL':
        thresh = int(tier.replace('+', ''))
        return df.filter((pl.col('signal_type') == 'CALL') & (pl.col('overall') >= thresh))
    else:
        thresh = int(tier.replace('<=', ''))
        return df.filter((pl.col('signal_type') == 'PUT') & (pl.col('overall') <= thresh))


def z_one_prop(p_cohort: float, p_base: float, n_cohort: int) -> float:
    """One-proportion z-test for miss rate cohort vs baseline.
    Returns z where positive z means cohort UNDERPERFORMS baseline (more misses)."""
    if n_cohort == 0 or p_base == 0 or p_base == 1:
        return float('nan')
    se = math.sqrt(p_base * (1 - p_base) / n_cohort)
    if se == 0:
        return float('nan')
    return (p_cohort - p_base) / se


def cohort_zscore(df: pl.DataFrame, cohort_col: str, label: str,
                  outcome_col: str = 'is_miss') -> list[dict]:
    """For each (signal_type, tier, cohort_value), compute z-test against
    the same (signal_type, tier) baseline."""
    results = []
    for side, tiers in [('CALL', CALL_TIERS), ('PUT', PUT_TIERS)]:
        for tier in tiers:
            base = tier_filter(df, side, tier)
            if len(base) < 50:
                continue
            base_n = len(base)
            base_p = base[outcome_col].mean()
            base_tp = 1 - base_p
            # Per cohort value
            cohort_vals = base[cohort_col].drop_nulls().unique().to_list()
            for v in cohort_vals:
                sub = base.filter(pl.col(cohort_col) == v)
                n = len(sub)
                if n < 50:
                    continue
                p = sub[outcome_col].mean()
                tp = 1 - p
                z = z_one_prop(p, base_p, n)
                results.append({
                    'cohort_label': label,
                    'side': side,
                    'tier': tier,
                    'cohort_value': str(v),
                    'n_cohort': n,
                    'tp_cohort': round(tp * 100, 2),
                    'tp_base': round(base_tp * 100, 2),
                    'delta_pp': round((tp - base_tp) * 100, 2),
                    'lift_miss': round(p / base_p, 3) if base_p > 0 else float('nan'),
                    'z': round(z, 2),
                    'base_n': base_n,
                })
    return results


def bucketize_pct_ema(col: str, df: pl.DataFrame, prefix: str = 'b') -> pl.DataFrame:
    """Add a 5-bucket coarse bin on % above/below EMA50:
       very_low (< -5), low (-5..-1), neutral (-1..+1), high (1..5), very_high (> 5)"""
    out = df.with_columns(
        pl.when(pl.col(col).is_null()).then(pl.lit(None))
          .when(pl.col(col) <= -5).then(pl.lit(f'{prefix}1_vlow'))
          .when(pl.col(col) <= -1).then(pl.lit(f'{prefix}2_low'))
          .when(pl.col(col) <= 1).then(pl.lit(f'{prefix}3_neut'))
          .when(pl.col(col) <= 5).then(pl.lit(f'{prefix}4_high'))
          .otherwise(pl.lit(f'{prefix}5_vhigh')).alias(f'{col}_bucket')
    )
    return out


def print_top_significant(rows: list[dict], n: int = 20):
    sorted_rows = sorted([r for r in rows if abs(r['z']) >= 1.96],
                        key=lambda r: -abs(r['z']))
    if not sorted_rows:
        print('  (no cells with |z| >= 1.96)')
        return
    print(f'  Top {min(n, len(sorted_rows))} significant cells (|z| >= 1.96):')
    print(f'  {"side":4s} {"tier":5s} {"cohort":15s} {"N":>6s} {"TP%":>6s} '
          f'{"base%":>6s} {"Δpp":>6s} {"z":>6s}')
    for r in sorted_rows[:n]:
        print(f'  {r["side"]:4s} {r["tier"]:5s} {r["cohort_value"]:15s} '
              f'{r["n_cohort"]:>6d} {r["tp_cohort"]:>6.2f} {r["tp_base"]:>6.2f} '
              f'{r["delta_pp"]:>+6.2f} {r["z"]:>+6.2f}')


def phase0_spy(df: pl.DataFrame):
    print('=' * 70)
    print('Phase 0 — SPY backdrop cohort (% above EMA50 at signal date)')
    print('=' * 70)
    df = bucketize_pct_ema('SPY_pct_ema50', df, prefix='spy')
    rows = cohort_zscore(df, 'SPY_pct_ema50_bucket', 'SPY_pct_ema50')
    print_top_significant(rows, n=15)
    pl.DataFrame(rows).write_csv(LOCAL_CACHE / 'phase0_spy_cohorts.csv')

    # Also print full table for transparency on top tiers
    print('\n  Full table for 75+ CALL and <=15 PUT:')
    for r in [r for r in rows if r['tier'] in ('75+', '<=15')]:
        print(f'  {r["side"]:4s} {r["tier"]:5s} {r["cohort_value"]:15s} '
              f'N={r["n_cohort"]:>5d} TP={r["tp_cohort"]:>6.2f} '
              f'base={r["tp_base"]:>6.2f} Δ={r["delta_pp"]:>+6.2f} z={r["z"]:>+6.2f}')


def phase05_static_sector(df: pl.DataFrame):
    print('\n' + '=' * 70)
    print('Phase 0.5 — Static-sector baseline (re-validation on v43)')
    print('=' * 70)
    rows = cohort_zscore(df, 'sector', 'static_sector')
    print_top_significant(rows, n=15)
    pl.DataFrame(rows).write_csv(LOCAL_CACHE / 'phase05_static_sector.csv')


def phase1_sector_etf(df: pl.DataFrame):
    print('\n' + '=' * 70)
    print('Phase 1 — Sector ETF % above EMA50 at signal date')
    print('=' * 70)
    sub = df.filter(pl.col('sec_etf_pct_ema50').is_not_null())
    print(f'  filtered to {len(sub):,} rows with sector ETF coverage')
    sub = bucketize_pct_ema('sec_etf_pct_ema50', sub, prefix='se')
    rows = cohort_zscore(sub, 'sec_etf_pct_ema50_bucket', 'sec_etf_pct_ema50')
    print_top_significant(rows, n=15)
    pl.DataFrame(rows).write_csv(LOCAL_CACHE / 'phase1_sec_etf_pctema.csv')

    # Also test by sector ETF RSI
    print('\n  By sector ETF RSI:')
    sub = sub.with_columns(
        pl.when(pl.col('sec_etf_rsi').is_null()).then(pl.lit(None))
          .when(pl.col('sec_etf_rsi') < 30).then(pl.lit('a_lt30'))
          .when(pl.col('sec_etf_rsi') < 45).then(pl.lit('b_30_45'))
          .when(pl.col('sec_etf_rsi') < 55).then(pl.lit('c_45_55'))
          .when(pl.col('sec_etf_rsi') < 70).then(pl.lit('d_55_70'))
          .otherwise(pl.lit('e_gt70')).alias('sec_etf_rsi_bucket')
    )
    rows2 = cohort_zscore(sub, 'sec_etf_rsi_bucket', 'sec_etf_rsi')
    print_top_significant(rows2, n=15)
    pl.DataFrame(rows2).write_csv(LOCAL_CACHE / 'phase1_sec_etf_rsi.csv')


def phase1b_counter_trend(df: pl.DataFrame):
    """Counter-trend cohort: signals fighting their sector ETF.
    Define alignment as sign(stock_signal_direction) vs sign(sec_etf_pct_ema50).
       call signal + sec_etf above EMA50 = ALIGNED-CALL
       call signal + sec_etf below EMA50 = COUNTER-CALL
       put  signal + sec_etf below EMA50 = ALIGNED-PUT
       put  signal + sec_etf above EMA50 = COUNTER-PUT
    """
    print('\n' + '=' * 70)
    print('Phase 1b — Counter-trend cohort (signal vs sector ETF direction)')
    print('=' * 70)
    sub = df.filter(pl.col('sec_etf_pct_ema50').is_not_null())
    sub = sub.with_columns(
        pl.when((pl.col('signal_type') == 'CALL') & (pl.col('sec_etf_pct_ema50') > 0)).then(pl.lit('ALIGN_CALL'))
          .when((pl.col('signal_type') == 'CALL') & (pl.col('sec_etf_pct_ema50') <= 0)).then(pl.lit('COUNTER_CALL'))
          .when((pl.col('signal_type') == 'PUT') & (pl.col('sec_etf_pct_ema50') < 0)).then(pl.lit('ALIGN_PUT'))
          .when((pl.col('signal_type') == 'PUT') & (pl.col('sec_etf_pct_ema50') >= 0)).then(pl.lit('COUNTER_PUT'))
          .otherwise(None).alias('ct_align')
    )
    rows = cohort_zscore(sub, 'ct_align', 'counter_trend')
    print_top_significant(rows, n=15)
    pl.DataFrame(rows).write_csv(LOCAL_CACHE / 'phase1b_counter_trend.csv')


def phase2_etf_breadth(df: pl.DataFrame):
    """Build candidate ETF-derived breadth signal and correlate with production breadth_score.

    Candidate breadth A: % of {SPY, QQQ, IWM, XLF, XLE, XLP, XLU, XLC, SMH} above EMA50
    Candidate breadth B: same but weighted by ETF (broad gets 1.0, sector gets 0.5)
    """
    print('\n' + '=' * 70)
    print('Phase 2 — ETF-as-breadth comparison')
    print('=' * 70)
    BASKET = ['SPY', 'QQQ', 'IWM', 'XLF', 'XLE', 'XLP', 'XLU', 'XLC', 'SMH']
    avail_cols = [f'{e}_pct_ema50' for e in BASKET if f'{e}_pct_ema50' in df.columns]
    print(f'  basket: {len(avail_cols)} ETFs available: {[c.split("_")[0] for c in avail_cols]}')

    # Per-row: count of ETFs in basket above EMA50, divided by available count
    expr_pos = pl.lit(0.0)
    expr_known = pl.lit(0.0)
    for c in avail_cols:
        expr_pos = expr_pos + pl.when(pl.col(c) > 0).then(1.0).otherwise(0.0)
        expr_known = expr_known + pl.when(pl.col(c).is_not_null()).then(1.0).otherwise(0.0)

    out = df.with_columns([
        expr_pos.alias('etf_pos'),
        expr_known.alias('etf_known'),
    ])
    out = out.with_columns(
        (pl.col('etf_pos') / pl.col('etf_known') * 100).alias('etf_breadth_pct')
    )

    # Get unique (date, breadth_score, etf_breadth_pct) triples for correlation
    daily = out.group_by('date').agg([
        pl.col('breadth_score').first(),
        pl.col('etf_breadth_pct').first(),
    ]).filter(
        pl.col('breadth_score').is_not_null() & pl.col('etf_breadth_pct').is_not_null()
    )

    if len(daily) > 0:
        corr = daily.select(pl.corr('breadth_score', 'etf_breadth_pct')).item()
        print(f'  daily correlation (production vs ETF-derived breadth, N={len(daily):,}): {corr:.4f}')

        # Distribution comparison
        print(f'\n  production breadth_score: mean={daily["breadth_score"].mean():.1f} '
              f'std={daily["breadth_score"].std():.1f}')
        print(f'  ETF-derived breadth (% above EMA50): mean={daily["etf_breadth_pct"].mean():.1f} '
              f'std={daily["etf_breadth_pct"].std():.1f}')

        # Save daily comparison
        daily.sort('date').write_csv(LOCAL_CACHE / 'phase2_breadth_daily.csv')

    # Cohort lift on F3F-relevant cells (low/high tails of each)
    print('\n  Cohort lift comparison on F3F-relevant breadth tails:')
    for label, col in [('production_breadth', 'breadth_score'),
                       ('etf_breadth_pct', 'etf_breadth_pct')]:
        out = out.with_columns(
            pl.when(pl.col(col).is_null()).then(pl.lit(None))
              .when(pl.col(col) <= 30).then(pl.lit(f'{label}_lo'))
              .when(pl.col(col) <= 50).then(pl.lit(f'{label}_mid_lo'))
              .when(pl.col(col) <= 75).then(pl.lit(f'{label}_mid_hi'))
              .otherwise(pl.lit(f'{label}_hi')).alias(f'{label}_bucket')
        )
        rows = cohort_zscore(out, f'{label}_bucket', label)
        sig = [r for r in rows if abs(r['z']) >= 1.96 and r['tier'] in ('75+', '<=25')]
        if sig:
            print(f'\n  {label}:')
            for r in sorted(sig, key=lambda r: -abs(r['z']))[:8]:
                print(f'    {r["side"]:4s} {r["tier"]:5s} {r["cohort_value"]:25s} '
                      f'N={r["n_cohort"]:>5d} TP={r["tp_cohort"]:>6.2f} '
                      f'Δ={r["delta_pp"]:>+6.2f} z={r["z"]:>+6.2f}')


def main():
    if not COHORT_PQ.exists():
        raise RuntimeError(f'Missing {COHORT_PQ} — run build_features.py')
    df = pl.read_parquet(COHORT_PQ)
    print(f'[load] {len(df):,} rows, date range {df["date"].min()} -> {df["date"].max()}')
    print(f'[load] CALL N={len(df.filter(pl.col("signal_type")=="CALL")):,}, '
          f'PUT N={len(df.filter(pl.col("signal_type")=="PUT")):,}')
    print(f'[load] holdout cutoff: 2026-05-15')
    print()

    phase0_spy(df)
    phase05_static_sector(df)
    phase1_sector_etf(df)
    phase1b_counter_trend(df)
    phase2_etf_breadth(df)

    print('\n[done] CSVs written to .cache/sector_etf_screen/')


if __name__ == '__main__':
    main()
