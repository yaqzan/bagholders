"""Stage 2 Phase A — Baseline pin: WR7 vs option TP% gap by tier on v46 substrate.

Per assessment-backtest.md Stage 2:
  Stage 2's job is to bound the WR7→TP% tax — minimize the gap between what
  scoring admitted (Stage 1 winners) and what the option strategy actually
  captures. Some WR7-positive signals fire option SL on shallow dips.

Goal: pin the current state. For each (side, tier):
  - WR7  (directional truth, K=2σ/M=5σ at w=7d, generic barriers)
  - opt_TP%  (option-aligned, K=1.274σ/M=1.092σ for calls; w=15d on 30dte_opt cache)
  - gap = WR7 - opt_TP%  (the SL tax, in pp)

Caveat: The cached '30dte_opt' barrier_set was built with K_call=1.274 / M_call=1.092
(corresponds to TP=0.35 / SL=-0.30). The currently-shipped values are
TP=0.33 / SL=-0.27 (K=1.201 / M=0.983). The cache is ~6-10% loose on K/M.
For Phase A this approximation gives the directional pattern correctly.
For Phase B we'd need a cache rebuild against shipped K/M.

Output:
  - Per-tier WR7 vs opt_TP% gap table
  - Identification of largest-gap tiers (where Stage 2 has room)
  - avg_option_pnl_15d per tier (theta-trap detection)
  - Capital recycling efficiency: median exit_bars per tier (B5 baseline)
"""
from __future__ import annotations
import io, json, sqlite3, sys, time
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CACHE_DIR = ROOT / '.cache' / 'sl_tax_stage2'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'

LOOKBACK_DAYS = 1825


def build_features(version_id: int, refresh: bool = False) -> pl.DataFrame:
    """Pull v{N} signals (calls + puts) with WR7 and 30dte_opt outcomes joined."""
    cache_pq = CACHE_DIR / f'signals_v{version_id}_{LOOKBACK_DAYS}.parquet'
    if cache_pq.exists() and not refresh:
        t0 = time.time()
        df = pl.read_parquet(cache_pq)
        print(f'[load] {len(df):,} rows ({time.time()-t0:.1f}s)', flush=True)
        return df

    from database.trader_database import DB
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    print(f'[build] pulling v{version_id} signals (date >= {cutoff})...', flush=True)
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall, s.weight_info
        FROM scores s
        WHERE s.version_id = {version_id}
          AND s.date >= '{cutoff}'
          AND s.overall IS NOT NULL
          AND ((s.overall >= 70) OR (s.overall <= 25))
    """)
    rows = cur.fetchall()
    print(f'  {len(rows):,} signal rows ({time.time()-t0:.0f}s)', flush=True)

    syms, dates, overalls, sides = [], [], [], []
    for sym, d, ov, _winfo in rows:
        syms.append(sym)
        dates.append(d.isoformat() if hasattr(d, 'isoformat') else str(d))
        overalls.append(int(ov))
        sides.append('low' if ov >= 70 else 'high')  # call->low barrier, put->high

    df_sig = pl.DataFrame({
        'symbol': syms,
        'date': dates,
        'overall': overalls,
        'side': sides,
    })

    # Tag side
    df_sig = df_sig.with_columns(
        pl.when(pl.col('overall') >= 70).then(pl.lit('call'))
          .otherwise(pl.lit('put')).alias('signal_type')
    )

    # WR7 from 30dte_generic w=7
    print('[join] WR7 (30dte_generic w=7)...', flush=True)
    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        wr7 = pl.read_database(
            "SELECT symbol, date, side, result AS wr7 "
            "FROM barrier_outcomes "
            "WHERE barrier_set='30dte_generic' AND w_days=7",
            connection=conn,
        )
        # Option TP% from 30dte_opt w=15 (NOTE: cache K/M is 1.274/1.092, ~6% loose vs shipped)
        opt15 = pl.read_database(
            "SELECT symbol, date, side, "
            "       result AS opt_tp_15, "
            "       exit_return AS opt_pnl_15, "
            "       exit_bars AS exit_bars_15, "
            "       fire_type AS fire_type_15 "
            "FROM barrier_outcomes "
            "WHERE barrier_set='30dte_opt' AND w_days=15",
            connection=conn,
        )
    finally:
        conn.close()

    df = df_sig.join(wr7, on=['symbol', 'date', 'side'], how='left')
    df = df.join(opt15, on=['symbol', 'date', 'side'], how='left')
    df.write_parquet(cache_pq)
    print(f'[done] -> {cache_pq}  rows={len(df):,}', flush=True)
    return df


def tier_stats(df: pl.DataFrame, tier_filter, label: str):
    """Compute WR7, opt_TP%, gap, avg_option_pnl, median bars-to-resolution."""
    sub = df.filter(tier_filter)
    n_total = len(sub)
    if n_total == 0:
        return None
    res_wr7 = sub.filter(pl.col('wr7').is_not_null())
    res_opt = sub.filter(pl.col('opt_tp_15').is_not_null())
    n_wr7 = len(res_wr7)
    n_opt = len(res_opt)
    if n_wr7 < 10 or n_opt < 10:
        return None

    wr7_pct = float(res_wr7['wr7'].mean()) * 100
    opt_tp_pct = float(res_opt['opt_tp_15'].mean()) * 100
    gap = wr7_pct - opt_tp_pct

    # avg option pnl on resolved (excludes expires by default — but exit_return IS populated for expire too)
    avg_pnl = float(res_opt['opt_pnl_15'].mean()) if n_opt > 0 else 0.0
    # exit_bars only meaningful on TP/SL fires (not expires)
    fired = res_opt.filter(pl.col('fire_type_15').is_in([1, 2]))
    median_bars = float(fired['exit_bars_15'].median()) if len(fired) > 0 else None

    return dict(
        label=label,
        n_total=n_total,
        n_wr7=n_wr7,
        n_opt=n_opt,
        wr7_pct=wr7_pct,
        opt_tp_pct=opt_tp_pct,
        gap_pp=gap,
        avg_pnl_pct=avg_pnl,
        median_bars=median_bars,
    )


def main():
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    print(f'Active version: id={av.id} commit={av.git_commit}', flush=True)

    df = build_features(av.id)
    print()

    # Per-tier gap analysis
    print('=== Per-tier WR7 vs option TP% gap (v46 substrate, 5y) ===')
    print(f'{"tier":>10s}  {"N_tot":>7s}  {"N_wr7":>7s}  {"N_opt":>7s}  '
          f'{"WR7":>7s}  {"oTP%":>7s}  {"gap_pp":>8s}  {"avg_pnl":>8s}  {"med_bar":>8s}')
    print('-' * 110)

    tiers = [
        ('CALL 95+', (pl.col('overall') >= 95)),
        ('CALL 90-94', (pl.col('overall') >= 90) & (pl.col('overall') < 95)),
        ('CALL 85-89', (pl.col('overall') >= 85) & (pl.col('overall') < 90)),
        ('CALL 80-84', (pl.col('overall') >= 80) & (pl.col('overall') < 85)),
        ('CALL 75-79', (pl.col('overall') >= 75) & (pl.col('overall') < 80)),
        ('CALL 70-74', (pl.col('overall') >= 70) & (pl.col('overall') < 75)),
        ('CALL 95+ cum', (pl.col('overall') >= 95)),
        ('CALL 90+ cum', (pl.col('overall') >= 90)),
        ('CALL 85+ cum', (pl.col('overall') >= 85)),
        ('CALL 80+ cum', (pl.col('overall') >= 80)),
        ('CALL 75+ cum', (pl.col('overall') >= 75)),
        ('CALL 70+ cum', (pl.col('overall') >= 70)),
        ('PUT <5',  (pl.col('overall') < 6)),
        ('PUT 5-10',  (pl.col('overall') >= 6) & (pl.col('overall') < 11)),
        ('PUT 11-15', (pl.col('overall') >= 11) & (pl.col('overall') < 16)),
        ('PUT 16-20', (pl.col('overall') >= 16) & (pl.col('overall') < 21)),
        ('PUT 21-25', (pl.col('overall') >= 21) & (pl.col('overall') < 26)),
        ('PUT <5 cum', (pl.col('overall') < 6)),
        ('PUT <10 cum', (pl.col('overall') < 11)),
        ('PUT <15 cum', (pl.col('overall') < 16)),
        ('PUT <20 cum', (pl.col('overall') < 21)),
        ('PUT <25 cum', (pl.col('overall') < 26)),
    ]

    results = []
    for label, flt in tiers:
        if 'CALL' in label:
            flt = flt & (pl.col('side') == 'low')
        else:
            flt = flt & (pl.col('side') == 'high')
        s = tier_stats(df, flt, label)
        if s is None:
            continue
        results.append(s)
        med_bars_str = f'{s["median_bars"]:.1f}' if s['median_bars'] is not None else 'n/a'
        print(f'{s["label"]:>14s}  {s["n_total"]:>7,}  {s["n_wr7"]:>7,}  {s["n_opt"]:>7,}  '
              f'{s["wr7_pct"]:>6.2f}%  {s["opt_tp_pct"]:>6.2f}%  '
              f'{s["gap_pp"]:>+8.2f}  {s["avg_pnl_pct"]:>+8.2f}%  {med_bars_str:>8s}')

    # Identify largest-gap tiers (where Stage 2 has room)
    print()
    print('=== Tiers ranked by WR7→option TP% gap (largest tax = most room) ===')
    gaps = [(r['label'], r['gap_pp'], r['n_opt']) for r in results
            if 'cum' not in r['label'] and r['n_opt'] >= 100]
    gaps.sort(key=lambda x: -x[1])
    print(f'{"tier":>14s}  {"gap_pp":>8s}  {"N_opt":>7s}')
    for lbl, gap, n in gaps:
        print(f'{lbl:>14s}  {gap:>+8.2f}  {n:>7,}')

    # B2 / B3 baselines aren't computed here (smoke MC needed)
    print()
    print('Caveat: cache K/M = 1.274/1.092 (TP=0.35/SL=-0.30). Shipped is')
    print('TP=0.33/SL=-0.27 (K=1.201/M=0.983). Cache is ~6-10% loose.')
    print('Phase A pattern is directionally correct; Phase B needs cache rebuild.')


if __name__ == '__main__':
    main()
