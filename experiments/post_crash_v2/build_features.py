"""Build post-crash feature parquet for put-peak cohort analysis.

For each put peak (overall <= 25) on the active version, attach:
  - ret_5d / ret_10d / ret_20d  (close return at signal vs N bars prior)
  - sigma_recent_10d, sigma_prior_30d, sigma_expansion (recent / prior)
  - gap_down_5d_count        (bars in last 5 where open < prev_close * 0.98)
  - days_since_52w_high      (trading bars since highest close in last 252)
  - ext_pct                  (close vs ema_50)
  - velocity_7d              (close return last 7 bars)

Join to:
  - market_regime: vix_score, regime_composite, breadth_score, regime_label
  - barrier_outcomes (side='high', barrier_set='30dte_generic'):
      result, exit_return, mae_pct, mfe_pct, sigma_pct  for w_days in {7,15,30,60}

Output: .cache/post_crash_v2/features_v{N}_1825.parquet
Pure read — no DB writes.
"""
from __future__ import annotations
import io, sys, sqlite3, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
OUT_DIR = ROOT / '.cache' / 'post_crash_v2'

LOOKBACK_DAYS = 1825


def main():
    from database.models.core import AlgorithmVersion
    from database.trader_database import DB

    av = AlgorithmVersion.get_active_scores_version()
    print(f'[build] Active version: id={av.id} ({av.git_commit[:8]})', flush=True)

    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    # --- Load put scores (overall <= 25) ---
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall
        FROM scores s
        WHERE s.version_id = {av.id}
          AND s.date >= '{cutoff}'
          AND s.overall IS NOT NULL
          AND s.overall <= 25
    """)
    rows = cur.fetchall()
    syms_set = sorted({r[0] for r in rows})
    scores = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
        'overall': [int(r[2]) for r in rows],
    })
    print(f'[build] put scores: {len(scores):,} across {len(syms_set):,} symbols ({time.time()-t0:.1f}s)', flush=True)

    # --- Load price history for those symbols, lookback + 60 buffer for ret_20d ---
    t0 = time.time()
    buf_cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS + 280)).isoformat()
    placeholders = ','.join(['%s'] * len(syms_set))
    cur = DB.execute_sql(
        f"""SELECT symbol, date, open, close FROM price_history
            WHERE date >= '{buf_cutoff}'
              AND symbol IN ({placeholders})""",
        tuple(syms_set),
    )
    ph_rows = cur.fetchall()
    ph = pl.DataFrame({
        'symbol': [r[0] for r in ph_rows],
        'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in ph_rows],
        'open': [float(r[2]) for r in ph_rows],
        'close': [float(r[3]) for r in ph_rows],
    }).sort(['symbol', 'date'])
    print(f'[build] price_history: {len(ph):,} rows ({time.time()-t0:.1f}s)', flush=True)

    # --- Compute window features per symbol ---
    t0 = time.time()
    ph = ph.with_columns([
        pl.col('close').pct_change(5).over('symbol').alias('ret_5d'),
        pl.col('close').pct_change(10).over('symbol').alias('ret_10d'),
        pl.col('close').pct_change(20).over('symbol').alias('ret_20d'),
        pl.col('close').pct_change(1).over('symbol').alias('ret_1d'),
        pl.col('close').shift(1).over('symbol').alias('prev_close'),
    ])
    ph = ph.with_columns([
        # sigma_recent_10d = stdev of ret_1d over last 10 bars (annualized in % terms below)
        pl.col('ret_1d').rolling_std(window_size=10).over('symbol').alias('sigma_recent_10d'),
        # sigma_prior_30d = stdev of ret_1d shifted 10 back, over 30 bars (the 30 bars BEFORE the recent 10)
        pl.col('ret_1d').shift(10).over('symbol').rolling_std(window_size=30).over('symbol').alias('sigma_prior_30d'),
        # gap-down today: open < prev_close * 0.98
        ((pl.col('open') < pl.col('prev_close') * 0.98)).cast(pl.Int8).alias('gap_down_today'),
        # 252-day rolling max close
        pl.col('close').rolling_max(window_size=252).over('symbol').alias('rmax_252'),
    ])
    ph = ph.with_columns([
        pl.col('gap_down_today').rolling_sum(window_size=5).over('symbol').alias('gap_down_5d_count'),
        pl.col('gap_down_today').rolling_sum(window_size=10).over('symbol').alias('gap_down_10d_count'),
        # sigma expansion ratio (recent / prior)
        (pl.col('sigma_recent_10d') / pl.col('sigma_prior_30d')).alias('sigma_expansion'),
        # pct below 252-day rolling high (negative = below high)
        ((pl.col('close') - pl.col('rmax_252')) / pl.col('rmax_252') * 100.0).alias('pct_from_252h'),
    ])
    print(f'[build] window features computed ({time.time()-t0:.1f}s)', flush=True)

    # --- Join indicators (ema_50) and velocity_7d ---
    t0 = time.time()
    placeholders2 = ','.join(['%s'] * len(syms_set))
    cur = DB.execute_sql(
        f"""SELECT symbol, date, ema_50 FROM indicators
            WHERE date >= '{buf_cutoff}'
              AND symbol IN ({placeholders2})""",
        tuple(syms_set),
    )
    ind_rows = cur.fetchall()
    ind = pl.DataFrame({
        'symbol': [r[0] for r in ind_rows],
        'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in ind_rows],
        'ema_50': [float(r[2]) if r[2] is not None else None for r in ind_rows],
    })
    print(f'[build] indicators: {len(ind):,} rows ({time.time()-t0:.1f}s)', flush=True)

    # Compute ext_pct = (close - ema50) / ema50 * 100, velocity_7d = close pct_change 7
    ph = ph.with_columns(pl.col('close').pct_change(7).over('symbol').alias('velocity_7d'))
    feats = ph.select([
        'symbol', 'date', 'close', 'ret_5d', 'ret_10d', 'ret_20d',
        'sigma_recent_10d', 'sigma_prior_30d', 'sigma_expansion',
        'gap_down_5d_count', 'gap_down_10d_count', 'pct_from_252h', 'velocity_7d',
    ])
    feats = feats.join(ind, on=['symbol', 'date'], how='left')
    feats = feats.with_columns(
        ((pl.col('close') - pl.col('ema_50')) / pl.col('ema_50') * 100.0).alias('ext_pct')
    )

    # --- Filter to put-peak rows only ---
    sf = scores.join(feats, on=['symbol', 'date'], how='left')
    print(f'[build] after feature join: {len(sf):,} put-peak rows', flush=True)

    # --- Load market_regime for date breadth/regime ---
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT date, regime_composite, vix_score, internal_breadth_score, vix_close, vix_10d_change
        FROM market_regime
        WHERE date >= '{cutoff}'
    """)
    mr = cur.fetchall()
    mreg = pl.DataFrame({
        'date': [r[0].isoformat() if hasattr(r[0], 'isoformat') else r[0] for r in mr],
        'regime_composite': [float(r[1]) if r[1] is not None else None for r in mr],
        'vix_score': [float(r[2]) if r[2] is not None else None for r in mr],
        'breadth_score': [float(r[3]) if r[3] is not None else None for r in mr],
        'vix_close': [float(r[4]) if r[4] is not None else None for r in mr],
        'vix_10d_pct': [float(r[5]) if r[5] is not None else None for r in mr],
    })
    sf = sf.join(mreg, on='date', how='left')
    print(f'[build] regime: {len(mreg):,} rows joined ({time.time()-t0:.1f}s)', flush=True)

    # --- Join barrier outcomes ---
    t0 = time.time()
    conn = sqlite3.connect(str(CACHE_DB))
    barriers = pl.read_database(
        f"""SELECT symbol, date, w_days, result, exit_return, mae_pct, mfe_pct, sigma_pct
            FROM barrier_outcomes
            WHERE side='high' AND barrier_set='30dte_generic'
              AND date >= '{cutoff}'
              AND w_days IN (7,15,30,60)""",
        connection=conn,
    )
    conn.close()
    print(f'[build] barriers: {len(barriers):,} rows ({time.time()-t0:.1f}s)', flush=True)

    # Pivot barriers wide so each put-peak becomes one row with W7/W15/W30/W60 columns
    barr_p = barriers.pivot(index=['symbol', 'date'], on='w_days',
                            values=['result', 'exit_return', 'mae_pct', 'mfe_pct', 'sigma_pct'],
                            aggregate_function='first')
    sf = sf.join(barr_p, on=['symbol', 'date'], how='left')
    print(f'[build] final shape: {sf.shape}', flush=True)
    print(f'[build] cols: {sf.columns}', flush=True)

    out = OUT_DIR / f'features_v{av.id}_1825.parquet'
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sf.write_parquet(out)
    print(f'[build] wrote {out} ({out.stat().st_size/1e6:.1f} MB)', flush=True)


if __name__ == '__main__':
    main()
