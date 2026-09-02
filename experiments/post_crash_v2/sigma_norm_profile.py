"""Profile post-crash cohort using sigma-normalized 10-day return.

ret_10d_sigma = ret_10d / (sigma_60d / 100 * sqrt(10))

Where sigma_60d is the same 60-day realized daily volatility (in %) that the
assess methodology uses for the barrier-touch math. This makes the cohort
definition consistent across stocks with different volatilities.

Compare to raw-ret_10d profile to see if normalization sharpens the signal.
"""
from __future__ import annotations
import io, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / '.cache' / 'post_crash_v2' / 'features_v36_1825.parquet'


def z_two_proportions(wr_a, n_a, wr_b, n_b):
    if n_a == 0 or n_b == 0:
        return 0.0
    p_a, p_b = wr_a / 100.0, wr_b / 100.0
    p_pool = (p_a * n_a + p_b * n_b) / (n_a + n_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    return (p_a - p_b) / se if se > 0 else 0.0


def main():
    df = pl.read_parquet(PATH)
    df = df.filter(pl.col('result_15').is_not_null())

    # Add sigma-normalized 10d return: ret_10d / (sigma_15 / 100 * sqrt(10))
    # sigma_pct_15 is on the option-barrier 30dte_generic. It represents the 60d realized
    # daily vol in %.
    df = df.with_columns([
        # Use sigma_pct_30 since it should be equivalent (same realized vol regardless of W)
        (pl.col('ret_10d') / (pl.col('sigma_pct_30') / 100.0 * math.sqrt(10))).alias('ret_10d_sigma'),
        (pl.col('ret_5d') / (pl.col('sigma_pct_30') / 100.0 * math.sqrt(5))).alias('ret_5d_sigma'),
    ])

    # Drop rows with no sigma
    n0 = len(df)
    df = df.filter(pl.col('ret_10d_sigma').is_not_null())
    print(f'[sigma-norm] {len(df):,} rows ({n0-len(df)} dropped for missing sigma)', flush=True)

    # ── Distribution check: how does ret_10d_sigma look? ──
    print('\n[sigma-norm] ret_10d_sigma percentiles across put peaks:')
    for q in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        v = df['ret_10d_sigma'].quantile(q / 100)
        print(f'  q{q:>3}%: {v:+7.3f}sigma')

    # ── PROFILE: ret_10d_sigma buckets x score tier ──
    score_tiers = [('<=25', 25), ('<=15', 15), ('<=5', 5)]
    print('\n' + '=' * 110)
    print('AXIS: ret_10d_sigma (10d return normalized by 60d daily vol * sqrt(10))')
    print('-' * 110)
    print(f'{"score":<7} {"bucket":<24} {"N":>6} {"WR15":>7} {"baseline":>9} {"lift":>7} {"z":>7}')
    print('-' * 110)

    sigma_buckets = [
        ('sigma >= 0  (rising)',           pl.col('ret_10d_sigma') >= 0),
        ('sigma -1..0',                    (pl.col('ret_10d_sigma') < 0) & (pl.col('ret_10d_sigma') >= -1.0)),
        ('sigma -2..-1',                   (pl.col('ret_10d_sigma') < -1.0) & (pl.col('ret_10d_sigma') >= -2.0)),
        ('sigma -3..-2',                   (pl.col('ret_10d_sigma') < -2.0) & (pl.col('ret_10d_sigma') >= -3.0)),
        ('sigma -4..-3',                   (pl.col('ret_10d_sigma') < -3.0) & (pl.col('ret_10d_sigma') >= -4.0)),
        ('sigma < -4 (crashed)',           pl.col('ret_10d_sigma') < -4.0),
    ]

    for tier_label, tier_max in score_tiers:
        sub = df.filter(pl.col('overall') <= tier_max)
        if len(sub) == 0: continue
        wr_base = sub['result_15'].sum() / len(sub) * 100
        print(f'{tier_label:<7} {"BASELINE":<24} {len(sub):>6} {wr_base:>6.1f}%')

        for label, pred in sigma_buckets:
            cell = sub.filter(pred)
            n_c = len(cell)
            if n_c < 10: continue
            wr_c = cell['result_15'].sum() / n_c * 100
            other = sub.filter(~pred)
            n_o = len(other)
            wr_o = other['result_15'].sum() / n_o * 100
            lift = wr_c - wr_o
            z = z_two_proportions(wr_c, n_c, wr_o, n_o)
            mark = ''
            if abs(z) >= 3: mark = ' ***'
            elif abs(z) >= 2: mark = ' **'
            elif abs(z) >= 1.5: mark = ' *'
            print(f'{tier_label:<7} {label:<24} {n_c:>6} {wr_c:>6.1f}% {wr_o:>8.1f}% {lift:>+6.2f} {z:>+6.2f}{mark}')
        print('-' * 110)

    # ── COMPARE: same data, raw ret_10d vs sigma-normalized ──
    # The question: is sigma-normalized form more cleanly monotone than raw ret_10d?
    # (Indicates whether vol normalization is the right framing.)

    print('\n[sigma-norm] CROSS-CHECK: at same nominal "post-crash" magnitude, do sigma-buckets discriminate better?')
    print('=' * 110)
    print(f'{"axis":<25} {"cutoff":<18} {"<25 N":>7} {"<25 WR15":>10} {"baseline":>10} {"lift":>7} {"z":>7}')
    print('-' * 110)

    sub25 = df.filter(pl.col('overall') <= 25)

    # Raw ret_10d cutoffs
    for c in [-0.05, -0.08, -0.10, -0.12, -0.15, -0.20]:
        cell = sub25.filter(pl.col('ret_10d') <= c)
        other = sub25.filter(pl.col('ret_10d') > c)
        n_c, n_o = len(cell), len(other)
        if n_c < 30: continue
        wr_c = cell['result_15'].sum() / n_c * 100
        wr_o = other['result_15'].sum() / n_o * 100
        lift = wr_c - wr_o
        z = z_two_proportions(wr_c, n_c, wr_o, n_o)
        print(f'{"raw ret_10d":<25} {f"<= {c:+.2f}":<18} {n_c:>7} {wr_c:>9.1f}% {wr_o:>9.1f}% {lift:>+6.2f} {z:>+6.2f}')

    print()

    # Sigma-normalized cutoffs (matched magnitudes — using approx average sigma=2.5%/day, sqrt(10)*2.5%=7.9%)
    # so -1sigma ≈ -8%, -2sigma ≈ -16%, -3sigma ≈ -24%
    for s in [-0.5, -1.0, -1.5, -2.0, -2.5, -3.0, -4.0]:
        cell = sub25.filter(pl.col('ret_10d_sigma') <= s)
        other = sub25.filter(pl.col('ret_10d_sigma') > s)
        n_c, n_o = len(cell), len(other)
        if n_c < 30: continue
        wr_c = cell['result_15'].sum() / n_c * 100
        wr_o = other['result_15'].sum() / n_o * 100
        lift = wr_c - wr_o
        z = z_two_proportions(wr_c, n_c, wr_o, n_o)
        print(f'{"ret_10d_sigma":<25} {f"<= {s:+.1f}σ":<18} {n_c:>7} {wr_c:>9.1f}% {wr_o:>9.1f}% {lift:>+6.2f} {z:>+6.2f}')

    # ── Key question: stock-vol confound check ──
    # Among the raw ret_10d <= -0.10 cohort, is the underperformance concentrated in
    # LOW-vol stocks (where -10% is a 3sigma crash) or HIGH-vol stocks (where it's noise)?
    print('\n' + '=' * 110)
    print('STOCK-VOL CONFOUND: split raw-ret_10d cohort by stock vol bucket')
    print('-' * 110)

    sub25_drop10 = sub25.filter(pl.col('ret_10d') <= -0.10)
    sub25_other = sub25.filter(pl.col('ret_10d') > -0.10)
    print(f'Cohort (ret_10d<=-10%): N={len(sub25_drop10)}; rest: N={len(sub25_other)}')
    print()
    print(f'{"vol bucket (sigma_pct/d)":<28} {"N drop":>7} {"WR drop":>9} {"WR rest":>9} {"lift":>7} {"z":>7}')
    print('-' * 110)
    vol_buckets = [
        ('sigma_d <= 1.5  (low-vol)',  pl.col('sigma_pct_30') <= 1.5),
        ('sigma_d 1.5..2.5',           (pl.col('sigma_pct_30') > 1.5) & (pl.col('sigma_pct_30') <= 2.5)),
        ('sigma_d 2.5..4.0 (mid-vol)', (pl.col('sigma_pct_30') > 2.5) & (pl.col('sigma_pct_30') <= 4.0)),
        ('sigma_d 4.0..6.0',           (pl.col('sigma_pct_30') > 4.0) & (pl.col('sigma_pct_30') <= 6.0)),
        ('sigma_d > 6.0 (high-vol)',   pl.col('sigma_pct_30') > 6.0),
    ]
    for label, vp in vol_buckets:
        d_cell = sub25_drop10.filter(vp)
        d_rest = sub25_other.filter(vp)
        n_d, n_r = len(d_cell), len(d_rest)
        if n_d < 20: continue
        wr_d = d_cell['result_15'].sum() / n_d * 100
        wr_r = d_rest['result_15'].sum() / n_r * 100 if n_r > 0 else 0
        lift = wr_d - wr_r
        z = z_two_proportions(wr_d, n_d, wr_r, n_r)
        mark = ''
        if abs(z) >= 3: mark = ' ***'
        elif abs(z) >= 2: mark = ' **'
        print(f'{label:<28} {n_d:>7} {wr_d:>8.1f}% {wr_r:>8.1f}% {lift:>+6.2f} {z:>+6.2f}{mark}')


if __name__ == '__main__':
    main()
