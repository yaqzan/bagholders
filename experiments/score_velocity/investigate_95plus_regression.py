"""Investigate the v40 vs v39 regression at 95+ tier (-4.10pp at 5y, -1.86 at 3y, -5.00 at 1y).

Three questions:
  1. SIGNIFICANCE: with N=42-43 at 5y, is the -4pp gap inside MC noise?
  2. THESIS CHECK: SVD assumed "high-conviction signals don't decel". For v39's 95+ cohort,
     what fraction actually have velocity_5d < 0? What's their WR vs accelerating peers?
  3. ATTRIBUTION: what fraction of the v39→v40 95+ N drop was caused by SVD displacement
     specifically (overall ≥ 95 AND vel<0) vs general data drift?

If the displaced 95+ cohort had AT or ABOVE baseline WR, SVD is destroying alpha at the
high tier — thesis flaw. If displaced cohort had BELOW baseline WR, SVD is correctly
filtering and the v40 regression is noise.
"""
from __future__ import annotations
import io, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]


def two_prop_z(p1, n1, p2, n2):
    """Two-proportion z-test. Returns z-score and approximate p-value."""
    if min(n1, n2) < 1: return None, None
    p_pool = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1/n1 + 1/n2))
    if se == 0: return None, None
    z = (p1 - p2) / se
    # Two-tailed p-value approximation using error function
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return z, p


def main():
    # Load v39 parquet — has velocity_5d we can use to profile what would be displaced
    df_v39 = pl.read_parquet(ROOT / '.cache' / 'score_velocity' / 'calls_v39_3650.parquet')
    df_v40 = pl.read_parquet(ROOT / '.cache' / 'score_velocity' / 'calls_v40_1825.parquet')
    print(f'v39 (10y): {len(df_v39):,} call peaks  | v40 (5y): {len(df_v40):,}')

    today = date.today()
    w5y = (today - timedelta(days=1825)).isoformat()
    w3y = (today - timedelta(days=1095)).isoformat()
    w1y = (today - timedelta(days=365)).isoformat()

    # Filter to 5y window for direct comparison
    v39_5y = df_v39.filter((pl.col('date') >= w5y) & pl.col('opt_result_15').is_not_null())
    v40_5y = df_v40.filter((pl.col('date') >= w5y) & pl.col('opt_result_15').is_not_null())
    print(f'\nIn 5y window with valid barrier: v39={len(v39_5y):,}  v40={len(v40_5y):,}')

    # ── Q1: SIGNIFICANCE ──────────────────────────────────────────────────
    print('\n══ Q1. SIGNIFICANCE TEST ══')
    print('Two-proportion z-test on v39 vs v40 across tiers (small N → underpowered):')
    print(f'  {"tier":>5}  {"v39 N":>6} {"v39 WR":>7}  {"v40 N":>6} {"v40 WR":>7}  {"ΔWR":>7}  {"z":>6}  {"p":>6}')
    for thr, label in [(95, '95+'), (90, '90+'), (85, '85+'), (80, '80+'), (75, '75+')]:
        v39_t = v39_5y.filter(pl.col('overall') >= thr)
        v40_t = v40_5y.filter(pl.col('overall') >= thr)
        n1 = len(v39_t); n2 = len(v40_t)
        if n1 == 0 or n2 == 0: continue
        p1 = (v39_t['opt_result_15'] == 1).sum() / n1
        p2 = (v40_t['opt_result_15'] == 1).sum() / n2
        z, p = two_prop_z(p1, n1, p2, n2)
        print(f'  {label:>5}  {n1:>6} {p1*100:>6.2f}%  {n2:>6} {p2*100:>6.2f}%  {(p2-p1)*100:>+5.2f}pp  {z:>+6.2f}  {p:>6.3f}')

    # ── Q2: THESIS CHECK ──────────────────────────────────────────────────
    # For v39 at 5y, profile 95+ cohort by velocity_5d. Use the v39 file at 5y.
    print('\n══ Q2. THESIS CHECK (v39 95+ cohort profile by velocity) ══')
    v39_95 = v39_5y.filter(pl.col('overall') >= 95)
    print(f'v39 95+ at 5y: N={len(v39_95)}')
    if 'velocity_5d' in v39_95.columns:
        non_null = v39_95.filter(pl.col('velocity_5d').is_not_null())
        print(f'  non-null velocity_5d: {len(non_null)}')

        # Profile by velocity buckets
        buckets = [
            ('vel <= -10', non_null.filter(pl.col('velocity_5d') <= -10)),
            ('-10 < vel <= -5', non_null.filter((pl.col('velocity_5d') > -10) & (pl.col('velocity_5d') <= -5))),
            ('-5 < vel <= -1', non_null.filter((pl.col('velocity_5d') > -5) & (pl.col('velocity_5d') <= -1))),
            ('vel == 0', non_null.filter(pl.col('velocity_5d') == 0)),
            ('1 <= vel <= 5', non_null.filter((pl.col('velocity_5d') >= 1) & (pl.col('velocity_5d') <= 5))),
            ('vel > 5', non_null.filter(pl.col('velocity_5d') > 5)),
        ]
        baseline_wr = (non_null['opt_result_15'] == 1).sum() / len(non_null) * 100 if len(non_null) > 0 else 0
        print(f'  baseline WR15 (all 95+ with non-null vel): {baseline_wr:.2f}%')
        print(f'\n  {"bucket":>20}  {"N":>4}  {"WR15":>7}  {"Δ vs base":>9}  {"z (vs others)":>15}')
        for label, sub in buckets:
            n = len(sub)
            if n == 0:
                print(f'  {label:>20}  {n:>4}  {"  --":>7}  {"  --":>9}  {"  --":>15}')
                continue
            wr = (sub['opt_result_15'] == 1).sum() / n * 100
            others = non_null.filter(~non_null['date'].is_in(sub['date']) | ~non_null['symbol'].is_in(sub['symbol']))
            # simpler: compute z vs aggregate baseline
            z, _ = two_prop_z(wr/100, n, baseline_wr/100, len(non_null))
            z_str = f'{z:+.2f}' if z is not None else '  --'
            print(f'  {label:>20}  {n:>4}  {wr:>6.2f}%  {(wr-baseline_wr):>+7.2f}pp  {z_str:>15}')

    # ── Q3: SVD-DISPLACED 95+ COHORT ──────────────────────────────────────
    # Of v39 95+ peaks with vel<0, what is their WR? These are EXACTLY the cohort
    # that v40 SVD displaces from 95+. If their WR < baseline, SVD is right.
    print('\n══ Q3. ATTRIBUTION — what does SVD displace from v39 95+? ══')
    if 'velocity_5d' in v39_95.columns:
        displaced = v39_95.filter(pl.col('velocity_5d') < 0)
        survived = v39_95.filter((pl.col('velocity_5d') >= 0) | pl.col('velocity_5d').is_null())
        print(f'  v39 95+ where vel<0 (displaced by SVD)        : N={len(displaced)}')
        print(f'  v39 95+ where vel>=0 or null (kept by SVD)    : N={len(survived)}')
        if len(displaced) > 0:
            wr_disp = (displaced['opt_result_15'] == 1).sum() / len(displaced) * 100
            print(f'  Displaced cohort WR15: {wr_disp:.2f}%  ({(displaced["opt_result_15"]==1).sum()}/{len(displaced)})')
        if len(survived) > 0:
            wr_surv = (survived['opt_result_15'] == 1).sum() / len(survived) * 100
            print(f'  Survived cohort WR15 : {wr_surv:.2f}%  ({(survived["opt_result_15"]==1).sum()}/{len(survived)})')
        if len(displaced) > 0 and len(survived) > 0:
            z, p = two_prop_z(wr_disp/100, len(displaced), wr_surv/100, len(survived))
            zs = f'{z:+.2f}' if z is not None else '--'
            ps = f'{p:.3f}' if p is not None else '--'
            print(f'  z(displaced vs survived) = {zs}  p = {ps}')
            # Verdict
            print(f'\n  VERDICT (95+ tier):')
            if wr_disp < wr_surv - 2:
                print(f'  ✓ Displaced cohort underperforms — SVD is correctly filtering at 95+.')
                print(f'    The v40 95+ regression is NOT due to SVD destroying alpha.')
                print(f'    Other explanations: small-N noise, data drift between recalcs.')
            elif wr_disp > wr_surv + 2:
                print(f'  ✗ Displaced cohort OUTPERFORMS — SVD destroys alpha at 95+.')
                print(f'    Thesis FLAW: 95+ decel signals are still good trades.')
                print(f'    Recommend re-calibration with score-tier-specific gates.')
            else:
                print(f'  ~ Displaced cohort ≈ survived cohort — SVD is approximately neutral at 95+.')
                print(f'    The v40 regression is small-N noise, not a thesis problem.')

    # Also do the same for 90+ and 85+
    print('\n══ Q3b. Same analysis for 90+ and 85+ ══')
    for thr_low, label in [(90, '90+'), (85, '85+'), (80, '80+'), (75, '75+')]:
        cohort = v39_5y.filter(pl.col('overall') >= thr_low)
        if 'velocity_5d' not in cohort.columns: continue
        displaced = cohort.filter(pl.col('velocity_5d') < 0)
        survived = cohort.filter((pl.col('velocity_5d') >= 0) | pl.col('velocity_5d').is_null())
        if len(displaced) == 0 or len(survived) == 0: continue
        wr_d = (displaced['opt_result_15'] == 1).sum() / len(displaced) * 100
        wr_s = (survived['opt_result_15'] == 1).sum() / len(survived) * 100
        z, p = two_prop_z(wr_d/100, len(displaced), wr_s/100, len(survived))
        zs = f'{z:+.2f}' if z is not None else '--'
        ps = f'{p:.3f}' if p is not None else '--'
        verdict = 'displaced UNDERPERFORMS (SVD ✓)' if wr_d < wr_s - 2 else (
            'displaced OUTPERFORMS (SVD destroys alpha)' if wr_d > wr_s + 2 else 'roughly neutral')
        print(f'  {label:>4}: disp N={len(displaced):>3} WR={wr_d:>5.1f}%  vs surv N={len(survived):>5} WR={wr_s:>5.1f}%  Δ={wr_d-wr_s:+.1f}pp  z={zs} p={ps}  → {verdict}')


if __name__ == '__main__':
    main()
