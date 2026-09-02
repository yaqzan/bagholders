"""Fast per-trade assessment driven by the barrier_outcomes cache.

Drop-in replacement for assess_scores.assess_peaks_in_memory() that pulls
pre-computed barrier outcomes from the SQLite cache instead of forward-walking
price bars per peak.

Speedup: ~10-12 min per variant -> ~10-30 sec per variant on 5y full-universe.

API:
    fast_assess_peaks(peaks, lookback_days, side='auto') -> dict
        peaks: iterable of objects with .symbol_id (str), .date (date), .overall (int)
        Returns same structure as assess_peaks_in_memory: {'bucketed_stats': {...}, ...}

Limitations vs forward-walk:
- Only the standard PERIODS = (1d,7d,15d,30d,60d,90d,150d) are supported
- Standard barrier params (K_high=1, M_high=2 / K_low=2, M_low=5)
- For ad-hoc K/M parameters, must fall back to forward-walk

Optional Polars backend (much faster aggregation): pass use_polars=True.
"""
from __future__ import annotations
import sqlite3
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

# Mirror constants from assess_scores
BUY_THRESHOLDS  = [95, 90, 85, 80, 75, 70]
SELL_THRESHOLDS = [30, 25, 20, 15, 10, 5]
THRESHOLD_KEYS  = [f'{t}+' for t in BUY_THRESHOLDS] + [f'<{t}' for t in SELL_THRESHOLDS]
PERIODS = [('1d', 1), ('7d', 7), ('15d', 15), ('30d', 30),
           ('60d', 60), ('90d', 90), ('150d', 150)]
PERIOD_LABELS = [p[0] for p in PERIODS]

ROOT = Path(__file__).resolve().parents[1]
CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'


def _peak_side(score):
    """Return 'low' for call (score >= 50), 'high' for put."""
    return 'low' if score >= 50 else 'high'


def _bucket_keys(score):
    """Return list of bucket labels this score belongs to (cumulative)."""
    keys = []
    if score >= 50:
        for t in BUY_THRESHOLDS:
            if score >= t:
                keys.append(f'{t}+')
    else:
        for t in SELL_THRESHOLDS:
            if score <= t:
                keys.append(f'<{t}')
    return keys


def _empty_bucket():
    return {
        'sample_count': 0,
        'wins_per_W': defaultdict(int),
        'totals_per_W': defaultdict(int),
        'returns_per_W': defaultdict(list),
        'mae_per_W': defaultdict(list),
        'mfe_per_W': defaultdict(list),
        'wins_unscaled_per_W': defaultdict(int),
        'totals_unscaled_per_W': defaultdict(int),
    }


def fast_assess_peaks(peaks, lookback_days=1825, use_polars=False):
    """Cache-driven assessment.

    peaks: iterable of objects with attrs .symbol_id (or .symbol), .date, .overall (or .score)
    Returns dict with the same shape as assess_peaks_in_memory().
    """
    # Normalize peaks into list of (symbol, date_iso, score, side)
    norm = []
    for p in peaks:
        sym = getattr(p, 'symbol_id', None) or getattr(p, 'symbol', None)
        d = getattr(p, 'date', None)
        score = getattr(p, 'overall', None) or getattr(p, 'score', None)
        if sym is None or d is None or score is None:
            continue
        d_iso = d.isoformat() if hasattr(d, 'isoformat') else str(d)
        norm.append((sym, d_iso, int(score), _peak_side(score)))

    if not norm:
        return _empty_result()

    # Bulk-fetch cache rows for ALL peaks across ALL periods in one query
    # Build a temporary table for the JOIN
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute("CREATE TEMP TABLE peaks (symbol TEXT, date TEXT, score INT, side TEXT)")
    conn.executemany("INSERT INTO peaks VALUES (?,?,?,?)", norm)
    conn.execute("CREATE INDEX peaks_idx ON peaks(symbol, date, side)")

    cur = conn.cursor()
    cur.execute("""
        SELECT p.symbol, p.date, p.score, p.side, b.w_days,
               b.result, b.exit_return, b.mae_pct, b.mfe_pct, b.result_u,
               b.entry_close, b.sigma_pct
        FROM peaks p
        JOIN barrier_outcomes b
          ON b.symbol = p.symbol AND b.date = p.date AND b.side = p.side
    """)
    rows = cur.fetchall()
    conn.close()

    if use_polars:
        return _aggregate_polars(rows)
    return _aggregate_dicts(rows)


def _aggregate_dicts(rows):
    """Pure-Python aggregation."""
    bucketed = {k: _empty_bucket() for k in THRESHOLD_KEYS}
    seen_keys = set()  # (symbol, date, w) -> avoid double-counting if same peak in multiple buckets
    sample_seen = set()  # for sample_count

    for r in rows:
        sym, dt, score, side, w, result, ret_pct, mae, mfe, res_u, entry, sigma = r
        b_keys = _bucket_keys(score)
        if not b_keys:
            continue
        # sample_count: increment once per (peak, w_label) per bucket
        for bk in b_keys:
            B = bucketed[bk]
            sk = (sym, dt, w, bk)
            if sk in sample_seen:
                continue
            sample_seen.add(sk)
            if w == 30:
                # sample_count tracks 30d-resolved peaks (matches assess_scores convention)
                B['sample_count'] += 1
            if result is not None:
                B['totals_per_W'][w] += 1
                B['wins_per_W'][w] += int(result)
                if ret_pct is not None:
                    B['returns_per_W'][w].append(ret_pct)
                if mae is not None:
                    B['mae_per_W'][w].append(mae)
                if mfe is not None:
                    B['mfe_per_W'][w].append(mfe)
            if res_u is not None:
                B['totals_unscaled_per_W'][w] += 1
                B['wins_unscaled_per_W'][w] += int(res_u)

    # Materialize stats in the format assess_peaks_in_memory expects
    bucketed_stats = {}
    for k, B in bucketed.items():
        out = {'sample_count': B['sample_count']}
        for label, w in PERIODS:
            n = B['totals_per_W'][w]
            wins = B['wins_per_W'][w]
            n_u = B['totals_unscaled_per_W'][w]
            wins_u = B['wins_unscaled_per_W'][w]
            out[f'win_rate_{label}'] = (wins / n * 100) if n else None
            out[f'win_rate_unscaled_{label}'] = (wins_u / n_u * 100) if n_u else None
            rets = B['returns_per_W'][w]
            maes = B['mae_per_W'][w]
            mfes = B['mfe_per_W'][w]
            out[f'avg_return_{label}'] = (sum(rets) / len(rets)) if rets else None
            out[f'avg_mae_{label}']    = (-sum(maes) / len(maes)) if maes else None  # stored as negative
            out[f'avg_mfe_{label}']    = (sum(mfes) / len(mfes)) if mfes else None
            avg_ret = out[f'avg_return_{label}']
            avg_mfe = out[f'avg_mfe_{label}']
            out[f'capture_ratio_{label}'] = (avg_ret / avg_mfe) if avg_mfe else None
        bucketed_stats[k] = out

    return {
        'bucketed_stats': bucketed_stats,
        'all_count': len(sample_seen),
    }


def _aggregate_polars(rows):
    """Polars-backed aggregation. ~10-30x faster for large peak counts."""
    import polars as pl
    if not rows:
        return _empty_result()
    df = pl.DataFrame({
        'symbol':      [r[0] for r in rows],
        'date':        [r[1] for r in rows],
        'score':       [r[2] for r in rows],
        'side':        [r[3] for r in rows],
        'w_days':      [r[4] for r in rows],
        'result':      [r[5] for r in rows],
        'exit_return': [r[6] for r in rows],
        'mae_pct':     [r[7] for r in rows],
        'mfe_pct':     [r[8] for r in rows],
        'result_u':    [r[9] for r in rows],
    })

    # Build bucket mapping: each peak score maps to multiple cumulative bucket keys.
    # Explode the dataframe per peak across its bucket keys.
    def _expand_buckets(score):
        if score >= 50:
            return [f'{t}+' for t in BUY_THRESHOLDS if score >= t]
        return [f'<{t}' for t in SELL_THRESHOLDS if score <= t]

    df = df.with_columns(
        pl.col('score').map_elements(_expand_buckets, return_dtype=pl.List(pl.Utf8))
          .alias('buckets')
    )
    df = df.explode('buckets').rename({'buckets': 'bucket'})

    # Group by (bucket, w_days) for win-rates and stats
    agg = df.group_by(['bucket', 'w_days']).agg([
        pl.count().alias('n_total'),
        pl.col('result').filter(pl.col('result').is_not_null()).count().alias('n_resolved'),
        pl.col('result').sum().alias('wins'),
        pl.col('result_u').sum().alias('wins_u'),
        pl.col('result_u').filter(pl.col('result_u').is_not_null()).count().alias('n_resolved_u'),
        pl.col('exit_return').mean().alias('avg_return'),
        pl.col('mae_pct').mean().alias('avg_mae'),
        pl.col('mfe_pct').mean().alias('avg_mfe'),
    ])

    # Pivot back to bucketed_stats shape
    bucketed_stats = {k: {'sample_count': 0} for k in THRESHOLD_KEYS}
    for row in agg.iter_rows(named=True):
        bk = row['bucket']
        w = row['w_days']
        label = next((l for l, w_ in PERIODS if w_ == w), None)
        if label is None: continue
        n = row['n_resolved'] or 0
        wins = row['wins'] or 0
        n_u = row['n_resolved_u'] or 0
        wins_u = row['wins_u'] or 0
        if bk not in bucketed_stats:
            bucketed_stats[bk] = {}
        bucketed_stats[bk][f'win_rate_{label}']           = (wins / n * 100) if n else None
        bucketed_stats[bk][f'win_rate_unscaled_{label}']  = (wins_u / n_u * 100) if n_u else None
        bucketed_stats[bk][f'avg_return_{label}']  = row['avg_return']
        bucketed_stats[bk][f'avg_mae_{label}']     = -row['avg_mae'] if row['avg_mae'] is not None else None
        bucketed_stats[bk][f'avg_mfe_{label}']     = row['avg_mfe']
        if row['avg_mfe']:
            bucketed_stats[bk][f'capture_ratio_{label}'] = (row['avg_return'] or 0) / row['avg_mfe']
        else:
            bucketed_stats[bk][f'capture_ratio_{label}'] = None

    # sample_count = unique peaks per bucket at 30d
    sc = (df.filter(pl.col('w_days') == 30)
            .group_by('bucket').agg(pl.col('symbol').n_unique().alias('sc')))
    sc_map = {row['bucket']: row['sc'] for row in sc.iter_rows(named=True)}
    for bk in bucketed_stats:
        bucketed_stats[bk]['sample_count'] = sc_map.get(bk, 0)

    return {
        'bucketed_stats': bucketed_stats,
        'all_count': len(df),
    }


def _empty_result():
    return {
        'bucketed_stats': {k: {'sample_count': 0} for k in THRESHOLD_KEYS},
        'all_count': 0,
    }


def fast_diff_assess(sim_scores, db_version, lookback_days=1825, use_polars=True, label_db='DB', label_sim='SIM'):
    """Drop-in replacement for ScoreSimulator.diff_assess().

    sim_scores: dict {(symbol, date): overall_score} from simulator.simulate()
    db_version: AlgorithmVersion id (or instance) to filter DB peaks
    """
    from datetime import timedelta
    from database.models.core import Score, AlgorithmVersion

    cutoff = _date.today() - timedelta(days=lookback_days)

    # Resolve version
    if isinstance(db_version, int):
        ver = AlgorithmVersion.get_or_none(AlgorithmVersion.id == db_version)
    else:
        ver = db_version

    # ── DB peaks ────────────────────────────────────────────────────
    q = Score.select(Score.symbol, Score.date, Score.overall).where(
        Score.date >= cutoff, Score.overall.is_null(False),
    )
    if ver:
        q = q.where(Score.version == ver)
    db_rows = list(q)

    class _FakePeak:
        __slots__ = ('symbol_id', 'date', 'overall')
        def __init__(self, sym, d, ov):
            self.symbol_id = sym
            self.date = d
            self.overall = ov

    # Apply qualifying filter (>=70 OR <=25) and per-symbol pruning (mirror simulator)
    def _prune(rows):
        by_sym = defaultdict(list)
        for r in rows:
            sym = r.symbol.symbol if hasattr(r.symbol, 'symbol') else r.symbol_id
            score = r.overall
            d = r.date
            if score is None:
                continue
            if score >= 70 or score <= 25:
                by_sym[sym].append((d, score))
        peaks = []
        for sym, entries in by_sym.items():
            date_set = {d for d, _ in entries}
            pruned = set()
            for d, score in sorted(entries, key=lambda x: abs(x[1] - 50), reverse=True):
                if (sym, d) in pruned:
                    continue
                peaks.append(_FakePeak(sym, d, score))
                from datetime import timedelta as _td
                for direction in (-1, 1):
                    walk = d + _td(days=direction)
                    while walk in date_set and (sym, walk) not in pruned:
                        pruned.add((sym, walk))
                        walk += _td(days=direction)
        return peaks

    db_peaks = _prune(db_rows)

    # ── Sim peaks ────────────────────────────────────────────────────
    sim_rows_obj = []
    class _SimRow:
        __slots__ = ('symbol_id', 'date', 'overall')
        def __init__(self, s, d, o):
            self.symbol_id, self.date, self.overall = s, d, o
    for (sym, d), score in sim_scores.items():
        if d < cutoff:
            continue
        sim_rows_obj.append(_SimRow(sym, d, score))
    # Re-prune sim against itself
    by_sym = defaultdict(list)
    for r in sim_rows_obj:
        if r.overall >= 70 or r.overall <= 25:
            by_sym[r.symbol_id].append((r.date, r.overall))
    sim_peaks = []
    for sym, entries in by_sym.items():
        date_set = {d for d, _ in entries}
        pruned = set()
        for d, score in sorted(entries, key=lambda x: abs(x[1] - 50), reverse=True):
            if (sym, d) in pruned:
                continue
            sim_peaks.append(_FakePeak(sym, d, score))
            from datetime import timedelta as _td
            for direction in (-1, 1):
                walk = d + _td(days=direction)
                while walk in date_set and (sym, walk) not in pruned:
                    pruned.add((sym, walk))
                    walk += _td(days=direction)

    print(f"[fast-diff-assess] DB peaks: {len(db_peaks)}  SIM peaks: {len(sim_peaks)}", flush=True)

    db_data  = fast_assess_peaks(db_peaks,  lookback_days, use_polars=use_polars)
    sim_data = fast_assess_peaks(sim_peaks, lookback_days, use_polars=use_polars)

    _print_diff(db_data, sim_data, label_db, label_sim)
    return db_data, sim_data


def _print_diff(old, new, l_old='DB', l_new='SIM'):
    print(f"\n=== Assessment Diff: {l_old} -> {l_new} ===")
    print(f"{'Bucket':>8} | {'N':>5}/{'':<4} |  {'WR15':>14}  {'WR30':>14}  {'Ret30':>14}  {'MAE30':>14}  {'MFE30':>14}  {'Cap30':>14}")
    print(f"{'':>8} | {l_old[:4]:>5}/{l_new[:3]:<4} |")
    print("-" * 130)
    active = [k for k in THRESHOLD_KEYS
              if (old['bucketed_stats'].get(k, {}).get('sample_count') or 0) > 0
              or (new['bucketed_stats'].get(k, {}).get('sample_count') or 0) > 0]
    for key in active:
        os = old['bucketed_stats'][key]
        ns = new['bucketed_stats'][key]
        no = os.get('sample_count') or 0
        nn = ns.get('sample_count') or 0
        cells = []
        for label, fld in [('WR15','win_rate_15d'), ('WR30','win_rate_30d'),
                           ('Ret30','avg_return_30d'), ('MAE30','avg_mae_30d'),
                           ('MFE30','avg_mfe_30d'), ('Cap30','capture_ratio_30d')]:
            ov = os.get(fld); nv = ns.get(fld)
            ov_s = f"{ov:.2f}" if ov is not None else '--'
            nv_s = f"{nv:.2f}" if nv is not None else '--'
            d = ((nv or 0) - (ov or 0)) if (ov is not None and nv is not None) else 0
            sign = '+' if d >= 0 else ''
            cells.append(f"{ov_s}->{nv_s}({sign}{d:.1f})")
        print(f"{key:>8} | {no:>5}/{nn:<4} |  {cells[0]:>14}  {cells[1]:>14}  {cells[2]:>14}  {cells[3]:>14}  {cells[4]:>14}  {cells[5]:>14}")
    print()
