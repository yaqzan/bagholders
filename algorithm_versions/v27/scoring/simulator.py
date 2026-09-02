"""
Score Simulator — test formula changes in minutes, not hours.

Loads all required data in bulk (5–8 queries total regardless of stock count),
runs the full scoring pipeline in memory, and feeds results directly into
assess_scores without writing a single score to the database.

Usage
-----
# Quick simulation: score the current algorithm against all stocks
python trader.py simulate

# Simulate a subset for speed
python trader.py simulate --symbols AAPL MSFT NVDA --days 180

# Plug in a custom weighting function for experimentation
# (see ScoreSimulator.simulate() docstring)

# Run assessment on the simulated scores
python trader.py simulate --assess

# Compare simulated scores against the stored scores for the current version
python trader.py simulate --compare
"""

from __future__ import annotations

import bisect
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from colorama import Fore, Style


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
SimScores = Dict[Tuple[str, date], int]       # (symbol, date) → score 0-100
ScoringFn = Callable[..., Optional[int]]      # custom overall scorer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _bisect_desc_upper(sorted_asc: list, target) -> int:
    """Index of the last element ≤ target in an ascending-sorted list."""
    return bisect.bisect_right(sorted_asc, target)


# ---------------------------------------------------------------------------
# StockContext — per-stock pre-loaded data
# ---------------------------------------------------------------------------

class StockContext:
    """
    Holds all pre-loaded data for one stock.  No DB calls after __init__.

    Attributes
    ----------
    stock       : Stock ORM row
    ind_rows    : list of Indicator rows, sorted ASCENDING by date
    ind_dates   : corresponding date list (parallel to ind_rows, for bisect)
    ph_map      : dict[date → PriceHistory row]
    ph_rows_asc : price history sorted ASCENDING (for volume amplifier)
    ph_rows_desc: price history sorted DESCENDING (for volume amplifier)
    ws_map      : dict[week_start_date → WeeklyScore row]
    earnings_set: set of dates where earnings occurred
    """

    __slots__ = (
        'stock', 'symbol',
        'ind_rows', 'ind_dates',
        'ph_map', 'ph_rows_asc', 'ph_rows_desc',
        'ws_map', 'earnings_set',
    )

    def __init__(self, stock, ind_rows, ph_rows_asc, ws_rows, earnings_dates):
        self.stock       = stock
        self.symbol      = stock.symbol
        self.ind_rows    = ind_rows                       # asc
        self.ind_dates   = [i.date for i in ind_rows]    # for bisect
        self.ph_rows_asc = ph_rows_asc
        self.ph_rows_desc = list(reversed(ph_rows_asc))
        self.ph_map      = {p.date: p for p in ph_rows_asc}
        self.ws_map      = {ws.date: ws for ws in ws_rows}
        self.earnings_set = set(earnings_dates)

    def ind_cache_for(self, target_date) -> list:
        """Return indicator rows ≤ target_date sorted DESCENDING — O(log n) slice."""
        idx = _bisect_desc_upper(self.ind_dates, target_date)
        return list(reversed(self.ind_rows[:idx]))

    def ph_slice_desc_for(self, target_date) -> list:
        """Price history rows ≤ target_date sorted DESCENDING — for volume amplifier."""
        ph_dates = [p.date for p in self.ph_rows_asc]
        idx = _bisect_desc_upper(ph_dates, target_date)
        return self.ph_rows_desc[len(self.ph_rows_asc) - idx:]


# ---------------------------------------------------------------------------
# ScoreSimulator
# ---------------------------------------------------------------------------

class ScoreSimulator:
    """
    Loads all data once, scores every (symbol, date) in memory, returns results
    as a plain dict.  No writes to the database.

    Parameters
    ----------
    symbols      : list of ticker strings; None → all stocks in DB
    lookback_days: how many calendar days of history to score
    scoring_fn   : optional replacement for compute_overall_score().
                   Signature: scoring_fn(trend, bb, rsi, macd, stoch, ta,
                                         **context_kwargs) → int | None
                   context_kwargs mirrors compute_overall_score() kwargs.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        lookback_days: int = 365,
        scoring_fn: Optional[ScoringFn] = None,
        mis_stress_full: float = 30.0,
    ):
        self.lookback_days = lookback_days
        self.scoring_fn = scoring_fn
        self.mis_stress_full = mis_stress_full
        self._contexts: Dict[str, StockContext] = {}
        self._load(symbols)

    def _build_mis_stress_map(self) -> Dict[date, float]:
        """Build {date: mis_stress_strength in [0, 1]} for the loaded window.

        Mis-stress = regime composite mislabels narrow-bull as stress.
        Detector requires (a) gap between SPY weekly composite and regime
        composite, AND (b) current-day objective bull state to filter out
        SPY-weekly lag artifacts.
        """
        import bisect
        from database.models.core import MarketRegime, WeeklyScore

        rows = list(
            MarketRegime.select(
                MarketRegime.date, MarketRegime.regime_composite,
                MarketRegime.vix_close, MarketRegime.spy_close,
                MarketRegime.spy_ema200,
            )
            .where(MarketRegime.regime_multiplier.is_null(False))
            .order_by(MarketRegime.date.asc())
            .namedtuples()
        )

        spy_wk_rows = list(
            WeeklyScore.select(WeeklyScore.date, WeeklyScore.composite)
            .where(WeeklyScore.symbol == 'SPY', WeeklyScore.composite.is_null(False))
            .order_by(WeeklyScore.date.asc())
            .namedtuples()
        )
        spy_wk_dates = [r.date for r in spy_wk_rows]
        spy_wk_vals = [float(r.composite) for r in spy_wk_rows]

        def _spy_wk(d):
            i = bisect.bisect_right(spy_wk_dates, d) - 1
            return spy_wk_vals[i] if i >= 0 else None

        # Pre-build SPY close index for spy_10d momentum
        spy_close_dates = [r.date for r in rows if r.spy_close is not None]
        spy_close_vals = {r.date: float(r.spy_close) for r in rows if r.spy_close is not None}

        def _spy_10d(d):
            try:
                idx = spy_close_dates.index(d)
                if idx < 10:
                    return None
                return (spy_close_vals[d] / spy_close_vals[spy_close_dates[idx - 10]] - 1.0) * 100.0
            except ValueError:
                return None

        # spy_10d via positional walk
        spy_close_idx = {d: i for i, d in enumerate(spy_close_dates)}

        def _spy_10d_fast(d):
            i = spy_close_idx.get(d)
            if i is None or i < 10:
                return None
            return (spy_close_vals[d] / spy_close_vals[spy_close_dates[i - 10]] - 1.0) * 100.0

        out: Dict[date, float] = {}
        for r in rows:
            comp = float(r.regime_composite) if r.regime_composite is not None else None
            if comp is None or r.spy_close is None or r.spy_ema200 is None or r.vix_close is None:
                continue
            s_wk = _spy_wk(r.date)
            if s_wk is None:
                continue
            mom = _spy_10d_fast(r.date)
            bull_obj = (
                float(r.spy_close) > float(r.spy_ema200)
                and float(r.vix_close) < 20.0
                and mom is not None and mom > 0
            )
            if not bull_obj:
                out[r.date] = 0.0
                continue
            gap = max(0.0, s_wk - comp)
            ms = min(1.0, gap / max(1e-6, self.mis_stress_full))
            out[r.date] = ms
        return out

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load(self, symbols: Optional[List[str]]):
        from database.models.core import Stock, WeeklyScore, EarningsDate, MarketRegime
        from database.models.technical import Indicator, PriceHistory

        cutoff = date.today() - timedelta(days=self.lookback_days + 400)
        # extra 400 days so sliding-window indicators have enough history

        if symbols:
            stocks = list(Stock.select().where(Stock.symbol.in_(symbols)))
        else:
            stocks = list(Stock.select().order_by(Stock.symbol))

        sym_list = [s.symbol for s in stocks]
        print(Fore.CYAN + f"[simulator] loading {len(sym_list)} stocks …" + Style.RESET_ALL)

        # ── Bulk queries (one per table) ────────────────────────────────
        all_inds = list(
            Indicator.select()
            .where(Indicator.symbol.in_(sym_list), Indicator.date >= cutoff)
            .order_by(Indicator.symbol, Indicator.date)
            .namedtuples()
        )
        all_ph = list(
            PriceHistory.select()
            .where(PriceHistory.symbol.in_(sym_list), PriceHistory.date >= cutoff)
            .order_by(PriceHistory.symbol, PriceHistory.date)
            .namedtuples()
        )
        all_ws = list(
            WeeklyScore.select()
            .where(WeeklyScore.symbol.in_(sym_list))
            .order_by(WeeklyScore.symbol, WeeklyScore.date)
            .namedtuples()
        )
        all_ed = list(
            EarningsDate.select(EarningsDate.symbol, EarningsDate.date)
            .where(EarningsDate.symbol.in_(sym_list))
            .namedtuples()
        )

        # ── MarketRegime map: date → multiplier ─────────────────────────
        all_regime = list(
            MarketRegime.select(MarketRegime.date, MarketRegime.regime_multiplier)
            .where(MarketRegime.regime_multiplier.is_null(False))
            .namedtuples()
        )
        self._regime_map = {r.date: float(r.regime_multiplier) for r in all_regime}

        # ── Mis-stress map (Priority #6 / #8 follow-up): date → strength ────
        # Detects narrow-bull-mislabeled-as-stress days using:
        #   - regime_composite (low) vs SPY_wk_composite (high) gap
        #   - objective bull state: SPY > EMA200, VIX < 20, SPY_10d > 0
        # Used by experimental ct_designs variants ('mis_stress_call_softener').
        # Vanilla compute_overall_score ignores this kwarg.
        self._mis_stress_map: Dict[date, float] = {}
        try:
            self._mis_stress_map = self._build_mis_stress_map()
        except Exception as _e:
            # Fail soft — production scoring doesn't depend on it.
            print(Fore.YELLOW + f"[simulator] mis_stress_map disabled: {_e}" + Style.RESET_ALL)

        # ── Partition by symbol ─────────────────────────────────────────
        inds_by_sym  = defaultdict(list)
        ph_by_sym    = defaultdict(list)
        ws_by_sym    = defaultdict(list)
        ed_by_sym    = defaultdict(list)

        for r in all_inds:  inds_by_sym[r.symbol].append(r)
        for r in all_ph:    ph_by_sym[r.symbol].append(r)
        for r in all_ws:    ws_by_sym[r.symbol].append(r)
        for r in all_ed:    ed_by_sym[r.symbol].append(r.date)

        stock_map = {s.symbol: s for s in stocks}

        for sym in sym_list:
            if sym not in stock_map:
                continue
            ctx = StockContext(
                stock        = stock_map[sym],
                ind_rows     = inds_by_sym[sym],        # already asc
                ph_rows_asc  = ph_by_sym[sym],          # already asc
                ws_rows      = ws_by_sym[sym],
                earnings_dates = ed_by_sym[sym],
            )
            self._contexts[sym] = ctx

        print(Fore.GREEN + f"[simulator] loaded {len(self._contexts)} contexts" + Style.RESET_ALL)

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def simulate(self, since: Optional[date] = None) -> SimScores:
        """
        Run the full scoring pipeline in memory for every (symbol, date) where
        indicator data exists.

        Returns
        -------
        dict mapping (symbol, date) → overall_score (int 0-100)
        """
        from database.utils.scoring import compute_overall_score
        from volume_amplifier import get_volume_multiplier_from_cache

        cutoff = since or (date.today() - timedelta(days=self.lookback_days))
        results: SimScores = {}

        for sym, ctx in self._contexts.items():
            stock = ctx.stock
            prior_vol_signals: Dict[date, Tuple[str, float, int]] = {}

            # Iterate dates in ascending order (decay requires forward walk)
            for ind_row in ctx.ind_rows:
                d = ind_row.date
                if d < cutoff:
                    continue

                ind_cache = ctx.ind_cache_for(d)

                # ── Component scores ────────────────────────────────────
                trend = stock.calculate_trend_score(
                    d, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)
                if trend is None:
                    continue

                _ph_sim = ctx.ph_map.get(d)
                _pct_ema_sim = (
                    (float(_ph_sim.close) - float(ind_row.ema_50)) / float(ind_row.ema_50) * 100
                    if _ph_sim and ind_row.ema_50 is not None else None
                )
                rsi = stock.calculate_rsi_score(
                    d, trend_score=trend, _ind_cache=ind_cache, _ph_cache=ctx.ph_map,
                    macdh_raw=float(ind_row.macd_hist) if ind_row.macd_hist is not None else None,
                    pct_from_ema50=_pct_ema_sim)
                bb = stock.calculate_bollinger_bands_score(
                    d, trend_score=trend, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)
                macd = stock.calculate_macd_score(
                    d, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)
                stoch = stock.calculate_stoch_score(
                    d, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)

                if None in (rsi, bb, macd, stoch):
                    continue

                # Technical alignment (pure math, no DB)
                scores_list = [bb, rsi, macd]
                total   = len(scores_list)
                bullish = sum(s >= 50 for s in scores_list)
                bearish = total - bullish
                agreement = max(bullish, bearish) / total
                avg_dist  = sum(s - 50 for s in scores_list) / total
                sig_str   = min(abs(avg_dist) / 50, 1.0)
                turbo     = 1 + 0.5 * sig_str * agreement
                ta = round(max(0, min(100, 50 + avg_dist * sig_str * (agreement ** 1.5) * turbo)))

                # ── Weekly context ──────────────────────────────────────
                ws      = ctx.ws_map.get(_week_start(d))
                pws     = ctx.ws_map.get(_week_start(d) - timedelta(weeks=1))
                ws_rsi  = ws.rsi  if ws and ws.rsi and ws.macd else None
                ws_macd = ws.macd if ws and ws.rsi and ws.macd else None

                # ── Volume signal ───────────────────────────────────────
                ph_slice = ctx.ph_slice_desc_for(d)
                vol_mult, vol_raw, vol_sig, vol_mag, blend_w, vol_target = \
                    get_volume_multiplier_from_cache(
                        d, ph_slice, prior_vol_signals, ctx.earnings_set)

                # ── BB position for overall score ───────────────────────
                bb_pct = None
                if None not in (ind_row.upper_band, ind_row.lower_band):
                    bw = float(ind_row.upper_band) - float(ind_row.lower_band)
                    ph = ctx.ph_map.get(d)
                    if bw > 0 and ph:
                        bb_pct = (float(ph.close) - float(ind_row.lower_band)) / bw

                # ── Regime multiplier for this date ─────────────────────
                _r_mult = self._regime_map.get(d)
                _mis_stress = self._mis_stress_map.get(d, 0.0)

                # ── Overall score ───────────────────────────────────────
                if self.scoring_fn is not None:
                    ph = ctx.ph_map.get(d)
                    raw_price = float(ph.close) if ph and ph.close else None
                    overall = self.scoring_fn(
                        trend, bb, rsi, macd, stoch, ta,
                        bb_pct=bb_pct,
                        ws_trend=ws.trend if ws else None,
                        ws_rsi=ws_rsi, ws_macd=ws_macd,
                        prev_ws_trend=pws.trend if pws else None,
                        prev_ws_rsi=pws.rsi   if pws else None,
                        prev_ws_macd=pws.macd if pws else None,
                        vol_mult=vol_mult, vol_raw=vol_raw,
                        vol_sig=vol_sig,   vol_mag=vol_mag,
                        blend_w=blend_w,   vol_target=vol_target,
                        raw_rsi=float(ind_row.rsi) if ind_row.rsi is not None else None,
                        raw_stoch=float(ind_row.stoch) if ind_row.stoch is not None else None,
                        raw_macd_hist=float(ind_row.macd_hist) if ind_row.macd_hist is not None else None,
                        raw_price=raw_price,
                        raw_ema50=float(ind_row.ema_50) if ind_row.ema_50 is not None else None,
                        raw_upper=float(ind_row.upper_band) if ind_row.upper_band is not None else None,
                        raw_lower=float(ind_row.lower_band) if ind_row.lower_band is not None else None,
                        raw_middle=float(ind_row.middle_band) if ind_row.middle_band is not None else None,
                        regime_multiplier=_r_mult,
                        mis_stress=_mis_stress,
                    )
                else:
                    ph = ctx.ph_map.get(d)
                    _pct_ema50 = None
                    if ind_row.ema_50 and ph and ph.close:
                        _pct_ema50 = (float(ph.close) - float(ind_row.ema_50)) / float(ind_row.ema_50) * 100
                    overall, _, _ = compute_overall_score(
                        trend, bb, rsi, macd, stoch, ta,
                        bb_pct=bb_pct,
                        pct_from_ema50=_pct_ema50,
                        ws_trend=ws.trend if ws else None,
                        ws_rsi=ws_rsi, ws_macd=ws_macd,
                        prev_ws_trend=pws.trend if pws else None,
                        prev_ws_rsi=pws.rsi   if pws else None,
                        prev_ws_macd=pws.macd if pws else None,
                        vol_mult=vol_mult, vol_raw=vol_raw,
                        vol_sig=vol_sig,   vol_mag=vol_mag,
                        blend_w=blend_w,   vol_target=vol_target,
                        macdh_raw=float(ind_row.macd_hist) if ind_row.macd_hist is not None else None,
                        regime_multiplier=_r_mult,
                        mis_stress=_mis_stress,
                    )

                if overall is None:
                    continue

                results[(sym, d)] = overall
                prior_vol_signals[d] = (vol_sig, vol_mag, vol_raw)

        print(Fore.GREEN + f"[simulator] scored {len(results)} (symbol, date) pairs" + Style.RESET_ALL)
        return results

    # ------------------------------------------------------------------
    # Assessment bridge
    # ------------------------------------------------------------------

    def assess(self, sim_scores: SimScores, lookback_days: Optional[int] = None,
               symbol: Optional[str] = None) -> None:
        """
        Run assessment (win rates, MAE/MFE, etc.) against simulated scores
        without touching the scores table.

        Prints the same output as `python trader.py assess`.
        """
        from assess_scores import (
            run_assessment_on_peaks, print_results,
            BUY_THRESHOLDS, SELL_THRESHOLDS,
        )
        from database.models.technical import PriceHistory

        lb = lookback_days or self.lookback_days
        cutoff = date.today() - timedelta(days=lb)

        # Build fake peak objects from sim_scores that look like Score rows
        # Filter to extremes
        peaks = []
        by_symbol: Dict[str, list] = defaultdict(list)
        for (sym, d), score in sim_scores.items():
            if symbol and sym != symbol:
                continue
            if d < cutoff:
                continue
            if score >= 70 or score <= 25:
                by_symbol[sym].append((d, score))

        # Prune to non-overlapping peaks (same logic as extract_peaks)
        for sym, entries in by_symbol.items():
            entries.sort(key=lambda x: x[1], reverse=True)  # rank by extremity
            date_set = {d for d, _ in entries}
            pruned: set = set()
            for d, score in sorted(entries, key=lambda x: abs(x[1] - 50), reverse=True):
                if (sym, d) in pruned:
                    continue
                peaks.append(_FakePeak(sym, d, score))
                for direction in (-1, 1):
                    walk = d + timedelta(days=direction)
                    while walk in date_set and (sym, walk) not in pruned:
                        pruned.add((sym, walk))
                        walk += timedelta(days=direction)

        print(Fore.CYAN + f"[simulator] assessing {len(peaks)} peaks …" + Style.RESET_ALL)

        # Bulk load price history for forward-return calculations
        sym_list = list({p.symbol for p in peaks})
        all_ph = list(
            PriceHistory.select()
            .where(PriceHistory.symbol.in_(sym_list), PriceHistory.date >= cutoff)
            .order_by(PriceHistory.symbol, PriceHistory.date)
            .namedtuples()
        )
        ph_by_sym: Dict[str, list] = defaultdict(list)
        for r in all_ph:
            ph_by_sym[r.symbol].append(r)

        run_assessment_on_peaks(peaks, ph_by_sym, lb)

    # ------------------------------------------------------------------
    # Diff assessment (sim vs DB side-by-side)
    # ------------------------------------------------------------------

    def diff_assess(self, sim_scores: SimScores, symbol: Optional[str] = None,
                    db_version=None) -> None:
        """Compare assessment metrics between stored DB scores and simulated scores.

        For each score bucket (70+, 80+, …) prints win-rate, return, MAE/MFE and
        capture ratio side-by-side so you can instantly see whether a formula change
        makes the algorithm better or worse without writing anything to the DB.

        db_version: optional AlgorithmVersion (or id) to filter DB peaks to a single
                    algorithm row. Without this, peaks from ALL versions in the scores
                    table are mixed, which makes A/B comparison meaningless.
        """
        from assess_scores import (
            extract_peaks, assess_peaks_in_memory, print_diff_assessment,
            BUY_THRESHOLDS, SELL_THRESHOLDS,
        )
        from database.models.core import AlgorithmVersion
        from database.models.technical import PriceHistory

        cutoff = date.today() - timedelta(days=self.lookback_days)

        # Resolve db_version to an AlgorithmVersion row if an id was passed
        if isinstance(db_version, int):
            db_version = AlgorithmVersion.get_or_none(AlgorithmVersion.id == db_version)

        # ── 1. DB peaks ─────────────────────────────────────────────────
        db_peaks = extract_peaks(symbol=symbol, lookback_days=self.lookback_days,
                                 version=db_version)
        # Filter DB peaks to the symbols actually loaded by the simulator,
        # so the comparison is apples-to-apples (matters when sim runs a subset)
        loaded_syms = set(self._contexts.keys())
        if loaded_syms:
            db_peaks = [p for p in db_peaks if p.symbol_id in loaded_syms]
        print(Fore.CYAN + f"[diff-assess] DB peaks: {len(db_peaks)}" + Style.RESET_ALL)

        # ── 2. Sim peaks (mirror of simulator.assess() logic) ───────────
        by_symbol: Dict[str, list] = defaultdict(list)
        for (sym, d), score in sim_scores.items():
            if symbol and sym != symbol:
                continue
            if d < cutoff:
                continue
            if score >= 70 or score <= 25:
                by_symbol[sym].append((d, score))

        sim_peaks = []
        for sym, entries in by_symbol.items():
            date_set = {d for d, _ in entries}
            pruned: set = set()
            for d, score in sorted(entries, key=lambda x: abs(x[1] - 50), reverse=True):
                if (sym, d) in pruned:
                    continue
                sim_peaks.append(_FakePeak(sym, d, score))
                for direction in (-1, 1):
                    walk = d + timedelta(days=direction)
                    while walk in date_set and (sym, walk) not in pruned:
                        pruned.add((sym, walk))
                        walk += timedelta(days=direction)

        print(Fore.CYAN + f"[diff-assess] Sim peaks: {len(sim_peaks)}" + Style.RESET_ALL)

        # ── 3. Bulk-load price history for all relevant symbols ─────────
        all_syms = list({p.symbol_id for p in db_peaks} | {p.symbol_id for p in sim_peaks})
        all_ph = list(
            PriceHistory.select()
            .where(PriceHistory.symbol.in_(all_syms), PriceHistory.date >= cutoff)
            .order_by(PriceHistory.symbol, PriceHistory.date)
            .namedtuples()
        )
        ph_by_sym: Dict[str, list] = defaultdict(list)
        for r in all_ph:
            ph_by_sym[r.symbol].append(r)

        # ── 4. Run both assessments in memory ───────────────────────────
        db_data  = assess_peaks_in_memory(db_peaks,  ph_by_sym, self.lookback_days)
        sim_data = assess_peaks_in_memory(sim_peaks, ph_by_sym, self.lookback_days)

        # ── 5. Print diff ───────────────────────────────────────────────
        print_diff_assessment(db_data, sim_data, label_old='DB', label_new='SIM')

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def compare(self, sim_scores: SimScores) -> None:
        """Print a summary comparing simulated scores vs stored DB scores."""
        from database.models.core import Score, AlgorithmVersion

        version = AlgorithmVersion.get_or_create_current()
        sym_list = list({sym for sym, _ in sim_scores})
        cutoff = date.today() - timedelta(days=self.lookback_days)

        stored = {
            (s.symbol_id, s.date): s.overall
            for s in Score.select(Score.symbol, Score.date, Score.overall)
            .where(Score.symbol.in_(sym_list), Score.date >= cutoff,
                   Score.version == version)
            .namedtuples()
        }

        deltas = []
        for (sym, d), sim_score in sim_scores.items():
            db_score = stored.get((sym, d))
            if db_score is not None:
                deltas.append(sim_score - db_score)

        if not deltas:
            print(Fore.YELLOW + "[compare] no overlapping dates found" + Style.RESET_ALL)
            return

        import statistics
        mean_delta = statistics.mean(deltas)
        median_delta = statistics.median(deltas)
        max_delta = max(deltas)
        min_delta = min(deltas)
        large = sum(1 for d in deltas if abs(d) >= 5)

        print(f"\n{'─'*50}")
        print(Fore.CYAN + "Simulated vs stored score comparison" + Style.RESET_ALL)
        print(f"  Pairs compared : {len(deltas)}")
        print(f"  Mean delta     : {mean_delta:+.2f}")
        print(f"  Median delta   : {median_delta:+.2f}")
        print(f"  Min / Max delta: {min_delta:+d} / {max_delta:+d}")
        print(f"  |delta| ≥ 5    : {large} ({100*large/len(deltas):.1f}%)")
        print(f"{'─'*50}\n")


# ---------------------------------------------------------------------------
# _FakePeak — duck-types a Score row for the assessment bridge
# ---------------------------------------------------------------------------

class _FakePeak:
    """Minimal Score-like object for assess_scores compatibility."""
    __slots__ = ('symbol', 'symbol_id', 'date', 'overall')

    def __init__(self, symbol: str, d: date, overall: int):
        self.symbol    = symbol
        self.symbol_id = symbol   # assess_scores uses symbol_id as key
        self.date      = d
        self.overall   = overall


# ---------------------------------------------------------------------------
# CLI entry-point (called from trader.py)
# ---------------------------------------------------------------------------

def run(symbols=None, days=365, do_assess=False, do_compare=False, do_diff_assess=False,
        scoring_fn=None, db_version=None):
    """Run a simulator pipeline.

    db_version: passed to diff_assess to filter DB peaks to a single algorithm
                version. Default None reads active production version dynamically
                — eliminates the cross-version pollution risk documented in
                .claude/docs/known-issues.md Priority #15.
    """
    sim = ScoreSimulator(symbols=symbols, lookback_days=days, scoring_fn=scoring_fn)
    scores = sim.simulate()

    # Default to active version to avoid cross-version pollution
    if db_version is None and do_diff_assess:
        from database.models.core import AlgorithmVersion
        try:
            db_version = AlgorithmVersion.get_active_scores_version().id
        except Exception:
            db_version = None

    if do_compare:
        sim.compare(scores)
    if do_diff_assess:
        sym = symbols[0] if symbols and len(symbols) == 1 else None
        sim.diff_assess(scores, symbol=sym, db_version=db_version)
    if do_assess:
        sim.assess(scores, lookback_days=days)

    return scores
