"""
v22 experiment — X-as-confidence-coefficient sweep.

Motivation
----------
PSKY 3/20–3/27 fired extreme puts (12–21) while 5 of 6 components (BB, RSI,
MACD, Stoch, TA) were bullish/neutral and only TREND was extreme bearish
(trend=0). Production weighting gives TREND 28–35% of weight in trending mode,
which overwhelms broad disagreement. The existing `technical_alignment` (X)
component sees only BB+RSI+MACD and is weighted at ~15%, so a 5-vs-1 split
stays mostly invisible to the final score.

v22 repurposes X as a *confidence coefficient* on `d` (trend_dominance):
    X_conf = signal_strength × agreement ** 2       ∈ [0, 1]
    d_eff  = d × (X_conf ** alpha)
X_conf computed across all 5 base components (TREND, BB, RSI, MACD, Stoch) —
not BB+RSI+MACD only, and not X itself (avoids circularity).

alpha sweep:
    α=0.00  → baseline (d_eff = d). Sanity check.
    α=0.25  → mild gate.
    α=0.50  → moderate.
    α=0.75  → strong.
    α=1.00  → full.

Also sweep `x_inputs`: 5-component (TR/BB/RSI/MACD/ST) vs 3-component
(BB/RSI/MACD — matches production X). 3-comp is a weaker variant; it lets us
isolate whether the win comes from the gate mechanism vs the widened input set.

Success gate (from CLAUDE.md #8):
  - call 70+ WR15 within ±0.5pp AND N within ±5% of baseline
  - put <25 and <15 WR15 improved
  - put <25 and <15 Ret15 / Ret30 lifted positive

Usage
-----
    python experiments/x_confidence_gate_sweep.py            # 22-now full universe
    python experiments/x_confidence_gate_sweep.py 2y         # 2y lookback
    python experiments/x_confidence_gate_sweep.py AAPL MSFT NVDA 730   # smoke
"""
from __future__ import annotations

import sys
import math
from collections import defaultdict
from datetime import date, timedelta

import numpy as np

import database.utils.scoring as scoring_mod

_ORIGINAL = scoring_mod.compute_overall_score


def make_v22(alpha: float, x_inputs: str = '5comp'):
    """Return a drop-in replacement for compute_overall_score with X-conf gate.

    Parameters
    ----------
    alpha     : float in [0, 1]. 0 = baseline (no gate). 1 = full gate.
    x_inputs  : '5comp' or '3comp'. Which components feed X_conf.
    """
    assert x_inputs in ('5comp', '3comp')

    def fn(
        trend, bb, rsi, macd, stoch, technical_alignment,
        *,
        bb_pct=None,
        pct_from_ema50=None,
        ws_trend=None, ws_rsi=None, ws_macd=None,
        prev_ws_trend=None, prev_ws_rsi=None, prev_ws_macd=None,
        vol_mult=1.0, vol_raw=50, vol_sig='NEUTRAL', vol_mag=0.0,
        blend_w=0.0, vol_target=50.0,
        macdh_raw=None,
        regime_multiplier=None,
        put_regime_multiplier=None,
    ):
        if None in (trend, bb, rsi, stoch, macd, technical_alignment):
            return None, {}, None

        # ── Dynamic weighting (same as production) ───────────────────────
        trend_bias      = float(np.tanh((trend - 50) * 0.06))
        trend_strength  = abs(trend_bias)
        trend_dominance = trend_strength ** 0.7

        if bb_pct is not None:
            bull_ext = max(0.0, (bb_pct - 0.80) / 0.20) * max(0, trend_bias)
            bear_ext = max(0.0, (0.20 - bb_pct) / 0.20) * max(0, -trend_bias)
            trend_dominance *= (1.0 - 0.5 * (bull_ext + bear_ext))

        osc_avg = (rsi + macd) / 2.0
        bull_div = max(0.0, (40 - osc_avg) / 40.0) * max(0, trend_bias)
        bear_div = max(0.0, (osc_avg - 60) / 40.0) * max(0, -trend_bias)
        osc_divergence = bull_div + bear_div
        if bb_pct is not None:
            osc_divergence = min(1.0, osc_divergence * (1.0 + abs(bb_pct - 0.5)))
        trend_dominance *= (1.0 - 0.7 * osc_divergence)

        d = trend_dominance

        # ── v22: X-confidence gate on d ───────────────────────────────────
        if x_inputs == '5comp':
            comps = [trend, bb, rsi, macd, stoch]
        else:  # 3comp — matches production X
            comps = [bb, rsi, macd]
        n_comp = len(comps)
        bull_c = sum(1 for s in comps if s >= 50)
        bear_c = n_comp - bull_c
        agreement = max(bull_c, bear_c) / n_comp
        avg_dist = sum(s - 50 for s in comps) / n_comp
        sig_str = min(abs(avg_dist) / 50.0, 1.0)
        x_conf = sig_str * (agreement ** 2)   # ∈ [0, 1]
        # alpha=0 → x_conf**0 = 1 → d_eff = d (baseline)
        # alpha=1 → full gate: d scaled linearly by x_conf
        d_eff = d * (x_conf ** alpha) if alpha > 0 else d

        # ── Weights using d_eff ───────────────────────────────────────────
        w_trend = 18 + 10 * d_eff
        w_bb    = 18
        w_rsi   = 25 -  9 * d_eff
        w_macd  = 25 -  6 * d_eff
        w_stoch =  5
        w_ta    =  9 +  6 * d_eff

        # ── Asymmetric MACD gate (unchanged) ──────────────────────────────
        PUT_MACD_GATE = 45.0
        _scale_no_macd = 100.0 / (100.0 - w_macd)
        _pre_no_macd = 50 + (
            (trend               - 50) * w_trend +
            (bb                  - 50) * w_bb +
            (rsi                 - 50) * w_rsi +
            (stoch               - 50) * w_stoch +
            (technical_alignment - 50) * w_ta
        ) * _scale_no_macd / 100
        if _pre_no_macd < PUT_MACD_GATE:
            w_trend *= _scale_no_macd
            w_bb    *= _scale_no_macd
            w_rsi   *= _scale_no_macd
            w_stoch *= _scale_no_macd
            w_ta    *= _scale_no_macd
            w_macd   = 0.0

        # ── Asymmetric RSI zero (unchanged) ───────────────────────────────
        _pre_rsi = 50 + (
            (trend               - 50) * w_trend +
            (bb                  - 50) * w_bb +
            (macd                - 50) * w_macd +
            (stoch               - 50) * w_stoch +
            (technical_alignment - 50) * w_ta
        ) / 100
        if _pre_rsi < 40:
            rsi = 50

        weighted_sum = 50 + (
            ((trend  - 50) * w_trend) +
            ((bb     - 50) * w_bb) +
            ((rsi    - 50) * w_rsi) +
            ((stoch  - 50) * w_stoch) +
            ((macd   - 50) * w_macd) +
            ((technical_alignment - 50) * w_ta)
        ) / 100

        # ── Weekly adjustment (via module — picks up asymmetric scaling) ──
        weekly_detail = None
        if None not in (ws_rsi, ws_macd):
            adj, weekly_detail = scoring_mod.calculate_weekly_adjustment(
                ws_trend, ws_rsi, ws_macd,
                prev_ws_trend, prev_ws_rsi, prev_ws_macd,
            )
            weighted_sum += adj

        # ── Volume amplification (unchanged) ──────────────────────────────
        vol_boost = 1.0 + 0.6 * trend_strength
        if pct_from_ema50 is not None:
            ext = abs(pct_from_ema50)
            if ext > 15.0:
                ema_damp = 1.0 - 0.5 * min(1.0, (ext - 15.0) / 25.0)
                vol_boost *= ema_damp

        if blend_w > 0:
            blend_w = min(blend_w * vol_boost, 0.60)
            if abs(weighted_sum - 50) < 25:
                if (vol_target < 35 and macd > 60) or (vol_target > 65 and macd < 40):
                    blend_w *= 0.50
            weighted_sum = weighted_sum * (1 - blend_w) + vol_target * blend_w
        elif vol_mult != 1.0:
            if vol_sig == 'CONVICTION':
                if (weighted_sum < 35 and macd > 60) or (weighted_sum > 65 and macd < 40):
                    vol_mult = 1.0 + (vol_mult - 1.0) * 0.25
            deviation = (vol_mult - 1.0) * vol_boost
            weighted_sum = 50 + (weighted_sum - 50) * (1.0 + deviation)

        pre_regime_overall = int(max(0, min(100, weighted_sum)))

        # ── Regime adjustment (unchanged) ─────────────────────────────────
        overall = pre_regime_overall
        _eff_mult = (put_regime_multiplier
                     if (overall < 50 and put_regime_multiplier is not None)
                     else regime_multiplier)
        if _eff_mult is not None and _eff_mult != 1.0:
            if overall >= 50:
                adjusted = 50 + (overall - 50) * _eff_mult
            else:
                adjusted = 50 + (overall - 50) * (2.0 - _eff_mult)
            overall = int(max(0, min(100, round(adjusted))))

        # ── Post-regime dampeners (unchanged) ─────────────────────────────
        if overall == 0 and pct_from_ema50 is not None and pct_from_ema50 < -10.0:
            ext = abs(pct_from_ema50)
            overall = min(20, int(round(5.0 + (ext - 10.0))))

        if overall <= 9 and macdh_raw is not None:
            score_weight = (9 - overall) / 9.0
            mh_pos = max(0.0, float(np.tanh(macdh_raw / 0.05)))
            _exh = score_weight * mh_pos * 0.5
            if _exh > 0.0:
                overall = int(round(overall * (1 - _exh) + 10.0 * _exh))

        if overall <= 25 and pct_from_ema50 is not None and pct_from_ema50 > 0.0:
            ext_ramp = min(1.0, pct_from_ema50 / 10.0)
            score_weight = (25 - overall) / 25.0
            _ext = 0.5 * ext_ramp * score_weight
            if _ext > 0.0:
                lift = _ext * (25 - overall)
                overall = int(min(100, round(overall + lift)))

        # Minimal weight_info — sufficient for pre_regime-based reassessments.
        weight_info = {
            'trend': round(w_trend, 1), 'bb': round(w_bb, 1),
            'rsi':   round(w_rsi, 1),   'macd': round(w_macd, 1),
            'stoch': round(w_stoch, 1), 'ta':   round(w_ta, 1),
            'td':    round(trend_dominance, 3),
            'd_eff': round(d_eff, 3),
            'x_conf': round(x_conf, 3),
            'pre_regime': pre_regime_overall,
        }
        if weekly_detail:
            weight_info.update(weekly_detail)

        vol_update = {'volume': vol_raw, 'volume_signal': vol_sig, 'volume_magnitude': vol_mag} \
            if vol_sig != 'NEUTRAL' else None
        return overall, weight_info, vol_update

    return fn


def _parse_args():
    syms = None
    # Default: 2022-01-01 → today (~1575 days as of 2026-04-21).
    # Bear-start window stresses initial capital destruction, then compounds
    # through 2023/2024/2025; matches the canonical 22-now MC window.
    days = (date.today() - date(2022, 1, 1)).days
    args = sys.argv[1:]
    out = []
    for a in args:
        low = a.lower()
        if low.endswith('y'):
            days = int(float(low[:-1]) * 365)
        elif low.endswith('d'):
            days = int(low[:-1])
        elif low.isdigit():
            days = int(low)
        else:
            out.append(a.upper())
    if out:
        syms = out
    return syms, days


def _slice_peaks(peaks, start, end):
    """Return peaks with start <= peak.date <= end."""
    return [p for p in peaks if start <= p.date <= end]


def _run_variant(label, alpha, x_inputs, sim, cutoff_date, windows, db_version_row):
    """Simulate one variant, cache peaks + price history once, then per-window
    assess and diff vs DB."""
    from assess_scores import (
        extract_peaks, assess_peaks_in_memory, print_diff_assessment,
    )
    from database.models.technical import PriceHistory
    from simulator import _FakePeak
    try:
        from colorama import Fore, Style
    except ImportError:
        class _Dummy:
            def __getattr__(self, _): return ''
        Fore = Style = _Dummy()

    print("\n" + "=" * 100)
    print(f"  VARIANT: {label}   alpha={alpha:.2f}   x_inputs={x_inputs}")
    print("=" * 100)

    # Monkey-patch (or restore for baseline)
    scoring_mod.compute_overall_score = (
        _ORIGINAL if alpha == 0.0 else make_v22(alpha, x_inputs)
    )

    # 1. Simulate once across full window
    scores = sim.simulate()

    # 2. Build sim peaks (mirror diff_assess logic)
    by_symbol = defaultdict(list)
    for (sym, d), score in scores.items():
        if d < cutoff_date:
            continue
        if score >= 70 or score <= 25:
            by_symbol[sym].append((d, score))

    sim_peaks = []
    for sym, entries in by_symbol.items():
        date_set = {d for d, _ in entries}
        pruned = set()
        for d, score in sorted(entries, key=lambda x: abs(x[1] - 50), reverse=True):
            if (sym, d) in pruned:
                continue
            sim_peaks.append(_FakePeak(sym, d, score))
            for direction in (-1, 1):
                walk = d + timedelta(days=direction)
                while walk in date_set and (sym, walk) not in pruned:
                    pruned.add((sym, walk))
                    walk += timedelta(days=direction)

    # 3. DB peaks (cached full-window, version-pinned)
    lookback = (date.today() - cutoff_date).days
    db_peaks = extract_peaks(symbol=None, lookback_days=lookback, version=db_version_row)
    loaded_syms = set(sim._contexts.keys())
    if loaded_syms:
        db_peaks = [p for p in db_peaks if p.symbol_id in loaded_syms]

    print(f"[{label}] DB peaks (full window): {len(db_peaks)}   Sim peaks: {len(sim_peaks)}")

    # 4. Bulk-load price history once
    all_syms = list({p.symbol_id for p in db_peaks} | {p.symbol_id for p in sim_peaks})
    all_ph = list(
        PriceHistory.select()
        .where(PriceHistory.symbol.in_(all_syms), PriceHistory.date >= cutoff_date)
        .order_by(PriceHistory.symbol, PriceHistory.date)
        .namedtuples()
    )
    ph_by_sym = defaultdict(list)
    for r in all_ph:
        ph_by_sym[r.symbol].append(r)

    # 5. Per-window diff-assess
    for win_label, start, end in windows:
        db_slice  = _slice_peaks(db_peaks,  start, end)
        sim_slice = _slice_peaks(sim_peaks, start, end)
        print(f"\n-- Window [{win_label}]  ({start} -> {end})   "
              f"DB={len(db_slice)}  SIM={len(sim_slice)}")
        if not db_slice or not sim_slice:
            print("  (insufficient peaks in window — skipped)")
            continue
        # lookback_days param in assess_peaks_in_memory is unused internally;
        # the walker just follows each peak forward in ph_by_sym. Passing the
        # full window length is safe.
        db_data  = assess_peaks_in_memory(db_slice,  ph_by_sym, lookback)
        sim_data = assess_peaks_in_memory(sim_slice, ph_by_sym, lookback)
        print_diff_assessment(db_data, sim_data,
                              label_old=f'DB-{win_label}',
                              label_new=f'SIM-{win_label}')


def main():
    syms, days = _parse_args()

    # Sweep grid. alpha=0.0 is the baseline sanity anchor — must match production
    # exactly (within integer rounding). Larger alpha = stronger gate.
    variants = [
        ('baseline (prod)', 0.00, '5comp'),
        ('a25_5c',          0.25, '5comp'),
        ('a50_5c',          0.50, '5comp'),
        ('a75_5c',          0.75, '5comp'),
        ('a100_5c',         1.00, '5comp'),
        ('a50_3c',          0.50, '3comp'),
        ('a100_3c',         1.00, '3comp'),
    ]

    from simulator import ScoreSimulator
    from database.models.core import AlgorithmVersion
    v21 = AlgorithmVersion.get_or_none(AlgorithmVersion.id == 21)
    if v21 is None:
        print("WARNING: AlgorithmVersion id=21 not found; diff will mix versions")

    sim = ScoreSimulator(symbols=syms, lookback_days=days, scoring_fn=None)
    cutoff_date = date.today() - timedelta(days=days)
    today = date.today()

    # Windows: per-year + 22-now aggregate. Clamp upper bounds at today.
    def clamp(d): return min(d, today)
    windows = [
        ('2022',   date(2022, 1, 1),  clamp(date(2022, 12, 31))),
        ('2023',   date(2023, 1, 1),  clamp(date(2023, 12, 31))),
        ('2024',   date(2024, 1, 1),  clamp(date(2024, 12, 31))),
        ('2025',   date(2025, 1, 1),  clamp(date(2025, 12, 31))),
        ('22-now', date(2022, 1, 1),  today),
    ]

    print(f"Lookback: {days}d   cutoff={cutoff_date}   today={today}")
    print(f"Windows:  {[w[0] for w in windows]}")

    for label, alpha, x_inputs in variants:
        _run_variant(label, alpha, x_inputs, sim, cutoff_date, windows, v21)

    scoring_mod.compute_overall_score = _ORIGINAL


if __name__ == '__main__':
    main()
