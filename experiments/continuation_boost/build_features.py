"""Build feature parquets for continuation-boost sweep.

Inputs:
  .cache/miss_ledger/components_v32_1825.parquet      # all v32 scores 5y
  .cache/runner/barriers_30opt_w15_1825.parquet       # 30dte_opt w=15d outcomes (TP%)
  .cache/barrier_outcomes.db                           # 30dte_generic w=7,15 (prior wins)
  ScoreAssessmentResult (active 5y 30 DTE run)        # avg_mfe_30d per bucket

Outputs:
  .cache/continuation_boost/peaks.parquet
      one row per call/put peak (overall>=75 or <=25), with:
      - symbol, date, side ('call'/'put'), overall, conviction (|score-50|),
        score_intent (signed: +/- by side),
        current_close, vol_pct,
        outcome_tp_15d (1=TP / 0=SL / null=expire) at 30dte_opt w=15d,
        bucket_label,
        mfe_anchor_self (avg_mfe_30d for the peak's own bucket)

  .cache/continuation_boost/priors_long.parquet
      one row per (peak, prior_peak) reference; long format:
      - symbol, date (peak), prior_date, gap_days, prior_score,
        prior_intent (signed: +calls / -puts; matches peak side by construction),
        prior_close, prior_bucket, prior_mfe_anchor (avg_mfe_30d),
        win_7d_30dgen, win_15d_30dgen        # cache results

We pre-cache the RAW per-prior data (not aggregated), so sweep variants can
re-aggregate under any (TAU, W15_W7, MAG_EXP, etc.) in a single Polars op.

The peak's `realized_pct_since_oldest_prior` is computed at variant time as:
    side * (current_close - oldest_prior_close) / oldest_prior_close * 100

The MFE anchor for the dampener is the OLDEST prior's bucket avg_mfe_30d
(measures consumption since the prior cluster started).
"""
from __future__ import annotations
import io, sys, sqlite3, time
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CACHE_DB        = ROOT / '.cache' / 'barrier_outcomes.db'
COMPONENTS_PQ   = ROOT / '.cache' / 'miss_ledger' / 'components_v32_1825.parquet'
BARRIERS_PQ     = ROOT / '.cache' / 'runner' / 'barriers_30opt_w15_1825.parquet'

OUT_DIR = ROOT / '.cache' / 'continuation_boost'
OUT_DIR.mkdir(parents=True, exist_ok=True)
PEAKS_PQ        = OUT_DIR / 'peaks.parquet'
PRIORS_PQ       = OUT_DIR / 'priors_long.parquet'

# Gradient design: count ALL directional priors. Magnitude in formula does
# the gradient work — score 70 contributes 0.4×, score 95 contributes 0.9×.
PRIOR_SCORE_MIN_DIST_FROM_50 = 15   # |score-50| >= 15 to be considered a "directional prior"
LOOKBACK_MAX_DAYS = 60   # outer bound; sweep can shrink LBMAX <= this
LOOKBACK_MIN_DAYS = 7    # inner bound; sweep can grow LBMIN >= this

PEAK_CALL_GATE = 70   # admit 70-74 too (boost can promote into 75+/80+/etc.)
PEAK_PUT_GATE  = 29   # admit 26-29 too (components data caps at 29; mirror to puts)

BARRIER_SET_PRIOR = '30dte_generic'   # prior-win definition
BARRIER_SET_CURR  = '30dte_opt'       # current peak outcome (TP%)

# Bucket boundaries (cumulative buckets; each peak goes into ALL bukets it qualifies for downstream)
# For mfe anchor, use TOP qualifying bucket (most specific).
def assign_top_bucket(overall: int) -> str:
    if overall >= 95: return '95+'
    if overall >= 90: return '90+'
    if overall >= 85: return '85+'
    if overall >= 80: return '80+'
    if overall >= 75: return '75+'
    if overall >= 70: return '70+'
    if overall <= 5:  return '<5'
    if overall <= 10: return '<10'
    if overall <= 15: return '<15'
    if overall <= 20: return '<20'
    if overall <= 25: return '<25'
    if overall <= 30: return '<30'
    return None


# ---------------------------------------------------------------------------

def load_components() -> pl.DataFrame:
    print(f'[load] components from {COMPONENTS_PQ.name}')
    df = pl.read_parquet(COMPONENTS_PQ).select(['symbol', 'date', 'overall'])
    df = df.with_columns(pl.col('date').str.to_date())
    print(f'[load]   {len(df):,} score rows; range {df["date"].min()} -> {df["date"].max()}')
    return df


def load_barriers_30dgen() -> pl.DataFrame:
    """Load all 30dte_generic w=7,15 outcomes into a Polars DataFrame.
    Used for prior-win lookup.
    """
    print(f'[load] 30dte_generic w=7,15 from SQLite ...')
    t0 = time.time()
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, date, side, w_days, result, entry_close
        FROM barrier_outcomes
        WHERE barrier_set = '30dte_generic' AND w_days IN (7, 15)
    """)
    rows = cur.fetchall()
    conn.close()
    print(f'[load]   {len(rows):,} rows in {time.time()-t0:.1f}s; building DataFrame ...')
    df = pl.DataFrame(
        rows,
        schema=['symbol', 'date_str', 'side', 'w_days', 'result', 'entry_close'],
        orient='row',
    ).with_columns([
        pl.col('date_str').str.to_date().alias('date'),
        pl.col('w_days').cast(pl.Int32),
        pl.col('result').cast(pl.Int32),
        pl.col('entry_close').cast(pl.Float64),
    ]).drop('date_str')
    print(f'[load]   shaped: {df.shape}')
    return df


def load_barriers_30dopt_w15() -> pl.DataFrame:
    """Load 30dte_opt w=15d outcomes from parquet (already cached by miss_ledger pipeline)."""
    print(f'[load] 30dte_opt w=15d from {BARRIERS_PQ.name}')
    df = pl.read_parquet(BARRIERS_PQ).select(['symbol', 'date', 'side', 'result'])
    df = df.with_columns([
        pl.col('result').cast(pl.Int32),
    ])
    if df['date'].dtype == pl.Utf8:
        df = df.with_columns(pl.col('date').str.to_date())
    print(f'[load]   {len(df):,} rows')
    return df


def load_bucket_mfe_anchors() -> dict:
    """Lookup: bucket_label -> avg_mfe_30d (raw %). Source: ScoreAssessmentResult active 5y 30 DTE."""
    print('[load] bucket MFE anchors from ScoreAssessmentResult ...')
    from database.models.core import ScoreAssessmentResult, ScoreAssessmentRun, AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    runs = list(ScoreAssessmentRun.select().where(
        (ScoreAssessmentRun.version == av) & (ScoreAssessmentRun.lookback_days == 1825)
    ))
    runs_30 = [r for r in runs if getattr(r, 'dte_strategy', None) == '30']
    if not runs_30:
        raise SystemExit('No 5y 30 DTE assessment run for active version!')
    run = runs_30[0]
    rows = list(ScoreAssessmentResult.select().where(ScoreAssessmentResult.run == run))
    out = {}
    for r in rows:
        if r.avg_mfe_30d is not None:
            out[r.bucket] = float(r.avg_mfe_30d)
    print(f'[load]   {len(out)} bucket anchors:', out)
    return out


# ---------------------------------------------------------------------------

def build_peaks_table(comp: pl.DataFrame, opt_w15: pl.DataFrame, mfe_anchors: dict) -> pl.DataFrame:
    """Build per-peak feature table."""
    # Filter to peaks (calls >= CALL_GATE OR puts <= PUT_GATE).
    # Then drop the early window where no priors can exist.
    earliest = comp['date'].min()
    cutoff_lo = earliest + timedelta(days=LOOKBACK_MAX_DAYS + 5)
    peaks = comp.filter(
        ((pl.col('overall') >= PEAK_CALL_GATE) | (pl.col('overall') <= PEAK_PUT_GATE)) &
        (pl.col('date') >= cutoff_lo)
    ).with_columns([
        pl.when(pl.col('overall') >= 50).then(pl.lit('call')).otherwise(pl.lit('put')).alias('side'),
        pl.when(pl.col('overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('barrier_side'),
        (pl.col('overall') - 50).abs().alias('conviction'),
        pl.when(pl.col('overall') >= 50)
          .then(pl.col('overall') - 50)
          .otherwise(50 - pl.col('overall'))
          .alias('score_intent'),
        pl.col('overall').map_elements(assign_top_bucket, return_dtype=pl.Utf8).alias('bucket'),
    ])

    # MFE anchor for the peak's own bucket (used if no priors)
    mfe_self = pl.Series('mfe_anchor_self', [
        mfe_anchors.get(b) for b in peaks['bucket'].to_list()
    ])
    peaks = peaks.with_columns(mfe_self)

    # Join with current outcome (30dte_opt w=15d, side from barrier_side)
    out_curr = opt_w15.rename({'result': 'outcome_tp_15d', 'side': 'barrier_side'})
    peaks = peaks.join(out_curr, on=['symbol', 'date', 'barrier_side'], how='left')

    print(f'[peaks] {len(peaks):,} peaks  ({peaks.filter(pl.col("side")=="call").height:,} call, '
          f'{peaks.filter(pl.col("side")=="put").height:,} put)')
    print(f'[peaks] outcome coverage: '
          f'TP={peaks.filter(pl.col("outcome_tp_15d")==1).height:,}  '
          f'SL={peaks.filter(pl.col("outcome_tp_15d")==0).height:,}  '
          f'null={peaks.filter(pl.col("outcome_tp_15d").is_null()).height:,}')

    return peaks


def build_priors_long(comp: pl.DataFrame, peaks: pl.DataFrame, gen_barriers: pl.DataFrame,
                      mfe_anchors: dict) -> pl.DataFrame:
    """For each peak, find prior peaks of same side in [D-LBMAX, D-LBMIN] same symbol.

    Long-format output (one row per (peak, prior) pair).
    """
    print(f'[priors] scanning prior peaks for {len(peaks):,} current peaks ...')
    t0 = time.time()

    # Pre-filter comp to qualifying priors: |overall-50| >= MIN_DIST. Bucket by symbol.
    prior_pool = comp.filter(
        (pl.col('overall') - 50).abs() >= PRIOR_SCORE_MIN_DIST_FROM_50
    ).with_columns([
        pl.when(pl.col('overall') >= 50).then(pl.lit('call')).otherwise(pl.lit('put')).alias('prior_side'),
        pl.when(pl.col('overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('prior_barrier_side'),
        (pl.col('overall') - 50).abs().alias('prior_conviction'),
        pl.col('overall').alias('prior_score'),
        pl.col('date').alias('prior_date'),
        pl.col('overall').map_elements(assign_top_bucket, return_dtype=pl.Utf8).alias('prior_bucket'),
    ]).select(['symbol', 'prior_date', 'prior_score', 'prior_side', 'prior_barrier_side',
               'prior_conviction', 'prior_bucket'])

    # Cross-join in Polars: peaks on the LEFT, priors with same symbol on the RIGHT
    # then filter on date range and side match.
    peak_keys = peaks.select(['symbol', 'date', 'side', 'barrier_side'])
    print(f'[priors]   peak_keys={len(peak_keys):,}, prior_pool={len(prior_pool):,}')

    joined = peak_keys.join(prior_pool, on='symbol', how='inner').filter(
        (pl.col('side') == pl.col('prior_side')) &
        (pl.col('prior_date') < pl.col('date') - timedelta(days=LOOKBACK_MIN_DAYS - 1)) &
        (pl.col('prior_date') >= pl.col('date') - timedelta(days=LOOKBACK_MAX_DAYS))
    ).with_columns(
        ((pl.col('date') - pl.col('prior_date')).dt.total_days()).alias('gap_days')
    )
    print(f'[priors]   {len(joined):,} (peak, prior) pairs after date filter')

    # Now join with barrier_outcomes (30dte_generic) at w=7 and w=15 separately.
    gen_w7  = (gen_barriers.filter(pl.col('w_days') == 7)
               .select(['symbol', 'date', 'side', 'result', 'entry_close'])
               .rename({'date': 'prior_date', 'side': 'prior_barrier_side',
                        'result': 'win_7d_30dgen', 'entry_close': 'prior_close_w7'}))
    gen_w15 = (gen_barriers.filter(pl.col('w_days') == 15)
               .select(['symbol', 'date', 'side', 'result', 'entry_close'])
               .rename({'date': 'prior_date', 'side': 'prior_barrier_side',
                        'result': 'win_15d_30dgen', 'entry_close': 'prior_close_w15'}))

    joined = (joined
              .join(gen_w7,  on=['symbol', 'prior_date', 'prior_barrier_side'], how='left')
              .join(gen_w15, on=['symbol', 'prior_date', 'prior_barrier_side'], how='left'))

    # Prefer w7 entry_close (always the same as w15, but w7 is more reliably present)
    joined = joined.with_columns([
        pl.coalesce(['prior_close_w7', 'prior_close_w15']).alias('prior_close')
    ]).drop(['prior_close_w7', 'prior_close_w15'])

    # MFE anchor by prior bucket
    mfe_lookup = pl.DataFrame({
        'prior_bucket': list(mfe_anchors.keys()),
        'prior_mfe_anchor': list(mfe_anchors.values()),
    })
    joined = joined.join(mfe_lookup, on='prior_bucket', how='left')

    # Final select
    priors = joined.select([
        'symbol', 'date', 'prior_date', 'gap_days', 'prior_score', 'prior_conviction',
        'prior_bucket', 'prior_mfe_anchor', 'prior_close',
        'win_7d_30dgen', 'win_15d_30dgen',
    ]).sort(['symbol', 'date', 'gap_days'])

    print(f'[priors] built in {time.time()-t0:.1f}s')
    print(f'[priors]   final: {len(priors):,} prior references; '
          f'avg {len(priors)/len(peaks):.2f}/peak')

    # Coverage stats
    n_peaks_with_prior = (priors.group_by(['symbol', 'date'])
                          .agg(pl.len().alias('npriors'))
                          .filter(pl.col('npriors') > 0).height)
    print(f'[priors]   peaks with >=1 prior: {n_peaks_with_prior:,} '
          f'({100*n_peaks_with_prior/len(peaks):.1f}%)')

    # Win rate sanity
    n_w7_one  = priors.filter(pl.col('win_7d_30dgen')  == 1).height
    n_w7_zero = priors.filter(pl.col('win_7d_30dgen')  == 0).height
    n_w15_one  = priors.filter(pl.col('win_15d_30dgen') == 1).height
    n_w15_zero = priors.filter(pl.col('win_15d_30dgen') == 0).height
    if n_w7_one + n_w7_zero > 0:
        print(f'[priors]   win_7d  WR = {100*n_w7_one/(n_w7_one+n_w7_zero):.1f}%  '
              f'(n={n_w7_one+n_w7_zero:,})')
    if n_w15_one + n_w15_zero > 0:
        print(f'[priors]   win_15d WR = {100*n_w15_one/(n_w15_one+n_w15_zero):.1f}%  '
              f'(n={n_w15_one+n_w15_zero:,})')

    return priors


def main():
    print(f'continuation-boost feature build  (target: {OUT_DIR})')

    comp = load_components()
    opt_w15 = load_barriers_30dopt_w15()
    gen_barriers = load_barriers_30dgen()
    mfe_anchors = load_bucket_mfe_anchors()

    peaks = build_peaks_table(comp, opt_w15, mfe_anchors)
    priors = build_priors_long(comp, peaks, gen_barriers, mfe_anchors)

    # Add current_close to peaks (from gen_barriers w=7 entry_close — same as price on date)
    cur_close = (gen_barriers.filter(pl.col('w_days') == 7)
                 .select(['symbol', 'date', 'side', 'entry_close'])
                 .rename({'side': 'barrier_side', 'entry_close': 'current_close'}))
    peaks = peaks.join(cur_close, on=['symbol', 'date', 'barrier_side'], how='left')
    miss = peaks.filter(pl.col('current_close').is_null()).height
    print(f'[peaks] current_close coverage: {len(peaks)-miss:,}/{len(peaks):,} '
          f'(missing {miss})')

    peaks.write_parquet(PEAKS_PQ)
    priors.write_parquet(PRIORS_PQ)
    print(f'[done] wrote {PEAKS_PQ} ({len(peaks):,} rows)')
    print(f'[done] wrote {PRIORS_PQ} ({len(priors):,} rows)')


if __name__ == '__main__':
    main()
