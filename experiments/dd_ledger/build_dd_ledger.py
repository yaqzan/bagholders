"""DD-contribution ledger build — joins MC trade tape with miss-ledger features.

Inputs (per window):
  .cache/dd_ledger/tape_{label}.parquet       — per-trade rows (one per closed trade × seed)
  .cache/dd_ledger/episodes_{label}.parquet   — per-seed top-3 worst DD episodes

Joined per-signal features (from .cache/miss_ledger/ledger_v{ver}_1825.parquet):
  - score components (bb/trend/rsi/macd/stoch/ta), context (vol_sig, vol_mag, ema50_pct, etc.)
  - weight_info-derived (wadj, pre_regime, dampeners)
  - regime composite/multiplier
  - breadth_score at signal date

DD-contribution attribution (proportional, v1):
  For each (seed, episode) we identify trades that were OPEN at any point during
  the peak->trough sequence (entry_date <= trough_date AND exit_date >= start_date).
  Each such trade's `pnl_dollars = premium_cost * option_pnl` contributes to the
  episode's DD-PnL. dd_contribution_share = trade_pnl_dollars / episode_loss_dollars
  (signed: negative pnl in negative episode = positive share — i.e. trade caused DD).

Per-trade additional features (NEW for DD analysis):
  - in_worst_episode      — bool, is this trade open during seed's worst episode
  - dd_contribution_share — proportional attribution of this trade to worst episode's loss
  - entry_dd_band         — bin of entry_dd: ['none', 'shallow', 'mid', 'deep']
  - concurrent_total      — entry_open_calls + entry_open_puts
  - breadth_5d_chg        — 5-trading-day change in breadth_score at entry
  - regime_5d_persistence — bars since last regime label change (proxy via reg_mult bucket)

Output:
  .cache/dd_ledger/dd_ledger_v{ver}.parquet   — joined trade rows ready for analysis
"""
from __future__ import annotations
import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import time
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.cache' / 'dd_ledger'
ML_CACHE = ROOT / '.cache' / 'miss_ledger'
RUNNER_CACHE = ROOT / '.cache' / 'runner'

# Match monte_carlo.py WINDOWS labels
WINDOWS = ['2021', '2022', '2023', '2024', 'dip', '22-now', '2025', '5y']


def regime_label_expr(col='reg_mult'):
    return (pl.when(pl.col(col) <= 0.78).then(pl.lit('STRESS'))
              .when(pl.col(col) <= 0.88).then(pl.lit('CAUTION'))
              .when(pl.col(col) <= 1.00).then(pl.lit('NEUTRAL'))
              .when(pl.col(col) <= 1.05).then(pl.lit('HEALTHY'))
              .otherwise(pl.lit('BULL')))


def attribute_dd_contribution(trades: pl.DataFrame, episodes: pl.DataFrame) -> pl.DataFrame:
    """Add dd_contribution_share + in_worst_episode columns.

    A trade contributes to an episode if it was OPEN at any point during
    [start_date, trough_date]. Attribution is proportional: each open trade
    gets share = pnl_dollars / sum(pnl_dollars across all open trades in episode).

    For v1 we only attribute to the WORST episode (rank=0) per seed since
    that drives the max_dd metric used by the ship gate.
    """
    # Only worst episode per seed
    worst = episodes.filter(pl.col('rank') == 0).select(
        pl.col('seed'),
        pl.col('start_date').alias('ep_start'),
        pl.col('trough_date').alias('ep_trough'),
        (pl.col('peak_value') - pl.col('trough_value')).alias('ep_loss_dollars'),
    )

    # Join worst-episode bounds onto each trade by seed
    trades = trades.join(worst, on='seed', how='left')

    # in_worst_episode = trade was open at any time in [ep_start, ep_trough]
    trades = trades.with_columns(
        ((pl.col('entry_date') <= pl.col('ep_trough'))
         & (pl.col('exit_date') >= pl.col('ep_start'))
        ).fill_null(False).alias('in_worst_episode')
    )

    # pnl_dollars (signed)
    trades = trades.with_columns(
        (pl.col('premium_cost') * pl.col('option_pnl')).alias('pnl_dollars')
    )

    # Sum of in-episode trade dollar PnL per seed
    seed_ep_pnl = (trades.filter(pl.col('in_worst_episode'))
                          .group_by('seed')
                          .agg(pl.col('pnl_dollars').sum().alias('seed_ep_total_pnl')))
    trades = trades.join(seed_ep_pnl, on='seed', how='left')

    # share = trade_pnl_dollars / sum(in-episode trade pnl_dollars)
    # Sign convention: if seed_ep_total_pnl < 0 (the episode is a real DD),
    # a losing trade has share > 0 (contributes to DD).
    trades = trades.with_columns(
        pl.when(pl.col('in_worst_episode') & (pl.col('seed_ep_total_pnl') != 0))
          .then(pl.col('pnl_dollars') / pl.col('seed_ep_total_pnl'))
          .otherwise(0.0)
          .alias('dd_contribution_share')
    )
    return trades


def add_path_features(trades: pl.DataFrame) -> pl.DataFrame:
    """Add entry_dd_band + concurrent_total + breadth/regime trajectory features."""
    trades = trades.with_columns([
        # Entry DD band
        pl.when(pl.col('entry_dd') < 0.05).then(pl.lit('none'))
          .when(pl.col('entry_dd') < 0.20).then(pl.lit('shallow'))
          .when(pl.col('entry_dd') < 0.40).then(pl.lit('mid'))
          .otherwise(pl.lit('deep'))
          .alias('entry_dd_band'),
        # Total concurrent positions at entry
        (pl.col('entry_open_calls') + pl.col('entry_open_puts')).alias('concurrent_total'),
        # Concurrent ratio (call-heavy vs put-heavy book)
        pl.when((pl.col('entry_open_calls') + pl.col('entry_open_puts')) == 0)
          .then(0.5)
          .otherwise(pl.col('entry_open_calls')
                     / (pl.col('entry_open_calls') + pl.col('entry_open_puts')))
          .alias('concurrent_call_share'),
    ])
    return trades


def add_breadth_trajectory(trades: pl.DataFrame, breadth: pl.DataFrame) -> pl.DataFrame:
    """Add breadth_5d_chg = breadth_score(entry) - breadth_score(entry - 5 trd days).

    Uses calendar 7-day lookback as proxy for 5 trading days (good enough — breadth
    series is daily, sparse on weekends).
    """
    # Sort breadth by date for as-of join
    b_now = breadth.rename({'breadth_score': 'brd_now'}).sort('date')
    # Build a lagged version (date + 7 cal days)
    b_lag = breadth.rename({'breadth_score': 'brd_lag'}).with_columns(
        (pl.col('date').str.to_date() + pl.duration(days=7)).dt.strftime('%Y-%m-%d').alias('date')
    ).sort('date')

    # Join entry_date -> brd_now (asof on or before entry date)
    trades = trades.sort('entry_date')
    trades = trades.join_asof(b_now, left_on='entry_date', right_on='date', strategy='backward')
    trades = trades.join_asof(b_lag, left_on='entry_date', right_on='date', strategy='backward')
    trades = trades.with_columns(
        (pl.col('brd_now') - pl.col('brd_lag')).alias('breadth_5d_chg')
    )
    return trades


def main(ledger_version: int):
    print(f'[dd_ledger] joining tape × ledger (v{ledger_version})...')
    t0 = time.time()

    # Load all per-window tapes
    tape_dfs = []
    ep_dfs = []
    for w in WINDOWS:
        tape_path = CACHE / f'tape_{w}.parquet'
        ep_path   = CACHE / f'episodes_{w}.parquet'
        if not tape_path.exists():
            print(f'[dd_ledger]   missing {tape_path} — skipping {w}', flush=True)
            continue
        t = pl.read_parquet(tape_path).with_columns(pl.lit(w).alias('window'))
        e = pl.read_parquet(ep_path)
        # Episodes already have window_label
        tape_dfs.append(t)
        ep_dfs.append(e)
        print(f'[dd_ledger]   {w}: {len(t):,} trades, {len(e)} episodes', flush=True)

    if not tape_dfs:
        raise SystemExit('no tape data found — run monte_carlo.py with MC_TRADE_TAPE=1')

    trades = pl.concat(tape_dfs)
    episodes = pl.concat(ep_dfs)
    print(f'[dd_ledger] total: {len(trades):,} trades, {len(episodes)} episodes', flush=True)

    # Load miss-ledger features (per-signal context — independent of seed)
    ledger_path = ML_CACHE / f'ledger_v{ledger_version}_1825.parquet'
    if not ledger_path.exists():
        raise SystemExit(f'missing {ledger_path} — run miss_ledger/build_ledger.py first')
    feats = pl.read_parquet(ledger_path)
    print(f'[dd_ledger] features: {len(feats):,} rows', flush=True)

    # Keep only feature cols we want (drop barrier outcome + duplicate side/sigma)
    feat_cols = ['symbol', 'date', 'overall',
                 'bb', 'trend', 'rsi', 'macd', 'stoch', 'ta',
                 'vol_sig', 'vol_mag', 'ema50_pct', 'ema200_pct', 'bb_pos',
                 'reg_comp', 'reg_mult', 'velocity_7d',
                 'wadj', 'pre_regime', 'cap_dampened',
                 'exh_damp', 'ext_damp', 'mis_stress',
                 'sigma_pct', 'breadth_score']
    feats = feats.select([c for c in feat_cols if c in feats.columns]).unique(['symbol', 'date'])

    # Join trades × features on (sym_id == symbol, entry_date == date)
    trades = trades.rename({'sym_id': 'symbol', 'entry_date': 'date'})
    trades = trades.join(feats, on=['symbol', 'date'], how='left')
    # Restore tape-friendly names
    trades = trades.rename({'symbol': 'sym_id', 'date': 'entry_date'})
    print(f'[dd_ledger] joined: {len(trades):,} rows × {len(trades.columns)} cols', flush=True)
    n_unmatched = trades.filter(pl.col('overall').is_null()).height
    print(f'[dd_ledger]   unmatched (no feature row): {n_unmatched:,} '
          f'({n_unmatched/len(trades)*100:.1f}%)', flush=True)

    # Add path features
    trades = add_path_features(trades)
    # Regime label (keep both numeric mult and label)
    trades = trades.with_columns(regime_label_expr('reg_mult').alias('regime_label'))

    # DD-contribution attribution
    trades = attribute_dd_contribution(trades, episodes)
    print(f'[dd_ledger] attributed DD contribution to worst-episode trades', flush=True)

    # Breadth 5d trajectory
    breadth_path = RUNNER_CACHE / 'breadth_1825.parquet'
    if breadth_path.exists():
        breadth = pl.read_parquet(breadth_path)
        # Coerce date to string for join compat
        if breadth['date'].dtype != pl.Utf8:
            breadth = breadth.with_columns(pl.col('date').cast(pl.Utf8))
        trades = add_breadth_trajectory(trades, breadth)
        print(f'[dd_ledger] added breadth_5d_chg', flush=True)

    # Score buckets (cumulative)
    trades = trades.with_columns(
        pl.when(pl.col('score') >= 95).then(pl.lit('95+'))
          .when(pl.col('score') >= 90).then(pl.lit('90-94'))
          .when(pl.col('score') >= 85).then(pl.lit('85-89'))
          .when(pl.col('score') >= 80).then(pl.lit('80-84'))
          .when(pl.col('score') >= 75).then(pl.lit('75-79'))
          .when(pl.col('score') >= 70).then(pl.lit('70-74'))
          .when(pl.col('score') <= 5).then(pl.lit('0-5'))
          .when(pl.col('score') <= 10).then(pl.lit('6-10'))
          .when(pl.col('score') <= 15).then(pl.lit('11-15'))
          .when(pl.col('score') <= 20).then(pl.lit('16-20'))
          .when(pl.col('score') <= 25).then(pl.lit('21-25'))
          .otherwise(pl.lit('na'))
          .alias('score_bin')
    )

    out_path = CACHE / f'dd_ledger_v{ledger_version}.parquet'
    trades.write_parquet(out_path)
    print(f'[dd_ledger] wrote {out_path} ({len(trades):,} rows × {len(trades.columns)} cols) '
          f'in {time.time()-t0:.1f}s')

    # Quick sanity numbers
    print(f'\n[dd_ledger] worst-episode trade share: '
          f'{trades.filter(pl.col("in_worst_episode")).height / len(trades) * 100:.1f}%')
    print(f'[dd_ledger] window x outcome:')
    print(trades.group_by(['window', 'outcome']).len().sort(['window', 'outcome']))


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--version', type=int, default=32, help='miss-ledger version id (32 = v32)')
    args = p.parse_args()
    main(args.version)
