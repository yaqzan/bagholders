"""Stage 2 Phase A — pin the baseline option TP% / avg_option_pnl with
Stage 1 CTSL winner applied vs no-CTSL on the CURRENT shipped barriers.

Per `assessment-backtest.md` "Stage 2 sweep cadence":
  Phase A baseline pin (~3 min cache-served): compute current barrier set's
  option TP% / avg_option_pnl / WorstDD.

This script answers: under the Stage 1 CTSL winner, does the CURRENT shipped
barrier set (TP_BASE=33%, TP_STRESS=42%, SL_BASE=-27%, SL_STRESS=-40%, HOLD=15)
produce reasonable option TP%? If yes, Stage 2 Phase B sweep may be optional.
If no, Phase B is mandatory to recover the WR7→TP% gap.

Inputs:
  - .cache/ctsl/scores_v46_1825.parquet (option-aligned 30dte_opt @ w=15)
  - Stage 1 CTSL winner config (locked)

Outputs (printed):
  - Per-bucket option TP% under (no-CTSL baseline) vs (CTSL applied)
  - Per-bucket avg_option_pnl under (no-CTSL baseline) vs (CTSL applied)
  - Affected-cohort option TP% delta vs WR7 delta — measures the SL TAX
"""
from __future__ import annotations
import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PARQUET_OPT = ROOT / '.cache' / 'ctsl' / 'scores_v46_1825.parquet'  # has 30dte_opt @ w=15
PARQUET_GEN = ROOT / '.cache' / 'ctsl' / 'scores_v46_stage1_1825.parquet'  # has WR7/15/30 generic

# Stage 1 winner (locked)
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

CALL_BUCKETS = [('95+', 95, 100), ('90-94', 90, 94), ('85-89', 85, 89),
                ('80-84', 80, 84), ('75-79', 75, 79), ('70-74', 70, 74)]
PUT_BUCKETS = [('<5', 0, 5), ('6-10', 6, 10), ('11-15', 11, 15),
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


def per_bucket_metrics(df: pl.DataFrame, score_col: str, side: str, buckets) -> dict:
    """Compute per-bucket option TP% + avg_option_pnl + N from option-aligned
    barrier outcomes (opt_result_15, opt_exit_return_15)."""
    sub = df.filter((pl.col('side_label') == side) & pl.col('opt_result_15').is_not_null())
    out = {}
    for label, lo, hi in buckets:
        cell = sub.filter((pl.col(score_col) >= lo) & (pl.col(score_col) <= hi))
        n = cell.height
        if n == 0:
            out[label] = {'n': 0, 'tp_pct': None, 'avg_pnl': None}
            continue
        tp_pct = cell.filter(pl.col('opt_result_15') == 1).height / n * 100.0
        avg_pnl = cell.select(pl.col('opt_exit_return_15').mean()).item()
        out[label] = {'n': n, 'tp_pct': tp_pct, 'avg_pnl': float(avg_pnl) if avg_pnl is not None else None}
    return out


def affected_metrics(df: pl.DataFrame, params: dict, side: str) -> dict:
    """Compute option TP% + avg_pnl on the affected cohort (signals where
    CTSL changes the rounded overall)."""
    df_ctsl = df.with_columns([
        ctsl_call_lift(params).alias('new_call'),
        ctsl_put_dampen(params).alias('new_put'),
    ])
    if side == 'call':
        sub = df_ctsl.filter(
            (pl.col('side_label') == 'call') &
            (pl.col('new_call').round(0) != pl.col('overall').cast(pl.Float64).round(0)) &
            pl.col('opt_result_15').is_not_null()
        )
    else:
        sub = df_ctsl.filter(
            (pl.col('side_label') == 'put') &
            (pl.col('new_put').round(0) != pl.col('overall').cast(pl.Float64).round(0)) &
            pl.col('opt_result_15').is_not_null()
        )
    n = sub.height
    if n == 0:
        return {'n': 0, 'tp_pct': None, 'avg_pnl': None, 'wr15': None}
    tp_pct = sub.filter(pl.col('opt_result_15') == 1).height / n * 100.0
    avg_pnl = sub.select(pl.col('opt_exit_return_15').mean()).item()
    return {
        'n': n,
        'tp_pct': tp_pct,
        'avg_pnl': float(avg_pnl) if avg_pnl is not None else None,
    }


def main():
    print('\n' + '#' * 78, flush=True)
    print(' Stage 2 Phase A — baseline pin under Stage 1 CTSL winner', flush=True)
    print('#' * 78, flush=True)

    df = pl.read_parquet(PARQUET_OPT)
    print(f'\nloaded {PARQUET_OPT.name}: {len(df):,} rows', flush=True)
    print(f'  resolved opt_result_15: {df.filter(pl.col("opt_result_15").is_not_null()).height:,}',
          flush=True)

    print('\n' + '=' * 78, flush=True)
    print(' Per-bucket option TP% (current shipped barriers, 30dte_opt @ w=15)', flush=True)
    print('=' * 78, flush=True)

    base_call = per_bucket_metrics(df, 'overall', 'call', CALL_BUCKETS)
    base_put = per_bucket_metrics(df, 'overall', 'put', PUT_BUCKETS)

    df_ctsl = df.with_columns([
        ctsl_call_lift(WINNER).alias('new_call'),
        ctsl_put_dampen(WINNER).alias('new_put'),
    ])
    df_ctsl = df_ctsl.with_columns(
        pl.when(pl.col('side_label') == 'call').then(pl.col('new_call'))
        .otherwise(pl.col('new_put')).alias('overall_new')
    )

    new_call = per_bucket_metrics(df_ctsl, 'overall_new', 'call', CALL_BUCKETS)
    new_put = per_bucket_metrics(df_ctsl, 'overall_new', 'put', PUT_BUCKETS)

    print('\nCALLS:')
    print(f'  {"bucket":<8}  {"baseline":>20}  {"CTSL applied":>20}  {"Δ TP%":>8}  {"Δ pnl":>8}')
    print('  ' + '-' * 70)
    for label, _, _ in CALL_BUCKETS:
        b = base_call.get(label, {})
        n = new_call.get(label, {})
        b_tp = b.get('tp_pct')
        n_tp = n.get('tp_pct')
        b_pnl = b.get('avg_pnl')
        n_pnl = n.get('avg_pnl')
        b_str = f'TP%={b_tp:5.2f}% N={b.get("n", 0):>5}' if b_tp is not None else f'N={b.get("n", 0):>5}'
        n_str = f'TP%={n_tp:5.2f}% N={n.get("n", 0):>5}' if n_tp is not None else f'N={n.get("n", 0):>5}'
        d_tp = f'{n_tp - b_tp:+5.2f}pp' if (b_tp is not None and n_tp is not None) else '       --'
        d_pnl = f'{n_pnl - b_pnl:+5.3f}' if (b_pnl is not None and n_pnl is not None) else '   --'
        print(f'  {label:<8}  {b_str:>20}  {n_str:>20}  {d_tp:>8}  {d_pnl:>8}')

    print('\nPUTS:')
    print(f'  {"bucket":<8}  {"baseline":>20}  {"CTSL applied":>20}  {"Δ TP%":>8}  {"Δ pnl":>8}')
    print('  ' + '-' * 70)
    for label, _, _ in PUT_BUCKETS:
        b = base_put.get(label, {})
        n = new_put.get(label, {})
        b_tp = b.get('tp_pct')
        n_tp = n.get('tp_pct')
        b_pnl = b.get('avg_pnl')
        n_pnl = n.get('avg_pnl')
        b_str = f'TP%={b_tp:5.2f}% N={b.get("n", 0):>5}' if b_tp is not None else f'N={b.get("n", 0):>5}'
        n_str = f'TP%={n_tp:5.2f}% N={n.get("n", 0):>5}' if n_tp is not None else f'N={n.get("n", 0):>5}'
        d_tp = f'{n_tp - b_tp:+5.2f}pp' if (b_tp is not None and n_tp is not None) else '       --'
        d_pnl = f'{n_pnl - b_pnl:+5.3f}' if (b_pnl is not None and n_pnl is not None) else '   --'
        print(f'  {label:<8}  {b_str:>20}  {n_str:>20}  {d_tp:>8}  {d_pnl:>8}')

    print('\n' + '=' * 78, flush=True)
    print(' Affected cohort — option TP% under current barriers', flush=True)
    print('=' * 78, flush=True)

    aff_call = affected_metrics(df, WINNER, 'call')
    aff_put = affected_metrics(df, WINNER, 'put')
    print(f'  CALL affected: N={aff_call["n"]}  option TP%={aff_call["tp_pct"]:.2f}%  '
          f'avg_pnl={aff_call["avg_pnl"]:+.3f}' if aff_call['n'] else '  CALL affected: N=0', flush=True)
    print(f'  PUT  affected: N={aff_put["n"]}  option TP%={aff_put["tp_pct"]:.2f}%  '
          f'avg_pnl={aff_put["avg_pnl"]:+.3f}' if aff_put['n'] else '  PUT  affected: N=0', flush=True)

    # Compute the WR7 → option TP% gap (the SL tax)
    print('\n' + '=' * 78, flush=True)
    print(' WR7 → option TP% gap (the "SL tax")', flush=True)
    print('=' * 78, flush=True)

    df_gen = pl.read_parquet(PARQUET_GEN)
    df_gen = df_gen.with_columns([
        ctsl_call_lift(WINNER).alias('new_call'),
        ctsl_put_dampen(WINNER).alias('new_put'),
    ])
    aff_call_g = df_gen.filter(
        (pl.col('side_label') == 'call') &
        (pl.col('new_call').round(0) != pl.col('overall').cast(pl.Float64).round(0)) &
        pl.col('win_7d').is_not_null()
    )
    aff_put_g = df_gen.filter(
        (pl.col('side_label') == 'put') &
        (pl.col('new_put').round(0) != pl.col('overall').cast(pl.Float64).round(0)) &
        pl.col('win_7d').is_not_null()
    )
    wr7_call = aff_call_g.filter(pl.col('win_7d') == 1).height / aff_call_g.height * 100.0 if aff_call_g.height else None
    wr7_put = aff_put_g.filter(pl.col('win_7d') == 1).height / aff_put_g.height * 100.0 if aff_put_g.height else None

    print(f'  CALL affected:  WR7={wr7_call:.2f}%  option TP%={aff_call["tp_pct"]:.2f}%  '
          f'gap (SL tax)={wr7_call - aff_call["tp_pct"]:+.2f}pp' if wr7_call else '', flush=True)
    print(f'  PUT  affected:  WR7={wr7_put:.2f}%  option TP%={aff_put["tp_pct"]:.2f}%  '
          f'gap (SL tax)={wr7_put - aff_put["tp_pct"]:+.2f}pp' if wr7_put else '', flush=True)

    print('\n[done] Phase A pin complete.', flush=True)


if __name__ == '__main__':
    main()
