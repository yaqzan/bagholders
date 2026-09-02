"""Build call-peak features for the BSD (Breadth Score Dampener) investigation.

For each call peak (overall >= 75) on the active version, attach:
  - regime_composite, vix_score, breadth_score (= internal_breadth_score), regime_multiplier
  - barrier outcomes from BOTH sets, side='low' (calls), w_days in {7, 15, 30}:
      30dte_generic   (K=2.0σ, M=5.0σ — score-calibration target)
      30dte_opt       (option TP barrier — what F3F responds to / what we trade in MC)

Output: .cache/breadth_score_dampener/calls_v{N}_1825.parquet

Pure read — no DB writes. Mirrors experiments/post_crash_v2/build_features.py
adapted for the call side (side='low').
"""
from __future__ import annotations
import io, sys, sqlite3, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
OUT_DIR = ROOT / '.cache' / 'breadth_score_dampener'

LOOKBACK_DAYS = 1825


def main():
    from database.models.core import AlgorithmVersion
    from database.trader_database import DB

    av = AlgorithmVersion.get_active_scores_version()
    print(f'[build] Active version: id={av.id} ({av.git_commit[:8]})', flush=True)

    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    # --- Load CALL scores (overall >= 75) ---
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall
        FROM scores s
        WHERE s.version_id = {av.id}
          AND s.date >= '{cutoff}'
          AND s.overall IS NOT NULL
          AND s.overall >= 75
    """)
    rows = cur.fetchall()
    scores = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
        'overall': [int(r[2]) for r in rows],
    })
    print(f'[build] call peaks (>=75): {len(scores):,} ({time.time()-t0:.1f}s)', flush=True)

    # --- market_regime (composite/multiplier — only 1y of internal_breadth_score backfilled, ignore that col) ---
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT date, regime_composite, vix_score, regime_multiplier, vix_close
        FROM market_regime
        WHERE date >= '{cutoff}'
    """)
    mr = cur.fetchall()
    mreg = pl.DataFrame({
        'date': [r[0].isoformat() if hasattr(r[0], 'isoformat') else r[0] for r in mr],
        'regime_composite': [float(r[1]) if r[1] is not None else None for r in mr],
        'vix_score': [float(r[2]) if r[2] is not None else None for r in mr],
        'regime_multiplier': [float(r[3]) if r[3] is not None else None for r in mr],
        'vix_close': [float(r[4]) if r[4] is not None else None for r in mr],
    })
    sf = scores.join(mreg, on='date', how='left')
    print(f'[build] regime joined: {len(mreg):,} regime rows -> {len(sf):,} call rows ({time.time()-t0:.1f}s)', flush=True)

    # --- market_breadth (the actual 5y-backfilled breadth_score source) ---
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT date, breadth_score, mcclellan_oscillator, pct_above_ema50, pct_above_ema200
        FROM market_breadth
        WHERE date >= '{cutoff}'
    """)
    mb = cur.fetchall()
    mbr = pl.DataFrame({
        'date': [r[0].isoformat() if hasattr(r[0], 'isoformat') else r[0] for r in mb],
        'breadth_score': [float(r[1]) if r[1] is not None else None for r in mb],
        'mcclellan_osc': [float(r[2]) if r[2] is not None else None for r in mb],
        'pct_above_ema50': [float(r[3]) if r[3] is not None else None for r in mb],
        'pct_above_ema200': [float(r[4]) if r[4] is not None else None for r in mb],
    })
    sf = sf.join(mbr, on='date', how='left')
    print(f'[build] market_breadth joined: {len(mbr):,} breadth rows -> {len(sf):,} call rows ({time.time()-t0:.1f}s)', flush=True)

    # --- Barrier outcomes for BOTH sets, side='low' (calls), w_days=7/15/30 ---
    t0 = time.time()
    conn = sqlite3.connect(str(CACHE_DB))

    for bset, prefix in [('30dte_generic', 'gen'), ('30dte_opt', 'opt')]:
        barriers = pl.read_database(
            f"""SELECT symbol, date, w_days, result, exit_return, mae_pct, mfe_pct
                FROM barrier_outcomes
                WHERE side='low' AND barrier_set='{bset}'
                  AND date >= '{cutoff}'
                  AND w_days IN (7, 15, 30)""",
            connection=conn,
        )
        print(f'[build] barriers({bset}, side=low): {len(barriers):,} rows', flush=True)

        # Pivot wide so each call-peak row gets columns w7/w15/w30
        wide = barriers.pivot(
            index=['symbol', 'date'], on='w_days',
            values=['result', 'exit_return', 'mae_pct', 'mfe_pct'],
            aggregate_function='first',
        )
        # Rename to add prefix (gen_ or opt_)
        renames = {c: f'{prefix}_{c}' for c in wide.columns if c not in ('symbol', 'date')}
        wide = wide.rename(renames)
        sf = sf.join(wide, on=['symbol', 'date'], how='left')

    conn.close()
    print(f'[build] barriers joined ({time.time()-t0:.1f}s)', flush=True)
    print(f'[build] final shape: {sf.shape}', flush=True)

    out = OUT_DIR / f'calls_v{av.id}_1825.parquet'
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sf.write_parquet(out)
    print(f'[build] wrote {out} ({out.stat().st_size/1e6:.1f} MB)', flush=True)


if __name__ == '__main__':
    main()
