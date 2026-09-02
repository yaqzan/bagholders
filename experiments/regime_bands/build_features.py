"""Build v45 score parquet for regime band sweep.

Pulls all (sym, date) score rows in last 5y for active version. Includes:
  - overall (post-regime, post-all-dampeners — what's stored)
  - pre_regime (from weight_info — pre-regime-multiplier overall)
  - regime_composite (from MarketRegime keyed by date)
  - opt_result_15 (from barrier_outcomes, side='low' & 'high')

Output: .cache/regime_bands/v45_5y.parquet

Linear approximation rationale:
  Score chain is: pre_regime → apply_regime(pre_regime, mult) → cascade of dampeners → overall
  Variant impact: shift = apply_regime(pre_regime, mult_v) - apply_regime(pre_regime, mult_0)
  Approximate: overall_v ≈ overall + shift  (ignores dampener gate-crossing)

  Validation: compare bucket counts using overall_v to actual stored overall under v45 bands
  (mult_0 = 1.0 for now since all stored). If approximation matches, sweep is reliable.
"""
from __future__ import annotations
import io, json, sqlite3, sys, time
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / '.cache' / 'regime_bands'
OUT_DIR.mkdir(parents=True, exist_ok=True)
BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'

LOOKBACK_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 1825


def main():
    from database.trader_database import DB
    from database.models.core import AlgorithmVersion, MarketRegime
    from datetime import date, timedelta

    active = AlgorithmVersion.get_active_scores_version()
    print(f'Active version: id={active.id} commit={active.git_commit[:8]}')

    cutoff = date.today()
    start = cutoff - timedelta(days=LOOKBACK_DAYS)
    print(f'Range: {start} → {cutoff}  ({LOOKBACK_DAYS} days)')

    # Pull v45 scores with weight_info → extract pre_regime
    print('[load] v45 scores...', flush=True)
    t0 = time.time()
    cur = DB.execute_sql(
        'SELECT symbol, date, overall, weight_info FROM scores '
        'WHERE version_id = %s AND date >= %s AND date <= %s',
        (active.id, start, cutoff)
    )
    rows = cur.fetchall()
    print(f'  {len(rows):,} rows  ({time.time()-t0:.0f}s)', flush=True)

    # Parse weight_info to extract pre_regime
    syms, dates, overalls, pre_regimes, mis_stress = [], [], [], [], []
    for sym, dt, ov, wi in rows:
        if not wi:
            continue
        try:
            info = json.loads(wi) if isinstance(wi, str) else wi
        except Exception:
            continue
        pr = info.get('pre_regime')
        if pr is None:
            continue
        syms.append(sym)
        dates.append(str(dt)[:10])
        overalls.append(int(ov))
        pre_regimes.append(int(pr))
        mis_stress.append(float(info.get('mis_stress', 0.0)))
    print(f'[parse] {len(syms):,} rows with pre_regime', flush=True)

    df_scores = pl.DataFrame({
        'symbol': syms,
        'date': dates,
        'overall': overalls,
        'pre_regime': pre_regimes,
        'mis_stress': mis_stress,
    })

    # MarketRegime: composite + multiplier per date
    print('[load] MarketRegime composites...', flush=True)
    t0 = time.time()
    mr = list(
        MarketRegime.select(
            MarketRegime.date,
            MarketRegime.regime_composite,
            MarketRegime.regime_multiplier,
        )
        .where(MarketRegime.date >= start, MarketRegime.regime_composite.is_null(False))
        .dicts()
    )
    df_mr = pl.DataFrame({
        'date': [str(r['date'])[:10] for r in mr],
        'composite': [float(r['regime_composite']) for r in mr],
        'mult_v45': [float(r['regime_multiplier']) for r in mr],
    })
    print(f'  {len(df_mr):,} regime rows  ({time.time()-t0:.0f}s)', flush=True)

    df = df_scores.join(df_mr, on='date', how='inner')
    print(f'[joined scores+regime] {len(df):,}')

    # Barrier outcomes (call + put)
    print('[load] barrier outcomes...', flush=True)
    t0 = time.time()
    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        call_wr = pl.read_database(
            "SELECT symbol, date, result AS opt_result_15_call "
            "FROM barrier_outcomes WHERE side='low' AND barrier_set='30dte_opt' AND w_days=15",
            connection=conn,
        )
        put_wr = pl.read_database(
            "SELECT symbol, date, result AS opt_result_15_put "
            "FROM barrier_outcomes WHERE side='high' AND barrier_set='30dte_opt' AND w_days=15",
            connection=conn,
        )
    finally:
        conn.close()
    print(f'  call WR: {len(call_wr):,}  put WR: {len(put_wr):,}  ({time.time()-t0:.0f}s)')

    df = df.join(call_wr, on=['symbol', 'date'], how='left')
    df = df.join(put_wr, on=['symbol', 'date'], how='left')

    out = OUT_DIR / f'v45_{LOOKBACK_DAYS}d.parquet'
    df.write_parquet(out)
    print(f'\n[done] → {out}  rows={len(df):,}', flush=True)

    # Summary
    print()
    print('Summary:')
    print(f'  Score range: pre_regime min={df["pre_regime"].min()}  max={df["pre_regime"].max()}')
    print(f'  Score range: overall    min={df["overall"].min()}     max={df["overall"].max()}')
    print(f'  Composite:   min={df["composite"].min():.1f}  max={df["composite"].max():.1f}')
    print(f'  Coverage: call_wr15 not-null = {df["opt_result_15_call"].is_not_null().sum():,}')
    print(f'            put_wr15 not-null = {df["opt_result_15_put"].is_not_null().sum():,}')


if __name__ == '__main__':
    main()
