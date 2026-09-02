"""Put-side orthogonality check: does the Ichimoku spread on ≤25 puts persist
after conditioning on wadj?

The put-side cohort spread we found is large (+12-20pp on cloud_position).
But ≤25 peaks in v39 are already heavily filtered:
  - v27 WCF lifts puts with wadj > -17 toward 50 (so residual has wadj ≤ -17 OR
    overall already very low)
  - v37 PCD lifts puts where ret_10d_sigma ≤ -1.0 toward 30
  - v39 PESS lifts puts in [16,20] near earnings toward 28

We need to confirm the Ichimoku spread is genuinely independent of these
existing mechanisms. v32 CWCF is calls-only so doesn't apply.

Most actionable check: split residual ≤25 puts by wadj cohort. If the
Ichimoku spread is concentrated entirely in the wadj ≤ -17 cohort (where v27
WCF doesn't reach), there's a clean residual to address. If it's spread
across all wadj values, the mechanism is fully orthogonal.

Reuses the same w_adj extraction pattern as wadj_decomp.py.
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.cache' / 'weekly_avwap'
BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'

PARQUET = sys.argv[1] if len(sys.argv) > 1 else 'calls_v39_1825d_min0.parquet'


def load_wadj_for_peaks(av_id: int, min_score: int, max_score: int) -> dict[tuple[str, str], float]:
    """Bulk-load w_adj for peaks in [min_score, max_score]."""
    from database.trader_database import DB
    cur = DB.execute_sql(f"""
        SELECT symbol, date, weight_info
        FROM scores
        WHERE version_id = {av_id}
          AND overall >= {min_score}
          AND overall <= {max_score}
          AND weight_info IS NOT NULL
          AND weight_info != ''
    """)
    out: dict[tuple[str, str], float] = {}
    for sym, dt, wi in cur.fetchall():
        try:
            d = json.loads(wi) if isinstance(wi, str) else wi
            wadj = d.get('w_adj')
            if wadj is not None:
                key = (sym, dt.isoformat() if hasattr(dt, 'isoformat') else str(dt))
                out[key] = float(wadj)
        except Exception:
            continue
    return out


def cohort_wr(df: pl.DataFrame, col: str) -> tuple[float, int]:
    sub = df.filter(pl.col(col).is_not_null())
    n = len(sub)
    if n == 0:
        return float('nan'), 0
    return float(sub[col].mean()) * 100, n


def main():
    pq = CACHE / PARQUET
    if not pq.exists():
        print(f'[ERROR] parquet not found: {pq}', flush=True)
        return

    df = pl.read_parquet(pq)
    print(f'[load] parquet: {len(df):,} rows', flush=True)

    # Filter to puts ≤25 only
    puts = df.filter(pl.col('overall') <= 25)
    print(f'[filter] puts ≤25: {len(puts):,}', flush=True)

    # Drop the inf rows on w52_low_pct
    puts = puts.with_columns(
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct')
    )

    # ------------------------------------------------------------------
    # Attach put-side WR15 (side=high)
    # ------------------------------------------------------------------
    print('[load] put-side WR15...', flush=True)
    t0 = time.time()
    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        put_wr15 = pl.read_database(
            """SELECT symbol, date, result AS put_result_15
               FROM barrier_outcomes
               WHERE side='high' AND barrier_set='30dte_opt' AND w_days=15""",
            connection=conn,
        )
    finally:
        conn.close()
    puts = puts.join(put_wr15, on=['symbol', 'date'], how='left')
    print(f'[joined] put_result_15  ({time.time()-t0:.1f}s)', flush=True)

    # ------------------------------------------------------------------
    # Attach wadj
    # ------------------------------------------------------------------
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()

    t0 = time.time()
    wadj_map = load_wadj_for_peaks(av.id, 0, 25)
    print(f'[load] w_adj for {len(wadj_map):,} put peaks  ({time.time()-t0:.1f}s)', flush=True)

    puts = puts.with_columns(
        pl.struct(['symbol', 'date']).map_elements(
            lambda s: wadj_map.get((s['symbol'], s['date'])),
            return_dtype=pl.Float64,
        ).alias('w_adj')
    )

    n_with_wadj = puts['w_adj'].drop_nulls().len()
    print(f'[joined] {n_with_wadj:,} of {len(puts):,} puts have w_adj', flush=True)

    # ------------------------------------------------------------------
    # wadj distribution at ≤25
    # ------------------------------------------------------------------
    valid = puts.filter(pl.col('w_adj').is_not_null())
    print(f'\n=== ≤25 puts wadj distribution (N={len(valid):,}) ===')
    print(f'  min={valid["w_adj"].min():.2f}  '
          f'p25={valid["w_adj"].quantile(0.25):.2f}  '
          f'median={valid["w_adj"].median():.2f}  '
          f'p75={valid["w_adj"].quantile(0.75):.2f}  '
          f'max={valid["w_adj"].max():.2f}')

    # ------------------------------------------------------------------
    # Decompose by wadj cohort
    # ------------------------------------------------------------------
    # v27 WCF lifts puts toward 50 when wadj > -17. Residual ≤25 puts after v27
    # tend to have wadj ≤ -17, but distribution should confirm.
    cohorts = [
        ('wadj ≤ -17    (v27 WCF: outside lift gate)',  pl.col('w_adj') <= -17),
        ('-17 < wadj ≤ -10',  (pl.col('w_adj') > -17) & (pl.col('w_adj') <= -10)),
        ('-10 < wadj ≤ 0',    (pl.col('w_adj') > -10) & (pl.col('w_adj') <= 0)),
        ('wadj > 0     (v27 lift target)',  pl.col('w_adj') > 0),
    ]

    for cohort_label, cohort_filter in cohorts:
        sub = valid.filter(cohort_filter)
        n_total = len(sub)
        if n_total == 0:
            print(f'\n--- {cohort_label} --- (empty)')
            continue

        n_resolved = sub.filter(pl.col('put_result_15').is_not_null()).height
        base_wr, _ = cohort_wr(sub, 'put_result_15')
        share = n_total / len(valid) * 100

        print(
            f'\n--- {cohort_label} | N={n_total:,} ({share:.1f}% of ≤25) | '
            f'baseline put_WR15={base_wr:.2f}% ---'
        )

        # cloud_position split
        for v in (-1, 0, 1):
            grp = sub.filter(pl.col('cloud_position') == v)
            if len(grp) < 30:
                print(f'  cloud_position = {v:+d}  N={len(grp):>4,}  (skip)')
                continue
            wr, _ = cohort_wr(grp, 'put_result_15')
            d = wr - base_wr
            print(f'  cloud_position = {v:+d}  N={len(grp):>5,}  '
                  f'put_WR15={wr:5.2f}%  Δ={d:+5.2f}pp')

        # tenkan_kijun_dir split
        for v in (-1, 0, 1):
            grp = sub.filter(pl.col('tenkan_kijun_dir') == v)
            if len(grp) < 30:
                continue
            wr, _ = cohort_wr(grp, 'put_result_15')
            d = wr - base_wr
            print(f'  tenkan_kijun  = {v:+d}  N={len(grp):>5,}  '
                  f'put_WR15={wr:5.2f}%  Δ={d:+5.2f}pp')

        # kijun_pct tercile split
        kij = sub.filter(pl.col('price_vs_kijun_pct').is_not_null()
                         & pl.col('price_vs_kijun_pct').is_finite())
        if len(kij) >= 90:
            q33 = kij['price_vs_kijun_pct'].quantile(0.333)
            q67 = kij['price_vs_kijun_pct'].quantile(0.667)
            for label, sub2 in [
                ('low ', kij.filter(pl.col('price_vs_kijun_pct') <= q33)),
                ('mid ', kij.filter((pl.col('price_vs_kijun_pct') > q33)
                                    & (pl.col('price_vs_kijun_pct') <= q67))),
                ('high', kij.filter(pl.col('price_vs_kijun_pct') > q67)),
            ]:
                if len(sub2) < 30:
                    continue
                wr, _ = cohort_wr(sub2, 'put_result_15')
                d = wr - base_wr
                print(f'  kijun {label}    N={len(sub2):>5,}  '
                      f'put_WR15={wr:5.2f}%  Δ={d:+5.2f}pp')

    # ------------------------------------------------------------------
    # Headline: Ichimoku spread within each wadj cohort
    # ------------------------------------------------------------------
    print('\n' + '=' * 90)
    print('HEADLINE: cloud_position spread within each wadj cohort (≤25 puts)')
    print('=' * 90)
    for cohort_label, cohort_filter in cohorts:
        sub = valid.filter(cohort_filter)
        if len(sub) < 60:
            continue
        cl_neg = sub.filter(pl.col('cloud_position') == -1)
        cl_pos = sub.filter(pl.col('cloud_position') == 1)
        if len(cl_neg) < 30 or len(cl_pos) < 30:
            print(f'  {cohort_label}  (insufficient sub-N)')
            continue
        wr_neg, _ = cohort_wr(cl_neg, 'put_result_15')
        wr_pos, _ = cohort_wr(cl_pos, 'put_result_15')
        spread = wr_pos - wr_neg
        print(f'  {cohort_label:<45s}  '
              f'cloud=-1 (N={len(cl_neg):>4,}) put_WR={wr_neg:5.2f}% | '
              f'cloud=+1 (N={len(cl_pos):>4,}) put_WR={wr_pos:5.2f}% | '
              f'spread={spread:+6.2f}pp')


if __name__ == '__main__':
    main()
