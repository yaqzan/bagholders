"""Profile call peaks (75+) by breadth_score bucket on v39 5y baseline.

Question: does the score's barrier-touch WR (calibration target, 30dte_generic K=2.0σ)
diverge from the option TP rate (30dte_opt) within the LOW-breadth tape?
If yes, the score is over-stating expected outcome in low-breadth regimes — and a
score-stage breadth dampener (BSD) would be alpha-positive.

Comparison columns per (score_tier, breadth_bucket):
  WR15_gen  — barrier-touch at K=2.0σ over 15 cal days  (score-calibrated)
  WR15_opt  — barrier-touch at H5 option σ thresholds   (what we trade)
  Δopt-gen  — divergence (negative = score over-states option outcome)

If divergence is concentrated in low-breadth quintile and absent in high-breadth,
BSD is encodable. If divergence is uniform across breadth, F3F is correlated-DD only.
"""
from __future__ import annotations
import io, os, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
# BSD_PARQUET env var lets the queue-runner point at any version's parquet
# (e.g. calls_v42_1825.parquet) without editing this file.
PATH = Path(os.environ.get('BSD_PARQUET',
            ROOT / '.cache' / 'breadth_score_dampener' / 'calls_v39_1825.parquet'))

CALL_BE_BASE = 45.0   # 27/(33+27) — from current v39 base TP/SL
CALL_BE_STR  = 48.8   # 40/(42+40) — stress
NULL_BARRIER_RESULT = -2  # how barrier_outcomes encodes "insufficient forward data" — we filter via is_not_null


def z_two_proportions(wr_a, n_a, wr_b, n_b):
    if n_a == 0 or n_b == 0:
        return 0.0
    p_a, p_b = wr_a / 100.0, wr_b / 100.0
    p_pool = (p_a * n_a + p_b * n_b) / (n_a + n_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    return (p_a - p_b) / se if se > 0 else 0.0


def cohort_table(df, axis_col, axis_buckets, score_tiers):
    """Per (score_tier, axis_bucket) report WR15_gen, WR15_opt, divergence."""
    print('\n' + '=' * 130)
    print(f'AXIS: {axis_col}')
    print('-' * 130)
    print(f'{"score":<7} {"bucket":<24} {"N":>6} '
          f'{"WR15_gen":>9} {"WR15_opt":>9} {"Δ(opt-gen)":>11} '
          f'{"Ret15_opt":>10} {"reg_mult":>9} '
          f'{"vs_pool_gen":>13} {"z":>6}')
    print('-' * 130)

    for tier_label, tier_min in score_tiers:
        sub = df.filter(pl.col('overall') >= tier_min)
        sub = sub.filter(pl.col('gen_result_15').is_not_null())
        n_base = len(sub)
        if n_base == 0:
            continue
        # treat positive outcome (TP) as result == 1; barrier_outcomes encodes 1=win, 0=stop, -1=expire
        wr_base_gen = (sub.filter(pl.col('gen_result_15') == 1).height / n_base) * 100
        wr_base_opt = (sub.filter(pl.col('opt_result_15') == 1).height / n_base) * 100
        ret_base_opt = sub['opt_exit_return_15'].mean()
        reg_base = sub['regime_multiplier'].mean()
        delta_base = wr_base_opt - wr_base_gen
        ret_s = f'{ret_base_opt:+.4f}' if ret_base_opt is not None else '   --'
        reg_s = f'{reg_base:+.4f}' if reg_base is not None else '   --'
        print(f'{tier_label:<7} {"BASELINE (all)":<24} {n_base:>6} '
              f'{wr_base_gen:>8.1f}% {wr_base_opt:>8.1f}% {delta_base:>+10.2f} '
              f'{ret_s:>10} {reg_s:>9}')

        for bkt_label, predicate in axis_buckets:
            cell = sub.filter(predicate)
            n_cell = len(cell)
            if n_cell < 30:
                continue
            wr_cell_gen = (cell.filter(pl.col('gen_result_15') == 1).height / n_cell) * 100
            wr_cell_opt = (cell.filter(pl.col('opt_result_15') == 1).height / n_cell) * 100
            ret_cell_opt = cell['opt_exit_return_15'].mean()
            reg_cell = cell['regime_multiplier'].mean()
            delta_cell = wr_cell_opt - wr_cell_gen
            # divergence vs pool: how much does THIS cell's gen-WR drop from baseline gen-WR?
            n_other = n_base - n_cell
            wr_other_gen = ((sub.filter(pl.col('gen_result_15') == 1).height
                             - cell.filter(pl.col('gen_result_15') == 1).height) / n_other) * 100 if n_other > 0 else 0
            vs_pool_gen = wr_cell_gen - wr_other_gen
            z = z_two_proportions(wr_cell_gen, n_cell, wr_other_gen, n_other)
            mark = ''
            if abs(z) >= 3.0: mark = ' ***'
            elif abs(z) >= 2.0: mark = ' **'
            elif abs(z) >= 1.5: mark = ' *'
            ret_s = f'{ret_cell_opt:+.4f}' if ret_cell_opt is not None else '   --'
            reg_s = f'{reg_cell:+.4f}' if reg_cell is not None else '   --'
            print(f'{tier_label:<7} {bkt_label:<24} {n_cell:>6} '
                  f'{wr_cell_gen:>8.1f}% {wr_cell_opt:>8.1f}% {delta_cell:>+10.2f} '
                  f'{ret_s:>10} {reg_s:>9} '
                  f'{vs_pool_gen:>+12.2f} {z:>+6.2f}{mark}')


def main():
    df = pl.read_parquet(PATH)
    print(f'[profile] loaded {len(df):,} call peak rows from {PATH.name}')
    print(f'[profile] columns: {df.columns}')
    print(f'[profile] breadth_score range: [{df["breadth_score"].min():.1f}, {df["breadth_score"].max():.1f}]')
    print(f'[profile] breadth_score quintile cuts (approx):')
    for q in [0.20, 0.40, 0.60, 0.80]:
        print(f'   q{int(q*100):02d}: {df["breadth_score"].quantile(q):.1f}')

    score_tiers = [
        ('75+',  75),
        ('80+',  80),
        ('85+',  85),
        ('90+',  90),
    ]

    # --- AXIS 1: breadth_score quintiles ---
    # Compute quintile cuts dynamically
    q20 = df['breadth_score'].quantile(0.20)
    q40 = df['breadth_score'].quantile(0.40)
    q60 = df['breadth_score'].quantile(0.60)
    q80 = df['breadth_score'].quantile(0.80)
    breadth_buckets = [
        (f'Q1 brd<={q20:.1f} (lowest)',   pl.col('breadth_score') <= q20),
        (f'Q2 {q20:.1f}<brd<={q40:.1f}',  (pl.col('breadth_score') > q20) & (pl.col('breadth_score') <= q40)),
        (f'Q3 {q40:.1f}<brd<={q60:.1f}',  (pl.col('breadth_score') > q40) & (pl.col('breadth_score') <= q60)),
        (f'Q4 {q60:.1f}<brd<={q80:.1f}',  (pl.col('breadth_score') > q60) & (pl.col('breadth_score') <= q80)),
        (f'Q5 brd>{q80:.1f} (highest)',   pl.col('breadth_score') > q80),
    ]
    cohort_table(df, 'breadth_score (quintile)', breadth_buckets, score_tiers)

    # --- AXIS 2: F3F-relevant breadth threshold buckets ---
    # F3F call floor LOW=30 means scaling kicks in below brd=30
    # Stress band threshold is 40 in v39
    f3f_buckets = [
        ('brd <= 30  (F3F floor)',       pl.col('breadth_score') <= 30),
        ('30 < brd <= 40 (F3F ramp)',    (pl.col('breadth_score') > 30) & (pl.col('breadth_score') <= 40)),
        ('40 < brd <= 50 (stress band)', (pl.col('breadth_score') > 40) & (pl.col('breadth_score') <= 50)),
        ('50 < brd <= 70',               (pl.col('breadth_score') > 50) & (pl.col('breadth_score') <= 70)),
        ('brd > 70  (healthy)',          pl.col('breadth_score') > 70),
    ]
    cohort_table(df, 'breadth_score (F3F thresholds)', f3f_buckets, score_tiers)

    # --- AXIS 3: regime_multiplier (the existing post-processing dampener) ---
    # Goal: see whether regime_multiplier ALREADY captures most of the breadth signal,
    # OR whether residual breadth signal exists after regime_multiplier is applied.
    reg_buckets = [
        ('reg_mult <= 0.78 (stress)',    pl.col('regime_multiplier') <= 0.78),
        ('0.78 < r <= 0.88 (caut)',      (pl.col('regime_multiplier') > 0.78) & (pl.col('regime_multiplier') <= 0.88)),
        ('0.88 < r <= 1.00 (neutral)',   (pl.col('regime_multiplier') > 0.88) & (pl.col('regime_multiplier') <= 1.00)),
        ('1.00 < r <= 1.05 (healthy)',   (pl.col('regime_multiplier') > 1.00) & (pl.col('regime_multiplier') <= 1.05)),
        ('r > 1.05 (bull)',              pl.col('regime_multiplier') > 1.05),
    ]
    cohort_table(df, 'regime_multiplier (existing dampener)', reg_buckets, score_tiers)

    # --- AXIS 4: residual breadth at NEUTRAL regime_multiplier ---
    # Filter to regime_multiplier in [0.88, 1.10] and split by breadth.
    # If breadth still moves WR within this band, it carries information the regime
    # multiplier missed (the F3F-shipping argument).
    print('\n' + '=' * 130)
    print('FILTER: regime_multiplier in [0.88, 1.10] (neutral/healthy regime — where F3F said composite under-reacts)')
    print('-' * 130)
    df_neutral = df.filter((pl.col('regime_multiplier') >= 0.88) & (pl.col('regime_multiplier') <= 1.10))
    print(f'  N_neutral_regime = {len(df_neutral):,} of {len(df):,} ({len(df_neutral)/len(df)*100:.0f}%)')
    cohort_table(df_neutral, 'breadth | reg_mult≈neutral', f3f_buckets, score_tiers)


if __name__ == '__main__':
    main()
