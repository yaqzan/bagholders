"""Phase B: Cohort profile for slow-indicator features (Priority #5b gate).

For each tier (70+/75+/80+/85+) AND each candidate feature:
  - Continuous features: split into terciles (low/mid/high)
  - Categorical features: split by value (-1/0/+1)
  - Compute option-aligned WR15 per group (1=win, 0=loss)
  - Report spread (max - min WR15) and z-scores

Gate: ≥5pp WR15 spread within the 75+ cohort on at least one feature.
A pass triggers Phase C (score-stage augmentation design).

Usage:
    python experiments/weekly_avwap/profile.py [parquet_name]
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

# Inputs
PARQUET = sys.argv[1] if len(sys.argv) > 1 else 'calls_v39_1825d.parquet'

CONT_FEATURES = [
    'avwap_pct',
    'price_vs_kijun_pct',
    'price_vs_tenkan_pct',
    'price_vs_span_b_pct',
    'sma50w_pct',
    'w52_high_pct',
    'w52_low_pct',
]

CAT_FEATURES = [
    'tenkan_kijun_dir',  # -1, 0, +1
    'cloud_position',    # -1, 0, +1
]

TIERS = [
    ('70+', 70, 100),
    ('75+', 75, 100),
    ('80+', 80, 100),
    ('85+', 85, 100),
]

GATE_PP = 5.0  # min spread (pp) to consider signal real
GATE_TIER = '75+'  # spec: signal must show on 75+


def _wr_z(n: int, k: int, p_baseline: float) -> tuple[float, float]:
    """Return (wr_pct, z_score_vs_baseline)."""
    if n == 0:
        return float('nan'), 0.0
    p = k / n
    se = math.sqrt(p_baseline * (1 - p_baseline) / n)
    z = (p - p_baseline) / se if se > 0 else 0.0
    return p * 100, z


def profile_continuous(df: pl.DataFrame, feature: str, tier_label: str) -> dict:
    """Tercile split on a continuous feature; report WR15 per tercile."""
    sub = df.filter(
        pl.col(feature).is_not_null()
        & pl.col(feature).is_finite()
        & pl.col('opt_result_15').is_not_null()
    )
    if len(sub) < 30:
        return {'feature': feature, 'n': len(sub), 'skipped': 'insufficient'}

    # Terciles via quantile cuts (33rd, 67th)
    q33 = sub[feature].quantile(0.333)
    q67 = sub[feature].quantile(0.667)

    # Define the buckets
    low  = sub.filter(pl.col(feature) <= q33)
    mid  = sub.filter((pl.col(feature) > q33) & (pl.col(feature) <= q67))
    high = sub.filter(pl.col(feature) > q67)

    base_wr = sub['opt_result_15'].mean()

    out = {'feature': feature, 'n_total': len(sub),
           'q33': float(q33), 'q67': float(q67),
           'baseline_wr': base_wr * 100}

    for label, grp in [('low', low), ('mid', mid), ('high', high)]:
        n = len(grp)
        k = int(grp['opt_result_15'].sum()) if n > 0 else 0
        wr, z = _wr_z(n, k, base_wr)
        out[f'{label}_n'] = n
        out[f'{label}_wr'] = wr
        out[f'{label}_z'] = z

    spread = abs(out['high_wr'] - out['low_wr'])
    out['spread_hi_lo'] = spread
    out['monotonic'] = (
        (out['low_wr'] <= out['mid_wr'] <= out['high_wr'])
        or (out['low_wr'] >= out['mid_wr'] >= out['high_wr'])
    )
    return out


def profile_categorical(df: pl.DataFrame, feature: str, tier_label: str) -> dict:
    """Group by categorical value; report WR15 per group."""
    sub = df.filter(
        pl.col(feature).is_not_null()
        & pl.col('opt_result_15').is_not_null()
    )
    if len(sub) < 30:
        return {'feature': feature, 'n': len(sub), 'skipped': 'insufficient'}

    base_wr = sub['opt_result_15'].mean()
    out = {'feature': feature, 'n_total': len(sub), 'baseline_wr': base_wr * 100}

    wrs = []
    for val in sorted(sub[feature].unique().drop_nulls().to_list()):
        grp = sub.filter(pl.col(feature) == val)
        n = len(grp)
        k = int(grp['opt_result_15'].sum())
        wr, z = _wr_z(n, k, base_wr)
        out[f'val_{val}_n'] = n
        out[f'val_{val}_wr'] = wr
        out[f'val_{val}_z'] = z
        wrs.append(wr)

    out['spread_max_min'] = max(wrs) - min(wrs) if wrs else 0
    return out


def fmt_cont(r: dict) -> str:
    if 'skipped' in r:
        return f"  {r['feature']:25s}  SKIP  ({r['skipped']}, n={r['n']})"
    flag = '  ✓' if r['spread_hi_lo'] >= GATE_PP else '   '
    mono = ' M' if r['monotonic'] else '  '
    return (
        f"  {r['feature']:25s}  N={r['n_total']:>5,}  "
        f"baseline={r['baseline_wr']:5.2f}%  | "
        f"low={r['low_wr']:5.2f}% (n={r['low_n']:>4,}, z={r['low_z']:+5.2f})  "
        f"mid={r['mid_wr']:5.2f}%  "
        f"high={r['high_wr']:5.2f}% (n={r['high_n']:>4,}, z={r['high_z']:+5.2f})  "
        f"spread={r['spread_hi_lo']:5.2f}pp{mono}{flag}"
    )


def fmt_cat(r: dict) -> str:
    if 'skipped' in r:
        return f"  {r['feature']:25s}  SKIP"
    parts = []
    for v in (-1, 0, 1):
        if f'val_{v}_n' in r:
            parts.append(
                f"v={v:+d}: {r[f'val_{v}_wr']:5.2f}% "
                f"(n={r[f'val_{v}_n']:>4,}, z={r[f'val_{v}_z']:+5.2f})"
            )
    flag = '  ✓' if r['spread_max_min'] >= GATE_PP else '   '
    return (
        f"  {r['feature']:25s}  N={r['n_total']:>5,}  "
        f"baseline={r['baseline_wr']:5.2f}%  | "
        + '  '.join(parts) + f"  spread={r['spread_max_min']:5.2f}pp{flag}"
    )


def main():
    pq = CACHE / PARQUET
    if not pq.exists():
        print(f'[ERROR] parquet not found: {pq}', flush=True)
        return

    df = pl.read_parquet(pq)
    print(f'\n=== Phase B cohort profile ===')
    print(f'  parquet: {pq.name}')
    print(f'  rows:    {len(df):,}')
    print(f'  gate:    ≥{GATE_PP}pp spread on 75+ tier')
    print(f'  metric:  option-aligned WR15 (30dte_opt, w=15)')
    print()

    pass_count = 0
    pass_features = []

    for tier_label, lo, hi in TIERS:
        tier_df = df.filter((pl.col('overall') >= lo) & (pl.col('overall') <= hi))
        n_resolved = tier_df.filter(pl.col('opt_result_15').is_not_null()).height
        if n_resolved == 0:
            continue
        print(f'─── Tier {tier_label} (N={len(tier_df):,}, resolved={n_resolved:,}) ───')

        for feat in CONT_FEATURES:
            r = profile_continuous(tier_df, feat, tier_label)
            line = fmt_cont(r)
            print(line)
            if (
                tier_label == GATE_TIER
                and 'spread_hi_lo' in r
                and r['spread_hi_lo'] >= GATE_PP
            ):
                pass_count += 1
                pass_features.append((feat, r['spread_hi_lo'], r['monotonic']))

        for feat in CAT_FEATURES:
            r = profile_categorical(tier_df, feat, tier_label)
            line = fmt_cat(r)
            print(line)
            if (
                tier_label == GATE_TIER
                and 'spread_max_min' in r
                and r['spread_max_min'] >= GATE_PP
            ):
                pass_count += 1
                pass_features.append((feat, r['spread_max_min'], None))

        print()

    # Summary
    print('=' * 80)
    if pass_count > 0:
        print(f'GATE PASSED — {pass_count} feature(s) show ≥{GATE_PP}pp spread on {GATE_TIER}:')
        for f, spread, mono in pass_features:
            mono_str = ' (monotonic)' if mono else (' (non-monotonic)' if mono is False else '')
            print(f'  • {f:25s}  spread={spread:.2f}pp{mono_str}')
        print('\n→ Proceed to Phase C: score-stage augmentation design.')
    else:
        print(f'GATE FAILED — no feature shows ≥{GATE_PP}pp spread on {GATE_TIER}.')
        print('  • Investigate stronger cohort splits (5-quantile, joint splits)')
        print('  • Or abandon this signal family and try Ichimoku/50W SMA standalone')
    print('=' * 80)


if __name__ == '__main__':
    main()
