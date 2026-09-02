"""Check whether mcap-aware LIFT (boosting large-cap 60-69 into call zone) is alpha.

The current dampener ships asymmetric (dampener-only). This script tests if a
symmetric mcap-aware lift would also be justified by data:

  Q: Among 60-69 signals (currently NOT call-qualifying), do large-caps have
     higher TP rate than the 70+ baseline (62.5% TP)?

If YES (cohort TP at 65-69 large-cap >= 70+ baseline):
  Lift symmetry IS justified — large-cap 65-69 are mis-categorized.

If NO (cohort TP at 65-69 large-cap < 70+ baseline):
  Lift would inflate signal count with sub-baseline-quality signals.
  Asymmetric dampener-only is correct.

Pulls 60-69 calls from MySQL, joins existing barrier_outcomes + stock meta.
Doesn't touch 70+ data (already in parquet).
"""
from __future__ import annotations
import io, sys, sqlite3, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments._holdout import pre_cutoff_filter, assert_no_holdout_leak

CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
LOCAL = ROOT / '.cache' / 'mcap_dampener'

LOOKBACK_DAYS = 3650


def main():
    from database.models.core import AlgorithmVersion
    from database.trader_database import DB
    av = AlgorithmVersion.get_active_scores_version()
    print(f'[check] active version: id={av.id} ({av.git_commit[:8]})', flush=True)

    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=300000')

    cache_pq = LOCAL / 'sub_qualifying_v39_3650.parquet'
    if cache_pq.exists():
        sub = pl.read_parquet(cache_pq)
        print(f'[check] cache hit: {len(sub):,} sub-qualifying rows', flush=True)
    else:
        # Pull v39 scores in [60, 69] over 10y, chunked
        print(f'[check] querying v39 calls 60-69 since {cutoff} (chunked)...', flush=True)
        rows = []
        start_year = int(cutoff[:4])
        end_year = date.today().year + 1
        t0 = time.time()
        for yr in range(start_year, end_year):
            y_start = f'{yr}-01-01' if yr != start_year else cutoff
            y_end = f'{yr}-12-31'
            ti = time.time()
            cur = DB.execute_sql(f"""
                SELECT s.symbol, s.date, s.overall
                FROM scores s
                WHERE s.version_id = {av.id}
                  AND s.date >= '{y_start}' AND s.date <= '{y_end}'
                  AND s.overall >= 60 AND s.overall <= 69
            """)
            chunk = cur.fetchall()
            rows.extend(chunk)
            print(f'  {yr}: {len(chunk):,} ({time.time()-ti:.1f}s)', flush=True)
        print(f'[check] total {len(rows):,} sub-qualifying rows ({time.time()-t0:.1f}s)', flush=True)

        peaks = pl.DataFrame({
            'symbol': [r[0] for r in rows],
            'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
            'overall': [int(r[2]) for r in rows],
        })

        # Stock meta
        cur = DB.execute_sql("SELECT symbol, market_cap FROM stocks WHERE market_cap IS NOT NULL")
        meta_rows = cur.fetchall()
        meta = pl.DataFrame({
            'symbol': [r[0] for r in meta_rows],
            'mcap_b': [float(r[1]) / 1e9 for r in meta_rows],
        })
        peaks = peaks.join(meta, on='symbol', how='left')

        # Barrier outcomes (30dte_opt @ w=15, side='low' for call signals — even sub-75
        # we treat as call-side for this analysis)
        conn = sqlite3.connect(str(CACHE_DB))
        barriers = pl.read_database(
            f"""SELECT symbol, date, result AS opt_result_15
                FROM barrier_outcomes
                WHERE side='low' AND barrier_set='30dte_opt'
                  AND date >= '{cutoff}' AND w_days = 15""",
            connection=conn,
        )
        conn.close()
        print(f'[check] barriers: {len(barriers):,}', flush=True)

        sub = peaks.join(barriers, on=['symbol', 'date'], how='left')
        sub = pre_cutoff_filter(sub)
        assert_no_holdout_leak(sub, 'mcap_dampener/check_lift_side')

        sub.write_parquet(cache_pq)
        print(f'[check] wrote {cache_pq} ({len(sub):,} rows)', flush=True)

    # Resolved subset
    res = sub.filter(pl.col('opt_result_15').is_not_null() & pl.col('mcap_b').is_not_null())
    print(f'\nResolved (with mcap + outcome): {len(res):,}')

    # Pull existing 70+ parquet for baseline comparison
    parquet70 = LOCAL / 'calls_v39_3650.parquet'
    base70 = pl.read_parquet(parquet70)
    base70 = base70.filter(pl.col('opt_result_15').is_not_null() & pl.col('mcap_b').is_not_null())
    print(f'70+ resolved (existing): {len(base70):,}')

    # mcap bins (same as cohort analysis)
    def add_mcap_bin(df):
        return df.with_columns(
            pl.when(pl.col('mcap_b') < 2).then(pl.lit('micro_lt2B'))
              .when(pl.col('mcap_b') < 10).then(pl.lit('small_2-10B'))
              .when(pl.col('mcap_b') < 50).then(pl.lit('mid_10-50B'))
              .when(pl.col('mcap_b') < 200).then(pl.lit('large_50-200B'))
              .when(pl.col('mcap_b') < 1000).then(pl.lit('xl_200B-1T'))
              .otherwise(pl.lit('mega_1T+')).alias('mcap_bin')
        )
    res = add_mcap_bin(res)
    base70 = add_mcap_bin(base70)

    # ── COHORT ANALYSIS ──
    # For [60, 69] split into sub-buckets: 60-64, 65-69
    res = res.with_columns(
        pl.when(pl.col('overall') < 65).then(pl.lit('60-64')).otherwise(pl.lit('65-69')).alias('score_bin')
    )
    base70 = base70.with_columns(
        pl.when(pl.col('overall') >= 75).then(pl.lit('75+'))
          .otherwise(pl.lit('70-74')).alias('score_bin')
    )

    # Headline: TP rate by score_bin × mcap_bin
    print('\n=== TP rate × N by score_bin × mcap_bin ===')
    print('(Below baseline = lift would be sub-baseline. Above baseline = lift would be alpha)\n')
    base70_tp_overall = base70.select((pl.col('opt_result_15') == 1).cast(pl.Float64).mean()).item()
    base75_tp_overall = base70.filter(pl.col('overall') >= 75).select((pl.col('opt_result_15') == 1).cast(pl.Float64).mean()).item()
    base7074_tp_overall = base70.filter((pl.col('overall') >= 70) & (pl.col('overall') < 75)).select((pl.col('opt_result_15') == 1).cast(pl.Float64).mean()).item()
    print(f'BASELINE 70+ TP: {base70_tp_overall*100:.2f}%  (N={len(base70):,})')
    print(f'BASELINE 75+ TP: {base75_tp_overall*100:.2f}%  (N={base70.filter(pl.col("overall")>=75).height:,})')
    print(f'BASELINE 70-74 TP: {base7074_tp_overall*100:.2f}%  (N={base70.filter((pl.col("overall")>=70) & (pl.col("overall")<75)).height:,})')

    print('\nFull table (60-64 / 65-69 sub-qualifying + 70-74 overflow + 75+ qualifying):')
    print(f"{'score_bin':>10} {'mcap_bin':>15} {'N':>7} {'TP%':>7} {'Δ vs 70+':>10} {'Δ vs 75+':>10}")

    combined = pl.concat([
        res.select(['score_bin', 'mcap_bin', 'opt_result_15']),
        base70.select(['score_bin', 'mcap_bin', 'opt_result_15']),
    ])

    cohort = (
        combined
        .group_by(['score_bin', 'mcap_bin'])
        .agg(
            pl.len().alias('N'),
            (pl.col('opt_result_15') == 1).cast(pl.Float64).mean().alias('tp_rate'),
        )
        .sort(['score_bin', 'mcap_bin'])
    )

    bin_order = {'60-64': 0, '65-69': 1, '70-74': 2, '75+': 3}
    cohort = cohort.sort([
        pl.col('score_bin').replace(bin_order, default=99).cast(pl.Int64),
        'mcap_bin'
    ])

    for r in cohort.iter_rows(named=True):
        tp = r['tp_rate'] * 100
        d70 = (tp - base70_tp_overall * 100)
        d75 = (tp - base75_tp_overall * 100)
        flag = ''
        if r['N'] >= 50:
            if d70 >= 1.0: flag = ' ← above 70+ baseline (lift candidate?)'
            elif d70 >= -1.0: flag = ' ≈ baseline'
        else:
            flag = ' (sparse)'
        print(f"{r['score_bin']:>10} {r['mcap_bin']:>15} {r['N']:>7,} {tp:>6.2f}% Δ70+ {d70:>+5.2f}pp Δ75+ {d75:>+5.2f}pp{flag}")

    # ── KEY QUESTION: large-cap 65-69 TP vs 70+ baseline ──
    print('\n=== KEY QUESTION: would lifting LARGE/XL/mega 65-69 calls into 75+ tier add alpha? ===')
    for size in ['large_50-200B', 'xl_200B-1T', 'mega_1T+']:
        sub = res.filter((pl.col('score_bin') == '65-69') & (pl.col('mcap_bin') == size))
        if sub.height < 20:
            print(f'  {size:>15s}: N={sub.height} (too sparse)')
            continue
        tp = sub.select((pl.col('opt_result_15') == 1).cast(pl.Float64).mean()).item() * 100
        print(f'  {size:>15s}: N={sub.height:>4}  TP={tp:.2f}%  (75+ baseline {base75_tp_overall*100:.2f}%, 70+ baseline {base70_tp_overall*100:.2f}%)')

    # Combined large+xl+mega
    big = res.filter((pl.col('score_bin') == '65-69') & pl.col('mcap_bin').is_in(['large_50-200B', 'xl_200B-1T', 'mega_1T+']))
    if big.height > 0:
        tp = big.select((pl.col('opt_result_15') == 1).cast(pl.Float64).mean()).item() * 100
        print(f'  COMBINED ≥$50B 65-69: N={big.height}, TP={tp:.2f}%')
        print(f'  Δ vs 70+ baseline: {tp - base70_tp_overall*100:+.2f}pp')
        print(f'  Δ vs 75+ baseline: {tp - base75_tp_overall*100:+.2f}pp')
        print(f'  Δ vs 70-74 overflow tier (currently disabled at 0% alloc): {tp - base7074_tp_overall*100:+.2f}pp')


if __name__ == '__main__':
    main()
