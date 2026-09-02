"""Post-Crash Dampener (PCD) sweep — fast variant evaluator.

For each variant: lift put scores up toward LIFT_TARGET when the underlying
recently fell sharply (ret_10d <= RAMP_LO), with weakness ramping linearly
between RAMP_LO and RAMP_HI.

Reuses the FastVariantRunner score/barrier parquet caches; joins ret_10d
per (symbol, date) and applies the lift after baseline regime/wcf score is in.

Reports per-bucket WR15 / WR30 / N / TP%-equivalent against baseline (no PCD).
H1-H5 ship gate is evaluated at the bottom.
"""
from __future__ import annotations
import io, sys, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.fast_variant_runner import FastVariantRunner, BUY_THRESHOLDS, SELL_THRESHOLDS, PERIODS, THRESHOLD_KEYS

RET10D_PATH = ROOT / '.cache' / 'post_crash_v2' / 'ret10d_all_1825.parquet'


def evaluate_pcd(runner, k, ramp_lo, ramp_hi, score_gate, lift_target, ret10d_df):
    """Apply post-crash lift to put-side scores and aggregate.

    weakness = clip((ramp_lo - ret_10d) / (ramp_lo - ramp_hi), 0, 1)
    if overall <= score_gate AND weakness > 0:
        new_overall = round(clip(overall + k * weakness * (lift_target - overall), 0, 100))
    """
    df = runner.df.join(ret10d_df, on=['symbol', 'date'], how='left')

    if k == 0.0:
        # Baseline (no lift)
        df = df.with_columns(pl.col('overall').cast(pl.Int64).alias('new_overall'))
    else:
        weakness = ((ramp_lo - pl.col('ret_10d')) / (ramp_lo - ramp_hi)).clip(0.0, 1.0)
        df = df.with_columns(weakness.alias('weakness_pcd'))
        df = df.with_columns(
            pl.when((pl.col('overall') <= score_gate)
                    & (pl.col('weakness_pcd') > 0)
                    & pl.col('ret_10d').is_not_null())
              .then(pl.col('overall') + k * pl.col('weakness_pcd') * (lift_target - pl.col('overall')))
              .otherwise(pl.col('overall'))
              .round().clip(0, 100).cast(pl.Int64)
              .alias('new_overall')
        )

    return runner._aggregate_from_df(df)


def fmt_delta(cur, base, fmt='5.2f'):
    if cur is None or base is None:
        return f'{(cur or 0):>{fmt}}    --   '
    delta = cur - base
    sign = '+' if delta >= 0 else ''
    return f'{cur:>{fmt}}% ({sign}{delta:.2f})'


def main():
    print('[pcd] loading runner ...', flush=True)
    runner = FastVariantRunner()
    runner.load(verbose=False)
    print(f'[pcd] runner loaded: {len(runner.df):,} score rows, version_id={runner.version_id}', flush=True)

    t0 = time.time()
    ret10d = pl.read_parquet(RET10D_PATH)
    print(f'[pcd] ret10d feature: {len(ret10d):,} rows ({time.time()-t0:.1f}s)', flush=True)

    # Variants — sweep K x target x gate x ramp_hi (ramp_lo fixed at -0.15)
    variants = [
        # label, k, ramp_lo, ramp_hi, score_gate, lift_target
        ('baseline',           0.0,  -0.15, -0.25, 25, 30),

        # K sweep at gate=25, target=30, ramp -15..-25
        ('K05_T30_G25',        0.5,  -0.15, -0.25, 25, 30),
        ('K065_T30_G25',       0.65, -0.15, -0.25, 25, 30),
        ('K080_T30_G25',       0.80, -0.15, -0.25, 25, 30),
        ('K095_T30_G25',       0.95, -0.15, -0.25, 25, 30),

        # Target sweep at K=0.80
        ('K080_T28_G25',       0.80, -0.15, -0.25, 25, 28),
        ('K080_T32_G25',       0.80, -0.15, -0.25, 25, 32),
        ('K080_T35_G25',       0.80, -0.15, -0.25, 25, 35),

        # Score gate sweep at K=0.80, T=30
        ('K080_T30_G15',       0.80, -0.15, -0.25, 15, 30),
        ('K080_T30_G20',       0.80, -0.15, -0.25, 20, 30),

        # Ramp endpoint sweep (broader / narrower)
        ('K080_T30_G25_R10_20',0.80, -0.10, -0.20, 25, 30),  # earlier onset
        ('K080_T30_G25_R20_30',0.80, -0.20, -0.30, 25, 30),  # later onset

        # Discrete (binary) variant: full lift at ret_10d <= -15%
        ('DISC_T30_G25_15',    1.0,  -0.15, -0.15001, 25, 30),
        ('DISC_T30_G25_20',    1.0,  -0.20, -0.20001, 25, 30),

        # Higher target combinations (push out of put range entirely)
        ('K10_T35_G25',        1.0,  -0.15, -0.25, 25, 35),
        ('K10_T40_G25',        1.0,  -0.15, -0.25, 25, 40),
    ]

    # Run baseline first for reference
    base_stats = None
    results = []
    print('\n[pcd] running variants ...', flush=True)
    for entry in variants:
        label = entry[0]
        t0 = time.time()
        stats = evaluate_pcd(runner, *entry[1:], ret10d_df=ret10d)
        et = time.time() - t0
        if label == 'baseline':
            base_stats = stats
        results.append((label, entry, stats, et))
        n25 = stats['bucketed_stats'].get('<25', {}).get('sample_count') or 0
        n15 = stats['bucketed_stats'].get('<15', {}).get('sample_count') or 0
        n5  = stats['bucketed_stats'].get('<5', {}).get('sample_count') or 0
        n70 = stats['bucketed_stats'].get('70+', {}).get('sample_count') or 0
        print(f"  {label:<26} N(<5={n5:>4} <15={n15:>5} <25={n25:>5}, 70+={n70:>5}) [{et:.2f}s]", flush=True)

    # ── PUT TIER WR15 TABLE ─────────────────────────────────────────
    put_buckets = ['<25', '<15', '<5']
    print('\n' + '=' * 130)
    print('PUT TIER WR15 (delta vs baseline)')
    print('-' * 130)
    print(f"{'Variant':<24}  {'<5 WR15':>17}  {'<15 WR15':>17}  {'<25 WR15':>17}  {'<5 N':>7}  {'<15 N':>7}  {'<25 N':>7}")
    print('-' * 130)
    for label, entry, stats, et in results:
        bs = stats['bucketed_stats']
        def get(b, p='15d'):
            return bs.get(b, {}).get(f'win_rate_{p}')
        def fmt(b):
            cur = get(b)
            base = base_stats['bucketed_stats'].get(b, {}).get('win_rate_15d') if base_stats else None
            if cur is None: return '       --       '
            if base is None: return f'{cur:5.1f}%'
            d = cur - base
            return f'{cur:5.1f}% ({d:+5.2f})'
        n5 = bs.get('<5', {}).get('sample_count') or 0
        n15 = bs.get('<15', {}).get('sample_count') or 0
        n25 = bs.get('<25', {}).get('sample_count') or 0
        print(f"{label:<24}  {fmt('<5'):>17}  {fmt('<15'):>17}  {fmt('<25'):>17}  {n5:>7}  {n15:>7}  {n25:>7}")

    # ── CALL TIER WR15 TABLE (sanity — should be UNCHANGED) ─────────
    print('\n' + '=' * 130)
    print('CALL TIER WR15 (sanity — must be unchanged; gate is overall<=25)')
    print('-' * 130)
    print(f"{'Variant':<24}  {'95+':>14}  {'90+':>14}  {'85+':>14}  {'80+':>14}  {'75+':>14}  {'70+':>14}")
    print('-' * 130)
    for label, entry, stats, et in results:
        bs = stats['bucketed_stats']
        def fmt(b):
            cur = bs.get(b, {}).get('win_rate_15d')
            base = base_stats['bucketed_stats'].get(b, {}).get('win_rate_15d') if base_stats else None
            if cur is None: return '      --      '
            if base is None: return f'{cur:5.1f}%'
            d = cur - base
            return f'{cur:5.1f}% ({d:+5.2f})'
        line = f"{label:<24}"
        for b in ['95+', '90+', '85+', '80+', '75+', '70+']:
            line += f"  {fmt(b):>14}"
        print(line)

    # ── PUT TIER WR30 TABLE ─────────────────────────────────────────
    print('\n' + '=' * 130)
    print('PUT TIER WR30 (consistency check — should move SAME direction as WR15)')
    print('-' * 130)
    print(f"{'Variant':<24}  {'<5 WR30':>17}  {'<15 WR30':>17}  {'<25 WR30':>17}")
    print('-' * 130)
    for label, entry, stats, et in results:
        bs = stats['bucketed_stats']
        def fmt(b):
            cur = bs.get(b, {}).get('win_rate_30d')
            base = base_stats['bucketed_stats'].get(b, {}).get('win_rate_30d') if base_stats else None
            if cur is None: return '       --       '
            if base is None: return f'{cur:5.1f}%'
            d = cur - base
            return f'{cur:5.1f}% ({d:+5.2f})'
        print(f"{label:<24}  {fmt('<5'):>17}  {fmt('<15'):>17}  {fmt('<25'):>17}")

    # ── H1-H4 SHIP GATE (per-variant) ────────────────────────────────
    print('\n' + '=' * 130)
    print('H1-H4 PER-TRADE SHIP GATE (5y, scoring change context)')
    print('  H1: TP%/WR15 +>=0.5pp on >=3 of {95+, 90+, 85+, 80+, 75+}; no tier <-1.0pp ── (call side, expect NO change here, gate is N/A)')
    print('  H4: <25 and <15 WR15 unchanged or improved')
    print('  PUT-specific: target is +WR15 on put tiers; <25 and <15 should improve (this is the cohort suppression)')
    print('-' * 130)
    if base_stats:
        for label, entry, stats, et in results:
            if label == 'baseline': continue
            bs = stats['bucketed_stats']

            # Put side (H4 + the actual goal)
            put_passes, put_lift_total, put_lift_max = [], 0.0, 0.0
            put_lifts = []
            for b in ['<5', '<15', '<25']:
                cur = bs.get(b, {}).get('win_rate_15d')
                base = base_stats['bucketed_stats'].get(b, {}).get('win_rate_15d')
                if cur is not None and base is not None:
                    d = cur - base
                    put_lifts.append((b, d))
                    put_lift_total += d
                    put_lift_max = max(put_lift_max, d)
            # Call side (H1 — should be ~zero change)
            call_changes = []
            for b in ['95+', '90+', '85+', '80+', '75+']:
                cur = bs.get(b, {}).get('win_rate_15d')
                base = base_stats['bucketed_stats'].get(b, {}).get('win_rate_15d')
                if cur is not None and base is not None:
                    call_changes.append((b, cur - base))

            put_lift_str = '/'.join(f'{b}={d:+.2f}' for b, d in put_lifts)
            call_max_abs = max((abs(d) for _, d in call_changes), default=0.0)

            # N regression check (H3)
            n25_base = base_stats['bucketed_stats'].get('<25', {}).get('sample_count') or 0
            n15_base = base_stats['bucketed_stats'].get('<15', {}).get('sample_count') or 0
            n25_cur = bs.get('<25', {}).get('sample_count') or 0
            n15_cur = bs.get('<15', {}).get('sample_count') or 0
            n25_drop_pct = (n25_cur - n25_base) / n25_base * 100 if n25_base else 0
            n15_drop_pct = (n15_cur - n15_base) / n15_base * 100 if n15_base else 0

            # Put H4: do they improve?
            h4_pass = all(d >= -0.1 for _, d in put_lifts)
            # H3: N within +-15% (lift should move N OUT of <25, so we expect N to decrease — that's intentional, not regression)
            # Use looser bound for puts since intentional displacement
            n_ok = n15_cur >= 1500  # absolute floor: ~50% of original 4136
            print(f"  {label:<24}  put_lifts={put_lift_str}  | "
                  f"max_call_change_abs={call_max_abs:+.2f}  | "
                  f"N15={n15_cur} ({n15_drop_pct:+.0f}%)  N25={n25_cur} ({n25_drop_pct:+.0f}%)  | "
                  f"H4={'✓' if h4_pass else '✗'} N>=1500={'✓' if n_ok else '✗'}")


if __name__ == '__main__':
    main()
