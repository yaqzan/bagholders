"""
experiments/exhaustion_footprint_profile.py

Profile the 'exhaustion footprint' on REJECTION volume signals to validate the
Put Gradient v4 thesis:

  Hypothesis: REJECTION at a capitulation extreme (Stoch<=15, RSI<=35,
  pct_from_ema50<=-10%, pre-vol weighted_sum<35) is statistically absorption
  (price-reversal signal), not breakdown continuation.  If confirmed,
  zeroing vol_mult in this gate (Design B) would stop the amplifier from
  pushing genuine put signals deeper into =0 territory.

Cohorts evaluated (all filtered to overall <= 25, i.e. put-qualified signals):
  (a) footprint:      vol_sig=='REJECTION' AND pre_vol_ws<35
                      AND raw_stoch<=STOCH_GATE AND raw_rsi<=RSI_GATE
                      AND pct_from_ema50<=EXT_GATE
  (b) rej_no_foot:    vol_sig=='REJECTION' AND pre_vol_ws<35
                      AND NOT (a) conditions
  (c) baseline:       all put signals overall <= 25

Confirmation gate: cohort(a) WR15 >= 5pp BELOW cohort(b).
If confirmed, proceed to implement Design B in experiments/put_gradient_fix_v4.py.

Usage:
    python experiments/exhaustion_footprint_profile.py [lookback_days]

Default lookback: 1825 days (5y).
"""
from __future__ import annotations

import sys
import os
import math
from datetime import date, timedelta
from typing import Dict, List, Tuple, Optional

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from colorama import Fore, Style, init
init(autoreset=True)

from database.utils.scoring import compute_overall_score, calculate_weekly_adjustment
from database.models.technical import PriceHistory
from volume_amplifier import get_volume_multiplier_from_cache
from simulator import ScoreSimulator, _week_start
from assess_scores import (
    _realized_vol_pct, _swing_walk,
    SWING_K_HIGH, SWING_M_HIGH,
    SWING_VOL_LOOKBACK, PERIOD_MAX_BARS,
    PERIODS,
)

# ── Gate thresholds (sweep candidates later) ─────────────────────────────────
STOCH_GATE  = 15.0    # raw stoch <= this
RSI_GATE    = 35.0    # raw rsi   <= this
EXT_GATE    = -10.0   # pct_from_ema50 <= this (below EMA50)
WS_GATE     = 35.0    # pre-vol weighted_sum < this

PUT_THRESHOLD = 25    # overall score threshold for put signals

LOOKBACK_DEFAULT = 1825  # 5 years


# ─────────────────────────────────────────────────────────────────────────────
# Pre-volume weighted_sum computation
# Replicates scoring.py up to (but not including) the volume amplification step.
# Must stay in sync with compute_overall_score if weights change.
# ─────────────────────────────────────────────────────────────────────────────

def _pre_vol_weighted_sum(
    trend, bb, rsi_score, macd, stoch, ta,
    bb_pct,
    ws_trend, ws_rsi, ws_macd,
    prev_ws_trend, prev_ws_rsi, prev_ws_macd,
) -> float:
    """
    Compute weighted_sum after weekly adjustment but BEFORE volume amplification.
    Returns the raw float (not clamped to int).
    """
    trend_bias      = float(np.tanh((trend - 50) * 0.06))
    trend_strength  = abs(trend_bias)
    trend_dominance = trend_strength ** 0.7

    if bb_pct is not None:
        bull_ext = max(0.0, (bb_pct - 0.80) / 0.20) * max(0, trend_bias)
        bear_ext = max(0.0, (0.20 - bb_pct) / 0.20) * max(0, -trend_bias)
        trend_dominance *= (1.0 - 0.5 * (bull_ext + bear_ext))

    osc_avg = (rsi_score + macd) / 2.0
    bull_div = max(0.0, (40 - osc_avg) / 40.0) * max(0, trend_bias)
    bear_div = max(0.0, (osc_avg - 60) / 40.0) * max(0, -trend_bias)
    osc_divergence = bull_div + bear_div
    if bb_pct is not None:
        osc_divergence = min(1.0, osc_divergence * (1.0 + abs(bb_pct - 0.5)))
    trend_dominance *= (1.0 - 0.7 * osc_divergence)

    d       = trend_dominance
    w_trend = 18 + 10 * d
    w_bb    = 18
    w_rsi   = 25 -  9 * d
    w_macd  = 25 -  6 * d
    w_stoch =  5
    w_ta    =  9 +  6 * d

    # Asymmetric MACD gate
    PUT_MACD_GATE   = 45.0
    _scale_no_macd  = 100.0 / (100.0 - w_macd)
    _pre_no_macd    = 50 + (
        (trend       - 50) * w_trend +
        (bb          - 50) * w_bb +
        (rsi_score   - 50) * w_rsi +
        (stoch       - 50) * w_stoch +
        (ta          - 50) * w_ta
    ) * _scale_no_macd / 100
    if _pre_no_macd < PUT_MACD_GATE:
        w_trend *= _scale_no_macd
        w_bb    *= _scale_no_macd
        w_rsi   *= _scale_no_macd
        w_stoch *= _scale_no_macd
        w_ta    *= _scale_no_macd
        w_macd   = 0.0

    # Asymmetric RSI gate
    _pre_rsi = 50 + (
        (trend       - 50) * w_trend +
        (bb          - 50) * w_bb +
        (macd        - 50) * w_macd +
        (stoch       - 50) * w_stoch +
        (ta          - 50) * w_ta
    ) / 100
    rsi_eff = 50 if _pre_rsi < 40 else rsi_score

    ws = 50 + (
        (trend   - 50) * w_trend  +
        (bb      - 50) * w_bb     +
        (rsi_eff - 50) * w_rsi    +
        (stoch   - 50) * w_stoch  +
        (macd    - 50) * w_macd   +
        (ta      - 50) * w_ta
    ) / 100

    # Weekly adjustment
    if None not in (ws_rsi, ws_macd):
        adj, _ = calculate_weekly_adjustment(
            ws_trend, ws_rsi, ws_macd,
            prev_ws_trend, prev_ws_rsi, prev_ws_macd,
        )
        ws += adj

    return ws


# ─────────────────────────────────────────────────────────────────────────────
# Barrier-touch walk for a single signal
# ─────────────────────────────────────────────────────────────────────────────

def _swing_result_for_signal(sym: str, sig_date: date, ph_rows_asc) -> Optional[dict]:
    """
    Run the standard put barrier-touch walk for a signal on `sig_date`.
    ph_rows_asc: ascending-sorted list of PriceHistory-like rows for `sym`.

    Returns {'win_15d': int|None, 'win_30d': int|None, 'ret_30d': float|None}
    or None if insufficient data.

    Side is always 'high' (put side): win = price drops K*sigma, stop = rises M*sigma.
    """
    rows = [(r.date, float(r.close), float(r.high), float(r.low)) for r in ph_rows_asc]
    if not rows:
        return None

    date_idx = {r[0]: i for i, r in enumerate(rows)}
    base_idx  = date_idx.get(sig_date)
    if base_idx is None:
        return None

    dates  = [r[0] for r in rows]
    closes = [r[1] for r in rows]
    highs  = [r[2] for r in rows]
    lows   = [r[3] for r in rows]

    vol_pct = _realized_vol_pct(closes, base_idx, lookback=SWING_VOL_LOOKBACK)
    swing   = _swing_walk(closes, base_idx, 'high', vol_pct,
                          highs=highs, lows=lows, dates=dates)
    if swing is None:
        return None

    def _win(label):
        s = swing.get(label)
        if s is None:
            return None
        r = s.get('result')
        if r == 'win':
            return 1
        if r in ('stop', 'expire'):
            return 0
        return None  # missing / insufficient history

    def _ret(label):
        s = swing.get(label)
        return s.get('exit_ret') if s else None

    return {
        'win_15d': _win('15d'),
        'win_30d': _win('30d'),
        'ret_30d': _ret('30d'),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Cohort stats aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _cohort_stats(results: list) -> dict:
    """Compute WR15, WR30, Ret30 from a list of barrier-touch results."""
    w15_yes, w15_no = 0, 0
    w30_yes, w30_no = 0, 0
    rets = []

    for r in results:
        if r['win_15d'] == 1:
            w15_yes += 1
        elif r['win_15d'] == 0:
            w15_no += 1

        if r['win_30d'] == 1:
            w30_yes += 1
        elif r['win_30d'] == 0:
            w30_no += 1

        if r['ret_30d'] is not None:
            rets.append(r['ret_30d'])

    n15 = w15_yes + w15_no
    n30 = w30_yes + w30_no
    return {
        'N':      len(results),
        'N_15d':  n15,
        'WR15':   100.0 * w15_yes / n15 if n15 else float('nan'),
        'N_30d':  n30,
        'WR30':   100.0 * w30_yes / n30 if n30 else float('nan'),
        'Ret30':  (sum(rets) / len(rets)) if rets else float('nan'),
    }


def _pct(val: float) -> str:
    return f"{val:+.1f}%" if not math.isnan(val) else "n/a"


def _fmt(val: float, decimals: int = 1) -> str:
    return f"{val:.{decimals}f}" if not math.isnan(val) else "n/a"


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run(lookback_days: int = LOOKBACK_DEFAULT):
    print(Fore.CYAN + f"\n=== Exhaustion Footprint Profile (v4 thesis, {lookback_days}d lookback) ===\n")
    print(f"  Gate: vol_sig==REJECTION, pre_vol_ws<{WS_GATE}, "
          f"raw_stoch<={STOCH_GATE}, raw_rsi<={RSI_GATE}, ext<={EXT_GATE}%")
    print(f"  Signal threshold: overall <= {PUT_THRESHOLD} (put side)\n")

    # ── Load data via ScoreSimulator ────────────────────────────────────────
    print(Fore.YELLOW + "Loading data..." + Style.RESET_ALL)
    # Extra 90 days so barrier walks have forward price history past the lookback cutoff
    sim = ScoreSimulator(lookback_days=lookback_days + 90)
    # Data is loaded in __init__; no separate _load_all() call needed

    cutoff = date.today() - timedelta(days=lookback_days)

    # ── Signal records: cohort assignment ───────────────────────────────────
    # Each entry: {'sym', 'date', 'overall', 'pre_vol_ws', 'vol_sig',
    #              'raw_stoch', 'raw_rsi', 'pct_from_ema50', 'cohort'}

    cohort_a: List[dict] = []  # footprint REJECTION
    cohort_b: List[dict] = []  # non-footprint REJECTION, pre_vol_ws < WS_GATE
    cohort_c: List[dict] = []  # all put signals (<= PUT_THRESHOLD)

    n_total = n_put = n_rej_ws = n_foot = 0

    for sym, ctx in sim._contexts.items():
        stock        = ctx.stock
        prior_vols: Dict[date, Tuple[str, float, int]] = {}
        ph_rows_asc  = ctx.ph_rows_asc  # already sorted ascending

        for ind_row in ctx.ind_rows:
            d = ind_row.date
            if d < cutoff:
                continue

            n_total += 1
            ind_cache = ctx.ind_cache_for(d)

            # ── Component scores ─────────────────────────────────────────
            trend = stock.calculate_trend_score(
                d, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)
            if trend is None:
                continue

            _ph = ctx.ph_map.get(d)
            _pct_ema50 = (
                (float(_ph.close) - float(ind_row.ema_50)) / float(ind_row.ema_50) * 100
                if _ph and ind_row.ema_50 is not None else None
            )
            rsi_score = stock.calculate_rsi_score(
                d, trend_score=trend, _ind_cache=ind_cache, _ph_cache=ctx.ph_map,
                macdh_raw=float(ind_row.macd_hist) if ind_row.macd_hist is not None else None,
                pct_from_ema50=_pct_ema50)
            bb = stock.calculate_bollinger_bands_score(
                d, trend_score=trend, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)
            macd = stock.calculate_macd_score(
                d, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)
            stoch = stock.calculate_stoch_score(
                d, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)

            if None in (rsi_score, bb, macd, stoch):
                continue

            # Technical alignment
            scores_list = [bb, rsi_score, macd]
            _total   = len(scores_list)
            _bullish = sum(s >= 50 for s in scores_list)
            _bearish = _total - _bullish
            _agreement = max(_bullish, _bearish) / _total
            _avg_dist  = sum(s - 50 for s in scores_list) / _total
            _sig_str   = min(abs(_avg_dist) / 50, 1.0)
            _turbo     = 1 + 0.5 * _sig_str * _agreement
            ta = round(max(0, min(100,
                50 + _avg_dist * _sig_str * (_agreement ** 1.5) * _turbo)))

            # ── Weekly context ─────────────────────────────────────────
            ws      = ctx.ws_map.get(_week_start(d))
            pws     = ctx.ws_map.get(_week_start(d) - timedelta(weeks=1))
            ws_rsi  = ws.rsi  if ws and ws.rsi and ws.macd else None
            ws_macd = ws.macd if ws and ws.rsi and ws.macd else None

            # BB position
            bb_pct = None
            if None not in (ind_row.upper_band, ind_row.lower_band):
                bw  = float(ind_row.upper_band) - float(ind_row.lower_band)
                if bw > 0 and _ph:
                    bb_pct = (float(_ph.close) - float(ind_row.lower_band)) / bw

            # ── Volume signal ──────────────────────────────────────────
            ph_slice = ctx.ph_slice_desc_for(d)
            vol_mult, vol_raw, vol_sig, vol_mag, blend_w, vol_target = \
                get_volume_multiplier_from_cache(
                    d, ph_slice, prior_vols, ctx.earnings_set)
            prior_vols[d] = (vol_sig, vol_mag, vol_raw)

            # ── Overall score ──────────────────────────────────────────
            _r_mult = sim._regime_map.get(d)
            overall, _, _ = compute_overall_score(
                trend, bb, rsi_score, macd, stoch, ta,
                bb_pct=bb_pct,
                pct_from_ema50=_pct_ema50,
                ws_trend=ws.trend if ws else None,
                ws_rsi=ws_rsi, ws_macd=ws_macd,
                prev_ws_trend=pws.trend if pws else None,
                prev_ws_rsi=pws.rsi    if pws else None,
                prev_ws_macd=pws.macd  if pws else None,
                vol_mult=vol_mult, vol_raw=vol_raw,
                vol_sig=vol_sig,   vol_mag=vol_mag,
                blend_w=blend_w,   vol_target=vol_target,
                macdh_raw=float(ind_row.macd_hist) if ind_row.macd_hist is not None else None,
                regime_multiplier=_r_mult,
            )

            if overall is None or overall > PUT_THRESHOLD:
                continue

            n_put += 1

            # ── Pre-volume weighted_sum ────────────────────────────────
            pre_vol_ws = _pre_vol_weighted_sum(
                trend, bb, rsi_score, macd, stoch, ta,
                bb_pct,
                ws.trend if ws else None, ws_rsi, ws_macd,
                pws.trend if pws else None,
                pws.rsi   if pws else None,
                pws.macd  if pws else None,
            )

            raw_stoch = float(ind_row.stoch)  if ind_row.stoch  is not None else None
            raw_rsi   = float(ind_row.rsi)    if ind_row.rsi    is not None else None

            # ── Barrier-touch walk ─────────────────────────────────────
            btr = _swing_result_for_signal(sym, d, ph_rows_asc)
            if btr is None:
                continue

            rec = {
                'sym':           sym,
                'date':          d,
                'overall':       overall,
                'pre_vol_ws':    pre_vol_ws,
                'vol_sig':       vol_sig,
                'raw_stoch':     raw_stoch,
                'raw_rsi':       raw_rsi,
                'pct_from_ema50': _pct_ema50,
                **btr,
            }

            cohort_c.append(rec)  # all put signals

            if vol_sig == 'REJECTION' and pre_vol_ws < WS_GATE:
                n_rej_ws += 1
                is_foot = (
                    raw_stoch is not None and raw_stoch <= STOCH_GATE and
                    raw_rsi   is not None and raw_rsi   <= RSI_GATE   and
                    _pct_ema50 is not None and _pct_ema50 <= EXT_GATE
                )
                if is_foot:
                    n_foot += 1
                    cohort_a.append(rec)
                else:
                    cohort_b.append(rec)

    # ── Stats ────────────────────────────────────────────────────────────────
    stats_a = _cohort_stats(cohort_a)
    stats_b = _cohort_stats(cohort_b)
    stats_c = _cohort_stats(cohort_c)

    print(Fore.CYAN + f"\n--- Signal counts ({lookback_days}d, overall <= {PUT_THRESHOLD}) ---")
    print(f"  Total scored rows:             {n_total:,}")
    print(f"  Put signals (overall ≤ {PUT_THRESHOLD}):   {n_put:,}")
    print(f"  REJECTION & ws<{WS_GATE}:          {n_rej_ws:,}")
    print(f"  Exhaustion footprint [cohort a]: {n_foot:,}")

    def _row(label, s, ref_wr15=None):
        wr15 = s['WR15']
        wr30 = s['WR30']
        ret30 = s['Ret30']
        delta = ""
        if ref_wr15 is not None and not math.isnan(wr15) and not math.isnan(ref_wr15):
            diff = wr15 - ref_wr15
            color = Fore.GREEN if diff > 0 else Fore.RED
            delta = color + f"  Δ vs (b): {diff:+.1f}pp" + Style.RESET_ALL
        print(f"  {label:<42} N={s['N']:>5}  "
              f"WR15={_fmt(wr15)}%  WR30={_fmt(wr30)}%  Ret30={_pct(ret30)}{delta}")

    print(Fore.CYAN + "\n--- Cohort barrier-touch results (put side K=1σ/M=2σ) ---")
    _row("(c) All put signals ≤25 [baseline]",   stats_c)
    _row("(b) REJECTION, ws<35 [no footprint]",  stats_b)
    _row("(a) Exhaustion footprint REJECTION",   stats_a, ref_wr15=stats_b['WR15'])

    # ── Confirmation gate ─────────────────────────────────────────────────────
    wr15_a = stats_a['WR15']
    wr15_b = stats_b['WR15']
    print()
    if math.isnan(wr15_a) or math.isnan(wr15_b):
        print(Fore.YELLOW + "WARNING: insufficient N in one cohort — cannot evaluate confirmation gate.")
    elif stats_a['N'] < 30:
        print(Fore.YELLOW + f"WARNING: cohort (a) N={stats_a['N']} is too small for reliable conclusion.")
    else:
        delta_ab = wr15_a - wr15_b
        if delta_ab <= -5.0:
            print(Fore.GREEN +
                  f"CONFIRMED: footprint WR15 ({wr15_a:.1f}%) is "
                  f"{abs(delta_ab):.1f}pp BELOW non-footprint WR15 ({wr15_b:.1f}%). "
                  f"Gate threshold (5pp) cleared.\n"
                  "→ Proceed to implement Design B in put_gradient_fix_v4.py.")
        else:
            print(Fore.RED +
                  f"NOT CONFIRMED: footprint WR15 ({wr15_a:.1f}%) vs "
                  f"non-footprint ({wr15_b:.1f}%), delta={delta_ab:+.1f}pp "
                  f"(need <= -5.0pp).\n"
                  "→ Exhaustion footprint does NOT identify a materially worse subset; "
                  "revisit gate thresholds or abandon v4.")

    # ── Threshold sensitivity ──────────────────────────────────────────────────
    if stats_a['N'] >= 30 and not math.isnan(wr15_a):
        print(Fore.CYAN + "\n--- Threshold sensitivity (vary one gate at a time) ---")
        _sensitivity_sweep(cohort_c, cohort_a, cohort_b)


def _sensitivity_sweep(all_puts, foot_recs, nofoot_recs):
    """Quick 1-D sweeps on each gate threshold to see how robust the finding is."""
    from copy import deepcopy

    def _classify(rec, stoch_g, rsi_g, ext_g, ws_g):
        return (
            rec['vol_sig'] == 'REJECTION' and
            rec['pre_vol_ws'] is not None and rec['pre_vol_ws'] < ws_g and
            rec['raw_stoch']  is not None and rec['raw_stoch']  <= stoch_g and
            rec['raw_rsi']    is not None and rec['raw_rsi']    <= rsi_g and
            rec['pct_from_ema50'] is not None and rec['pct_from_ema50'] <= ext_g
        )

    def _sweep(param, vals, default_s, default_r, default_e, default_w):
        print(f"\n  Sweep {param}:")
        for v in vals:
            s_g = v if param == 'stoch' else default_s
            r_g = v if param == 'rsi'   else default_r
            e_g = v if param == 'ext'   else default_e
            w_g = v if param == 'ws'    else default_w

            foot   = [r for r in all_puts if _classify(r, s_g, r_g, e_g, w_g)]
            nofoot = [r for r in all_puts
                      if r['vol_sig'] == 'REJECTION' and
                         r['pre_vol_ws'] is not None and r['pre_vol_ws'] < w_g and
                         not _classify(r, s_g, r_g, e_g, w_g)]

            s_a = _cohort_stats(foot)
            s_b = _cohort_stats(nofoot)
            if s_a['N'] < 10:
                print(f"    {param}={v:>6}: N_foot={s_a['N']:>4}  (too small)")
                continue
            delta = s_a['WR15'] - s_b['WR15']
            mark  = Fore.GREEN + "✓" if delta <= -5.0 else (Fore.RED + "✗" if not math.isnan(delta) else "?")
            print(f"    {param}={v:>6}:  "
                  f"N_foot={s_a['N']:>5}  WR15_foot={_fmt(s_a['WR15'])}%  "
                  f"WR15_nofoot={_fmt(s_b['WR15'])}%  "
                  f"Δ={delta:+.1f}pp  {mark}{Style.RESET_ALL}")

    _sweep('stoch', [10, 15, 20, 25], STOCH_GATE, RSI_GATE,   EXT_GATE,  WS_GATE)
    _sweep('rsi',   [25, 30, 35, 40], STOCH_GATE, RSI_GATE,   EXT_GATE,  WS_GATE)
    _sweep('ext',   [-5, -10, -15, -20], STOCH_GATE, RSI_GATE, EXT_GATE, WS_GATE)
    _sweep('ws',    [30, 35, 40, 45], STOCH_GATE, RSI_GATE,   EXT_GATE,  WS_GATE)


if __name__ == '__main__':
    lb = int(sys.argv[1]) if len(sys.argv) > 1 else LOOKBACK_DEFAULT
    run(lb)
