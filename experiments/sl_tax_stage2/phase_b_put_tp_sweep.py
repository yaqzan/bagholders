"""Stage 2 Phase B — Put-side TP sweep on v46 substrate.

Phase A finding: put-side WR7→option TP% gap is +22-24pp vs call-side +11-15pp.
The put-side is paying ~2x the SL tax. v32_optim recently retuned call-side
(TP_BASE 0.35→0.33, SL_BASE -0.30→-0.27) but kept PUT_TP=0.35/PUT_SL=-0.20.
Hypothesis: tighter PUT_TP captures more put dips before reversal.

Constraint per known-issues.md: "Never widen PUT_SL beyond -20%" — failed
DD floor in prior tests under bounded-fill MC. So PUT_SL stays at -0.20
(M=0.728σ at PREMIUM_MULT=1.82). Only PUT_TP varies.

Method:
1. Pre-load v46 put signals + per-symbol PriceHistory bars (one-time)
2. For each PUT_TP variant ∈ {0.20, 0.25, 0.28, 0.30, 0.32, 0.35-baseline, 0.40}:
   - Compute K = PUT_TP * PREMIUM_MULT / DELTA = PUT_TP * 1.82 / 0.5 = PUT_TP * 3.64
   - Numba forward-walk on each signal with (K_var, M_fixed=0.728, w=15)
   - Aggregate: per-tier opt_TP%, avg_pnl, median_bars_to_resolution
3. Apply Stage 2 B gates:
   B1: Stage 1 frozen — yes (we're not modifying scoring)
   B2/B3: smoke MC DD bound — DEFERRED (would need full MC; B-gate light pass via per-trade only here)
   B4: per-tier option TP% — winner must improve at least 1 tier without regressing any by >1pp
   B5: capital recycling — median bars-to-resolution ≤ baseline +1
"""
from __future__ import annotations
import io, json, sqlite3, sys, time
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CACHE_DIR = ROOT / '.cache' / 'sl_tax_stage2'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

LOOKBACK_DAYS = 1825
PREMIUM_MULT = 1.82
DELTA = 0.5
W_BARS = 15  # holding window in calendar days; barrier walk uses calendar gaps
PUT_SL_FIXED = -0.20  # fixed per known-issues constraint
M_FIXED = abs(PUT_SL_FIXED) * PREMIUM_MULT / DELTA  # 0.728


def load_signal_bars(version_id: int, refresh: bool = False) -> tuple[pl.DataFrame, dict]:
    """Pull v{N} signals + ALL forward PriceHistory bars per symbol.

    Returns:
      df_signals: per-signal rows with (symbol, date, overall, sigma_pct, base_idx)
      bars_by_sym: {sym: (closes, highs, lows, opens, dates) np arrays}
    """
    sig_pq = CACHE_DIR / f'all_signals_v{version_id}_{LOOKBACK_DAYS}.parquet'
    bars_pq = CACHE_DIR / f'bars_full_{LOOKBACK_DAYS}.parquet'

    # SIGNALS — only need puts for Phase B
    if sig_pq.exists() and not refresh:
        t0 = time.time()
        df_sig = pl.read_parquet(sig_pq)
        print(f'[load] signals parquet: {len(df_sig):,} rows ({time.time()-t0:.1f}s)', flush=True)
    else:
        from database.trader_database import DB
        cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
        print(f'[build] pulling v{version_id} signals (>= {cutoff})...', flush=True)
        t0 = time.time()
        cur = DB.execute_sql(f"""
            SELECT s.symbol, s.date, s.overall
            FROM scores s
            WHERE s.version_id = {version_id}
              AND s.date >= '{cutoff}'
              AND s.overall IS NOT NULL
              AND ((s.overall >= 70) OR (s.overall <= 25))
            ORDER BY s.symbol, s.date
        """)
        rows = cur.fetchall()
        df_sig = pl.DataFrame({
            'symbol': [r[0] for r in rows],
            'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else str(r[1]) for r in rows],
            'overall': [int(r[2]) for r in rows],
        })
        df_sig.write_parquet(sig_pq)
        print(f'  built+cached {len(df_sig):,} signals ({time.time()-t0:.0f}s)', flush=True)

    # BARS — all PriceHistory for needed symbols within window
    syms_needed = sorted(set(df_sig['symbol'].to_list()))
    print(f'[bars] need {len(syms_needed):,} symbols', flush=True)
    if bars_pq.exists() and not refresh:
        t0 = time.time()
        df_bars = pl.read_parquet(bars_pq)
        print(f'[load] bars parquet: {len(df_bars):,} rows ({time.time()-t0:.1f}s)', flush=True)
    else:
        from database.trader_database import DB
        # Pull from earliest signal date - 70d (for sigma) to latest signal date + 30d (for forward walk)
        min_date = df_sig['date'].min()
        max_date = df_sig['date'].max()
        cutoff_lo = (date.fromisoformat(min_date) - timedelta(days=80)).isoformat()
        cutoff_hi = (date.fromisoformat(max_date) + timedelta(days=40)).isoformat()
        print(f'[build] pulling PriceHistory ({cutoff_lo} to {cutoff_hi})...', flush=True)
        t0 = time.time()
        # Chunk symbols to avoid massive single query
        chunks = []
        chunk_size = 100
        for i in range(0, len(syms_needed), chunk_size):
            batch = syms_needed[i:i+chunk_size]
            placeholders = ','.join(['%s'] * len(batch))
            cur = DB.execute_sql(
                f"SELECT symbol, date, open, high, low, close "
                f"FROM price_history "
                f"WHERE symbol IN ({placeholders}) "
                f"  AND date >= %s AND date <= %s",
                tuple(batch) + (cutoff_lo, cutoff_hi),
            )
            chunks.extend(cur.fetchall())
            if (i // chunk_size) % 5 == 0:
                print(f'  ... {i+len(batch)}/{len(syms_needed)} symbols, {len(chunks):,} bars ({time.time()-t0:.0f}s)', flush=True)
        print(f'  total {len(chunks):,} bars ({time.time()-t0:.0f}s)', flush=True)
        df_bars = pl.DataFrame({
            'symbol': [r[0] for r in chunks],
            'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else str(r[1]) for r in chunks],
            'open': [float(r[2]) if r[2] is not None else 0.0 for r in chunks],
            'high': [float(r[3]) if r[3] is not None else 0.0 for r in chunks],
            'low':  [float(r[4]) if r[4] is not None else 0.0 for r in chunks],
            'close': [float(r[5]) if r[5] is not None else 0.0 for r in chunks],
        }).sort(['symbol', 'date'])
        df_bars.write_parquet(bars_pq)
        print(f'  cached {len(df_bars):,} bars ({time.time()-t0:.0f}s)', flush=True)

    # Build per-symbol numpy arrays
    print('[arrays] building per-symbol numpy arrays...', flush=True)
    t0 = time.time()
    bars_by_sym = {}
    for sym, group in df_bars.group_by('symbol'):
        sym = sym[0] if isinstance(sym, tuple) else sym
        # group already sorted by symbol (and we sort by date globally)
        g = group.sort('date')
        bars_by_sym[sym] = {
            'dates': g['date'].to_list(),
            'closes': np.array(g['close'].to_list(), dtype=np.float64),
            'highs':  np.array(g['high'].to_list(),  dtype=np.float64),
            'lows':   np.array(g['low'].to_list(),   dtype=np.float64),
            'opens':  np.array(g['open'].to_list(),  dtype=np.float64),
        }
        # date->index
        bars_by_sym[sym]['date_to_idx'] = {d: i for i, d in enumerate(bars_by_sym[sym]['dates'])}
    print(f'  {len(bars_by_sym):,} symbols, total bars cached ({time.time()-t0:.0f}s)', flush=True)
    return df_sig, bars_by_sym


def realized_vol_pct(closes: np.ndarray, idx: int, lookback: int = 60) -> float:
    """60-day realized daily stdev as %, computed at idx."""
    lo = max(0, idx - lookback - 1)
    if idx - lo < 5:
        return 0.0
    sub = closes[lo:idx+1]
    if len(sub) < 5:
        return 0.0
    rets = np.diff(sub) / sub[:-1] * 100.0
    if len(rets) < 5:
        return 0.0
    return float(np.std(rets, ddof=1))


def walk_put_signals(df_sig: pl.DataFrame, bars_by_sym: dict, K: float, M: float, w_bars: int) -> pl.DataFrame:
    """Apply put forward walk to all qualifying put signals (overall <= 25)."""
    from database.barrier_walk_numba import walk_one_put

    puts = df_sig.filter(pl.col('overall') <= 25)
    syms = puts['symbol'].to_list()
    dates = puts['date'].to_list()
    overalls = puts['overall'].to_list()

    n = len(puts)
    results = np.full(n, -1, dtype=np.int64)
    exit_bars = np.zeros(n, dtype=np.int64)
    exit_returns = np.zeros(n, dtype=np.float64)
    sigmas = np.zeros(n, dtype=np.float64)
    skipped = 0
    for i in range(n):
        sym = syms[i]
        d = dates[i]
        if sym not in bars_by_sym:
            skipped += 1
            continue
        bars = bars_by_sym[sym]
        idx = bars['date_to_idx'].get(d)
        if idx is None:
            skipped += 1
            continue
        sigma = realized_vol_pct(bars['closes'], idx, 60)
        if sigma <= 0:
            skipped += 1
            continue
        sigmas[i] = sigma
        out = walk_one_put(bars['closes'], bars['highs'], bars['lows'], bars['opens'],
                            idx, w_bars, sigma, K, M)
        results[i] = out[0]
        exit_bars[i] = out[1]
        exit_returns[i] = out[3]
    return pl.DataFrame({
        'symbol': syms,
        'date': dates,
        'overall': overalls,
        'sigma_pct': sigmas,
        'result': results,
        'exit_bars': exit_bars,
        'exit_return': exit_returns,
    })


def evaluate_variant(df_sig: pl.DataFrame, bars_by_sym: dict, put_tp: float):
    """Evaluate a put-TP variant. Returns per-tier stats dict."""
    K = abs(put_tp) * PREMIUM_MULT / DELTA
    M = M_FIXED
    walked = walk_put_signals(df_sig, bars_by_sym, K, M, W_BARS)
    resolved = walked.filter(pl.col('result') != -1)

    tiers = [
        ('all', None),
        ('<5',   pl.col('overall') < 6),
        ('5-10', (pl.col('overall') >= 6)  & (pl.col('overall') < 11)),
        ('11-15',(pl.col('overall') >= 11) & (pl.col('overall') < 16)),
        ('16-20',(pl.col('overall') >= 16) & (pl.col('overall') < 21)),
        ('21-25',(pl.col('overall') >= 21) & (pl.col('overall') < 26)),
    ]
    out = {}
    for tag, flt in tiers:
        sub = resolved if flt is None else resolved.filter(flt)
        if len(sub) < 30:
            continue
        # result: 1=TP, 0=SL or expire
        n = len(sub)
        n_tp = int((sub['result'] == 1).sum())
        wr = n_tp / n * 100
        avg_ret = float(sub['exit_return'].mean())
        fired = sub.filter(pl.col('result').is_in([0, 1])) # both TP and SL fired (not expire)
        median_bars = float(fired['exit_bars'].median()) if len(fired) > 0 else None
        out[tag] = dict(n=n, wr_pct=wr, avg_pnl=avg_ret, median_bars=median_bars)
    return out


def main():
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    print(f'Active version: id={av.id} commit={av.git_commit}', flush=True)

    df_sig, bars_by_sym = load_signal_bars(av.id)

    # Variants — PUT_TP from 0.20 to 0.40, SL fixed at -0.20
    variants = [0.20, 0.25, 0.28, 0.30, 0.32, 0.35, 0.38, 0.40]
    print(f'\n=== Stage 2 Phase B: PUT_TP sweep (PUT_SL fixed at {PUT_SL_FIXED}) ===')
    print(f'PREMIUM_MULT={PREMIUM_MULT}  DELTA={DELTA}  W_BARS={W_BARS}')
    print(f'{"PUT_TP":>7s}  {"K":>6s}  {"M":>6s}  {"tier":>7s}  {"N":>6s}  {"opt_TP":>7s}  {"avg_pnl":>8s}  {"med_bars":>9s}')
    print('-' * 80)

    all_results = {}
    for tp in variants:
        K = tp * PREMIUM_MULT / DELTA
        t0 = time.time()
        stats = evaluate_variant(df_sig, bars_by_sym, tp)
        all_results[tp] = stats
        for tag in ['all', '<5', '5-10', '11-15', '16-20', '21-25']:
            if tag not in stats:
                continue
            s = stats[tag]
            mb = f'{s["median_bars"]:.1f}' if s['median_bars'] is not None else 'n/a'
            print(f'{tp:>7.3f}  {K:>6.3f}  {M_FIXED:>6.3f}  {tag:>7s}  {s["n"]:>6,}  '
                  f'{s["wr_pct"]:>6.2f}%  {s["avg_pnl"]:>+8.3f}  {mb:>9s}')
        print(f'    ({time.time()-t0:.0f}s)', flush=True)
        print()

    # Cross-variant comparison: per-tier opt_TP% across variants
    print('\n=== Cross-variant opt_TP% by tier ===')
    print(f'{"tier":>7s}  ' + '  '.join(f'{tp:>5.2f}' for tp in variants))
    for tag in ['all', '<5', '5-10', '11-15', '16-20', '21-25']:
        row = [f'{tag:>7s}']
        for tp in variants:
            s = all_results[tp].get(tag)
            if s is None:
                row.append(f'{"n/a":>5s}')
            else:
                row.append(f'{s["wr_pct"]:>5.1f}')
        print('  '.join(row) + '%')

    print('\n=== Cross-variant avg_pnl by tier ===')
    print(f'{"tier":>7s}  ' + '  '.join(f'{tp:>5.2f}' for tp in variants))
    for tag in ['all', '<5', '5-10', '11-15', '16-20', '21-25']:
        row = [f'{tag:>7s}']
        for tp in variants:
            s = all_results[tp].get(tag)
            if s is None:
                row.append(f'{"n/a":>5s}')
            else:
                row.append(f'{s["avg_pnl"]:>+5.2f}')
        print('  '.join(row))

    # Persist results JSON
    out_json = ROOT / 'experiments' / 'sl_tax_stage2' / 'phase_b_put_tp_results.json'
    with open(out_json, 'w') as f:
        json.dump({str(tp): all_results[tp] for tp in variants}, f, indent=2, default=str)
    print(f'\n[saved] {out_json}')


if __name__ == '__main__':
    main()
