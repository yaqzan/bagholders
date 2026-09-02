"""Miss-ledger build — per-signal feature row + outcome label.

Joins:
  - Score components (bb/trend/rsi/macd/stoch/ta) + context (vol, ema50%, regime) from MySQL
  - weight_info-derived features (wadj, pre_regime, dampeners) from runner cache
  - 30 DTE option-aligned barrier outcome at w=15d (TP/SL/EXP) from runner cache
  - MarketBreadth (breadth_score) from runner cache

Outputs:
  .cache/miss_ledger/ledger_v{ver}_{lookback}.parquet

Outcome labels (option-aligned, w=15d, 30dte_opt barriers):
  TP      = barrier result=1 (target touched first) -> CORRECT
  SL      = barrier result=0 (stop touched first)   -> MISS_STOP
  EXP     = barrier result=NULL (neither in 15d)    -> MISS_EXPIRE

For "miss-rate" we treat MISS = SL OR EXP (i.e. NOT TP). This matches the
TP%/win_rate_15d primary optimization metric — what fraction of qualifying
peaks fail to TP within the 15-day window.

Filter: peaks only (overall>=70 for calls or <=25 for puts), with the
side that matches the score direction (calls -> side='low' barriers,
puts -> side='high' barriers).
"""
from __future__ import annotations
import io, sys, json, time, sqlite3
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LOOKBACK_DAYS = 1825
RUNNER_CACHE = ROOT / '.cache' / 'runner'
LOCAL_CACHE = ROOT / '.cache' / 'miss_ledger'
LOCAL_CACHE.mkdir(parents=True, exist_ok=True)


def fetch_score_components(version_id: int, refresh: bool = False) -> pl.DataFrame:
    """Pull per-signal component scores + context from MySQL once and cache."""
    cache_pq = LOCAL_CACHE / f'components_v{version_id}_{LOOKBACK_DAYS}.parquet'
    if cache_pq.exists() and not refresh:
        t0 = time.time()
        df = pl.read_parquet(cache_pq)
        print(f'[ledger] components cache: {len(df):,} rows in {time.time()-t0:.1f}s', flush=True)
        return df

    from database.trader_database import DB
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    print(f'[ledger] building components from MySQL (cutoff={cutoff})...', flush=True)
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall,
               s.bb, s.trend, s.rsi, s.macd, s.stoch, s.technical_alignment,
               s.volume_signal, s.volume_magnitude,
               s.pct_from_ema50, s.pct_from_ema200, s.bb_position,
               s.regime_composite, s.regime_multiplier,
               s.score_velocity_7d
        FROM scores s
        WHERE s.version_id = {version_id}
          AND s.date >= '{cutoff}'
          AND s.overall IS NOT NULL
          AND ((s.overall >= 70) OR (s.overall <= 25))
    """)
    rows = cur.fetchall()
    cols = ['symbol', 'date', 'overall',
            'bb', 'trend', 'rsi', 'macd', 'stoch', 'ta',
            'vol_sig', 'vol_mag',
            'ema50_pct', 'ema200_pct', 'bb_pos',
            'reg_comp', 'reg_mult',
            'velocity_7d']
    data = {c: [] for c in cols}
    for r in rows:
        sym, d, ov, bb, tr, rs, mc, st, ta, vs, vm, e50, e200, bbp, rc, rm, v7 = r
        data['symbol'].append(sym)
        data['date'].append(d.isoformat() if hasattr(d, 'isoformat') else str(d))
        data['overall'].append(int(ov))
        data['bb'].append(int(bb) if bb is not None else None)
        data['trend'].append(int(tr) if tr is not None else None)
        data['rsi'].append(int(rs) if rs is not None else None)
        data['macd'].append(int(mc) if mc is not None else None)
        data['stoch'].append(int(st) if st is not None else None)
        data['ta'].append(int(ta) if ta is not None else None)
        data['vol_sig'].append(vs or 'NEUTRAL')
        data['vol_mag'].append(float(vm) if vm is not None else 0.0)
        data['ema50_pct'].append(float(e50) if e50 is not None else None)
        data['ema200_pct'].append(float(e200) if e200 is not None else None)
        data['bb_pos'].append(float(bbp) if bbp is not None else None)
        data['reg_comp'].append(float(rc) if rc is not None else None)
        data['reg_mult'].append(float(rm) if rm is not None else None)
        data['velocity_7d'].append(int(v7) if v7 is not None else None)

    df = pl.DataFrame(data)
    df.write_parquet(cache_pq)
    print(f'[ledger] cached {len(df):,} component rows in {time.time()-t0:.1f}s', flush=True)
    return df


def main(refresh: bool = False):
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    print(f'[ledger] active version: id={av.id} commit={av.git_commit}', flush=True)

    # Component scores + context
    comp = fetch_score_components(av.id, refresh=refresh)

    # weight_info-derived features
    scores_pq = RUNNER_CACHE / f'scores_v{av.id}_{LOOKBACK_DAYS}.parquet'
    if not scores_pq.exists():
        raise RuntimeError(f'Missing {scores_pq} — run fast_variant_runner first')
    weights = pl.read_parquet(scores_pq).select(
        ['symbol', 'date', 'wadj', 'pre_regime', 'cap_dampened',
         'exh_damp', 'ext_damp', 'mis_stress']
    )
    print(f'[ledger] weights cache: {len(weights):,} rows', flush=True)

    # Barrier outcomes (30 DTE option-aligned, w=15d)
    barriers_pq = RUNNER_CACHE / f'barriers_30opt_w15_{LOOKBACK_DAYS}.parquet'
    if not barriers_pq.exists():
        raise RuntimeError(f'Missing {barriers_pq}')
    barriers = pl.read_parquet(barriers_pq).select(
        ['symbol', 'date', 'side', 'result', 'exit_return',
         'entry_close', 'sigma_pct']
    )
    print(f'[ledger] barriers cache: {len(barriers):,} rows', flush=True)

    # Breadth
    breadth_pq = RUNNER_CACHE / f'breadth_{LOOKBACK_DAYS}.parquet'
    breadth = pl.read_parquet(breadth_pq) if breadth_pq.exists() else None
    if breadth is not None:
        print(f'[ledger] breadth cache: {len(breadth):,} rows', flush=True)

    # Side from overall — calls (>=70) need side='low' barriers, puts (<=25) need 'high'
    comp = comp.with_columns(
        pl.when(pl.col('overall') >= 70).then(pl.lit('low'))
          .when(pl.col('overall') <= 25).then(pl.lit('high'))
          .otherwise(None).alias('side'),
        pl.when(pl.col('overall') >= 70).then(pl.lit('CALL'))
          .when(pl.col('overall') <= 25).then(pl.lit('PUT'))
          .otherwise(None).alias('signal_type')
    )

    # Score buckets (cumulative — assign top bucket only for compactness; cumulative analysis later)
    comp = comp.with_columns(
        pl.when(pl.col('overall') >= 95).then(pl.lit('95+'))
          .when(pl.col('overall') >= 90).then(pl.lit('90-94'))
          .when(pl.col('overall') >= 85).then(pl.lit('85-89'))
          .when(pl.col('overall') >= 80).then(pl.lit('80-84'))
          .when(pl.col('overall') >= 75).then(pl.lit('75-79'))
          .when(pl.col('overall') >= 70).then(pl.lit('70-74'))
          .when(pl.col('overall') <= 5).then(pl.lit('0-5'))
          .when(pl.col('overall') <= 10).then(pl.lit('6-10'))
          .when(pl.col('overall') <= 15).then(pl.lit('11-15'))
          .when(pl.col('overall') <= 20).then(pl.lit('16-20'))
          .when(pl.col('overall') <= 25).then(pl.lit('21-25'))
          .otherwise(None).alias('score_bin')
    )

    # Join: comp + weights + barriers
    df = comp.join(weights, on=['symbol', 'date'], how='left')
    df = df.join(barriers, on=['symbol', 'date', 'side'], how='inner')
    if breadth is not None:
        df = df.join(breadth, on='date', how='left')
    print(f'[ledger] joined: {len(df):,} rows', flush=True)

    # Outcome label
    # result=1 -> TP (CORRECT), result=0 -> SL (MISS_STOP), null -> EXPIRE (MISS_EXPIRE)
    df = df.with_columns(
        pl.when(pl.col('result') == 1).then(pl.lit('TP'))
          .when(pl.col('result') == 0).then(pl.lit('SL'))
          .otherwise(pl.lit('EXP')).alias('outcome'),
        # is_miss = NOT TP (1 if SL or EXP)
        pl.when(pl.col('result') == 1).then(0).otherwise(1).alias('is_miss')
    )

    # Drop signals without forward window (recent — outcome may still resolve, but null means
    # entirely insufficient forward bars).  For pattern-mining we want resolved outcomes only.
    # Note: result=NULL means "insufficient forward history" — exclude those, but treat
    # result=NULL with sufficient bars as expire.  The barrier_cache treats both the same.
    # For analysis we keep all rows — EXP is a valid outcome.
    print(f'[ledger] outcome distribution:', flush=True)
    print(df.group_by('outcome').len().sort('outcome'), flush=True)
    print(f'[ledger] signal_type x outcome:', flush=True)
    print(df.group_by(['signal_type', 'outcome']).len().sort(['signal_type', 'outcome']), flush=True)

    # Save
    out_pq = LOCAL_CACHE / f'ledger_v{av.id}_{LOOKBACK_DAYS}.parquet'
    df.write_parquet(out_pq)
    print(f'[ledger] wrote {out_pq} ({len(df):,} rows, {len(df.columns)} cols)', flush=True)
    print(f'[ledger] columns: {df.columns}', flush=True)


if __name__ == '__main__':
    refresh = '--refresh' in sys.argv
    main(refresh=refresh)
