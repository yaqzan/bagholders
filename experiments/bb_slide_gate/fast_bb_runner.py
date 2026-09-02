"""
Fast BB-component-score variant runner.

Optimized path for changes that ONLY modify the BB component score (e.g.
slide-gate refinements, base-score tweaks). 4-30× faster than the simulator
because:

  1. Bulk-loads everything once via Polars DataFrames + Parquet cache
     (instant reload across runs).
  2. Reads RSI/MACD/Stoch/TA/trend from existing v31 scores — does NOT
     recompute them. Only the BB component is recalculated per (symbol, date).
  3. Re-uses the already-cached barrier_outcomes SQLite cache for assessment.
  4. Multiprocessing across symbols (each process is independent).

Assumes the BB change does NOT alter:
  - trend, RSI, MACD, Stoch, TA component scores (they're read from DB)
  - weekly adjustment, volume amplifier, regime multiplier (read from DB)

If a future change DOES touch other components, this runner won't apply.
For those, use the slow simulator path.

Per-variant runtime targets:
  Cold (first call, parquet build): ~60-90 sec
  Warm (parquet hit, multiprocessing): ~15-30 sec on 5y full universe

Usage
-----
    from experiments.bb_slide_gate.fast_bb_runner import FastBBRunner

    runner = FastBBRunner(lookback_days=1825)
    runner.load()  # ~60s first call, ~5s reload

    # Evaluate any BB variant by passing a custom bb_score_fn
    result = runner.evaluate_bb_variant(my_bb_fn, label='trend-gated-slide')
    # result['bucketed_stats'] matches assess_peaks_in_memory output shape
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

if __name__ == '__main__' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / '.cache' / 'fast_bb_runner'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
BARRIER_CACHE = ROOT / '.cache' / 'barrier_outcomes.db'

BUY_THRESHOLDS = [95, 90, 85, 80, 75, 70]
SELL_THRESHOLDS = [30, 25, 20, 15, 10, 5]
THRESHOLD_KEYS = [f'{t}+' for t in BUY_THRESHOLDS] + [f'<{t}' for t in SELL_THRESHOLDS]
PERIODS = [('1d', 1), ('7d', 7), ('15d', 15), ('30d', 30),
           ('60d', 60), ('90d', 90), ('150d', 150)]


# Type for a BB scoring function:
#   bb_fn(trend_score, indicator_window, ph_close_window) -> int
#
# Where:
#   indicator_window is a list of dicts (or rows) with keys
#     {date, upper_band, middle_band, lower_band} for the LAST 11 bars
#     (current + 10 prior), DESCENDING by date.
#   ph_close_window is a parallel list of close prices (DESCENDING).
#
# Returns: BB component score 0-100, or None if insufficient data.
BBFn = Callable[[float, list, list], Optional[int]]


# ---------------------------------------------------------------------------
# Volume-multiplier cache builder (stateful per-symbol, parallelizable across symbols)
# ---------------------------------------------------------------------------

def _build_vol_mult_for_symbol(args):
    """Walk price history ascending for one symbol, calling the volume amplifier
    at each date with accumulated decay state. Returns list of dicts."""
    sym, ph_rows_asc, earnings_set = args
    from volume_amplifier import get_volume_multiplier_from_cache

    # ph_rows_asc: list of dicts with date + open/high/low/close/volume + adj_close
    n = len(ph_rows_asc)
    rows_desc_acc = []  # built lazily
    out = []
    prior: Dict[date, Tuple[str, float, int]] = {}

    # Convert to objects with .date/.close/etc attributes (volume_amp expects rows)
    class _Row:
        __slots__ = ('date', 'open', 'high', 'low', 'close', 'volume', 'adj_close')
        def __init__(self, d):
            self.date = d['date']
            self.open = d.get('open')
            self.high = d.get('high')
            self.low = d.get('low')
            self.close = d.get('close')
            self.volume = d.get('volume')
            self.adj_close = d.get('adj_close', d.get('close'))

    rows = [_Row(d) for d in ph_rows_asc]

    for i in range(n):
        today = rows[i].date
        # ph_list_desc: rows from index i down to 0
        ph_list_desc = rows[:i + 1][::-1]
        try:
            mult, vraw, vsig, vmag, blend_w, vtarget = get_volume_multiplier_from_cache(
                today, ph_list_desc, prior, earnings_set
            )
        except Exception:
            mult, vraw, vsig, vmag, blend_w, vtarget = (1.0, 50, 'NEUTRAL', 0.0, 0.0, 50.0)

        if vsig != 'NEUTRAL':
            prior[today] = (vsig, float(vmag or 0.0), int(vraw or 50))

        out.append({
            'symbol': sym,
            'date': today,
            'vol_mult': float(mult),
            'vol_raw': int(vraw),
            'vol_sig': vsig,
            'vol_mag': float(vmag or 0.0),
            'blend_w': float(blend_w or 0.0),
            'vol_target': float(vtarget or 50.0),
        })

    return out


# ---------------------------------------------------------------------------
# Worker function (top-level so it pickles for multiprocessing)
# ---------------------------------------------------------------------------

def _score_symbol_worker(args):
    """Worker: given a per-symbol bundle, return list of (symbol, date, new_overall)."""
    sym, ind_arr, score_arr, week_arr, regime_arr, mis_stress_arr, vol_arr, ed_set, bb_fn_pickle = args

    # Unpickle the BB function (cloudpickle if needed)
    import pickle
    bb_fn = pickle.loads(bb_fn_pickle)

    from database.utils.scoring import compute_overall_score

    out = []
    n_inds = len(ind_arr)

    # ind_arr is sorted ascending by date (numpy structured array)
    # For each (symbol, date) where we have a v31 score, recompute BB+overall
    score_by_date = {row['date']: row for row in score_arr}
    week_by_start = {row['week_start']: row for row in week_arr}
    regime_by_date = {row['date']: row['mult'] for row in regime_arr}
    mis_by_date = {row['date']: row['mis_stress'] for row in mis_stress_arr}
    vol_by_date = {row['date']: row for row in vol_arr}

    for i, ind_row in enumerate(ind_arr):
        d = ind_row['date']
        if d not in score_by_date:
            continue
        score_row = score_by_date[d]

        # Build indicator window: [i, i-1, ..., i-10] (descending)
        lo = max(0, i - 10)
        # Slice ascending then reverse for descending
        window = list(ind_arr[lo:i + 1][::-1])
        if len(window) < 5:
            continue

        # Current bar
        upper = float(window[0]['upper_band']) if window[0]['upper_band'] is not None else None
        lower = float(window[0]['lower_band']) if window[0]['lower_band'] is not None else None
        middle = float(window[0]['middle_band']) if window[0]['middle_band'] is not None else None
        close = float(window[0]['close']) if window[0]['close'] is not None else None
        if None in (upper, lower, middle, close) or upper == lower:
            continue

        bb_pct = (close - lower) / (upper - lower)
        ema_50 = float(window[0]['ema_50']) if window[0]['ema_50'] is not None else None
        macd_hist = float(window[0]['macd_hist']) if window[0]['macd_hist'] is not None else None

        # Compute new BB component score
        trend_score = float(score_row['trend'])
        new_bb = bb_fn(trend_score, window, [w['close'] for w in window])
        if new_bb is None:
            continue

        # Read other components from existing v31 score
        rsi = float(score_row['rsi'])
        macd = float(score_row['macd'])
        stoch = float(score_row['stoch'])
        ta = float(score_row['ta'])

        # Weekly context
        wk_start = d - timedelta(days=d.weekday())
        prev_wk_start = wk_start - timedelta(days=7)
        ws = week_by_start.get(wk_start)
        pws = week_by_start.get(prev_wk_start)
        ws_trend = ws['trend'] if ws is not None else None
        ws_rsi = ws['rsi'] if ws is not None and ws.get('rsi') and ws.get('macd') else None
        ws_macd = ws['macd'] if ws is not None and ws.get('rsi') and ws.get('macd') else None
        prev_trend = pws['trend'] if pws is not None else None
        prev_rsi = pws['rsi'] if pws is not None else None
        prev_macd = pws['macd'] if pws is not None else None

        # Volume amplification — use the dynamically-computed cache, NOT the score row
        # (vol_mult isn't stored on the Score model — must be recomputed via volume_amplifier)
        vol_row = vol_by_date.get(d)
        if vol_row is not None:
            vol_mult = float(vol_row['vol_mult'])
            vol_raw = float(vol_row['vol_raw'])
            vol_sig = vol_row['vol_sig']
            vol_mag = float(vol_row['vol_mag'])
            blend_w_v = float(vol_row['blend_w'])
            vol_target = float(vol_row['vol_target'])
        else:
            vol_mult, vol_raw, vol_sig, vol_mag = 1.0, 50.0, 'NEUTRAL', 0.0
            blend_w_v, vol_target = 0.0, 50.0

        regime_mult = regime_by_date.get(d, 1.0)
        mis_stress = mis_by_date.get(d, 0.0)

        # pct_from_ema50
        pct_from_ema50 = (close - ema_50) / ema_50 * 100 if ema_50 else None

        # Days to earnings — strict-future (v31): 1 <= delta <= W (5).
        # d=0 (signal day == earnings effective_date) is excluded since the
        # effective_date already reflects the post-reaction day for AMC.
        d_to_ern = None
        for delta in range(1, 6):
            if (d + timedelta(days=delta)) in ed_set:
                d_to_ern = delta
                break

        result = compute_overall_score(
            trend_score, new_bb, rsi, macd, stoch, ta,
            bb_pct=bb_pct,
            pct_from_ema50=pct_from_ema50,
            ws_trend=ws_trend, ws_rsi=ws_rsi, ws_macd=ws_macd,
            prev_ws_trend=prev_trend, prev_ws_rsi=prev_rsi, prev_ws_macd=prev_macd,
            vol_mult=vol_mult, vol_raw=vol_raw,
            vol_sig=vol_sig, vol_mag=vol_mag,
            blend_w=blend_w_v, vol_target=vol_target,
            macdh_raw=macd_hist,
            regime_multiplier=regime_mult,
            mis_stress=mis_stress,
            days_to_earnings=d_to_ern,
        )
        if result is None or result[0] is None:
            continue
        out.append((sym, d, int(result[0]), int(new_bb)))

    return out


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

class FastBBRunner:
    def __init__(self, lookback_days: int = 1825, version_id: Optional[int] = None,
                 mis_stress_full: float = 30.0, metric: str = 'wr'):
        if version_id is None:
            from database.models.core import AlgorithmVersion
            version_id = AlgorithmVersion.get_active_scores_version().id
        self.version_id = version_id
        self.lookback_days = lookback_days
        self.mis_stress_full = mis_stress_full
        # metric='wr' -> use 30dte_generic K/M barriers; 'tp' -> 30dte_opt option-aligned
        self.metric = metric
        self.barrier_set_name = '30dte_opt' if metric == 'tp' else '30dte_generic'

        # Per-symbol bundles assembled in load()
        self._symbol_bundles: Dict[str, dict] = {}
        # Barrier outcomes DataFrame
        self.barrier_df: Optional[pl.DataFrame] = None
        # DB peaks (for diff comparison)
        self.db_peaks_df: Optional[pl.DataFrame] = None

    def load(self, verbose: bool = True, refresh: bool = False):
        """Bulk-load all data needed for variant evaluation.

        ind_cutoff includes a +400d buffer so the BB scoring function has
        enough rolling-window history. score_cutoff is exactly lookback_days
        so we don't accidentally count peaks from the buffer region.
        """
        score_cutoff = date.today() - timedelta(days=self.lookback_days)
        ind_cutoff = date.today() - timedelta(days=self.lookback_days + 400)
        score_cutoff_iso = score_cutoff.isoformat()
        cutoff_iso = ind_cutoff.isoformat()
        self._score_cutoff = score_cutoff

        scores_pq = CACHE_DIR / f'scores_v{self.version_id}_{self.lookback_days}.parquet'
        ind_pq = CACHE_DIR / f'ind_{self.lookback_days}.parquet'
        ph_pq = CACHE_DIR / f'ph_{self.lookback_days}.parquet'
        wk_pq = CACHE_DIR / f'wk_{self.lookback_days}.parquet'
        regime_pq = CACHE_DIR / f'regime_{self.lookback_days}.parquet'
        ed_pq = CACHE_DIR / f'ed_{self.lookback_days}.parquet'
        barrier_pq = CACHE_DIR / f'barriers_{self.barrier_set_name}_{self.lookback_days}.parquet'

        # ── Scores (with components and vol fields) ──
        if scores_pq.exists() and not refresh:
            scores_df = pl.read_parquet(scores_pq)
        else:
            from database.trader_database import DB
            t0 = time.time()
            cur = DB.execute_sql(f"""
                SELECT s.symbol, s.date, s.overall, s.bb, s.trend, s.rsi, s.macd, s.stoch,
                       s.technical_alignment AS ta, s.volume, s.volume_signal, s.volume_magnitude,
                       s.weight_info, s.regime_multiplier
                FROM scores s
                WHERE s.version_id = {self.version_id}
                  AND s.date >= '{score_cutoff_iso}'
                  AND s.overall IS NOT NULL
            """)
            rows = cur.fetchall()
            data = {
                'symbol': [], 'date': [], 'overall': [],
                'trend': [], 'bb': [], 'rsi': [], 'macd': [], 'stoch': [], 'ta': [],
                'vol_raw': [], 'vol_sig': [], 'vol_mag': [], 'vol_mult': [],
                'regime_mult': [],
            }
            for r in rows:
                sym, d, ov, bb, trend, rsi, macd, stoch, ta, vol, vsig, vmag, winfo, rmult = r
                if None in (trend, bb, rsi, macd, stoch, ta):
                    continue
                # vol_mult derivation: not stored; we approximate via vol_raw -> mult lookup later
                # For now we use a safe default if the volume signal isn't NEUTRAL.
                # The original simulator computes vol_mult dynamically; here we use existing
                # score's vol fields and compute_overall_score won't apply volume amp in the
                # same way unless vol_mult is correct. SIMPLIFICATION: skip vol_mult input so
                # compute_overall_score treats it as 1.0. We compensate by using stored
                # weight_info weighted_sum diff arithmetic — but simpler: pass vol_mult=1.0.
                # This is OK for BB-only changes because the volume amp is a multiplicative
                # stretch that affects new_bb-driven delta the same as it affected the old.
                vmult_val = 1.0  # Will be overridden via direct compute below
                data['symbol'].append(sym)
                data['date'].append(d if isinstance(d, date) else date.fromisoformat(str(d)))
                data['overall'].append(int(ov))
                data['trend'].append(float(trend))
                data['bb'].append(float(bb))
                data['rsi'].append(float(rsi))
                data['macd'].append(float(macd))
                data['stoch'].append(float(stoch))
                data['ta'].append(float(ta))
                data['vol_raw'].append(float(vol or 50))
                data['vol_sig'].append(vsig or 'NEUTRAL')
                data['vol_mag'].append(float(vmag or 0))
                data['vol_mult'].append(vmult_val)
                data['regime_mult'].append(float(rmult or 1.0))
            scores_df = pl.DataFrame(data)
            scores_df.write_parquet(scores_pq)
            if verbose:
                print(f"[fast-bb] built scores parquet ({len(scores_df):,} rows) in {time.time()-t0:.1f}s")

        if verbose:
            print(f"[fast-bb] scores: {len(scores_df):,} rows v{self.version_id}")

        # ── Indicators ──
        if ind_pq.exists() and not refresh:
            ind_df = pl.read_parquet(ind_pq)
        else:
            t0 = time.time()
            from database.trader_database import DB
            cur = DB.execute_sql(f"""
                SELECT i.symbol, i.date, i.upper_band, i.middle_band, i.lower_band,
                       i.ema_50, i.ema_200, i.rsi, i.macd_hist, i.stoch
                FROM indicators i
                WHERE i.date >= '{cutoff_iso}'
            """)
            rows = cur.fetchall()
            data = defaultdict(list)
            for r in rows:
                sym, d, upper, middle, lower, ema50, ema200, rsi, macd_h, stoch = r
                data['symbol'].append(sym)
                data['date'].append(d if isinstance(d, date) else date.fromisoformat(str(d)))
                data['upper_band'].append(float(upper) if upper is not None else None)
                data['middle_band'].append(float(middle) if middle is not None else None)
                data['lower_band'].append(float(lower) if lower is not None else None)
                data['ema_50'].append(float(ema50) if ema50 is not None else None)
                data['ema_200'].append(float(ema200) if ema200 is not None else None)
                data['rsi_raw'].append(float(rsi) if rsi is not None else None)
                data['macd_hist'].append(float(macd_h) if macd_h is not None else None)
                data['stoch_raw'].append(float(stoch) if stoch is not None else None)
            ind_df = pl.DataFrame(dict(data))
            ind_df.write_parquet(ind_pq)
            if verbose:
                print(f"[fast-bb] built indicators parquet ({len(ind_df):,} rows) in {time.time()-t0:.1f}s")

        # ── Price History (need OHLCV for volume amplifier) ──
        if ph_pq.exists() and not refresh:
            ph_df = pl.read_parquet(ph_pq)
        else:
            t0 = time.time()
            from database.trader_database import DB
            cur = DB.execute_sql(f"""
                SELECT p.symbol, p.date, p.open, p.high, p.low, p.close, p.volume
                FROM price_history p
                WHERE p.date >= '{cutoff_iso}'
            """)
            rows = cur.fetchall()
            ph_df = pl.DataFrame({
                'symbol': [r[0] for r in rows],
                'date': [r[1] if isinstance(r[1], date) else date.fromisoformat(str(r[1])) for r in rows],
                'open': [float(r[2]) if r[2] is not None else None for r in rows],
                'high': [float(r[3]) if r[3] is not None else None for r in rows],
                'low': [float(r[4]) if r[4] is not None else None for r in rows],
                'close': [float(r[5]) if r[5] is not None else None for r in rows],
                'volume': [int(r[6]) if r[6] is not None else None for r in rows],
                'adj_close': [float(r[5]) if r[5] is not None else None for r in rows],  # alias close
            })
            ph_df.write_parquet(ph_pq)
            if verbose:
                print(f"[fast-bb] built price-history parquet ({len(ph_df):,} rows) in {time.time()-t0:.1f}s")

        # Join indicators + price into a single DF keyed by (symbol, date)
        ind_df = ind_df.join(ph_df, on=['symbol', 'date'], how='left')

        # ── Weekly scores ──
        if wk_pq.exists() and not refresh:
            wk_df = pl.read_parquet(wk_pq)
        else:
            t0 = time.time()
            from database.trader_database import DB
            cur = DB.execute_sql(f"""
                SELECT w.symbol, w.date, w.trend, w.rsi, w.macd, w.composite
                FROM weekly_scores w
            """)
            rows = cur.fetchall()
            wk_df = pl.DataFrame({
                'symbol': [r[0] for r in rows],
                'week_start': [r[1] if isinstance(r[1], date) else date.fromisoformat(str(r[1])) for r in rows],
                'trend': [float(r[2]) if r[2] is not None else None for r in rows],
                'rsi': [float(r[3]) if r[3] is not None else None for r in rows],
                'macd': [float(r[4]) if r[4] is not None else None for r in rows],
                'composite': [float(r[5]) if r[5] is not None else None for r in rows],
            })
            wk_df.write_parquet(wk_pq)
            if verbose:
                print(f"[fast-bb] built weekly parquet ({len(wk_df):,} rows) in {time.time()-t0:.1f}s")

        # ── Market Regime + Mis-stress ──
        if regime_pq.exists() and not refresh:
            regime_df = pl.read_parquet(regime_pq)
        else:
            t0 = time.time()
            regime_df = self._build_regime_with_mis_stress()
            regime_df.write_parquet(regime_pq)
            if verbose:
                print(f"[fast-bb] built regime parquet ({len(regime_df):,} rows) in {time.time()-t0:.1f}s")

        # ── Volume multiplier cache (per (symbol, date), recomputed via volume amplifier) ──
        vol_pq = CACHE_DIR / f'vol_v{self.version_id}_{self.lookback_days}.parquet'
        # We must build this AFTER ph_df + ed_df are ready. Build ed first.
        # ── Earnings Dates ──
        if ed_pq.exists() and not refresh:
            ed_df = pl.read_parquet(ed_pq)
        else:
            t0 = time.time()
            from database.trader_database import DB
            cur = DB.execute_sql("""
                SELECT e.symbol, e.effective_date
                FROM earnings_dates e
                WHERE e.effective_date IS NOT NULL
            """)
            rows = cur.fetchall()
            ed_df = pl.DataFrame({
                'symbol': [r[0] for r in rows],
                'date': [r[1] if isinstance(r[1], date) else date.fromisoformat(str(r[1])) for r in rows],
            })
            ed_df.write_parquet(ed_pq)
            if verbose:
                print(f"[fast-bb] built earnings parquet ({len(ed_df):,} rows) in {time.time()-t0:.1f}s")

        # ── Barriers (reuse the fast_variant_runner cache schema) ──
        if barrier_pq.exists() and not refresh:
            self.barrier_df = pl.read_parquet(barrier_pq)
            if verbose:
                print(f"[fast-bb] loaded barriers parquet ({len(self.barrier_df):,} rows)")
        else:
            t0 = time.time()
            conn = sqlite3.connect(str(BARRIER_CACHE))
            self.barrier_df = pl.read_database(
                f"""SELECT symbol, date, side, w_days, result, exit_return, mae_pct, mfe_pct, result_u
                    FROM barrier_outcomes WHERE date >= '{cutoff_iso}'
                      AND barrier_set = '{self.barrier_set_name}'""",
                connection=conn,
            )
            conn.close()
            self.barrier_df.write_parquet(barrier_pq)
            if verbose:
                print(f"[fast-bb] built barriers parquet ({len(self.barrier_df):,} rows) in {time.time()-t0:.1f}s")

        # Cast barrier_df.date to Date type (SQLite stores as str) so it joins with scores.
        if self.barrier_df is not None and self.barrier_df['date'].dtype == pl.String:
            self.barrier_df = self.barrier_df.with_columns(
                pl.col('date').str.to_date().alias('date')
            )

        # ── Volume multiplier cache (build via per-symbol walk; expensive, cache to parquet) ──
        if vol_pq.exists() and not refresh:
            vol_df = pl.read_parquet(vol_pq)
            if verbose:
                print(f"[fast-bb] loaded vol_mult parquet ({len(vol_df):,} rows)")
        else:
            t0 = time.time()
            if verbose:
                print(f"[fast-bb] building vol_mult cache via per-symbol walk (multiprocessing) ...")
            # Bundle ph data per symbol
            ph_by_sym = ph_df.partition_by('symbol', as_dict=True)
            ed_by_sym = ed_df.partition_by('symbol', as_dict=True)
            args_list = []
            for sym_key, sym_ph in ph_by_sym.items():
                sym = sym_key[0] if isinstance(sym_key, tuple) else sym_key
                ph_rows = sym_ph.sort('date').to_dicts()
                ed_set = set()
                if sym_key in ed_by_sym:
                    ed_set = set(r['date'] for r in ed_by_sym[sym_key].to_dicts())
                args_list.append((sym, ph_rows, ed_set))

            n_workers = max(1, cpu_count() - 1)
            results = []
            with Pool(n_workers) as pool:
                for chunk in pool.imap_unordered(_build_vol_mult_for_symbol, args_list, chunksize=8):
                    results.extend(chunk)

            vol_df = pl.DataFrame(results) if results else pl.DataFrame({
                'symbol': [], 'date': [], 'vol_mult': [], 'vol_raw': [],
                'vol_sig': [], 'vol_mag': [], 'blend_w': [], 'vol_target': [],
            })
            vol_df.write_parquet(vol_pq)
            if verbose:
                print(f"[fast-bb] built vol_mult parquet ({len(vol_df):,} rows) in {time.time()-t0:.1f}s")

        # ── Partition by symbol ──
        t0 = time.time()
        self._partition_by_symbol(scores_df, ind_df, wk_df, regime_df, ed_df, vol_df, verbose)
        if verbose:
            print(f"[fast-bb] partitioned by symbol in {time.time()-t0:.1f}s "
                  f"({len(self._symbol_bundles)} bundles)")

    def _build_regime_with_mis_stress(self) -> pl.DataFrame:
        """Build regime DataFrame with mis_stress strength per date."""
        import bisect
        from database.models.core import MarketRegime, WeeklyScore

        rows = list(
            MarketRegime.select(
                MarketRegime.date, MarketRegime.regime_composite, MarketRegime.regime_multiplier,
                MarketRegime.vix_close, MarketRegime.spy_close, MarketRegime.spy_ema200,
            )
            .where(MarketRegime.regime_multiplier.is_null(False))
            .order_by(MarketRegime.date.asc())
            .namedtuples()
        )
        spy_wk = list(
            WeeklyScore.select(WeeklyScore.date, WeeklyScore.composite)
            .where(WeeklyScore.symbol == 'SPY', WeeklyScore.composite.is_null(False))
            .order_by(WeeklyScore.date.asc()).namedtuples()
        )
        spy_wk_dates = [r.date for r in spy_wk]
        spy_wk_vals = [float(r.composite) for r in spy_wk]
        spy_close_dates = [r.date for r in rows if r.spy_close is not None]
        spy_close_vals = {r.date: float(r.spy_close) for r in rows if r.spy_close is not None}
        spy_close_idx = {d: i for i, d in enumerate(spy_close_dates)}

        out_dates, out_mults, out_mis = [], [], []
        for r in rows:
            comp = float(r.regime_composite) if r.regime_composite is not None else None
            mult = float(r.regime_multiplier)
            out_dates.append(r.date)
            out_mults.append(mult)
            if comp is None or r.spy_close is None or r.spy_ema200 is None or r.vix_close is None:
                out_mis.append(0.0)
                continue
            i = bisect.bisect_right(spy_wk_dates, r.date) - 1
            s_wk = spy_wk_vals[i] if i >= 0 else None
            ci = spy_close_idx.get(r.date)
            mom = None
            if ci is not None and ci >= 10:
                mom = (spy_close_vals[r.date] /
                       spy_close_vals[spy_close_dates[ci - 10]] - 1.0) * 100.0
            bull_obj = (
                float(r.spy_close) > float(r.spy_ema200)
                and float(r.vix_close) < 20.0
                and mom is not None and mom > 0
                and s_wk is not None
            )
            if not bull_obj:
                out_mis.append(0.0)
            else:
                gap = max(0.0, s_wk - comp)
                ms = min(1.0, gap / max(1e-6, self.mis_stress_full))
                out_mis.append(ms)

        return pl.DataFrame({'date': out_dates, 'mult': out_mults, 'mis_stress': out_mis})

    def _partition_by_symbol(self, scores_df, ind_df, wk_df, regime_df, ed_df, vol_df, verbose):
        """Build per-symbol bundles for parallel scoring."""
        # Group by symbol once
        scores_by = scores_df.partition_by('symbol', as_dict=True)
        ind_by = ind_df.partition_by('symbol', as_dict=True)
        wk_by = wk_df.partition_by('symbol', as_dict=True)
        ed_by = ed_df.partition_by('symbol', as_dict=True)
        vol_by = vol_df.partition_by('symbol', as_dict=True)

        # Convert regime_df to a list of dicts (small, ~1300 rows)
        regime_list = [{'date': r['date'], 'mult': r['mult']}
                       for r in regime_df.iter_rows(named=True)]
        mis_list = [{'date': r['date'], 'mis_stress': r['mis_stress']}
                    for r in regime_df.iter_rows(named=True)]

        all_syms = set(scores_by.keys()) & set(ind_by.keys())

        for sym_key in all_syms:
            # partition_by keys are tuples in newer Polars
            sym = sym_key[0] if isinstance(sym_key, tuple) else sym_key
            score_rows = scores_by[sym_key].sort('date').to_dicts()
            ind_rows = ind_by[sym_key].sort('date').to_dicts()
            wk_rows = wk_by.get(sym_key)
            wk_list = wk_rows.sort('week_start').to_dicts() if wk_rows is not None else []
            ed_rows = ed_by.get(sym_key)
            ed_set = set(r['date'] for r in (ed_rows.to_dicts() if ed_rows is not None else []))
            vol_rows = vol_by.get(sym_key)
            vol_list = vol_rows.sort('date').to_dicts() if vol_rows is not None else []

            self._symbol_bundles[sym] = {
                'symbol': sym,
                'ind_arr': ind_rows,
                'score_arr': score_rows,
                'week_arr': wk_list,
                'regime_arr': regime_list,
                'mis_stress_arr': mis_list,
                'vol_arr': vol_list,
                'ed_set': ed_set,
            }

    def evaluate_bb_variant(
        self,
        bb_fn: BBFn,
        label: str = 'variant',
        n_workers: Optional[int] = None,
        verbose: bool = True,
    ) -> dict:
        """Run the BB variant across all symbols and return aggregated stats.

        bb_fn signature: (trend_score, indicator_window, ph_close_window) -> int|None
        """
        import pickle
        bb_fn_pickled = pickle.dumps(bb_fn)

        n_workers = n_workers or max(1, cpu_count() - 1)
        if verbose:
            print(f"[fast-bb / {label}] scoring {len(self._symbol_bundles)} symbols on {n_workers} workers ...")

        t0 = time.time()
        args_list = [
            (b['symbol'], b['ind_arr'], b['score_arr'], b['week_arr'],
             b['regime_arr'], b['mis_stress_arr'], b['vol_arr'], b['ed_set'], bb_fn_pickled)
            for b in self._symbol_bundles.values()
        ]

        results: List[Tuple[str, date, int, int]] = []
        if n_workers <= 1:
            for args in args_list:
                results.extend(_score_symbol_worker(args))
        else:
            with Pool(n_workers) as pool:
                for chunk in pool.imap_unordered(_score_symbol_worker, args_list, chunksize=8):
                    results.extend(chunk)

        elapsed = time.time() - t0
        if verbose:
            print(f"[fast-bb / {label}] scored {len(results):,} (sym, date) in {elapsed:.1f}s "
                  f"({len(results) / max(elapsed, 0.001):.0f}/s)")

        # Build a Polars DF of new scores
        new_df = pl.DataFrame({
            'symbol': [r[0] for r in results],
            'date':   [r[1] for r in results],
            'new_overall': [r[2] for r in results],
            'new_bb': [r[3] for r in results],
        })

        return self._aggregate(new_df, label=label, verbose=verbose)

    def evaluate_baseline(self, label: str = 'BASELINE', verbose: bool = True) -> dict:
        """Run with the EXISTING DB BB scores — no recompute. For sanity checks."""
        # Build the DataFrame from stored v31 scores directly
        rows = []
        for b in self._symbol_bundles.values():
            for s in b['score_arr']:
                rows.append((b['symbol'], s['date'], int(s['overall']), int(s['bb'])))
        new_df = pl.DataFrame({
            'symbol': [r[0] for r in rows],
            'date': [r[1] for r in rows],
            'new_overall': [r[2] for r in rows],
            'new_bb': [r[3] for r in rows],
        })
        return self._aggregate(new_df, label=label, verbose=verbose)

    def _aggregate(self, new_df: pl.DataFrame, label: str = '', verbose: bool = True) -> dict:
        """Filter peaks → join barriers → bucket → produce stats matching assess shape."""
        peaks = new_df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
        peaks = peaks.with_columns(
            pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high'))
              .alias('side')
        )
        joined = peaks.join(self.barrier_df, on=['symbol', 'date', 'side'], how='inner')

        bucket_results = []
        for t in BUY_THRESHOLDS:
            sub = joined.filter(pl.col('new_overall') >= t).with_columns(pl.lit(f'{t}+').alias('bucket'))
            bucket_results.append(sub)
        for t in SELL_THRESHOLDS:
            sub = joined.filter(pl.col('new_overall') <= t).with_columns(pl.lit(f'<{t}').alias('bucket'))
            bucket_results.append(sub)

        if not bucket_results:
            return {'bucketed_stats': {k: {'sample_count': 0} for k in THRESHOLD_KEYS}}

        bucketed = pl.concat(bucket_results, how='vertical')
        agg = bucketed.group_by(['bucket', 'w_days']).agg([
            pl.len().alias('n'),
            pl.col('result').filter(pl.col('result').is_not_null()).count().alias('n_resolved'),
            pl.col('result').sum().alias('wins'),
            pl.col('result_u').filter(pl.col('result_u').is_not_null()).count().alias('n_u'),
            pl.col('result_u').sum().alias('wins_u'),
            pl.col('exit_return').mean().alias('avg_return'),
            pl.col('mae_pct').mean().alias('avg_mae'),
            pl.col('mfe_pct').mean().alias('avg_mfe'),
        ])

        bucketed_stats = {k: {'sample_count': 0} for k in THRESHOLD_KEYS}
        for row in agg.iter_rows(named=True):
            bk = row['bucket']
            w = row['w_days']
            label_p = next((l for l, w_ in PERIODS if w_ == w), None)
            if label_p is None:
                continue
            n = row['n_resolved'] or 0
            wins = row['wins'] or 0
            n_u = row['n_u'] or 0
            wins_u = row['wins_u'] or 0
            stats = bucketed_stats.setdefault(bk, {})
            stats[f'win_rate_{label_p}'] = (wins / n * 100) if n else None
            stats[f'win_rate_unscaled_{label_p}'] = (wins_u / n_u * 100) if n_u else None
            stats[f'avg_return_{label_p}'] = row['avg_return']
            stats[f'avg_mae_{label_p}'] = -row['avg_mae'] if row['avg_mae'] is not None else None
            stats[f'avg_mfe_{label_p}'] = row['avg_mfe']
            mfe = row['avg_mfe']
            stats[f'capture_ratio_{label_p}'] = ((row['avg_return'] or 0) / mfe) if mfe else None
            if w == 30:
                stats['sample_count'] = n
        return {'bucketed_stats': bucketed_stats}


# ---------------------------------------------------------------------------
# Diff print
# ---------------------------------------------------------------------------

def print_bucket_diff(baseline: dict, variant: dict, label_b='BASELINE', label_v='VARIANT'):
    """Print a side-by-side WR/N comparison between baseline and variant stats."""
    metrics = [
        ('WR15', 'win_rate_15d', '.1f', '%'),
        ('WR30', 'win_rate_30d', '.1f', '%'),
        ('Ret30', 'avg_return_30d', '.2f', '%'),
        ('MFE30', 'avg_mfe_30d', '.2f', '%'),
        ('Cap30', 'capture_ratio_30d', '.3f', ''),
    ]
    col_w = 18
    print(f"\n=== {label_b} -> {label_v} ===")
    print(f"{'Bucket':>7} | {'N':>5}/{'':<5} | " +
          '  '.join(f"{m[0]:^{col_w}}" for m in metrics))
    print('-' * (20 + len(metrics) * (col_w + 2)))

    for k in THRESHOLD_KEYS:
        b = baseline['bucketed_stats'].get(k, {})
        v = variant['bucketed_stats'].get(k, {})
        n_b = b.get('sample_count') or 0
        n_v = v.get('sample_count') or 0
        if n_b == 0 and n_v == 0:
            continue
        cells = []
        for _, field, fmt, suf in metrics:
            ov = b.get(field)
            nv = v.get(field)
            if ov is None and nv is None:
                cells.append('--'.center(col_w))
                continue
            ov_s = f"{ov:{fmt}}{suf}" if ov is not None else '--'
            nv_s = f"{nv:{fmt}}{suf}" if nv is not None else '--'
            if ov is not None and nv is not None:
                d = nv - ov
                ds = f"({d:+.2f})"
            else:
                ds = ''
            cells.append(f"{ov_s}->{nv_s}{ds}".ljust(col_w))
        print(f"{k:>7} | {n_b:>5}/{n_v:<5} | " + '  '.join(cells))
