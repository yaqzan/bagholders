"""Cohort lift/z-score mining over (sector × mcap × signal × score_bucket).

Question: do misses (SL or EXP) cluster on identifiable structural axes
(sector, market cap, industry) within the v39 score universe?

Methodology:
  - Baseline cohort: same (signal_type × tier_group), e.g. all "CALL × 75+"
  - Subcohort: baseline ∩ (sector=X) or (mcap_bin=X)
  - lift = miss_rate_subcohort / miss_rate_baseline (>1 = worse than peers)
  - z = (miss_rate - baseline_p) / sqrt(baseline_p*(1-baseline_p)/N)
  - Filter N >= 50 to avoid noise

Outputs:
  - sector_cohorts.csv       — sector × signal × tier_group ranked by |z|
  - mcap_cohorts.csv         — mcap_bin × signal × tier_group ranked by |z|
  - joint_cohorts.csv        — sector × mcap_bin × signal × tier_group (top |z|)
  - industry_cohorts.csv     — industry × signal (top concentrations only, N>=80)

Reads .cache/sector_mcap_cohort/cohort_v39_3650.parquet.
"""
from __future__ import annotations
import io, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
LOCAL_CACHE = ROOT / '.cache' / 'sector_mcap_cohort'

# Tier groups for cumulative analysis
CALL_TIERS = ['95+', '90+', '85+', '80+', '75+', '70+']
PUT_TIERS = ['<=5', '<=10', '<=15', '<=20', '<=25']

MIN_N_PRIMARY = 50    # sector x tier
MIN_N_JOINT = 30      # sector x mcap x tier (sparser)
MIN_N_INDUSTRY = 80   # industry too sparse — gate higher


def add_cumulative_tier(df: pl.DataFrame) -> pl.DataFrame:
    """Add cumulative tier columns: matches if score >= threshold (calls) or <= threshold (puts)."""
    out = df
    # Each cumulative tier is a boolean — let us compute mean(is_miss) across qualifying signals
    # Simpler: group_by (signal, tier_label) where tier_label is a list. Use unpivot.
    # We'll just compute per discrete bin and aggregate cumulatively when rolling up.
    return out


def compute_baseline(df: pl.DataFrame, sig: str, tier_filter: pl.Expr) -> tuple[float, int]:
    """Baseline miss-rate for (signal × tier_filter) across ALL stocks."""
    sub = df.filter((pl.col('signal_type') == sig) & tier_filter)
    n = sub.height
    if n == 0:
        return float('nan'), 0
    miss_rate = sub.select(pl.col('is_miss').mean()).item()
    return float(miss_rate), n


def cohort_z(p_sub: float, n_sub: int, p_base: float) -> float:
    """One-proportion z statistic vs baseline p."""
    if n_sub < 1 or p_base <= 0 or p_base >= 1:
        return 0.0
    se = math.sqrt(p_base * (1 - p_base) / n_sub)
    if se == 0:
        return 0.0
    return (p_sub - p_base) / se


def tier_filters() -> list[tuple[str, str, pl.Expr]]:
    """Yield (signal_type, label, polars expression) for each tier."""
    out = []
    for thr in [95, 90, 85, 80, 75, 70]:
        out.append(('CALL', f'{thr}+', pl.col('overall') >= thr))
    for thr in [5, 10, 15, 20, 25]:
        out.append(('PUT', f'<={thr}', pl.col('overall') <= thr))
    return out


def analyze_sector(df: pl.DataFrame) -> pl.DataFrame:
    """Sector × signal × tier cohort analysis."""
    rows = []
    for sig, tier_label, tier_expr in tier_filters():
        p_base, n_base = compute_baseline(df, sig, tier_expr)
        if n_base < MIN_N_PRIMARY:
            continue
        # Per sector
        sub_all = df.filter((pl.col('signal_type') == sig) & tier_expr)
        sectors = sub_all.select('sector').unique().to_series().to_list()
        for sec in sectors:
            sub = sub_all.filter(pl.col('sector') == sec)
            n = sub.height
            if n < MIN_N_PRIMARY:
                continue
            p = sub.select(pl.col('is_miss').mean()).item()
            tp = 1.0 - p
            tp_base = 1.0 - p_base
            z = cohort_z(p, n, p_base)
            lift = p / p_base if p_base > 0 else float('nan')
            rows.append({
                'signal': sig,
                'tier': tier_label,
                'sector': sec,
                'N': n,
                'tp_pct': round(tp * 100, 2),
                'tp_base_pct': round(tp_base * 100, 2),
                'tp_delta_pp': round((tp - tp_base) * 100, 2),
                'miss_lift': round(lift, 3),
                'z': round(z, 2),
                'baseline_n': n_base,
            })
    out = pl.DataFrame(rows)
    return out.sort([pl.col('z').abs()], descending=True)


def analyze_mcap(df: pl.DataFrame) -> pl.DataFrame:
    """Mcap_bin × signal × tier cohort analysis."""
    rows = []
    for sig, tier_label, tier_expr in tier_filters():
        p_base, n_base = compute_baseline(df, sig, tier_expr)
        if n_base < MIN_N_PRIMARY:
            continue
        sub_all = df.filter((pl.col('signal_type') == sig) & tier_expr)
        bins = sub_all.select('mcap_bin').unique().to_series().to_list()
        for b in bins:
            sub = sub_all.filter(pl.col('mcap_bin') == b)
            n = sub.height
            if n < MIN_N_PRIMARY:
                continue
            p = sub.select(pl.col('is_miss').mean()).item()
            tp = 1.0 - p
            tp_base = 1.0 - p_base
            z = cohort_z(p, n, p_base)
            lift = p / p_base if p_base > 0 else float('nan')
            rows.append({
                'signal': sig,
                'tier': tier_label,
                'mcap_bin': b,
                'N': n,
                'tp_pct': round(tp * 100, 2),
                'tp_base_pct': round(tp_base * 100, 2),
                'tp_delta_pp': round((tp - tp_base) * 100, 2),
                'miss_lift': round(lift, 3),
                'z': round(z, 2),
                'baseline_n': n_base,
            })
    out = pl.DataFrame(rows)
    return out.sort([pl.col('z').abs()], descending=True)


def analyze_joint(df: pl.DataFrame) -> pl.DataFrame:
    """Sector × mcap_bin × signal × tier (looser N gate)."""
    rows = []
    for sig, tier_label, tier_expr in tier_filters():
        p_base, n_base = compute_baseline(df, sig, tier_expr)
        if n_base < MIN_N_PRIMARY:
            continue
        sub_all = df.filter((pl.col('signal_type') == sig) & tier_expr)
        for sec in sub_all.select('sector').unique().to_series().to_list():
            for b in sub_all.select('mcap_bin').unique().to_series().to_list():
                sub = sub_all.filter((pl.col('sector') == sec) & (pl.col('mcap_bin') == b))
                n = sub.height
                if n < MIN_N_JOINT:
                    continue
                p = sub.select(pl.col('is_miss').mean()).item()
                tp = 1.0 - p
                tp_base = 1.0 - p_base
                z = cohort_z(p, n, p_base)
                lift = p / p_base if p_base > 0 else float('nan')
                rows.append({
                    'signal': sig,
                    'tier': tier_label,
                    'sector': sec,
                    'mcap_bin': b,
                    'N': n,
                    'tp_pct': round(tp * 100, 2),
                    'tp_base_pct': round(tp_base * 100, 2),
                    'tp_delta_pp': round((tp - tp_base) * 100, 2),
                    'miss_lift': round(lift, 3),
                    'z': round(z, 2),
                    'baseline_n': n_base,
                })
    out = pl.DataFrame(rows)
    return out.sort([pl.col('z').abs()], descending=True)


def analyze_industry(df: pl.DataFrame) -> pl.DataFrame:
    """Industry × signal × tier (no tier subdivision since N too small for most)."""
    rows = []
    # Just look at signal_type level (no tier subdivision)
    for sig in ['CALL', 'PUT']:
        if sig == 'CALL':
            tier_filter = pl.col('overall') >= 75
            tier_label = '75+'
        else:
            tier_filter = pl.col('overall') <= 25
            tier_label = '<=25'
        p_base, n_base = compute_baseline(df, sig, tier_filter)
        if n_base < MIN_N_PRIMARY:
            continue
        sub_all = df.filter((pl.col('signal_type') == sig) & tier_filter)
        industries = sub_all.select('industry').unique().to_series().to_list()
        for ind in industries:
            sub = sub_all.filter(pl.col('industry') == ind)
            n = sub.height
            if n < MIN_N_INDUSTRY:
                continue
            p = sub.select(pl.col('is_miss').mean()).item()
            tp = 1.0 - p
            tp_base = 1.0 - p_base
            z = cohort_z(p, n, p_base)
            lift = p / p_base if p_base > 0 else float('nan')
            rows.append({
                'signal': sig,
                'tier': tier_label,
                'industry': ind,
                'N': n,
                'tp_pct': round(tp * 100, 2),
                'tp_base_pct': round(tp_base * 100, 2),
                'tp_delta_pp': round((tp - tp_base) * 100, 2),
                'miss_lift': round(lift, 3),
                'z': round(z, 2),
                'baseline_n': n_base,
            })
    out = pl.DataFrame(rows)
    return out.sort([pl.col('z').abs()], descending=True)


def main():
    pq = LOCAL_CACHE / 'cohort_v39_3650.parquet'
    df = pl.read_parquet(pq)
    print(f'Loaded {len(df):,} rows from {pq}')
    dmin = df.select(pl.col('date').min()).item()
    dmax = df.select(pl.col('date').max()).item()
    print(f'Date range: {dmin} → {dmax}')
    print(f'Outcome counts: {df.group_by("outcome").len().sort("outcome")}')
    print()

    # Baselines table
    print('=' * 80)
    print('BASELINE TP rates by (signal × tier)')
    print('=' * 80)
    for sig, label, expr in tier_filters():
        p_base, n_base = compute_baseline(df, sig, expr)
        if n_base < MIN_N_PRIMARY:
            continue
        print(f'  {sig:5s} {label:6s}: TP={(1-p_base)*100:.2f}%  N={n_base:,}')
    print()

    # Sector
    print('=' * 80)
    print(f'SECTOR cohorts (|z|>=1.96 highlighted, N>={MIN_N_PRIMARY})')
    print('=' * 80)
    sec_df = analyze_sector(df)
    sec_df.write_csv(LOCAL_CACHE / 'sector_cohorts.csv')
    sig_sec = sec_df.filter(pl.col('z').abs() >= 1.96)
    print(f'Total sector cells: {len(sec_df)}, significant (|z|>=1.96): {len(sig_sec)}')
    print()
    print('Top 25 by |z|:')
    print(sec_df.head(25))
    print()

    # Mcap
    print('=' * 80)
    print(f'MCAP cohorts (N>={MIN_N_PRIMARY})')
    print('=' * 80)
    mc_df = analyze_mcap(df)
    mc_df.write_csv(LOCAL_CACHE / 'mcap_cohorts.csv')
    sig_mc = mc_df.filter(pl.col('z').abs() >= 1.96)
    print(f'Total mcap cells: {len(mc_df)}, significant (|z|>=1.96): {len(sig_mc)}')
    print()
    print('Top 25 by |z|:')
    print(mc_df.head(25))
    print()

    # Joint
    print('=' * 80)
    print(f'SECTOR × MCAP joint cohorts (top |z|, N>={MIN_N_JOINT})')
    print('=' * 80)
    jt_df = analyze_joint(df)
    jt_df.write_csv(LOCAL_CACHE / 'joint_cohorts.csv')
    print(f'Total joint cells: {len(jt_df)}')
    print()
    print('Top 25 by |z|:')
    print(jt_df.head(25))
    print()

    # Industry
    print('=' * 80)
    print(f'INDUSTRY cohorts (signal×75+ or <=25 only, N>={MIN_N_INDUSTRY})')
    print('=' * 80)
    ind_df = analyze_industry(df)
    ind_df.write_csv(LOCAL_CACHE / 'industry_cohorts.csv')
    sig_ind = ind_df.filter(pl.col('z').abs() >= 1.96)
    print(f'Total industry cells: {len(ind_df)}, significant (|z|>=1.96): {len(sig_ind)}')
    print()
    print('Top 30 by |z|:')
    print(ind_df.head(30))
    print()

    print('Outputs written to:', LOCAL_CACHE)


if __name__ == '__main__':
    main()
