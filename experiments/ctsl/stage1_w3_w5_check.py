"""Validate W3 (multi-time-window directional consistency on WR7) and
W5 (N capacity floor) for the Stage 1 winner."""
from __future__ import annotations
import io, sys, json
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
from datetime import date, timedelta
import polars as pl
import sys as _sys

ROOT = Path(__file__).resolve().parents[2]
_sys.path.insert(0, str(ROOT))
PARQUET = ROOT / '.cache' / 'ctsl' / 'scores_v46_stage1_1825.parquet'

# Stage 1 winner (from sweep_stage1.py output)
WINNER = {
    'CTSL_CALL_TREND_MAX':         15,
    'CTSL_CALL_TARGET':            98.4186,
    'CTSL_CALL_ALPHA':             0.5620,
    'CTSL_CALL_TREND_POWER':       2.8243,
    'CTSL_CALL_TIER_FLOOR':        74.6705,
    'CTSL_CALL_SCORE_NORM_WEIGHT': 0.7532,
    'CTSL_CALL_SCORE_NORM_POWER':  2.2712,
    'CTSL_PUT_TREND_MIN':          76,
    'CTSL_PUT_TARGET':            -0.1304,
    'CTSL_PUT_ALPHA':              0.8292,
    'CTSL_PUT_TREND_POWER':        0.9934,
    'CTSL_PUT_TIER_CEILING':       27.9176,
    'CTSL_PUT_SCORE_NORM_WEIGHT': -0.2159,
    'CTSL_PUT_SCORE_NORM_POWER':   1.6817,
}

# N capacity floor table (per assessment-backtest.md "N capacity floor table — H6 successor")
N_FLOOR = {
    '95+':   26,
    '90-94': 83,
    '85-89': 83,    # (the H6 table groups 85-94 as 83/yr; same floor as 90-94)
    '80-84': 164,
    '75-79': 436,
    '<15':   196,
    '16-20': 398,
    '21-25': 541,
}

CALL_BUCKETS = [('95+', 95, 100), ('90-94', 90, 94), ('85-89', 85, 89),
                ('80-84', 80, 84), ('75-79', 75, 79), ('70-74', 70, 74)]
PUT_BUCKETS  = [('<5', 0, 5), ('6-10', 6, 10), ('11-15', 11, 15),
                ('16-20', 16, 20), ('21-25', 21, 25)]


def ctsl_call_lift(params: dict) -> pl.Expr:
    tm = params['CTSL_CALL_TREND_MAX']
    target = params['CTSL_CALL_TARGET']
    alpha = params['CTSL_CALL_ALPHA']
    power = params['CTSL_CALL_TREND_POWER']
    floor = params['CTSL_CALL_TIER_FLOOR']
    snw = params['CTSL_CALL_SCORE_NORM_WEIGHT']
    snp = params['CTSL_CALL_SCORE_NORM_POWER']

    is_ct = (
        (pl.col('side_label') == 'call') &
        (pl.col('overall') >= 70) &
        pl.col('trend').is_not_null() &
        (pl.col('trend') <= tm)
    )
    trend_dist = ((pl.lit(tm) - pl.col('trend') + 1).cast(pl.Float64) / float(tm + 1)).clip(0.0, 1.0)
    if abs(snw) < 1e-6:
        sf = pl.lit(1.0)
    elif snw > 0:
        sn = ((pl.col('overall').cast(pl.Float64) - 70.0) / 30.0).clip(0.0, 1.0)
        sf = pl.lit(1.0) + snw * (sn ** snp)
    else:
        sn = ((76.0 - pl.col('overall').cast(pl.Float64)) / 6.0).clip(0.0, 1.0)
        sf = pl.lit(1.0) + abs(snw) * (sn ** snp)
    lift = alpha * (trend_dist ** power) * sf * (target - pl.col('overall').cast(pl.Float64))
    lifted = pl.col('overall').cast(pl.Float64) + lift
    rescued = pl.when(lifted < floor).then(floor).otherwise(lifted)
    return pl.when(is_ct).then(rescued.clip(0.0, 100.0)).otherwise(pl.col('overall').cast(pl.Float64))


def ctsl_put_dampen(params: dict) -> pl.Expr:
    tm = params['CTSL_PUT_TREND_MIN']
    target = params['CTSL_PUT_TARGET']
    alpha = params['CTSL_PUT_ALPHA']
    power = params['CTSL_PUT_TREND_POWER']
    ceiling = params['CTSL_PUT_TIER_CEILING']
    snw = params['CTSL_PUT_SCORE_NORM_WEIGHT']
    snp = params['CTSL_PUT_SCORE_NORM_POWER']
    is_ct = (
        (pl.col('side_label') == 'put') &
        (pl.col('overall') <= 25) &
        pl.col('trend').is_not_null() &
        (pl.col('trend') >= tm)
    )
    span = max(1.0, float(100 - tm + 1))
    trend_dist = ((pl.col('trend') - pl.lit(tm) + 1).cast(pl.Float64) / span).clip(0.0, 1.0)
    if abs(snw) < 1e-6:
        sf = pl.lit(1.0)
    elif snw > 0:
        sn = ((25.0 - pl.col('overall').cast(pl.Float64)) / 25.0).clip(0.0, 1.0)
        sf = pl.lit(1.0) + snw * (sn ** snp)
    else:
        sn = ((pl.col('overall').cast(pl.Float64) - 19.0) / 6.0).clip(0.0, 1.0)
        sf = pl.lit(1.0) + abs(snw) * (sn ** snp)
    dampen = alpha * (trend_dist ** power) * sf * (pl.col('overall').cast(pl.Float64) - target)
    dampened = pl.col('overall').cast(pl.Float64) - dampen
    capped = pl.when(dampened > ceiling).then(ceiling).otherwise(dampened)
    return pl.when(is_ct).then(capped.clip(0.0, 100.0)).otherwise(pl.col('overall').cast(pl.Float64))


def affected_wr7(df: pl.DataFrame, params: dict, side: str) -> tuple[float, int]:
    """Compute affected-cohort WR7 for one side, on a given date-filtered df."""
    df_ctsl = df.with_columns(
        ctsl_call_lift(params).alias('new_call'),
        ctsl_put_dampen(params).alias('new_put'),
    )
    if side == 'call':
        sub = df_ctsl.filter(
            (pl.col('side_label') == 'call') &
            (pl.col('new_call').round(0) != pl.col('overall').cast(pl.Float64).round(0)) &
            pl.col('win_7d').is_not_null()
        )
    else:
        sub = df_ctsl.filter(
            (pl.col('side_label') == 'put') &
            (pl.col('new_put').round(0) != pl.col('overall').cast(pl.Float64).round(0)) &
            pl.col('win_7d').is_not_null()
        )
    n = sub.height
    if n == 0:
        return None, 0
    wr = sub.filter(pl.col('win_7d') == 1).height / n * 100.0
    return wr, n


def main():
    df = pl.read_parquet(PARQUET)
    print(f'\n[w3-w5] loaded {len(df):,} rows', flush=True)

    today = date.today()

    # ---- W3: multi-time-window directional consistency on WR7 ----
    # 1y / 3y / 5y windows (rolling from today)
    print('\n' + '=' * 78, flush=True)
    print(' W3: Multi-time-window WR7 on affected cohort (1y / 3y / 5y)', flush=True)
    print('=' * 78, flush=True)
    print(f'  {"window":<6}  {"call WR7":>10}  {"call N":>8}  {"put WR7":>10}  {"put N":>8}', flush=True)
    for label, days in [('1y', 365), ('3y', 1095), ('5y', 1825)]:
        cutoff = (today - timedelta(days=days)).isoformat()
        df_w = df.filter(pl.col('date') >= cutoff)
        cw, cn = affected_wr7(df_w, WINNER, 'call')
        pw, pn = affected_wr7(df_w, WINNER, 'put')
        cw_s = f'{cw:>9.2f}%' if cw is not None else '       N/A'
        pw_s = f'{pw:>9.2f}%' if pw is not None else '       N/A'
        print(f'  {label:<6}  {cw_s}  {cn:>8,}  {pw_s}  {pn:>8,}', flush=True)

    # ---- W5: per-tier signals/year offered to cascade ----
    print('\n' + '=' * 78, flush=True)
    print(' W5: N capacity floor (per-tier signals/year, post-CTSL)', flush=True)
    print('=' * 78, flush=True)

    # Apply CTSL to whole 5y df
    df_ctsl = df.with_columns(
        ctsl_call_lift(WINNER).alias('new_call'),
        ctsl_put_dampen(WINNER).alias('new_put'),
    )
    df_ctsl = df_ctsl.with_columns(
        pl.when(pl.col('side_label') == 'call').then(pl.col('new_call'))
        .otherwise(pl.col('new_put')).alias('overall_new')
    )

    print(f'  {"tier":<10}  {"floor/yr":>10}  {"new offered/yr":>15}  {"verdict":>10}', flush=True)
    print('  CALLS:')
    for label, lo, hi in CALL_BUCKETS:
        cell = df_ctsl.filter(
            (pl.col('side_label') == 'call') &
            (pl.col('overall_new') >= lo) &
            (pl.col('overall_new') <= hi)
        )
        n_5y = cell.height
        n_per_yr = n_5y / 5.0
        floor = N_FLOOR.get(label)
        if floor is None:
            verdict = '(no floor)'
        elif n_per_yr >= floor:
            verdict = 'PASS'
        else:
            verdict = f'FAIL'
        floor_s = f'{floor:>9,}' if floor else '       n/a'
        print(f'  {label:<10}  {floor_s}  {n_per_yr:>14.0f}  {verdict:>10}', flush=True)

    print('  PUTS:')
    for label, lo, hi in PUT_BUCKETS:
        cell = df_ctsl.filter(
            (pl.col('side_label') == 'put') &
            (pl.col('overall_new') >= lo) &
            (pl.col('overall_new') <= hi)
        )
        n_5y = cell.height
        n_per_yr = n_5y / 5.0
        # Map put bucket label to N_FLOOR key
        floor_key = '<15' if label in ('<5', '6-10', '11-15') else label
        floor = N_FLOOR.get(floor_key)
        if floor is None:
            verdict = '(no floor)'
        elif n_per_yr >= floor:
            verdict = 'PASS'
        else:
            verdict = f'FAIL'
        floor_s = f'{floor:>9,}' if floor else '       n/a'
        print(f'  {label:<10}  {floor_s}  {n_per_yr:>14.0f}  {verdict:>10}', flush=True)

    print('\n[done]', flush=True)


if __name__ == '__main__':
    main()
