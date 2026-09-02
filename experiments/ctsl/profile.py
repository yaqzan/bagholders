"""CTSL cohort profile — Phase 1.

Reads scores_{label}_1825.parquet built by build_features.py, computes per-trade
quality on the option-aligned 30dte_opt @ w=15 barrier for the relevant cohorts.

Quality metrics:
  - WR15  = P(opt_result_15 == +1)  (TP fired before SL within 15 trading days)
  - TP%   = same number, framed as the strategy-relevant "would have hit TP"
  - avg_option_pnl_15 = mean exit_return on option premium

The "BE" reference for context only:
  - 30 DTE call option (TP=0.33, SL=-0.27): BE = 27 / (33+27) = 45.0%
  - 30 DTE put  option (TP=0.35, SL=-0.20): BE = 20 / (35+20) = 36.4%

What we want to learn:

1. Does the CT cohort (call: overall>=70 AND trend<=20; put: overall<=25 AND
   trend>=80) carry meaningfully higher per-trade option WR15 than the non-CT
   baseline within its same overall-bucket?

2. If yes, where does the alpha live in the (overall × trend) grid? Is it
   concentrated at deep CT (trend ~ 0 for calls, trend ~ 100 for puts) or
   spread evenly?

3. Does the alpha persist post-MCD/ICH (v44) or did the recent dampener stack
   already absorb it (v39 alpha but not v44 alpha)?

Output: prints two tables per version (call cohort, put cohort) with
WR15/avg_pnl/N for ct vs non-ct, plus a 2D grid heat-table.
"""
from __future__ import annotations
import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
CACHE_DIR = ROOT / '.cache' / 'ctsl'

LOOKBACK_DAYS = 1825


def _wr_pnl_n(df: pl.DataFrame) -> tuple[float, float, int]:
    """WR15 (% TP fires), avg_option_pnl on option premium, N. Resolved only."""
    sub = df.filter(pl.col('opt_result_15').is_not_null())
    n = sub.height
    if n == 0:
        return float('nan'), float('nan'), 0
    wr = (sub.filter(pl.col('opt_result_15') == 1).height) / n * 100.0
    avg_pnl = sub.select(pl.col('opt_exit_return_15').mean()).item()
    return wr, float(avg_pnl) if avg_pnl is not None else float('nan'), n


def _print_cohort_table(df: pl.DataFrame, side_label: str, ct_pred, baseline_pred, label_ct: str, label_base: str):
    ct = df.filter(ct_pred & (pl.col('side_label') == side_label))
    base = df.filter(baseline_pred & (pl.col('side_label') == side_label))

    wr_ct, pnl_ct, n_ct = _wr_pnl_n(ct)
    wr_base, pnl_base, n_base = _wr_pnl_n(base)

    print(f'  {label_ct:>30}: WR15={wr_ct:5.2f}%  pnl={pnl_ct:+6.4f}  N={n_ct:>6,}')
    print(f'  {label_base:>30}: WR15={wr_base:5.2f}%  pnl={pnl_base:+6.4f}  N={n_base:>6,}')
    if n_ct > 0 and n_base > 0:
        print(f'  {"DELTA (ct - base)":>30}: WR15={wr_ct-wr_base:+5.2f}pp pnl={pnl_ct-pnl_base:+6.4f}')


def profile_call_grid(df: pl.DataFrame):
    """2D heat: (overall_bucket × trend_bucket) for calls only, WR15 + N."""
    sub = df.filter(
        (pl.col('side_label') == 'call') &
        (pl.col('overall') >= 70) &
        (pl.col('trend').is_not_null()) &
        (pl.col('opt_result_15').is_not_null())
    )
    print(f'  call grid: total resolved {sub.height:,}')

    score_buckets = [(70, 74), (75, 79), (80, 84), (85, 89), (90, 100)]
    trend_buckets = [(0, 10), (11, 20), (21, 30), (31, 50), (51, 100)]
    HEADER = 'overall / trend'

    print(f'\n  WR15 grid (rows = overall, cols = trend):')
    print(f'  {HEADER:<18}' + ''.join(f'{f"{lo}-{hi}":>10}' for lo, hi in trend_buckets))
    for s_lo, s_hi in score_buckets:
        row = f'  {f"{s_lo}-{s_hi}":<18}'
        for t_lo, t_hi in trend_buckets:
            cell = sub.filter(
                (pl.col('overall') >= s_lo) & (pl.col('overall') <= s_hi) &
                (pl.col('trend') >= t_lo) & (pl.col('trend') <= t_hi)
            )
            n = cell.height
            if n < 30:
                row += f'{"--":>10}'
            else:
                wr = cell.filter(pl.col('opt_result_15') == 1).height / n * 100.0
                row += f'{wr:>7.1f}%'
        print(row)

    print(f'\n  N grid (rows = overall, cols = trend):')
    print(f'  {HEADER:<18}' + ''.join(f'{f"{lo}-{hi}":>10}' for lo, hi in trend_buckets))
    for s_lo, s_hi in score_buckets:
        row = f'  {f"{s_lo}-{s_hi}":<18}'
        for t_lo, t_hi in trend_buckets:
            cell = sub.filter(
                (pl.col('overall') >= s_lo) & (pl.col('overall') <= s_hi) &
                (pl.col('trend') >= t_lo) & (pl.col('trend') <= t_hi)
            )
            row += f'{cell.height:>10,}'
        print(row)


def profile_put_grid(df: pl.DataFrame):
    """2D heat: (overall_bucket × trend_bucket) for puts only, WR15 + N."""
    sub = df.filter(
        (pl.col('side_label') == 'put') &
        (pl.col('overall') <= 25) &
        (pl.col('trend').is_not_null()) &
        (pl.col('opt_result_15').is_not_null())
    )
    print(f'  put grid: total resolved {sub.height:,}')

    score_buckets = [(0, 5), (6, 10), (11, 15), (16, 20), (21, 25)]
    trend_buckets = [(0, 49), (50, 69), (70, 79), (80, 89), (90, 100)]
    HEADER = 'overall / trend'

    print(f'\n  WR15 grid (rows = overall, cols = trend):')
    print(f'  {HEADER:<18}' + ''.join(f'{f"{lo}-{hi}":>10}' for lo, hi in trend_buckets))
    for s_lo, s_hi in score_buckets:
        row = f'  {f"{s_lo}-{s_hi}":<18}'
        for t_lo, t_hi in trend_buckets:
            cell = sub.filter(
                (pl.col('overall') >= s_lo) & (pl.col('overall') <= s_hi) &
                (pl.col('trend') >= t_lo) & (pl.col('trend') <= t_hi)
            )
            n = cell.height
            if n < 30:
                row += f'{"--":>10}'
            else:
                wr = cell.filter(pl.col('opt_result_15') == 1).height / n * 100.0
                row += f'{wr:>7.1f}%'
        print(row)

    print(f'\n  N grid (rows = overall, cols = trend):')
    print(f'  {HEADER:<18}' + ''.join(f'{f"{lo}-{hi}":>10}' for lo, hi in trend_buckets))
    for s_lo, s_hi in score_buckets:
        row = f'  {f"{s_lo}-{s_hi}":<18}'
        for t_lo, t_hi in trend_buckets:
            cell = sub.filter(
                (pl.col('overall') >= s_lo) & (pl.col('overall') <= s_hi) &
                (pl.col('trend') >= t_lo) & (pl.col('trend') <= t_hi)
            )
            row += f'{cell.height:>10,}'
        print(row)


def main(label: str):
    pq = CACHE_DIR / f'scores_{label}_{LOOKBACK_DAYS}.parquet'
    if not pq.exists():
        print(f'!! parquet not found: {pq}', file=sys.stderr)
        sys.exit(1)
    df = pl.read_parquet(pq)
    print(f'\n========== {label} ({pq.name}) ==========')
    print(f'rows: {len(df):,}  resolved: {df.filter(pl.col("opt_result_15").is_not_null()).height:,}')

    print('\n--- CALL cohort comparison ---')
    print('  (CT = overall >= 70 AND trend <= 20; baseline = overall >= 70 AND trend > 20)')
    _print_cohort_table(
        df, 'call',
        ct_pred=(pl.col('overall') >= 70) & (pl.col('trend') <= 20),
        baseline_pred=(pl.col('overall') >= 70) & (pl.col('trend') > 20),
        label_ct='CT-call (trend<=20)',
        label_base='non-CT call (trend>20)',
    )
    # Per-bucket discrimination
    print('\n  Per-bucket CT vs non-CT (overall buckets):')
    for s_lo, s_hi in [(70,74), (75,79), (80,84), (85,89), (90,100)]:
        bucket_pred = (pl.col('overall') >= s_lo) & (pl.col('overall') <= s_hi)
        ct = df.filter(bucket_pred & (pl.col('trend') <= 20) & (pl.col('side_label') == 'call'))
        base = df.filter(bucket_pred & (pl.col('trend') > 20) & (pl.col('side_label') == 'call'))
        wr_ct, pnl_ct, n_ct = _wr_pnl_n(ct)
        wr_base, pnl_base, n_base = _wr_pnl_n(base)
        delta = wr_ct - wr_base if (n_ct >= 30 and n_base >= 30) else float('nan')
        d_str = f'{delta:+5.2f}pp' if delta == delta else '   --'
        print(f'    {s_lo}-{s_hi:>3}: ct N={n_ct:>5,} WR={wr_ct:5.2f}% | base N={n_base:>5,} WR={wr_base:5.2f}% | delta={d_str}')

    profile_call_grid(df)

    print('\n--- PUT cohort comparison ---')
    print('  (CT = overall <= 25 AND trend >= 80; baseline = overall <= 25 AND trend < 80)')
    _print_cohort_table(
        df, 'put',
        ct_pred=(pl.col('overall') <= 25) & (pl.col('trend') >= 80),
        baseline_pred=(pl.col('overall') <= 25) & (pl.col('trend') < 80),
        label_ct='CT-put (trend>=80)',
        label_base='non-CT put (trend<80)',
    )
    print('\n  Per-bucket CT vs non-CT (overall buckets):')
    for s_lo, s_hi in [(0,5), (6,10), (11,15), (16,20), (21,25)]:
        bucket_pred = (pl.col('overall') >= s_lo) & (pl.col('overall') <= s_hi)
        ct = df.filter(bucket_pred & (pl.col('trend') >= 80) & (pl.col('side_label') == 'put'))
        base = df.filter(bucket_pred & (pl.col('trend') < 80) & (pl.col('side_label') == 'put'))
        wr_ct, pnl_ct, n_ct = _wr_pnl_n(ct)
        wr_base, pnl_base, n_base = _wr_pnl_n(base)
        delta = wr_ct - wr_base if (n_ct >= 30 and n_base >= 30) else float('nan')
        d_str = f'{delta:+5.2f}pp' if delta == delta else '   --'
        print(f'    {s_lo}-{s_hi:>3}: ct N={n_ct:>5,} WR={wr_ct:5.2f}% | base N={n_base:>5,} WR={wr_base:5.2f}% | delta={d_str}')

    profile_put_grid(df)


if __name__ == '__main__':
    labels = sys.argv[1:] or ['v44', 'v39']
    for lab in labels:
        main(lab)
